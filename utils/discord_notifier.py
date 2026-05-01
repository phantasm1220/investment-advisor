"""
utils/discord_notifier.py  v4.4
- Discord Embed フィールドvalue上限: 1024文字（API制限）
- description上限: 4096文字
- エージェントサマリーを見切れないよう文字数を適切に調整
- summary は全文表示（250文字まで）
"""
import logging, os
import math as _math

def _fmt_price(v, default="N/A") -> str:
    """価格を ¥X,XXX 形式に変換。NaN/None は default を返す"""
    if v is None:
        return default
    try:
        f = float(v)
        if _math.isnan(f) or _math.isinf(f) or f <= 0:
            return default
        return f"¥{f:,.0f}"
    except (TypeError, ValueError):
        return default

def _fmt_pct(v, default="N/A") -> str:
    """変化率を ▲X.XX% 形式に変換。NaN/None は default を返す"""
    if v is None:
        return default
    try:
        f = float(v)
        if _math.isnan(f) or _math.isinf(f):
            return default
        arrow = "▲" if f >= 0 else "▼"
        return f"{arrow}{abs(f):.2f}%"
    except (TypeError, ValueError):
        return default

from typing import Optional
import requests
from core.signal import FinalDecision, Verdict, RiskLevel, AgentSignal, StockOverview

logger = logging.getLogger(__name__)

VERDICT_EMOJI = {
    Verdict.STRONG_BUY: "🚀", Verdict.BUY: "✅",
    Verdict.HOLD: "⏸️", Verdict.SELL: "⚠️", Verdict.STRONG_SELL: "🔴",
}
VERDICT_COLOR = {
    Verdict.STRONG_BUY: 0x00C851, Verdict.BUY: 0x2ECC71,
    Verdict.HOLD: 0x95A5A6, Verdict.SELL: 0xE67E22, Verdict.STRONG_SELL: 0xFF4444,
}
RISK_EMOJI = {
    RiskLevel.LOW: "🟢", RiskLevel.MEDIUM: "🟡",
    RiskLevel.HIGH: "🟠", RiskLevel.EXTREME: "🔴",
}

# Discord API の上限
FIELD_VALUE_MAX  = 1024   # フィールドvalue最大文字数
DESCRIPTION_MAX  = 4096   # description最大文字数
SUMMARY_MAX      = 250    # エージェントサマリーの表示文字数（見切れ防止）


