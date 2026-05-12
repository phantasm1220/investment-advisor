"""
scripts/generate_names_master.py

JPX上場全銘柄（約3,800件）の日本語名を取得して
data/jp_names_master.json に保存する。

取得方法:
  1. JPX上場銘柄一覧（Excel）から日本語名を一括取得（最速）
  2. JPX取得失敗時はyfinanceで補完

実行タイミング:
  - 初回: GitHub Actions の初回実行時に自動実行
  - 以降: Webパネルの「銘柄名マスターを更新」ボタン押下時のみ
"""
import json, logging, os, sys, time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "jp_names_master.json"
)
BATCH = 50


def fetch_from_jpx() -> dict[str, str]:
    """JPXの上場銘柄一覧Excelから日本語名を取得"""
    import requests, pandas as pd, io
    url = ("https://www.jpx.co.jp/markets/statistics-equities/misc/"
           "tvdivq0000001vg2-att/data_j.xls")
    logger.info("JPX上場銘柄一覧を取得中...")
    try:
        resp = requests.get(url, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        for engine in ["xlrd", "openpyxl"]:
            try:
                df = pd.read_excel(io.BytesIO(resp.content),
                                   dtype={"コード": str}, engine=engine)
                break
            except Exception:
                continue
        else:
            return {}

        name_col = next((c for c in ["銘柄名","銘柄名称","名称"]
                         if c in df.columns), None)
        if not name_col:
            logger.warning(f"銘柄名列が見つかりません: {list(df.columns)}")
            return {}

        names = {}
        for _, row in df.iterrows():
            code = str(row.get("コード","")).strip().zfill(4)
            name = str(row.get(name_col,"")).strip()
            if code and name and name not in ("nan","NaN",""):
                names[code] = name
        logger.info(f"JPXから {len(names)}銘柄取得")
        return names
    except Exception as e:
        logger.warning(f"JPX取得失敗: {e}")
        return {}


def fetch_from_yfinance(codes: list[str]) -> dict[str, str]:
    """yfinanceで銘柄名を取得（JPX失敗時のフォールバック）"""
    import yfinance as yf
    names = {}
    total = len(codes)
    logger.info(f"yfinanceで {total}銘柄を取得中...")

    for i in range(0, total, BATCH):
        batch = codes[i:i+BATCH]
        syms  = " ".join(f"{c}.T" for c in batch)
        try:
            tickers = yf.Tickers(syms)
            for code in batch:
                try:
                    info  = tickers.tickers[f"{code}.T"].info
                    short = info.get("shortName","")
                    long_ = info.get("longName","")
                    name  = ""
                    for n in [short, long_]:
                        if n and any(
                            "\u3040" <= ch <= "\u30ff" or
                            "\u4e00" <= ch <= "\u9fff"
                            for ch in n
                        ):
                            name = n
                            break
                    if not name:
                        name = short or long_
                    if name:
                        names[code] = name
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"バッチ {i//BATCH+1} 失敗: {e}")

        if (i // BATCH + 1) % 10 == 0:
            logger.info(f"  進捗: {min(i+BATCH,total)}/{total} ({len(names)}件取得済み)")
        time.sleep(0.2)

    logger.info(f"yfinanceから {len(names)}銘柄取得")
    return names


def generate() -> bool:
    logger.info("=== 銘柄名マスター生成開始 ===")

    # Step1: JPXから一括取得
    names = fetch_from_jpx()

    # Step2: JPX失敗時はyfinanceで取得
    if len(names) < 100:
        logger.info("JPX取得失敗 → yfinanceで取得します...")
        codes = [str(i).zfill(4) for i in range(1300, 10000)]
        codes += ["285A","143A","216A"]
        names = fetch_from_yfinance(codes)

    if len(names) < 100:
        logger.error(f"取得件数不足: {len(names)}件")
        return False

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generated": date.today().isoformat(),
            "count":     len(names),
            "names":     names,
        }, f, ensure_ascii=False, indent=2)

    logger.info(f"=== 保存完了: {OUTPUT_PATH} ({len(names)}銘柄) ===")
    return True


if __name__ == "__main__":
    sys.exit(0 if generate() else 1)
