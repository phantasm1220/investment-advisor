"""
core/etf_scanner.py  v4.6

ETFスキャナー（2エージェント統合版）。

分析フロー（1ETFあたり）:
  ┌──────────────────┐  ┌────────────────────────┐
  │  ETFAgent        │  │  ETFHoldingsAgent      │
  │  ・NAV乖離        │  │  ・構成銘柄Top10        │
  │  ・テクニカル     │  │  ・セクターバランス      │
  │  ・買いタイミング │  │  ・マクロ整合性          │
  └────────┬─────────┘  └────────────┬───────────┘
           │ AgentSignal              │ AgentSignal
           └──────────┬───────────────┘
                      │ 2エージェント統合
                      ▼
               ETFDecision（最終判断）
                      │
                      ▼
               Discord通知
"""
import logging

from datetime import datetime
from typing import Optional
from collections import defaultdict

from core.llm_client import LLMClient
from core.etf_signal import ETFDecision, ETFScanResult, HoldingsAnalysis
from core.signal import Verdict, AgentSignal
from agents.etf_agent import ETFAgent
from agents.etf_holdings_agent import ETFHoldingsAgent
from utils.etf_data import ETFFetcher, ETFInfo
from utils.etf_holdings import ETFHoldingsFetcher
from utils.discord_notifier import DiscordNotifier

logger = logging.getLogger(__name__)

# ラベル定数
NAV_LABEL = {
    "PREMIUM_HIGH":  "🔴 大幅プレミアム(割高)",
    "PREMIUM_LOW":   "🟠 小幅プレミアム",
    "FAIR":          "🟢 適正水準",
    "DISCOUNT_LOW":  "🟡 小幅ディスカウント",
    "DISCOUNT_HIGH": "💎 大幅ディスカウント(割安)",
}
OUTLOOK_LABEL  = {"BULLISH": "📈 強気", "NEUTRAL": "➡️ 中立", "BEARISH": "📉 弱気"}
TIMING_LABEL   = {"GOOD": "✅ 今が買い時", "NEUTRAL": "⏸️ 様子見", "WAIT": "⏳ 待機推奨"}
MACRO_LABEL    = {"BULLISH": "📈 マクロ追い風", "NEUTRAL": "➡️ 中立", "BEARISH": "📉 マクロ逆風"}
VERDICT_EMOJI  = {
    Verdict.STRONG_BUY: "🚀", Verdict.BUY: "✅",
    Verdict.HOLD: "⏸️", Verdict.SELL: "⚠️", Verdict.STRONG_SELL: "🔴",
}
VERDICT_COLOR  = {
    Verdict.STRONG_BUY: 0x00C851, Verdict.BUY: 0x2ECC71,
    Verdict.HOLD: 0x95A5A6, Verdict.SELL: 0xE67E22, Verdict.STRONG_SELL: 0xFF4444,
}


