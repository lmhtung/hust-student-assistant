""" Context for the base assistant agent """
from __future__ import annotations
from dataclasses import dataclass
from uuid import UUID

from src.schemas.auth import CurrentUser


@dataclass(frozen=True)
class AgentContext:
    current_user: CurrentUser
    request_id: UUID
    session_id: UUID