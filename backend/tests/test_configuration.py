"""Contract của cấu hình/xác thực: không cần model hoặc database học vụ."""

import time

import jwt
import pytest
import yaml
from pydantic import ValidationError

from src.auth import verify_web_token
from src.config import Settings


def test_example_yaml_is_a_valid_configuration(settings):
    from src.config import BACKEND_DIR

    data = yaml.safe_load((BACKEND_DIR / "config.example.yaml").read_text(encoding="utf-8"))
    configured = Settings(
        **data,
        auth_secret=settings.auth_secret,
        mcp_secret=settings.mcp_secret,
        _env_file=None,
    )
    assert configured.app.port == 8023
    assert configured.ui.port == 5173
    assert configured.llm.default_model == "qwen3-8b-fp8"
    assert configured.short_memory.history_path == BACKEND_DIR / "data/conversations.sqlite3"


def test_nested_yaml_and_environment_merge_without_losing_other_values(monkeypatch, tmp_path):
    import src.config as config

    data = {
        "app": {"host": "127.0.0.1", "port": 9010},
        "llm": {"default_model": "test-model", "temperature": 0.5},
        "mcp": {"url": "http://academic.test/mcp", "timeout": 7},
        "agents": {"max_tool_rounds": 2},
        "short_memory": {"enabled": True, "history_path": "data/test.sqlite3"},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr(config, "BACKEND_DIR", tmp_path)
    monkeypatch.setenv("ASSISTANT_AUTH_SECRET", "web-test-" * 6)
    monkeypatch.setenv("ASSISTANT_MCP_SECRET", "mcp-test-" * 6)
    monkeypatch.setenv("ASSISTANT_APP__PORT", "9011")
    monkeypatch.setenv("ASSISTANT_LLM__TEMPERATURE", "0.1")
    config.get_settings.cache_clear()
    try:
        configured = config.get_settings()
        assert configured.app.port == 9011
        assert configured.app.host == "127.0.0.1"
        assert configured.llm.temperature == 0.1
        assert configured.llm.default_model == "test-model"
        assert configured.mcp.timeout == 7
        assert configured.agents.max_tool_rounds == 2
        assert configured.short_memory.history_path == tmp_path / "data/test.sqlite3"
    finally:
        config.get_settings.cache_clear()


@pytest.mark.parametrize(
    "overrides",
    [
        {"app": {"port": 70000}},
        {"llm": {"api_base_url": "http://old-field.test/v1"}},
        {"agents": {"max_tool_rounds": 0}},
        {"short_memory": {"max_messages": 0}},
        {"embeddings": {"enabled": True}},
        {"long_memory": {"enabled": True}},
    ],
)
def test_invalid_or_unimplemented_configuration_is_rejected(settings, overrides):
    with pytest.raises(ValidationError):
        Settings(
            **overrides,
            auth_secret=settings.auth_secret,
            mcp_secret=settings.mcp_secret,
            _env_file=None,
        )


def test_main_passes_app_configuration_to_uvicorn(settings, monkeypatch):
    import uvicorn

    import src.main as main_module

    app = settings.app.model_copy(
        update={"host": "127.0.0.9", "port": 9123, "reload": True, "log_level": "debug"}
    )
    monkeypatch.setattr(
        main_module, "get_settings", lambda: settings.model_copy(update={"app": app})
    )
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda target, **kwargs: calls.append((target, kwargs)))

    main_module.main()

    assert calls == [
        (
            "src.main:app",
            {"host": "127.0.0.9", "port": 9123, "reload": True, "log_level": "debug"},
        )
    ]


def test_web_and_mcp_secrets_must_be_different(tmp_path):
    with pytest.raises(ValidationError):
        Settings(
            auth_secret="same-secret-" * 4,
            mcp_secret="same-secret-" * 4,
            short_memory={"history_path": tmp_path / "history.sqlite3"},
            _env_file=None,
        )


def test_environment_overrides_public_config(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSISTANT_LLM__TEMPERATURE", "0.2")
    settings = Settings(
        auth_secret="test-web-" * 6,
        mcp_secret="test-mcp-" * 6,
        llm={"temperature": 0.7},
        short_memory={"history_path": tmp_path / "history.sqlite3"},
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
