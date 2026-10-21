"""Sự kiện dùng chung cho agent và service; không chứa bản nháp hoặc tool payload."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.agent import AgentResult


class AgentProgress(BaseModel):
    """Các trạng thái do code phát ra; không nhận nội dung tự do từ model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Literal["checking_input", "thinking", "using_tools", "checking_output"]


class AgentEvent(BaseModel):
    """Danh tính và thứ tự sự kiện trong một lượt xử lý."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    session_id: UUID
    sequence: int = Field(ge=1)


class StatusEvent(AgentEvent):
    event: Literal["status"] = "status"
    data: AgentProgress


class ResultEvent(AgentEvent):
    """Agent đã kiểm tra kết quả; service còn phải lưu trước khi phát API answer."""

    event: Literal["result"] = "result"
    data: AgentResult


AgentStreamEvent = StatusEvent | ResultEvent
