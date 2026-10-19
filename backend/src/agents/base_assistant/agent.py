"""Giao diện chung; quyền chọn agent nằm trong code, không giao cho LLM."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from src.agents.base_assistant.context import AgentContext

AnswerStatus = Literal["answered", "blocked", "clarification_required", "limited"]
StatusEmitter = Callable[[dict], Awaitable[None]]


@dataclass(frozen=True)
class AgentResult:
    """Chỉ câu trả lời đã được kiểm tra mới được đưa ra ngoài agent."""

    response: str
    status: AnswerStatus = "answered"


class BaseAssistant(ABC):
    @abstractmethod
    async def run(
        self, message: str, history: list[dict], context: AgentContext, emit: StatusEmitter
    ) -> AgentResult:
        """Một lượt chat; emit chỉ gửi trạng thái được định nghĩa trong code."""
