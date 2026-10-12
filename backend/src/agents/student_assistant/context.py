""" Context for the student assistant agent """
from __future__ import annotations
from uuid import UUID
from dataclasses import dataclass
from src.schemas.auth import CurrentUser
from src.agents.base_assistant.context import AgentContext as BaseAgentContext

# @dataclass(frozen=True)
# class AgentContext:
#     """Context for the student assistant agent."""
#     current_user: CurrentUser
#     session_id: UUID
#     request_id: UUID