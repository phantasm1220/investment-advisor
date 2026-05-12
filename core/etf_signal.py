"""
core/etf_signal.py
ETFスキャン結果のデータクラス（v4.6: 2エージェント対応）
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from core.signal import Verdict


@dataclass
class HoldingsAnalysis:
    """ETF構成銘柄エージェントの評価結果"""
    verdict: Verdict
    confidence: float
    summary: str
    key_factors: list[str] = field(default_factory=list)

    top_sector: str = "不明"
    top_sector_weight_pct: float = 0.0
    top_sector_macro_view: str = "NEUTRAL"   # BULLISH / NEUTRAL / BEARISH
    concentration_risk: str = "MEDIUM"       # LOW / MEDIUM / HIGH
    macro_alignment: str = "NEUTRAL"         # ALIGNED / NEUTRAL / MISALIGNED
    holdings_quality_score: float = 0.5
    recommend_weight: str = "MARKETWEIGHT"   # OVERWEIGHT / MARKETWEIGHT / UNDERWEIGHT
    ai_tech_exposure_pct: float = 0.0
    defensive_ratio_pct: float = 0.0


@dataclass
class ETFDecision:
    """1ETFの最終判断（ETFAgent + HoldingsAgentの統合結果）"""
    code: str
    name: str
    index_name: str
    theme: str
    expense_ratio: float

    current_price: float
    nav_price: float
    nav_premium_pct: float
    nav_assessment: str

    # ETFAgent（NAV・テクニカル）の判断
    etf_agent_verdict: Verdict
    etf_agent_confidence: float
    etf_agent_summary: str

    # HoldingsAgent（構成銘柄・セクターバランス）の判断
    holdings_analysis: Optional[HoldingsAnalysis] = None

    # 統合最終判断
    verdict: Verdict = Verdict.HOLD
    confidence: float = 0.5
    summary: str = ""
    key_factors: list[str] = field(default_factory=list)

    sector_outlook: str = "NEUTRAL"
    timing_signal: str  = "NEUTRAL"
    volume_ratio: float = 1.0
    change_pct: float   = 0.0
    rsi_14: Optional[float] = None

    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ETFScanResult:
    """ETFスキャン全体の結果"""
    scan_date: datetime
    total_etfs_analyzed: int

    decisions: list[ETFDecision] = field(default_factory=list)

    hot_themes: list[str] = field(default_factory=list)
    buy_candidates: list[str] = field(default_factory=list)
    avoid_list: list[str] = field(default_factory=list)
    discount_opportunities: list[str] = field(default_factory=list)

    market_overview: str = ""
