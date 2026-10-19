"""HTTP client cho API Chat Completions của vLLM; không gọi model khi import.

Không stream token thô: agent phải kiểm tra toàn bộ câu trả lời trước khi API
phát nó bằng SSE. Client này không biết danh tính sinh viên hoặc secrets MCP.
"""

from typing import Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from src.config import LLMSettings


class ToolFunction(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    arguments: str


class ToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    type: Literal["function"] = "function"
    function: ToolFunction


class ModelMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @field_validator("tool_calls", mode="before")
    @classmethod
    def normalize_null_calls(cls, value):
        # Nhiều model server trả tool_calls: null khi chỉ trả văn bản.
        return [] if value is None else value


class LLMClient:
    def __init__(self, settings: LLMSettings):
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=settings.api_base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.api_key.get_secret_value()}"},
            timeout=httpx.Timeout(60, connect=10),
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def complete(
        self, messages: list[dict], tools: list[dict] | None = None, *, assessment: bool = False
    ) -> ModelMessage:
        payload = {
            "model": self.settings.model_name,
            "messages": messages,
            "stream": False,
            "temperature": 0 if assessment else self.settings.temperature,
            "max_tokens": min(512, self.settings.max_tokens)
            if assessment
            else self.settings.max_tokens,
        }
        if assessment:
            payload["response_format"] = {"type": "json_object"}
        elif tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.settings.send_chat_template_kwargs:
            payload["chat_template_kwargs"] = {"enable_thinking": self.settings.enable_thinking}
        response = await self.http.post("chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Model response phải là một object")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("Model response thiếu choices hợp lệ")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise ValueError("Model response bị cắt vì giới hạn token")
        # Các field phụ, gồm reasoning_content, không được chuyển vào graph/SSE.
        return ModelMessage.model_validate(choice.get("message"))
