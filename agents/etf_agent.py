"""
agents/etf_agent.py

ETF専用分析エージェント。
以下の3軸で判断を下す:
  1. NAV乖離分析  : 基準価額に対してプレミアム/ディスカウントがあるか
  2. セクター成長性: 対象インデックスが今後伸びるセクターかどうか（マクロ視点）
  3. 買いタイミング: テクニカル（RSI・移動平均・出来高）から今が買い時かどうか
"""

import json
import logging
from datetime import date
from typing import Any

from core.base_agent import BaseAgent
from core.signal import AgentSignal, Verdict, RiskLevel, DataSource
from utils.etf_data import ETFInfo

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたはETF（上場投資信託）の専門アナリストです。
個別株と異なり、ETFは以下の観点で分析してください:

1. NAV乖離分析
   - 市場価格と基準価額（NAV）の乖離率を評価
   - プレミアム(+): 割高 → 慎重/SELL方向
   - ディスカウント(-): 割安 → 買い機会の可能性
   - ±0.5%以内: 適正水準

2. セクター・インデックス成長性（マクロ視点）
   - 対象インデックスが現在の市場テーマと合致しているか
   - 金利環境・景気サイクルとの整合性
   - 半導体・AI・グロース系なのか、ディフェンシブなのか

3. 買いタイミング
   - RSI・移動平均からの過熱/割安判断
   - 出来高増加はトレンド確認シグナル
   - レバレッジ・インバース型は短期トレード前提として評価

分析結果は以下のJSON形式のみで返してください:
{
  "verdict": "BUY|SELL|HOLD|STRONG_BUY|STRONG_SELL",
  "confidence": 0.0〜1.0,
  "risk_level": "LOW|MEDIUM|HIGH|EXTREME",
  "summary": "200字以内の日本語要約",
  "key_factors": ["根拠1", "根拠2", "根拠3"],
  "nav_assessment": "PREMIUM_HIGH|PREMIUM_LOW|FAIR|DISCOUNT_LOW|DISCOUNT_HIGH",
  "sector_outlook": "BULLISH|NEUTRAL|BEARISH",
  "timing_signal": "GOOD|NEUTRAL|WAIT",
  "raw_scores": {
    "nav_premium_pct": 数値,
    "rsi_14": 数値またはnull,
    "ma25_deviation_pct": 数値またはnull,
    "volume_ratio": 数値,
    "sector_momentum": "HIGH|MEDIUM|LOW"
  }
}"""


class ETFAgent(BaseAgent):
    """ETF専用分析エージェント"""

    def __init__(self):
        super().__init__(name="etf_analyzer", system_prompt=SYSTEM_PROMPT)
        self._etf_info: ETFInfo | None = None

    def set_etf_info(self, etf_info: ETFInfo) -> None:
        """分析対象のETFInfoをセットする（analyze()の前に呼ぶ）"""
        self._etf_info = etf_info

    def gather_data(self, ticker: str) -> dict[str, Any]:
        today = date.today().isoformat()
        etf   = self._etf_info

        if etf is None:
            # ETFInfoがセットされていない場合は基本データのみ
            return {
                "ticker": ticker,
                "data_fetch_date": today,
                "error": "ETFInfoが未設定",
            }

        # NAV乖離の評価
        nav_assess = _classify_nav_premium(etf.nav_premium_pct)

        return {
            "ticker":          ticker,
            "data_fetch_date": today,
            "name":            etf.name,
            "index_name":      etf.index_name,
            "theme":           etf.theme,
            "expense_ratio":   etf.expense_ratio,
            "is_leveraged":    etf.is_leveraged,
            "is_sector":       etf.is_sector,
            # 価格・NAV
            "current_price":    etf.current_price,
            "nav_price":        etf.nav_price,
            "nav_premium_pct":  etf.nav_premium_pct,
            "nav_assessment":   nav_assess,
            # 需給・テクニカル
            "volume_ratio":     etf.volume_ratio,
            "change_pct":       etf.change_pct,
            "rsi_14":           etf.rsi_14,
            "ma25_pct":         etf.price_vs_ma25_pct,
            "ma75_pct":         etf.price_vs_ma75_pct,
        }

    def build_user_prompt(self, ticker: str, data: dict[str, Any]) -> str:
        if data.get("error"):
            return f"ETF {ticker} のデータ取得に失敗しました。HOLDで返してください。"

        nav_sign = "プレミアム(割高)" if data["nav_premium_pct"] > 0 else "ディスカウント(割安)"
        lev_note = "⚠️ レバレッジ/インバース型：短期トレード向け" if data["is_leveraged"] else ""

        rsi_str  = f"{data['rsi_14']:.1f}" if data["rsi_14"] is not None else "データなし"
        ma25_str = f"{data['ma25_pct']:+.1f}%" if data["ma25_pct"] is not None else "データなし"
        ma75_str = f"{data['ma75_pct']:+.1f}%" if data["ma75_pct"] is not None else "データなし"

        return f"""
