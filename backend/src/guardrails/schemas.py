"""Schemas for guardrails."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GuardrailCategory(str, Enum):
    ROLE_POLICY_VIOLATION = "role_policy_violation"
    HARMFUL_FACILITATION = "harmful_facilitation"
    PROMPT_INJECTION = "prompt_injection"
    PERSONAL_DATA_MISUSE = "personal_data_misuse"


class GuardrailAssessment(BaseModel):
    """Kết quả có cấu trúc do LLM guardrails trả về."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "block", "clarify"]
    categories: list[GuardrailCategory] = Field(default_factory=list)
