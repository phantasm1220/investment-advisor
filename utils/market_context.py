"""
utils/market_context.py

現在の市場テーマ・マクロ環境を一元管理するモジュール。
全エージェントが共通のコンテキストを参照することで、
偏りなく最新テーマ（AI・半導体・エネルギー等）を判断に組み込む。

本来はWebスクレイピングや有料データAPIで動的取得すべきだが、
現段階ではアナリスト品質の固定コンテキストとして管理する。
定期的に手動更新すること（月1回推奨）。
"""

from datetime import date

# ── 更新日 ────────────────────────────────────────────────
CONTEXT_UPDATED = "2026-04-27"

# ── グローバル市場テーマ（重要度順） ──────────────────────
GLOBAL_THEMES = [
    {
        "theme":       "AI・生成AI インフラ投資",
        "outlook":     "BULLISH",
        "horizon":     "中長期（1〜3年）",
        "description": (
            "ChatGPT/Claude等の生成AI普及によりデータセンター・GPU需要が爆発的拡大。"
            "NVIDIA・AMD・TSMC等が恩恵。電力消費増大でエネルギー需要も連動して上昇。"
            "クラウド大手（AWS・Azure・GCP）の設備投資は2026年も継続拡大。"
        ),
        "key_stocks":  ["NVDA", "AMD", "TSMC", "6857.T", "6920.T", "8035.T"],
        "risk":        "高バリュエーション、規制リスク",
    },
    {
        "theme":       "半導体・製造装置",
        "outlook":     "BULLISH",
        "horizon":     "中期（6〜18ヶ月）",
        "description": (
            "AI需要に加え、車載半導体・IoT需要が継続。CHIPS法による米国・日本での工場建設ラッシュ。"
            "前工程装置（東京エレクトロン・アドバンテスト）は受注残が高水準。"
            "ただし米中半導体規制強化によるサプライチェーン再編リスクあり。"
        ),
        "key_stocks":  ["6857.T", "8035.T", "6920.T", "6723.T", "AMAT", "LRCX"],
        "risk":        "米中規制、在庫調整サイクル",
    },
    {
        "theme":       "エネルギー（原子力・再生可能エネルギー）",
        "outlook":     "BULLISH",
        "horizon":     "中長期（1〜5年）",
        "description": (
            "AI・データセンターの電力需要急増を受けて原子力発電の再評価が進む。"
            "米国・欧州・日本で原発再稼働・新設の動き加速。"
            "再生可能エネルギー（太陽光・洋上風力）も政策的追い風。"
            "電力株・原子力関連・送電インフラ株が恩恵を受ける。"
        ),
        "key_stocks":  ["CEG", "VST", "9501.T", "9503.T", "1699"],
        "risk":        "規制変更、建設コスト超過",
    },
    {
        "theme":       "防衛・宇宙産業",
        "outlook":     "BULLISH",
        "horizon":     "中長期（2〜5年）",
        "description": (
            "ロシア・ウクライナ戦争長期化、台湾海峡緊張を背景にNATO加盟国のGDP比2%達成へ防衛費増大。"
            "日本も2027年までにGDP比2%への倍増方針。宇宙分野への民間投資も拡大。"
        ),
        "key_stocks":  ["LMT", "RTX", "7011.T", "7013.T", "6758.T"],
        "risk":        "政権交代による政策変更",
    },
    {
        "theme":       "日本株・賃上げ・コーポレートガバナンス改革",
        "outlook":     "BULLISH",
        "horizon":     "短中期（3〜12ヶ月）",
        "description": (
            "東証PBR1倍割れ是正要求・株主還元強化・自社株買いが継続。"
            "春闘での賃上げ加速（2025年: 平均5.2%）→ 個人消費・内需拡大への期待。"
            "外国人投資家の日本株再評価が続く。バフェット効果で総合商社にも注目。"
        ),
        "key_stocks":  ["8031.T", "8053.T", "8001.T", "7203.T", "8306.T"],
        "risk":        "円高進行、日銀利上げ加速",
    },
    {
        "theme":       "金融・銀行（金利上昇恩恵）",
        "outlook":     "NEUTRAL_BULLISH",
        "horizon":     "短期（3〜6ヶ月）",
        "description": (
            "日銀の追加利上げ観測が強まり、国内銀行の利ざや拡大期待。"
            "三菱UFJ・三井住友等のメガバンクは純利益過去最高水準を更新中。"
            "ただし景気後退懸念が高まると与信コスト増大リスクあり。"
        ),
        "key_stocks":  ["8306.T", "8316.T", "8411.T"],
        "risk":        "景気後退による不良債権増加",
    },
    {
        "theme":       "中国関連（慎重）",
        "outlook":     "BEARISH",
        "horizon":     "短中期（6〜12ヶ月）",
        "description": (
            "米国の対中関税強化（一部品目125%）・デカップリング加速。"
            "中国向け輸出依存度が高い企業（自動車・電子部品等）は業績下振れリスク。"
            "不動産市場の低迷継続で内需も弱い。"
        ),
        "key_stocks":  [],
        "risk":        "関税協議進展による急反発リスクもあり",
    },
    {
        "theme":       "グロース株全般（金利高止まりで慎重）",
        "outlook":     "NEUTRAL",
        "horizon":     "短期（〜3ヶ月）",
        "description": (
            "FRBの利下げ先送りが続く中、高PERグロース株のバリュエーション圧迫が継続。"
            "ただしAI関連は例外的に買われている。"
            "金利ピークアウト確認後に再評価の場が訪れる可能性。"
        ),
        "key_stocks":  [],
        "risk":        "インフレ再燃による追加利上げ",
    },
]

