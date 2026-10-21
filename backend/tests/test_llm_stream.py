"""Kiểm tra HTTP streaming thật bằng byte stream giả, không gọi model server.

Event/barrier giúp kiểm tra dữ liệu được đọc dần và tài nguyên được đóng khi
ngắt giữa chừng, thay vì dựa vào sleep hoặc nối chuỗi sau complete().
"""

import asyncio
import json
from contextlib import asynccontextmanager

import anyio
import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

from src.services.llms.client import LLMClient, message_from_chunk


def delta_event(delta=None, finish_reason=None):
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}]}


def sse(data):
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"data: {payload}\n\n".encode()


def fragment(index, *, call_id=None, name=None, arguments=None):
    call = {"index": index}
    if call_id is not None:
        call["id"] = call_id
        call["type"] = "function"
    function = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    if function:
        call["function"] = function
    return call


class TrackedStream(httpx.AsyncByteStream):
    """Byte stream có thể dừng trước một phần dữ liệu và theo dõi aclose()."""

    def __init__(self, parts, *, pause_before=None):
        self.parts = parts
        self.pause_before = pause_before
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()
        self.exhausted = False
        self.closed = False

    async def __aiter__(self):
        for index, part in enumerate(self.parts):
            if index == self.pause_before:
                self.paused.set()
                await self.resume.wait()
            yield part
        self.exhausted = True

    async def aclose(self):
        self.closed = True


@asynccontextmanager
async def mock_streaming_client(settings, stream):
    requests = []

    async def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)

    client = LLMClient(settings.llm)
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://llm/v1/"
    )
    try:
        yield client, requests
    finally:
        await client.aclose()


async def chunks_from(client, messages=None, tools=None):
    return [chunk async for chunk in client.astream(messages or [HumanMessage("Câu hỏi")], tools)]


def assemble(chunks):
    assert chunks
    assert all(isinstance(chunk, AIMessageChunk) for chunk in chunks)
    combined = chunks[0]
    for chunk in chunks[1:]:
        combined = combined + chunk
    return message_from_chunk(combined)


async def test_first_chunk_arrives_before_remaining_http_response_is_available(settings):
    stream = TrackedStream(
        [
            sse(delta_event({"content": "Xin "})),
            sse(delta_event({"content": "chào bạn"})),
            sse(delta_event(finish_reason="stop")),
            sse("[DONE]"),
        ],
        pause_before=1,
    )
    tools = [
        {"type": "function", "function": {"name": "get_course", "parameters": {"type": "object"}}}
    ]

    async with mock_streaming_client(settings, stream) as (client, requests):
        iterator = client.astream([SystemMessage("Tiếng Việt."), HumanMessage("Xin chào")], tools)
        try:
            # Nếu client đọc hết response rồi mới yield, phần này sẽ timeout.
            first = await asyncio.wait_for(anext(iterator), timeout=5)
            assert isinstance(first, AIMessageChunk)
            assert first.content == "Xin "
            assert not stream.exhausted

            async def remaining():
                return [chunk async for chunk in iterator]

            rest_task = asyncio.create_task(remaining())
            await asyncio.wait_for(stream.paused.wait(), timeout=5)
            assert not rest_task.done()
            stream.resume.set()
            rest = await asyncio.wait_for(rest_task, timeout=5)
        finally:
            stream.resume.set()
            await iterator.aclose()

    result = assemble([first, *rest])
    assert isinstance(result, AIMessage)
    assert result.content == "Xin chào bạn"
    assert requests[0]["stream"] is True
    assert requests[0]["messages"] == [
        {"role": "system", "content": "Tiếng Việt."},
        {"role": "user", "content": "Xin chào"},
    ]
    assert requests[0]["tools"] == tools
    assert requests[0]["tool_choice"] == "auto"
    assert len(requests) == 1
    assert stream.closed


