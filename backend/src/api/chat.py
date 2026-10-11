""" Schema for chat API requests and responses. """
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, StringConstraints
from typing import Literal
from typing import List, Optional, Annotated, Literal
from uuid import UUID

MessageText = Annotated[
    str,
    StringConstraints(
        min_length=1, 
        max_length=500, 
        description="Avoid too long messages"
        )
]

class ChatRequest(BaseModel):
    """Schema for chat request."""
    model_config = ConfigDict(extra="forbid")
    # user_id: str
    # top_k: int = 5
    # role: Literal["student", "admin"]
    session_id: UUID
    message: MessageText

class ChatResponse(BaseModel):
    """Schema for chat response."""
    response_id: UUID
    session_id: UUID
    response: str
    status: Literal["answered", "blocked", "clarification_required"]
    # user_id: str
    # tool_call: Optional[List[dict]] = None