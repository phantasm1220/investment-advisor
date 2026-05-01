"""agents/institutional_agent.py v4.9 — プロンプト最適化版"""
import logging
from datetime import date
from typing import Any

import yfinance as yf

from core.base_agent import BaseAgent
from core.signal import AgentSignal, Verdict, RiskLevel, DataSource
from utils.market_data import SUBSECTOR_MAP, SECTOR_MAP

logger = logging.getLogger(__name__)

TARGET_INSTITUTIONS = [
    "Goldman Sachs","Morgan Stanley","JPMorgan","Bank of America",
    "BlackRock","Vanguard","Deutsche Bank","Citigroup","UBS","野村証券","大和証券",
]

SYSTEM_PROMPT = """機関投資家リサーチの専門家として、指定銘柄に対する主要機関の最新見解を調査・統合してください。
⚠️重要: 必ず指定された銘柄コード・企業名のデータのみを使用すること。他銘柄と混同しないこと。
JSON形式のみで返答:
{"verdict":"BUY|SELL|HOLD|STRONG_BUY|STRONG_SELL","confidence":0.0~1.0,"risk_level":"LOW|MEDIUM|HIGH|EXTREME","summary":"250字以内。複数機関の見解統合","key_factors":["機関名:根拠","機関名:根拠","コンセンサス方向"],"institutional_data":{"consensus_rating":"OVERWEIGHT|EQUALWEIGHT|UNDERWEIGHT","avg_target_price":数値orNull,"bullish_institutions":["機関名"],"bearish_institutions":["機関名"],"recent_rating_changes":["変更要約"],"smart_money_flow":"INFLOW|NEUTRAL|OUTFLOW"},"raw_scores":{"institutional_consensus_score":0~1,"rating_momentum":"UPGRADING|STABLE|DOWNGRADING","data_freshness":"FRESH|DATED|ESTIMATED"}}"""

MAX_TOKENS = 800


class InstitutionalAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="institutional", system_prompt=SYSTEM_PROMPT)
        self.MAX_TOKENS = MAX_TOKENS

    def gather_data(self, ticker: str) -> dict[str, Any]:
        today = date.today().isoformat()
        name, sector = _get_info(ticker)
        return {
            "ticker": ticker, "name": name, "sector": sector,
            "data_fetch_date": today,
            "today_str": date.today().strftime("%Y年%m月%d日"),
        }

    def build_user_prompt(self, ticker: str, d: dict) -> str:
        inst = "、".join(TARGET_INSTITUTIONS[:8])
        return f"""{d['today_str']}時点。以下の銘柄のみ調査してください。

【調査対象（厳守）】
コード:{ticker}（東証） 企業名:{d['name']} セクター:{d['sector']}
⚠️ {ticker}は「{d['name']}」です。他銘柄と混同しないこと。

【調査機関】{inst}

調査内容:
1. {d['name']}({ticker})の最新レーティング・目標株価（直近3〜6ヶ月）
2. {d['sector']}セクターへの機関投資家スタンス・資金フロー
3. 最近のレーティング変化
4. 強気・弱気機関それぞれの根拠

確認できない情報は「確認できず」と記載。情報鮮度をkey_factorsに含めること。JSONのみで回答。"""

    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal:
        d = self.gather_data(ticker)
        try:
            parsed = self._safe_parse_json(raw_text)
        except Exception as e:
            logger.warning(f"[institutional] パース失敗: {e}")
            parsed = {
                "verdict":"HOLD","confidence":0.35,"risk_level":"MEDIUM",
                "summary":"機関投資家データ取得失敗。","key_factors":["データ取得不可"],
                "institutional_data":{"consensus_rating":"EQUALWEIGHT","avg_target_price":None,
                    "bullish_institutions":[],"bearish_institutions":[],
                    "recent_rating_changes":[],"smart_money_flow":"NEUTRAL"},
                "raw_scores":{"institutional_consensus_score":0.5,
                    "rating_momentum":"STABLE","data_freshness":"ESTIMATED"},
            }
        rs = parsed.setdefault("raw_scores", {})
        inst = parsed.get("institutional_data", {})
        for k, v in inst.items():
            rs[f"inst__{k}"] = v
        return AgentSignal(
            agent_name=self.name, ticker=ticker,
            verdict=Verdict(parsed.get("verdict","HOLD")),
            confidence=float(parsed.get("confidence",0.35)),
            risk_level=RiskLevel(parsed.get("risk_level","MEDIUM")),
            summary=parsed.get("summary",""),
            key_factors=parsed.get("key_factors",[]),
            raw_scores=rs,
            data_sources=[DataSource(name="機関投資家レーティング", date=d["data_fetch_date"],
                note=f"コンセンサス:{inst.get('consensus_rating','N/A')} 鮮度:{rs.get('data_freshness','ESTIMATED')}")],
        )


def _get_info(ticker: str) -> tuple[str, str]:
    code = ticker.replace(".T","")
    try:
        info = yf.Ticker(f"{code}.T").info or {}
        name = info.get("longName") or info.get("shortName") or ticker
        if code in SUBSECTOR_MAP:
            sector = SUBSECTOR_MAP[code]
        else:
            sector = SECTOR_MAP.get(info.get("sector",""), info.get("sector","不明"))
        return name, sector
    except:
        return ticker, "不明"