# ── 現在の市場レジーム ─────────────────────────────────
MARKET_REGIME = {
    "primary":     "TECH_CAPEX_DRIVEN",   # テック設備投資主導
    "secondary":   "RATE_SENSITIVE",      # 金利感応度高い
    "fear_factor": "RECESSION_FEAR",      # 景気後退よりインフレ懸念がやや優勢
    "summary": (
        "AIデータセンター投資・半導体需要が相場を牽引。"
        "FRBの利下げ先送りで金利高止まりも、AI・半導体・エネルギーは例外的に強い。"
        "日本株はガバナンス改革・賃上げ期待で外国人買いが継続。"
        "リスク: 米中関税摩擦、地政学リスク（中東・台湾）、円高加速。"
    ),
}

# ── セクター別マクロ見通し ──────────────────────────────
SECTOR_MACRO_VIEW: dict[str, str] = {
    # 強気
    "半導体製造装置":    "BULLISH",
    "電気機器":          "BULLISH",
    "情報通信":          "BULLISH",
    "テクノロジー":      "BULLISH",
    "半導体":            "BULLISH",
    "AI/テック":         "BULLISH",
    "エネルギー":        "BULLISH",
    "原子力":            "BULLISH",
    "防衛":              "BULLISH",
    "銀行":              "BULLISH",
    "金融":              "BULLISH",
    "不動産（物流）":    "BULLISH",
    "国内REIT":          "NEUTRAL",
    "米国株・テック":    "BULLISH",
    "米国株・広域":      "NEUTRAL_BULLISH",
    # 中立
    "機械":              "NEUTRAL",
    "化学":              "NEUTRAL",
    "輸送機器":          "NEUTRAL",
    "医薬品":            "NEUTRAL",
    "小売":              "NEUTRAL",
    "サービス":          "NEUTRAL",
    "国内株・広域":      "NEUTRAL",
    "国内株・大型":      "NEUTRAL",
    # 注意
    "中国関連":          "BEARISH",
    "新興国":            "BEARISH",
    "インバース":        "NEUTRAL",   # 市場次第
    "レバレッジ":        "NEUTRAL",   # 短期トレード向け
    "その他":            "NEUTRAL",
}


def get_theme_context_for_prompt() -> str:
    """エージェントのプロンプトに挿入する市場テーマ文字列を生成する"""
    bullish = [t for t in GLOBAL_THEMES if t["outlook"] in ("BULLISH", "NEUTRAL_BULLISH")]
    bearish = [t for t in GLOBAL_THEMES if t["outlook"] == "BEARISH"]

    lines = [
        f"【{CONTEXT_UPDATED}時点の市場テーマ（必ず分析に反映すること）】",
        "",
        f"■ 市場レジーム: {MARKET_REGIME['summary']}",
        "",
        "■ 強気テーマ（買い推奨方向）:",
    ]
    for t in bullish[:5]:
        lines.append(f"  ▶ {t['theme']}（{t['horizon']}）: {t['description'][:80]}…")

    lines += ["", "■ 注意テーマ（慎重・弱気）:"]
    for t in bearish:
        lines.append(f"  ▶ {t['theme']}: {t['description'][:60]}…")

    lines += [
        "",
        "■ セクター別見通し:",
        "  強気: 半導体/電気機器/情報通信/テクノロジー/エネルギー/銀行/防衛",
        "  中立: 機械/化学/輸送機器/医薬品/REIT",
        "  弱気: 中国関連/新興国",
    ]
    return "\n".join(lines)


def get_sector_macro_view(sector: str) -> str:
    """セクター名からマクロ見通し（BULLISH/NEUTRAL/BEARISH）を返す"""
    # 完全一致
    if sector in SECTOR_MACRO_VIEW:
        return SECTOR_MACRO_VIEW[sector]
    # 部分一致
    for key, view in SECTOR_MACRO_VIEW.items():
        if key in sector or sector in key:
            return view
    return "NEUTRAL"
