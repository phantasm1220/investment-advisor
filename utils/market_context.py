"""
utils/market_context.py  v2.0

マクロ市況データをリアルタイムで取得する。
yfinanceから USD/JPY・米10年金利・SOX指数・日経225を取得。
当日キャッシュ付き（同日内は1回だけ取得）。
"""
import logging
import math
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

_cache: dict = {}
_cache_date: str = ""
_session_cache: dict = {}  # セッション内永続


def get_market_context() -> dict:
    """
    リアルタイム市況データを返す。当日キャッシュ付き。

    Returns:
        usdjpy_cur:   USD/JPY 現在値
        usdjpy_1m:    USD/JPY 1ヶ月変化率（%）
        us10y:        米10年金利（%）
        us10y_1m:     米10年金利 1ヶ月変化（%pt）
        boj_hike_prob: 日銀利上げ確率（推定）
        sox_ytd:      SOX指数 年初来騰落率（%）
        nikkei_1m:    日経225 1ヶ月騰落率（%）
        vix:          VIX指数
    """
    global _cache, _cache_date, _session_cache
    today = date.today().isoformat()
    if _session_cache:
        return _session_cache
    if _cache_date == today and _cache:
        _session_cache = _cache
        return _cache

    ctx = _fetch()
    _cache         = ctx
    _cache_date    = today
    _session_cache = ctx
    return ctx


def _fetch() -> dict:
    try:
        import yfinance as yf

        def _get_change(sym: str, period: str = "1mo") -> tuple[float, float]:
            """現在値と変化率を返す"""
            try:
                hist = yf.Ticker(sym).history(period=period)
                if hist.empty or len(hist) < 2:
                    return (None, None)
                cur  = float(hist["Close"].dropna().iloc[-1])
                base = float(hist["Close"].dropna().iloc[0])
                chg  = (cur - base) / abs(base) * 100 if base != 0 else 0.0
                return (cur, round(chg, 2))
            except Exception:
                return (None, None)

        # USD/JPY
        usdjpy_cur, usdjpy_1m = _get_change("JPY=X", "1mo")

        # 米10年金利（TNX = 10年金利 × 10）
        us10y_raw, us10y_chg = _get_change("^TNX", "1mo")
        us10y     = round(us10y_raw / 10, 3) if us10y_raw else None
        us10y_1m  = round(us10y_chg / 10, 3) if us10y_chg else None

        # SOX指数（フィラデルフィア半導体）
        try:
            sox_hist = yf.Ticker("^SOX").history(period="ytd")
            if not sox_hist.empty and len(sox_hist) >= 2:
                sox_base = float(sox_hist["Close"].dropna().iloc[0])
                sox_cur  = float(sox_hist["Close"].dropna().iloc[-1])
                sox_ytd  = round((sox_cur - sox_base) / sox_base * 100, 1)
            else:
                sox_ytd = None
        except Exception:
            sox_ytd = None

        # 日経225
        _, nikkei_1m = _get_change("^N225", "1mo")

        # VIX
        vix_cur, _ = _get_change("^VIX", "5d")

        # 日銀利上げ確率（10年金利の変化から推定）
        if us10y and us10y_1m:
            boj_hike_prob = min(0.95, max(0.05, 0.5 + us10y_1m * 0.3))
        else:
            boj_hike_prob = 0.5

        ctx = {
            "usdjpy_cur":    round(usdjpy_cur, 2)  if usdjpy_cur  else 148.0,
            "usdjpy_1m":     round(usdjpy_1m, 2)   if usdjpy_1m   else 0.0,
            "us10y":         us10y                  if us10y       else 4.5,
            "us10y_1m":      us10y_1m               if us10y_1m    else 0.0,
            "boj_hike_prob": round(boj_hike_prob, 2),
            "sox_ytd":       sox_ytd                if sox_ytd is not None else 0.0,
            "nikkei_1m":     round(nikkei_1m, 2)   if nikkei_1m   else 0.0,
            "vix":           round(vix_cur, 1)      if vix_cur     else 20.0,
            "source":        "yfinance_live",
        }

        logger.info(
            f"[MarketContext] USD/JPY={ctx['usdjpy_cur']} "
            f"US10Y={ctx['us10y']}% SOX YTD={ctx['sox_ytd']}% "
            f"VIX={ctx['vix']}"
        )
        return ctx

    except Exception as e:
        logger.warning(f"[MarketContext] 取得失敗、フォールバック使用: {e}")
        return _fallback()


def _fallback() -> dict:
    return {
        "usdjpy_cur": 148.0, "usdjpy_1m": 0.0,
        "us10y": 4.5,        "us10y_1m": 0.0,
        "boj_hike_prob": 0.5, "sox_ytd": 0.0,
        "nikkei_1m": 0.0,    "vix": 20.0,
        "source": "fallback",
    }
