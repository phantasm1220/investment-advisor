"""
agents/macro_agent.py  v6.0 — 構造化LLM + リアルタイム市況版

固定値をリアルタイムのyfinanceデータに置き換え、
構造化フォーマットでLLMに銘柄固有のマクロ影響を判断させる。
"""
import logging
from datetime import date
from typing import Any

import yfinance as yf

from core.base_agent import BaseAgent
from core.signal import AgentSignal, Verdict, RiskLevel, DataSource
from utils.market_data import SUBSECTOR_MAP, SECTOR_MAP

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたはマクロ経済アナリストです。
提供されたリアルタイムの市況データと銘柄固有の特性をもとに、
この銘柄がマクロ環境から受ける影響を構造化して評価してください。

【評価手順（この順番で実行）】
① 為替影響: この銘柄の為替感応度タイプと現在の為替動向を掛け合わせて評価
② 金利影響: セクターの金利感応度と現在の金利環境を評価
③ テーマ整合性: AI/半導体/防衛/エネルギー等の現在テーマとの整合性を評価
④ セクターローテーション: 直近20日の資金フローを評価
⑤ 総合判断（以下の基準に従うこと）:
   - テーマSTRONGかつ他要因が中立 → BUY（テーマを優先、確信度0.65〜0.75）
   - テーマSTRONGかつ逆風要因あり → HOLD（確信度0.55〜0.65）
   - テーマMODERATEかつ追い風あり → BUY（確信度0.60〜0.70）
   - テーマMODERATEかつ中立       → HOLD（確信度0.55〜0.60）
   - テーマWEAKまたは逆風が強い   → SELL方向（確信度0.55〜0.65）

