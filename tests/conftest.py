from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from cerno.config import Settings, reset_settings


@pytest.fixture
def tmp_data_root(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    reset_settings()
    with tempfile.TemporaryDirectory(prefix="cerno-test-") as td:
        root = Path(td)
        monkeypatch.setenv("CERNO_DATA_ROOT", str(root))
        monkeypatch.setenv("CERNO_LLM_ENABLED", "false")
        yield root
    reset_settings()


@pytest.fixture
def settings(tmp_data_root: Path) -> Settings:
    from cerno.config import get_settings

    return get_settings()
