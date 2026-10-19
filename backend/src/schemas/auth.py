"""Danh tính được tạo từ token đã kiểm tra, không nhận role từ body chat."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CurrentUser(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: str = Field(min_length=1, max_length=128)
    # Giữ admin từ schema ban đầu làm alias manager.
    role: Literal["student", "admin", "manager", "lecturer"]
    permissions: frozenset[str] = Field(default_factory=frozenset)
