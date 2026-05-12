"""
agents/institutional_agent.py  v6.0 — レーティング変化 + 構造化LLM版

yfinanceのアナリストデータ + upgrades_downgrades（レーティング変化履歴）を活用。
コンセンサスの強度・変化方向・意見分散度を構造化評価。
"""
import logging
import math
from datetime import date, timedelta
from typing import Any

import yfinance as yf

from core.base_agent import BaseAgent
from core.signal import AgentSignal, Verdict, RiskLevel, DataSource

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは機関投資家動向アナリストです。
提供されたアナリストコンセンサスデータとレーティング変化履歴をもとに、
構造化して評価してください。

【評価手順（この順番で実行）】
① コンセンサス強度: BUY比率・アナリスト人数から信頼性を評価
② 目標株価乖離: 現在値から目標株価までの上昇余地を評価
③ モメンタム: 直近のレーティング変化方向（格上げ/格下げ）を評価
④ 意見分散度: 目標株価レンジの広さから不確実性を評価
⑤ 総合判断

【確信度の計算基準（必ずこの基準で計算すること）】
ベース値: 0.40
① コンセンサス方向: strong_buy/buy=+0.20, hold=0.00, sell=-0.10
② 目標株価上昇余地: +20%以上=+0.10, +10〜20%=+0.05, 0〜10%=0.00, マイナス=-0.10
③ レーティングモメンタム: 格上げ優勢=+0.10, 変化なし=0.00, 格下げ優勢=-0.10
④ 目標株価レンジ幅: 50%超=-0.05（意見分散ペナルティ）
計算例: buy(+0.20) + 上昇余地+15%(+0.05) + 変化なし(0.00) = 0.65
上限0.85、下限0.25

JSONのみで返答:
{
  "consensus_strength": "STRONG/MODERATE/WEAK",
  "upside_score": "A/B/C/D",
  "momentum_score": "A/B/C/D",
  "dispersion_risk": "LOW/MEDIUM/HIGH",
  "verdict": "STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL",
  "confidence": 0.0~1.0,
  "risk_level": "LOW/MEDIUM/HIGH/EXTREME",
  "summary": "150字以内。具体的な数値（目標株価・上昇余地・変化方向）を含む",
  "key_factors": ["数値付き根拠1", "根拠2", "根拠3"]
}"""

MAX_TOKENS = 260


class InstitutionalAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="institutional", system_prompt=SYSTEM_PROMPT)
        self.MAX_TOKENS = MAX_TOKENS
        self._shared_info = {}

    def gather_data(self, ticker: str) -> dict[str, Any]:
        today = date.today().isoformat()
        code  = ticker.replace(".T", "")
        sym   = f"{code}.T"

        if self._shared_info:
            name   = self._shared_info.get("name", ticker)
            sector = self._shared_info.get("sector", "不明")
        else:
            name, sector = ticker, "不明"

        result = {
            "ticker": ticker, "name": name, "sector": sector,
            "data_fetch_date": today,
            "rec_key": None, "rec_mean": None,
            "target_mean": None, "target_high": None, "target_low": None,
            "analyst_count": None, "current_price": None,
            "upside": None, "target_range_pct": None,
            # レーティング変化（直近90日）
            "upgrades_90d": 0, "downgrades_90d": 0,
            "rating_momentum": "STABLE",
            "is_real": False,
        }

        try:
            # orchestratorから共有情報があればyfinance呼び出しをスキップ
            if self._shared_info and self._shared_info.get("info"):
                info = self._shared_info["info"]
            else:
                info = yf.Ticker(sym).info or {}

            def _f(key):
                v = info.get(key)
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    return None
                return v

            result["rec_key"]       = _f("recommendationKey")
            result["rec_mean"]      = _f("recommendationMean")
            result["target_mean"]   = _f("targetMeanPrice")
            result["target_high"]   = _f("targetHighPrice")
            result["target_low"]    = _f("targetLowPrice")
            result["analyst_count"] = _f("numberOfAnalystOpinions")
            result["current_price"] = _f("currentPrice") or _f("regularMarketPrice")

            if result["current_price"] and result["target_mean"]:
                result["upside"] = (
                    result["target_mean"] - result["current_price"]
                ) / result["current_price"]

            # 目標株価レンジ幅（意見分散度）
            if result["target_high"] and result["target_low"] and result["target_mean"]:
                result["target_range_pct"] = (
                    result["target_high"] - result["target_low"]
                ) / result["target_mean"]

            result["is_real"] = any(result[k] is not None
                for k in ["rec_key", "rec_mean", "target_mean"])

            # レーティング変化履歴（直近90日）
            try:
                import logging as _log
                _yf_logger = _log.getLogger("yfinance")
                _prev_level = _yf_logger.level
                _yf_logger.setLevel(_log.CRITICAL)  # 404エラーログを抑制
                try:
                    tk = yf.Ticker(sym)
                    upgrades = tk.upgrades_downgrades
                finally:
                    _yf_logger.setLevel(_prev_level)
                if upgrades is not None and not upgrades.empty:
                    cutoff = date.today() - timedelta(days=90)
                    upgrades.index = upgrades.index.tz_localize(None) \
                        if upgrades.index.tz is not None else upgrades.index
                    recent = upgrades[upgrades.index.date >= cutoff]
                    if not recent.empty and "Action" in recent.columns:
                        result["upgrades_90d"]   = int((recent["Action"] == "up").sum())
                        result["downgrades_90d"] = int((recent["Action"] == "down").sum())
                        u, d_ = result["upgrades_90d"], result["downgrades_90d"]
                        if u > d_:
                            result["rating_momentum"] = "UPGRADING"
                        elif d_ > u:
                            result["rating_momentum"] = "DOWNGRADING"
                        else:
                            result["rating_momentum"] = "STABLE"
            except Exception:
                pass

        except Exception as e:
            logger.warning(f"[institutional] {ticker} 取得失敗: {e}")

        return result

    def build_user_prompt(self, ticker: str, d: dict) -> str:
        if not d.get("is_real"):
            return (f"銘柄{ticker}のアナリストデータ取得失敗。"
                    "確信度低めでHOLDを返してください。")

        def fmt(v, f=",.0f"): return f"¥{v:{f}}" if v is not None else "N/A"

        upside_str = f"{d['upside']:+.1%}" if d.get("upside") is not None else "N/A"
        range_str  = f"{d['target_range_pct']:.0%}" if d.get("target_range_pct") else "N/A"
        momentum_label = {
            "UPGRADING":   "↑ 格上げ優勢（直近90日）",
            "DOWNGRADING": "↓ 格下げ優勢（直近90日）",
            "STABLE":      "→ 変化なし",
        }.get(d["rating_momentum"], "不明")

        u, dg = d["upgrades_90d"], d["downgrades_90d"]
        momentum_detail = f"格上げ{u}件 / 格下げ{dg}件（直近90日）"

        return f"""【{ticker}（{d['name']}）機関投資家コンセンサスデータ】
