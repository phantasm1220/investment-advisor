"""
agents/technical_agent.py  v6.0 — 構造化LLM版

各指標を構造化フォーマットで個別評価させ、
複合パターンと相互作用をLLMに判断させる。
ルールベースのスコアを「補助情報」として渡し、
LLMが最終的な文脈判断を行う。
"""
import logging
import math
from datetime import date
from typing import Any

import numpy as np
import yfinance as yf

from core.base_agent import BaseAgent
from core.signal import AgentSignal, Verdict, RiskLevel, DataSource

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは株式テクニカルアナリストです。
提供された各テクニカル指標を個別に評価し、指標間の相互作用を考慮した上で最終判断を出してください。

【評価手順（この順番で必ず実行）】
① 各指標をA/B/C/Dで個別評価（A=強い買い, B=買い, C=中立, D=売り）
② 指標間の整合性を確認（例：RSI売られすぎ + MACD上昇転換 = 反発シグナル重複）
③ チャートパターンを判定
④ 総合判断を出す

JSONのみで返答（前置き不要）:
{
  "indicator_scores": {
    "rsi": "A/B/C/D",
    "macd": "A/B/C/D",
    "bollinger": "A/B/C/D",
    "moving_average": "A/B/C/D",
    "volume": "A/B/C/D"
  },
  "pattern": "GOLDEN_CROSS/DEATH_CROSS/DOUBLE_BOTTOM/BREAKOUT/CONSOLIDATION/OTHER",
  "signal_consistency": "HIGH/MEDIUM/LOW",
  "verdict": "STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL",
  "confidence": 0.0~1.0,
  "risk_level": "LOW/MEDIUM/HIGH/EXTREME",
  "summary": "150字以内。各指標の評価と相互作用を具体的な数値で説明",
  "key_factors": ["指標名と数値を含む根拠1", "根拠2", "根拠3"]
}"""

MAX_TOKENS = 280


class TechnicalAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="technical", system_prompt=SYSTEM_PROMPT)
        self.MAX_TOKENS = MAX_TOKENS

    def gather_data(self, ticker: str) -> dict[str, Any]:
        today = date.today().isoformat()
        sym   = ticker if ticker.endswith(".T") else f"{ticker}.T"
        try:
            hist = yf.Ticker(sym).history(period="250d")
        except Exception as e:
            logger.warning(f"[technical] {ticker} 取得失敗: {e}")
            return self._fallback(ticker, today)

        if hist.empty or len(hist) < 10:
            return self._fallback(ticker, today)

        close  = hist["Close"].dropna()
        volume = hist["Volume"].dropna()
        if close.empty:
            return self._fallback(ticker, today)

        cur  = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) >= 2 else cur
        chg  = (cur - prev) / prev * 100 if prev > 0 else 0.0

        ma5   = float(close.tail(5).mean())   if len(close) >= 5   else None
        ma25  = float(close.tail(25).mean())  if len(close) >= 25  else None
        ma75  = float(close.tail(75).mean())  if len(close) >= 75  else None
        ma200 = float(close.tail(200).mean()) if len(close) >= 200 else None

        rsi14 = _rsi(close.values, 14)
        ml, ms, mh = _macd(close)
        bu, bm, bl, bp = _bb(close)

        # チャートパターン検出
        pattern = _detect_pattern(close)

        # ゴールデン/デッドクロス
        cross = "NONE"
        if ma25 and ma75:
            prev_ma25 = float(close.tail(26).head(25).mean()) if len(close) >= 26 else ma25
            prev_ma75 = float(close.tail(76).head(75).mean()) if len(close) >= 76 else ma75
            if prev_ma25 <= prev_ma75 and ma25 > ma75:
                cross = "GOLDEN_CROSS"
            elif prev_ma25 >= prev_ma75 and ma25 < ma75:
                cross = "DEATH_CROSS"

        vol_today = int(volume.iloc[-1]) if not volume.empty else 0
        vol_5d    = int(volume.tail(5).mean()) if len(volume) >= 5 else vol_today
        vol_ratio = vol_today / vol_5d if vol_5d > 0 else 1.0

        h52 = float(close.tail(252).max()) if len(close) >= 20 else cur
        l52 = float(close.tail(252).min()) if len(close) >= 20 else cur

        def pct(a, b): return round((a-b)/b*100, 1) if b else None

        return {
            "ticker": ticker, "data_fetch_date": today, "is_real": True,
            "cur": cur, "chg": round(chg, 2),
            "rsi": round(rsi14, 1) if rsi14 else None,
            "macd_line": round(ml, 2) if ml else None,
            "macd_signal": round(ms, 2) if ms else None,
            "macd_hist": round(mh, 2) if mh else None,
            "bb_upper": round(bu, 1) if bu else None,
            "bb_mid": round(bm, 1) if bm else None,
            "bb_lower": round(bl, 1) if bl else None,
            "bb_pos": round(bp, 2) if bp else None,
            "ma5": round(ma5, 1) if ma5 else None,
            "ma25": round(ma25, 1) if ma25 else None,
            "ma75": round(ma75, 1) if ma75 else None,
            "ma200": round(ma200, 1) if ma200 else None,
            "ma25_pct": pct(cur, ma25), "ma75_pct": pct(cur, ma75),
            "ma200_pct": pct(cur, ma200),
            "cross": cross, "pattern": pattern,
            "h52": round(h52, 1), "l52": round(l52, 1),
            "h52_pct": pct(cur, h52), "l52_pct": pct(cur, l52),
            "vol_ratio": round(vol_ratio, 2),
            "vol_expand": vol_ratio >= 1.5,
            "days": len(close),
        }

    def build_user_prompt(self, ticker: str, d: dict) -> str:
        if not d.get("is_real"):
            return (f"銘柄{ticker}のデータ取得失敗。"
                    "確信度低めでHOLDを返してください。")

        def v(k, f=".1f"):
            val = d.get(k)
            return f"{val:{f}}" if val is not None else "N/A"

        bb_desc = "N/A"
        if d.get("bb_pos") is not None:
            bp = d["bb_pos"]
            bb_desc = f"{bp:.2f}（{'上限付近' if bp>=0.85 else '下限付近' if bp<=0.15 else '中央付近'}）"

        cross_desc = {
            "GOLDEN_CROSS": "🟢 ゴールデンクロス発生中",
            "DEATH_CROSS":  "🔴 デッドクロス発生中",
            "NONE":         "クロスなし",
        }.get(d.get("cross","NONE"), "N/A")

        return f"""【{ticker} テクニカル分析データ】