JSONのみで返答:
{
  "fx_impact": "POSITIVE_LARGE/POSITIVE_SMALL/NEUTRAL/NEGATIVE_SMALL/NEGATIVE_LARGE",
  "rate_impact": "POSITIVE/NEUTRAL/NEGATIVE",
  "theme_alignment": "STRONG/MODERATE/WEAK/NEGATIVE",
  "rotation_signal": "INFLOW/NEUTRAL/OUTFLOW",
  "verdict": "STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL",
  "confidence": 0.0~1.0,
  "risk_level": "LOW/MEDIUM/HIGH/EXTREME",
  "summary": "150字以内。各要素の評価と具体的数値を含む",
  "key_factors": ["数値付き根拠1", "根拠2", "根拠3"]
}"""

MAX_TOKENS = 280

FX_MAP = {
    "半導体":             ("HIGH_EXPORT",    +0.8, "半導体輸出→円安で業績大幅改善"),
    "電気機器":           ("HIGH_EXPORT",    +0.7, "電気機器輸出→円安恩恵大"),
    "輸送機器（自動車）": ("HIGH_EXPORT",    +0.8, "自動車輸出→円安で業績大幅改善"),
    "機械":               ("MODERATE_EXPORT",+0.4, "機械輸出→円安恩恵中程度"),
    "精密機器":           ("MODERATE_EXPORT",+0.5, "精密機器輸出→円安恩恵"),
    "商社・卸売":         ("FX_NEUTRAL",     +0.2, "資源調達と輸出の相殺"),
    "化学":               ("MODERATE_EXPORT",+0.3, "化学品輸出→円安やや有利"),
    "鉄鋼・非鉄":        ("MODERATE_EXPORT",+0.3, "金属輸出→円安やや有利"),
    "医薬品":             ("MODERATE_EXPORT",+0.4, "医薬品輸出→円安恩恵"),
    "防衛":               ("DOMESTIC",       +0.1, "国内防衛→為替影響小"),
    "銀行":               ("DOMESTIC",       -0.1, "国内銀行→円安はコスト増"),
    "保険":               ("DOMESTIC",        0.0, "国内保険→為替影響中立"),
    "不動産":             ("DOMESTIC",       -0.2, "不動産→円安でコスト増"),
    "電力・原子力":       ("DOMESTIC",       -0.3, "燃料輸入→円安逆風"),
    "AI・テック":         ("MODERATE_EXPORT",+0.5, "IT輸出・グローバル展開"),
    "情報通信":           ("DOMESTIC",        0.0, "通信→国内中心"),
    "小売":               ("DOMESTIC",       -0.2, "輸入品コスト増→円安逆風"),
    "食品":               ("DOMESTIC",       -0.2, "原材料輸入コスト増"),
    "建設・資材":         ("DOMESTIC",       -0.1, "資材輸入コスト増"),
    "運輸":               ("DOMESTIC",       -0.3, "燃料・輸入コスト増"),
}

RATE_MAP = {
    "銀行": "BENEFIT", "保険": "BENEFIT", "証券": "BENEFIT",
    "不動産": "HURT", "建設・資材": "SLIGHT_HURT",
    "電力・原子力": "SLIGHT_HURT", "情報通信": "SLIGHT_HURT",
    "AI・テック": "SLIGHT_HURT", "半導体": "SLIGHT_HURT",
    "輸送機器（自動車）": "SLIGHT_HURT",
}

THEME_MAP = {
    "半導体": "STRONG", "AI・テック": "STRONG",
    "防衛": "STRONG", "電力・原子力": "STRONG", "非鉄金属": "STRONG",
    "精密機器": "MODERATE", "機械": "MODERATE", "電気機器": "MODERATE",
    "輸送機器（自動車）": "MODERATE", "情報通信": "MODERATE",
    "化学": "MODERATE", "商社・卸売": "MODERATE", "銀行": "MODERATE",
    "医薬品": "WEAK", "不動産": "WEAK", "食品": "WEAK",
    "小売": "WEAK", "建設・資材": "WEAK", "運輸": "WEAK",
}


def _get_sector(ticker: str) -> str:
    code = ticker.replace(".T", "")
    if code in SUBSECTOR_MAP:
        return SUBSECTOR_MAP[code]
    try:
        info = yf.Ticker(f"{code}.T").info or {}
        jpx  = info.get("sector") or "不明"
        return SECTOR_MAP.get(jpx, jpx)
    except Exception:
        return "不明"


class MacroAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="macro", system_prompt=SYSTEM_PROMPT)
        self.MAX_TOKENS = MAX_TOKENS
        self._shared_info = {}

    def gather_data(self, ticker: str) -> dict[str, Any]:
        today = date.today().isoformat()

        if self._shared_info:
            sector = self._shared_info.get("sector") or _get_sector(ticker)
            name   = self._shared_info.get("name", ticker)
        else:
            sector = _get_sector(ticker)
            name   = ticker

        fx_type, fx_ben, fx_desc = FX_MAP.get(
            sector, ("MODERATE_EXPORT", 0.2, "感応度中程度"))
        rate  = RATE_MAP.get(sector, "NEUTRAL")
        theme = THEME_MAP.get(sector, "WEAK")

        # ── リアルタイム市況データを取得 ──────────────────────────
        from utils.market_context import get_market_context
        mkt = get_market_context()

        # ── セクターローテーション ─────────────────────────────────
        rotation = {}
        try:
            from utils.sector_rotation import get_sector_rotation
            rotation = get_sector_rotation()
        except Exception:
            pass

        # セクターへの資金フロー
        all_rot    = {n: v for n, v in rotation.get("all", [])}
        sector_rot = all_rot.get(sector)

        return {
            "ticker": ticker, "name": name, "sector": sector,
            "fx_type": fx_type, "fx_desc": fx_desc, "fx_benefit": fx_ben,
            "rate": rate, "theme": theme,
            "sector_rot": sector_rot,
            "rotation_summary": rotation.get("summary", "データなし"),
            # リアルタイム市況
            "usdjpy_cur":    mkt["usdjpy_cur"],
            "usdjpy_1m":     mkt["usdjpy_1m"],
            "us10y":         mkt["us10y"],
            "us10y_1m":      mkt["us10y_1m"],
            "boj_hike_prob": mkt["boj_hike_prob"],
            "sox_ytd":       mkt["sox_ytd"],
            "nikkei_1m":     mkt["nikkei_1m"],
            "vix":           mkt["vix"],
            "data_source":   mkt["source"],
            "data_fetch_date": today,
        }

    def build_user_prompt(self, ticker: str, d: dict) -> str:
        fx_dir  = "円安進行中" if d["usdjpy_1m"] > 0.5 else \
                  "円高進行中" if d["usdjpy_1m"] < -0.5 else "横ばい"
        rate_env = "上昇傾向" if d["us10y_1m"] > 0.1 else \
                   "低下傾向" if d["us10y_1m"] < -0.1 else "横ばい"
        vix_level = "高い（リスクオフ）" if d["vix"] > 25 else \
                    "低い（リスクオン）" if d["vix"] < 15 else "中程度"

        rot_text = ""
        if d["sector_rot"] is not None:
            rot_dir = "資金流入中" if d["sector_rot"] > 1 else \
                      "資金流出中" if d["sector_rot"] < -1 else "中立"
            rot_text = f"{d['sector']}セクター: {d['sector_rot']:+.1f}%（{rot_dir}）"
        else:
            rot_text = d["rotation_summary"]

        return f"""【{ticker}（{d['name']}）マクロ分析データ】
