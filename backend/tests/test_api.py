import asyncio
import json
import time
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from src.agents.base_assistant.agent import AgentResult
from src.main import create_app


def headers(settings, user="demo-et", role="student", permissions=None, **overrides):
    now = int(time.time())
    claims = {
        "sub": user,
        "role": role,
        "permissions": ["academic:read"] if permissions is None else permissions,
        "aud": "assistant-api",
        "iss": "web-backend",
        "iat": now,
        "exp": now + 60,
        **overrides,
    }
    return {
        "Authorization": "Bearer "
        + jwt.encode(claims, settings.auth_secret.get_secret_value(), algorithm="HS256")
    }


class FakeAssistant:
    async def run(self, message, history, context, emit):
        await emit({"stage": "checking_output"})
        return AgentResult("Câu trả lời an toàn")


def test_auth_and_identity_cannot_come_from_body_or_headers(settings):
    with TestClient(create_app(settings, FakeAssistant())) as client:
        assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 401
        assert (
            client.post(
                "/api/v1/chat",
                json={"message": "hi"},
                headers={"X-Role": "student", "X-User-Id": "demo-et"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/chat", json={"message": "hi", "role": "admin"}, headers=headers(settings)
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/chat",
                json={"message": "hi"},
                headers=headers(settings, exp=int(time.time()) - 10),
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/chat",
                json={"message": "hi"},
                headers=headers(settings, aud="academic-mcp"),
            ).status_code
            == 401
        )


@pytest.mark.parametrize("role,status", [("admin", 501), ("manager", 501), ("lecturer", 403)])
def test_role_routing_and_manager_placeholder(settings, role, status):
    with TestClient(create_app(settings, FakeAssistant())) as client:
        response = client.post(
            "/api/v1/chat/stream", json={"message": "hi"}, headers=headers(settings, role=role)
        )
        assert response.status_code == status
        assert "text/event-stream" not in response.headers["content-type"]


def test_missing_permission_is_denied(settings):
    with TestClient(create_app(settings, FakeAssistant())) as client:
        assert (
            client.post(
                "/api/v1/chat", json={"message": "hi"}, headers=headers(settings, permissions=[])
            ).status_code
            == 403
        )


def test_conversation_ownership_is_checked_before_streaming(settings):
    session_id = str(uuid4())
    with TestClient(create_app(settings, FakeAssistant())) as client:
        body = {"session_id": session_id, "message": "hi"}
        assert client.post("/api/v1/chat", json=body, headers=headers(settings)).status_code == 200
        stolen = client.post(
            "/api/v1/chat/stream", json=body, headers=headers(settings, user="another-student")
        )
        assert stolen.status_code == 403


def parse_events(text):
    result = []
    for block in text.split("\n\n"):
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            if line.startswith("data: "):
                data = json.loads(line[6:])
        if event:
            result.append((event, data))
    return result


def test_sse_contract_and_json_share_history(settings):
    session_id = str(uuid4())

    class RememberingAssistant(FakeAssistant):
        async def run(self, message, history, context, emit):
            if message == "second":
                assert history == [
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "Câu trả lời an toàn"},
                ]
            return await super().run(message, history, context, emit)

    with TestClient(create_app(settings, RememberingAssistant())) as client:
        response = client.post(
            "/api/v1/chat/stream",
            json={"message": "first", "session_id": session_id},
            headers=headers(settings),
        )
        events = parse_events(response.text)
        assert [name for name, _ in events] == ["status", "answer", "done"]
        assert events[1][1]["session_id"] == session_id
        assert events[-1][1]["ok"] is True
        assert (
            client.post(
                "/api/v1/chat",
                json={"message": "second", "session_id": session_id},
                headers=headers(settings),
            ).status_code
            == 200
        )


