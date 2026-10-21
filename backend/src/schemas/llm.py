"""Kiểm tra JSON từ model server trước khi chuyển sang LangChain messages."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ToolFunction(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"\S")
    arguments: str


class ToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=256, pattern=r"\S")
    type: Literal["function"] = "function"
    function: ToolFunction


class ModelMessage(BaseModel):
    """Chỉ nhận text và function calls; các field phụ của provider bị bỏ qua."""

    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @field_validator("tool_calls", mode="before")
    @classmethod
    def normalize_null_calls(cls, value):
        # Một số model server trả null khi chỉ trả văn bản.
        return [] if value is None else value


class StreamFunction(BaseModel):
    """Tên và JSON arguments có thể được chia qua nhiều delta."""

    name: str | None = None
    arguments: str | None = None


class StreamToolCall(BaseModel):
    index: int = Field(ge=0, strict=True)
    id: str | None = None
    type: Literal["function"] | None = None
    function: StreamFunction | None = None


class ModelDelta(BaseModel):
    """Chỉ giữ text và mảnh tool call; bỏ reasoning/metadata từ provider."""

    role: Literal["assistant"] | None = None
    content: str | None = None
    tool_calls: list[StreamToolCall] = Field(default_factory=list)

    @field_validator("tool_calls", mode="before")
    @classmethod
    def normalize_null_calls(cls, value):
        return [] if value is None else value


class StreamChoice(BaseModel):
    index: int = Field(default=0, ge=0, strict=True)
    delta: ModelDelta
    finish_reason: str | None = None
