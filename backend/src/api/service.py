"""Điều phối chat độc lập HTTP: chọn agent, giữ lease, chạy và lưu kết quả.

Không nhận Request hay Pydantic API model. CLI/job/test có thể gọi cùng service;
HTTP routes và SSE adapter tự chuyển lỗi thành phản hồi tương ứng.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from uuid import UUID, uuid4

import anyio

from src.agents.base_assistant.agent import BaseAssistant
from src.agents.base_assistant.context import AgentContext
from src.agents.base_assistant.events import AgentEvents, StatusEmitter, ignore_status
from src.agents.factory import get_assistant
from src.api.conversations import ConversationStore, History
from src.config import Settings
from src.schemas.agent import AgentResult
from src.schemas.auth import CurrentUser
from src.schemas.events import AgentStreamEvent


@dataclass(frozen=True)
class PreparedTurn:
    agent: BaseAssistant
    context: AgentContext
    lease: str
    history: History
    message: str


class ChatService:
    def __init__(
        self, settings: Settings, conversations: ConversationStore, student_assistant: BaseAssistant
    ):
        self.settings = settings
        self.conversations = conversations
        self.student_assistant = student_assistant
        self.slots = asyncio.Semaphore(settings.app.max_concurrent_requests)

    async def prepare(self, user: CurrentUser, session_id: UUID, message: str) -> PreparedTurn:
        # Router chọn theo role đã xác thực, trước khi mở hội thoại hoặc gọi LLM.
        agent = get_assistant(user, self.student_assistant)
        context = AgentContext(user, uuid4(), session_id)
        lease, history = await self.conversations.acquire(
            session_id, user, self.settings.app.request_timeout
        )
        return PreparedTurn(agent, context, lease, history, message)

    async def run(self, turn: PreparedTurn, emit: StatusEmitter = ignore_status) -> AgentResult:
        try:
            # Bao gồm thời gian chờ slot. anyio hỗ trợ Python 3.10 của môi trường vllm.
            with anyio.fail_after(self.settings.app.request_timeout):
                async with self.slots:
                    result = await turn.agent.run(turn.message, turn.history, turn.context, emit)
                    await self.conversations.commit(
                        turn.context.session_id,
                        turn.lease,
                        turn.history
                        + [
                            {"role": "user", "content": turn.message},
                            {"role": "assistant", "content": result.response},
                        ],
                    )
                    # HTTP adapter chỉ thấy kết quả đã qua guardrails và lưu thành công.
                    return result
        finally:
            await self.release(turn)

    async def astream(
        self, turn: PreparedTurn, *, buffer_size: int = 32
    ) -> AsyncIterator[AgentStreamEvent]:
        """Stream cả lượt chat; chỉ phát kết quả sau khi run() đã commit lịch sử."""
        try:
            events = AgentEvents(turn.context, buffer_size)
            async with aclosing(events.stream(lambda emit: self.run(turn, emit))) as stream:
                async for event in stream:
                    yield event
        finally:
            # Consumer có thể đóng stream trước khi worker kịp gọi run().
            await self.release(turn)

    async def release(self, turn: PreparedTurn) -> None:
        # AnyIO cancel scope có thể hủy lặp lại ở từng await; shield cả scope
        # lẫn asyncio task để disconnect/timeout vẫn nhả lease SQLite.
        with anyio.CancelScope(shield=True):
            await asyncio.shield(self.conversations.release(turn.context.session_id, turn.lease))
