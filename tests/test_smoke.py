from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from cerno import __version__
from cerno.config import get_settings
from cerno.main import create_app


def test_version_string() -> None:
    assert __version__ == "0.1.0"


def test_health_endpoint(tmp_data_root: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": __version__}


def test_settings_data_root_created(tmp_data_root: Path) -> None:
    settings = get_settings()
    assert settings.data_root == tmp_data_root
    assert settings.data_root.exists()


def test_settings_path_helpers(tmp_data_root: Path) -> None:
    settings = get_settings()
    assert settings.session_dir("abc").name == "abc"
    assert settings.parquet_path("abc", "file1").suffix == ".parquet"
    assert settings.db_path().name == "cerno.sqlite"
