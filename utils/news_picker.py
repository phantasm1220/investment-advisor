"""
utils/news_picker.py

翌日株価が大きく動く可能性のある銘柄を
最新ニュース（24時間以内）からピックアップする。

実行タイミング:
  - 定時: 日〜金 23:00 JST（UTC 14:00）
  - 手動: Webパネルの「📰 今日の注目ニュース」ボタン

判定基準:
  - 決算: 市場予想コンセンサスとの乖離を重視（予想通りはスキップ）
  - その他: ポジティブ/ネガティブ + 既報かどうかを精査
"""
import json
import logging
import re
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

_session_cache: Optional[dict] = None
_cache_date: str = ""


def get_news_picks(force: bool = False) -> Optional[dict]:
    """
    翌日注目銘柄ニュースピックを取得する。
    当日キャッシュ付き（同日は1回のみ取得）。
    """
    global _session_cache, _cache_date
    today = date.today().isoformat()
    if not force and _cache_date == today and _session_cache:
        logger.info("[NewsPicker] セッションキャッシュから返します")
        return _session_cache

    result = _fetch_news_picks()
    if result:
        _session_cache = result
        _cache_date    = today
    return result


def _fetch_news_picks() -> Optional[dict]:
    from core.llm_client import LLMClient
    client = LLMClient()

    today_str     = date.today().strftime("%Y年%m月%d日")
    today_iso     = date.today().isoformat()
    weekday_names = ["月", "火", "水", "木", "金", "土", "日"]
    weekday       = weekday_names[date.today().weekday()]

    system = """あなたは日本株専門のニュースアナリストです。
本日の最新ニュース（過去24時間以内）を検索し、翌日の株価に大きく影響しそうな銘柄をピックアップしてください。

【重要な判断基準】
1. 決算ニュース: 市場予想（アナリストコンセンサス）との比較を必ず行うこと
   - 予想を大幅上回る（+10%以上）→ UP_HIGH
   - 予想を上回る（+5〜10%）→ UP_MEDIUM
   - 予想通り（±5%以内）→ スキップ（織り込み済みのため対象外）
   - 予想を下回る（-5〜10%）→ DOWN_MEDIUM
   - 予想を大幅下回る（-10%以上）→ DOWN_HIGH

2. 決算以外のニュース: 以下を必ず判定すること
   a) 新規情報か既報か: 過去1週間以内に類似ニュースが出ていた場合は「既報あり」と明記
   b) ポジティブ/ネガティブ: 株価への影響方向を明確に
   c) 既報の場合は織り込み済みの可能性を考慮してimpactを一段下げること

JSONのみで返答（前置き不要）:"""

    prompt = f"""
本日{today_str}（{weekday}曜日）時点で、過去24時間以内に発表・報道された
日本株に関するニュースを検索してください。

【対象とするニュース種別（優先順）】
1. 決算発表・業績修正（本日または明日発表予定を含む）
2. M&A・買収・合併・資本提携
3. 重要な受注・大型契約
4. アナリストの格上げ/格下げ（目標株価の大幅変更）
5. 不祥事・リコール・規制変更
6. 新製品・新技術の発表

【除外するニュース】
- 1週間以上前から継続している話題（既に株価に織り込み済みの可能性大）
- 業界全体の一般論（個別銘柄への影響が不明なもの）
- 海外株のニュース（日本株の東証上場銘柄のみ対象）

以下のJSON形式で5〜10件を回答してください:
{{
  "date": "{today_iso}",
  "generated_at": "現在時刻（日本時間）",
  "market_summary": "本日の市場全体の注目ポイントを50字以内で",
  "picks": [
    {{
      "ticker": "4桁の銘柄コード（例: 6963）",
      "name": "企業名",
      "news_title": "ニュースの見出し（40字以内）",
      "news_category": "決算/業績修正/M&A/受注/格付/不祥事/その他",
      "direction": "UP/DOWN/WATCH",
      "impact": "HIGH/MEDIUM/LOW",
      "is_already_reported": true/false,
      "prior_news_note": "既報がある場合のみ: いつ頃から報道されているか1行で。新規の場合はnull",
      "consensus_comparison": "決算の場合のみ: 予想との比較（例: 営業利益が市場予想を15%上回る）。非決算はnull",
      "reason": "株価への影響理由を60字以内で。既報の場合は織り込み度も言及",
      "source": "情報源（例: 日経電子版・Bloomberg・会社IR）"
    }}
  ],
  "disclaimer": "本情報はAIによる自動収集であり、投資助言ではありません"
}}

注意事項:
- 必ず{today_str}時点の最新情報（過去24時間以内）のみを対象とすること
- 古い情報・曖昧な情報はピックアップしないこと
- 決算は必ず市場予想との比較を記載すること
- is_already_reported は過去1週間以内に同一テーマの報道があった場合にtrueとすること
"""

    try:
        raw = client.chat(system, prompt, max_tokens=2000)
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("JSONが見つかりません")
        data = json.loads(match.group())
        logger.info(
            f"[NewsPicker] {len(data.get('picks', []))}件のニュースピックを取得"
        )
        return data
    except Exception as e:
        logger.warning(f"[NewsPicker] 取得失敗: {e}")
        return None


