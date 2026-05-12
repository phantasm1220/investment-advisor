"""
core/orchestrator.py  v5.0

最適化版:
- yfinanceの銘柄情報を起動時に1回だけ取得し全エージェントに渡す
- ファンダ/マクロ/テクニカルを並列実行（機関投資家は直列でWeb検索）
- 銘柄名・セクター情報を共有してエージェント間の重複取得を排除
"""
import logging
import concurrent.futures
import math
from typing import Optional

from agents.fundamentals_agent import FundamentalsAgent
from agents.macro_agent import MacroAgent
from agents.technical_agent import TechnicalAgent
from agents.institutional_agent import InstitutionalAgent
from agents.manager_agent import ManagerAgent
from utils.discord_notifier import DiscordNotifier
from utils.market_data import MarketDataFetcher, SUBSECTOR_MAP, SECTOR_MAP
from core.signal import FinalDecision, StockOverview, InstitutionalSummary

logger = logging.getLogger(__name__)


class InvestmentOrchestrator:
    def __init__(self, discord_webhook_url: Optional[str] = None):
        self._fundamentals  = FundamentalsAgent()
        self._macro         = MacroAgent()
        self._technical     = TechnicalAgent()
        self._institutional = InstitutionalAgent()
        self._manager       = ManagerAgent()
        self._notifier      = DiscordNotifier(webhook_url=discord_webhook_url)
        self._fetcher       = MarketDataFetcher()

    def run(
        self,
        ticker: str,
        current_price: float,
        dry_run: bool = False,
        stock_name: str = "",
    ) -> FinalDecision:
        logger.info(f"=== {ticker} 分析開始 ===")

        # ── 銘柄情報を1回だけ取得してキャッシュ ──────────────
        import yfinance as yf
        sym  = ticker if ticker.endswith(".T") else f"{ticker}.T"
        try:
            info = yf.Ticker(sym).info or {}
        except Exception:
            info = {}

        # code を先に定義してから使用
        code = ticker.replace(".T", "")
        jpx_sector = info.get("sector") or "不明"
        sector = SUBSECTOR_MAP.get(code, SECTOR_MAP.get(jpx_sector, jpx_sector))

        # JPXリストの日本語名を優先
        from utils.name_resolver import get_jp_name as _gjn
        _yf_name = info.get("longName") or info.get("shortName") or stock_name or ticker
        name = _gjn(code, None) or _yf_name

        # エージェントに共有情報を注入（yfinance重複取得を防ぐ）
        shared = {"name": name, "sector": sector, "info": info, "code": code}
        self._macro._shared_info         = shared
        self._institutional._shared_info = shared
        self._fundamentals._shared_info  = shared

        # ── ファンダ/マクロ/テクニカルを並列実行 ──────────────
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            f_fund  = ex.submit(self._fundamentals.analyze, ticker)
            f_macro = ex.submit(self._macro.analyze, ticker)
            f_tech  = ex.submit(self._technical.analyze, ticker)
            fund_signal  = f_fund.result()
            macro_signal = f_macro.result()
            tech_signal  = f_tech.result()

        logger.info(
            f"[3並列完了] fund={fund_signal.verdict.value}({fund_signal.confidence:.2f}) "
            f"macro={macro_signal.verdict.value}({macro_signal.confidence:.2f}) "
            f"tech={tech_signal.verdict.value}({tech_signal.confidence:.2f})"
        )

        # ── 機関投資家（Web検索・直列） ─────────────────────
        try:
            inst_signal = self._institutional.analyze(ticker)
            logger.info(
                f"[機関投資家] {inst_signal.verdict.value}({inst_signal.confidence:.2f}) "
                f"consensus={inst_signal.raw_scores.get('inst__consensus_rating','N/A')}"
            )
        except Exception as e:
            logger.warning(f"[機関投資家] 取得失敗→HOLDで継続: {e}")
            inst_signal = _fallback_inst_signal(ticker)

        # ── マネージャーが統合 ───────────────────────────────
        decision = self._manager.integrate(
            fundamentals=fund_signal, macro=macro_signal,
            technical=tech_signal, current_price=current_price,
            institutional=inst_signal,
        )

        # ── 市況サマリーと機関投資家サマリーを付与 ──────────
        from core.orchestrator_helpers import build_stock_overview
        decision.stock_overview = build_stock_overview(
            ticker, current_price, name, tech_signal, fund_signal
        )
        decision.institutional_summary = _build_inst_summary(inst_signal)

        self._notifier.send_decision(decision, dry_run=dry_run)
        logger.info(
            f"=== {ticker} 完了: {decision.verdict.value} "
            f"(conf={decision.composite_confidence:.2f}) ==="
        )
        return decision


def _fallback_inst_signal(ticker: str):
    from core.signal import AgentSignal, Verdict, RiskLevel
    return AgentSignal(
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


def _build_inst_summary(inst_signal) -> InstitutionalSummary:
    rs   = inst_signal.raw_scores
    inst = {k.replace("inst__", ""): v
            for k, v in rs.items() if k.startswith("inst__")}
    bullish = inst.get("bullish_institutions", [])
    bearish = inst.get("bearish_institutions", [])
    return InstitutionalSummary(
        consensus_rating=inst.get("consensus_rating", "EQUALWEIGHT"),
        avg_target_price=inst.get("avg_target_price"),
        smart_money_flow=inst.get("smart_money_flow", "NEUTRAL"),
        rating_momentum=rs.get("rating_momentum", "STABLE"),
        bullish_count=len(bullish) if isinstance(bullish, list) else 0,
        bearish_count=len(bearish) if isinstance(bearish, list) else 0,
        summary=inst_signal.summary[:200],
        data_freshness=rs.get("data_freshness", "ESTIMATED"),
    )