async def test_interleaved_tool_fragments_assemble_by_index_with_complete_ids(settings):
    deltas = [
        {"content": "Mình sẽ tra cứu. "},
        {
            "tool_calls": [
                fragment(0, call_id="course-call", name="get_course", arguments='{"course_'),
                fragment(
                    1, call_id="classes-call", name="list_open_classes", arguments='{"semester":'
                ),
            ]
        },
        {"tool_calls": [fragment(1, arguments='"2026-1"}')]},
        {"tool_calls": [fragment(0, arguments='code":"ET101"}')]},
    ]
    stream = TrackedStream(
        [
            *(sse(delta_event(delta)) for delta in deltas),
            sse(delta_event(finish_reason="tool_calls")),
            sse("[DONE]"),
        ]
    )

    async with mock_streaming_client(settings, stream) as (client, _):
        chunks = await chunks_from(client)

    assert any(chunk.tool_call_chunks for chunk in chunks)
    message = assemble(chunks)
    assert message.content == "Mình sẽ tra cứu. "
    assert message.invalid_tool_calls == []
    assert message.tool_calls == [
        {
            "name": "get_course",
            "args": {"course_code": "ET101"},
            "id": "course-call",
            "type": "tool_call",
        },
        {
            "name": "list_open_classes",
            "args": {"semester": "2026-1"},
            "id": "classes-call",
            "type": "tool_call",
        },
    ]
    assert stream.closed


@pytest.mark.parametrize("arguments", ['{"course_code":"ET101"', "not-json", "[]", "null"])
async def test_tool_arguments_use_strict_json_after_chunk_aggregation(settings, arguments):
    stream = TrackedStream(
        [
            sse(
                delta_event(
                    {
                        "tool_calls": [
                            fragment(0, call_id="bad-call", name="get_course", arguments=arguments)
                        ]
                    }
                )
            ),
            sse(delta_event(finish_reason="tool_calls")),
            sse("[DONE]"),
        ]
    )

    async with mock_streaming_client(settings, stream) as (client, requests):
        message = assemble(await chunks_from(client))

    # LangChain có thể đoán object từ JSON thiếu dấu đóng. Finalizer phải dùng
    # chuỗi arguments gốc, để tool sai không được phép thực thi.
    assert message.tool_calls == []
    assert message.invalid_tool_calls == [
        {
            "name": "get_course",
            "args": arguments,
            "id": "bad-call",
            "error": "invalid_tool_arguments",
            "type": "invalid_tool_call",
        }
    ]
    assert len(requests) == 1
    assert stream.closed


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        [],
        {"choices": None},
        {"choices": [None]},
        {"choices": [{"index": 0, "delta": None, "finish_reason": None}]},
        delta_event({"content": ["non-text"]}),
        delta_event({"role": "user", "content": "wrong role"}),
        delta_event(
            {"tool_calls": [fragment(-1, call_id="bad", name="get_course", arguments="{}")]}
        ),
        delta_event(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "bad",
                        "function": {"name": "get_course", "arguments": {"code": "ET101"}},
                    }
                ]
            }
        ),
        {"error": {"message": "Provider failed"}},
    ],
)
async def test_bad_stream_envelope_is_rejected_without_non_streaming_fallback(settings, body):
    stream = TrackedStream([sse(body), sse(delta_event(finish_reason="stop")), sse("[DONE]")])

    async with mock_streaming_client(settings, stream) as (client, requests):
        with pytest.raises(ValueError):
            await chunks_from(client)

    assert len(requests) == 1
    assert requests[0]["stream"] is True
    assert stream.closed


@pytest.mark.parametrize(
    "ending",
    [
        [],
        [sse("[DONE]")],
        [sse(delta_event(finish_reason="stop"))],
    ],
)
async def test_premature_eof_or_missing_finish_marker_rejects_partial_answer(settings, ending):
    stream = TrackedStream([sse(delta_event({"content": "Bản nháp chưa hoàn tất"})), *ending])

    async with mock_streaming_client(settings, stream) as (client, _):
        with pytest.raises(ValueError):
            await chunks_from(client)

    assert stream.closed


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
async def test_truncated_or_filtered_finish_does_not_become_a_complete_message(
    settings, finish_reason
):
    stream = TrackedStream(
        [
            sse(delta_event({"content": "Bản nháp"})),
            sse(delta_event(finish_reason=finish_reason)),
            sse("[DONE]"),
        ]
    )

    async with mock_streaming_client(settings, stream) as (client, _):
        with pytest.raises(ValueError):
            await chunks_from(client)

    assert stream.closed