def test_sse_failure_is_sanitized_and_lease_is_released(settings):
    class BrokenAssistant(FakeAssistant):
        async def run(self, *args):
            raise RuntimeError("sensitive internal error must never be sent")

    app = create_app(settings, BrokenAssistant())
    session_id = str(uuid4())
    with TestClient(app) as client:
        body = {"message": "hi", "session_id": session_id}
        response = client.post("/api/v1/chat/stream", json=body, headers=headers(settings))
        events = parse_events(response.text)
        assert [name for name, _ in events] == ["error", "done"]
        assert events[-1][1]["ok"] is False
        assert "sensitive internal" not in response.text
        app.state.chat_service.student_assistant = FakeAssistant()
        assert client.post("/api/v1/chat", json=body, headers=headers(settings)).status_code == 200


def test_stream_timeout_cancels_agent_and_releases_lease(settings):
    cancelled = []

    class SlowAssistant(FakeAssistant):
        async def run(self, *args):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)

    app = create_app(settings.model_copy(update={"request_timeout": 0.03}), SlowAssistant())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/stream", json={"message": "hi"}, headers=headers(settings)
        )
        assert parse_events(response.text)[0][1]["code"] == "timeout"
        assert cancelled == [True]


async def test_closing_sse_generator_cancels_work_and_unlocks_conversation(settings):
    from starlette.requests import Request

    from src.api.chat import stream
    from src.schemas.auth import CurrentUser
    from src.schemas.chat import ChatRequest

    cancelled = []

    class WaitingAssistant(FakeAssistant):
        async def run(self, message, history, context, emit):
            try:
                await emit({"stage": "thinking"})
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

    app = create_app(settings, WaitingAssistant())
    user = CurrentUser(user_id="demo-et", role="student", permissions={"academic:read"})
    body = ChatRequest(message="hello")
    async with app.router.lifespan_context(app):
        request = Request({"type": "http", "app": app, "headers": []})
        response = await stream(body, request, user)
        assert "event: status" in await anext(response.body_iterator)
        await response.body_iterator.aclose()
        assert cancelled == [True]
        lease, _ = await app.state.chat_service.conversations.acquire(body.session_id, user, 10)
        await app.state.chat_service.conversations.release(body.session_id, lease)


def test_sse_heartbeat_works_on_python_310(settings, monkeypatch):
    import importlib

    monkeypatch.setattr(importlib.import_module("src.api.sse"), "HEARTBEAT_SECONDS", 0.005)

    class ThinkingAssistant(FakeAssistant):
        async def run(self, *args):
            await asyncio.sleep(0.025)
            return AgentResult("Đã xử lý")

    with TestClient(create_app(settings, ThinkingAssistant())) as client:
        response = client.post(
            "/api/v1/chat/stream", json={"message": "hi"}, headers=headers(settings)
        )
        assert ": ping\n\n" in response.text
        assert [name for name, _ in parse_events(response.text)] == ["answer", "done"]


def test_lost_lease_never_emits_answer(settings):
    from src.api.conversations import ConversationLeaseLost, Conversations

    class LostLeaseStore(Conversations):
        async def commit(self, *args):
            raise ConversationLeaseLost()

    store = LostLeaseStore(settings.history_path)
    with TestClient(create_app(settings, FakeAssistant(), store)) as client:
        response = client.post(
            "/api/v1/chat/stream", json={"message": "hi"}, headers=headers(settings)
        )
        names = [name for name, _ in parse_events(response.text)]
        assert names == ["status", "error", "done"]
        assert "Câu trả lời an toàn" not in response.text


def test_storage_unavailable_before_stream_is_http_error(settings):
    from src.api.conversations import Conversations

    class BrokenStore(Conversations):
        async def acquire(self, *args):
            raise RuntimeError("private database connection string")

    with TestClient(
        create_app(settings, FakeAssistant(), BrokenStore(settings.history_path))
    ) as client:
        response = client.post(
            "/api/v1/chat/stream", json={"message": "hi"}, headers=headers(settings)
        )
        assert response.status_code == 503
        assert "text/event-stream" not in response.headers["content-type"]
        assert "private" not in response.text
