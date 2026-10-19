"""Academic MCP HTTP server. Tất cả tools đều chỉ đọc.

Chạy từ mcp/: uvicorn src.server:create_app --factory --port 8002.
Không bật stdio: tools cá nhân yêu cầu HTTP principal đã xác thực.
"""

import asyncio
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.auth import BearerMiddleware, principal_from_context
from src.models import ScheduleRequest
from src.repository import AcademicRepository
from src.scheduling import suggest

MCP_DIR = Path(__file__).resolve().parents[1]
Code = Annotated[str, Field(min_length=1, max_length=30)]
Query = Annotated[str, Field(max_length=100)]
Limit = Annotated[int, Field(ge=1, le=30)]
Offset = Annotated[int, Field(ge=0, le=10000)]
CourseCodes = Annotated[list[Code], Field(min_length=1, max_length=8)]
Day = Annotated[int, Field(ge=0, le=6)]
AvoidDays = Annotated[list[Day], Field(max_length=7)]
StartMinute = Annotated[int, Field(ge=0, lt=1440)]
EndMinute = Annotated[int, Field(gt=0, le=1440)]
ResultLimit = Annotated[int, Field(ge=1, le=5)]
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASSISTANT_", env_file=MCP_DIR / ".env", extra="ignore"
    )
    mcp_secret: SecretStr = Field(min_length=32)
    academic_db: Path = MCP_DIR / "data/academic.sqlite3"
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*"]
    mcp_allowed_origins: list[str] = ["http://127.0.0.1:*", "http://localhost:*"]


async def read_data(operation, *args):
    try:
        return await asyncio.to_thread(operation, *args)
    except ValueError as exc:
        # Các ValueError nghiệp vụ tự viết không chứa dữ liệu nhạy cảm.
        raise ToolError("Không đủ dữ liệu hợp lệ hoặc cần thu hẹp yêu cầu.") from exc
    except (sqlite3.Error, OSError) as exc:
        raise ToolError("Nguồn dữ liệu học vụ tạm thời không khả dụng.") from exc


def register_tools(server: FastMCP, repository: AcademicRepository):
    """Chỉ khai báo tool; xác thực và nghiệp vụ nằm ở module riêng."""

    @server.tool(annotations=READ_ONLY)
    async def search_courses(
        ctx: Context,
        query: Query = "",
        curriculum_only: bool = False,
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """Tìm học phần; CTĐT lấy từ hồ sơ sinh viên, gồm cả các học phần chung."""
        user = principal_from_context(ctx)
        return await read_data(
            repository.search_courses, user.user_id, query, curriculum_only, limit, offset
        )

    @server.tool(annotations=READ_ONLY)
    async def get_course(ctx: Context, course_code: Code) -> dict[str, Any]:
        """Thông tin học phần theo mã chính xác, gồm số tín chỉ."""
        principal_from_context(ctx)
        return await read_data(repository.course, course_code)

    @server.tool(annotations=READ_ONLY)
    async def list_open_classes(
        ctx: Context,
        semester: Code,
        course_code: Code | None = None,
        curriculum_only: bool = False,
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """Lớp mở theo học kỳ, mã học phần hoặc CTĐT. Dùng next_offset để xem tiếp."""
        user = principal_from_context(ctx)
        return await read_data(
            repository.open_classes,
            user.user_id,
            semester,
            course_code,
            curriculum_only,
            limit,
            offset,
        )

    @server.tool(annotations=READ_ONLY)
    async def search_lecturers(
        ctx: Context, query: Query = "", limit: Limit = 20, offset: Offset = 0
    ) -> dict[str, Any]:
        """Tìm giảng viên theo tên hoặc khoa/viện; chỉ trường hồ sơ công khai."""
        principal_from_context(ctx)
        return await read_data(repository.search_lecturers, query, limit, offset)

    @server.tool(annotations=READ_ONLY)
    async def get_lecturer(ctx: Context, lecturer_id: Code, semester: Code) -> dict[str, Any]:
        """Hồ sơ, lớp giảng dạy và thống kê đánh giá đã công bố, tối thiểu 5 đánh giá."""
        principal_from_context(ctx)
        return await read_data(repository.lecturer, lecturer_id, semester)

    @server.tool(annotations=READ_ONLY)
    async def get_my_academic_context(ctx: Context, semester: Code) -> dict[str, Any]:
        """CTĐT/học phần đã chọn/lớp đã đăng ký của CHÍNH người đang chat."""
        user = principal_from_context(ctx)
        return await read_data(repository.academic_context, user.user_id, semester)

    @server.tool(annotations=READ_ONLY)
    async def suggest_timetable(
        ctx: Context,
        semester: Code,
        course_codes: CourseCodes | None = None,
        avoid_weekdays: AvoidDays | None = None,
        earliest_minute: StartMinute = 0,
        latest_minute: EndMinute = 1440,
        max_results: ResultLimit = 3,
    ) -> dict[str, Any]:
        """Gợi ý lịch. Nếu thiếu course_codes, dùng học phần sinh viên đã chọn.

        Tuần/thứ/giờ đều được kiểm tra. 0=thứ Hai,6=CN; giờ theo phút từ 00:00.
        Giữ lớp đã đăng ký, bỏ lớp đầy/đóng. Không thực hiện đăng ký hay ghi DB.
        """
        user = principal_from_context(ctx)
        request = ScheduleRequest(
            semester=semester,
            course_codes=course_codes,
            avoid_weekdays=avoid_weekdays or [],
            earliest_minute=earliest_minute,
            latest_minute=latest_minute,
            max_results=max_results,
        )
        return await read_data(suggest, repository, user.user_id, request)


def create_app(settings: MCPSettings | None = None):
    config = settings or MCPSettings()
    repository = AcademicRepository(config.academic_db)
    server = FastMCP(
        "academic-readonly",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=config.mcp_allowed_hosts,
            allowed_origins=config.mcp_allowed_origins,
        ),
    )

    register_tools(server, repository)
    app = server.streamable_http_app()
    app.add_middleware(BearerMiddleware, secret=config.mcp_secret.get_secret_value())
    return app
