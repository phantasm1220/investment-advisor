"""
utils/etf_data.py  v4.7.2

高速化・安定化版:
- yf.download バッチ方式を廃止（MultiIndex問題が継続するため）
- yf.Ticker 個別取得に統一（確実・高速）
- period="5d"（個別株と同じ）
- concurrent.futures で並列取得（最大8スレッド）
- 廃止済み銘柄は即スキップ
"""

import logging
import io
import concurrent.futures
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import yfinance as yf
import requests as req

logger = logging.getLogger(__name__)

JPX_ETF_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xls"
)

# 上場廃止・取引停止済みの既知コード（エラーが出るため除外）
DELISTED_CODES = {
    "2066", "2067", "2068", "2069",  # 廃止確認済み
    "1586", "1587", "1588", "1589",  # 予防的除外
}

# コード: (名称, 対象インデックス, テーマ, 信託報酬%)
ETF_MASTER: dict[str, tuple[str, str, str, float]] = {
    "1306": ("NEXT FUNDS TOPIX連動型ETF",         "TOPIX",        "国内株・広域",       0.066),
    "1321": ("NEXT FUNDS 日経225連動型ETF",        "日経225",       "国内株・大型",       0.066),
    "1330": ("上場インデックスファンド225",          "日経225",       "国内株・大型",       0.066),
    "1308": ("上場インデックスファンドTOPIX",        "TOPIX",        "国内株・広域",       0.066),
    "1570": ("NEXT FUNDS 日経平均レバレッジ",       "日経225×2",    "レバレッジ",          0.880),
    "1571": ("NEXT FUNDS 日経平均インバース",       "日経225×-1",   "インバース",          0.880),
    "1357": ("NEXT FUNDS 日経ダブルインバース",     "日経225×-2",   "インバース",          0.880),
    "1545": ("NEXT FUNDS NASDAQ-100連動型",       "NASDAQ100",    "米国株・テック",       0.220),
    "1546": ("NEXT FUNDS ダウ・ジョーンズ連動",    "DJIA",         "米国株・大型",        0.450),
    "1547": ("上場インデックスファンド米国株式",     "S&P500",       "米国株・広域",        0.165),
    "1557": ("SPDR S&P500 ETF",                 "S&P500",       "米国株・広域",        0.095),
    "2558": ("MAXIS米国株式S&P500上場投信",        "S&P500",       "米国株・広域",        0.077),
    "2563": ("iシェアーズ S&P500米国株ETF",        "S&P500",       "米国株・広域",        0.066),
    "1655": ("iシェアーズ S&P500ETF",             "S&P500",       "米国株・広域",        0.077),
    "1328": ("NEXT FUNDS 金価格連動型ETF",         "金(Gold)",     "コモディティ",        0.500),
    "1540": ("純金上場信託",                        "金(Gold)",     "コモディティ",        0.440),
    "1343": ("NEXT FUNDS 東証REIT指数連動",        "東証REIT",     "国内REIT",            0.176),
    "1597": ("MAXIS Jリート上場投信",              "東証REIT",     "国内REIT",            0.155),
    "1482": ("iシェアーズ 米国債7-10年ETF",        "米国7-10年債", "債券",                0.154),
    "1496": ("iシェアーズ 米ドル建投資適格社債",    "米IG社債",     "債券",                0.308),
    "2516": ("東証グロース250ETF",                 "グロース250",  "国内株・グロース",     0.440),
    "1394": ("SMDAM 東証プライム市場ETF",          "TSEプライム",  "国内株・広域",        0.066),
    "1615": ("NEXT FUNDS 東証銀行業ETF",           "東証銀行業",   "セクター・銀行",       0.330),
    "1617": ("NEXT FUNDS 東証食品ETF",             "東証食品",     "セクター・食品",       0.330),
    "1619": ("NEXT FUNDS 東証電気機器ETF",         "東証電気機器", "セクター・電気機器",   0.330),
    "1621": ("NEXT FUNDS 東証機械ETF",             "東証機械",     "セクター・機械",       0.330),
    "1622": ("NEXT FUNDS 東証医薬品ETF",           "東証医薬品",   "セクター・医薬品",     0.330),
    "1623": ("NEXT FUNDS 東証自動車ETF",           "東証自動車",   "セクター・輸送機器",   0.330),
    "1458": ("楽天ETF-日経レバレッジ指数連動型",   "日経225×2",    "レバレッジ",          0.380),
}

# 確実に上場中の主要ETF（フォールバック用）
RELIABLE_ETF_CODES = list(ETF_MASTER.keys())


@dataclass
class ETFInfo:
    code: str
    name: str
    index_name: str
    theme: str
    expense_ratio: float
    current_price: float
    nav_price: float
    nav_premium_pct: float
    volume_today: int
    volume_avg_5d: int
    volume_ratio: float
    change_pct: float
    rsi_14: Optional[float] = None
    price_vs_ma25_pct: Optional[float] = None
    price_vs_ma75_pct: Optional[float] = None
    is_leveraged: bool = False
    is_sector: bool = False
    raw_history: Optional[object] = field(default=None, repr=False)


