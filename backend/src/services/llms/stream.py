"""Đọc SSE từ model server; chỉ trả chunks nếu cấu trúc delta hợp lệ."""

import json
from collections.abc import AsyncIterator

import httpx
from langchain_core.messages import AIMessageChunk

from src.schemas.llm import StreamChoice


async def _sse_data(response: httpx.Response) -> AsyncIterator[str]:
    """Ghép các dòng data của một event; httpx xử lý CRLF và UTF-8 bị chia nhỏ."""
    data_lines = []
    async for line in response.aiter_lines():
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data_lines.append(value.removeprefix(" "))
    # Không dispatch event thiếu dòng trống kết thúc. Parser bên dưới sẽ từ
    # chối stream thiếu [DONE], thay vì chấp nhận dữ liệu bị cắt giữa chừng.


async def stream_chunks(response: httpx.Response) -> AsyncIterator[AIMessageChunk]:
    finished = False
    async for payload in _sse_data(response):
        if payload.strip() == "[DONE]":
            if not finished:
                raise ValueError("Model stream thiếu finish_reason")
            return

        data = json.loads(payload)
        if not isinstance(data, dict) or "error" in data:
            raise ValueError("Model stream trả error hoặc envelope không hợp lệ")
        choices = data.get("choices")
        if finished and choices == [] and isinstance(data.get("usage"), dict):
            # Một số server gửi thống kê token giữa finish và [DONE].
            continue
        if not isinstance(choices, list) or len(choices) != 1 or finished:
            raise ValueError("Model stream thiếu choice hoặc có dữ liệu sau finish")
        choice = StreamChoice.model_validate(choices[0])
        if choice.index != 0:
            raise ValueError("Model stream chỉ hỗ trợ một completion")
        if choice.finish_reason is not None:
            if choice.finish_reason not in {"stop", "tool_calls"}:
                raise ValueError("Model stream chưa hoàn tất câu trả lời")
            finished = True

        tool_chunks = []
        for call in choice.delta.tool_calls:
            function = call.function
            tool_chunks.append(
                {
                    "index": call.index,
                    "id": call.id,
                    "name": function.name if function is not None else None,
                    "args": function.arguments if function is not None else None,
                    "type": "tool_call_chunk",
                }
            )
        # Không đưa provider ID, usage, reasoning hoặc raw delta vào chunk.
        yield AIMessageChunk(content=choice.delta.content or "", tool_call_chunks=tool_chunks)

    raise ValueError("Model stream thiếu event [DONE]")