セクター: {d['sector']}  取得日: {d['data_fetch_date']}（yfinanceリアルタイム）

【① アナリストコンセンサス】
推奨: {str(d.get('rec_key') or 'N/A').upper()}
コンセンサス平均スコア: {d.get('rec_mean') or 'N/A'}
カバーアナリスト数: {d.get('analyst_count') or 'N/A'}人

【② 目標株価】
平均目標株価: {fmt(d.get('target_mean'))}  現在値からの乖離: {upside_str}
最高目標株価: {fmt(d.get('target_high'))}
最低目標株価: {fmt(d.get('target_low'))}
目標株価レンジ幅: {range_str}（広いほど意見が分散）

【③ レーティング変化モメンタム】
方向: {momentum_label}
{momentum_detail}

① コンセンサスの強度（人数×推奨の強さ）、
② 目標株価の上昇余地と信頼性（レンジ幅）、
③ レーティング変化の方向性を構造化評価してJSONで回答してください。"""

    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal:
        d = self.gather_data(ticker)
        try:
            parsed = self._safe_parse_json(raw_text)
        except Exception:
            parsed = {"verdict": "HOLD", "confidence": 0.35,
                      "risk_level": "MEDIUM", "summary": "解析失敗",
                      "key_factors": []}

        consensus = {
            Verdict.STRONG_BUY: "OVERWEIGHT", Verdict.BUY: "OVERWEIGHT",
            Verdict.HOLD: "EQUALWEIGHT",
            Verdict.SELL: "UNDERWEIGHT", Verdict.STRONG_SELL: "UNDERWEIGHT",
        }.get(Verdict(parsed.get("verdict", "HOLD")), "EQUALWEIGHT")

        rs = {
            "inst__consensus_rating": consensus,
            "inst__avg_target_price": d.get("target_mean"),
            "inst__smart_money_flow": "NEUTRAL",
            "inst__bullish_institutions": [],
            "inst__bearish_institutions": [],
            "rating_momentum":   d["rating_momentum"],
            "data_freshness":    "FRESH",
            "consensus_strength": parsed.get("consensus_strength", "MODERATE"),
            "dispersion_risk":   parsed.get("dispersion_risk", "MEDIUM"),
            "upgrades_90d":      d["upgrades_90d"],
            "downgrades_90d":    d["downgrades_90d"],
        }

        return AgentSignal(
            agent_name=self.name, ticker=ticker,
            verdict=Verdict(parsed.get("verdict", "HOLD")),
            confidence=float(parsed.get("confidence", 0.35)),
            risk_level=RiskLevel(parsed.get("risk_level", "MEDIUM")),
            summary=parsed.get("summary", ""),
            key_factors=parsed.get("key_factors", []),
            raw_scores=rs,
            data_sources=[DataSource(
                name="アナリストコンセンサス（yfinance）",
                date=d["data_fetch_date"],
                note=(f"推奨={d.get('rec_key','N/A')} "
                      f"人数={d.get('analyst_count','N/A')} "
                      f"モメンタム={d['rating_momentum']}")
                if d.get("is_real") else "データ取得失敗"
            )],
        )
