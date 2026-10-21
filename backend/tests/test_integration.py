"""Một lượt chat dùng HTTP model client + LangGraph + MCP process + SQLite.

Chỉ model được thay bằng HTTP MockTransport để test không tốn tiền/không cần GPU.
Toàn bộ auth web→API→MCP, SDK transport, tool schema và persistence chạy thật.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from src.agents.student_assistant.agent import StudentAssistant
from src.guardrails.policy import Guardrails
from src.main import create_app
from src.services.llms.client import LLMClient
from src.services.mcp.mcp import AcademicTools
from tests.test_api import headers, parse_events


def test_chat_uses_actual_mcp_process(settings, tmp_path):
    mcp_dir = Path(__file__).resolve().parents[2] / "mcp"
    db_path = tmp_path / "academic.sqlite3"
    subprocess.run(
        [sys.executable, "-m", "scripts.seed_demo", "--path", str(db_path)],
        cwd=mcp_dir,
        check=True,
        capture_output=True,
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "ASSISTANT_MCP_SECRET": settings.mcp_secret.get_secret_value(),
        "ASSISTANT_ACADEMIC_DB": str(db_path),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.server:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        cwd=mcp_dir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        ready = False
        with httpx.Client(timeout=0.2) as probe:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError(process.stderr.read().decode())
                try:
                    ready = probe.post(url).status_code == 401
                    if ready:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
        assert ready, "MCP server did not become ready"
        settings = settings.model_copy(update={"mcp": settings.mcp.model_copy(update={"url": url})})
        llm = LLMClient(settings.llm)
        # Client chưa gửi request, close synchronous transport resources bằng TestClient sau.
        original_http = llm.http
        calls = []

        def model_stream(deltas, finish_reason):
            frames = [{"choices": [{"index": 0, "delta": delta}]} for delta in deltas]
            frames.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]})
            body = "".join(f"data: {json.dumps(frame, ensure_ascii=False)}\n\n" for frame in frames)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=body + "data: [DONE]\n\n",
            )

        async def respond(request):
            body = json.loads(request.content)
            calls.append(body)
            if body.get("response_format"):
                assert body["stream"] is False
                message = {"role": "assistant", "content": '{"decision":"allow","categories":[]}'}
                return httpx.Response(
                    200, json={"choices": [{"finish_reason": "stop", "message": message}]}
                )
            assert body["stream"] is True
            if body["messages"][-1]["role"] == "tool":
                data = json.loads(body["messages"][-1]["content"])
                assert data["course"]["credits"] == 3
                return model_stream(
                    [
                        {"content": "ET101 có "},
                        {"content": "3 tín chỉ "},
                        {"content": "(dữ liệu demo)."},
                    ],
                    "stop",
                )
            assert len(body["tools"]) == 7
            return model_stream(
                [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "integration-call",
                                "type": "function",
                                "function": {"name": "get_course", "arguments": '{"course_'},
                            }
                        ],
                    },
                    {"tool_calls": [{"index": 0, "function": {"arguments": 'code":"ET101"}'}}]},
                ],
                "tool_calls",
            )

        llm.http = httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="http://llm/v1/"
        )
        assistant = StudentAssistant(
            llm, lambda user: AcademicTools(settings, user), Guardrails(llm), settings
        )
        with TestClient(create_app(settings, assistant)) as client:
            try:
                response = client.post(
                    "/api/v1/chat/stream",
                    json={"message": "ET101 có mấy tín chỉ?"},
                    headers=headers(settings),
                )
                events = parse_events(response.text)
                assert events[-2][0] == "answer"
                assert events[-2][1]["response"] == "ET101 có 3 tín chỉ (dữ liệu demo)."
                assert events[-1][1]["ok"] is True
                assert len(calls) == 4  # input safety, model/tool decision, answer, output safety
                assert [call["stream"] for call in calls] == [False, True, True, False]
            finally:
                client.portal.call(llm.aclose)
                client.portal.call(original_http.aclose)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()
