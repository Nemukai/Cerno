from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from cerno.config import Settings, reset_settings
from cerno.models import FileSchema, SchemaColumn
from cerno.repositories import FileRepository, SchemaRepository, SessionRepository


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
    parquet_path = settings.parquet_path(session_id, file.id)
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
def install_file_factory(settings: Settings):
    """Pytest fixture exposing install_file as a closure with bound settings."""

    def _factory(conn: Any, session_id: str, filename: str, frame: pl.DataFrame, **kwargs: Any):
        return install_file(
            conn=conn,
            settings=settings,
            session_id=session_id,
            filename=filename,
            frame=frame,
            **kwargs,
        )

    return _factory


__all__ = ["install_file", "install_file_factory"]
