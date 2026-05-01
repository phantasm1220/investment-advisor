"""
utils/market_data.py  v4.3.2

修正点:
- yfinance 0.2.x のマルチTickerダウンロード時のDataFrame構造に対応
  (MultiIndex列: (Price, Ticker) 形式)
- バッチ間の time.sleep を完全撤廃（課金プランのため不要）
- バッチサイズを200に拡大してバッチ数を削減
- 個別Ticker方式のフォールバックを追加
"""

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf
import requests as req
import io

logger = logging.getLogger(__name__)

JPX_LISTING_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xls"
)

SECTOR_MAP = {
    "水産・農林業": "農林水産", "鉱業": "鉱業", "建設業": "建設",
    "食料品": "食品", "繊維製品": "繊維", "パルプ・紙": "紙",
    "化学": "化学", "医薬品": "医薬品", "石油・石炭製品": "石油",
    "ゴム製品": "ゴム", "ガラス・土石製品": "窯業", "鉄鋼": "鉄鋼",
    "非鉄金属": "非鉄金属", "金属製品": "金属", "機械": "機械",
    "電気機器": "電気機器", "輸送用機器": "輸送機器", "精密機器": "精密機器",
    "その他製品": "その他製品", "電気・ガス業": "電力・ガス",
    "陸運業": "陸運", "海運業": "海運", "空運業": "空運",
    "倉庫・運輸関連業": "倉庫", "情報・通信業": "情報通信",
    "卸売業": "卸売", "小売業": "小売", "銀行業": "銀行",
    "証券、商品先物取引業": "証券", "保険業": "保険",
    "その他金融業": "金融", "不動産業": "不動産", "サービス業": "サービス",
}

# ────────────────────────────────────────────────────────────────
# サブセクターマッピング（銘柄コード → 詳細テーマ）
#
# JPXの33業種区分は大括りすぎるため、実態に合った「投資テーマ」として
# 個別銘柄コードからサブセクターを識別する。
#
# 例: 東京エレクトロン(8035)・アドバンテスト(6857) は「電気機器」だが
#     実態は「半導体製造装置」。この辞書で「半導体」として再分類。
# ────────────────────────────────────────────────────────────────
SUBSECTOR_MAP: dict[str, str] = {
    # ── 半導体・製造装置（AI需要の直接受益） ──
    "8035": "半導体",    # 東京エレクトロン
    "6857": "半導体",    # アドバンテスト
    "6920": "半導体",    # レーザーテック
    "6723": "半導体",    # ルネサスエレクトロニクス
    "6963": "半導体",    # ローム
    "6770": "半導体",    # アルプスアルパイン
    "4063": "半導体",    # 信越化学（半導体ウェハ）
    "4004": "半導体",    # レゾナック（半導体材料）
    "4188": "半導体",    # 三菱ケミカル（半導体材料）
    "6146": "半導体",    # ディスコ（半導体研削）
    "7735": "半導体",    # SCREENホールディングス
    "6869": "半導体",    # シスメックス（半導体含む精密）
    "3659": "半導体",    # ネクソン（半導体投資含む）

    # ── AI・データセンター関連 ──
    "9984": "AI・テック",   # ソフトバンクグループ（ARM）
    "4307": "AI・テック",   # 野村総合研究所
    "3697": "AI・テック",   # SHIFT
    "4716": "AI・テック",   # 日本オラクル
    "9613": "AI・テック",   # NTTデータ
    "4689": "AI・テック",   # LY Corporation（ヤフー）
    "4755": "AI・テック",   # 楽天グループ

    # ── 電力・エネルギー（AI電力需要の恩恵） ──
    "9501": "電力・原子力",  # 東京電力
    "9503": "電力・原子力",  # 関西電力
    "9502": "電力・原子力",  # 中部電力
    "9531": "電力・原子力",  # 東京ガス
    "1605": "電力・原子力",  # INPEX

    # ── 非鉄金属（半導体材料・EV電池） ──
    "5713": "非鉄金属",   # 住友金属鉱山
    "5706": "非鉄金属",   # 三井金属鉱業
    "5707": "非鉄金属",   # 東邦亜鉛
    "5714": "非鉄金属",   # DOWAホールディングス
    "5727": "非鉄金属",   # 東洋製鋼
    "5801": "非鉄金属",   # 古河電気工業（銅・EV）
    "5802": "非鉄金属",   # 住友電気工業

    # ── 防衛・宇宙 ──
    "7011": "防衛",   # 三菱重工業
    "7013": "防衛",   # IHI
    "6952": "防衛",   # カシオ（防衛関連含む）
    "7012": "防衛",   # 川崎重工業

    # ── 商社（資源・AI投資） ──
    "8031": "総合商社",  # 三井物産
    "8053": "総合商社",  # 住友商事
    "8001": "総合商社",  # 伊藤忠商事
    "8002": "総合商社",  # 丸紅
    "8058": "総合商社",  # 三菱商事

    # ── メガバンク（日銀利上げ恩恵） ──
    "8306": "メガバンク",  # 三菱UFJ
    "8316": "メガバンク",  # 三井住友FG
    "8411": "メガバンク",  # みずほFG
}


