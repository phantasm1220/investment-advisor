"""
utils/kelly_criterion.py
ケリー基準によるポジションサイズ計算ユーティリティ
"""
import math


def kelly_fraction(
    win_probability: float,
    win_loss_ratio: float,
    fraction: float = 0.5,
) -> float:
    """
    ケリー基準によるポジションサイズを計算する。

    Args:
        win_probability: 勝率 (0〜1)
        win_loss_ratio: 勝ち時の利益 / 負け時の損失 (例: 目標+15%, 損切-5% → 3.0)
        fraction: フラクショナルケリー係数 (安全のため通常0.25〜0.5)

    Returns:
        推奨ポジションサイズ（総資産に対する割合、0〜1）
    """
    if win_probability <= 0 or win_probability >= 1:
        return 0.0
    if win_loss_ratio <= 0:
        return 0.0

    b = win_loss_ratio
    p = win_probability
    q = 1 - p

    # Kelly formula: f* = (bp - q) / b
    kelly = (b * p - q) / b
    kelly = max(0.0, kelly)  # 負値はポジションゼロ

    # フラクショナルケリーを適用（過大投資防止）
    return min(kelly * fraction, 0.25)  # 最大25%キャップ


def adjust_for_volatility(
    base_size: float,
    current_volatility: float,
    target_volatility: float = 0.15,
) -> float:
    """
    ボラティリティベースでポジションサイズを調整する。
    ボラティリティが高い時は自動的にサイズ縮小。

    Args:
        base_size: ベースのポジションサイズ (0〜1)
        current_volatility: 現在の年率ボラティリティ（例: 0.25 = 25%）
        target_volatility: 目標ボラティリティ（デフォルト15%）

    Returns:
        調整後のポジションサイズ
    """
    if current_volatility <= 0:
        return base_size
    vol_ratio = target_volatility / current_volatility
    return base_size * min(vol_ratio, 1.0)  # ボラ高い時のみ縮小


def calc_position_size(
    confidence: float,
    target_return_pct: float,
    stop_loss_pct: float,
    current_volatility: float = 0.20,
    kelly_fraction_coef: float = 0.5,
) -> float:
    """
    確信度・目標リターン・損切り設定からポジションサイズを計算する統合関数。

    Args:
        confidence: エージェントの総合確信度 (0〜1)
        target_return_pct: 目標利益率 (例: 0.15 = 15%)
        stop_loss_pct: 損切り率 (例: 0.07 = 7%)
        current_volatility: 現在の年率ボラティリティ
        kelly_fraction_coef: フラクショナルケリー係数

    Returns:
        推奨ポジションサイズ（%）
    """
    win_loss_ratio = target_return_pct / stop_loss_pct if stop_loss_pct > 0 else 1.0
    base = kelly_fraction(confidence, win_loss_ratio, kelly_fraction_coef)
    adjusted = adjust_for_volatility(base, current_volatility)
    return round(adjusted * 100, 1)  # %で返す
