"""
tests/test_agents.py
各コンポーネントの単体テスト
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.signal import AgentSignal, Verdict, RiskLevel
from core.conflict_resolver import ConflictResolver
from utils.kelly_criterion import kelly_fraction, calc_position_size


# ─────────────────────────────────────────
#  ConflictResolver テスト
# ─────────────────────────────────────────

def _make_signal(agent: str, verdict: Verdict, confidence: float, risk: RiskLevel,
                 key_factors=None) -> AgentSignal:
    return AgentSignal(
        agent_name=agent, ticker="TEST",
        verdict=verdict, confidence=confidence,
        risk_level=risk, summary="test",
        key_factors=key_factors or [],
    )


def test_conflict_fund_buy_macro_geopolitical():
    """ファンダBUY + マクロ地政学リスクSELL → ペナルティ大（テクニカルHOLD=0.0で三者分散にならないよう調整）"""
    resolver = ConflictResolver()
    fund = _make_signal("fundamentals", Verdict.BUY, 0.8, RiskLevel.LOW)
    macro = _make_signal("macro", Verdict.SELL, 0.75, RiskLevel.HIGH,
                         key_factors=["地政学リスク上昇", "中東緊張"])
    # techをSELLにしてfundとmacroの矛盾に焦点（二者が対立、techは同方向）
    tech = _make_signal("technical", Verdict.SELL, 0.5, RiskLevel.MEDIUM)

    result = resolver.resolve(fund, macro, tech)
    assert result.has_conflict
    assert "GEOPOLITICAL" in result.conflict_type
    assert result.confidence_penalty < 0.50
    print(f"✅ test_conflict_fund_buy_macro_geopolitical: penalty={result.confidence_penalty}")


def test_consensus_all_buy():
    """全員BUY → 矛盾なし"""
    resolver = ConflictResolver()
    fund = _make_signal("fundamentals", Verdict.BUY, 0.8, RiskLevel.LOW)
    macro = _make_signal("macro", Verdict.BUY, 0.7, RiskLevel.MEDIUM)
    tech = _make_signal("technical", Verdict.BUY, 0.75, RiskLevel.LOW)

    result = resolver.resolve(fund, macro, tech)
    assert not result.has_conflict
    assert result.confidence_penalty == 1.0
    print(f"✅ test_consensus_all_buy: penalty={result.confidence_penalty}")


def test_three_way_split():
    """三者三様 → HOLDへ"""
    resolver = ConflictResolver()
    fund = _make_signal("fundamentals", Verdict.BUY,  0.7, RiskLevel.LOW)
    macro = _make_signal("macro",  Verdict.SELL, 0.65, RiskLevel.HIGH)
    tech = _make_signal("technical", Verdict.HOLD, 0.5, RiskLevel.MEDIUM)

    result = resolver.resolve(fund, macro, tech)
    assert result.has_conflict
    assert result.conflict_type == "THREE_WAY_SPLIT"
    assert result.confidence_penalty <= 0.40
    print(f"✅ test_three_way_split: penalty={result.confidence_penalty}")


# ─────────────────────────────────────────
#  Kelly Criterion テスト
# ─────────────────────────────────────────

def test_kelly_basic():
    """基本的なケリー計算"""
    result = kelly_fraction(win_probability=0.6, win_loss_ratio=2.0, fraction=0.5)
    assert 0 < result <= 0.25
    print(f"✅ test_kelly_basic: fraction={result:.4f}")


def test_kelly_negative_expectation():
    """期待値マイナスの場合はゼロ"""
    result = kelly_fraction(win_probability=0.3, win_loss_ratio=0.5, fraction=0.5)
    assert result == 0.0
    print(f"✅ test_kelly_negative_expectation: fraction={result}")


def test_calc_position_size():
    """高確信度・良好なリスクリワード"""
    size = calc_position_size(
        confidence=0.75,
        target_return_pct=0.15,
        stop_loss_pct=0.07,
        current_volatility=0.20,
    )
    assert 0 < size <= 25.0
    print(f"✅ test_calc_position_size: size={size:.1f}%")


if __name__ == "__main__":
    print("\n=== テスト実行 ===\n")
    test_conflict_fund_buy_macro_geopolitical()
    test_consensus_all_buy()
    test_three_way_split()
    test_kelly_basic()
    test_kelly_negative_expectation()
    test_calc_position_size()
    print("\n✅ 全テスト通過\n")
