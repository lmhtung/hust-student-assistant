"""Dispatch bằng role đã xác thực; không cho LLM handoff sang manager."""

from src.agents.base_assistant.agent import BaseAssistant
from src.schemas.auth import CurrentUser


class ManagerAssistantUnavailable(RuntimeError):
    """Mốc mở rộng: manager chưa được triển khai và chưa có quyền ghi nào."""


def get_assistant(user: CurrentUser, student_assistant: BaseAssistant) -> BaseAssistant:
    if user.role in {"admin", "manager"}:
        raise ManagerAssistantUnavailable("Manager assistant chưa được triển khai")
    if user.role != "student" or "academic:read" not in user.permissions:
        raise PermissionError("Chỉ student có academic:read được sử dụng")
    return student_assistant
