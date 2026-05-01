"""
core/market_signal.py

市場スキャン結果のデータクラス。
個別銘柄シグナルとマネージャーの市場総評をまとめる。
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core.signal import FinalDecision


@dataclass
class SectorSummary:
    """業種単位の集計サマリー"""
    sector: str
    avg_confidence: float       # 平均確信度
    bullish_count: int          # 強気銘柄数
    bearish_count: int          # 弱気銘柄数
    top_ticker: str             # 代表銘柄
    top_ticker_name: str
    momentum_score: float       # モメンタムスコア（出来高比率の平均）


@dataclass
class MarketScanResult:
    """市場スキャン全体の結果"""
    scan_date: datetime
    total_stocks_analyzed: int

    # 個別銘柄の判断リスト（確信度降順）
    decisions: list[FinalDecision] = field(default_factory=list)

    # マネージャーの総評
    market_overview: str = ""           # 全体的な市場環境コメント
    hot_sectors: list[str] = field(default_factory=list)        # 注目セクター
    rising_candidates: list[str] = field(default_factory=list)  # 上昇候補銘柄
    falling_candidates: list[str] = field(default_factory=list) # 急落リスク銘柄
    sector_summaries: list[SectorSummary] = field(default_factory=list)
