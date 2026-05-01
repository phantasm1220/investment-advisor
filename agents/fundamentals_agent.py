"""
agents/fundamentals_agent.py — ファンダメンタルズ担当
"""
import logging
import json
from datetime import date
from typing import Any

from core.base_agent import BaseAgent
from core.signal import AgentSignal, Verdict, RiskLevel, DataSource

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは株式投資のファンダメンタルズ分析の専門家です。
将来のキャッシュフロー予測と情報の非対称性に焦点を当てて分析します。

分析結果は必ず以下のJSON形式で返してください（マークダウン不要）:
{
  "verdict": "BUY|SELL|HOLD|STRONG_BUY|STRONG_SELL",
  "confidence": 0.0〜1.0,
  "risk_level": "LOW|MEDIUM|HIGH|EXTREME",
  "summary": "200字以内の日本語要約",
  "key_factors": ["根拠1", "根拠2", "根拠3"],
  "raw_scores": {
    "progress_rate_vs_3yr_avg": 数値,
    "fx_hedge_gap_pct": 数値,
    "disclosure_change_score": 数値,
    "per_ttm": 数値,
    "progress_rate_delta": 数値
  }
}"""


class FundamentalsAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="fundamentals", system_prompt=SYSTEM_PROMPT)

    def gather_data(self, ticker: str) -> dict[str, Any]:
        today = date.today().isoformat()
        # 直近の決算日（サンプル。実運用ではEDINET/TDnet APIに差し替え）
        latest_earnings_date = "2025-11-14"
        prev_earnings_date   = "2025-08-12"

        return {
            "ticker": ticker,
            "company_name": f"銘柄{ticker}",
            "fiscal_year": "2026/3期",
            "data_fetch_date": today,             # ★ データ取得日
            "latest_earnings_date": latest_earnings_date,
            "prev_earnings_date":   prev_earnings_date,
            "progress_rate_current": 0.68,
            "progress_rate_3yr_avg": 0.59,
            "progress_rate_delta": 0.09,
            "fx_hedge_rate": 135.0,
            "fx_current_rate": 148.5,
            "fx_sensitivity_per_1yen": 2.5e8,
            "fx_gap_impact_bn_jpy": (148.5 - 135.0) * 2.5e8 / 1e8,
            "disclosures": [
                {
                    "date": latest_earnings_date,
                    "title": "2026年3月期 第2四半期決算短信",
                    "sentiment_change": +0.15,
                    "flagged_phrases": ["受注環境は改善傾向", "下期に向けてさらなる収益改善"],
                },
                {
                    "date": prev_earnings_date,
                    "title": "2026年3月期 第1四半期決算短信",
                    "sentiment_change": +0.05,
                    "flagged_phrases": ["堅調に推移"],
                },
            ],
            "per_ttm": 14.2,
            "per_sector_avg": 17.8,
            "pbr": 1.3,
            "roe": 0.112,
        }

    def build_user_prompt(self, ticker: str, data: dict[str, Any]) -> str:
        fx_impact = data.get("fx_gap_impact_bn_jpy", 0)
        progress_delta = data.get("progress_rate_delta", 0)
        return f"""
銘柄 {ticker}（{data.get('company_name', '')}）のファンダメンタルズ分析を行ってください。
データ取得日: {data['data_fetch_date']}
最新決算日: {data['latest_earnings_date']} / 前期決算日: {data['prev_earnings_date']}

【営業利益進捗率】
- 今期 Q3 時点: {data['progress_rate_current']:.1%}
- 過去3年平均: {data['progress_rate_3yr_avg']:.1%}
- 乖離: {progress_delta:+.1%}

【為替エクスポージャー】
- 会社予約レート: {data['fx_hedge_rate']:.1f} 円/USD
- 現在実勢レート: {data['fx_current_rate']:.1f} 円/USD
- 利益積み増し効果試算: {fx_impact:.1f} 億円

【適時開示の文言変化】
{json.dumps(data['disclosures'], ensure_ascii=False, indent=2)}

【バリュエーション】
- PER(TTM): {data['per_ttm']:.1f}倍（セクター平均: {data['per_sector_avg']:.1f}倍）
- PBR: {data['pbr']:.2f}倍 / ROE: {data['roe']:.1%}

以上を踏まえ、上方修正期待や情報の非対称性も考慮してJSONで投資シグナルを出力してください。
"""

    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal:
        data = self.gather_data(ticker)   # data_sources 組み立てに再利用
        try:
            parsed = self._safe_parse_json(raw_text)
        except Exception as e:
            logger.warning(f"[fundamentals] JSONパース失敗: {e}")
            parsed = {
                "verdict": "HOLD", "confidence": 0.3, "risk_level": "MEDIUM",
                "summary": "データ解析エラー。", "key_factors": [], "raw_scores": {},
            }

        # ★ 参照データソースを列挙
        sources = [
            DataSource(name="決算短信（最新）",   date=data["latest_earnings_date"],
                       note=f"Q3進捗率{data['progress_rate_current']:.0%}"),
            DataSource(name="決算短信（前期）",   date=data["prev_earnings_date"]),
            DataSource(name="為替実勢レート",     date=data["data_fetch_date"],
                       note=f"{data['fx_current_rate']:.1f}円/USD"),
            DataSource(name="バリュエーション",   date=data["data_fetch_date"],
                       note=f"PER{data['per_ttm']:.1f}倍"),
        ]

        return AgentSignal(
            agent_name=self.name,
            ticker=ticker,
            verdict=Verdict(parsed["verdict"]),
            confidence=float(parsed["confidence"]),
            risk_level=RiskLevel(parsed["risk_level"]),
            summary=parsed["summary"],
            key_factors=parsed.get("key_factors", []),
            raw_scores=parsed.get("raw_scores", {}),
            data_sources=sources,
        )