def format_discord_embed(data: dict) -> dict:
    """Discord Embed 形式に変換"""
    from datetime import datetime

    picks    = data.get("picks", [])
    date_str = data.get("date", date.today().isoformat())
    summary  = data.get("market_summary", "")
    ts       = datetime.now().strftime("%Y-%m-%d %H:%M JST")

    # 上昇期待・下落リスク・要注目に分類
    up_picks   = [p for p in picks if p["direction"] == "UP"]
    down_picks = [p for p in picks if p["direction"] == "DOWN"]
    watch_picks = [p for p in picks if p["direction"] == "WATCH"]

    fields = []

    def _fmt_pick(p: dict) -> str:
        impact_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵"}.get(p["impact"], "⚪")
        cat         = p.get("news_category", "")
        already     = p.get("is_already_reported", False)
        already_str = f"\n  ⚠️ 既報あり: {p['prior_news_note']}" if already else ""
        consensus   = p.get("consensus_comparison")
        cons_str    = f"\n  📊 {consensus}" if consensus else ""

        return (
            f"`{p['ticker']}` **{p['name']}**  {impact_icon}{p['impact']}  "
            f"_{cat}_\n"
            f"  📰 {p['news_title']}{cons_str}\n"
            f"  💬 {p['reason']}{already_str}\n"
            f"  📡 {p.get('source', 'N/A')}"
        )

    if up_picks:
        fields.append({
            "name": "🔺 上昇期待",
            "value": "\n\n".join(_fmt_pick(p) for p in up_picks)[:1024],
            "inline": False,
        })
    if down_picks:
        fields.append({
            "name": "🔻 下落リスク",
            "value": "\n\n".join(_fmt_pick(p) for p in down_picks)[:1024],
            "inline": False,
        })
    if watch_picks:
        fields.append({
            "name": "👁 要注目（方向感不明）",
            "value": "\n\n".join(_fmt_pick(p) for p in watch_picks)[:1024],
            "inline": False,
        })

    # コストサマリー
    try:
        from core.llm_client import CostTracker
        cost_str = CostTracker.get_summary()
    except Exception:
        cost_str = ""

    return {
        "embeds": [{
            "title": f"📰 翌日注目銘柄ニュースピック（{date_str}）",
            "description": f"📊 {summary}\n⚠️ AIによる自動収集です。情報の正確性は保証されません。",
            "color": 0x4A90D9,
            "fields": fields,
            "footer": {"text": f"投資助言ではありません。 | {ts}  {cost_str}"},
        }]
    }


def send_to_discord(webhook_url: str) -> bool:
    """ニュースピックをDiscordに送信する"""
    import requests

    data = get_news_picks()
    if not data or not data.get("picks"):
        logger.warning("[NewsPicker] ピック結果が空です")
        return False

    payload = format_discord_embed(data)
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            logger.info(f"[NewsPicker] Discord送信成功 ({len(data['picks'])}件)")
            return True
        else:
            logger.error(f"[NewsPicker] Discord送信失敗: {resp.status_code}")
            return False
    except Exception as e:
        logger.error(f"[NewsPicker] Discord送信エラー: {e}")
        return False
