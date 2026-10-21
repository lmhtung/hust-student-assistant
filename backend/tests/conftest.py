import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        auth_secret="test-web-" * 6,
        mcp_secret="test-mcp-" * 6,
        short_memory={"enabled": True, "history_path": tmp_path / "history.sqlite3"},
        _env_file=None,
    )
