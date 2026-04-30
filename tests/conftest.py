from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from cerno.config import Settings, reset_settings
from cerno.models import FileSchema, SchemaColumn, User
from cerno.repositories import FileRepository, SchemaRepository


@pytest.fixture
def tmp_data_root(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    reset_settings()
    with tempfile.TemporaryDirectory(prefix="cerno-test-") as td:
        root = Path(td)
        monkeypatch.setenv("CERNO_DATA_ROOT", str(root))
        monkeypatch.setenv("CERNO_LLM_ENABLED", "false")
        monkeypatch.setenv("CERNO_SESSION_SECRET", "test-secret")
        yield root
    reset_settings()


@pytest.fixture
def settings(tmp_data_root: Path) -> Settings:
    from cerno.config import get_settings

    return get_settings()


@pytest.fixture
def test_user(settings: Settings) -> User:
    """A user row created directly in the SQLite DB, pre-granted beta access.

    The auth flow is bypassed in unit tests; the cookie fixture below signs a
    cookie for this user so endpoint tests get past the UserDep guard. The
    user is pre-granted so they can hit data-plane endpoints without going
    through the redeem flow.
    """
    from cerno.db import connect
    from cerno.repositories import UserRepository

    conn = connect(settings)
    try:
        repo = UserRepository(conn)
        user = repo.upsert_from_google(
            google_sub="test-sub-123",
            email="test@cerno.local",
            name="Test User",
            picture=None,
        )
        granted = repo.mark_granted(user.id, "TEST")
        conn.commit()
    finally:
        conn.close()
    assert granted is not None
    return granted


@pytest.fixture
def auth_cookies(test_user: User, settings: Settings) -> dict[str, str]:
    """Signed session cookie for the test user."""
    from cerno.api.deps import cookie_serializer

    token = cookie_serializer(settings).dumps({"user_id": test_user.id})
    return {settings.session_cookie_name: token}


@pytest.fixture
def auth_client(
    settings: Settings, auth_cookies: dict[str, str]
):
    """A FastAPI TestClient pre-loaded with the test user's session cookie."""
    from fastapi.testclient import TestClient

    from cerno.main import create_app

    app = create_app()
    client = TestClient(app)
    for k, v in auth_cookies.items():
        client.cookies.set(k, v)
    return client


_DTYPE_TO_INFERRED: dict[str, str] = {
    "Int64": "int",
    "Int32": "int",
    "Float64": "float",
    "Float32": "float",
    "String": "string",
    "Boolean": "bool",
    "Date": "date",
    "Datetime": "datetime",
}


def install_file(
    *,
    conn: Any,
    settings: Settings,
    user_id: str,
    session_id: str,
    filename: str,
    frame: pl.DataFrame,
    column_overrides: dict[str, str] | None = None,
) -> tuple[Any, FileSchema]:
    """Install a file directly: write parquet, create File row + FileSchema.
    Used by tests that don't exercise the ingest/discovery flow."""
    files_repo = FileRepository(conn)
    schemas_repo = SchemaRepository(conn)
    file = files_repo.create(
        session_id=session_id,
        filename=filename,
        parquet_path="",
        row_count=frame.height,
    )
    parquet_path = settings.parquet_path(user_id, session_id, file.id)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(parquet_path)
    files_repo.update_processed(
        file_id=file.id,
        parquet_path=str(parquet_path),
        row_count=frame.height,
        header_row=0,
        friendly_name=filename,
        description="",
    )
    file.parquet_path = str(parquet_path)
    overrides = column_overrides or {}
    columns = [
        SchemaColumn(
            file_id=file.id,
            schema_version=1,
            name=name,
            dtype=str(frame.schema[name]),
            inferred_kind=overrides.get(name, _DTYPE_TO_INFERRED.get(str(frame.schema[name]), "string")),
            confidence=1.0,
            position=i,
            column_id=name,
            description="",
        )
        for i, name in enumerate(frame.columns)
    ]
    schema = FileSchema(file_id=file.id, schema_version=1, columns=columns)
    schemas_repo.replace(schema)
    return file, schema


@pytest.fixture
def install_file_factory(settings: Settings, test_user: User):
    """Pytest fixture exposing install_file as a closure with bound settings + test user."""

    def _factory(
        conn: Any,
        session_id: str,
        filename: str,
        frame: pl.DataFrame,
        *,
        user_id: str | None = None,
        **kwargs: Any,
    ):
        return install_file(
            conn=conn,
            settings=settings,
            user_id=user_id or test_user.id,
            session_id=session_id,
            filename=filename,
            frame=frame,
            **kwargs,
        )

    return _factory


__all__ = ["install_file", "install_file_factory"]
