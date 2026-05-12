"""
utils/earnings_calendar.py  v1.0

① 決算スケジュール連動フィルタ
yfinanceから次回決算日を取得し、分析時に警告フラグと確信度調整を行う。

決算前後の取り扱い:
  - 決算3日以内（前後）: ⚠️決算直前/直後 タグ付け、確信度 -10%
  - 決算7日以内（前後）: 📅決算近接 タグ付け、確信度 -5%
"""
import logging
import math
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_cache: dict[str, dict] = {}


def get_earnings_info(ticker: str) -> dict:
    """
    決算情報を取得する。

    Returns:
        {
            "next_earnings_date": "2026-05-15" or None,
            "days_until_earnings": 6 or None,
            "earnings_risk_level": "HIGH" / "MEDIUM" / "LOW" / "NONE",
            "earnings_tag": "⚠️決算3日以内" / "📅決算1週間以内" / "" 
            "confidence_penalty": -0.10 / -0.05 / 0.0,
        }
    """
    code = ticker.replace(".T", "")
    if code in _cache:
        return _cache[code]

    result = _fetch_earnings(code)
    _cache[code] = result
    return result


def _fetch_earnings(code: str) -> dict:
    default = {
        "next_earnings_date": None,
        "days_until_earnings": None,
        "earnings_risk_level": "NONE",
        "earnings_tag": "",
        "confidence_penalty": 0.0,
    }
    try:
        import yfinance as yf
        sym  = f"{code}.T"
        info = yf.Ticker(sym).info or {}
        today = date.today()

        # yfinanceの決算日フィールド
        earnings_ts = (
            info.get("earningsTimestamp") or
            info.get("earningsTimestampStart")
        )
        if not earnings_ts:
            return default

        earnings_date = datetime.fromtimestamp(earnings_ts).date()
        days_diff = (earnings_date - today).days
        abs_days  = abs(days_diff)

        if abs_days <= 3:
            return {
                "next_earnings_date": earnings_date.isoformat(),
                "days_until_earnings": days_diff,
                "earnings_risk_level": "HIGH",
                "earnings_tag": f"⚠️決算{'前' if days_diff >= 0 else '後'}{abs_days}日以内",
                "confidence_penalty": -0.10,
            }
        elif abs_days <= 7:
            return {
                "next_earnings_date": earnings_date.isoformat(),
                "days_until_earnings": days_diff,
                "earnings_risk_level": "MEDIUM",
                "earnings_tag": f"📅決算{'前' if days_diff >= 0 else '後'}{abs_days}日以内",
                "confidence_penalty": -0.05,
            }
        else:
            return {
                "next_earnings_date": earnings_date.isoformat(),
                "days_until_earnings": days_diff,
                "earnings_risk_level": "LOW",
                "earnings_tag": "",
                "confidence_penalty": 0.0,
            }

    except Exception as e:
        logger.debug(f"[earnings] {code} 取得失敗: {e}")
        return default
