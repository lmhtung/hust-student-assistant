"""Danh tính được tạo từ token đã kiểm tra, không nhận role từ body chat."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CurrentUser(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: str = Field(min_length=1, max_length=128)
    # Quyền quản lý dùng role admin; giảng viên là dữ liệu học vụ, không là role.
    role: Literal["student", "admin"]
    permissions: frozenset[str] = Field(default_factory=frozenset)
