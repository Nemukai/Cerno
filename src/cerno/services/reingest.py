from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import polars as pl

from cerno.config import Settings
from cerno.models import FileSchema, InferredKind, SchemaColumn
from cerno.repositories import (
    AssetArtifactRepository,
    FileRepository,
    LinkRepository,
    ProcessingEventRepository,
    SchemaRepository,
    SessionRepository,
    WorkspaceTableRepository,
)
from cerno.services.artifact_cache import ensure_file_artifact_cached
from cerno.services.canonicalize import (
    cast_column_for_dtype,
    iso_to_datetime_series,
    normalize_cell,
    to_polars_series,
)
from cerno.storage import ObjectStore, processed_artifact_key

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
    scale_factor: float = 1.0


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


@dataclass(frozen=True)
class CastedColumn:
    series: pl.Series
    inferred_kind: InferredKind
    confidence: float


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


def _cast_column_with_metadata(series: pl.Series, dtype: str) -> CastedColumn:
    values = series.to_list()

    target = DTYPE_TO_POLARS.get(dtype, pl.String)
    if target == pl.String:
        column = cast_column_for_dtype(values, "string")
        return CastedColumn(to_polars_series(series.name, column), "string", column.confidence)
    if target == pl.Boolean:
        normalized = [normalize_cell(value) for value in values]
        base = pl.Series(series.name, normalized, dtype=pl.String)
        lowered = base.str.to_lowercase()
        truthy = lowered.is_in(["true", "yes", "y", "1", "t"])
        falsy = lowered.is_in(["false", "no", "n", "0", "f"])
        out = pl.Series([None] * base.len(), dtype=pl.Boolean)
        out = out.zip_with(truthy, pl.Series([True] * base.len()))
        out = out.zip_with(falsy, pl.Series([False] * base.len()))
        return CastedColumn(out.alias(series.name), "bool", 1.0)
    try:
        if target == pl.Date or target == pl.Datetime:
            column = cast_column_for_dtype(values, dtype)
            if column.low_confidence or column.kind == "string":
                return CastedColumn(
                    to_polars_series(series.name, column),
                    "string",
                    column.confidence,
                )
            return CastedColumn(
                iso_to_datetime_series(series.name, column, as_date=target == pl.Date),
                "date" if target == pl.Date else "datetime",
                column.confidence,
            )
        if target == pl.Int64 or target == pl.Float64:
            column = cast_column_for_dtype(values, dtype)
            return CastedColumn(
                to_polars_series(series.name, column),
                "int" if column.kind == "int" else "float",
                column.confidence,
            )
        normalized = [normalize_cell(value) for value in values]
        return CastedColumn(pl.Series(series.name, normalized).cast(target, strict=False), "string", 1.0)
    except Exception as exc:
        logger.warning(
            "cast to %s failed for column %r: %s — keeping original dtype",
            target,
            series.name,
            exc,
        )
        column = cast_column_for_dtype(values, "string")
        return CastedColumn(to_polars_series(series.name, column), "string", 0.5)


def _cast_column(series: pl.Series, dtype: str) -> pl.Series:
    return _cast_column_with_metadata(series, dtype).series


def _is_row_empty(row: dict[str, Any]) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in row.values())


