""" Schemas for guardrails."""
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
    categories: list[GuardrailCategory] = Field(
        default_factory=list,
    )
    user_message: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Thông báo ngắn cho người dùng khi từ chối "
            "hoặc yêu cầu làm rõ; không chứa suy luận nội bộ."
        ),
    )


class GuardrailResult(BaseModel):
    """Kết quả nội bộ để ghi nhận policy đã sử dụng."""

    assessment: GuardrailAssessment
    policy_version: str