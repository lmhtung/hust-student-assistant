"""Kiểu kết quả chung; không phụ thuộc graph hoặc tầng HTTP."""

from dataclasses import dataclass
from typing import Literal

AnswerStatus = Literal["answered", "blocked", "clarification_required", "limited"]


@dataclass(frozen=True)
class AgentResult:
    """Câu trả lời hoàn chỉnh đã qua kiểm tra an toàn của agent."""

    response: str
    status: AnswerStatus = "answered"
