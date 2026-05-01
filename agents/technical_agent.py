"""agents/technical_agent.py v4.9 — プロンプト最適化版"""
import logging
import math
from datetime import date
from typing import Any, Optional

import yfinance as yf
import numpy as np
import pandas as pd

from core.base_agent import BaseAgent
from core.signal import AgentSignal, Verdict, RiskLevel, DataSource

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """テクニカル分析の専門家として、提供された実データから銘柄固有の需給・タイミングを判断してください。
summaryとkey_factorsには必ず具体的な数値を引用すること。
JSON形式のみで返答:
{"verdict":"BUY|SELL|HOLD|STRONG_BUY|STRONG_SELL","confidence":0.0~1.0,"risk_level":"LOW|MEDIUM|HIGH|EXTREME","summary":"200字以内","key_factors":["数値引用した根拠1","根拠2","根拠3"],"raw_scores":{"rsi_14":数値,"value_area_position":"ABOVE|INSIDE|BELOW","poc":数値,"sns_sentiment_score":0.5,"volume_momentum":"EXPANDING|CONTRACTING|NEUTRAL","overheat_warning":true/false,"volume_momentum_ratio":数値,"price_vs_ma200_pct":数値,"change_pct":数値}}"""

MAX_TOKENS = 512


class TechnicalAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="technical", system_prompt=SYSTEM_PROMPT)
        self.MAX_TOKENS = MAX_TOKENS

    def gather_data(self, ticker: str) -> dict[str, Any]:
        today = date.today().isoformat()
        sym   = ticker if ticker.endswith(".T") else f"{ticker}.T"
        try:
            hist = yf.Ticker(sym).history(period="250d", interval="1d")
        except Exception as e:
            logger.warning(f"[technical] {ticker} 取得失敗: {e}")
            return self._fallback(ticker, today)

        if hist.empty or len(hist) < 5:
            return self._fallback(ticker, today)

        close  = hist["Close"].dropna()
        volume = hist["Volume"].dropna()

        if close.empty:
            return self._fallback(ticker, today)

        cur = float(close.iloc[-1])
        if math.isnan(cur) or cur <= 0:
            return self._fallback(ticker, today)

        prev       = float(close.iloc[-2]) if len(close) >= 2 else cur
        change_pct = (cur - prev) / prev * 100 if prev > 0 else 0.0

        ma25   = float(close.tail(25).mean())  if len(close) >= 25  else None
        ma75   = float(close.tail(75).mean())  if len(close) >= 75  else None
        ma200  = float(close.tail(200).mean()) if len(close) >= 200 else None

        rsi14      = _rsi(close.values, 14)
        rsi_prev   = _rsi(close.values[:-1], 14) if len(close) > 14 else rsi14
        ml, ms, mh = _macd(close)
        bu, bm, bl, bp = _bb(close)

        vol_today  = int(volume.iloc[-1]) if not volume.empty else 0
        vol_5d_avg = int(volume.tail(5).mean()) if len(volume) >= 5 else vol_today
        vol_ratio  = vol_today / vol_5d_avg if vol_5d_avg > 0 else 1.0

        poc   = float(close.tail(20).mean()) if len(close) >= 20 else cur
        h52   = float(close.tail(252).max()) if len(close) >= 20 else None
        l52   = float(close.tail(252).min()) if len(close) >= 20 else None

        def pct(a, b): return round((a - b) / b * 100, 1) if b else None

        return {
            "ticker": ticker, "data_fetch_date": today, "is_real": True,
            "cur": cur, "chg": round(change_pct, 2),
            "rsi": round(rsi14, 1) if rsi14 else None,
            "rsi_p": round(rsi_prev, 1) if rsi_prev else None,
            "macd_h": round(mh, 2) if mh else None,
            "bb_u": round(bu, 1) if bu else None,
            "bb_l": round(bl, 1) if bl else None,
            "bb_pos": round(bp, 2) if bp else None,
            "ma25": round(ma25, 1) if ma25 else None,
            "ma75": round(ma75, 1) if ma75 else None,
            "ma200": round(ma200, 1) if ma200 else None,
            "ma25p": pct(cur, ma25),  "ma75p": pct(cur, ma75),  "ma200p": pct(cur, ma200),
            "h52": round(h52, 1) if h52 else None,  "l52": round(l52, 1) if l52 else None,
            "h52p": pct(cur, h52),  "l52p": pct(cur, l52),
            "vol_r": round(vol_ratio, 2),
            "vol_t": "EXPANDING" if vol_ratio >= 1.2 else ("CONTRACTING" if vol_ratio <= 0.8 else "NEUTRAL"),
            "poc": round(poc, 1),
            "vol_area": "ABOVE" if cur > poc * 1.02 else ("BELOW" if cur < poc * 0.98 else "INSIDE"),
            "overheat": (rsi14 or 0) > 75 or ((rsi14 or 0) > 68 and vol_ratio > 1.5),
            "oversold": (rsi14 or 0) < 25,
            "days": len(close),
        }

    def _fallback(self, ticker, today):
        return {"ticker": ticker, "data_fetch_date": today, "is_real": False,
                "cur": None, "chg": 0, "rsi": None, "rsi_p": None, "macd_h": None,
                "bb_u": None, "bb_l": None, "bb_pos": None,
                "ma25": None, "ma75": None, "ma200": None,
                "ma25p": None, "ma75p": None, "ma200p": None,
                "h52": None, "l52": None, "h52p": None, "l52p": None,
                "vol_r": 1.0, "vol_t": "NEUTRAL", "poc": None, "vol_area": "INSIDE",
                "overheat": False, "oversold": False, "days": 0}

    def build_user_prompt(self, ticker: str, d: dict) -> str:
        if not d.get("is_real") or d.get("cur") is None:
            return f"銘柄{ticker}のデータ取得失敗。確信度低でHOLDを返してください。"

        def p(v, f=".1f", s=""): return f"{v:{f}}{s}" if v is not None else "N/A"
        def pp(v): return f"{v:+.1f}%" if v is not None else "N/A"

        alert = ("⚠️過熱" if d["overheat"] else "⚠️売られすぎ" if d["oversold"] else "なし")
        rsi_note = ("買われすぎ圏" if (d["rsi"] or 0)>75 else "売られすぎ圏" if (d["rsi"] or 0)<25 else "中立圏")

        return f"""銘柄{ticker} テクニカル実データ({d['data_fetch_date']}, {d['days']}日分)

現在値¥{d['cur']:,.0f} 前日比{d['chg']:+.2f}%
RSI(14):{p(d['rsi'])}({rsi_note}) 前回:{p(d['rsi_p'])} MACDヒスト:{p(d['macd_h'],'+.2f')}
BB上限¥{p(d['bb_u'],',.0f')} 下限¥{p(d['bb_l'],',.0f')} 位置:{p(d['bb_pos'])}
MA25乖離:{pp(d['ma25p'])}(¥{p(d['ma25'],',.0f')}) MA75:{pp(d['ma75p'])} MA200:{pp(d['ma200p'])}(¥{p(d['ma200'],',.0f')})
52週高値¥{p(d['h52'],',.0f')}({pp(d['h52p'])}) 安値¥{p(d['l52'],',.0f')}({pp(d['l52p'])})
出来高比率:{d['vol_r']}x {d['vol_t']} POC¥{p(d['poc'],',.0f')} 位置:{d['vol_area']}
過熱判定:{alert}

この実データから銘柄固有のテクニカル分析を行い、具体的数値を引用してJSONで回答してください。"""

    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal:
        d = self.gather_data(ticker)
        try:
            parsed = self._safe_parse_json(raw_text)
        except Exception as e:
            logger.warning(f"[technical] パース失敗: {e}")
            parsed = {"verdict": "HOLD", "confidence": 0.25, "risk_level": "MEDIUM",
                      "summary": "解析エラー。", "key_factors": [], "raw_scores": {}}
        rs = parsed.setdefault("raw_scores", {})
        rs.setdefault("rsi_14",               d.get("rsi"))
        rs.setdefault("volume_momentum_ratio", d.get("vol_r", 1.0))
        rs.setdefault("price_vs_ma200_pct",    d.get("ma200p"))
        rs.setdefault("change_pct",            d.get("chg", 0))
        rs.setdefault("volume_momentum",       d.get("vol_t", "NEUTRAL"))
        rs.setdefault("value_area_position",   d.get("vol_area", "INSIDE"))
        rs.setdefault("overheat_warning",      d.get("overheat", False))
        return AgentSignal(
            agent_name=self.name, ticker=ticker,
            verdict=Verdict(parsed.get("verdict", "HOLD")),
            confidence=float(parsed.get("confidence", 0.3)),
            risk_level=RiskLevel(parsed.get("risk_level", "MEDIUM")),
            summary=parsed.get("summary", ""),
            key_factors=parsed.get("key_factors", []),
            raw_scores=rs,
            data_sources=[DataSource(name="株価・テクニカル", date=d["data_fetch_date"],
                note=f"RSI={d.get('rsi','N/A')} MA200乖離={d.get('ma200p','N/A')}%")],
        )


def _rsi(prices, period=14):
    try:
        if len(prices) < period + 1: return None
        d = np.diff(prices[-(period+1):])
        g, l = np.where(d>0,d,0).mean(), np.where(d<0,-d,0).mean()
        return float(100 - 100/(1 + g/l)) if l > 0 else (100.0 if g > 0 else 50.0)
    except: return None

def _macd(close, fast=12, slow=26, sig=9):
    try:
        if len(close) < slow+sig: return None, None, None
        ef = close.ewm(span=fast, adjust=False).mean()
        es = close.ewm(span=slow, adjust=False).mean()
        ml = ef - es; sl = ml.ewm(span=sig, adjust=False).mean()
        return float(ml.iloc[-1]), float(sl.iloc[-1]), float((ml-sl).iloc[-1])
    except: return None, None, None

def _bb(close, period=20, n=2.0):
    try:
        if len(close) < period: return None, None, None, None
        r = close.tail(period); m = float(r.mean()); s = float(r.std())
        u, l = m+n*s, m-n*s; c = float(close.iloc[-1])
        return u, m, l, float(np.clip((c-l)/(u-l), 0, 1)) if u != l else 0.5
    except: return None, None, None, None
