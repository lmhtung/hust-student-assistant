""" Schema for chat API requests and responses. """
from __future__ import annotations
from pydantic import BaseModel
from typing import List, Optional

class ChatRequest(BaseModel):
    """Schema for chat request."""
    user_id: str
    # role: str
    session_id: str
    top_k: int = 5
    message: str

class ChatResponse(BaseModel):
    """Schema for chat response."""
    response: str
    session_id: str
    user_id: str
    # tool_call: Optional[List[dict]] = None