class ETFScanner:
    """ETF一括スキャナー（2エージェント版）"""

    def __init__(self, discord_webhook_url: Optional[str] = None):
        self._etf_fetcher      = ETFFetcher()
        self._holdings_fetcher = ETFHoldingsFetcher()
        self._etf_agent        = ETFAgent()
        self._holdings_agent   = ETFHoldingsAgent()
        self._llm              = LLMClient()
        self._notifier         = DiscordNotifier(webhook_url=discord_webhook_url)

    # ──────────────────────────────────────────────────────────
    #  スキャンエントリーポイント
    # ──────────────────────────────────────────────────────────

    def run_scan(
        self,
        top_n: int = 50,
        dry_run: bool = False,
        test_mode: bool = False,
    ) -> ETFScanResult:
        effective_n = 5 if test_mode else top_n
        logger.info(f"=== ETFスキャン開始 (上位{effective_n}件・2エージェント) ===")

        etfs = self._etf_fetcher.get_top_volume_etfs(top_n=effective_n)
        if not etfs:
            raise RuntimeError("ETFデータの取得に失敗しました")

        logger.info(f"分析対象: {len(etfs)}件")

        decisions: list[ETFDecision] = []
        for i, etf in enumerate(etfs, 1):
            logger.info(f"[{i}/{len(etfs)}] {etf.code} {etf.name}")
            try:
                decision = self._analyze_etf(etf)
                if decision:
                    decisions.append(decision)
            except Exception as e:
                logger.error(f"  [{etf.code}] 分析エラー（スキップ）: {e}")

        if not decisions:
            raise RuntimeError("全ETFの分析に失敗しました")

        result = self._build_result(decisions)
        self._send_discord_summary(result, dry_run=dry_run)
        logger.info(f"=== ETFスキャン完了: {len(decisions)}件 ===")
        return result

    # ──────────────────────────────────────────────────────────
    #  単一ETF分析（single --etf 用）
    # ──────────────────────────────────────────────────────────

    def analyze_single(self, code: str, dry_run: bool = False) -> Optional[ETFDecision]:
        logger.info(f"=== ETF単体分析開始: {code} ===")

        etf = self._etf_fetcher.get_etf_info(code)
        if etf is None:
            logger.error(f"[{code}] ETFデータの取得に失敗しました")
            return None

        nav_sign = "プレミアム" if etf.nav_premium_pct > 0 else "ディスカウント"
        chg      = f"{'▲' if etf.change_pct >= 0 else '▼'}{abs(etf.change_pct):.2f}%"
        print(
            f"✅ {code} ({etf.name})\n"
            f"   現在値: ¥{etf.current_price:,.0f}  {chg}\n"
            f"   対象: {etf.index_name} / テーマ: {etf.theme}\n"
            f"   NAV乖離: {etf.nav_premium_pct:+.3f}% ({nav_sign})"
        )

        decision = self._analyze_etf(etf)
        if decision is None:
            logger.error(f"[{code}] 分析に失敗しました")
            return None

        self._send_discord_single(decision, dry_run=dry_run)
        logger.info(f"=== ETF単体分析完了: {code} → {decision.verdict.value} ===")
        return decision

    # ──────────────────────────────────────────────────────────
    #  コアロジック: 2エージェント並列実行 → 統合
    # ──────────────────────────────────────────────────────────

    def _analyze_etf(self, etf: ETFInfo) -> Optional[ETFDecision]:
        """ETFAgent と ETFHoldingsAgent を直列実行して統合する。
        
        ※ 並列実行ではスレッド内で LLMClient が新規作成され
           os.environ から GEMINI_API_KEY が取れない場合があるため直列に変更。
        """

        # 構成銘柄データを先取得
        holdings_data = self._holdings_fetcher.get_holdings(etf.code)

        # ── ETFAgent（NAV・テクニカル）──────────────────────
        self._etf_agent.set_etf_info(etf)
        etf_signal = self._etf_agent.analyze(etf.code)

        # ── ETFHoldingsAgent（構成銘柄・セクター）──────────
        self._holdings_agent.set_holdings_data(holdings_data)
        holdings_signal = self._holdings_agent.analyze(etf.code)

        logger.info(
            f"  [{etf.code}] ETFAgent={etf_signal.verdict.value}({etf_signal.confidence:.2f}) "
            f"HoldingsAgent={holdings_signal.verdict.value}({holdings_signal.confidence:.2f})"
        )

        # ── 統合 ──────────────────────────────────────────────
        return self._integrate(etf, etf_signal, holdings_signal)

    def _integrate(
        self,
        etf: ETFInfo,
        etf_sig: AgentSignal,
        hold_sig: AgentSignal,
    ) -> ETFDecision:
        """
        2エージェントの結果を重み付け統合する。

        重み付け方針:
          - ETFAgent（NAV・テクニカル）: 40%
          - HoldingsAgent（セクター・マクロ）: 60%
            → 構成銘柄の質が長期パフォーマンスの根幹なのでやや重め
        """
        ETF_WEIGHT      = 0.40
        HOLDINGS_WEIGHT = 0.60

        etf_num  = etf_sig.to_numeric()
        hold_num = hold_sig.to_numeric()
        composite_score = etf_num * ETF_WEIGHT + hold_num * HOLDINGS_WEIGHT

        # 最終Verdictへ変換
        if   composite_score >=  0.65: final_verdict = Verdict.STRONG_BUY
        elif composite_score >=  0.30: final_verdict = Verdict.BUY
        elif composite_score >= -0.30: final_verdict = Verdict.HOLD
        elif composite_score >= -0.65: final_verdict = Verdict.SELL
        else:                          final_verdict = Verdict.STRONG_SELL

        # 確信度: 両エージェントの確信度の加重平均 × 合意ボーナス
        composite_conf = (
            etf_sig.confidence  * ETF_WEIGHT
            + hold_sig.confidence * HOLDINGS_WEIGHT
        )
        # 両エージェントが同方向なら+10%ボーナス
        if (etf_num > 0.1) == (hold_num > 0.1) and etf_num != 0 and hold_num != 0:
            composite_conf = min(1.0, composite_conf * 1.10)

        # 矛盾がある場合は確信度を下げる
        if (etf_num > 0.1 and hold_num < -0.1) or (etf_num < -0.1 and hold_num > 0.1):
            composite_conf *= 0.75
            logger.info(
                f"  [{etf.code}] 2エージェント間で意見相違 "
                f"→ 確信度を{composite_conf:.2f}に下方修正"
            )

        # HoldingsAgentのraw_scoresからHoldingsAnalysisを構築
        rs_h = hold_sig.raw_scores
        holdings_analysis = HoldingsAnalysis(
            verdict=hold_sig.verdict,
            confidence=hold_sig.confidence,
            summary=hold_sig.summary,
            key_factors=hold_sig.key_factors,
            top_sector=str(rs_h.get("sector_assessment__top_sector",
                                    getattr(self._holdings_agent._holdings_data, "top_sector", "不明")
                                    if self._holdings_agent._holdings_data else "不明")),
            top_sector_weight_pct=float(rs_h.get("sector_assessment__top_sector_weight_pct",
                                                   getattr(self._holdings_agent._holdings_data,
                                                           "top_sector_weight_pct", 0)
                                                   if self._holdings_agent._holdings_data else 0)),
            top_sector_macro_view=str(rs_h.get("sector_assessment__top_sector_macro_view", "NEUTRAL")),
            concentration_risk=str(rs_h.get("sector_assessment__concentration_risk", "MEDIUM")),
            macro_alignment=str(rs_h.get("sector_assessment__macro_alignment", "NEUTRAL")),
            holdings_quality_score=float(rs_h.get("holdings_quality_score", 0.5)),
            recommend_weight=str(rs_h.get("recommend_weight", "MARKETWEIGHT")),
            ai_tech_exposure_pct=float(rs_h.get("holdings_assessment__ai_tech_exposure_pct", 0)),
            defensive_ratio_pct=float(rs_h.get("holdings_assessment__defensive_ratio_pct", 0)),
        )

        # 統合サマリー（両エージェントの要点を結合）
        integrated_summary = (
            f"【ETF分析】{etf_sig.summary[:100]}\n"
            f"【構成銘柄】{hold_sig.summary[:100]}"
        )

        # キーファクターをマージ（ETF2件+Holdings2件）
        merged_factors = etf_sig.key_factors[:2] + hold_sig.key_factors[:2]

        return ETFDecision(
            code=etf.code,
            name=etf.name,
            index_name=etf.index_name,
            theme=etf.theme,
            expense_ratio=etf.expense_ratio,
            current_price=etf.current_price,
            nav_price=etf.nav_price,
            nav_premium_pct=etf.nav_premium_pct,
            nav_assessment=etf_sig.raw_scores.get("nav_assessment", "FAIR"),
            etf_agent_verdict=etf_sig.verdict,
            etf_agent_confidence=etf_sig.confidence,
            etf_agent_summary=etf_sig.summary,
            holdings_analysis=holdings_analysis,
            verdict=final_verdict,
            confidence=round(composite_conf, 3),
            summary=integrated_summary,
            key_factors=merged_factors,
            sector_outlook=str(etf_sig.raw_scores.get("sector_outlook", "NEUTRAL")),
            timing_signal=str(etf_sig.raw_scores.get("timing_signal", "NEUTRAL")),
            volume_ratio=etf.volume_ratio,
            change_pct=etf.change_pct,
            rsi_14=etf.rsi_14,
        )

    # ──────────────────────────────────────────────────────────
    #  集計・総評
    # ──────────────────────────────────────────────────────────

    def _build_result(self, decisions: list[ETFDecision]) -> ETFScanResult:
        decisions.sort(key=lambda d: d.confidence, reverse=True)

        buy_candidates = [
            d.code for d in decisions
            if d.verdict in (Verdict.BUY, Verdict.STRONG_BUY) and d.confidence >= 0.62
        ][:10]
        avoid_list = [
            d.code for d in decisions
            if d.verdict in (Verdict.SELL, Verdict.STRONG_SELL) and d.confidence >= 0.58
        ][:10]
        discount_opps = [
            d.code for d in decisions
            if d.nav_assessment in ("DISCOUNT_HIGH", "DISCOUNT_LOW")
            and d.verdict != Verdict.STRONG_SELL
        ][:8]

        # 注目テーマ: HoldingsAgentがBULLISHと判定したセクター
        bullish_themes = [
            d.holdings_analysis.top_sector_macro_view == "BULLISH"
            and d.holdings_analysis.top_sector
            for d in decisions
            if d.holdings_analysis
            and d.holdings_analysis.top_sector_macro_view == "BULLISH"
        ]
        hot_themes = list(dict.fromkeys(
            d.holdings_analysis.top_sector
            for d in decisions
            if d.holdings_analysis
            and d.holdings_analysis.top_sector_macro_view == "BULLISH"
        ))[:5]

        overview = self._generate_overview(decisions, hot_themes, buy_candidates, avoid_list)

        return ETFScanResult(
            scan_date=datetime.now(),
            total_etfs_analyzed=len(decisions),
            decisions=decisions,
            hot_themes=hot_themes,
            buy_candidates=buy_candidates,
            avoid_list=avoid_list,
            discount_opportunities=discount_opps,
            market_overview=overview,
        )

    def _generate_overview(
        self,
        decisions: list[ETFDecision],
        hot_themes: list[str],
        buy_candidates: list[str],
        avoid_list: list[str],
    ) -> str:
        from utils.market_theme_fetcher import get_themes_text as get_theme_context_for_prompt
        theme_ctx = get_theme_context_for_prompt()
        top5 = "\n".join(
            f"  {d.code}({d.name[:10]}): {d.verdict.value} 確信度{d.confidence:.0%} "
            f"NAV{d.nav_premium_pct:+.2f}% "
            f"主力セクター:{d.holdings_analysis.top_sector if d.holdings_analysis else '不明'}"
            for d in decisions[:5]
        )
        # 候補に名称を付与
        dmap = {d.code: d for d in decisions}
        def fmt(codes, n=5):
            items = []
            for c in codes[:n]:
                d = dmap.get(c)
                name = d.name[:10] if d else c
                price = f"¥{d.current_price:,.0f}" if d else ""
                items.append(f"{c}({name}){price}")
            return ", ".join(items) if items else "なし"

        prompt = f"""
ETF出来高上位{len(decisions)}件の2エージェント分析（NAV乖離+構成銘柄評価）の結果を総括してください。

{theme_ctx}

【上位5件の結果（コード・銘柄名・現在値・判断）】
{top5}
【マクロ追い風テーマ】: {', '.join(hot_themes) or 'なし'}
【買い候補（コード・銘柄名・現在値）】: {fmt(buy_candidates)}
【避けるべき（コード・銘柄名・現在値）】: {fmt(avoid_list)}

以下3点を各150字以内で日本語でまとめてください:
1. AI・半導体・エネルギーテーマと整合するETFセクター（銘柄名を明示）
2. NAV乖離と構成銘柄の両面から見た割安・買い機会（銘柄名を明示）
3. セクター構成がマクロ環境と合わない・避けるべきETF（銘柄名を明示）
最後に「ETF市場総合判断: 強気/中立/弱気」を一言で。
重要: AI・半導体・エネルギーへの言及を必ず含めること。
"""
        try:
            return self._llm.chat(
                "あなたはETF投資の専門アナリストです。",
                prompt, max_tokens=600,
            )
        except Exception as e:
            logger.error(f"総評生成エラー: {e}")
            return "ETF総評の生成に失敗しました。"

    # ──────────────────────────────────────────────────────────
    #  Discord送信（スキャン総評）
    # ──────────────────────────────────────────────────────────

    def _send_discord_summary(self, result: ETFScanResult, dry_run: bool) -> None:
        ts = result.scan_date.strftime("%Y-%m-%d %H:%M JST")
        dmap = {d.code: d for d in result.decisions}

        def fmt_codes(codes: list[str]) -> str:
            items = []
            for c in codes[:6]:
                d = dmap.get(c)
                if not d:
                    items.append(f"`{c}`")
                    continue
                nav   = f"NAV{d.nav_premium_pct:+.1f}%"
                macro = MACRO_LABEL.get(
                    d.holdings_analysis.top_sector_macro_view
                    if d.holdings_analysis else "NEUTRAL", ""
                )
                items.append(f"`{c}` {d.name[:8]}  {nav}  {macro}")
            return "\n".join(items) if items else "なし"

        # 上位ETF一覧（2エージェント情報付き）
        top_text = ""
        for d in result.decisions[:8]:
            em    = VERDICT_EMOJI.get(d.verdict, "")
            ha    = d.holdings_analysis
            macro = MACRO_LABEL.get(ha.top_sector_macro_view if ha else "NEUTRAL", "")
            conc  = f"集中:{ha.concentration_risk[:3]}" if ha else ""
            top_text += (
                f"{em} `{d.code}` {d.name[:10]}  "
                f"NAV{d.nav_premium_pct:+.2f}%  {macro}  {conc}\n"
            )

        # ディスカウント一覧
        discount_text = ""
        for c in result.discount_opportunities[:5]:
            d = dmap.get(c)
            if d:
                ha = d.holdings_analysis
                discount_text += (
                    f"`{c}` {d.name[:10]}  "
                    f"{NAV_LABEL.get(d.nav_assessment, '')}  "
                    f"{TIMING_LABEL.get(d.timing_signal, '')}\n"
                )
        if not discount_text:
            discount_text = "現在ディスカウント銘柄なし"

        embed = {
            "title": f"📦 ETFスキャン総評 — {result.scan_date.strftime('%Y/%m/%d')} （2エージェント分析）",
            "description": result.market_overview[:2000],
            "color": 0x9B59B6,
            "fields": [
                {
                    "name": "🔥 マクロ追い風テーマ",
                    "value": " / ".join(f"**{t}**" for t in result.hot_themes) or "なし",
                    "inline": False,
                },
                {
                    "name": "✅ 買い候補ETF（コード・銘柄名・NAV乖離）",
                    "value": fmt_codes(result.buy_candidates),
                    "inline": False,
                },
                {
                    "name": "⚠️ 避けるべきETF（コード・銘柄名・NAV乖離）",
                    "value": fmt_codes(result.avoid_list),
                    "inline": False,
                },
                {
                    "name": "💎 NAVディスカウント（割安機会）",
                    "value": discount_text[:1024],
                    "inline": False,
                },
                {
                    "name": "📋 上位ETF一覧（NAV乖離＋セクターマクロ観）",
                    "value": top_text[:1024] or "データなし",
                    "inline": False,
                },
                {
                    "name": "📈 分析統計",
                    "value": (
                        f"分析件数: **{result.total_etfs_analyzed}件**  "
                        f"買い: **{sum(1 for d in result.decisions if d.verdict in (Verdict.BUY, Verdict.STRONG_BUY))}**  "
                        f"売り: **{sum(1 for d in result.decisions if d.verdict in (Verdict.SELL, Verdict.STRONG_SELL))}**  "
                        f"中立: **{sum(1 for d in result.decisions if d.verdict == Verdict.HOLD)}**"
                    ),
                    "inline": False,
                },
            ],
            "footer": {"text": f"⚠️ 投資助言ではありません。 | {ts}"},
            "timestamp": result.scan_date.isoformat(),
        }

        self._post_discord({"embeds": [embed]}, dry_run, result)

    # ──────────────────────────────────────────────────────────
    #  Discord送信（単体分析）
    # ──────────────────────────────────────────────────────────

    def _send_discord_single(self, d: ETFDecision, dry_run: bool) -> None:
        verdict_emoji = VERDICT_EMOJI.get(d.verdict, "❓")
        ts  = d.timestamp.strftime("%Y-%m-%d %H:%M JST")
        ha  = d.holdings_analysis
        bar = "█" * round(d.confidence * 10) + "░" * (10 - round(d.confidence * 10))

        # NAV詳細フィールド
        nav_detail = (
            f"市場価格: **¥{d.current_price:,.0f}**\n"
            f"基準価額(NAV): **¥{d.nav_price:,.0f}**\n"
            f"乖離率: **{d.nav_premium_pct:+.3f}%**  "
            f"{NAV_LABEL.get(d.nav_assessment, d.nav_assessment)}\n"
            f"タイミング: {TIMING_LABEL.get(d.timing_signal, d.timing_signal)}"
        )

        # 構成銘柄フィールド
        if ha:
            holdings_detail = (
                f"主力セクター: **{ha.top_sector}** ({ha.top_sector_weight_pct:.0f}%)\n"
                f"マクロ観: {MACRO_LABEL.get(ha.top_sector_macro_view, ha.top_sector_macro_view)}\n"
                f"集中リスク: `{ha.concentration_risk}`\n"
                f"マクロ整合性: `{ha.macro_alignment}`\n"
                f"推奨ウェイト: **{ha.recommend_weight}**"
            )
            if ha.ai_tech_exposure_pct > 0:
                holdings_detail += f"\nAI/テック露出度: `{ha.ai_tech_exposure_pct:.0f}%`"
        else:
            holdings_detail = "構成銘柄データなし"

        # 2エージェントの個別意見フィールド
        etf_opinion     = (
            f"判断: **{d.etf_agent_verdict.value}** {VERDICT_EMOJI.get(d.etf_agent_verdict, '')} "
            f"| 確信度: `{d.etf_agent_confidence:.0%}`\n"
            f"{d.etf_agent_summary[:200]}"
        )
        holdings_opinion = (
            f"判断: **{ha.verdict.value}** {VERDICT_EMOJI.get(ha.verdict, '')} "
            f"| 確信度: `{ha.confidence:.0%}`\n"
            f"{ha.summary[:200]}"
        ) if ha else "データなし"

        # キーファクター
        key_text = "\n".join(f"• {f}" for f in d.key_factors[:4]) if d.key_factors else "なし"

        embed = {
            "title": f"{verdict_emoji}  {d.name}（{d.code}）",
            "description": f"**最終判断: {d.verdict.value}**  確信度: {d.confidence:.0%}\n",
            "color": VERDICT_COLOR.get(d.verdict, 0x9B59B6),
            "fields": [
                {
                    "name": "📈 総合確信度（2エージェント統合）",
                    "value": f"`{bar}` **{d.confidence:.0%}**",
                    "inline": False,
                },
                {
                    "name": "📌 ETF基本情報",
                    "value": (
                        f"対象: **{d.index_name}**  テーマ: **{d.theme}**\n"
                        f"信託報酬: `{d.expense_ratio:.3f}%`  "
                        f"前日比: `{'▲' if d.change_pct >= 0 else '▼'}"
                        f"{abs(d.change_pct):.2f}%`  出来高: `{d.volume_ratio:.1f}x`"
                    ),
                    "inline": False,
                },
                {
                    "name": "💎 NAV乖離分析（ETFエージェント）",
                    "value": nav_detail[:1024],
                    "inline": True,
                },
                {
                    "name": "🏗️ 構成銘柄・セクター分析（保有銘柄エージェント）",
                    "value": holdings_detail[:1024],
                    "inline": True,
                },
                {
                    "name": "📊 ETFエージェントの所見",
                    "value": etf_opinion[:1024],
                    "inline": False,
                },
                {
                    "name": "🔬 保有銘柄エージェントの所見",
                    "value": holdings_opinion[:1024],
                    "inline": False,
                },
                {
                    "name": "🔍 統合判断根拠",
                    "value": key_text[:1024],
                    "inline": False,
                },
            ],
            "footer": {"text": f"⚠️ 投資助言ではありません。判断は自己責任で。 | {ts}"},
            "timestamp": d.timestamp.isoformat(),
        }

        if dry_run:
            self._print_single_console(d)
            return

        self._post_discord({"embeds": [embed]}, dry_run=False, result_for_log=None)

    # ──────────────────────────────────────────────────────────
    #  ユーティリティ
    # ──────────────────────────────────────────────────────────

    def _post_discord(self, payload: dict, dry_run: bool, result_for_log=None) -> None:
        if dry_run:
            if result_for_log:
                logger.info(f"[Discord DryRun] ETF総評:\n{result_for_log.market_overview[:300]}")
            return
        if not self._notifier.webhook_url:
            logger.warning("DISCORD_WEBHOOK_URL 未設定のためスキップ")
            return
        import requests as rq
        try:
            resp = rq.post(self._notifier.webhook_url, json=payload, timeout=10)
            if resp.status_code in (200, 204):
                logger.info("[Discord] 送信成功")
            else:
                logger.error(f"[Discord] 失敗: {resp.status_code}")
        except Exception as e:
            logger.error(f"[Discord] エラー: {e}")

    @staticmethod
    def _print_single_console(d: ETFDecision) -> None:
        ha = d.holdings_analysis
        em = VERDICT_EMOJI.get(d.verdict, "")
        print("\n" + "=" * 70)
        print(f"  {em}  {d.name}（{d.code}）")
        print(f"  現在値: ¥{d.current_price:,.0f}  NAV乖離: {d.nav_premium_pct:+.3f}%  "
              f"{NAV_LABEL.get(d.nav_assessment, '')}")
        print(f"  対象: {d.index_name}  テーマ: {d.theme}")
        print(f"\n  ── 最終判断（2エージェント統合）──")
        print(f"  {d.verdict.value}  確信度: {d.confidence:.0%}")
        print(f"\n  ── ETFエージェント（NAV・テクニカル）──")
        print(f"  {d.etf_agent_verdict.value} ({d.etf_agent_confidence:.0%}): {d.etf_agent_summary[:150]}")
        if ha:
            print(f"\n  ── 保有銘柄エージェント（セクター・マクロ）──")
            print(f"  {ha.verdict.value} ({ha.confidence:.0%}): {ha.summary[:150]}")
            print(f"  主力セクター: {ha.top_sector} ({ha.top_sector_weight_pct:.0f}%)")
            print(f"  マクロ観: {ha.top_sector_macro_view}  整合性: {ha.macro_alignment}")
            print(f"  集中リスク: {ha.concentration_risk}  推奨: {ha.recommend_weight}")
        if d.key_factors:
            print(f"\n  判断根拠:")
            for f in d.key_factors[:4]:
                print(f"    • {f}")
        print("=" * 70 + "\n")
