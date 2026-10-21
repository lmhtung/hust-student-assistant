"""Student dùng state chung và bổ sung bản nháp, bộ đếm tra cứu của graph."""

from src.agents.base_assistant.state import AgentState


class StudentState(AgentState):
    """Chỉ sống trong một lượt; bản nháp không được đưa vào event gửi client."""

    draft: str
    tool_rounds: int
    tool_calls: int
