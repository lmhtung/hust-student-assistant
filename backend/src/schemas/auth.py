""" Schema for authentication API requests and responses. """
from __future__ import annotations
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal

class CurrentUser(BaseModel):
    """Schema for current user."""
    model_config = ConfigDict(extra="forbid", frozen =True)
    user_id: str = Field(min_length=1)
    role: Literal["student", "admin"]
    permissions: frozenset[str] = Field(default_factory=frozenset)