class ETFFetcher:

    # 並列取得のスレッド数
    MAX_WORKERS = 8

    def __init__(self):
        self._listing_cache: Optional[pd.DataFrame] = None

    def get_top_volume_etfs(self, top_n: int = 50) -> list[ETFInfo]:
        logger.info(f"ETF出来高ランキング上位{top_n}件を取得中（並列個別取得）...")

        etf_codes = self._get_etf_codes()
        logger.info(f"ETF候補: {len(etf_codes)}件 → 並列{self.MAX_WORKERS}スレッドで取得")

        etfs = self._fetch_parallel(etf_codes)

        if not etfs:
            logger.warning("取得0件 → マスターリストで再試行")
            etfs = self._fetch_parallel(
                [f"{c}.T" for c in RELIABLE_ETF_CODES]
            )

        etfs.sort(key=lambda e: e.volume_today, reverse=True)
        result = etfs[:top_n]
        logger.info(f"ETF上位{len(result)}件の取得完了")
        return result

    def get_etf_info(self, code: str) -> Optional[ETFInfo]:
        sym = code if code.endswith(".T") else f"{code}.T"
        return self._fetch_one(sym)

    # ──────────────────────────────────────────────

    def _get_etf_codes(self) -> list[str]:
        try:
            response = req.get(JPX_ETF_URL, timeout=15)
            response.raise_for_status()
            df = pd.read_excel(io.BytesIO(response.content), engine="xlrd")
            etf_mask = df["市場・商品区分"].str.contains("ETF|ETN", na=False)
            codes = df[etf_mask]["コード"].astype(str).str.zfill(4).tolist()
            # 既知の廃止・停止コードを除外
            codes = [c for c in codes if c not in DELISTED_CODES]
            logger.info(f"JPXからETFコード{len(codes)}件取得（廃止コード除外済み）")
            return [f"{c}.T" for c in codes]
        except Exception as e:
            logger.warning(f"JPX取得失敗 → マスターリストを使用: {e}")
            return [f"{c}.T" for c in ETF_MASTER.keys()]

    def _fetch_parallel(self, tickers: list[str]) -> list[ETFInfo]:
        """並列で個別取得（最大MAX_WORKERSスレッド）"""
        results: list[ETFInfo] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as ex:
            futures = {ex.submit(self._fetch_one, sym): sym for sym in tickers}
            for future in concurrent.futures.as_completed(futures):
                try:
                    etf = future.result(timeout=10)
                    if etf is not None:
                        results.append(etf)
                except Exception:
                    pass
        logger.info(f"  並列取得完了: {len(results)}/{len(tickers)}件成功")
        return results

    def _fetch_one(self, sym: str) -> Optional[ETFInfo]:
        """1件のETFを取得（5日分・個別株と同じ期間）"""
        code = sym.replace(".T", "")
        try:
            tk   = yf.Ticker(sym)
            hist = tk.history(period="5d")   # 30d/10d → 5d に統一
            if hist.empty:
                return None

            info = {}
            try:
                info = tk.info
            except Exception:
                pass

            close_s  = hist["Close"].dropna()
            volume_s = hist["Volume"].dropna()

            if close_s.empty or float(close_s.iloc[-1]) <= 0:
                return None

            current_price = float(close_s.iloc[-1])
            prev_price    = float(close_s.iloc[-2]) if len(close_s) >= 2 else current_price
            change_pct    = (current_price - prev_price) / prev_price * 100 if prev_price else 0.0
            volume_today  = int(volume_s.iloc[-1]) if not volume_s.empty else 0
            volume_avg_5d = int(volume_s.mean())    if not volume_s.empty else 0
            volume_ratio  = volume_today / volume_avg_5d if volume_avg_5d > 0 else 1.0

            # NAV
            nav = (info.get("navPrice")
                   or info.get("regularMarketPreviousClose")
                   or current_price)
            nav_premium_pct = (current_price - nav) / nav * 100 if nav > 0 else 0.0

            # マスターデータで補完
            master       = ETF_MASTER.get(code, ("", "", "その他", 0.0))
            name         = info.get("longName") or info.get("shortName") or master[0] or code
            index_name   = master[1] or info.get("category", "不明")
            theme        = master[2] or "その他"
            expense      = master[3] or (info.get("annualReportExpenseRatio") or 0) * 100

            # RSI（5日分しかないので簡易計算）
            rsi = _calc_rsi(close_s.values) if len(close_s) >= 3 else None

            is_leveraged = any(kw in theme for kw in ["レバレッジ", "インバース"])
            is_sector    = "セクター" in theme

            return ETFInfo(
                code=code, name=name, index_name=index_name,
                theme=theme, expense_ratio=expense,
                current_price=current_price, nav_price=nav,
                nav_premium_pct=nav_premium_pct,
                volume_today=volume_today, volume_avg_5d=volume_avg_5d,
                volume_ratio=volume_ratio, change_pct=change_pct,
                rsi_14=rsi, price_vs_ma25_pct=None, price_vs_ma75_pct=None,
                is_leveraged=is_leveraged, is_sector=is_sector,
            )
        except Exception:
            return None


def _calc_rsi(prices, period: int = 5) -> Optional[float]:
    try:
        import numpy as np
        if len(prices) < 2:
            return None
        deltas = np.diff(prices)
        gains  = max(deltas[deltas > 0].sum() / len(deltas), 0)
        losses = max(-deltas[deltas < 0].sum() / len(deltas), 0)
        if losses == 0:
            return 100.0 if gains > 0 else 50.0
        return round(100 - 100 / (1 + gains / losses), 1)
    except Exception:
        return None