class DiscordNotifier:
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        if not self.webhook_url:
            logger.warning("DISCORD_WEBHOOK_URL が未設定です。")

    def send_decision(self, decision: FinalDecision, dry_run: bool = False) -> bool:
        payload = self._build_payload(decision)
        if dry_run:
            self._print_to_console(decision)
            return True
        if not self.webhook_url:
            self._print_to_console(decision)
            return False
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            if resp.status_code in (200, 204):
                logger.info(f"[Discord] 送信成功 ({decision.ticker})")
                return True
            logger.error(f"[Discord] 送信失敗: {resp.status_code} {resp.text[:200]}")
            return False
        except requests.RequestException as e:
            logger.error(f"[Discord] 通信エラー: {e}")
            return False

    def _build_payload(self, d: FinalDecision) -> dict:
        ov            = d.stock_overview
        verdict_emoji = VERDICT_EMOJI.get(d.verdict, "❓")
        color         = VERDICT_COLOR.get(d.verdict, 0x808080)
        ts            = d.timestamp.strftime("%Y-%m-%d %H:%M JST")

        # タイトル（銘柄名を先頭に）
        if ov and ov.name and ov.name != d.ticker:
            title = f"{verdict_emoji}  {ov.name}（{d.ticker}）"
        else:
            title = f"{verdict_emoji}  {d.ticker}"

        # description（最終判断 + 理由）
        desc = f"**最終判断: {d.verdict.value}**\n\n{d.rationale}"
        desc = desc[:DESCRIPTION_MAX]

        # 銘柄・市況サマリー
        overview_fields = []
        if ov:
            _chg   = 0.0 if (ov.change_pct != ov.change_pct) else float(ov.change_pct)  # NaN→0
            arrow  = "▲" if _chg >= 0 else "▼"
            parts  = [f"現在値 **{_fmt_price(ov.current_price)}**  {_fmt_pct(_chg)}"]
            if ov.rsi is not None:
                parts.append(f"RSI `{ov.rsi:.0f}`")
            if ov.per is not None:
                parts.append(f"PER `{ov.per:.1f}倍`")
            if ov.price_vs_ma200_pct is not None:
                parts.append(f"200日線 `{ov.price_vs_ma200_pct:+.1f}%`")
            parts.append(f"出来高 `{ov.volume_ratio:.1f}x`")
            val = "  ".join(parts) + f"\n🏷️ {ov.market_condition}"
            overview_fields.append({
                "name": "📌 銘柄・市況",
                "value": val[:FIELD_VALUE_MAX],
                "inline": False,
            })

        # エージェント個別結果
        agent_fields = []
        for sig in d.agent_signals:
            if sig.agent_name == "institutional":
                continue  # 機関投資家は inst_fields で表示
            risk_e   = RISK_EMOJI.get(sig.risk_level, "⚪")
            summary  = sig.summary
            # summary が長い場合は SUMMARY_MAX 文字で切る
            if len(summary) > SUMMARY_MAX:
                summary = summary[:SUMMARY_MAX] + "…"
            key_text = "\n".join(f"• {f}" for f in sig.key_factors[:3]) if sig.key_factors else ""
            val = (
                f"判断: **{sig.verdict.value}** {VERDICT_EMOJI.get(sig.verdict, '')} "
                f"| 確信度: `{sig.confidence:.0%}` {risk_e}\n"
                f"{summary}"
                + (f"\n{key_text}" if key_text else "")
            )
            agent_fields.append({
                "name": _agent_label(sig.agent_name),
                "value": val[:FIELD_VALUE_MAX],
                "inline": False,
            })

        # ── 機関投資家サマリー ──────────────────────────────
        inst_fields = []
        inst     = getattr(d, "institutional_summary", None)
        inst_sig = next((s for s in d.agent_signals if s.agent_name == "institutional"), None)
        if inst or inst_sig:
            flow_map  = {"INFLOW": "💰 資金流入", "NEUTRAL": "➡️ 中立", "OUTFLOW": "🚨 資金流出"}
            mom_map   = {"UPGRADING": "⬆️ 格上げ傾向", "STABLE": "➡️ 安定", "DOWNGRADING": "⬇️ 格下げ傾向"}
            cons_map  = {"OVERWEIGHT": "📈 強気(OVERWEIGHT)", "EQUALWEIGHT": "➡️ 中立(EQUALWEIGHT)", "UNDERWEIGHT": "📉 弱気(UNDERWEIGHT)"}
            fresh_map = {"FRESH": "1ヶ月以内", "DATED": "1〜6ヶ月", "ESTIMATED": "推定値"}

            inst_val = ""
            if inst_sig:
                risk_e = RISK_EMOJI.get(inst_sig.risk_level, "⚪")
                inst_val += (
                    f"判断: **{inst_sig.verdict.value}** {VERDICT_EMOJI.get(inst_sig.verdict, '')} "
                    f"| 確信度: `{inst_sig.confidence:.0%}` {risk_e}\n"
                )
            if inst:
                c_line = f"コンセンサス: **{cons_map.get(inst.consensus_rating, inst.consensus_rating)}**  鮮度: `{fresh_map.get(inst.data_freshness, inst.data_freshness)}`"
                f_line = f"スマートマネー: {flow_map.get(inst.smart_money_flow, inst.smart_money_flow)}  動向: {mom_map.get(inst.rating_momentum, inst.rating_momentum)}"
                inst_val += c_line + "\n" + f_line + "\n"
                if getattr(inst, "avg_target_price", None):
                    inst_val += "平均目標株価: `¥{:,.0f}`\n".format(inst.avg_target_price)
                bc = getattr(inst, "bullish_count", 0)
                be = getattr(inst, "bearish_count", 0)
                if bc or be:
                    inst_val += f"強気機関: {bc}社 / 弱気機関: {be}社\n"
            # 所見テキスト（inst_sig.summary を優先、inst.summary をフォールバック）
            detail = (inst_sig.summary if inst_sig else "") or (getattr(inst, "summary", "") if inst else "")
            if detail:
                inst_val += f"> {detail[:200]}"
            # キーファクター
            if inst_sig and inst_sig.key_factors:
                kf = "\n".join(f"• {f}" for f in inst_sig.key_factors[:3])
                inst_val += f"\n{kf}"
            inst_fields.append({
                "name":   "🏦 機関投資家コンセンサス",
                "value":  inst_val[:1024],
                "inline": False,
            })

        # 矛盾ノート
        conflict_fields = []
        if d.conflict_note:
            conflict_fields.append({
                "name": "⚡ 矛盾・リスク注記",
                "value": d.conflict_note[:FIELD_VALUE_MAX],
                "inline": False,
            })

        # 価格目標
        price_fields = []
        if d.target_price and d.stop_loss:
            price_fields.append({
                "name": "📊 価格目標",
                "value": (
                    f"🎯 目標: `¥{d.target_price:,.0f}`\n"
                    f"🛑 損切: `¥{d.stop_loss:,.0f}`\n"
                    f"📦 推奨ポジション: `{d.position_size_pct:.1f}%`"
                ),
                "inline": True,
            })

        embed = {
            "title": title[:256],
            "description": desc,
            "color": color,
            "fields": [
                {
                    "name": "📈 総合確信度",
                    "value": f"`{_bar(d.composite_confidence)}` **{d.composite_confidence:.0%}**",
                    "inline": False,
                },
                *overview_fields,
                *agent_fields,
                *inst_fields,
                *conflict_fields,
                *price_fields,
            ],
            "footer": {"text": f"⚠️ 投資助言ではありません。判断は自己責任で。 | {ts}"},
            "timestamp": d.timestamp.isoformat(),
        }
        return {"embeds": [embed]}

    @staticmethod
    def _print_to_console(d: FinalDecision) -> None:
        emoji = VERDICT_EMOJI.get(d.verdict, "")
        ov    = d.stock_overview
        print("\n" + "=" * 65)
        if ov and ov.name and ov.name != d.ticker:
            print(f"  {emoji}  {ov.name}（{d.ticker}）")
        else:
            print(f"  {emoji}  {d.ticker}")
        if ov:
            chg = f"({'▲' if ov.change_pct>=0 else '▼'}{abs(ov.change_pct):.2f}%)"
            print(f"  現在値: {_fmt_price(ov.current_price)} {chg}  市況: {ov.market_condition}")
        print(f"  最終判断: {d.verdict.value}  確信度: {d.composite_confidence:.0%}")
        if d.target_price:
            print(f"  目標: ¥{d.target_price:,.0f}  損切: ¥{d.stop_loss:,.0f}  ポジション: {d.position_size_pct:.1f}%")
        print(f"\n  {d.rationale}")
        for sig in d.agent_signals:
            print(f"\n  [{_agent_label(sig.agent_name)}] {sig.verdict.value} {sig.confidence:.0%}")
            print(f"  {sig.summary[:200]}")
        if d.conflict_note:
            print(f"\n  ⚡ {d.conflict_note[:200]}")
        print("=" * 65 + "\n")


def _bar(v: float, n: int = 10) -> str:
    f = round(v * n)
    return "█" * f + "░" * (n - f)

def _agent_label(name: str) -> str:
    return {"fundamentals": "🏦 ファンダメンタルズ担当",
            "macro":        "🌐 マクロ担当",
            "technical":    "📉 テクニカル担当"}.get(name, f"🤖 {name}")
