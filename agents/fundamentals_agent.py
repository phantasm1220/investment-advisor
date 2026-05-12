"""
agents/fundamentals_agent.py  v6.0 — セクター平均比較 + 構造化LLM版

セクター内の主要銘柄との相対比較を追加し、
構造化フォーマットでLLMに評価させる。
"""
import logging
import math
from datetime import date
from typing import Any

import yfinance as yf

from core.base_agent import BaseAgent
from core.signal import AgentSignal, Verdict, RiskLevel, DataSource

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは株式ファンダメンタルズアナリストです。
提供された財務データとセクター平均との比較をもとに、
構造化された手順で評価してください。

【評価手順（この順番で実行）】
① バリュエーション: PER・PBRをセクター平均と比較して割安/割高を判定
② 収益性: ROE・営業利益率のレベルと改善/悪化トレンドを評価
③ 成長性: 売上・EPS成長率と加速/減速を評価
④ アナリスト評価: 目標株価までの乖離率と人数を評価
⑤ リスク: 決算リスク・財務健全性を評価
⑥ 総合判断

JSONのみで返答:
{
  "valuation_score": "A/B/C/D",
  "profitability_score": "A/B/C/D",
  "growth_score": "A/B/C/D",
  "analyst_score": "A/B/C/D",
  "verdict": "STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL",
  "confidence": 0.0~1.0,
  "risk_level": "LOW/MEDIUM/HIGH/EXTREME",
  "summary": "150字以内。セクター平均比較を含む具体的数値で説明",
  "key_factors": ["数値付き根拠1", "根拠2", "根拠3"],
  "raw_scores": {
    "per_ttm": 数値またはnull,
    "pbr": 数値またはnull,
    "roe": 数値またはnull
  }
}"""

MAX_TOKENS = 320

# セクター別の同業主要銘柄（セクター平均PER計算用）
SECTOR_PEERS: dict[str, list[str]] = {
    "半導体":             ["8035.T", "6857.T", "6920.T", "6723.T", "6963.T"],
    "電気機器":           ["6758.T", "6861.T", "6954.T", "6762.T", "6981.T"],
    "輸送機器（自動車）": ["7203.T", "7267.T", "7269.T", "7270.T", "7201.T"],
    "銀行":               ["8306.T", "8316.T", "8411.T"],
    "商社・卸売":         ["8031.T", "8053.T", "8001.T", "8002.T", "8058.T"],
    "AI・テック":         ["9984.T", "4689.T", "9613.T"],
    "医薬品":             ["4519.T", "4568.T", "4502.T", "4503.T"],
    "防衛":               ["7011.T", "7013.T", "7012.T"],
}


def _get_sector_avg(sector: str, metric: str = "trailingPE") -> float | None:
    """セクター内主要銘柄の指標平均を返す"""
    peers = SECTOR_PEERS.get(sector)
    if not peers:
        return None
    import logging as _log
    _yf_logger = _log.getLogger("yfinance")
    _prev_level = _yf_logger.level
    _yf_logger.setLevel(_log.CRITICAL)
    vals = []
    try:
        for sym in peers[:4]:
            try:
                info = yf.Ticker(sym).fast_info
                v = getattr(info, metric if hasattr(info, metric) else "", None)
                if v and not math.isnan(float(v)) and 0 < float(v) < 200:
                    vals.append(float(v))
            except Exception:
                pass
    finally:
        _yf_logger.setLevel(_prev_level)
    return round(sum(vals) / len(vals), 1) if vals else None


class FundamentalsAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="fundamentals", system_prompt=SYSTEM_PROMPT)
        self.MAX_TOKENS  = MAX_TOKENS
        self._shared_info = {}

    def gather_data(self, ticker: str) -> dict[str, Any]:
        today = date.today().isoformat()
        code  = ticker.replace(".T", "")
        sym   = f"{code}.T"

        result = {
            "ticker": ticker, "data_fetch_date": today,
            "name": ticker, "sector": "不明",
            "per": None, "pbr": None, "per_fwd": None, "ev_ebitda": None,
            "roe": None, "roa": None, "op_margin": None,
            "revenue_yoy": None, "eps_yoy": None, "eps_growth_fwd": None,
            "dividend_yield": None, "payout_ratio": None,
            "analyst_target": None, "analyst_upside": None,
            "analyst_rec": None, "analyst_count": None,
            "debt_equity": None, "current_ratio": None,
            # セクター平均（比較用）
            "sector_avg_per": None, "sector_avg_pbr": None,
            "earnings_tag": "", "earnings_penalty": 0.0,
            "next_earnings_date": None,
            "is_real": False,
        }

        try:
            # orchestratorから共有情報があればyfinance呼び出しをスキップ
            if self._shared_info and self._shared_info.get("info"):
                info = self._shared_info["info"]
                result["name"]   = self._shared_info.get("name", ticker)
                result["sector"] = self._shared_info.get("sector", "不明")
            else:
                info = yf.Ticker(sym).info or {}

            def _f(key):
                v = info.get(key)
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    return None
                return v

            from utils.name_resolver import get_jp_name
            result["name"]   = get_jp_name(code) or _f("longName") or _f("shortName") or ticker
            result["sector"] = _f("sector") or "不明"

            result["per"]          = _f("trailingPE")
            result["pbr"]          = _f("priceToBook")
            result["per_fwd"]      = _f("forwardPE")
            result["ev_ebitda"]    = _f("enterpriseToEbitda")
            result["roe"]          = _f("returnOnEquity")
            result["roa"]          = _f("returnOnAssets")
            result["op_margin"]    = _f("operatingMargins")
            result["revenue_yoy"]  = _f("revenueGrowth")
            result["eps_yoy"]      = _f("earningsGrowth")
            result["eps_growth_fwd"] = _f("earningsQuarterlyGrowth")

            # 配当バリデーション
            _dy = _f("dividendYield")
            result["dividend_yield"] = _dy if (_dy and 0 <= _dy <= 0.30) else None
            _pr = _f("payoutRatio")
            result["payout_ratio"] = _pr if (_pr and 0 <= _pr <= 1.5) else None

            result["analyst_target"] = _f("targetMeanPrice")
            result["analyst_count"]  = _f("numberOfAnalystOpinions")
            result["analyst_rec"]    = _f("recommendationKey")
            result["debt_equity"]    = _f("debtToEquity")
            result["current_ratio"]  = _f("currentRatio")

            cur = _f("currentPrice") or _f("regularMarketPrice")
            if cur and result["analyst_target"]:
                result["analyst_upside"] = (
                    result["analyst_target"] - cur) / cur

            result["is_real"] = any(result[k] is not None
                for k in ["per", "roe", "revenue_yoy", "analyst_target"])

            # セクター平均を取得（同じsectorのピアと比較）
            from utils.market_data import SUBSECTOR_MAP
            subsector = SUBSECTOR_MAP.get(code, result["sector"])
            result["sector_avg_per"] = _get_sector_avg(subsector, "pe_ratio")
            result["sector_avg_pbr"] = _get_sector_avg(subsector, "price_to_book")

        except Exception as e:
            logger.warning(f"[fundamentals] {ticker} 取得失敗: {e}")

        # 決算スケジュール
        try:
            from utils.earnings_calendar import get_earnings_info
            earn = get_earnings_info(code)
            result["earnings_tag"]       = earn["earnings_tag"]
            result["earnings_penalty"]   = earn["confidence_penalty"]
            result["next_earnings_date"] = earn["next_earnings_date"]
        except Exception:
            result["earnings_tag"]       = ""
            result["earnings_penalty"]   = 0.0
            result["next_earnings_date"] = None

        return result

    def build_user_prompt(self, ticker: str, d: dict) -> str:
        if not d.get("is_real"):
            return (f"銘柄{ticker}の財務データ取得に失敗。"
                    "確信度低めでHOLDを返してください。")

        def pct(v): return f"{v:.1%}" if v is not None else "N/A"
        def fmt(v, f=".1f"): return f"{v:{f}}" if v is not None else "N/A"

        # セクター平均との比較テキスト
        per_vs = ""
        if d.get("per") and d.get("sector_avg_per"):
            diff = (d["per"] - d["sector_avg_per"]) / d["sector_avg_per"] * 100
            per_vs = f"（セクター平均{d['sector_avg_per']}倍比 {diff:+.0f}%{'・割安' if diff < -10 else '・割高' if diff > 20 else ''}）"

        upside_str = f"{d['analyst_upside']:+.1%}" if d.get("analyst_upside") is not None else "N/A"
        earnings_str = f"⚠️ {d['earnings_tag']}" if d.get("earnings_tag") else "なし（7日以内の決算なし）"

        return f"""【{ticker}（{d['name']}）ファンダメンタルズ分析データ】
