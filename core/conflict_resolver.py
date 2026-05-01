"""
core/conflict_resolver.py

エージェント間の矛盾を検出し、マネージャーが使う「リスクリワード再計算」ロジック。

典型的矛盾例:
  - ファンダメンタルズ=BUY, マクロ=SELL（地政学リスク高）
  - テクニカル=STRONG_BUY, マクロ=HOLD（景気後退懸念）
"""

import logging
from dataclasses import dataclass
from typing import Optional

from core.signal import AgentSignal, Verdict, RiskLevel

logger = logging.getLogger(__name__)


@dataclass
class ConflictAnalysis:
    """矛盾分析の結果"""
    has_conflict: bool
    conflict_type: str        # 矛盾の種類
    description: str          # 人間可読な説明
    confidence_penalty: float # 確信度へのペナルティ（0〜1を乗算）
    recommended_action: str   # マネージャーへの推奨アクション


class ConflictResolver:
    """
    3エージェントのシグナルを受け取り、矛盾を検出・解釈して
    最終判断に使うConfidenceAdjustmentを返す。

    ベイズ的思考:
      P(実際に上昇 | ファンダBUY ∧ マクロSELL)
        = P(マクロSELL | 実際上昇) × P(実際上昇 | ファンダBUY) / P(マクロSELL)

    この実装では簡略化として「矛盾パターン × 過去勝率」から
    ペナルティ係数を経験則で設定する。
    """

    # エージェントごとの局面別過去勝率（バックテスト結果で更新すること）
    AGENT_RELIABILITY: dict[str, dict[str, float]] = {
        "fundamentals": {
            "individual_stock": 0.68,  # 個別株局面での勝率
            "macro_stress":     0.42,  # マクロショック時の勝率（低い）
        },
        "macro": {
            "macro_dominant":   0.72,  # マクロ主導相場での勝率
            "calm_market":      0.51,  # 穏やかな市場での勝率
        },
        "technical": {
            "trending":         0.65,  # トレンド相場での勝率
            "range_bound":      0.55,  # レンジ相場での勝率
        },
    }

    def resolve(
        self,
        fundamentals: AgentSignal,
        macro: AgentSignal,
        technical: AgentSignal,
    ) -> ConflictAnalysis:
        """矛盾検出 → 適切なConflictAnalysisを返す"""

        fund_num  = fundamentals.to_numeric()
        macro_num = macro.to_numeric()
        tech_num  = technical.to_numeric()

        # ─── パターン3を先に判定: 全員バラバラ（意見が三者三様） ───
        # BUY/HOLD/SELLが全て揃っている場合は個別矛盾より優先
        if self._all_different(fund_num, macro_num, tech_num):
            return ConflictAnalysis(
                has_conflict=True,
                conflict_type="THREE_WAY_SPLIT",
                description=(
                    "3エージェントの意見が完全に分散しています。"
                    "市場の方向感が不明確な可能性が高く、様子見を推奨。"
                ),
                confidence_penalty=0.40,  # 確信度を40%に圧縮
                recommended_action="HOLD: 方向感が出るまで待機",
            )

        # ─── パターン1: ファンダBUY + マクロSELL（地政学・金融リスク） ───
        if fund_num > 0 and macro_num < 0:
            return self._handle_fundamental_vs_macro(fundamentals, macro)

        # ─── パターン2: テクニカルBUY + ファンダSELL（過熱・バリュエーション） ───
        if tech_num > 0.5 and fund_num < 0:
            return self._handle_technical_vs_fundamental(technical, fundamentals)

        # ─── パターン4: 2対1（多数派あり） ───
        majority_verdict, minority = self._get_majority(
            fund_num, macro_num, tech_num
        )
        if minority is not None:
            penalty = self._calc_minority_penalty(minority)
            return ConflictAnalysis(
                has_conflict=True,
                conflict_type="MAJORITY_WITH_DISSENT",
                description=(
                    f"2対1で {majority_verdict} 優勢ですが、"
                    f"少数意見（{minority.agent_name}）も考慮が必要。"
                ),
                confidence_penalty=penalty,
                recommended_action=f"方向は {majority_verdict} だが規模を抑制",
            )

        # ─── 矛盾なし ───
        return ConflictAnalysis(
            has_conflict=False,
            conflict_type="CONSENSUS",
            description="全エージェントが同方向。確信度はそのまま採用。",
            confidence_penalty=1.0,
            recommended_action="通常ポジションサイズで実行可",
        )

    # ──────────────────────────────────────────────
    #  矛盾パターン別の詳細ロジック
    # ──────────────────────────────────────────────

    def _handle_fundamental_vs_macro(
        self,
        fund: AgentSignal,
        macro: AgentSignal,
    ) -> ConflictAnalysis:
        """
        ファンダメンタルズBUY vs マクロSELL の矛盾解消

        考え方:
          - マクロリスクが「一時的」なら、ファンダを優先してBUYできる
          - マクロリスクが「構造的」（金利上昇サイクル継続 etc.）なら
            どれだけ割安でも株価は下がる可能性が高い
          - 地政学リスク（戦争・制裁）は価格に織り込まれにくいため
            マクロ担当の警告を重く見る
        """
        macro_is_geopolitical = any(
            kw in " ".join(macro.key_factors).lower()
            for kw in ["地政学", "geopolit", "war", "sanction", "制裁", "戦争"]
        )

        if macro.risk_level in (RiskLevel.HIGH, RiskLevel.EXTREME):
            if macro_is_geopolitical:
                return ConflictAnalysis(
                    has_conflict=True,
                    conflict_type="FUND_BUY_vs_MACRO_GEOPOLITICAL",
                    description=(
                        "【矛盾: ファンダBUY × マクロ地政学リスク高】\n"
                        "地政学リスクは「価格に織り込まれない突発的ショック」を引き起こしやすい。"
                        "ファンダメンタルズの割安さは正しいが、ショック発生時の"
                        "一時的な下落余地（10〜30%）を見込んだポジションサイズに縮小すべき。\n"
                        "戦略: 通常の1/3サイズでエントリーし、リスクイベント通過後に追加。"
                    ),
                    confidence_penalty=0.35,
                    recommended_action=(
                        "SMALL_BUY: ファンダ優位だがポジション1/3に制限。"
                        "損切りラインをタイトに設定（-5〜7%）。"
                    ),
                )
            else:
                # 金利・景気懸念など構造的マクロリスク
                return ConflictAnalysis(
                    has_conflict=True,
                    conflict_type="FUND_BUY_vs_MACRO_STRUCTURAL",
                    description=(
                        "【矛盾: ファンダBUY × マクロ構造リスク】\n"
                        f"マクロ担当の懸念事項: {', '.join(macro.key_factors[:3])}\n"
                        "構造的な金利上昇・景気後退局面では"
                        "「割安株がさらに割安になる」現象（バリュートラップ）に注意。\n"
                        f"マクロ担当の過去勝率（マクロ主導局面）: "
                        f"{self.AGENT_RELIABILITY['macro']['macro_dominant']:.0%}\n"
                        "ファンダ担当の過去勝率（マクロストレス局面）: "
                        f"{self.AGENT_RELIABILITY['fundamentals']['macro_stress']:.0%}\n"
                        "→ この局面ではマクロ担当の信頼度を優先。"
                    ),
                    confidence_penalty=0.30,
                    recommended_action=(
                        "HOLD or SMALL_BUY: マクロ転換のシグナルを確認後エントリー推奨。"
                    ),
                )
        else:
            # マクロがLOW/MEDIUMリスクならファンダを優先
            return ConflictAnalysis(
                has_conflict=True,
                conflict_type="FUND_BUY_vs_MACRO_MILD",
                description=(
                    "マクロの懸念は軽微。ファンダメンタルズ優位の個別株局面と判断。"
                    "ファンダ担当の個別株勝率を優先採用。"
                ),
                confidence_penalty=0.75,
                recommended_action="BUY: ただし市場急変に備えてストップロス設定必須",
            )

    def _handle_technical_vs_fundamental(
        self,
        tech: AgentSignal,
        fund: AgentSignal,
    ) -> ConflictAnalysis:
        """テクニカル過熱 vs ファンダ割高の矛盾"""
        return ConflictAnalysis(
            has_conflict=True,
            conflict_type="TECH_OVERBOUGHT_vs_FUND_OVERVALUED",
            description=(
                "【矛盾: テクニカル強気 × ファンダ割高】\n"
                "モメンタム相場では「割高でも買われ続ける」が、"
                "いずれファンダへの回帰が起きる。\n"
                "テクニカルシグナルはエントリータイミング、"
                "ファンダは上値の限界（バリュエーション天井）を示している。\n"
                "戦略: 新規エントリーは見送り。既存ポジションはトレーリングストップで保護。"
            ),
            confidence_penalty=0.45,
            recommended_action="HOLD/PARTIAL_SELL: 新規買いは見送り、利益確定を検討",
        )

    # ──────────────────────────────────────────────
    #  ユーティリティ
    # ──────────────────────────────────────────────

    @staticmethod
    def _all_different(a: float, b: float, c: float) -> bool:
        """3値が全て異なる方向（正・零・負）かを判定"""
        signs = {(v > 0.1) - (v < -0.1) for v in (a, b, c)}
        return len(signs) == 3

    @staticmethod
    def _get_majority(
        fund_num: float, macro_num: float, tech_num: float
    ) -> tuple[str, Optional[AgentSignal]]:
        """多数派の方向と少数派シグナルを返す（簡易実装）"""
        bull = sum(1 for v in (fund_num, macro_num, tech_num) if v > 0.1)
        bear = sum(1 for v in (fund_num, macro_num, tech_num) if v < -0.1)
        if bull >= 2:
            return "BUY", None
        if bear >= 2:
            return "SELL", None
        return "HOLD", None

    @staticmethod
    def _calc_minority_penalty(minority: Optional[AgentSignal]) -> float:
        """少数意見の確信度に応じたペナルティを計算"""
        if minority is None:
            return 0.80
        # 少数派の確信度が高いほど多数派の信頼度を下げる
        return max(0.50, 0.90 - minority.confidence * 0.40)
