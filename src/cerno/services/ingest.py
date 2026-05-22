from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
from python_calamine import CalamineWorkbook

from cerno.config import Settings
from cerno.models import File
from cerno.repositories import (
    AssetArtifactRepository,
    FileRepository,
    SourceAssetRepository,
    WorkspaceAssetRepository,
    WorkspaceTableRepository,
    new_id,
)
from cerno.storage import ObjectStore, raw_artifact_key, source_object_key

CSV_SUFFIXES = {".csv", ".tsv"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xlsb", ".xls", ".ods"}
SUPPORTED_SUFFIXES = CSV_SUFFIXES | EXCEL_SUFFIXES


class IngestError(RuntimeError):
    pass


def slugify_table_name(filename: str) -> str:
    import re

    stem = filename.rsplit(".", 1)[0]
    stem = stem.replace("#", "_")
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", stem).strip("_").lower()
    if not slug:
        slug = "table"
    if slug[0].isdigit():
        slug = f"t_{slug}"
    return slug


@dataclass
class RawSheet:
    sheet_name: str | None
    rows: list[list[Any]]


@dataclass
class IngestedFile:
    file: File
    duplicate: bool = False


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _read_csv_rows(source_path: Path, separator: str) -> list[list[Any]]:
    with source_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        import csv

        reader = csv.reader(f, delimiter=separator)
        rows = [[_normalize_cell(cell) for cell in row] for row in reader]
    width = max((len(r) for r in rows), default=0)
    return [r + [None] * (width - len(r)) for r in rows]


def read_raw_sheets(source_path: Path) -> list[RawSheet]:
    suffix = source_path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        separator = "\t" if suffix == ".tsv" else ","
        rows = _read_csv_rows(source_path, separator)
        if not rows:
            raise IngestError(f"{source_path.name} is empty")
        return [RawSheet(sheet_name=None, rows=rows)]

    if suffix in EXCEL_SUFFIXES:
        try:
            workbook = CalamineWorkbook.from_path(str(source_path))
            sheets: list[RawSheet] = []
            for name in workbook.sheet_names:
                sheet = workbook.get_sheet_by_name(name)
                sheet_rows: list[list[Any]] = sheet.to_python()
                normalized = [[_normalize_cell(cell) for cell in row] for row in sheet_rows]
                width = max((len(r) for r in normalized), default=0)
                if width == 0:
                    continue
                normalized = [r + [None] * (width - len(r)) for r in normalized]
                if any(any(cell is not None for cell in row) for row in normalized):
                    sheets.append(RawSheet(sheet_name=name, rows=normalized))
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError(
                f"could not parse {source_path.name}: file appears corrupt or unsupported ({exc})"
            ) from exc
        if not sheets:
            raise IngestError(f"no non-empty sheets in {source_path.name}")
        return sheets

    raise IngestError(f"unsupported file type: {suffix}")


def _raw_to_frame(rows: list[list[Any]]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    width = len(rows[0])
    columns = {
        f"c{i}": [str(row[i]) if row[i] is not None else None for row in rows] for i in range(width)
    }
    return pl.DataFrame(columns)


def ingest_file(
    *,
    source_path: Path,
    original_filename: str,
    original_content_type: str | None = None,
    original_size_bytes: int | None = None,
    user_id: str,
    organization_id: str | None = None,
    session_id: str,
    settings: Settings,
    files_repo: FileRepository,
    source_assets_repo: SourceAssetRepository | None = None,
    workspace_assets_repo: WorkspaceAssetRepository | None = None,
    artifacts_repo: AssetArtifactRepository | None = None,
    tables_repo: WorkspaceTableRepository | None = None,
    object_store: ObjectStore | None = None,
) -> list[IngestedFile]:
    if not source_path.exists():
        raise IngestError(f"source not found: {source_path}")

    content_hash = hash_file(source_path)
    existing = files_repo.find_by_hash(session_id, content_hash)
    if existing is not None:
        return [IngestedFile(file=existing, duplicate=True)]

    session_dir = settings.session_dir(user_id, session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    if (
        not source_assets_repo
        or not workspace_assets_repo
        or not artifacts_repo
        or not tables_repo
        or not object_store
    ):
        raise IngestError("Postgres/R2 storage repositories are required")

    source_asset = source_assets_repo.get_by_hash(
        user_id,
        content_hash,
        organization_id=organization_id,
    )
    if source_asset is None:
        asset_id = new_id()
        object_key = source_object_key(user_id, asset_id, content_hash, original_filename)
        stored = object_store.put_path(
            source_path,
            object_key,
            content_type=original_content_type or "application/octet-stream",
        )
        source_asset = source_assets_repo.create(
            user_id=user_id,
            organization_id=organization_id,
            sha256=content_hash,
            original_filename=original_filename,
            mime_type=original_content_type,
            size_bytes=stored.size_bytes,
            storage_backend=stored.backend,
            object_key=stored.object_key,
            asset_id=asset_id,
        )
        artifacts_repo.create(
            user_id=user_id,
            organization_id=organization_id,
            source_asset_id=source_asset.id,
            artifact_type="source",
            storage_backend=stored.backend,
            object_key=stored.object_key,
            content_hash=content_hash,
            size_bytes=stored.size_bytes,
            mime_type=original_content_type,
        )
    workspace_asset = workspace_assets_repo.get(session_id, source_asset.id)
    if workspace_asset is None:
        workspace_asset = workspace_assets_repo.create(
            session_id=session_id,
            source_asset_id=source_asset.id,
            display_name=original_filename,
        )

    sheets = read_raw_sheets(source_path)
    results: list[IngestedFile] = []
    for index, sheet in enumerate(sheets):
        display_name = (
            f"{original_filename}#{sheet.sheet_name}"
            if sheet.sheet_name is not None and len(sheets) > 1
            else original_filename
        )
        file = files_repo.create(
            session_id=session_id,
            filename=display_name,
            parquet_path="",
            row_count=len(sheet.rows),
            original_size_bytes=original_size_bytes,
            content_hash=content_hash,
        )
        raw_frame = _raw_to_frame(sheet.rows)
        raw_path = settings.raw_parquet_path(user_id, session_id, file.id)
        raw_frame.write_parquet(raw_path)
        key = raw_artifact_key(user_id, source_asset.id, file.id)
        stored = object_store.put_path(raw_path, key, content_type="application/vnd.apache.parquet")
        artifacts_repo.create(
            user_id=user_id,
            organization_id=organization_id,
            session_id=session_id,
            source_asset_id=source_asset.id,
            file_id=file.id,
            artifact_type="raw_parquet",
            storage_backend=stored.backend,
            object_key=stored.object_key,
            content_hash=content_hash,
            size_bytes=stored.size_bytes,
            mime_type="application/vnd.apache.parquet",
        )
        raw_path.unlink(missing_ok=True)
        tables_repo.create(
            session_id=session_id,
            workspace_asset_id=workspace_asset.id,
            legacy_file_id=file.id,
            sheet_name=sheet.sheet_name,
            table_index=index,
            display_name=display_name,
            row_count=len(sheet.rows),
        )
        results.append(IngestedFile(file=file))
    return results


def first_n_raw_rows(file_path: Path, limit: int = 10) -> list[list[Any]]:
    frame = pl.read_parquet(file_path)
    head = frame.head(limit).to_dicts()
    columns = frame.columns
    return [[row.get(c) for c in columns] for row in head]
