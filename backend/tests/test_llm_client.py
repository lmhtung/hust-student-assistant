"""Kiểm tra ranh giới LangChain messages ↔ HTTP JSON của model server."""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.services.llms.client import LLMClient


def model_response(message):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": message}]})


def wire_call(arguments, call_id="call-1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "get_course", "arguments": arguments},
    }


@asynccontextmanager
async def mock_client(settings, handler):
    client = LLMClient(settings.llm)
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://llm/v1/"
    )
    try:
        yield client
    finally:
        await client.aclose()


async def test_http_boundary_converts_all_four_message_types(settings):
    async def handle(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == settings.llm.default_model
        assert body["stream"] is False
        messages = body["messages"]
        assert [message["role"] for message in messages] == ["system", "user", "assistant", "tool"]
        assert messages[0]["content"] == "Trả lời tiếng Việt."
        assert messages[1]["content"] == "Thông tin ET101"
        assert messages[2]["tool_calls"][0] == wire_call('{"course_code": "ET101"}')
        assert messages[3] == {
            "role": "tool",
            "content": '{"credits": 3}',
            "tool_call_id": "call-1",
        }
        assert all("type" not in message for message in messages)
        return model_response({"content": "ET101 có 3 tín chỉ."})

    messages = [
        SystemMessage("Trả lời tiếng Việt."),
        HumanMessage("Thông tin ET101"),
        AIMessage(
            "",
            tool_calls=[{"name": "get_course", "args": {"course_code": "ET101"}, "id": "call-1"}],
        ),
        ToolMessage('{"credits": 3}', tool_call_id="call-1"),
    ]
    async with mock_client(settings, handle) as client:
        result = await client.complete(messages)
    assert isinstance(result, AIMessage)
    assert result.content == "ET101 có 3 tín chỉ."


@pytest.mark.parametrize("bad_arguments", ["not-json", "[]", "null", '"ET101"'])
async def test_valid_and_invalid_tool_calls_keep_ids_when_replayed(settings, bad_arguments):
    requests = []

    async def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return model_response(
                {
                    "content": None,
                    "tool_calls": [
                        wire_call('{"course_code":"ET101"}', "valid-call"),
                        wire_call(bad_arguments, "invalid-call"),
                    ],
                }
            )
        assistant = body["messages"][1]
        assert assistant["tool_calls"] == [
            wire_call('{"course_code": "ET101"}', "valid-call"),
            wire_call(bad_arguments, "invalid-call"),
        ]
        assert body["messages"][-1]["tool_call_id"] == "invalid-call"
        assert "invalid_tool_calls" not in assistant
        assert "error" not in assistant["tool_calls"][1]
        return model_response({"content": "Bạn hãy cho biết mã học phần."})

    question = HumanMessage("Thông tin học phần")
    async with mock_client(settings, handle) as client:
        result = await client.complete([question])
        assert result.content == ""
        assert result.tool_calls == [
            {
                "name": "get_course",
                "args": {"course_code": "ET101"},
                "id": "valid-call",
                "type": "tool_call",
            }
        ]
        assert result.invalid_tool_calls == [
            {
                "name": "get_course",
                "args": bad_arguments,
                "id": "invalid-call",
                "error": "invalid_tool_arguments",
                "type": "invalid_tool_call",
            }
        ]
        corrected = await client.complete(
            [
                question,
                result,
                ToolMessage('{"credits":3}', tool_call_id="valid-call"),
                ToolMessage('{"error":"invalid_tool_arguments"}', tool_call_id="invalid-call"),
            ]
        )
    assert corrected.content == "Bạn hãy cho biết mã học phần."


@pytest.mark.parametrize("content", [None, "Chào bạn"])
async def test_null_content_and_tool_calls_are_normalized(settings, content):
    async with mock_client(
        settings, lambda request: model_response({"content": content, "tool_calls": None})
    ) as client:
        result = await client.complete([HumanMessage("Xin chào")])
    assert result.content == (content or "")
    assert result.tool_calls == []
    assert result.invalid_tool_calls == []


async def test_provider_reasoning_and_metadata_are_not_copied_into_messages(settings):
    raw = {
        "content": "Câu trả lời công khai",
        "reasoning_content": "private reasoning",
        "additional_kwargs": {"secret": "private secret"},
        "response_metadata": {"internal": "private metadata"},
        "id": "private-provider-id",
    }
    async with mock_client(settings, lambda request: model_response(raw)) as client:
        result = await client.complete([HumanMessage("Câu hỏi")])
    assert result.content == "Câu trả lời công khai"
    assert result.additional_kwargs == {}
    assert result.response_metadata == {}
    assert result.id is None
    assert "private" not in str(result.model_dump())


async def test_assessment_requests_json_without_tools(settings):
    async def handle(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["temperature"] == 0
        assert "tools" not in body
        return model_response({"content": '{"decision":"allow","categories":[]}'})

    async with mock_client(settings, handle) as client:
        result = await client.complete(
            [SystemMessage("Phân loại an toàn."), HumanMessage("Câu hỏi")], assessment=True
        )
    assert isinstance(result, AIMessage)
    assert json.loads(result.content)["decision"] == "allow"


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"choices": None},
        {"choices": [None]},
        {"choices": [{"message": None}]},
        {"choices": [{"finish_reason": "length", "message": {"content": "truncated"}}]},
        {"choices": [{"message": {"content": ["non-text response"]}}]},
        {"choices": [{"message": {"role": "user", "content": "wrong role"}}]},
        {"choices": [{"message": {"tool_calls": [wire_call("{}", "")]}}]},
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {"name": "", "arguments": "{}"},
                            }
                        ]
                    }
                }
            ]
        },
        {"choices": [{"message": {"tool_calls": [wire_call({"already": "parsed"})]}}]},
    ],
)
async def test_invalid_response_shapes_fail_before_entering_the_graph(settings, body):
    async with mock_client(settings, lambda request: httpx.Response(200, json=body)) as client:
        with pytest.raises(ValueError):
            await client.complete([HumanMessage("Câu hỏi")])
