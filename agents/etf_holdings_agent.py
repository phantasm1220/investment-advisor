"""
agents/etf_holdings_agent.py — ETF構成銘柄評価エージェント v4.7
市場テーマ（AI・半導体・エネルギー）を必ず評価軸に含める。
"""
import logging
from datetime import date
from typing import Any

from core.base_agent import BaseAgent
from core.signal import AgentSignal, Verdict, RiskLevel, DataSource
from utils.etf_holdings import ETFHoldingsData
from utils.market_theme_fetcher import get_themes_text as get_theme_context_for_prompt, get_sector_view_live as get_sector_macro_view
from utils.market_context import MARKET_REGIME

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたはETFの構成銘柄・ポートフォリオ分析の専門家です。
ETFの「中身」を現在の市場テーマと照らし合わせて評価します。

【重要な分析姿勢】
現在の市場では以下のテーマが最も重要です。これらへの露出度を必ず評価してください:
1. AI・生成AI（データセンター・GPU・クラウド）→ 強く強気
2. 半導体・製造装置（NVDA・TSMC・東京エレクトロン・アドバンテスト）→ 強く強気
3. エネルギー・原子力（AI電力需要増大の恩恵）→ 強気
4. 防衛・宇宙産業 → 強気
5. 日本株ガバナンス改革・賃上げ → 中立〜強気
6. 金融・銀行（日銀利上げ恩恵） → 中立〜強気
7. 中国関連 → 弱気（関税リスク）

評価の観点:
1. AI・半導体テーマへの露出度（%）を定量的に示す
2. セクター配分と現在の市場テーマとの整合性
3. 集中リスク（特定銘柄・セクターへの過度な依存）
4. 地域・通貨リスク

以下のJSON形式のみで返してください:
{
  "verdict": "BUY|SELL|HOLD|STRONG_BUY|STRONG_SELL",
  "confidence": 0.0〜1.0,
  "risk_level": "LOW|MEDIUM|HIGH|EXTREME",
  "summary": "250字以内。AI・半導体・エネルギーへの露出度と、今買うべきかを明確に",
  "key_factors": ["AI/半導体露出度の評価", "セクター評価", "リスク要因"],
  "sector_assessment": {
    "top_sector": "最も比率が高いセクター名",
    "top_sector_macro_view": "BULLISH|NEUTRAL|BEARISH",
    "concentration_risk": "LOW|MEDIUM|HIGH",
    "macro_alignment": "ALIGNED|NEUTRAL|MISALIGNED"
  },
  "holdings_assessment": {
    "quality_score": 0.0〜1.0,
    "ai_tech_exposure_pct": 推定数値（必須・0でも記載）,
    "semicon_exposure_pct": 推定数値（必須）,
    "energy_exposure_pct": 推定数値（必須）,
    "defensive_ratio_pct": 推定数値,
    "top_holding_risk": "LOW|MEDIUM|HIGH",
    "theme_alignment": "HIGH|MEDIUM|LOW"
  },
  "raw_scores": {
    "sector_concentration_risk": 0〜1,
    "macro_alignment_score": 0〜1,
    "holdings_quality_score": 0〜1,
    "recommend_weight": "OVERWEIGHT|MARKETWEIGHT|UNDERWEIGHT"
  }
}"""


class ETFHoldingsAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="etf_holdings", system_prompt=SYSTEM_PROMPT)
        self._holdings_data: ETFHoldingsData | None = None

    def set_holdings_data(self, data: ETFHoldingsData) -> None:
        self._holdings_data = data

    def gather_data(self, ticker: str) -> dict[str, Any]:
        today = date.today().isoformat()
        h = self._holdings_data

        if h is None:
            return {"ticker": ticker, "data_fetch_date": today, "error": "HoldingsData未設定"}

        sector_text = "\n".join(
            f"  {a.sector}: {a.weight_pct:.1f}%"
            for a in h.sector_allocations[:8]
        ) if h.sector_allocations else "  データなし"

        holdings_text = "\n".join(
            f"  {i+1}. {s.name}({s.symbol}) {s.weight_pct:.1f}% [{s.sector}]"
            for i, s in enumerate(h.top_holdings[:10])
        ) if h.top_holdings else "  データなし"

        # 市場テーマとのマッチング
        top_sector_view = get_sector_macro_view(h.top_sector)

        return {
            "ticker":                  ticker,
            "data_fetch_date":         today,
            "name":                    h.name,
            "top10_concentration_pct": h.top10_concentration_pct,
            "top_sector":              h.top_sector,
            "top_sector_weight_pct":   h.top_sector_weight_pct,
            "top_sector_macro_view":   top_sector_view,
            "sector_text":             sector_text,
            "holdings_text":           holdings_text,
            "domestic_pct":            h.domestic_pct,
            "us_pct":                  h.us_pct,
            "other_pct":               h.other_pct,
            "is_complete":             h.is_complete,
            "data_source":             h.data_source,
            "theme_context":           get_theme_context_for_prompt(),
            "market_regime":           MARKET_REGIME["summary"],
        }

    def build_user_prompt(self, ticker: str, data: dict[str, Any]) -> str:
        if data.get("error"):
            return f"ETF {ticker} の構成銘柄データ取得に失敗。HOLDで返してください。"

        region_parts = []
        if data["domestic_pct"] > 0:
            region_parts.append(f"国内: {data['domestic_pct']:.0f}%")
        if data["us_pct"] > 0:
            region_parts.append(f"米国: {data['us_pct']:.0f}%")
        if data["other_pct"] > 0:
            region_parts.append(f"その他: {data['other_pct']:.0f}%")
        region_text = " / ".join(region_parts) if region_parts else "不明"
        note = "※構成銘柄データは推定値を含みます。" if not data["is_complete"] else ""

        return f"""
