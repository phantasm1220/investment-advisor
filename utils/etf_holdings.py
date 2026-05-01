"""
utils/etf_holdings.py

ETFの構成銘柄・セクター比率を取得するモジュール。

データソース:
  - yfinance の Ticker.funds_data: 構成銘柄Top10・セクター比率
  - ETF_HOLDINGS_MASTER: yfinanceで取れない主要ETFの手動補完データ

取得できる主な情報:
  - 上位保有銘柄（Top10）と保有比率
  - セクター別配分（%）
  - 地域別配分（国内/米国/その他）
  - 上位5銘柄の個別株情報（株価・RSI・勢い）
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass
class HoldingStock:
    """構成銘柄の1銘柄"""
    symbol: str
    name: str
    weight_pct: float       # 保有比率(%)
    sector: str = "不明"
    recent_change_pct: float = 0.0   # 直近1週間の騰落率


@dataclass
class SectorAllocation:
    """セクター別配分"""
    sector: str
    weight_pct: float
    macro_outlook: str = "NEUTRAL"   # BULLISH / NEUTRAL / BEARISH（後段で付与）


@dataclass
class ETFHoldingsData:
    """ETF構成銘柄・配分の全データ"""
    code: str
    name: str

    # 上位保有銘柄
    top_holdings: list[HoldingStock] = field(default_factory=list)
    top10_concentration_pct: float = 0.0   # 上位10銘柄の集中度

    # セクター配分
    sector_allocations: list[SectorAllocation] = field(default_factory=list)
    top_sector: str = "不明"
    top_sector_weight_pct: float = 0.0

    # 地域配分
    domestic_pct: float = 0.0    # 国内比率
    us_pct: float = 0.0          # 米国比率
    other_pct: float = 0.0       # その他

    # データ品質
    data_source: str = "yfinance"
    is_complete: bool = False    # 完全なデータが取れたか


# ──────────────────────────────────────────────────────────
#  主要ETFの構成銘柄マスターデータ（yfinanceで取れない場合の補完）
# ──────────────────────────────────────────────────────────
ETF_HOLDINGS_MASTER: dict[str, dict] = {
    "1306": {  # TOPIX ETF
        "top_holdings": [
            ("7203.T", "トヨタ自動車",   3.5, "輸送機器"),
            ("6758.T", "ソニーグループ", 2.8, "電気機器"),
            ("8306.T", "三菱UFJFG",     2.6, "銀行"),
            ("6861.T", "キーエンス",     2.2, "電気機器"),
            ("9432.T", "NTT",           1.9, "情報通信"),
            ("9433.T", "KDDI",          1.8, "情報通信"),
            ("6367.T", "ダイキン工業",   1.5, "機械"),
            ("4063.T", "信越化学",       1.4, "化学"),
            ("8035.T", "東京エレクトロン",1.3,"電気機器"),
            ("7974.T", "任天堂",         1.2, "その他製品"),
        ],
        "sectors": {
            "電気機器": 20.5, "銀行": 9.8, "情報通信": 8.2,
            "輸送機器": 7.5, "化学": 6.8, "機械": 5.5,
            "卸売": 4.2, "医薬品": 3.8, "不動産": 3.5, "その他": 30.2,
        },
        "region": {"domestic": 100.0, "us": 0.0, "other": 0.0},
    },
    "1321": {  # 日経225 ETF
        "top_holdings": [
            ("9983.T", "ファーストリテイリング", 10.2, "小売"),
            ("9984.T", "ソフトバンクG",          5.8, "情報通信"),
            ("6954.T", "ファナック",              4.2, "電気機器"),
            ("6857.T", "アドバンテスト",          3.9, "電気機器"),
            ("4519.T", "中外製薬",                3.5, "医薬品"),
            ("6367.T", "ダイキン工業",            3.1, "機械"),
            ("7203.T", "トヨタ自動車",            2.8, "輸送機器"),
            ("6861.T", "キーエンス",              2.6, "電気機器"),
            ("4901.T", "富士フイルム",            2.2, "化学"),
            ("8035.T", "東京エレクトロン",        2.1, "電気機器"),
        ],
        "sectors": {
            "電気機器": 25.8, "小売": 12.5, "医薬品": 8.2,
            "情報通信": 7.5, "機械": 6.8, "輸送機器": 5.5,
            "化学": 5.2, "精密機器": 4.1, "その他": 24.4,
        },
        "region": {"domestic": 100.0, "us": 0.0, "other": 0.0},
    },
    "1545": {  # NASDAQ-100 ETF
        "top_holdings": [
            ("MSFT",  "マイクロソフト",     9.1, "テクノロジー"),
            ("AAPL",  "アップル",           8.6, "テクノロジー"),
            ("NVDA",  "エヌビディア",       7.8, "半導体"),
            ("AMZN",  "アマゾン",           5.2, "一般消費財"),
            ("META",  "メタプラットフォームズ",4.1,"通信サービス"),
            ("GOOGL", "アルファベットA",    3.8, "通信サービス"),
            ("GOOG",  "アルファベットC",    3.5, "通信サービス"),
            ("TSLA",  "テスラ",             2.9, "一般消費財"),
            ("AVGO",  "ブロードコム",        2.7, "半導体"),
            ("COST",  "コストコ",           2.1, "生活必需品"),
        ],
        "sectors": {
            "テクノロジー": 52.3, "半導体": 12.8, "通信サービス": 11.2,
            "一般消費財": 8.9, "ヘルスケア": 5.8, "生活必需品": 3.2,
            "その他": 5.8,
        },
        "region": {"domestic": 0.0, "us": 100.0, "other": 0.0},
    },
    "1557": {  # SPDR S&P500 ETF
        "top_holdings": [
            ("MSFT",  "マイクロソフト",   6.8, "テクノロジー"),
            ("AAPL",  "アップル",         6.2, "テクノロジー"),
            ("NVDA",  "エヌビディア",     5.9, "半導体"),
            ("AMZN",  "アマゾン",         3.8, "一般消費財"),
            ("META",  "メタプラットフォームズ",2.8,"通信サービス"),
            ("GOOGL", "アルファベットA",  2.5, "通信サービス"),
            ("TSLA",  "テスラ",           2.1, "一般消費財"),
            ("BRK.B", "バークシャーB",    1.8, "金融"),
            ("JPM",   "JPモルガン",       1.7, "金融"),
            ("UNH",   "ユナイテッドヘルス",1.6,"ヘルスケア"),
        ],
        "sectors": {
            "テクノロジー": 32.5, "金融": 13.2, "ヘルスケア": 11.8,
            "一般消費財": 9.8, "通信サービス": 8.5, "半導体": 7.2,
            "資本財": 6.8, "生活必需品": 5.2, "エネルギー": 3.8, "その他": 1.2,
        },
        "region": {"domestic": 0.0, "us": 100.0, "other": 0.0},
    },
    "2558": {  # MAXIS S&P500
        "top_holdings": [
            ("MSFT",  "マイクロソフト",   6.8, "テクノロジー"),
            ("AAPL",  "アップル",         6.2, "テクノロジー"),
            ("NVDA",  "エヌビディア",     5.9, "半導体"),
            ("AMZN",  "アマゾン",         3.8, "一般消費財"),
            ("META",  "メタプラットフォームズ",2.8,"通信サービス"),
        ],
        "sectors": {
            "テクノロジー": 32.5, "金融": 13.2, "ヘルスケア": 11.8,
            "一般消費財": 9.8, "その他": 32.7,
        },
        "region": {"domestic": 0.0, "us": 100.0, "other": 0.0},
    },
    "1343": {  # 東証REIT ETF
        "top_holdings": [
            ("3462.T", "野村不動産マスターF",   9.8, "不動産"),
            ("8952.T", "ジャパンリアルエステイト", 8.5, "不動産"),
            ("3283.T", "日本プロロジスリート", 7.2, "不動産"),
            ("8960.T", "ユナイテッドアーバン",  6.8, "不動産"),
            ("3269.T", "アドバンスレジデンス",  6.1, "不動産"),
        ],
        "sectors": {"不動産（オフィス）": 35.2, "不動産（物流）": 22.5,
                    "不動産（住宅）": 18.8, "不動産（商業）": 15.5, "その他": 8.0},
        "region": {"domestic": 100.0, "us": 0.0, "other": 0.0},
    },
    "1360": {  # 日経平均ベア2倍上場投信
        "top_holdings": [],
        "sectors": {"インバース（日経225×-2）": 100.0},
        "region": {"domestic": 100.0, "us": 0.0, "other": 0.0},
    },
    "1366": {  # ダイワ上場投信 日経平均
        "top_holdings": [
            ("9983.T", "ファーストリテイリング", 10.2, "小売"),
            ("9984.T", "ソフトバンクG",          5.8, "情報通信"),
            ("6954.T", "ファナック",              4.2, "電気機器"),
        ],
        "sectors": {"電気機器": 25.8, "小売": 12.5, "情報通信": 7.5,
                    "機械": 6.8, "輸送機器": 5.5, "その他": 42.0},
        "region": {"domestic": 100.0, "us": 0.0, "other": 0.0},
    },
    "1459": {  # 楽天ETF-日経レバレッジ指数連動
        "top_holdings": [],
        "sectors": {"レバレッジ（日経225×2）": 100.0},
        "region": {"domestic": 100.0, "us": 0.0, "other": 0.0},
    },
    "1579": {  # 日経平均ブル2倍上場投信
        "top_holdings": [],
        "sectors": {"レバレッジ（日経225×2）": 100.0},
        "region": {"domestic": 100.0, "us": 0.0, "other": 0.0},
    },
    "1619": {  # 電気機器セクターETF
        "top_holdings": [
            ("6861.T", "キーエンス",      15.2, "電気機器"),
            ("8035.T", "東京エレクトロン",14.8, "電気機器"),
            ("6857.T", "アドバンテスト",  12.5, "電気機器"),
            ("6920.T", "レーザーテック",   9.8, "電気機器"),
            ("6954.T", "ファナック",        8.5, "電気機器"),
            ("6723.T", "ルネサスエレクトロ",6.2,"電気機器"),
            ("6758.T", "ソニーグループ",   5.8, "電気機器"),
            ("6762.T", "TDK",              4.5, "電気機器"),
            ("6981.T", "村田製作所",        3.8, "電気機器"),
            ("4935.T", "リベルタ",          2.1, "電気機器"),
        ],
        "sectors": {"半導体製造装置": 42.5, "電子部品": 25.8,
                    "産業用ロボット": 15.2, "その他電気機器": 16.5},
        "region": {"domestic": 100.0, "us": 0.0, "other": 0.0},
    },
}


class ETFHoldingsFetcher:
    """ETFの構成銘柄・セクター配分を取得するクラス"""

    def get_holdings(self, code: str) -> ETFHoldingsData:
        """
        ETFコードの構成銘柄・セクター配分を返す。
        優先順位:
          1. yfinanceのfunds_dataから取得（最新）
          2. ETF_HOLDINGS_MASTERから補完（手動データ）
          3. ETF名・テーマのみの最小データ
        """
        logger.info(f"[{code}] 構成銘柄データを取得中...")

        # 1. yfinanceから試みる
        data = self._fetch_from_yfinance(code)
        if data and data.is_complete:
            logger.info(f"[{code}] yfinanceから構成銘柄取得成功")
            return data

        # 2. マスターデータで補完
        if code in ETF_HOLDINGS_MASTER:
            logger.info(f"[{code}] マスターデータから構成銘柄を補完")
            return self._build_from_master(code, data)

        # 3. 最小データ（yfinanceのinfo情報のみ）
        logger.warning(f"[{code}] 構成銘柄データなし。最小データで代用")
        return data or ETFHoldingsData(code=code, name=code)

    # ──────────────────────────────────────────────

    def _fetch_from_yfinance(self, code: str) -> Optional[ETFHoldingsData]:
        """yfinanceのfunds_dataから構成銘柄を取得"""
        sym = code if code.endswith(".T") else f"{code}.T"
        try:
            tk     = yf.Ticker(sym)
            info   = tk.info
            name   = info.get("longName") or info.get("shortName") or code

            result = ETFHoldingsData(code=code, name=name, data_source="yfinance")

            # funds_data（ETFの場合のみ存在する属性）
            try:
                fd = tk.funds_data
                if fd is not None:
                    # セクター配分
                    sector_weights = getattr(fd, "sector_weightings", None)
                    if sector_weights and isinstance(sector_weights, dict):
                        allocs = []
                        for sec, w in sorted(sector_weights.items(),
                                             key=lambda x: x[1], reverse=True):
                            allocs.append(SectorAllocation(
                                sector=_translate_sector(sec),
                                weight_pct=round(w * 100, 2),
                            ))
                        result.sector_allocations = allocs[:10]
                        if allocs:
                            result.top_sector = allocs[0].sector
                            result.top_sector_weight_pct = allocs[0].weight_pct

                    # 上位保有銘柄
                    holdings = getattr(fd, "top_holdings", None)
                    if holdings is not None and not holdings.empty:
                        stocks = []
                        total_w = 0.0
                        for _, row in holdings.head(10).iterrows():
                            sym_h = str(row.get("Symbol", ""))
                            w     = float(row.get("Holding Percent", 0)) * 100
                            name_h = str(row.get("Name", sym_h))
                            stocks.append(HoldingStock(
                                symbol=sym_h, name=name_h, weight_pct=round(w, 2)
                            ))
                            total_w += w
                        result.top_holdings = stocks
                        result.top10_concentration_pct = round(total_w, 1)
                        result.is_complete = len(stocks) >= 3

            except (AttributeError, Exception) as e:
                logger.debug(f"[{code}] funds_data取得失敗: {e}")

            # 地域配分（infoから推定）
            category = info.get("category", "")
            if "Japan" in category or "日本" in category or code in ("1306","1321","1343"):
                result.domestic_pct = 100.0
            elif "U.S." in category or "S&P" in category or "NASDAQ" in category:
                result.us_pct = 100.0

            return result

        except Exception as e:
            logger.error(f"[{code}] yfinance取得エラー: {e}")
            return None

    def _build_from_master(
        self,
        code: str,
        base: Optional[ETFHoldingsData],
    ) -> ETFHoldingsData:
        """マスターデータからETFHoldingsDataを構築"""
        m    = ETF_HOLDINGS_MASTER[code]
        name = base.name if base else code

        holdings = [
            HoldingStock(symbol=s, name=n, weight_pct=w, sector=sec)
            for s, n, w, sec in m.get("top_holdings", [])
        ]
        total_w = sum(h.weight_pct for h in holdings[:10])

        allocs = [
            SectorAllocation(sector=sec, weight_pct=w)
            for sec, w in sorted(
                m.get("sectors", {}).items(), key=lambda x: x[1], reverse=True
            )
        ]

        region = m.get("region", {})
        top_sec = allocs[0].sector if allocs else "不明"

        return ETFHoldingsData(
            code=code, name=name,
            top_holdings=holdings,
            top10_concentration_pct=round(total_w, 1),
            sector_allocations=allocs,
            top_sector=top_sec,
            top_sector_weight_pct=allocs[0].weight_pct if allocs else 0,
            domestic_pct=region.get("domestic", 0),
            us_pct=region.get("us", 0),
            other_pct=region.get("other", 0),
            data_source="master",
            is_complete=True,
        )


def _translate_sector(en: str) -> str:
    """yfinanceの英語セクター名を日本語に変換"""
    mapping = {
        "technology":            "テクノロジー",
        "financial_services":    "金融",
        "healthcare":            "ヘルスケア",
        "consumer_cyclical":     "一般消費財",
        "communication_services":"通信サービス",
        "industrials":           "資本財",
        "consumer_defensive":    "生活必需品",
        "energy":                "エネルギー",
        "basic_materials":       "素材",
        "real_estate":           "不動産",
        "utilities":             "公益",
        "realestate":            "不動産",
        "semiconductors":        "半導体",
    }
    return mapping.get(en.lower().replace(" ", "_"), en)
