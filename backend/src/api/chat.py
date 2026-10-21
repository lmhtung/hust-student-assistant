"""HTTP routes: xác thực, chuyển lỗi sang HTTP và chọn JSON hoặc SSE."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.agents.factory import AdminAssistantUnavailable
from src.api.conversations import ConversationBusy, ConversationForbidden
from src.api.dependencies import get_current_user
from src.api.service import ChatService, PreparedTurn
from src.api.sse import stream_events
from src.schemas.auth import CurrentUser
from src.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/v1", tags=["chat"])
logger = logging.getLogger(__name__)


async def _prepare(service: ChatService, user: CurrentUser, body: ChatRequest) -> PreparedTurn:
    # Phân quyền và lỗi lưu trữ phải được xử lý trước khi SSE gửi headers 200.
    try:
        return await service.prepare(user, body.session_id, body.message)
    except AdminAssistantUnavailable:
        raise HTTPException(501, "Admin assistant chưa được triển khai") from None
    except (PermissionError, ConversationForbidden):
        raise HTTPException(403, "Assistant or conversation access denied") from None
    except ConversationBusy:
        raise HTTPException(409, "Conversation is processing another request") from None
    except Exception as exc:
        logger.warning("chat_prepare_failed kind=%s", type(exc).__name__)
        raise HTTPException(503, "Assistant temporarily unavailable") from None


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request, user: CurrentUser = Depends(get_current_user)):
    service: ChatService = request.app.state.chat_service
    turn = await _prepare(service, user, body)
    try:
        result = await service.run(turn)
        return ChatResponse(
            response_id=turn.context.request_id,
            session_id=turn.context.session_id,
            response=result.response,
            status=result.status,
        )
    except TimeoutError:
        raise HTTPException(504, "Assistant request timed out") from None
    except Exception as exc:
        logger.warning(
            "chat_failed request_id=%s kind=%s", turn.context.request_id, type(exc).__name__
        )
        raise HTTPException(503, "Assistant temporarily unavailable") from None


@router.post("/chat/stream")
async def stream(
    body: ChatRequest, request: Request, user: CurrentUser = Depends(get_current_user)
):
    service: ChatService = request.app.state.chat_service
    turn = await _prepare(service, user, body)
    return StreamingResponse(
        stream_events(service, turn),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
