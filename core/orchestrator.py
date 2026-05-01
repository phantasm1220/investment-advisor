"""
core/orchestrator.py  v4.7

4エージェント構成:
  ファンダメンタルズ / マクロ / テクニカル を並列実行
  + 機関投資家エージェント（直列・Web検索）
  → マネージャーが全4エージェントを統合して最終判断
"""
import logging
import concurrent.futures
from typing import Optional

from agents.fundamentals_agent import FundamentalsAgent
from agents.macro_agent import MacroAgent
from agents.technical_agent import TechnicalAgent
from agents.institutional_agent import InstitutionalAgent
from agents.manager_agent import ManagerAgent
from utils.discord_notifier import DiscordNotifier
from utils.market_data import MarketDataFetcher
from core.signal import FinalDecision, StockOverview, InstitutionalSummary

logger = logging.getLogger(__name__)


class InvestmentOrchestrator:
    """
    マルチエージェント投資助言システムのオーケストレーター。

    実行フロー:
      Step1: ファンダ / マクロ / テクニカルを並列実行
      Step2: 機関投資家エージェント（Web検索・直列）
      Step3: マネージャーが4エージェント統合
      Step4: Discord通知
    """

    def __init__(self, discord_webhook_url: Optional[str] = None):
        self._fundamentals   = FundamentalsAgent()
        self._macro          = MacroAgent()
        self._technical      = TechnicalAgent()
        self._institutional  = InstitutionalAgent()
        self._manager        = ManagerAgent()
        self._notifier       = DiscordNotifier(webhook_url=discord_webhook_url)
        self._fetcher        = MarketDataFetcher()

    def run(
        self,
        ticker: str,
        current_price: float,
        dry_run: bool = False,
        stock_name: str = "",
    ) -> FinalDecision:
        logger.info(f"=== {ticker} 分析開始（4エージェント）===")

        # ─── Step 1: ファンダ/マクロ/テクニカル を並列実行 ───
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            f_fund  = executor.submit(self._fundamentals.analyze, ticker)
            f_macro = executor.submit(self._macro.analyze, ticker)
            f_tech  = executor.submit(self._technical.analyze, ticker)
            fund_signal  = f_fund.result()
            macro_signal = f_macro.result()
            tech_signal  = f_tech.result()

        logger.info(
            f"[3エージェント完了] "
            f"fund={fund_signal.verdict.value}({fund_signal.confidence:.2f}), "
            f"macro={macro_signal.verdict.value}({macro_signal.confidence:.2f}), "
            f"tech={tech_signal.verdict.value}({tech_signal.confidence:.2f})"
        )

        # ─── Step 2: 機関投資家エージェント（Web検索・直列） ───
        logger.info("[4/4] 機関投資家エージェント（Web検索）分析中...")
        try:
            inst_signal = self._institutional.analyze(ticker)
            logger.info(
                f"[機関投資家] {inst_signal.verdict.value}({inst_signal.confidence:.2f}) "
                f"コンセンサス:{inst_signal.raw_scores.get('inst__consensus_rating','N/A')}"
            )
        except Exception as e:
            logger.warning(f"[機関投資家] 取得失敗（HOLDで継続）: {e}")
            from core.signal import AgentSignal, Verdict, RiskLevel
            inst_signal = AgentSignal(
                agent_name="institutional", ticker=ticker,
                verdict=Verdict.HOLD, confidence=0.3, risk_level=RiskLevel.MEDIUM,
                summary="機関投資家データの取得に失敗しました。",
                key_factors=["データ取得不可"],
                raw_scores={
                    "inst__consensus_rating": "EQUALWEIGHT",
                    "inst__smart_money_flow": "NEUTRAL",
                    "rating_momentum": "STABLE",
                    "data_freshness": "ESTIMATED",
                },
            )

        # ─── Step 3: マネージャーが4エージェント統合 ───
        decision = self._manager.integrate(
            fundamentals=fund_signal,
            macro=macro_signal,
            technical=tech_signal,
            current_price=current_price,
            institutional=inst_signal,
        )

        # ─── Step 4: 市況サマリーと機関投資家サマリーを付与 ───
        decision.stock_overview = self._build_stock_overview(
            ticker=ticker,
            current_price=current_price,
            stock_name=stock_name,
            tech_signal=tech_signal,
            fund_signal=fund_signal,
        )
        decision.institutional_summary = _build_inst_summary(inst_signal)

        # ─── Step 5: Discord通知 ───
        self._notifier.send_decision(decision, dry_run=dry_run)

        logger.info(
            f"=== {ticker} 分析完了: {decision.verdict.value} "
            f"(confidence={decision.composite_confidence:.2f}) ==="
        )
        return decision

    def _build_stock_overview(
        self, ticker, current_price, stock_name, tech_signal, fund_signal
    ) -> StockOverview:
        from core.orchestrator_helpers import build_stock_overview
        return build_stock_overview(
            ticker, current_price, stock_name, tech_signal, fund_signal
        )


def _build_inst_summary(inst_signal) -> InstitutionalSummary:
    """AgentSignal から InstitutionalSummary を構築"""
    rs = inst_signal.raw_scores
    inst_data = {k.replace("inst__", ""): v
                 for k, v in rs.items() if k.startswith("inst__")}
    bullish = inst_data.get("bullish_institutions", [])
    bearish = inst_data.get("bearish_institutions", [])
    return InstitutionalSummary(
        consensus_rating=inst_data.get("consensus_rating", "EQUALWEIGHT"),
        avg_target_price=inst_data.get("avg_target_price"),
        smart_money_flow=inst_data.get("smart_money_flow", "NEUTRAL"),
        rating_momentum=rs.get("rating_momentum", "STABLE"),
        bullish_count=len(bullish) if isinstance(bullish, list) else 0,
        bearish_count=len(bearish) if isinstance(bearish, list) else 0,
        summary=inst_signal.summary[:200],
        data_freshness=rs.get("data_freshness", "ESTIMATED"),
    )
