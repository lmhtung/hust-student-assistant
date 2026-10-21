"""Kiểm tra stream ở tầng agent, độc lập HTTP/SSE và không gọi dịch vụ thật."""

import asyncio

import pytest
from pydantic import ValidationError

from src.guardrails.policy import SAFE_CLARIFICATION, SAFE_REFUSAL, SafetyUnavailable
from src.schemas.agent import AgentResult
from src.schemas.events import ResultEvent, StatusEvent
from tests.test_base_agent import (
    FakeSafety,
    FakeTools,
    MinimalAssistant,
    context_for,
    ignore_status,
)


async def collect(stream):
    return [event async for event in stream]


async def test_run_and_stream_return_the_same_checked_result(settings):
    sessions = []

    async def respond(state, context, emit):
        await emit({"stage": "thinking"})
        await emit({"stage": "using_tools"})
        return {**state, "response": "Lớp ET101 đang mở.", "status": "answered"}

    def tools_factory(user):
        tools = FakeTools(respond)
        sessions.append(tools)
        return tools

    safety = FakeSafety()
    agent = MinimalAssistant(None, tools_factory, safety, settings)
    context = context_for()

    result = await agent.run("Tra cứu lớp", [], context, ignore_status)
    events = await collect(agent.astream("Tra cứu lớp", [], context))

    assert isinstance(result, AgentResult)
    assert [event.data.stage for event in events if isinstance(event, StatusEvent)] == [
        "checking_input",
        "thinking",
        "using_tools",
        "checking_output",
    ]
    assert isinstance(events[-1], ResultEvent)
    assert events[-1].data == result
    assert [event.event for event in events] == ["status"] * 4 + ["result"]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert all(event.request_id == context.request_id for event in events)
    assert all(event.session_id == context.session_id for event in events)
    assert all(tools.closed for tools in sessions)
    assert safety.calls[-1] == ("Lớp ET101 đang mở.", "output")


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_response"),
    [
        ("block", "blocked", SAFE_REFUSAL),
        ("clarify", "clarification_required", SAFE_CLARIFICATION),
    ],
)
async def test_denied_input_streams_safe_result_without_opening_tools(
    settings, decision, expected_status, expected_response
):
    async def unexpected_graph(state, context, emit):
        pytest.fail("Input bị từ chối không được chạy graph")

    tools = FakeTools(unexpected_graph)
    agent = MinimalAssistant(
        None, lambda user: tools, FakeSafety(input_decision=decision), settings
    )

    events = await collect(agent.astream("Yêu cầu", [], context_for()))

    assert len(events) == 2
    assert isinstance(events[0], StatusEvent)
    assert events[0].data.stage == "checking_input"
    assert isinstance(events[-1], ResultEvent)
    assert events[-1].data == AgentResult(expected_response, expected_status)
    assert not tools.opened


async def test_stream_never_publishes_draft_rejected_by_output_guardrails(settings):
    draft = "Nội dung bản nháp cần chặn và không được phát ra ngoài."

    async def respond(state, context, emit):
        await emit({"stage": "thinking"})
        return {**state, "response": draft, "status": "answered"}

    tools = FakeTools(respond)
    safety = FakeSafety(output_decision="block")
    agent = MinimalAssistant(None, lambda user: tools, safety, settings)

    events = await collect(agent.astream("Câu hỏi", [], context_for()))

    assert events[-1].data == AgentResult(SAFE_REFUSAL, "blocked")
    assert safety.calls[-1] == (draft, "output")
    assert all(draft not in event.model_dump_json() for event in events)
    assert tools.closed


@pytest.mark.parametrize("failure_phase", ["input", "output"])
async def test_classifier_failure_propagates_without_publishing_result(settings, failure_phase):
    class UnavailableSafety(FakeSafety):
        async def assess(self, text, phase):
            if phase == failure_phase:
                raise SafetyUnavailable("Không kiểm tra được nội dung")
            return await super().assess(text, phase)

    async def respond(state, context, emit):
        return {**state, "response": "Bản nháp chưa được kiểm tra.", "status": "answered"}

    tools = FakeTools(respond)
    agent = MinimalAssistant(None, lambda user: tools, UnavailableSafety(), settings)
    received = []

    with pytest.raises(SafetyUnavailable):
        async for event in agent.astream("Câu hỏi", [], context_for()):
            received.append(event)

    assert received
    assert all(isinstance(event, StatusEvent) for event in received)
    assert tools.opened == (failure_phase == "output")
    assert tools.closed == (failure_phase == "output")


async def test_two_streams_on_one_agent_keep_context_history_and_events_separate(settings):
    both_started = asyncio.Event()
    contexts = {}
    sessions = {}

    def tools_factory(user):
        async def respond(state, context, emit):
            contexts[user.user_id] = context
            if len(contexts) == 2:
                both_started.set()
            await both_started.wait()
            await emit({"stage": "thinking"})
            response = f"{user.user_id}: {state['messages'][1].content}"
            return {**state, "response": response, "status": "answered"}

        tools = FakeTools(respond)
        sessions[user.user_id] = tools
        return tools

    context_a, context_b = context_for("student-a"), context_for("student-b")
    agent = MinimalAssistant(None, tools_factory, FakeSafety(), settings)

    stream_a, stream_b = await asyncio.wait_for(
        asyncio.gather(
            collect(
                agent.astream("Câu hỏi A", [{"role": "user", "content": "history-a"}], context_a)
            ),
            collect(
                agent.astream("Câu hỏi B", [{"role": "user", "content": "history-b"}], context_b)
            ),
        ),
        timeout=5,
    )

    for events, context, expected in [
        (stream_a, context_a, "student-a: history-a"),
        (stream_b, context_b, "student-b: history-b"),
    ]:
        assert events[-1].data.response == expected
        assert all(event.request_id == context.request_id for event in events)
        assert all(event.session_id == context.session_id for event in events)
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert sessions["student-a"] is not sessions["student-b"]
    assert all(tools.closed for tools in sessions.values())


