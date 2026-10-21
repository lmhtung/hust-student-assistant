"""Bộ đệm sự kiện cho một lượt chạy; độc lập với FastAPI và định dạng SSE.

Worker chạy toàn bộ nghiệp vụ trong một task. Consumer chỉ nhận status đã kiểm
tra cấu trúc và kết quả cuối, không nhận state graph, token thô hay tool payload.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress

import anyio

from src.agents.base_assistant.context import AgentContext
from src.schemas.agent import AgentResult
from src.schemas.events import AgentProgress, AgentStreamEvent, ResultEvent, StatusEvent

StatusEmitter = Callable[[dict], Awaitable[None]]
RunCallback = Callable[[StatusEmitter], Awaitable[AgentResult]]


async def ignore_status(data: dict) -> None:
    """Lượt gọi chỉ cần kết quả cuối không phải tự tạo callback rỗng."""


class AgentEvents:
    """Mỗi instance dùng cho đúng một stream, không lưu trên singleton agent."""

    def __init__(self, context: AgentContext, buffer_size: int = 32):
        if buffer_size < 1:
            raise ValueError("buffer_size must be positive")
        self._request_id = context.request_id
        self._session_id = context.session_id
        self._queue: asyncio.Queue[StatusEvent] = asyncio.Queue(maxsize=buffer_size)
        self._sequence = 0
        self._started = False

    async def emit(self, data: dict) -> None:
        """Chỉ nhận stage do code phát; từ chối field thừa chứa dữ liệu nội bộ."""
        progress = AgentProgress.model_validate(data)
        self._sequence += 1
        await self._queue.put(
            StatusEvent(
                request_id=self._request_id,
                session_id=self._session_id,
                sequence=self._sequence,
                data=progress,
            )
        )

    async def stream(self, run: RunCallback) -> AsyncIterator[AgentStreamEvent]:
        """Phát status ngay khi có, rồi một result nếu worker thành công.

        Kết quả/lỗi nằm trên task, không put terminal vào queue: consumer ngắt
        khi queue đầy vẫn có thể hủy worker và đóng tools mà không chờ queue.
        Lỗi được truyền cho caller; HTTP adapter chịu trách nhiệm che thông tin.
        """
        if self._started:
            raise RuntimeError("An event stream cannot be reused")
        self._started = True
        producer = asyncio.create_task(run(self.emit))
        pending: asyncio.Task | None = None
        try:
            while True:
                # Giữ thứ tự: trả hết status trước khi đọc kết quả/lỗi của worker.
                if not self._queue.empty():
                    yield self._queue.get_nowait()
                    continue
                if producer.done():
                    result = producer.result()
                    self._sequence += 1
                    yield ResultEvent(
                        request_id=self._request_id,
                        session_id=self._session_id,
                        sequence=self._sequence,
                        data=result,
                    )
                    return

                # Chờ cả task và queue để lỗi/cancellation của worker không làm
                # consumer kẹt ở queue.get() khi chẳng còn event nào được phát.
                pending = asyncio.create_task(self._queue.get())
                await asyncio.wait((producer, pending), return_when=asyncio.FIRST_COMPLETED)
                if pending.done():
                    yield pending.result()
                else:
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending
                pending = None
        finally:
            producer.cancel()
            if pending is not None:
                pending.cancel()
            # MCP mở/chạy/đóng trong producer; chờ nó đóng trước khi kết thúc.
            with anyio.CancelScope(shield=True):
                for task in (pending, producer):
                    if task is not None:
                        with suppress(Exception, asyncio.CancelledError):
                            await asyncio.shield(task)
