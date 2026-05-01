"""
agents/manager_agent.py  v4.7

4エージェント統合版。機関投資家シグナルを追加。

重み（ベース）:
  ファンダメンタルズ: 30%  マクロ: 25%
  テクニカル:        25%  機関投資家: 20%

機関投資家シグナルの鮮度（data_freshness）で重みを動的調整:
  FRESH   → 20%  DATED → 12%  ESTIMATED → 5%
"""
import logging
from core.llm_client import LLMClient
from core.signal import AgentSignal, FinalDecision, Verdict, RiskLevel
from core.conflict_resolver import ConflictResolver, ConflictAnalysis
from utils.kelly_criterion import calc_position_size

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは投資ファンドのポートフォリオマネージャーです。
4名の専門アナリスト（ファンダメンタルズ・マクロ・テクニカル・機関投資家）の分析を統合し、
最終投資判断を下す責任があります。

統合の原則:
1. 機関投資家エージェントが「FRESH」な情報を持つ場合は重視する
2. 複数エージェントの意見が一致している場合は確信度を高める
3. 機関投資家のコンセンサスが「OVERWEIGHT（強気）」なら追い風、「UNDERWEIGHT（弱気）」なら逆風
4. スマートマネーの資金フロー（INFLOW/OUTFLOW）を重要なシグナルとして扱う
5. AI・半導体・エネルギーなど現在の主要テーマへの整合性を確認する

出力はJSON形式のみ:
{
  "verdict": "BUY|SELL|HOLD|STRONG_BUY|STRONG_SELL",
  "composite_confidence": 0.0〜1.0,
  "target_price": 数値またはnull,
  "stop_loss": 数値またはnull,
  "rationale": "250字以内の最終判断理由。機関投資家コンセンサスへの言及必須",
  "risk_reward_ratio": 数値,
  "max_drawdown_estimate": 数値,
  "dominant_risk_factor": "最も警戒すべきリスク"
}"""

# 局面別重み（機関投資家を追加、合計1.0に正規化）
AGENT_WEIGHTS = {
    "MACRO_DOMINANT":   {"fundamentals": 0.18, "macro": 0.45, "technical": 0.20, "institutional": 0.17},
    "INDIVIDUAL_STOCK": {"fundamentals": 0.38, "macro": 0.18, "technical": 0.25, "institutional": 0.19},
    "BALANCED":         {"fundamentals": 0.28, "macro": 0.24, "technical": 0.24, "institutional": 0.24},
}
CONFIDENCE_THRESHOLD = 0.70


class ManagerAgent:
    def __init__(self):
        self._llm      = LLMClient()
        self._resolver = ConflictResolver()

    def integrate(
        self,
        fundamentals: AgentSignal,
        macro: AgentSignal,
        technical: AgentSignal,
        current_price: float,
        institutional: AgentSignal | None = None,
    ) -> FinalDecision:
        ticker   = fundamentals.ticker
        conflict = self._resolver.resolve(fundamentals, macro, technical)
        regime   = self._detect_regime(macro)

        # 機関投資家シグナルの鮮度で重みを動的調整
        weights  = self._calc_weights(regime, institutional)
        composite = self._calc_confidence(
            fundamentals, macro, technical, institutional, weights, conflict
        )

        data = self._call_llm(
            fundamentals, macro, technical, institutional,
            conflict, composite, current_price
        )

        verdict = Verdict(data.get("verdict", "HOLD"))
        if composite < CONFIDENCE_THRESHOLD and verdict != Verdict.HOLD:
            logger.info(f"[manager] 確信度({composite:.2f}) < 閾値 → HOLD")
            verdict = Verdict.HOLD

        target, stop = data.get("target_price"), data.get("stop_loss")
        position_size = 0.0
        if verdict in (Verdict.BUY, Verdict.STRONG_BUY) and target and stop and current_price > 0:
            position_size = calc_position_size(
                confidence=composite,
                target_return_pct=(target - current_price) / current_price,
                stop_loss_pct=(current_price - stop) / current_price,
            )

        show_prices = composite >= CONFIDENCE_THRESHOLD
        signals = [fundamentals, macro, technical]
        if institutional:
            signals.append(institutional)

        return FinalDecision(
            ticker=ticker,
            verdict=verdict,
            composite_confidence=composite,
            target_price=target if show_prices else None,
            stop_loss=stop    if show_prices else None,
            position_size_pct=position_size,
            rationale=data.get("rationale", ""),
            conflict_note=conflict.description if conflict.has_conflict else "",
            agent_signals=signals,
        )

    def _calc_weights(self, regime: str, institutional: AgentSignal | None) -> dict:
        base = dict(AGENT_WEIGHTS.get(regime, AGENT_WEIGHTS["BALANCED"]))
        if institutional is None:
            # 機関投資家なし → その分を他3エージェントで等分
            inst_w = base.pop("institutional", 0.20)
            total  = sum(base.values())
            return {k: v + inst_w * v / total for k, v in base.items()}

        # 鮮度による重み調整
        freshness = institutional.raw_scores.get("data_freshness", "ESTIMATED")
        inst_w = {"FRESH": 0.20, "DATED": 0.12, "ESTIMATED": 0.05}.get(freshness, 0.10)

        diff   = base["institutional"] - inst_w
        scale  = 1.0 + diff / (sum(base.values()) - base["institutional"])
        return {
            k: v * scale if k != "institutional" else inst_w
            for k, v in base.items()
        }

    def _calc_confidence(self, fund, macro, tech, inst, weights, conflict) -> float:
        c = (
            weights.get("fundamentals",  0.28) * fund.confidence
          + weights.get("macro",         0.24) * macro.confidence
          + weights.get("technical",     0.24) * tech.confidence
          + (weights.get("institutional",0.24) * inst.confidence if inst else 0)
        ) * conflict.confidence_penalty

        # 全エージェント同方向ボーナス
        nums = [fund.to_numeric(), macro.to_numeric(), tech.to_numeric()]
        if inst: nums.append(inst.to_numeric())
        if len(set((v > 0.1) - (v < -0.1) for v in nums)) == 1 and not conflict.has_conflict:
            c = min(1.0, c * 1.12)

        # スマートマネー流入ボーナス
        if inst:
            flow = inst.raw_scores.get("inst__smart_money_flow", "NEUTRAL")
            if flow == "INFLOW":
                c = min(1.0, c * 1.05)
            elif flow == "OUTFLOW":
                c *= 0.95

        logger.info(f"[manager] composite={c:.3f} (regime detected, inst_freshness={inst.raw_scores.get('data_freshness','N/A') if inst else 'N/A'})")
        return round(c, 3)

    def _detect_regime(self, macro: AgentSignal) -> str:
        regime = macro.raw_scores.get("regime", "BALANCED")
        if regime in ("INFLATION_FEAR", "RECESSION_FEAR"):
            return "MACRO_DOMINANT"
        return "INDIVIDUAL_STOCK" if macro.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM) else "BALANCED"

    def _call_llm(self, fund, macro, tech, inst, conflict, composite, current_price) -> dict:
        inst_section = ""
        if inst:
            rs        = inst.raw_scores
            consensus = rs.get("inst__consensus_rating", "N/A")
            flow      = rs.get("inst__smart_money_flow", "N/A")
            momentum  = rs.get("rating_momentum", "N/A")
            freshness = rs.get("data_freshness", "N/A")
            inst_section = f"""