async def test_closing_after_first_event_cancels_graph_and_closes_tools(settings):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def waiting_graph(state, context, emit):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    tools = FakeTools(waiting_graph)
    agent = MinimalAssistant(None, lambda user: tools, FakeSafety(), settings)
    stream = agent.astream("Câu hỏi", [], context_for())

    try:
        first = await asyncio.wait_for(anext(stream), timeout=5)
        assert first.data.stage == "checking_input"
        await asyncio.wait_for(started.wait(), timeout=5)
    finally:
        await asyncio.wait_for(stream.aclose(), timeout=5)

    assert stopped.is_set()
    assert tools.closed


async def test_cancelling_waiting_consumer_closes_its_running_tools(settings):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def waiting_graph(state, context, emit):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    tools = FakeTools(waiting_graph)
    agent = MinimalAssistant(None, lambda user: tools, FakeSafety(), settings)
    consumer = asyncio.create_task(collect(agent.astream("Câu hỏi", [], context_for())))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
    finally:
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(consumer, timeout=5)

    assert stopped.is_set()
    assert tools.closed


async def test_full_status_buffer_applies_backpressure_and_can_be_closed(settings):
    about_to_emit_again = asyncio.Event()
    second_emit_returned = asyncio.Event()
    graph_stopped = asyncio.Event()

    async def verbose_graph(state, context, emit):
        try:
            await emit({"stage": "thinking"})
            about_to_emit_again.set()
            await emit({"stage": "using_tools"})
            second_emit_returned.set()
            return {**state, "response": "Kết quả", "status": "answered"}
        finally:
            graph_stopped.set()

    tools = FakeTools(verbose_graph)
    agent = MinimalAssistant(None, lambda user: tools, FakeSafety(), settings)
    stream = agent.astream("Câu hỏi", [], context_for(), buffer_size=1)

    try:
        first = await asyncio.wait_for(anext(stream), timeout=5)
        assert first.data.stage == "checking_input"
        await asyncio.wait_for(about_to_emit_again.wait(), timeout=5)
        # Chưa lấy event kế tiếp: một status đang đầy buffer nên emit phải chờ.
        assert not second_emit_returned.is_set()
    finally:
        await asyncio.wait_for(stream.aclose(), timeout=5)

    assert graph_stopped.is_set()
    assert tools.closed
    assert not second_emit_returned.is_set()


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_graph_failure_or_self_cancellation_terminates_stream(settings, error_type):
    async def broken_graph(state, context, emit):
        raise error_type("Graph dừng")

    tools = FakeTools(broken_graph)
    agent = MinimalAssistant(None, lambda user: tools, FakeSafety(), settings)

    # Worker tự raise CancelledError cũng phải đánh thức consumer đang đợi event.
    with pytest.raises(error_type):
        await asyncio.wait_for(collect(agent.astream("Câu hỏi", [], context_for())), timeout=5)

    assert tools.closed


@pytest.mark.parametrize(
    "payload",
    [
        {"stage": "thinking", "raw_tool_payload": "private data"},
        {"stage": "unknown_stage"},
    ],
)
async def test_invalid_status_payload_cannot_be_published(settings, payload):
    async def invalid_graph(state, context, emit):
        await emit(payload)
        return {**state, "response": "Không được tới đây", "status": "answered"}

    tools = FakeTools(invalid_graph)
    agent = MinimalAssistant(None, lambda user: tools, FakeSafety(), settings)
    received = []

    with pytest.raises(ValidationError):
        async for event in agent.astream("Câu hỏi", [], context_for()):
            received.append(event)

    assert all(isinstance(event, StatusEvent) for event in received)
    assert all("private data" not in event.model_dump_json() for event in received)
    assert tools.closed


@pytest.mark.parametrize("buffer_size", [0, -1])
async def test_stream_rejects_non_positive_buffer_before_opening_tools(settings, buffer_size):
    async def unexpected_graph(state, context, emit):
        pytest.fail("Buffer không hợp lệ không được mở graph")

    tools = FakeTools(unexpected_graph)
    agent = MinimalAssistant(None, lambda user: tools, FakeSafety(), settings)

    with pytest.raises(ValueError):
        stream = agent.astream("Câu hỏi", [], context_for(), buffer_size=buffer_size)
        await anext(stream)

    assert not tools.opened


def test_event_metadata_and_progress_are_immutable_and_reject_extra_fields():
    context = context_for()
    payload = {
        "request_id": context.request_id,
        "session_id": context.session_id,
        "sequence": 1,
        "data": {"stage": "thinking"},
    }
    event = StatusEvent(**payload)

    with pytest.raises(ValidationError):
        event.sequence = 2
    with pytest.raises(ValidationError):
        event.data.stage = "using_tools"
    with pytest.raises(ValidationError):
        StatusEvent(**{**payload, "sequence": 0})
    with pytest.raises(ValidationError):
        StatusEvent(**payload, raw_state={"private": "data"})
