"""Vòng đời chung của agent; lớp con chỉ định nghĩa prompt và graph riêng.

run() trả kết quả, astream() phát sự kiện từ cùng luồng xử lý. Các phương thức
bắt đầu bằng dấu _ là nội bộ hoặc điểm mở rộng cho lớp con.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from typing import Literal, final

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)

from src.agents.base_assistant.context import AgentContext
from src.agents.base_assistant.events import AgentEvents, StatusEmitter, ignore_status
from src.agents.base_assistant.state import AgentState
from src.config import Settings
from src.guardrails.policy import SAFE_CLARIFICATION, SAFE_REFUSAL, Guardrails
from src.schemas.agent import AgentResult
from src.schemas.events import AgentStreamEvent
from src.services.llms.client import LLMClient, message_from_chunk


class BaseAssistant(ABC):
    def __init__(self, llm: LLMClient, tools_factory, guardrails: Guardrails, settings: Settings):
        # Chỉ giữ dependencies dùng chung. Tools, context và state phải thuộc
        # từng run(), vì một instance agent có thể phục vụ nhiều request cùng lúc.
        self._llm = llm
        self._tools_factory = tools_factory
        self._guardrails = guardrails
        self._settings = settings

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Chỉ dẫn riêng của agent; không chứa danh tính hoặc secrets của request."""

    @abstractmethod
    def _build_graph(self, tools, context: AgentContext, emit: StatusEmitter):
        """Tạo graph riêng cho lượt gọi, trả object có phương thức async ainvoke().

        Graph nhận messages và trả response/status. Context chỉ dành cho code
        nghiệp vụ; không đưa nguyên context vào messages gửi cho model.
        """

    def _initial_state(self, messages: list[AnyMessage]) -> AgentState:
        """Lớp con có thể bổ sung state riêng bằng super()._initial_state(messages)."""
        return {"messages": messages, "response": "", "status": "answered"}

    def _prepare_messages(self, message: str, history: list[dict]) -> list[AnyMessage]:
        """Đổi lịch sử SQLite thành message mới cho lượt chạy, giữ nguyên thứ tự.

        Chỉ system prompt do code tạo được dùng làm SystemMessage. Kho hội thoại
        chỉ lưu user/assistant; kết quả tool và chỉ dẫn không lấy từ client.
        """
        messages: list[AnyMessage] = [SystemMessage(content=self.system_prompt)]
        active_history = history if self._settings.short_memory.enabled else []
        for item in active_history:
            if item["role"] == "user":
                messages.append(HumanMessage(content=item["content"]))
            elif item["role"] == "assistant":
                messages.append(AIMessage(content=item["content"]))
            else:
                raise ValueError("Conversation history only supports user and assistant roles")
        messages.append(HumanMessage(content=message))
        return messages

    async def _generate(
        self, messages: Sequence[BaseMessage], tools: list[dict] | None = None
    ) -> AIMessage:
        """Nhận token/tool fragments rồi ghép thành một message cho graph.

        Chunk chỉ sống trong lượt sinh nội dung; không lưu từng chunk vào state
        và không phát bản nháp trước guardrails. Hàm đóng stream cả khi bị hủy.
        """
        combined: AIMessageChunk | None = None
        async with aclosing(self._llm.astream(messages, tools=tools)) as stream:
            async for chunk in stream:
                combined = chunk if combined is None else combined + chunk
        if combined is None:
            raise ValueError("Model stream did not contain a message")
        # JSON của tool phải được parse nghiêm ngặt sau khi ghép đủ fragments;
        # parser một phần của AIMessageChunk chưa đủ để cho phép gọi tool.
        return message_from_chunk(combined)

    @final
    async def run(
        self,
        message: str,
        history: list[dict],
        context: AgentContext,
        emit: StatusEmitter = ignore_status,
    ) -> AgentResult:
        """Luồng chung: kiểm tra input → chạy graph → kiểm tra output.

        Lớp con mở rộng qua các hook ở trên thay vì override run() và lặp lại
        vòng đời. @final hỗ trợ type checker, không phải cơ chế phân quyền.
        """
        await emit({"stage": "checking_input"})
        denied = await self._check_content(message, "input")
        if denied is not None:
            return denied

        messages = self._prepare_messages(message, history)
        async with self._tools_factory(context.current_user).connect() as tools:
            graph = self._build_graph(tools, context, emit)
            state = await graph.ainvoke(
                self._initial_state(messages),
                {"recursion_limit": 2 * self._settings.agents.max_tool_rounds + 5},
            )

        # Đóng tools trước khi kiểm tra output. Mọi nhánh graph, kể cả limited,
        # đều qua bước này; lớp con không cần tự nhớ gọi guardrails.
        result = AgentResult(state["response"], state["status"])
        await emit({"stage": "checking_output"})
        if not result.response.strip():
            return AgentResult(
                "Mình chưa tạo được câu trả lời. Bạn hãy diễn đạt lại yêu cầu.", "limited"
            )
        denied = await self._check_content(result.response, "output")
        return denied if denied is not None else result

    @final
    async def astream(
        self,
        message: str,
        history: list[dict],
        context: AgentContext,
        *,
        buffer_size: int = 32,
    ) -> AsyncIterator[AgentStreamEvent]:
        """Phát status và result đã kiểm tra; không phụ thuộc HTTP hoặc SSE.

        Khi consumer nhận chậm, queue giới hạn làm producer chờ. Đóng iterator
        sẽ hủy lượt chạy và chờ tools được dọn; mỗi lượt có bộ đệm riêng.
        """
        events = AgentEvents(context, buffer_size)
        async with aclosing(
            events.stream(lambda emit: self.run(message, history, context, emit))
        ) as stream:
            async for event in stream:
                yield event

    async def _check_content(
        self, text: str, phase: Literal["input", "output"]
    ) -> AgentResult | None:
        """Dùng cùng cách xử lý block/clarify ở cả hai phía của graph."""
        assessment = await self._guardrails.assess(text, phase)
        if assessment.decision == "block":
            return AgentResult(SAFE_REFUSAL, "blocked")
        if assessment.decision == "clarify":
            return AgentResult(SAFE_CLARIFICATION, "clarification_required")
        return None
