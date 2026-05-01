"""
core/signal.py
エージェント間で共有するシグナルデータクラス
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from datetime import datetime


class Verdict(str, Enum):
    STRONG_BUY  = "STRONG_BUY"
    BUY         = "BUY"
    HOLD        = "HOLD"
    SELL        = "SELL"
    STRONG_SELL = "STRONG_SELL"


class RiskLevel(str, Enum):
    LOW     = "LOW"
    MEDIUM  = "MEDIUM"
    HIGH    = "HIGH"
    EXTREME = "EXTREME"


@dataclass
class DataSource:
    """エージェントが参照したデータソースの情報"""
    name: str           # データ名（例: "決算短信", "米国CPI", "RSI"）
    date: str           # データの日付（例: "2026-02-14", "2025-11-14"）
    note: str = ""      # 補足（例: "Q3進捗率68%"）


@dataclass
class StockOverview:
    """個別銘柄の市況サマリー（Discord表示用）"""
    name: str                           # 銘柄名
    current_price: float                # 現在株価
    change_pct: float                   # 前日比(%)
    volume_ratio: float                 # 出来高比率（平均比）
    rsi: Optional[float] = None         # RSI(14)
    per: Optional[float] = None         # PER
    market_condition: str = ""          # 市況ひとこと（買われすぎ/売られすぎ/好調/軟調 etc.）
    price_vs_ma200_pct: Optional[float] = None  # 200日線からの乖離率(%)


@dataclass
class AgentSignal:
    """各エージェントが出力するシグナル"""
    agent_name: str
    ticker: str
    verdict: Verdict
    confidence: float
    risk_level: RiskLevel
    summary: str
    key_factors: list[str] = field(default_factory=list)
    raw_scores: dict = field(default_factory=dict)
    # ★ 参照データソース（日付付き）
    data_sources: list[DataSource] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_numeric(self) -> float:
        mapping = {
            Verdict.STRONG_BUY:  1.0,
            Verdict.BUY:         0.5,
            Verdict.HOLD:        0.0,
            Verdict.SELL:       -0.5,
            Verdict.STRONG_SELL:-1.0,
        }
        return mapping[self.verdict]


@dataclass
class FinalDecision:
    """マネージャーが出力する最終判断"""
    ticker: str
    verdict: Verdict
    composite_confidence: float
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    position_size_pct: float = 0.0
    rationale: str = ""
    conflict_note: str = ""
    agent_signals: list[AgentSignal] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    # ★ 銘柄の市況サマリー（個別分析モード用）
    stock_overview: Optional[StockOverview] = None
    # ★ 機関投資家エージェントのサマリー
    institutional_summary: Optional['InstitutionalSummary'] = None


@dataclass
class InstitutionalSummary:
    """機関投資家エージェントの要約（FinalDecision に付与）"""
    consensus_rating: str = "EQUALWEIGHT"   # OVERWEIGHT/EQUALWEIGHT/UNDERWEIGHT
    avg_target_price: Optional[float] = None
    smart_money_flow: str = "NEUTRAL"       # INFLOW/NEUTRAL/OUTFLOW
    rating_momentum:  str = "STABLE"        # UPGRADING/STABLE/DOWNGRADING
    bullish_count:    int = 0
    bearish_count:    int = 0
    summary:          str = ""
    data_freshness:   str = "ESTIMATED"
