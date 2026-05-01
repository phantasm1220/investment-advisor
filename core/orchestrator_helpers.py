import math
"""core/orchestrator_helpers.py — StockOverview 生成ヘルパー"""
from core.signal import StockOverview


def _safe_float(v, default=0.0):
    """NaN・None・inf を default に変換する"""
    if v is None:
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def build_stock_overview(ticker, current_price, stock_name, tech_signal, fund_signal) -> StockOverview:
    rs = tech_signal.raw_scores
    rsi        = rs.get("rsi_14")
    vol_ratio  = _safe_float(rs.get("volume_momentum_ratio"), 1.0)
    ma200_pct  = rs.get("price_vs_ma200_pct")
    change_pct = _safe_float(rs.get("change_pct"), 0.0)
    # current_price 自体の NaN チェック
    current_price = _safe_float(current_price, 0.0)
    fs         = fund_signal.raw_scores
    per        = fs.get("per_ttm")

    tags = []
    if rsi is not None:
        if rsi >= 75:   tags.append("🔴 買われすぎ(RSI高)")
        elif rsi >= 65: tags.append("🟠 やや過熱")
        elif rsi <= 25: tags.append("🟢 売られすぎ(RSI低)")
        elif rsi <= 35: tags.append("🟡 やや売られすぎ")
    if ma200_pct is not None:
        if ma200_pct >= 20:  tags.append("📈 200日線から大幅上方乖離")
        elif ma200_pct <= -15: tags.append("📉 200日線を大幅下回り")
    prog_delta = fs.get("progress_rate_delta", 0)
    if prog_delta > 0.08:   tags.append("💹 業績好調（上方修正期待）")
    elif prog_delta < -0.08: tags.append("⚠️ 業績軟調")
    if vol_ratio >= 2.0:    tags.append(f"🔊 出来高急増({vol_ratio:.1f}x)")
    elif vol_ratio <= 0.5:  tags.append("🔕 出来高閑散")
    if change_pct >= 3.0:   tags.append(f"⬆️ 急騰(前日比{change_pct:+.1f}%)")
    elif change_pct <= -3.0: tags.append(f"⬇️ 急落(前日比{change_pct:+.1f}%)")
    condition = " / ".join(tags) if tags else "📊 特段の過熱・売られすぎなし"

    return StockOverview(
        name=stock_name or ticker,
        current_price=current_price,
        change_pct=change_pct,
        volume_ratio=vol_ratio,
        rsi=rsi,
        per=per,
        market_condition=condition,
        price_vs_ma200_pct=ma200_pct,
    )
