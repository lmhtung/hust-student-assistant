"""Chuyển sự kiện chat thành SSE; heartbeat không hủy lượt xử lý đang chạy.

Chỉ gửi answer hoàn chỉnh đã qua guardrails và commit; không phát token/bản nháp.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress

import anyio

from src.api.service import ChatService, PreparedTurn
from src.schemas.chat import ChatResponse
from src.schemas.events import ResultEvent, StatusEvent

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 10


async def stream_events(service: ChatService, turn: PreparedTurn) -> AsyncIterator[str]:
    stream = service.astream(turn)
    pending = None
    sequence = 0

    def encode(event: str, data: dict) -> str:
        nonlocal sequence
        sequence += 1
        payload = json.dumps(data, ensure_ascii=False)
        return f"id: {turn.context.request_id}:{sequence}\nevent: {event}\ndata: {payload}\n\n"

    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(stream))
            ready, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS)
            if not ready:
                # Giữ nguyên pending: wait_for(anext(...)) sẽ hủy agent khi timeout.
                yield ": ping\n\n"
                continue

            completed, pending = pending, None
            event = completed.result()
            if isinstance(event, StatusEvent):
                yield encode("status", event.data.model_dump(mode="json"))
            elif isinstance(event, ResultEvent):
                answer = ChatResponse(
                    response_id=turn.context.request_id,
                    session_id=turn.context.session_id,
                    response=event.data.response,
                    status=event.data.status,
                )
                yield encode("answer", answer.model_dump(mode="json"))
                yield encode("done", {"ok": True, "session_id": str(turn.context.session_id)})
                return
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception as exc:
        logger.warning(
            "stream_failed request_id=%s kind=%s",
            turn.context.request_id,
            type(exc).__name__,
        )
        yield encode(
            "error",
            {
                "code": "timeout" if isinstance(exc, TimeoutError) else "assistant_unavailable",
                "request_id": str(turn.context.request_id),
            },
        )
        yield encode("done", {"ok": False, "session_id": str(turn.context.session_id)})
    finally:
        # Đợi anext dừng hẳn trước aclose(), rồi nhả lease kể cả worker chưa chạy.
        # Không giữ cancel scope qua yield; scope này chỉ bao quanh cleanup.
        with anyio.CancelScope(shield=True):
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(pending)
            try:
                await asyncio.shield(stream.aclose())
            finally:
                await service.release(turn)