def reingest_file(
    *,
    file_id: str,
    spec: FileSpec,
    user_id: str,
    organization_id: str | None = None,
    settings: Settings,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    artifacts_repo: AssetArtifactRepository | None = None,
    tables_repo: WorkspaceTableRepository | None = None,
    object_store: ObjectStore | None = None,
) -> None:
    file = files_repo.get(file_id)
    if file is None:
        raise ReingestError(f"file not found: {file_id}")
    if not artifacts_repo or not tables_repo or not object_store:
        raise ReingestError("Postgres/R2 artifact repositories are required")

    if not spec.columns:
        logger.warning("skipping reingest for %s: no columns in spec", file_id)
        return
    schema_version = files_repo.bump_schema_version(file.id)

    raw_path = ensure_file_artifact_cached(
        file=file,
        artifact_type="raw_parquet",
        settings=settings,
        artifacts_repo=artifacts_repo,
        object_store=object_store,
    )
    if not raw_path:
        raise ReingestError(f"file {file_id} missing raw parquet")
    raw = pl.read_parquet(raw_path)
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
        normalized = [normalize_cell(c) for c in cells]
        if all(c is None for c in normalized):
            continue
        filtered_rows.append(normalized)

    column_names = _dedupe_headers([c.name for c in spec.columns])
    casted_columns: dict[str, CastedColumn] = {}
    if not filtered_rows:
        frame = pl.DataFrame({n: [] for n in column_names})
    else:
        frame_data = {
            name: [row[i] for row in filtered_rows] for i, name in enumerate(column_names)
        }
        frame = pl.DataFrame(frame_data)
        for i, col in enumerate(spec.columns):
            name = column_names[i]
            casted = _cast_column_with_metadata(frame[name], col.dtype)
            if col.scale_factor != 1.0:
                casted = _scale_casted_column(casted, col.scale_factor)
            casted_columns[name] = casted
            frame = frame.with_columns(casted.series.alias(name))

    processed_path = settings.parquet_path(user_id, file.session_id, file.id)
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(processed_path)
    processed_artifact_id: str | None = None
    key = processed_artifact_key(user_id, file.session_id, file.id, schema_version)
    stored = object_store.put_path(
        processed_path,
        key,
        content_type="application/vnd.apache.parquet",
    )
    artifact = artifacts_repo.create(
        user_id=user_id,
        organization_id=organization_id,
        session_id=file.session_id,
        file_id=file.id,
        artifact_type="processed_parquet",
        storage_backend=stored.backend,
        object_key=stored.object_key,
        size_bytes=stored.size_bytes,
        mime_type="application/vnd.apache.parquet",
    )
    processed_artifact_id = artifact.id
    processed_path.unlink(missing_ok=True)

    files_repo.update_processed(
        file_id=file.id,
        parquet_path="",
        row_count=frame.height,
        header_row=spec.header_row,
        friendly_name=spec.friendly_name,
        description=spec.description,
    )
    tables_repo.set_processed_artifact(
        legacy_file_id=file.id,
        artifact_id=processed_artifact_id,
        schema_version=schema_version,
        row_count=frame.height,
    )

    schema_columns: list[SchemaColumn] = []
    for i, col in enumerate(spec.columns):
        name = column_names[i]
        casted = casted_columns.get(
            name,
            CastedColumn(
                series=pl.Series(name, []),
                inferred_kind=DTYPE_TO_INFERRED_KIND.get(col.dtype, "string"),
                confidence=1.0,
            ),
        )
        schema_columns.append(
            SchemaColumn(
                file_id=file.id,
                schema_version=schema_version,
                name=name,
                dtype=str(frame.schema[name]),
                inferred_kind=casted.inferred_kind,
                confidence=casted.confidence,
                position=i,
                column_id=col.column_id or name,
                description=col.description,
            )
        )
    schema = FileSchema(
        file_id=file.id,
        schema_version=schema_version,
        columns=schema_columns,
    )
    schemas_repo.replace(schema)


def _scale_casted_column(casted: CastedColumn, scale_factor: float) -> CastedColumn:
    if casted.inferred_kind not in {"int", "float"}:
        return CastedColumn(casted.series, casted.inferred_kind, min(casted.confidence, 0.5))
    scaled = casted.series.cast(pl.Float64, strict=False) * scale_factor
    if casted.inferred_kind == "int" and float(scale_factor).is_integer():
        return CastedColumn(
            scaled.round(0).cast(pl.Int64, strict=False).alias(casted.series.name),
            "int",
            casted.confidence,
        )
    return CastedColumn(scaled.alias(casted.series.name), "float", casted.confidence)


def apply_approval(
    *,
    session_id: str,
    user_id: str,
    organization_id: str | None = None,
    payload: ApprovalPayload,
    settings: Settings,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    links_repo: LinkRepository,
    sessions_repo: SessionRepository,
    events_repo: ProcessingEventRepository,
    artifacts_repo: AssetArtifactRepository | None = None,
    tables_repo: WorkspaceTableRepository | None = None,
    object_store: ObjectStore | None = None,
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
            organization_id=organization_id,
            settings=settings,
            files_repo=files_repo,
            schemas_repo=schemas_repo,
            artifacts_repo=artifacts_repo,
            tables_repo=tables_repo,
            object_store=object_store,
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


def preview_rows(
    *,
    file_id: str,
    limit: int,
    files_repo: FileRepository,
    settings: Settings,
    artifacts_repo: AssetArtifactRepository,
    object_store: ObjectStore,
) -> dict[str, Any]:
    file = files_repo.get(file_id)
    if file is None:
        raise ReingestError(f"file not found: {file_id}")
    path = ensure_file_artifact_cached(
        file=file,
        artifact_type="processed_parquet",
        settings=settings,
        artifacts_repo=artifacts_repo,
        object_store=object_store,
    )
    if not path:
        path = ensure_file_artifact_cached(
            file=file,
            artifact_type="raw_parquet",
            settings=settings,
            artifacts_repo=artifacts_repo,
            object_store=object_store,
        )
    if not path:
        raise ReingestError(f"no R2 parquet artifact available for file {file_id}")
    frame = pl.read_parquet(path).head(limit)
    columns = frame.columns
    rows = frame.to_dicts()
    return {
        "file_id": file.id,
        "columns": columns,
        "rows": [[row.get(c) for c in columns] for row in rows],
        "total_rows": file.row_count,
    }
