import sys
from pathlib import Path

import pytest

# Hai service có package src riêng: chạy hai test suite trong hai process riêng.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.seed_demo import seed
from src.repository import AcademicRepository


@pytest.fixture
def repository(tmp_path):
    path = tmp_path / "academic.sqlite3"
    seed(path)
    return AcademicRepository(path)
