"""agents/macro_agent.py v4.9 — プロンプト最適化版"""
import logging
import json
from datetime import date
from typing import Any

import yfinance as yf

from core.base_agent import BaseAgent
from core.signal import AgentSignal, Verdict, RiskLevel, DataSource
from utils.market_theme_fetcher import get_themes_text as _themes
from utils.market_context import MARKET_REGIME
from utils.market_data import SUBSECTOR_MAP, SECTOR_MAP

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """マクロ経済アナリストとして、この銘柄・セクター固有のマクロ影響を評価してください。
市況全体ではなく「この銘柄がマクロ環境から受ける具体的な影響」に集中して回答すること。
JSON形式のみで返答:
{"verdict":"BUY|SELL|HOLD|STRONG_BUY|STRONG_SELL","confidence":0.0~1.0,"risk_level":"LOW|MEDIUM|HIGH|EXTREME","summary":"200字以内。銘柄固有のマクロ影響を具体的に","key_factors":["銘柄固有の根拠1","根拠2","根拠3"],"raw_scores":{"surprise_index":数値,"regime":"INFLATION_FEAR|RECESSION_FEAR|BALANCED","geopolitical_risk_score":0~1,"rate_path_risk":0~1,"fx_sensitivity":"HIGH_EXPORT|MODERATE_EXPORT|DOMESTIC|FX_NEUTRAL","theme_alignment":"STRONG|MODERATE|WEAK|NEGATIVE","ai_semicon_theme_strength":0~1,"energy_theme_strength":0~1}}"""

MAX_TOKENS = 512

FX_MAP = {
    "半導体":      ("HIGH_EXPORT",     +0.9, "円安大恩恵"),
    "電気機器":    ("HIGH_EXPORT",     +0.7, "輸出多、円安恩恵"),
    "輸送機器":    ("HIGH_EXPORT",     +0.8, "自動車、円安恩恵大"),
    "機械":        ("MODERATE_EXPORT", +0.5, "輸出・内需バランス"),
    "精密機器":    ("MODERATE_EXPORT", +0.5, "輸出中程度"),
    "化学":        ("MODERATE_EXPORT", +0.3, "原材料輸入で一部相殺"),
    "情報通信":    ("DOMESTIC",        -0.2, "国内中心、円安コスト増"),
    "AI・テック":  ("MODERATE_EXPORT", +0.4, "海外収益で一部恩恵"),
    "銀行":        ("FX_NEUTRAL",       0.0, "金利上昇が主要因"),
    "メガバンク":  ("FX_NEUTRAL",      +0.2, "金利上昇で利ざや拡大"),
    "不動産":      ("DOMESTIC",        -0.3, "金利上昇が最大リスク"),
    "電力・原子力":("DOMESTIC",        -0.4, "燃料輸入コスト増"),
    "医薬品":      ("MODERATE_EXPORT", +0.4, "海外展開企業は恩恵"),
    "防衛":        ("DOMESTIC",        +0.1, "防衛費増額が主因"),
    "総合商社":    ("HIGH_EXPORT",     +0.6, "資源・海外資産保有"),
    "非鉄金属":    ("MODERATE_EXPORT", +0.3, "資源価格・為替両方影響"),
}
RATE_MAP = {
    "銀行":"BENEFIT","メガバンク":"BENEFIT","保険":"BENEFIT",
    "不動産":"HURT","国内REIT":"HURT",
    "半導体":"NEUTRAL","輸送機器":"NEUTRAL","総合商社":"BENEFIT",
    "情報通信":"SLIGHT_HURT","AI・テック":"SLIGHT_HURT",
}
THEME_MAP = {
    "半導体":"STRONG","AI・テック":"STRONG","電力・原子力":"STRONG","防衛":"STRONG",
    "電気機器":"MODERATE","精密機器":"MODERATE","非鉄金属":"MODERATE",
    "輸送機器":"WEAK","銀行":"WEAK","メガバンク":"WEAK","不動産":"NEGATIVE","医薬品":"WEAK",
}


class MacroAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="macro", system_prompt=SYSTEM_PROMPT)
        self.MAX_TOKENS = MAX_TOKENS

    def gather_data(self, ticker: str) -> dict[str, Any]:
        today = date.today().isoformat()
        sector = _get_sector(ticker)
        fx_type, fx_ben, fx_desc = FX_MAP.get(sector, ("MODERATE_EXPORT", 0.2, "感応度中程度"))
        rate = RATE_MAP.get(sector, "NEUTRAL")
        theme = THEME_MAP.get(sector, "WEAK")
        name = _get_name(ticker)
        return {
            "ticker": ticker, "name": name, "sector": sector,
            "fx_type": fx_type, "fx_desc": fx_desc, "fx_benefit": fx_ben,
            "rate": rate, "theme": theme,
            "data_fetch_date": today,
            "usdjpy_1m": +2.3, "usdjpy_cur": 148.5,
            "us10y": 4.65, "us10y_1m": +0.28,
            "vix": 18.5, "boj_hike_prob": 0.72,
            "sox_ytd": +18.5,
            "surprises": [
                ("米国CPI", "2026-04-10", +0.3),
                ("米国雇用統計", "2026-04-04", +42.2),
                ("日本GDP", "2026-01-30", -33.3),
            ],
        }

    def build_user_prompt(self, ticker: str, d: dict) -> str:
        # 為替影響を計算
        fx_dir = "円安進行中" if d["usdjpy_1m"] > 0 else "円高方向"
        if d["fx_benefit"] > 0.5: fx_effect = "→業績への大きなプラス"
        elif d["fx_benefit"] < -0.2: fx_effect = "→コスト増のマイナス"
        else: fx_effect = "→影響軽微"

        rate_txt = {"BENEFIT":"✅金利上昇恩恵","HURT":"⚠️金利上昇逆風",
                    "SLIGHT_HURT":"🟡金利上昇やや逆風","NEUTRAL":"→金利影響中立"}.get(d["rate"],"→中立")
        theme_txt = {"STRONG":"🚀現在テーマに直結","MODERATE":"📈テーマと一定関連",
                     "WEAK":"→テーマとの直接関連薄い","NEGATIVE":"📉テーマが逆風"}.get(d["theme"],"→関連薄")

        pos_surp = sum(1 for _,_,v in d["surprises"] if v > 0)

        # テーマは簡潔版のみ（market_theme_fetcher は呼ばない→高速化）
        return f"""銘柄{ticker}({d['name']}) セクター:{d['sector']} のマクロ影響評価

【銘柄固有のマクロ感応度】
為替({d['fx_type']}): USD/JPY{d['usdjpy_cur']}円({fx_dir} {d['usdjpy_1m']:+.1f}%/月) {d['fx_desc']} {fx_effect}
金利: 米10年{d['us10y']}%({d['us10y_1m']:+.2f}%/月) 日銀利上げ確率{d['boj_hike_prob']:.0%} → {rate_txt}
テーマ: SOX YTD{d['sox_ytd']:+.1f}% → {theme_txt}(AI/半導体/エネルギー)

【経済指標サプライズ】ポジティブ{pos_surp}/3
{chr(10).join(f"  {n}({dt}): {v:+.1f}%" for n,dt,v in d['surprises'])}

{ticker}({d['sector']})固有の視点で、為替/金利/テーマそれぞれの影響をJSONで回答してください。"""

    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal:
        d = self.gather_data(ticker)
        try:
            parsed = self._safe_parse_json(raw_text)
        except Exception as e:
            logger.warning(f"[macro] パース失敗: {e}")
            parsed = {"verdict":"HOLD","confidence":0.3,"risk_level":"HIGH",
                      "summary":"解析エラー。","key_factors":[],"raw_scores":{}}
        rs = parsed.setdefault("raw_scores", {})
        rs.setdefault("fx_sensitivity", d["fx_type"])
        rs.setdefault("theme_alignment", d["theme"])
        rs.setdefault("rate_sensitivity", d["rate"])
        return AgentSignal(
            agent_name=self.name, ticker=ticker,
            verdict=Verdict(parsed.get("verdict","HOLD")),
            confidence=float(parsed.get("confidence",0.3)),
            risk_level=RiskLevel(parsed.get("risk_level","MEDIUM")),
            summary=parsed.get("summary",""),
            key_factors=parsed.get("key_factors",[]),
            raw_scores=rs,
            data_sources=[DataSource(name=n, date=dt, note=f"サプライズ{v:+.1f}%")
                          for n,dt,v in d["surprises"]] + [
                          DataSource(name=f"セクター({d['sector']})", date=d["data_fetch_date"],
                          note=f"FX={d['fx_type']} テーマ={d['theme']}")],
        )


def _get_sector(ticker: str) -> str:
    code = ticker.replace(".T","")
    if code in SUBSECTOR_MAP: return SUBSECTOR_MAP[code]
    try:
        info = yf.Ticker(f"{code}.T").info or {}
        return SECTOR_MAP.get(info.get("sector",""), info.get("sector","不明"))
    except: return "不明"

def _get_name(ticker: str) -> str:
    code = ticker.replace(".T","")
    try:
        info = yf.Ticker(f"{code}.T").info or {}
        return info.get("longName") or info.get("shortName") or ticker
    except: return ticker
