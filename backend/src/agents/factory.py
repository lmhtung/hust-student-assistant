"""Chọn agent bằng role student/admin đã xác thực, không do LLM quyết định."""

from src.agents.base_assistant.agent import BaseAssistant
from src.schemas.auth import CurrentUser


class AdminAssistantUnavailable(RuntimeError):
    """Agent cho admin chưa được triển khai và chưa có quyền ghi nào."""


def get_assistant(user: CurrentUser, student_assistant: BaseAssistant) -> BaseAssistant:
    if user.role == "admin":
        raise AdminAssistantUnavailable("Admin assistant chưa được triển khai")
    if user.role != "student" or "academic:read" not in user.permissions:
        raise PermissionError("Chỉ student có academic:read được sử dụng")
    return student_assistant
