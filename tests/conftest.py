from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    """Backward-compatible alias used by the existing Windows-focused tests."""
    return tmp_path


@pytest.fixture(autouse=True)
def isolate_runtime_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TAM_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("TAM_DATA_DIR", str(tmp_path / "data"))