def get_effective_sector(code: str, jpx_sector: str) -> str:
    """
    銘柄コードとJPX業種から実質的な投資テーマ（サブセクター）を返す。
    SUBSECTOR_MAPに登録されている銘柄はサブセクターを優先し、
    それ以外はJPX業種をそのまま返す。
    """
    return SUBSECTOR_MAP.get(code, jpx_sector)


@dataclass
class StockInfo:
    code: str
    name: str
    sector: str
    current_price: float
    volume_today: int
    volume_avg_5d: int
    volume_ratio: float
    change_pct: float
    market_cap: float


class MarketDataFetcher:

    def __init__(self):
        self._listing_cache: Optional[pd.DataFrame] = None

    # ──────────────────────────────────────────────

    def get_top_volume_stocks(self, top_n: int = 100) -> list[StockInfo]:
        logger.info(f"出来高ランキング上位{top_n}銘柄を取得中...")

        listing = self._get_listing()
        if listing is not None and not listing.empty:
            prime = listing[
                listing["市場・商品区分"].str.contains("プライム", na=False)
            ]
            # コード順の偏りをシャッフルで排除し、全プライム銘柄を候補とする
            # 出来高は fetch_volume_data → sort で決まるため候補の並びは関係ない
            import random as _rnd
            codes_all = (
                prime["コード"].astype(str).str.zfill(4) + ".T"
            ).tolist()
            _rnd.shuffle(codes_all)  # コード順の偏りを除去
            candidates = codes_all   # 全件対象（上限なし）
            logger.info(f"JPXプライム全{len(candidates)}銘柄を候補に設定（シャッフル済み）")
        else:
            candidates = self._fallback_tickers()
            logger.info(f"フォールバックリスト({len(candidates)}銘柄)を使用")

        stocks = self._fetch_volume_data(candidates)

        if not stocks:
            logger.warning("バッチ取得が0件。個別Ticker方式で再試行...")
            stocks = self._fetch_individual(self._fallback_tickers()[:50])

        if not stocks:
            logger.error("全取得方式で0件。yfinanceの接続を確認してください。")
            return []

        stocks.sort(key=lambda s: s.volume_today, reverse=True)
        result = stocks[:top_n]
        logger.info(f"出来高上位{len(result)}銘柄の取得完了")
        return result

    def get_stock_info(self, code: str) -> Optional[StockInfo]:
        ticker_sym = code if code.endswith(".T") else f"{code}.T"
        logger.info(f"[{code}] 銘柄情報を取得中...")
        try:
            tk   = yf.Ticker(ticker_sym)
            hist = tk.history(period="5d")
            info = tk.info

            if hist.empty:
                logger.warning(f"[{code}] 株価データが取得できませんでした")
                return None

            # NaN・無効値チェック付きで価格を取得
            close_series = hist["Close"].dropna()
            if close_series.empty:
                logger.warning(f"[{code}] 有効な終値データがありません")
                return None

            current_price = float(close_series.iloc[-1])
            if current_price != current_price or current_price <= 0:  # NaN or invalid
                logger.warning(f"[{code}] 株価が無効な値です: {current_price}")
                return None

            prev_price    = float(close_series.iloc[-2]) if len(close_series) >= 2 else current_price
            import math as _m
            _cp = current_price - prev_price
            change_pct = (_cp / prev_price * 100
                          if prev_price and not _m.isnan(prev_price) and prev_price > 0
                          else 0.0)

            vol_series    = hist["Volume"].dropna()
            volume_today  = int(vol_series.iloc[-1]) if not vol_series.empty else 0
            volume_avg_5d = int(vol_series.mean())   if not vol_series.empty else 0
            volume_ratio  = volume_today / volume_avg_5d if volume_avg_5d > 0 else 1.0
            name          = info.get("longName") or info.get("shortName") or code
            sector        = get_effective_sector(code.replace(".T", ""), info.get("sector") or "不明")

            return StockInfo(
                code=code.replace(".T", ""),
                name=name,
                sector=sector,
                current_price=current_price,
                volume_today=volume_today,
                volume_avg_5d=volume_avg_5d,
                volume_ratio=volume_ratio,
                change_pct=change_pct,
                market_cap=info.get("marketCap") or 0,
            )
        except Exception as e:
            logger.error(f"[{code}] 取得エラー: {e}")
            return None

    # ──────────────────────────────────────────────
    #  JPX銘柄一覧
    # ──────────────────────────────────────────────

    def _get_listing(self) -> Optional[pd.DataFrame]:
        if self._listing_cache is not None:
            return self._listing_cache
        try:
            logger.info("JPX上場銘柄一覧を取得中...")
            response = req.get(JPX_LISTING_URL, timeout=30)
            response.raise_for_status()
            df = pd.read_excel(io.BytesIO(response.content), engine="xlrd")
            df["コード"] = df["コード"].astype(str).str.zfill(4)
            self._listing_cache = df
            logger.info(f"JPX銘柄一覧取得完了: {len(df)}銘柄")
            return df
        except ImportError:
            logger.error("xlrd が未インストール: pip install xlrd>=2.0.1")
            return None
        except Exception as e:
            logger.error(f"JPX銘柄一覧の取得に失敗: {e}")
            return None

    # ──────────────────────────────────────────────
    #  バッチダウンロード（メイン方式）
    # ──────────────────────────────────────────────

    def _fetch_volume_data(self, tickers: list[str]) -> list[StockInfo]:
        """
        yfinance.download() でまとめて取得する。
        yfinance 0.2.x 以降のMultiIndex列構造に対応。
        待機なし（課金プランのため不要）。
        """
        results: list[StockInfo] = []
        batch_size = 200   # バッチを大きくしてバッチ数を削減

        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i + batch_size]
            bn    = i // batch_size + 1
            total = (len(tickers) + batch_size - 1) // batch_size
            logger.info(f"  バッチ {bn}/{total} ({len(batch)}銘柄) 取得中...")

            try:
                raw = yf.download(
                    batch,
                    period="5d",
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )
            except Exception as e:
                logger.warning(f"  バッチ{bn} ダウンロードエラー: {e}")
                continue

            if raw is None or raw.empty:
                logger.warning(f"  バッチ{bn} 空データ")
                continue

            # ── DataFrame構造を判定してデータを抽出 ──────────────
            # yfinance 0.2.x: 複数銘柄時は MultiIndex(Price, Ticker)
            # yfinance 0.1.x: 単純な (Date, Price) で列名がTickerになる場合も

            for sym in batch:
                code = sym.replace(".T", "")
                try:
                    close_s, volume_s = self._extract_series(raw, sym, batch)
                    if close_s is None or close_s.dropna().empty:
                        continue
                    close_s  = close_s.dropna()
                    volume_s = volume_s.dropna() if volume_s is not None else pd.Series(dtype=float)

                    current_price = float(close_s.iloc[-1])
                    import math as _m
                    if _m.isnan(current_price) or _m.isinf(current_price) or current_price <= 0:
                        continue
                    prev_price    = float(close_s.iloc[-2]) if len(close_s) >= 2 else current_price
                    change_pct    = (current_price - prev_price) / prev_price * 100 if prev_price else 0.0
                    volume_today  = int(volume_s.iloc[-1]) if not volume_s.empty else 0
                    volume_avg_5d = int(volume_s.mean())   if not volume_s.empty else 0
                    volume_ratio  = volume_today / volume_avg_5d if volume_avg_5d > 0 else 1.0

                    name, sector = self._lookup_name_sector(code)

                    results.append(StockInfo(
                        code=code, name=name, sector=sector,
                        current_price=current_price,
                        volume_today=volume_today,
                        volume_avg_5d=volume_avg_5d,
                        volume_ratio=volume_ratio,
                        change_pct=change_pct,
                        market_cap=0,
                    ))
                except Exception:
                    continue

        logger.info(f"  バッチ取得完了: {len(results)}銘柄")
        return results

    @staticmethod
    def _extract_series(
        df: pd.DataFrame,
        sym: str,
        batch: list[str],
    ):
        """
        yfinanceのバージョンによって異なるDataFrame構造から
        Close/Volumeシリーズを取り出す。

        パターンA: MultiIndex (Price, Ticker) — yfinance 0.2.x 複数銘柄
          df.columns = [("Close","7203.T"), ("Close","6758.T"), ...]
          → df[("Close", sym)]

        パターンB: 単純列 Price — yfinance 単一銘柄 or 旧バージョン
          df.columns = ["Close", "Volume", ...]
          → df["Close"]
        """
        cols = df.columns

        # MultiIndexの場合
        if isinstance(cols, pd.MultiIndex):
            try:
                close_s  = df[("Close",  sym)]
                volume_s = df[("Volume", sym)]
                return close_s, volume_s
            except KeyError:
                return None, None

        # 単純列かつ単一銘柄
        if len(batch) == 1 and "Close" in cols:
            return df["Close"], df.get("Volume")

        # 単純列かつ複数銘柄（古いyfinance）
        if sym in cols:
            # group_by="ticker" で列名がTickerになるケース
            sub = df[sym]
            return sub.get("Close"), sub.get("Volume")

        return None, None

    # ──────────────────────────────────────────────
    #  フォールバック: 個別Ticker方式
    # ──────────────────────────────────────────────

    def _fetch_individual(self, tickers: list[str]) -> list[StockInfo]:
        """
        バッチ方式が失敗した場合の保険。
        yf.Ticker() で1件ずつ取得する（低速だが確実）。
        """
        results: list[StockInfo] = []
        logger.info(f"  個別Ticker方式で{len(tickers)}銘柄を取得中...")

        for sym in tickers:
            code = sym.replace(".T", "")
            try:
                tk   = yf.Ticker(sym)
                hist = tk.history(period="5d")
                if hist.empty or float(hist["Close"].iloc[-1]) <= 0:
                    continue

                current_price = float(hist["Close"].iloc[-1])
                prev_price    = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current_price
                change_pct    = (current_price - prev_price) / prev_price * 100 if prev_price else 0.0
                volume_today  = int(hist["Volume"].iloc[-1])
                volume_avg_5d = int(hist["Volume"].mean())
                volume_ratio  = volume_today / volume_avg_5d if volume_avg_5d > 0 else 1.0

                name, sector = self._lookup_name_sector(code)

                results.append(StockInfo(
                    code=code, name=name, sector=sector,
                    current_price=current_price,
                    volume_today=volume_today,
                    volume_avg_5d=volume_avg_5d,
                    volume_ratio=volume_ratio,
                    change_pct=change_pct,
                    market_cap=0,
                ))
            except Exception:
                continue

        logger.info(f"  個別方式完了: {len(results)}銘柄")
        return results

    # ──────────────────────────────────────────────
    #  ユーティリティ
    # ──────────────────────────────────────────────

    def _lookup_name_sector(self, code: str) -> tuple[str, str]:
        """
        JPXキャッシュから銘柄名と業種を返す。
        SUBSECTOR_MAPに登録されている銘柄は実質的な投資テーマを優先する。
        例: 東京エレクトロン(8035) → 「電気機器」ではなく「半導体」を返す
        """
        listing = self._listing_cache
        if listing is not None and not listing.empty:
            row = listing[listing["コード"] == code]
            if not row.empty:
                name      = str(row.iloc[0].get("銘柄名", code))
                jpx_sector = SECTOR_MAP.get(str(row.iloc[0].get("33業種区分", "")), "不明")
                sector     = get_effective_sector(code, jpx_sector)
                return name, sector
        # JPXリストにない場合もサブセクターを確認
        return code, get_effective_sector(code, "不明")

    @staticmethod
    def _fallback_tickers() -> list[str]:
        """2026年4月時点で上場中の主要銘柄（廃止済み除外済み）"""
        codes = [
            "7203","6758","9984","8306","6861","9433","7974","9432","6902","8035",
            "8316","4063","6367","9022","8411","7267","6954","9020","4519","6594",
            "6501","7751","4543","8001","4661","8002","9983","6762","4901","6920",
            "8031","8053","7269","6503","7201","5108","8725","4502","7270","4523",
            "6702","9735","4704","8604","7733","3382","6301","4307","7832","9766",
            "6723","8015","7011","4911","6645","2914","8309","7735","3661","8801",
            "6471","4568","9602","5401","6857","9531","6586","4578","8303","4324",
            "6869","8830","3289","6326","7741","4452","7013","6841","4021","8697",
            "3407","6770","4042","6724","8267","9202","7261","7272","4188","4183",
            "6361","5020","3099","9507","7912","3659","6963","4755","6981","4689",
        ]
        seen, result = set(), []
        for c in codes:
            if c not in seen:
                seen.add(c)
                result.append(f"{c}.T")
        return result
