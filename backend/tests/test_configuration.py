"""Contract của cấu hình/xác thực: không cần model hoặc database học vụ."""

import time

import jwt
import pytest
from pydantic import ValidationError

from src.auth import verify_web_token
from src.config import Settings


def test_web_and_mcp_secrets_must_be_different(tmp_path):
    with pytest.raises(ValidationError):
        Settings(
            auth_secret="same-secret-" * 4,
            mcp_secret="same-secret-" * 4,
            history_path=tmp_path / "history.sqlite3",
            _env_file=None,
        )


def test_environment_overrides_public_config(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSISTANT_LLM__TEMPERATURE", "0.2")
    settings = Settings(
        auth_secret="test-web-" * 6,
        mcp_secret="test-mcp-" * 6,
        llm={"temperature": 0.7},
        history_path=tmp_path / "history.sqlite3",
        _env_file=None,
    )
    assert settings.llm.temperature == 0.2


@pytest.mark.parametrize("yaml_text", ["[]", "false", "42", "not a mapping"])
def test_config_rejects_non_mapping_yaml(monkeypatch, tmp_path, yaml_text):
    import src.config as config

    (tmp_path / "config.yaml").write_text(yaml_text)
    monkeypatch.setattr(config, "BACKEND_DIR", tmp_path)
    config.get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="mapping"):
            config.get_settings()
    finally:
        config.get_settings.cache_clear()


def test_signed_web_token_has_bounded_lifetime(settings):
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "demo-et",
            "role": "student",
            "permissions": ["academic:read"],
            "iss": "web-backend",
            "aud": "assistant-api",
            "iat": now,
            "exp": now + 901,
        },
        settings.auth_secret.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(ValueError):
        verify_web_token(token, settings)
