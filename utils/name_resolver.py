"""
utils/name_resolver.py  v2.0

銘柄コードから日本語企業名を解決するモジュール。

優先順位:
  1. data/jp_names_master.json（JPX全銘柄日本語名・起動時ロード）
  2. JPXリストキャッシュ（DataFrameが渡された場合）
  3. yfinanceのshortName
  4. 銘柄コードそのまま（取得不可の場合）

jp_names_master.json は scripts/generate_names_master.py で生成。
GitHub Actions では scan 実行前に自動更新される。
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MASTER_PATH = os.path.join(_BASE_DIR, "data", "jp_names_master.json")

# ── マスターのロード ──────────────────────────────────────────
_master: dict[str, str] = {}
_master_loaded = False


def _load_master() -> dict[str, str]:
    global _master, _master_loaded
    if _master_loaded:
        return _master

    if os.path.exists(_MASTER_PATH):
        try:
            with open(_MASTER_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _master = data.get("names", {})
            logger.debug(f"[name_resolver] マスターロード: {len(_master)}銘柄")
        except Exception as e:
            logger.warning(f"[name_resolver] マスターロード失敗: {e}")
            _master = {}
    else:
        logger.debug("[name_resolver] マスターファイルなし（generate_names_master.pyを実行してください）")
        _master = {}

    _master_loaded = True
    return _master


def get_jp_name(code: str, listing_cache=None) -> str:
    """
    銘柄コードから日本語企業名を返す。
    優先順位: マスターJSON > JPXキャッシュ > yfinance > コードそのまま
    """
    code = code.replace(".T", "").strip().zfill(4)
    master = _load_master()

    # 1. マスターJSONから取得
    if code in master:
        return master[code]

    # 1b. フォールバックハードコードマスター（JSON未生成時）
    fb = _get_fallback(code)
    if fb:
        return fb

    # 2. JPXリストキャッシュから取得
    if listing_cache is not None:
        try:
            row = listing_cache[listing_cache["コード"] == code]
            if not row.empty:
                name = str(row.iloc[0].get("銘柄名", ""))
                if name and name not in ("nan", "NaN", ""):
                    master[code] = name
                    return name
        except Exception:
            pass

    # 3. yfinanceから取得
    try:
        import yfinance as yf
        info  = yf.Ticker(f"{code}.T").info or {}
        short = info.get("shortName", "")
        long_ = info.get("longName", "")
        for name in [short, long_]:
            if name and any(
                "\u3000" <= ch <= "\u9fff" or
                "\u30a0" <= ch <= "\u30ff" or
                "\u3040" <= ch <= "\u309f"
                for ch in name
            ):
                master[code] = name
                return name
        result = short or long_
        if result:
            master[code] = result
            return result
    except Exception:
        pass

    return code


def enrich_decisions_with_jp_names(decisions, listing_cache=None) -> None:
    """FinalDecisionリストの銘柄名を日本語名で上書きする。"""
    for d in decisions:
        if d.stock_overview:
            jp = get_jp_name(d.ticker, listing_cache)
            if jp and jp != d.ticker:
                d.stock_overview.name = jp

# ── フォールバック用ハードコードマスター ─────────────────────
# jp_names_master.json が存在しない場合のために主要銘柄をハードコード
_FALLBACK_MASTER: dict[str, str] = {
    "1605":"INPEX","1801":"大成建設","1802":"大林組",
    "1803":"清水建設","1812":"鹿島建設","1925":"大和ハウス工業",
    "2269":"明治HD","2502":"アサヒグループHD","2503":"キリンHD",
    "2801":"キッコーマン","2802":"味の素","2914":"日本たばこ産業",
    "3038":"神戸物産","3092":"ZOZO","3099":"三越伊勢丹HD",
    "3382":"セブン&アイHD","3407":"旭化成","3659":"ネクソン",
    "4005":"住友化学","4042":"東ソー","4063":"信越化学工業",
    "4183":"三井化学","4188":"三菱ケミカルグループ",
    "4307":"野村総研","4324":"電通グループ","4385":"メルカリ",
    "4452":"花王","4502":"武田薬品","4503":"アステラス製薬",
    "4519":"中外製薬","4523":"エーザイ","4528":"小野薬品",
    "4568":"第一三共","4578":"大塚HD","4661":"オリエンタルランド",
    "4689":"LYコーポレーション","4704":"トレンドマイクロ",
    "4755":"楽天グループ","4901":"富士フイルムHD","4911":"資生堂",
    "5020":"ENEOSホールディングス","5108":"ブリヂストン","5201":"AGC",
    "5401":"日本製鉄","5411":"JFEホールディングス",
    "5713":"住友金属鉱山","5802":"住友電気工業",
    "6098":"リクルートHD","6146":"ディスコ","6178":"日本郵政",
    "6273":"SMC","6301":"コマツ","6326":"クボタ",
    "6367":"ダイキン工業","6460":"セガサミーHD",
    "6479":"ミネベアミツミ","6501":"日立製作所","6502":"東芝",
    "6503":"三菱電機","6504":"富士電機","6506":"安川電機",
    "6594":"ニデック","6645":"オムロン","6701":"NEC","6702":"富士通",
    "6723":"ルネサスエレクトロニクス","6740":"ジャパンディスプレイ",
    "6752":"パナソニックHD","6753":"シャープ","6758":"ソニーグループ",
    "6762":"TDK","6841":"横河電機","6857":"アドバンテスト",
    "6861":"キーエンス","6869":"シスメックス","6902":"デンソー",
    "6920":"レーザーテック","6952":"カシオ計算機","6954":"ファナック",
    "6963":"ローム","6971":"京セラ","6981":"村田製作所",
    "7011":"三菱重工業","7012":"川崎重工業","7013":"IHI",
    "7182":"ゆうちょ銀行","7201":"日産自動車","7202":"いすゞ自動車",
    "7203":"トヨタ自動車","7211":"三菱自動車","7261":"マツダ",
    "7267":"ホンダ","7269":"スズキ","7270":"SUBARU",
    "7272":"ヤマハ発動機","7276":"小糸製作所","7309":"シマノ",
    "7453":"良品計画","7731":"ニコン","7733":"オリンパス",
    "7741":"HOYA","7751":"キヤノン","7752":"リコー",
    "7832":"バンダイナムコHD","7936":"アシックス","7974":"任天堂",
    "8001":"伊藤忠商事","8002":"丸紅","8012":"長瀬産業",
    "8015":"豊田通商","8028":"ファミリーマート","8031":"三井物産",
    "8035":"東京エレクトロン","8053":"住友商事","8058":"三菱商事",
    "8113":"ユニ・チャーム","8252":"丸井グループ","8267":"イオン",
    "8306":"三菱UFJ FG","8316":"三井住友FG","8331":"千葉銀行",
    "8411":"みずほFG","8473":"SBIホールディングス",
    "8591":"オリックス","8604":"野村HD","8630":"SOMPO HD",
    "8750":"第一生命HD","8766":"東京海上HD",
    "8801":"三井不動産","8802":"三菱地所",
    "9005":"東急","9020":"東日本旅客鉄道","9021":"西日本旅客鉄道",
    "9022":"東海旅客鉄道","9101":"日本郵船","9104":"商船三井",
    "9107":"川崎汽船","9201":"日本航空","9202":"ANAホールディングス",
    "9432":"NTT","9433":"KDDI","9501":"東京電力HD","9503":"関西電力",
    "9531":"東京ガス","9613":"NTTデータグループ",
    "9735":"セコム","9843":"ニトリHD",
    "9983":"ファーストリテイリング","9984":"ソフトバンクグループ",
    "285A":"キオクシアHD",
}


def _get_fallback(code: str) -> Optional[str]:
    """フォールバックマスターから名称を返す"""
    return _FALLBACK_MASTER.get(code.zfill(4))