以下のETFを分析してください。
{lev_note}

【基本情報】
- コード: {ticker} / {data['name']}
- 対象インデックス: {data['index_name']}
- テーマ/セクター: {data['theme']}
- 信託報酬: {data['expense_ratio']:.3f}%

【NAV乖離分析】
- 現在の市場価格: ¥{data['current_price']:,.0f}
- 基準価額（NAV）: ¥{data['nav_price']:,.0f}
- 乖離率: {data['nav_premium_pct']:+.3f}% → {nav_sign}
- 評価: {data['nav_assessment']}
  ※ ±0.5%以内=適正、+1%超=割高、-1%超=割安買い機会

【テクニカル】
- 前日比: {data['change_pct']:+.2f}%
- 出来高比率（5日平均比）: {data['volume_ratio']:.2f}x
- RSI(14): {rsi_str}
- 25日移動平均乖離: {ma25_str}
- 75日移動平均乖離: {ma75_str}

【判断のポイント】
1. NAVに対して割安か割高か → 買い/売りタイミング
2. 対象インデックス（{data['index_name']}）のセクター成長性 → 今後伸びるか
3. テクニカル的に今が買い時かどうか
4. レバレッジ型の場合は短期前提での評価

JSONのみで回答してください。
"""

    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal:
        data = self.gather_data(ticker)
        try:
            parsed = self._safe_parse_json(raw_text)
        except Exception as e:
            logger.warning(f"[etf_agent] JSONパース失敗: {e}")
            parsed = {
                "verdict": "HOLD", "confidence": 0.3, "risk_level": "MEDIUM",
                "summary": "ETFデータ解析エラー。",
                "key_factors": [], "nav_assessment": "FAIR",
                "sector_outlook": "NEUTRAL", "timing_signal": "NEUTRAL",
                "raw_scores": {},
            }

        # raw_scoresにフォールバック値補完
        rs = parsed.setdefault("raw_scores", {})
        rs.setdefault("nav_premium_pct",    data.get("nav_premium_pct", 0))
        rs.setdefault("rsi_14",             data.get("rsi_14"))
        rs.setdefault("ma25_deviation_pct", data.get("ma25_pct"))
        rs.setdefault("volume_ratio",       data.get("volume_ratio", 1.0))

        # DataSourceを付与
        sources = [
            DataSource(name="市場価格・NAV",  date=data["data_fetch_date"],
                       note=f"乖離{data.get('nav_premium_pct', 0):+.2f}%"),
            DataSource(name="テクニカル指標", date=data["data_fetch_date"],
                       note=f"RSI={data.get('rsi_14', 'N/A')}"),
        ]

        return AgentSignal(
            agent_name=self.name,
            ticker=ticker,
            verdict=Verdict(parsed["verdict"]),
            confidence=float(parsed.get("confidence", 0.5)),
            risk_level=RiskLevel(parsed.get("risk_level", "MEDIUM")),
            summary=parsed.get("summary", ""),
            key_factors=parsed.get("key_factors", []),
            raw_scores=rs,
            data_sources=sources,
        )


def _classify_nav_premium(pct: float) -> str:
    """NAV乖離率を5段階に分類する"""
    if pct > 1.5:
        return "PREMIUM_HIGH"    # 大幅プレミアム(割高)
    elif pct > 0.3:
        return "PREMIUM_LOW"     # 小幅プレミアム
    elif pct < -1.5:
        return "DISCOUNT_HIGH"   # 大幅ディスカウント(割安・買い機会)
    elif pct < -0.3:
        return "DISCOUNT_LOW"    # 小幅ディスカウント
    else:
        return "FAIR"            # 適正水準
