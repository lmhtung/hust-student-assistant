"""Chuyển kết quả chat thành sự kiện SSE và dừng tác vụ khi client ngắt kết nối.

Chỉ gửi answer hoàn chỉnh đã qua guardrails; status không chứa token từ LLM.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress

import anyio

from src.api.service import ChatService, PreparedTurn
from src.schemas.chat import ChatResponse

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 10


async def stream_events(service: ChatService, turn: PreparedTurn) -> AsyncIterator[str]:
    # Giới hạn bộ nhớ: producer chờ nếu client nhận chậm.
    queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=32)

    async def emit(data: dict) -> None:
        await queue.put(("status", data))

    async def produce() -> None:
        ok = False
        try:
            result = await service.run(turn, emit)
            answer = ChatResponse(
                response_id=turn.context.request_id,
                session_id=turn.context.session_id,
                response=result.response,
                status=result.status,
            )
            await queue.put(("answer", answer.model_dump(mode="json")))
            ok = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "stream_failed request_id=%s kind=%s",
                turn.context.request_id,
                type(exc).__name__,
            )
            await queue.put(
                (
                    "error",
                    {
                        "code": "timeout"
                        if isinstance(exc, TimeoutError)
                        else "assistant_unavailable",
                        "request_id": str(turn.context.request_id),
                    },
                )
            )
        await queue.put(("done", {"ok": ok, "session_id": str(turn.context.session_id)}))

    task = asyncio.create_task(produce())
    sequence = 0
    try:
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                # Python 3.10 dùng asyncio.TimeoutError tại wait_for.
                yield ": ping\n\n"
                continue
            sequence += 1
            payload = json.dumps(data, ensure_ascii=False)
            yield f"id: {turn.context.request_id}:{sequence}\nevent: {event}\ndata: {payload}\n\n"
            if event == "done":
                break
    finally:
        # Starlette hủy iterator khi disconnect. Shield để việc nhả lease
        # vẫn hoàn tất, kể cả khi producer chưa kịp chạy lần đầu.
        task.cancel()
        with anyio.CancelScope(shield=True):
            with suppress(asyncio.CancelledError):
                await asyncio.shield(task)
            await service.release(turn)
