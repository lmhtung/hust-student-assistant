"""MCP session và delegated token riêng cho từng lượt, không trộn danh tính."""

import json
from contextlib import asynccontextmanager

import anyio
import httpx
from jsonschema import validate
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from src.auth import issue_mcp_token
from src.config import Settings
from src.schemas.auth import CurrentUser

# Allowlist nằm ngoài LLM: dù server quảng bá tool ghi thì student không nhận nó.
READ_TOOLS = frozenset(
    {
        "search_courses",
        "get_course",
        "list_open_classes",
        "search_lecturers",
        "get_lecturer",
        "get_my_academic_context",
        "suggest_timetable",
    }
)
TOOL_DESCRIPTIONS = {
    "search_courses": "Tìm học phần; curriculum_only dùng quan hệ CTĐT của chính sinh viên.",
    "get_course": "Thông tin chính xác học phần theo mã, gồm số tín chỉ.",
    "list_open_classes": "Lớp mở theo học kỳ/mã học phần hoặc CTĐT. Có phân trang.",
    "search_lecturers": "Tìm hồ sơ giảng viên công khai theo tên/đơn vị.",
    "get_lecturer": "Hồ sơ công khai, lớp dạy và đánh giá tổng hợp đã công bố.",
    "get_my_academic_context": "CTĐT, học phần đã chọn và lớp đã đăng ký của người đang chat.",
    "suggest_timetable": "Tính các phương án không trùng lịch, giữ lớp đã đăng ký. Không đăng ký lớp.",
}


def _closed_schema(value):
    """Chặn tham số thừa cả ở object lồng nhau; giữ nguyên các ràng buộc server."""
    if isinstance(value, list):
        return [_closed_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _closed_schema(item) for key, item in value.items()}
    if result.get("type") == "object" or "properties" in result:
        result["additionalProperties"] = False
    return result


class AcademicTools:
    def __init__(self, settings: Settings, user: CurrentUser):
        self.settings = settings
        self.user = user
        self.session: ClientSession | None = None
        self.schemas: dict[str, dict] = {}

    @asynccontextmanager
    async def connect(self):
        if self.user.role != "student" or "academic:read" not in self.user.permissions:
            raise PermissionError("Student read access required")
        token = issue_mcp_token(self.user, self.settings)
        # Credentials chỉ nằm ở HTTP header, tuyệt đối không nằm trong tool arguments.
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=20
        ) as http:
            async with streamable_http_client(self.settings.mcp_url, http_client=http) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    with anyio.fail_after(20):
                        await session.initialize()
                        result = await session.list_tools()
                    self.schemas = {
                        tool.name: _closed_schema(tool.inputSchema)
                        for tool in result.tools
                        if tool.name in READ_TOOLS
                    }
                    self.session = session
                    try:
                        yield self
                    finally:
                        self.session = None
                        self.schemas = {}

    def definitions(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": TOOL_DESCRIPTIONS[name],
                    "parameters": schema,
                },
            }
            for name, schema in self.schemas.items()
        ]

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in READ_TOOLS or name not in self.schemas:
            raise PermissionError("Tool not allowed")
        # Model không được tự thêm student_id/user_id, SQL hoặc context phân quyền.
        validate(arguments, self.schemas[name])
        if self.session is None:
            raise RuntimeError("MCP session is not connected")
        with anyio.fail_after(20):
            result = await self.session.call_tool(name, arguments)
        if result.isError:
            return {"error": "academic_tool_failed", "hint": "Kiểm tra tham số hoặc hỏi lại."}
        data = result.structuredContent
        if data is None:
            try:
                text = "".join(item.text for item in result.content if item.type == "text")
                data = json.loads(text)
            except ValueError:
                return {"error": "invalid_tool_result"}
        if not isinstance(data, dict):
            return {"error": "invalid_tool_result"}
        # Không cắt JSON giữa chừng hoặc nhét kết quả quá lớn vào context model.
        if len(json.dumps(data, ensure_ascii=False)) > 50000:
            return {"error": "result_too_large", "hint": "Hãy thu hẹp tìm kiếm."}
        return data
