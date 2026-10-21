"""Cấu hình theo nhóm trong YAML; secret và override trong environment/.env.

Không tạo client HTTP hay kết nối database khi import module.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class ConfigSection(BaseModel):
    """Báo lỗi khi gõ sai tên cấu hình thay vì âm thầm bỏ qua."""

    model_config = ConfigDict(extra="forbid")


class ServerSettings(ConfigSection):
    host: str = Field(default="0.0.0.0", min_length=1)
    port: int = Field(default=8023, ge=1, le=65535)


class AppSettings(ServerSettings):
    reload: bool = False
    log_level: Literal["trace", "debug", "info", "warning", "error", "critical"] = "info"
    request_timeout: float = Field(default=120, ge=10, le=300)
    max_concurrent_requests: int = Field(default=8, ge=1, le=64)


class UISettings(ServerSettings):
    """Địa chỉ UI được ui/scripts/serve.mjs đọc; backend không khởi chạy UI."""

    port: int = Field(default=5173, ge=1, le=65535)


class LLMSettings(ConfigSection):
    api_key: SecretStr = SecretStr("sk-1234")
    base_url: str = "http://localhost:8024/v1"
    default_model: str = Field(default="qwen3-8b-fp8", min_length=1)
    max_tokens: int = Field(default=3000, ge=64, le=8192)
    temperature: float = Field(default=0.0, ge=0, le=2)
    enable_thinking: bool = False
    # Chỉ bật cho server hỗ trợ tham số riêng của Qwen/vLLM.
    send_chat_template_kwargs: bool = True


class EmbeddingsSettings(ConfigSection):
    """Giữ hợp đồng cấu hình; chỉ bật sau khi triển khai embedding client."""

    enabled: Literal[False] = False
    api_key: SecretStr = SecretStr("sk-1234")
    base_url: str = "http://localhost:8026/v1"
    model: str = "qwen3-embedding-4b"
    max_input_tokens: int = Field(default=31950, gt=0)


class MCPSettings(ConfigSection):
    url: str = "http://127.0.0.1:8002/mcp"
    timeout: float = Field(default=20, gt=0, le=120)


class AgentSettings(ConfigSection):
    """Giới hạn chung của vòng model → tools, không quyết định role người dùng."""

    max_tool_rounds: int = Field(default=5, ge=1, le=10)
    max_tool_calls: int = Field(default=12, ge=1, le=30)


class ShortMemorySettings(ConfigSection):
    # Tắt chỉ ngừng đưa lịch sử vào model; hội thoại vẫn được lưu và kiểm tra owner.
    enabled: bool = False
    history_path: Path = Path("data/conversations.sqlite3")
    max_messages: int = Field(default=24, ge=2, le=200)


class LongMemorySettings(ConfigSection):
    """Chưa có long memory/RAG; từ chối enabled=true để tránh cấu hình giả."""

    enabled: Literal[False] = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASSISTANT_",
        env_nested_delimiter="__",
        env_file=BACKEND_DIR / ".env",
        extra="forbid",
    )
    app: AppSettings = Field(default_factory=AppSettings)
    ui: UISettings = Field(default_factory=UISettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    embeddings: EmbeddingsSettings = Field(default_factory=EmbeddingsSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    agents: AgentSettings = Field(default_factory=AgentSettings)
    short_memory: ShortMemorySettings = Field(default_factory=ShortMemorySettings)
    long_memory: LongMemorySettings = Field(default_factory=LongMemorySettings)
    # Hai secret này lấy từ .env; không phải API key mẫu của model server.
    auth_secret: SecretStr = Field(min_length=32)
    mcp_secret: SecretStr = Field(min_length=32)

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        # Environment > .env > YAML truyền qua init > mặc định.
        return env_settings, dotenv_settings, init_settings, file_secret_settings

    @model_validator(mode="after")
    def separate_credentials(self):
        if self.auth_secret == self.mcp_secret:
            raise ValueError("Phải dùng hai secret khác nhau cho web→API và API→MCP")
        if not self.short_memory.history_path.is_absolute():
            self.short_memory.history_path = BACKEND_DIR / self.short_memory.history_path
        return self


@lru_cache
def get_settings() -> Settings:
    data = yaml.safe_load((BACKEND_DIR / "config.yaml").read_text(encoding="utf-8"))
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("config.yaml phải chứa một mapping")
    return Settings(**data)
