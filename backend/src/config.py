"""Cấu hình công khai trong YAML; secret và override trong environment/.env.

Không tạo client HTTP hay kết nối database khi import module.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class LLMSettings(BaseModel):
    model_name: str = "qwen35-27b-fp8"
    api_key: SecretStr = SecretStr("local-not-used")
    api_base_url: str = "http://localhost:8000/v1"
    max_tokens: int = Field(default=2048, ge=64, le=8192)
    temperature: float = Field(default=0.7, ge=0, le=2)
    enable_thinking: bool = False
    # Chỉ bật cho server hỗ trợ tham số riêng của Qwen/vLLM.
    send_chat_template_kwargs: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASSISTANT_",
        env_nested_delimiter="__",
        env_file=BACKEND_DIR / ".env",
        extra="forbid",
    )
    llm: LLMSettings = Field(default_factory=LLMSettings)
    auth_secret: SecretStr = Field(min_length=32)
    mcp_secret: SecretStr = Field(min_length=32)
    mcp_url: str = "http://127.0.0.1:8002/mcp"
    history_path: Path = BACKEND_DIR / "data/conversations.sqlite3"
    max_tool_rounds: int = Field(default=5, ge=1, le=10)
    max_tool_calls: int = Field(default=12, ge=1, le=30)
    request_timeout: float = Field(default=120, ge=10, le=300)
    max_concurrent_requests: int = Field(default=8, ge=1, le=64)

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
        if not self.history_path.is_absolute():
            self.history_path = BACKEND_DIR / self.history_path
        return self


@lru_cache
def get_settings() -> Settings:
    data = yaml.safe_load((BACKEND_DIR / "config.yaml").read_text(encoding="utf-8"))
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("config.yaml phải chứa một mapping")
    return Settings(**data)
