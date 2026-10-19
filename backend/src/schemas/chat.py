"""Dữ liệu vào/ra dùng chung cho API JSON và SSE."""

from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.agents.base_assistant.agent import AnswerStatus


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID = Field(default_factory=uuid4)
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]


class ChatResponse(BaseModel):
    response_id: UUID
    session_id: UUID
    response: str
    status: AnswerStatus
