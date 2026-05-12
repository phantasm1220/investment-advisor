"""
utils/sector_rotation.py  v1.0

③ セクターローテーション検知
過去20日のセクターETF（東証上場）の騰落率・出来高変化から
資金が流入/流出しているセクターを検知する。

使用ETF:
  1306 TOPIX（全体基準）
  1619 電気機器（半導体・IT）
  1623 電力・ガス
  1625 情報通信
  1628 不動産
  1631 銀行
  1632 証券・商品
  1633 保険
  1634 医薬品
  1635 食料品
  1636 建設・資材
  1637 自動車・輸送機器
  1638 機械
  1639 鉄鋼・非鉄
  1640 商社・卸売
"""
import logging
import math
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

# セクターETFコード → セクター名
# 東証セクターETF（上場廃止分を除外・現存するもののみ）
SECTOR_ETF_MAP: dict[str, str] = {
    "1619": "電気機器（半導体・IT）",
    "1631": "銀行",
    "1625": "情報通信",
    "1628": "不動産",
}

_cache: dict = {}
_cache_date: str = ""
_session_cache: dict = {}


def get_sector_rotation() -> dict:
    """
    セクターETFの20日騰落率を計算してローテーション状況を返す。
    結果は当日キャッシュされる。

    Returns:
        {
            "inflow":  [("電気機器", +8.5), ...],  # 資金流入セクター上位3
            "outflow": [("不動産", -4.2), ...],     # 資金流出セクター上位3
            "summary": "半導体・IT に資金集中、不動産から流出",
            "date": "2026-05-09",
        }
    """
    global _cache, _cache_date, _session_cache
    today = date.today().isoformat()
    if _session_cache:
        return _session_cache
    if _cache_date == today and _cache:
        _session_cache = _cache
        return _cache

    try:
        result = _fetch_rotation()
        _cache = result
        _cache_date = today
        return result
    except Exception as e:
        logger.warning(f"[sector_rotation] 取得失敗: {e}")
        return _fallback()


def _fetch_rotation() -> dict:
    import yfinance as yf

    perf: list[tuple[str, float]] = []

    for code, name in SECTOR_ETF_MAP.items():
        try:
            sym  = f"{code}.T"
            hist = yf.Ticker(sym).history(period="25d")
            if hist.empty or len(hist) < 5:
                continue
            close = hist["Close"].dropna()
            if len(close) < 5:
                continue

            # 20日騰落率
            days = min(20, len(close) - 1)
            p_now  = float(close.iloc[-1])
            p_base = float(close.iloc[-days])
            if p_base <= 0 or math.isnan(p_base):
                continue
            ret = (p_now - p_base) / p_base * 100
            perf.append((name, round(ret, 1)))
        except Exception:
            continue

    if not perf:
        return _fallback()

    # TOPIX全体比較のため中央値を基準に相対パフォーマンスへ
    vals = [v for _, v in perf]
    median = sorted(vals)[len(vals) // 2]
    relative = [(n, round(v - median, 1)) for n, v in perf]
    relative.sort(key=lambda x: x[1], reverse=True)

    inflow  = [(n, v) for n, v in relative if v > 0][:3]
    outflow = [(n, v) for n, v in relative[::-1] if v < 0][:3]

    if inflow:
        inflow_str  = "・".join(f"{n}({v:+.1f}%)" for n, v in inflow)
        outflow_str = "・".join(f"{n}({v:+.1f}%)" for n, v in outflow) if outflow else "なし"
        summary = f"流入: {inflow_str} / 流出: {outflow_str}"
    else:
        summary = "セクター間の資金移動は限定的"

    return {
        "inflow":  inflow,
        "outflow": outflow,
        "all":     relative,
        "summary": summary,
        "date":    date.today().isoformat(),
    }


def _fallback() -> dict:
    return {
        "inflow": [], "outflow": [], "all": [],
        "summary": "セクターローテーションデータを取得できませんでした",
        "date": date.today().isoformat(),
    }
