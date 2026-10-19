"""State chỉ sống trong một lượt graph; không lưu JWT, secrets hay suy luận."""

from typing import TypedDict

from src.agents.base_assistant.agent import AnswerStatus


class StudentState(TypedDict):
    messages: list[dict]
    draft: str
    response: str
    status: AnswerStatus
    tool_rounds: int
    tool_calls: int