現在値: ¥{d['cur']:,.0f} ({d['chg']:+.1f}%)  データ取得日: {d['data_fetch_date']}

【RSI】
RSI(14): {v('rsi')}

【MACD】
MACDライン: {v('macd_line')}  シグナル: {v('macd_signal')}  ヒストグラム: {v('macd_hist')}

【ボリンジャーバンド】
上限: ¥{v('bb_upper')}  中央: ¥{v('bb_mid')}  下限: ¥{v('bb_lower')}
バンド内位置: {bb_desc}

【移動平均線】
MA25: ¥{v('ma25')} ({v('ma25_pct')}%)  MA75: ¥{v('ma75')} ({v('ma75_pct')}%)
MA200: ¥{v('ma200')} ({v('ma200_pct')}%)
クロス状況: {cross_desc}

【52週高安値】
52週高値: ¥{v('h52')} ({v('h52_pct')}%)  52週安値: ¥{v('l52')} ({v('l52_pct')}%)

【出来高】
直近/5日平均比: {v('vol_ratio')}x {'← 出来高急増！' if d.get('vol_expand') else ''}

【検出パターン】
{d.get('pattern', 'UNKNOWN')}

① 各指標をA/B/C/Dで評価し、②指標間の相互作用を分析し、
③チャートパターンを考慮した上で総合判断をJSONで回答してください。"""

    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal:
        d = self.gather_data(ticker)
        try:
            parsed = self._safe_parse_json(raw_text)
        except Exception:
            parsed = {"verdict": "HOLD", "confidence": 0.3,
                      "risk_level": "MEDIUM", "summary": "解析失敗",
                      "key_factors": [], "indicator_scores": {},
                      "pattern": "OTHER", "signal_consistency": "LOW"}

        rs = {
            "rsi_14":                d.get("rsi"),  # 数値を保持（orchestrator_helpersが使用）
            "rsi_score":             parsed.get("indicator_scores", {}).get("rsi", "C"),
            "macd_score":            parsed.get("indicator_scores", {}).get("macd", "C"),
            "bb_score":              parsed.get("indicator_scores", {}).get("bollinger", "C"),
            "ma_score":              parsed.get("indicator_scores", {}).get("moving_average", "C"),
            "volume_score":          parsed.get("indicator_scores", {}).get("volume", "C"),
            "pattern":               parsed.get("pattern", "OTHER"),
            "signal_consistency":    parsed.get("signal_consistency", "LOW"),
            "volume_momentum_ratio": d.get("vol_ratio", 1.0),
            "price_vs_ma200_pct":    d.get("ma200_pct"),
            "change_pct":            d.get("chg", 0),
            "volume_surge_flag":     d.get("vol_expand", False),
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
                name="テクニカル分析（構造化LLM）",
                date=d.get("data_fetch_date", today if (today := date.today().isoformat()) else ""),
                note=f"RSI={d.get('rsi','N/A')} Pattern={d.get('pattern','N/A')}"
            )],
        )

    def _fallback(self, ticker, today):
        return {"ticker": ticker, "data_fetch_date": today, "is_real": False,
                "cur": None, "chg": 0, "rsi": None, "macd_line": None,
                "macd_signal": None, "macd_hist": None, "bb_upper": None,
                "bb_mid": None, "bb_lower": None, "bb_pos": None,
                "ma5": None, "ma25": None, "ma75": None, "ma200": None,
                "ma25_pct": None, "ma75_pct": None, "ma200_pct": None,
                "cross": "NONE", "pattern": "UNKNOWN",
                "h52": None, "l52": None, "h52_pct": None, "l52_pct": None,
                "vol_ratio": 1.0, "vol_expand": False, "days": 0}


# ── テクニカル指標計算 ──────────────────────────────────────────

def _rsi(prices, period=14):
    try:
        if len(prices) < period + 1: return None
        d = np.diff(prices[-(period+1):])
        g = np.where(d>0, d, 0).mean()
        l = np.where(d<0, -d, 0).mean()
        return float(100 - 100/(1+g/l)) if l > 0 else (100.0 if g > 0 else 50.0)
    except: return None

def _macd(close, fast=12, slow=26, sig=9):
    try:
        if len(close) < slow+sig: return None, None, None
        ef = close.ewm(span=fast, adjust=False).mean()
        es = close.ewm(span=slow, adjust=False).mean()
        ml = ef - es
        sl = ml.ewm(span=sig, adjust=False).mean()
        return float(ml.iloc[-1]), float(sl.iloc[-1]), float((ml-sl).iloc[-1])
    except: return None, None, None

def _bb(close, period=20, n=2.0):
    try:
        if len(close) < period: return None, None, None, None
        r = close.tail(period)
        m, s = float(r.mean()), float(r.std())
        u, l = m+n*s, m-n*s
        c = float(close.iloc[-1])
        pos = float(np.clip((c-l)/(u-l), 0, 1)) if u != l else 0.5
        return u, m, l, pos
    except: return None, None, None, None

def _detect_pattern(close) -> str:
    """簡易チャートパターン検出"""
    try:
        if len(close) < 20: return "INSUFFICIENT_DATA"
        c = close.values[-20:]
        cur = c[-1]
        mid = c[len(c)//2]
        start = c[0]

        ma5  = c[-5:].mean()
        ma20 = c.mean()

        # ゴールデン/デッドクロスはgather_dataで別途判定
        # 52週高値ブレイクアウト
        if cur >= close.tail(252).max() * 0.99 if len(close) >= 252 else c.max() * 0.99:
            return "BREAKOUT_52W"
        # 上昇トレンド
        if cur > ma5 > ma20 and cur > start:
            return "UPTREND"
        # 下降トレンド
        if cur < ma5 < ma20 and cur < start:
            return "DOWNTREND"
        # ダブルボトム的（下落→回復）
        if c[10] < c[0] * 0.95 and cur > c[10] * 1.05:
            return "DOUBLE_BOTTOM_CANDIDATE"
        # ボックス圏（レンジ）
        if abs(cur - start) / start < 0.03:
            return "CONSOLIDATION"

        return "OTHER"
    except:
        return "UNKNOWN"
