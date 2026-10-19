"""Schemas for routing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RoutingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal[
        "general_chat",
        "system_task",
        "clarify",
    ]
