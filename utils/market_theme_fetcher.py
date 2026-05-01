"""
utils/market_theme_fetcher.py

Web検索を使って最新の市場テーマ・マクロ動向を自動取得するモジュール。
起動時に1回だけ実行し、market_context.py の GLOBAL_THEMES / SECTOR_MACRO_VIEW を
動的に上書きする。

検索ソース（LLM経由のWeb検索）:
  - 最新の機関投資家レポート要約
  - 主要インデックスの動向
  - 話題のセクター・テーマ
  - 金利・為替・コモディティの現況

フォールバック:
  Web検索に失敗した場合は market_context.py の固定データを使用する
"""

import os
import json
import logging
import re
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# キャッシュ（同一プロセス内で2回目以降は再取得しない）
_cached_context: Optional[dict] = None
_cache_date: Optional[str] = None


def get_live_market_context(force_refresh: bool = False) -> dict:
    """
    最新の市場テーマ・マクロ環境をWeb検索で取得する。
    同一日のキャッシュがあればそれを返す。

    Returns:
        dict: {
            "themes_text":    プロンプト用テキスト,
            "sector_views":   {セクター名: "BULLISH"|"NEUTRAL"|"BEARISH"},
            "fetched_at":     取得日時,
            "source":         "live" | "cached" | "fallback"
        }
    """
    global _cached_context, _cache_date

    today = date.today().isoformat()

    # キャッシュヒット
    if not force_refresh and _cached_context and _cache_date == today:
        logger.debug("[ThemeFetcher] キャッシュから市場テーマを返します")
        return _cached_context

    logger.info("[ThemeFetcher] 最新の市場テーマをWeb検索で取得中...")

    try:
        result = _fetch_via_llm_websearch()
        if result:
            _cached_context = result
            _cache_date = today
            logger.info(f"[ThemeFetcher] 市場テーマ取得成功（{result['source']}）")
            return result
    except Exception as e:
        logger.warning(f"[ThemeFetcher] Web検索失敗: {e} → フォールバックを使用")

    # フォールバック: market_context.py の固定データ
    fallback = _build_fallback()
    _cached_context = fallback
    _cache_date = today
    return fallback


def _fetch_via_llm_websearch() -> Optional[dict]:
    """
    LLM（Gemini）にWeb検索させて最新市場テーマを取得する。
    Gemini の grounding/search 機能または通常のプロンプトで最新情報を収集。
    """
    from core.llm_client import LLMClient

    client = LLMClient()

    today_str = date.today().strftime("%Y年%m月%d日")

    system = """あなたは市場調査の専門家です。
最新の金融市場動向を調査し、投資判断に使えるJSON形式で回答してください。
JSONのみを返し、マークダウンやコードフェンスは使わないでください。"""

    prompt = f"""
{today_str}現在の最新グローバル金融市場テーマを調査してください。

以下の形式のJSONのみで回答してください:
{{
  "fetch_date": "{today_str}",
  "market_regime": "現在の市場レジームを100字以内で",
  "top_themes": [
    {{
      "theme": "テーマ名（例: AI・半導体設備投資）",
      "outlook": "BULLISH or NEUTRAL or BEARISH",
      "horizon": "短期/中期/長期",
      "description": "テーマの説明100字以内",
      "key_tickers": ["銘柄1", "銘柄2"]
    }}
  ],
  "sector_views": {{
    "半導体": "BULLISH",
    "AI・テクノロジー": "BULLISH",
    "エネルギー": "BULLISH",
    "防衛": "BULLISH",
    "金融・銀行": "NEUTRAL",
    "医薬品": "NEUTRAL",
    "不動産": "NEUTRAL",
    "中国関連": "BEARISH",
    "一般消費財": "NEUTRAL",
    "素材": "NEUTRAL"
  }},
  "key_risks": ["リスク1", "リスク2", "リスク3"],
  "institutional_consensus": "機関投資家の主要見解を100字以内で（バンガード・ブラックロック・ゴールドマン等の最新スタンス）"
}}

注意:
- 必ず{today_str}時点の最新情報に基づいて回答すること
- 現在最も資金が流入しているセクター/テーマを上位に記載
- AIデータセンター・半導体・エネルギー・防衛等の最新動向を必ず含めること
- sector_viewsのキーは日本語セクター名とすること
"""

    try:
        raw = client.chat(system, prompt, max_tokens=1500)
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("JSONが見つかりません")
        data = json.loads(match.group())
        return _build_context_from_fetched(data)
    except Exception as e:
        logger.warning(f"[ThemeFetcher] LLM解析エラー: {e}")
        return None