【機関投資家エージェント】{inst.verdict.value} 確信度{inst.confidence:.2f} (鮮度:{freshness})
コンセンサス:{consensus}  スマートマネー:{flow}  レーティング動向:{momentum}
{inst.summary[:150]}
根拠: {'; '.join(inst.key_factors[:3])}
"""

        prompt = f"""
以下の4エージェントの分析を統合し、最終投資判断を下してください。

【ファンダメンタルズ】{fund.verdict.value} 確信度{fund.confidence:.2f}
{fund.summary[:120]}
根拠: {'; '.join(fund.key_factors[:2])}

【マクロ】{macro.verdict.value} 確信度{macro.confidence:.2f} リスク{macro.risk_level.value}
{macro.summary[:120]}
根拠: {'; '.join(macro.key_factors[:2])}

【テクニカル】{tech.verdict.value} 確信度{tech.confidence:.2f}
{tech.summary[:120]}
根拠: {'; '.join(tech.key_factors[:2])}
{inst_section}
【矛盾分析】{conflict.conflict_type}: {conflict.recommended_action}
【総合確信度（計算済み）】{composite:.3f}  【現在株価】¥{current_price:,.0f}

機関投資家のコンセンサスとスマートマネーの動向を必ず rationale に言及してください。
確信度{CONFIDENCE_THRESHOLD}以上の場合のみ目標価格(+10〜20%)と損切価格(-5〜10%)を提示。
"""
        try:
            return LLMClient.safe_parse_json(
                self._llm.chat(SYSTEM_PROMPT, prompt, max_tokens=600)
            )
        except Exception as e:
            logger.error(f"[manager] LLM エラー: {e}")
            return {
                "verdict": "HOLD", "composite_confidence": composite,
                "target_price": None, "stop_loss": None,
                "rationale": "統合判断中にエラーが発生しました。",
                "risk_reward_ratio": 0.0, "max_drawdown_estimate": 20.0,
                "dominant_risk_factor": "システムエラー",
            }
