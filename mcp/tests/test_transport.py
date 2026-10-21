"""Kiểm thử MCP protocol qua ASGI, gồm auth và dữ liệu có cấu trúc.

Không cần cổng mạng/model thật; ClientSession là client chính thức của MCP SDK.
"""

import time
from contextlib import asynccontextmanager

import httpx
import jwt
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

from src.server import MCPSettings, create_app

SECRET = "test-mcp-secret-" + "x" * 40
URL = "http://localhost:8002/mcp"


def token(**overrides):
    now = int(time.time())
    claims = {
        "sub": "demo-et",
        "role": "student",
        "permissions": ["academic:read"],
        "iss": "assistant-api",
        "aud": "academic-mcp",
        "iat": now,
        "exp": now + 60,
    }
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def app_for(repository):
    return create_app(MCPSettings(mcp_secret=SECRET, academic_db=repository.path, _env_file=None))


@asynccontextmanager
async def session_for(repository, bearer):
    app = app_for(repository)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            headers={"Authorization": f"Bearer {bearer}"},
        ) as client:
            async with streamable_http_client(URL, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


@pytest.mark.asyncio
async def test_tool_contract_is_read_only_and_personal_tools_accept_no_user_id(repository):
    async with session_for(repository, token()) as session:
        tools = (await session.list_tools()).tools
        assert {t.name for t in tools} == {
            "search_courses",
            "get_course",
            "list_open_classes",
            "search_lecturers",
            "get_lecturer",
            "get_my_academic_context",
            "suggest_timetable",
        }
        for tool in tools:
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False
            assert "user_id" not in tool.inputSchema.get("properties", {})


@pytest.mark.asyncio
async def test_structured_result_and_own_profile(repository):
    async with session_for(repository, token(sub="demo-it")) as session:
        result = await session.call_tool("get_my_academic_context", {"semester": "2026-1"})
        assert not result.isError
        assert result.structuredContent["program_id"] == "DEMO-IT"
        assert result.structuredContent["selected_courses"] == ["IT101"]
        result = await session.call_tool("get_course", {"course_code": "ET101"})
        assert result.structuredContent["course"]["credits"] == 3


@pytest.mark.asyncio
async def test_transport_suggests_valid_schedule_without_writing(repository):
    async with session_for(repository, token()) as session:
        result = await session.call_tool("suggest_timetable", {"semester": "2026-1"})
        assert not result.isError
        assert result.structuredContent["plans"]
        assert result.structuredContent["plans"][0]["total_credits"] == 10
    assert len(repository.enrolled_classes("demo-et", "2026-1")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bearer,status",
    [
        (None, 401),
        ("not-a-jwt", 401),
        (token(iss="web-backend"), 401),
        (token(aud="other-service"), 401),
        (token(exp=int(time.time()) + 301), 401),
        (token(iat=int(time.time()) - 200, exp=int(time.time()) - 1), 401),
        (token(role="admin"), 403),
        (token(permissions=[]), 403),
    ],
)
async def test_http_boundary_rejects_invalid_identity(repository, bearer, status):
    app = app_for(repository)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        response = await client.post(
            URL, headers={"Authorization": f"Bearer {bearer}"} if bearer else {}
        )
    assert response.status_code == status
    assert response.json()["error"] in {"unauthorized", "forbidden"}


def test_server_rejects_short_secret():
    with pytest.raises(ValidationError):
        MCPSettings(mcp_secret="short", _env_file=None)
