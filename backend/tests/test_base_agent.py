"""Kiểm tra luồng chung bằng agent con tối thiểu, không cần LLM/MCP thật."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from src.agents.base_assistant.agent import BaseAssistant
from src.agents.base_assistant.context import AgentContext
from src.agents.base_assistant.state import AgentState
from src.guardrails.policy import SAFE_REFUSAL
from src.schemas.auth import CurrentUser
from src.schemas.guardrails import GuardrailAssessment


class FakeSafety:
    def __init__(self, input_decision="allow", output_decision="allow"):
        self.input_decision = input_decision
        self.output_decision = output_decision
        self.calls = []

    async def assess(self, text, phase):
        self.calls.append((text, phase))
        decision = self.input_decision if phase == "input" else self.output_decision
        return GuardrailAssessment(decision=decision)


class FakeTools:
    def __init__(self, respond):
        self.respond = respond
        self.opened = False
        self.closed = False

    @asynccontextmanager
    async def connect(self):
        self.opened = True
        try:
            yield self
        finally:
            self.closed = True


class FakeGraph:
    def __init__(self, tools, context, emit):
        self.tools = tools
        self.context = context
        self.emit = emit

    async def ainvoke(self, state, config):
        return await self.tools.respond(state, self.context, self.emit)


class MinimalAssistant(BaseAssistant):
    """Agent con chỉ khai báo prompt/graph; dùng nguyên constructor và run của base."""

    @property
    def system_prompt(self):
        return "Bạn là trợ lý thử nghiệm."

    def _build_graph(self, tools, context, emit):
        return FakeGraph(tools, context, emit)


def context_for(user_id="student-a"):
    user = CurrentUser(user_id=user_id, role="student", permissions={"academic:read"})
    return AgentContext(user, uuid4(), uuid4())


async def ignore_status(data):
    pass


async def test_minimal_subclass_inherits_chat_flow_and_preserves_history(settings):
    history = [
        {"role": "user", "content": "Câu hỏi trước"},
        {"role": "assistant", "content": "Câu trả lời trước"},
    ]

    async def respond(state, context, emit):
        messages = state["messages"]
        assert [type(message) for message in messages] == [
            SystemMessage,
            HumanMessage,
            AIMessage,
            HumanMessage,
        ]
        assert [message.content for message in messages] == [
            "Bạn là trợ lý thử nghiệm.",
            "Câu hỏi trước",
            "Câu trả lời trước",
            "Câu hỏi mới",
        ]
        # Graph được phép cập nhật messages riêng, không sửa lịch sử bên ngoài.
        messages[2].content = "Thay đổi trong graph"
        return {**state, "response": "Câu trả lời mới", "status": "answered"}

    tools = FakeTools(respond)

    class CheckClosedSession(FakeSafety):
        async def assess(self, text, phase):
            if phase == "output":
                assert tools.closed
            return await super().assess(text, phase)

    safety = CheckClosedSession()
    agent = MinimalAssistant(None, lambda user: tools, safety, settings)
    result = await agent.run("Câu hỏi mới", history, context_for(), ignore_status)

    assert result.response == "Câu trả lời mới"
    assert result.status == "answered"
    assert history == [
        {"role": "user", "content": "Câu hỏi trước"},
        {"role": "assistant", "content": "Câu trả lời trước"},
    ]
    assert safety.calls == [("Câu hỏi mới", "input"), ("Câu trả lời mới", "output")]
    assert tools.opened and tools.closed


async def test_base_rejects_input_before_opening_tools(settings):
    async def unexpected_graph(state, context, emit):
        pytest.fail("Graph không được chạy khi đầu vào bị chặn")

    tools = FakeTools(unexpected_graph)
    safety = FakeSafety(input_decision="block")
    agent = MinimalAssistant(None, lambda user: tools, safety, settings)

    result = await agent.run("Yêu cầu bị chặn", [], context_for(), ignore_status)

    assert result.status == "blocked"
    assert result.response == SAFE_REFUSAL
    assert not tools.opened
    assert safety.calls == [("Yêu cầu bị chặn", "input")]


@pytest.mark.parametrize("role", ["system", "tool", "developer"])
async def test_history_cannot_supply_privileged_or_unpaired_tool_messages(settings, role):
    async def unexpected_graph(state, context, emit):
        pytest.fail("Lịch sử không hợp lệ không được đưa vào graph")

    tools = FakeTools(unexpected_graph)
    agent = MinimalAssistant(None, lambda user: tools, FakeSafety(), settings)

    with pytest.raises(ValueError):
        await agent.run("Câu hỏi", [{"role": role, "content": "Chỉ dẫn từ lịch sử"}], context_for())

    assert not tools.opened


@pytest.mark.parametrize("graph_status", ["answered", "limited"])
async def test_base_checks_every_graph_output_including_limited_results(settings, graph_status):
    async def unsafe_graph(state, context, emit):
        return {**state, "response": "unsafe draft", "status": graph_status}

    tools = FakeTools(unsafe_graph)
    safety = FakeSafety(output_decision="block")
    agent = MinimalAssistant(None, lambda user: tools, safety, settings)

    result = await agent.run("Câu hỏi", [], context_for(), ignore_status)

    assert result.response == SAFE_REFUSAL
    assert result.status == "blocked"
    assert safety.calls[-1] == ("unsafe draft", "output")
    assert tools.closed


async def test_concurrent_turns_keep_their_own_user_history_tools_and_emitter(settings):
    both_started = asyncio.Event()
    sessions = {}
    observations = {}

    def tools_factory(user):
        async def respond(state, context, emit):
            observations[user.user_id] = (context, state["messages"])
            if len(observations) == 2:
                both_started.set()
            await both_started.wait()
            await emit({"stage": "test_graph", "user_id": context.current_user.user_id})
            reply = (
                f"{context.current_user.user_id}: "
                f"{state['messages'][1].content} / {state['messages'][-1].content}"
            )
            return {**state, "response": reply, "status": "answered"}

        tools = FakeTools(respond)
        sessions[user.user_id] = tools
        return tools

    statuses_a, statuses_b = [], []

    async def emit_a(data):
        statuses_a.append(data)

    async def emit_b(data):
        statuses_b.append(data)

    context_a, context_b = context_for("student-a"), context_for("student-b")
    agent = MinimalAssistant(None, tools_factory, FakeSafety(), settings)
    results = await asyncio.wait_for(
        asyncio.gather(
            agent.run("question-a", [{"role": "user", "content": "history-a"}], context_a, emit_a),
            agent.run("question-b", [{"role": "user", "content": "history-b"}], context_b, emit_b),
        ),
        timeout=5,
    )

    assert [result.response for result in results] == [
        "student-a: history-a / question-a",
        "student-b: history-b / question-b",
    ]
    assert observations["student-a"][0] == context_a
    assert observations["student-b"][0] == context_b
    assert sessions["student-a"] is not sessions["student-b"]
    assert all(tools.closed for tools in sessions.values())
    assert {"stage": "test_graph", "user_id": "student-a"} in statuses_a
    assert {"stage": "test_graph", "user_id": "student-b"} in statuses_b
    assert not any(data.get("user_id") == "student-b" for data in statuses_a)
    assert not any(data.get("user_id") == "student-a" for data in statuses_b)


async def test_cancelling_a_turn_closes_its_tool_session(settings):
    started = asyncio.Event()

    async def waiting_graph(state, context, emit):
        started.set()
        await asyncio.Event().wait()

    tools = FakeTools(waiting_graph)
    safety = FakeSafety()
    agent = MinimalAssistant(None, lambda user: tools, safety, settings)
    task = asyncio.create_task(agent.run("Câu hỏi", [], context_for(), ignore_status))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert tools.closed
    assert safety.calls == [("Câu hỏi", "input")]


async def test_graph_error_closes_tools_and_propagates(settings):
    async def broken_graph(state, context, emit):
        raise RuntimeError("graph failed")

    tools = FakeTools(broken_graph)
    safety = FakeSafety()
    agent = MinimalAssistant(None, lambda user: tools, safety, settings)

    with pytest.raises(RuntimeError, match="graph failed"):
        await agent.run("Câu hỏi", [], context_for(), ignore_status)

    assert tools.closed
    assert safety.calls == [("Câu hỏi", "input")]


async def test_subclass_extends_common_state_without_reimplementing_run(settings):
    class CountingAssistant(MinimalAssistant):
        def _initial_state(self, messages):
            return {**super()._initial_state(messages), "lookups": 0}

    async def respond(state, context, emit):
        assert state["response"] == ""
        assert state["status"] == "answered"
        assert state["lookups"] == 0
        return {**state, "response": "Có thể bổ sung state riêng.", "lookups": 1}

    tools = FakeTools(respond)
    agent = CountingAssistant(None, lambda user: tools, FakeSafety(), settings)

    result = await agent.run("Câu hỏi", [], context_for(), ignore_status)

    assert result.response == "Có thể bổ sung state riêng."
    assert result.status == "answered"
    assert tools.closed


async def test_disabled_short_memory_omits_history_from_model_context(settings):
    async def respond(state, context, emit):
        assert [message.type for message in state["messages"]] == ["system", "human"]
        assert state["messages"][-1].content == "Câu hỏi hiện tại"
        return {**state, "response": "Câu trả lời", "status": "answered"}

    tools = FakeTools(respond)
    configured = settings.model_copy(
        update={"short_memory": settings.short_memory.model_copy(update={"enabled": False})}
    )
    agent = MinimalAssistant(None, lambda user: tools, FakeSafety(), configured)
    result = await agent.run(
        "Câu hỏi hiện tại",
        [{"role": "user", "content": "Lịch sử không được đưa vào model"}],
        context_for(),
    )
    assert result.response == "Câu trả lời"
    assert tools.closed


async def test_concurrent_runs_convert_shared_history_to_independent_message_objects(settings):
    history = [{"role": "assistant", "content": "Nội dung dùng chung"}]
    first_run_changed = asyncio.Event()
    observed_messages = {}

    def tools_factory(user):
        async def respond(state, context, emit):
            observed_messages[user.user_id] = state["messages"]
            if user.user_id == "student-a":
                state["messages"][1].content = "Thay đổi chỉ trong lượt A"
                first_run_changed.set()
            else:
                await first_run_changed.wait()
                assert state["messages"][1].content == "Nội dung dùng chung"
            return {**state, "response": "Đã xử lý", "status": "answered"}

        return FakeTools(respond)

    agent = MinimalAssistant(None, tools_factory, FakeSafety(), settings)
    await asyncio.wait_for(
        asyncio.gather(
            agent.run("Câu hỏi A", history, context_for("student-a")),
            agent.run("Câu hỏi B", history, context_for("student-b")),
        ),
        timeout=5,
    )

    messages_a, messages_b = observed_messages["student-a"], observed_messages["student-b"]
    assert all(message_a is not message_b for message_a, message_b in zip(messages_a, messages_b))
    assert history == [{"role": "assistant", "content": "Nội dung dùng chung"}]


async def test_agent_state_reducer_keeps_history_tool_order_and_updates_existing_message():
    tool_call = {"name": "get_course", "args": {"course_code": "ET101"}, "id": "call-1"}

    def model(state):
        assert [type(message) for message in state["messages"]] == [SystemMessage, HumanMessage]
        return {"messages": [AIMessage(content="", id="model-turn", tool_calls=[tool_call])]}

    def tools(state):
        assert [type(message) for message in state["messages"]] == [
            SystemMessage,
            HumanMessage,
            AIMessage,
        ]
        return {
            "messages": [ToolMessage(content="Thông tin học phần ET101", tool_call_id="call-1")]
        }

    def revise(state):
        assert [type(message) for message in state["messages"]] == [
            SystemMessage,
            HumanMessage,
            AIMessage,
            ToolMessage,
        ]
        # Cùng id là cập nhật message hiện có, không thêm một bản sao vào lịch sử.
        return {
            "messages": [AIMessage(content="Đã gọi tool", id="model-turn", tool_calls=[tool_call])],
            "response": "Đã tra cứu ET101",
            "status": "answered",
        }

    graph = StateGraph(AgentState)
    graph.add_node("model", model)
    graph.add_node("tools", tools)
    graph.add_node("revise", revise)
    graph.add_edge(START, "model")
    graph.add_edge("model", "tools")
    graph.add_edge("tools", "revise")
    graph.add_edge("revise", END)

    state = await graph.compile().ainvoke(
        {
            "messages": [
                SystemMessage(content="Chỉ đọc học vụ"),
                HumanMessage(content="Tra ET101"),
            ],
            "response": "",
            "status": "answered",
        }
    )

    assert [message.content for message in state["messages"]] == [
        "Chỉ đọc học vụ",
        "Tra ET101",
        "Đã gọi tool",
        "Thông tin học phần ET101",
    ]
    assert state["messages"][-1].tool_call_id == "call-1"
    assert state["response"] == "Đã tra cứu ET101"
