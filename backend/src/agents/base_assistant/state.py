"""Dữ liệu chung của graph trong một lượt; không chứa danh tính hoặc secrets."""

from langgraph.graph import MessagesState

from src.schemas.agent import AnswerStatus


class AgentState(MessagesState):
    """MessagesState đã có messages và reducer add_messages để ghép theo ID.

    Messages giữ đúng thứ tự System/Human/AI/Tool; node chỉ trả message mới.
    response là kết quả của graph, chỉ được công bố sau guardrails tại base.
    """

    response: str
    status: AnswerStatus
