"""Domain tối thiểu cho adapter demo; không phụ thuộc database của backend web."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Principal(DomainModel):
    user_id: str = Field(min_length=1, max_length=128)
    role: Literal["student"]
    permissions: frozenset[str]


class Meeting(DomainModel):
    # 0 = thứ Hai, 6 = Chủ nhật. Phút tính từ 00:00; [start, end).
    weekday: int = Field(ge=0, le=6)
    start_minute: int = Field(ge=0, lt=1440)
    end_minute: int = Field(gt=0, le=1440)
    weeks: frozenset[int] = Field(min_length=1, max_length=53)
    room: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.start_minute >= self.end_minute or any(not 1 <= w <= 53 for w in self.weeks):
            raise ValueError("Khoảng giờ/tuần không hợp lệ")
        return self


class Section(DomainModel):
    section_id: str
    course_code: str
    course_name: str
    credits: int = Field(gt=0, le=30)
    semester: str
    lecturer_id: str
    capacity: int = Field(ge=0)
    enrolled: int = Field(ge=0)
    is_open: bool
    # Một đơn vị đăng ký chứa TẤT CẢ buổi LT/BT/TH liên quan.
    meetings: list[Meeting] = Field(min_length=1, max_length=30)


class ScheduleRequest(DomainModel):
    semester: str = Field(min_length=1, max_length=30)
    course_codes: list[str] | None = Field(default=None, min_length=1, max_length=8)
    avoid_weekdays: list[int] = Field(default_factory=list, max_length=7)
    earliest_minute: int = Field(default=0, ge=0, lt=1440)
    latest_minute: int = Field(default=1440, gt=0, le=1440)
    max_results: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def valid_preferences(self):
        if any(d not in range(7) for d in self.avoid_weekdays):
            raise ValueError("weekday phải thuộc 0..6")
        if self.earliest_minute >= self.latest_minute:
            raise ValueError("Khoảng giờ mong muốn không hợp lệ")
        if self.course_codes is not None:
            if any(not c.strip() or len(c) > 30 for c in self.course_codes):
                raise ValueError("Mã học phần không hợp lệ")
            if len(set(self.course_codes)) != len(self.course_codes):
                raise ValueError("Mã học phần bị lặp")
        return self