def _build_context_from_fetched(data: dict) -> dict:
    """取得したJSONデータをエージェント用コンテキスト形式に変換"""
    themes       = data.get("top_themes", [])
    sector_views = data.get("sector_views", {})
    regime       = data.get("market_regime", "")
    risks        = data.get("key_risks", [])
    inst_view    = data.get("institutional_consensus", "")
    fetch_date   = data.get("fetch_date", date.today().isoformat())

    # プロンプト用テキスト生成
    lines = [
        f"【{fetch_date}時点の最新市場テーマ（Web検索取得・必ず分析に反映すること）】",
        "",
        f"■ 市場レジーム: {regime}",
        f"■ 機関投資家コンセンサス: {inst_view}",
        "",
        "■ 注目テーマ（重要度順）:",
    ]
    for t in themes[:6]:
        outlook = t.get("outlook", "NEUTRAL")
        emoji   = "📈" if outlook == "BULLISH" else ("📉" if outlook == "BEARISH" else "➡️")
        tickers = ", ".join(t.get("key_tickers", [])[:3])
        lines.append(
            f"  {emoji} {t.get('theme', '')}（{t.get('horizon', '')}）: "
            f"{t.get('description', '')[:80]}"
            + (f"  関連: {tickers}" if tickers else "")
        )

    lines += ["", "■ セクター別見通し:"]
    bullish = [k for k, v in sector_views.items() if v == "BULLISH"]
    neutral = [k for k, v in sector_views.items() if v == "NEUTRAL"]
    bearish = [k for k, v in sector_views.items() if v == "BEARISH"]
    if bullish: lines.append(f"  強気: {', '.join(bullish)}")
    if neutral: lines.append(f"  中立: {', '.join(neutral)}")
    if bearish: lines.append(f"  弱気: {', '.join(bearish)}")

    if risks:
        lines += ["", f"■ 主要リスク: {', '.join(risks[:3])}"]

    return {
        "themes_text":   "\n".join(lines),
        "sector_views":  sector_views,
        "fetched_at":    fetch_date,
        "source":        "live",
        "raw_data":      data,
    }


def _build_fallback() -> dict:
    """market_context.py の静的データをコンテキスト形式で返す"""
    from utils.market_context import (
        get_theme_context_for_prompt,
        SECTOR_MACRO_VIEW,
        CONTEXT_UPDATED,
    )
    logger.info(f"[ThemeFetcher] フォールバック: market_context.py ({CONTEXT_UPDATED}) を使用")
    return {
        "themes_text":  get_theme_context_for_prompt(),
        "sector_views": {k: v for k, v in SECTOR_MACRO_VIEW.items()},
        "fetched_at":   CONTEXT_UPDATED,
        "source":       "fallback",
        "raw_data":     {},
    }


def get_themes_text() -> str:
    """プロンプト用テキストだけを返す簡易インターフェース"""
    ctx = get_live_market_context()
    return ctx["themes_text"]


def get_sector_view_live(sector: str) -> str:
    """セクター名からライブ取得した見通しを返す"""
    ctx = get_live_market_context()
    views = ctx.get("sector_views", {})
    # 完全一致
    if sector in views:
        return views[sector]
    # 部分一致
    for key, view in views.items():
        if key in sector or sector in key:
            return view
    # フォールバック: market_context.py
    from utils.market_context import get_sector_macro_view
    return get_sector_macro_view(sector)
