from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from cerno.config import Settings
from cerno.models import FileSchema, InferredKind, SchemaColumn
from cerno.repositories import (
    FileRepository,
    LinkRepository,
    ProcessingEventRepository,
    SchemaRepository,
    SessionRepository,
)

logger = logging.getLogger(__name__)

DTYPE_TO_POLARS: dict[str, Any] = {
    "string": pl.String,
    "int": pl.Int64,
    "float": pl.Float64,
    "date": pl.Date,
    "datetime": pl.Datetime,
    "bool": pl.Boolean,
    "category": pl.String,
}

DTYPE_TO_INFERRED_KIND: dict[str, InferredKind] = {
    "string": "string",
    "int": "int",
    "float": "float",
    "date": "date",
    "datetime": "datetime",
    "bool": "bool",
    "category": "category",
}


class ReingestError(RuntimeError):
    pass


@dataclass
class ColumnSpec:
    column_id: str
    name: str
    dtype: str
    description: str


@dataclass
class FileSpec:
    file_id: str
    header_row: int
    friendly_name: str
    description: str
    columns: list[ColumnSpec]


@dataclass
class LinkSpec:
    file_a_id: str
    col_a: str
    file_b_id: str
    col_b: str
    direction: str
    summary: str


@dataclass
class ApprovalPayload:
    files: list[FileSpec]
    links: list[LinkSpec]
    overview: str


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for h in headers:
        base = h if h else "column"
        if base not in seen:
            seen[base] = 0
            out.append(base)
        else:
            seen[base] += 1
            out.append(f"{base}_{seen[base]}")
    return out


def _cast_column(series: pl.Series, dtype: str) -> pl.Series:
    trimmed = series.cast(pl.String).str.strip_chars()
    blank_mask = (trimmed == "") | trimmed.is_null()
    base = trimmed.set(blank_mask, None)

    target = DTYPE_TO_POLARS.get(dtype, pl.String)
    if target == pl.String:
        return base
    if target == pl.Boolean:
        lowered = base.str.to_lowercase()
        truthy = lowered.is_in(["true", "yes", "y", "1", "t"])
        falsy = lowered.is_in(["false", "no", "n", "0", "f"])
        out = pl.Series([None] * base.len(), dtype=pl.Boolean)
        out = out.zip_with(truthy, pl.Series([True] * base.len()))
        out = out.zip_with(falsy, pl.Series([False] * base.len()))
        return out
    try:
        if target == pl.Date:
            return base.str.to_date(strict=False)
        if target == pl.Datetime:
            return base.str.to_datetime(strict=False)
        return base.cast(target, strict=False)
    except Exception as exc:
        logger.warning(
            "cast to %s failed for column %r: %s — keeping original dtype",
            target,
            base.name,
            exc,
        )
        return base


def _is_row_empty(row: dict[str, Any]) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in row.values())


def reingest_file(
    *,
    file_id: str,
    spec: FileSpec,
    user_id: str,
    settings: Settings,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
) -> None:
    file = files_repo.get(file_id)
    if file is None or not file.raw_parquet_path:
        raise ReingestError(f"file {file_id} missing raw parquet")

    raw = pl.read_parquet(file.raw_parquet_path)
    raw_rows = raw.to_numpy().tolist()
    if spec.header_row >= len(raw_rows):
        raise ReingestError(f"header_row {spec.header_row} beyond file length {len(raw_rows)}")

    data_rows = raw_rows[spec.header_row + 1 :]
    expected_width = len(spec.columns)

    filtered_rows: list[list[Any]] = []
    for row in data_rows:
        cells: list[Any] = list(row)
        if expected_width > len(cells):
            cells = cells + [None] * (expected_width - len(cells))
        elif expected_width < len(cells):
            cells = cells[:expected_width]
        normalized = [
            None if c is None or (isinstance(c, str) and not c.strip()) else c for c in cells
        ]
        if all(c is None for c in normalized):
            continue
        filtered_rows.append(normalized)

    column_names = _dedupe_headers([c.name for c in spec.columns])
    if not filtered_rows:
        frame = pl.DataFrame({n: [] for n in column_names})
    else:
        frame_data = {
            name: [row[i] for row in filtered_rows] for i, name in enumerate(column_names)
        }
        frame = pl.DataFrame(frame_data)
        for i, col in enumerate(spec.columns):
            name = column_names[i]
            casted = _cast_column(frame[name], col.dtype)
            frame = frame.with_columns(casted.alias(name))

    processed_path = settings.parquet_path(user_id, file.session_id, file.id)
    frame.write_parquet(processed_path)

    files_repo.update_processed(
        file_id=file.id,
        parquet_path=str(processed_path),
        row_count=frame.height,
        header_row=spec.header_row,
        friendly_name=spec.friendly_name,
        description=spec.description,
    )

    schema = FileSchema(
        file_id=file.id,
        schema_version=file.schema_version,
        columns=[
            SchemaColumn(
                file_id=file.id,
                schema_version=file.schema_version,
                name=column_names[i],
                dtype=str(frame.schema[column_names[i]]),
                inferred_kind=DTYPE_TO_INFERRED_KIND.get(col.dtype, "string"),
                confidence=1.0,
                position=i,
                column_id=col.column_id or column_names[i],
                description=col.description,
            )
            for i, col in enumerate(spec.columns)
        ],
    )
    schemas_repo.replace(schema)


def apply_approval(
    *,
    session_id: str,
    user_id: str,
    payload: ApprovalPayload,
    settings: Settings,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    links_repo: LinkRepository,
    sessions_repo: SessionRepository,
    events_repo: ProcessingEventRepository,
) -> None:
    files = files_repo.list_for_session(session_id)
    file_ids = {f.id for f in files}

    for spec in payload.files:
        if spec.file_id not in file_ids:
            continue
        events_repo.append(
            session_id=session_id,
            kind="reingesting_file",
            message=f"reprocessing {spec.friendly_name or spec.file_id}",
        )
        reingest_file(
            file_id=spec.file_id,
            spec=spec,
            user_id=user_id,
            settings=settings,
            files_repo=files_repo,
            schemas_repo=schemas_repo,
        )

    links_repo.delete_for_session(session_id)
    for link in payload.links:
        if link.file_a_id not in file_ids or link.file_b_id not in file_ids:
            continue
        links_repo.create(
            session_id=session_id,
            file_a=link.file_a_id,
            col_a=link.col_a,
            file_b=link.file_b_id,
            col_b=link.col_b,
            overlap=1.0,
            direction=link.direction,
            score=1.0,
            summary=link.summary or None,
            source="user_added",
        )

    sessions_repo.set_overview(session_id, payload.overview)
    sessions_repo.set_discovery_status(session_id, "approved")
    sessions_repo.set_status(session_id, "ready")
    events_repo.append(
        session_id=session_id, kind="done", message="schema approved, files reprocessed"
    )


def preview_rows(*, file_id: str, limit: int, files_repo: FileRepository) -> dict[str, Any]:
    file = files_repo.get(file_id)
    if file is None:
        raise ReingestError(f"file not found: {file_id}")
    path = file.parquet_path
    if not path or not Path(path).exists():
        if file.raw_parquet_path and Path(file.raw_parquet_path).exists():
            path = file.raw_parquet_path
        else:
            raise ReingestError(f"no parquet available for file {file_id}")
    frame = pl.read_parquet(path).head(limit)
    columns = frame.columns
    rows = frame.to_dicts()
    return {
        "file_id": file.id,
        "columns": columns,
        "rows": [[row.get(c) for c in columns] for row in rows],
        "total_rows": file.row_count,
    }