async def test_reasoning_provider_metadata_and_usage_never_enter_message_chunks(settings):
    first = delta_event({"role": "assistant", "reasoning_content": "private reasoning"})
    first.update({"id": "private-provider-id", "response_metadata": {"secret": "private metadata"}})
    stream = TrackedStream(
        [
            sse(first),
            sse(
                delta_event(
                    {"content": "Câu trả lời công khai", "reasoning_content": "private analysis"}
                )
            ),
            sse(delta_event(finish_reason="stop")),
            sse({"choices": [], "usage": {"completion_tokens": 12}}),
            sse("[DONE]"),
        ]
    )

    async with mock_streaming_client(settings, stream) as (client, _):
        chunks = await chunks_from(client)

    message = assemble(chunks)
    assert message.content == "Câu trả lời công khai"
    for chunk in [*chunks, message]:
        assert chunk.additional_kwargs == {}
        assert chunk.response_metadata == {}
        assert chunk.id is None
        assert "private" not in str(chunk.model_dump())
    assert stream.closed


async def test_sse_comments_crlf_and_utf8_byte_boundaries_are_supported(settings):
    wire = (
        b": heartbeat\r\n\r\n"
        + b"event: message\r\n"
        + sse(delta_event({"content": "Điện tử viễn thông"})).replace(b"\n", b"\r\n")
    )
    wire += sse(delta_event(finish_reason="stop")) + sse("[DONE]")
    # Chia từng byte để ký tự tiếng Việt và cả dấu CRLF đi qua các ranh giới.
    stream = TrackedStream([bytes([byte]) for byte in wire])

    async with mock_streaming_client(settings, stream) as (client, _):
        message = assemble(await chunks_from(client))

    assert message.content == "Điện tử viễn thông"
    assert stream.closed


@pytest.mark.parametrize("partial_tool", [False, True])
async def test_aclose_mid_response_closes_http_stream_even_with_partial_tool(
    settings, partial_tool
):
    delta = (
        {
            "tool_calls": [
                fragment(0, call_id="unfinished", name="get_course", arguments='{"course_code":')
            ]
        }
        if partial_tool
        else {"content": "Đang trả lời"}
    )
    stream = TrackedStream(
        [sse(delta_event(delta)), sse(delta_event(finish_reason="stop")), sse("[DONE]")],
        pause_before=1,
    )

    async with mock_streaming_client(settings, stream) as (client, _):
        iterator = client.astream([HumanMessage("Câu hỏi")])
        first = await asyncio.wait_for(anext(iterator), timeout=5)
        assert isinstance(first, AIMessageChunk)
        assert not stream.closed
        await asyncio.wait_for(iterator.aclose(), timeout=5)
        assert stream.closed


async def test_cancelling_read_halfway_through_sse_line_closes_http_response(settings):
    stream = TrackedStream(
        [b'data: {"choices":[{"delta":{"content":"unfinished', b'"}}]}\n\n'],
        pause_before=1,
    )

    async with mock_streaming_client(settings, stream) as (client, _):
        task = asyncio.create_task(chunks_from(client))
        try:
            await asyncio.wait_for(stream.paused.wait(), timeout=5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)

        assert stream.closed


async def test_anyio_timeout_allows_async_response_cleanup_to_finish(settings):
    class AsyncClosingStream(TrackedStream):
        async def aclose(self):
            # fail_after sẽ lặp lại cancellation tại checkpoint nếu cleanup
            # không được shield; chỉ đặt closed khi cleanup thực sự hoàn tất.
            await anyio.sleep(0)
            self.closed = True

    stream = AsyncClosingStream(
        [b'data: {"choices":[{"delta":{"content":"unfinished', b'"}}]}\n\n'],
        pause_before=1,
    )

    async with mock_streaming_client(settings, stream) as (client, _):
        with pytest.raises(TimeoutError):
            with anyio.fail_after(0.05):
                await chunks_from(client)

        assert stream.paused.is_set()
        # Kiểm tra trước khi đóng AsyncClient để không che lỗi cleanup response.
        assert stream.closed