セクター: {d['sector']}  データ: {d['data_fetch_date']}（{d['data_source']}）

【為替環境】
USD/JPY現在値: {d['usdjpy_cur']}円（1ヶ月変化: {d['usdjpy_1m']:+.1f}%、{fx_dir}）
この銘柄の為替感応度: {d['fx_type']}（{d['fx_desc']}）
→ 円安メリット係数: {d['fx_benefit']:+.1f}

【金利環境】
米10年金利: {d['us10y']}%（1ヶ月変化: {d['us10y_1m']:+.3f}%pt、{rate_env}）
日銀利上げ確率: {d['boj_hike_prob']:.0%}
このセクターの金利感応度: {d['rate']}

【テーマ・市場センチメント】
このセクターのテーマ整合性: {d['theme']}
SOX指数 年初来: {d['sox_ytd']:+.1f}%
日経225 1ヶ月: {d['nikkei_1m']:+.1f}%
VIX: {d['vix']}（{vix_level}）

【セクターローテーション（直近20日）】
{rot_text}

① 為替・金利・テーマ・ローテーションの各影響を評価し、
② この銘柄固有の状況（輸出比率・テーマ親和性）を考慮して
JSONで回答してください。"""

    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal:
        d = self.gather_data(ticker)
        try:
            parsed = self._safe_parse_json(raw_text)
        except Exception:
            parsed = {"verdict": "HOLD", "confidence": 0.3,
                      "risk_level": "MEDIUM", "summary": "解析失敗", "key_factors": []}

        rs = {
            "fx_sensitivity":        d["fx_type"],
            "theme_alignment":       d["theme"],
            "rate_sensitivity":      d["rate"],
            "fx_impact":             parsed.get("fx_impact", "NEUTRAL"),
            "rate_impact":           parsed.get("rate_impact", "NEUTRAL"),
            "rotation_signal":       parsed.get("rotation_signal", "NEUTRAL"),
            "regime":                "BALANCED",
            "rate_path_risk":        0.6 if d["us10y_1m"] > 0.1 else 0.3,
            "ai_semicon_theme_strength": 0.8 if d["theme"] == "STRONG" else 0.4,
        }

        return AgentSignal(
            agent_name=self.name, ticker=ticker,
            verdict=Verdict(parsed.get("verdict", "HOLD")),
            confidence=float(parsed.get("confidence", 0.3)),
            risk_level=RiskLevel(parsed.get("risk_level", "MEDIUM")),
            summary=parsed.get("summary", ""),
            key_factors=parsed.get("key_factors", []),
            raw_scores=rs,
            data_sources=[DataSource(
                name=f"マクロ分析（{d['data_source']}）",
                date=d["data_fetch_date"],
                note=f"USD/JPY={d['usdjpy_cur']} US10Y={d['us10y']}% VIX={d['vix']}"
            )],
        )