セクター: {d['sector']}  取得日: {d['data_fetch_date']}

【① バリュエーション】
PER（実績）: {fmt(d.get('per'))}倍 {per_vs}
PER（予想）: {fmt(d.get('per_fwd'))}倍
PBR: {fmt(d.get('pbr'),',.2f')}倍
EV/EBITDA: {fmt(d.get('ev_ebitda'))}倍

【② 収益性】
ROE: {pct(d.get('roe'))}  ROA: {pct(d.get('roa'))}
営業利益率: {pct(d.get('op_margin'))}

【③ 成長性】
売上成長率（前年比）: {pct(d.get('revenue_yoy'))}
EPS成長率（前年比）: {pct(d.get('eps_yoy'))}
EPS成長率（直近Q）: {pct(d.get('eps_growth_fwd'))}

【④ アナリスト評価】
推奨: {str(d.get('analyst_rec') or 'N/A').upper()}  人数: {d.get('analyst_count') or 'N/A'}人
平均目標株価: ¥{fmt(d.get('analyst_target'),',.0f')}  上昇余地: {upside_str}

【⑤ リスク評価】
決算リスク: {earnings_str}
D/Eレシオ: {fmt(d.get('debt_equity'))}  流動比率: {fmt(d.get('current_ratio'))}倍
配当利回り: {pct(d.get('dividend_yield'))}  配当性向: {pct(d.get('payout_ratio'))}

