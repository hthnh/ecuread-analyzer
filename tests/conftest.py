from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        model_dir=tmp_path / "data" / "models",
        window_size_samples=20,
        window_step_samples=5,
        isolation_n_estimators=50,
        isolation_contamination=0.05,
    )


@pytest.fixture
def fixture_204330() -> Path:
    return FIXTURES / "serial_log_20251105_204330.txt"


@pytest.fixture
def fixture_203744() -> Path:
    return FIXTURES / "serial_log_20251105_203744.txt"

