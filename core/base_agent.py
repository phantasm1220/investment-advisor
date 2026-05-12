"""
core/base_agent.py  v4.4
最適化: MAX_TOKENS を 1024 に削減（JSONレスポンスは短いため）
"""
import logging
from abc import ABC, abstractmethod
from typing import Any

from core.llm_client import LLMClient
from core.signal import AgentSignal

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    MAX_TOKENS = 1024   # 2048→1024: JSON出力なので十分

    def __init__(self, name: str, system_prompt: str):
        self.name = name
        self.system_prompt = system_prompt
        self._llm = LLMClient()

    @abstractmethod
    def gather_data(self, ticker: str) -> dict[str, Any]: ...

    @abstractmethod
    def build_user_prompt(self, ticker: str, data: dict[str, Any]) -> str: ...

    @abstractmethod
    def parse_response(self, ticker: str, raw_text: str) -> AgentSignal: ...

    def analyze(self, ticker: str) -> AgentSignal:
        logger.info(f"[{self.name}] {ticker} 分析中...")
        data        = self.gather_data(ticker)
        user_prompt = self.build_user_prompt(ticker, data)
        raw_text    = self._llm.chat(self.system_prompt, user_prompt, self.MAX_TOKENS)
        signal      = self.parse_response(ticker, raw_text)
        logger.info(f"[{self.name}] {ticker}: {signal.verdict.value} ({signal.confidence:.2f})")
        return signal

    @staticmethod
    def _safe_parse_json(text: str) -> dict:
        return LLMClient.safe_parse_json(text)