① PERはセクター平均比で割安/割高か、② ROEのレベルは適切か、
③ 成長トレンドは加速か減速か、④ アナリスト評価は信頼できるか
を構造化して評価し、JSONで回答してください。"""

    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal:
        d = self.gather_data(ticker)
        try:
            parsed = self._safe_parse_json(raw_text)
        except Exception:
            parsed = {"verdict": "HOLD", "confidence": 0.3,
                      "risk_level": "MEDIUM", "summary": "解析失敗",
                      "key_factors": [], "raw_scores": {}}

        # 決算ペナルティ
        base_conf    = float(parsed.get("confidence", 0.3))
        earn_penalty = d.get("earnings_penalty", 0.0) or 0.0
        adj_conf     = max(0.10, min(1.0, base_conf + earn_penalty))
        if earn_penalty != 0.0:
            logger.info(
                f"[fundamentals] {ticker} 決算リスクペナルティ: "
                f"{earn_penalty:+.2f} → {base_conf:.2f}→{adj_conf:.2f}"
            )

        rs = parsed.get("raw_scores", {})
        rs.setdefault("per_ttm",               d.get("per"))
        rs.setdefault("pbr",                   d.get("pbr"))
        rs.setdefault("roe",                   d.get("roe"))

        rs["valuation_score"]    = parsed.get("valuation_score", "C")
        rs["profitability_score"] = parsed.get("profitability_score", "C")
        rs["growth_score"]       = parsed.get("growth_score", "C")
        rs["analyst_score"]      = parsed.get("analyst_score", "C")

        return AgentSignal(
            agent_name=self.name, ticker=ticker,
            verdict=Verdict(parsed.get("verdict", "HOLD")),
            confidence=adj_conf,
            risk_level=RiskLevel(parsed.get("risk_level", "MEDIUM")),
            summary=parsed.get("summary", ""),
            key_factors=parsed.get("key_factors", []),
            raw_scores=rs,
            data_sources=[DataSource(
                name="財務データ（yfinance）",
                date=d["data_fetch_date"],
                note=f"PER={d.get('per','N/A')} ROE={d.get('roe','N/A')} "
                     f"セクター平均PER={d.get('sector_avg_per','N/A')}"
                     if d.get("is_real") else "データ取得失敗"
            )],
        )
