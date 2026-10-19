"""App factory: chỉ wiring dependencies, không chứa nghiệp vụ chat hay prompt.

Chạy từ backend/: python -m uvicorn src.main:app --port 8080.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.agents.student_assistant.agent import StudentAssistant
from src.api.chat import router
from src.api.conversations import Conversations, ConversationStore
from src.api.service import ChatService
from src.config import Settings, get_settings
from src.guardrails.policy import Guardrails
from src.services.llms.client import LLMClient
from src.services.mcp.mcp import AcademicTools


def create_app(
    settings: Settings | None = None,
    student_assistant=None,
    conversations: ConversationStore | None = None,
):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or get_settings()
        app.state.settings = config
        store = conversations if conversations is not None else Conversations(config.history_path)
        await store.initialize()
        llm = None
        if student_assistant is None:
            llm = LLMClient(config.llm)
            assistant = StudentAssistant(
                llm, lambda user: AcademicTools(config, user), Guardrails(llm), config
            )
        else:
            assistant = student_assistant
        app.state.chat_service = ChatService(config, store, assistant)
        try:
            yield
        finally:
            if llm is not None:
                await llm.aclose()

    app = FastAPI(title="HUST Assistant Service", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    @app.get("/health")
    async def health():
        # Liveness; không cam kết LLM/MCP/database bên ngoài đang sẵn sàng.
        return {"status": "ok", "manager_assistant": "not_implemented"}

    return app


app = create_app()
