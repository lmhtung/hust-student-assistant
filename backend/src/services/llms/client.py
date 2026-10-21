"""HTTP client cho API Chat Completions của vLLM; không gọi model khi import.

complete() phục vụ guardrails; astream() trả chunks nội bộ cho graph. API chỉ
phát câu trả lời qua SSE sau khi agent kiểm tra toàn bộ nội dung. Client không
biết danh tính sinh viên hoặc secrets MCP.
"""

import json
from collections.abc import AsyncIterator, Sequence

import anyio
import httpx
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    convert_to_openai_messages,
)

from src.config import LLMSettings
from src.schemas.llm import ModelMessage
from src.services.llms.stream import stream_chunks


def _message_from_wire(message: ModelMessage) -> AIMessage:
    """Dùng JSON hoàn chỉnh; không sửa hộ arguments bị thiếu dấu đóng."""
    tool_calls = []
    invalid_tool_calls = []
    for call in message.tool_calls:
        try:
            arguments = json.loads(call.function.arguments)
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
        except ValueError:
            # Agent gửi ToolMessage lỗi để model sửa tham số; không thực thi.
            invalid_tool_calls.append(
                {
                    "name": call.function.name,
                    "args": call.function.arguments,
                    "id": call.id,
                    "error": "invalid_tool_arguments",
                    "type": "invalid_tool_call",
                }
            )
        else:
            tool_calls.append(
                {
                    "name": call.function.name,
                    "args": arguments,
                    "id": call.id,
                    "type": "tool_call",
                }
            )
    return AIMessage(
        content=message.content or "", tool_calls=tool_calls, invalid_tool_calls=invalid_tool_calls
    )


def message_from_chunk(chunk: AIMessageChunk) -> AIMessage:
    """Kiểm tra kết quả sau khi graph ghép hết stream bằng chunk + chunk.

    AIMessageChunk tự parse JSON chưa đầy đủ. Vì vậy chỉ đọc raw tool_call_chunks
    và kiểm tra lại schema/JSON hoàn chỉnh trước khi cho phép agent gọi tool.
    """
    message = ModelMessage.model_validate(
        {
            "content": chunk.content,
            "tool_calls": [
                {
                    "id": call.get("id"),
                    "function": {"name": call.get("name"), "arguments": call.get("args")},
                }
                for call in chunk.tool_call_chunks
            ],
        }
    )
    return _message_from_wire(message)


class LLMClient:
    def __init__(self, settings: LLMSettings):
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.api_key.get_secret_value()}"},
            timeout=httpx.Timeout(60, connect=10),
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    def _build_payload(
        self,
        messages: Sequence[BaseMessage],
        tools: list[dict] | None = None,
        *,
        assessment: bool = False,
        stream: bool = False,
    ) -> dict:
        wire_messages = convert_to_openai_messages(messages)
        for message, wire_message in zip(messages, wire_messages, strict=True):
            if isinstance(message, AIMessage) and message.invalid_tool_calls:
                # LangChain converter chỉ gửi tool_calls hợp lệ. Giữ lời gọi sai
                # trong HTTP history để ToolMessage lỗi vẫn khớp tool_call_id.
                calls = wire_message.setdefault("tool_calls", [])
                for call in message.invalid_tool_calls:
                    calls.append(
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["args"]},
                        }
                    )
        payload = {
            "model": self.settings.default_model,
            "messages": wire_messages,
            "stream": stream,
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
        return payload

    async def complete(
        self,
        messages: Sequence[BaseMessage],
        tools: list[dict] | None = None,
        *,
        assessment: bool = False,
    ) -> AIMessage:
        """Lấy trọn câu trả lời; guardrails dùng chế độ này để phân loại JSON."""
        payload = self._build_payload(messages, tools, assessment=assessment)
        response = await self.http.post("chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Model response phải là một object")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("Model response thiếu choices hợp lệ")
        choice = choices[0]
        if choice.get("finish_reason") not in {None, "stop", "tool_calls"}:
            raise ValueError("Model response chưa hoàn tất câu trả lời")
        message = ModelMessage.model_validate(choice.get("message"))
        # Không gắn raw response vào additional_kwargs: reasoning và metadata
        # ngoài schema không được chuyển vào state hoặc SSE.
        return _message_from_wire(message)

    async def astream(
        self, messages: Sequence[BaseMessage], tools: list[dict] | None = None
    ) -> AsyncIterator[AIMessageChunk]:
        """Nhận chunks nội bộ; caller phải ghép hết và kiểm tra trước khi công bố."""
        payload = self._build_payload(messages, tools, stream=True)
        async with self.http.stream("POST", "chat/completions", json=payload) as response:
            try:
                response.raise_for_status()
                async for chunk in stream_chunks(response):
                    yield chunk
            finally:
                # AnyIO timeout hủy lặp lại ở mỗi await. Đóng trong shield để
                # socket được dọn trước khi HTTPX thoát context (lần đóng sau là no-op).
                with anyio.CancelScope(shield=True):
                    await response.aclose()