ETF「{data['name']}」（{ticker}）の構成銘柄・セクター配分を評価してください。{note}

{data['theme_context']}

【セクター配分】
{data['sector_text']}
  主力セクター: {data['top_sector']} ({data['top_sector_weight_pct']:.1f}%)
  主力セクターのマクロ見通し: {data['top_sector_macro_view']}

【上位保有銘柄（Top10）】
{data['holdings_text']}
  上位10銘柄集中度: {data['top10_concentration_pct']:.1f}%

【地域配分】{region_text}

以下を必ず定量的に評価してください:
1. AI・半導体テーマへの露出度（%）: NVDA・AMD・TSMC・東京エレクトロン等の合計比率
2. エネルギー（原子力含む）への露出度（%）
3. 主力セクター（{data['top_sector']}）は現在の市場テーマ（AI・半導体・エネルギー）と整合するか
4. 中国関連リスクの有無（輸出依存企業が多いか）
5. このETFは今の市場環境で買うべきか（具体的な理由付きで）

AI・半導体露出度が高い場合は積極的に評価し、
低い場合でもセクター特性に応じた客観的評価を行ってください。
"""

    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal:
        data = self.gather_data(ticker)
        try:
            parsed = self._safe_parse_json(raw_text)
        except Exception as e:
            logger.warning(f"[etf_holdings] JSONパース失敗: {e}")
            parsed = {
                "verdict": "HOLD", "confidence": 0.4, "risk_level": "MEDIUM",
                "summary": "構成銘柄データ解析エラー。",
                "key_factors": [], "sector_assessment": {},
                "holdings_assessment": {}, "raw_scores": {},
            }

        rs = parsed.setdefault("raw_scores", {})
        rs.setdefault("sector_concentration_risk", 0.5)
        rs.setdefault("macro_alignment_score", 0.5)
        rs.setdefault("holdings_quality_score", 0.5)

        for sub_key in ("sector_assessment", "holdings_assessment"):
            for k, v in parsed.get(sub_key, {}).items():
                rs[f"{sub_key}__{k}"] = v

        sources = [
            DataSource(name="ETF構成銘柄", date=data["data_fetch_date"],
                       note=f"集中度{data.get('top10_concentration_pct', 0):.0f}% "
                            f"({data.get('data_source', '不明')})"),
            DataSource(name="セクター配分", date=data["data_fetch_date"],
                       note=f"主力:{data.get('top_sector','不明')} "
                            f"{data.get('top_sector_weight_pct', 0):.0f}%"),
        ]

        return AgentSignal(
            agent_name=self.name, ticker=ticker,
            verdict=Verdict(parsed.get("verdict", "HOLD")),
            confidence=float(parsed.get("confidence", 0.4)),
            risk_level=RiskLevel(parsed.get("risk_level", "MEDIUM")),
            summary=parsed.get("summary", ""),
            key_factors=parsed.get("key_factors", []),
            raw_scores=rs,
            data_sources=sources,
        )
