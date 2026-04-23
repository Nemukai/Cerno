from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
from python_calamine import CalamineWorkbook

from cerno.config import Settings
from cerno.models import File, FileSchema
from cerno.repositories import FileRepository, SchemaRepository
from cerno.services.schema import NULL_SENTINELS, build_schema

CSV_SUFFIXES = {".csv", ".tsv"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xlsb", ".xls", ".ods"}
SUPPORTED_SUFFIXES = CSV_SUFFIXES | EXCEL_SUFFIXES


class IngestError(RuntimeError):
    pass


@dataclass
class SheetFrame:
    sheet_name: str | None
    frame: pl.DataFrame


@dataclass
class IngestedFile:
    file: File
    schema: FileSchema
    table_name: str


def slugify_table_name(filename: str, sheet: str | None = None) -> str:
    base = Path(filename).stem
    if sheet:
        base = f"{base}_{sheet}"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", base).strip("_").lower()
    if not slug:
        slug = "table"
    if slug[0].isdigit():
        slug = f"t_{slug}"
    return slug


def read_tabular(source_path: Path) -> list[SheetFrame]:
    suffix = source_path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        separator = "\t" if suffix == ".tsv" else ","
        frame = pl.read_csv(
            source_path,
            separator=separator,
            null_values=list(NULL_SENTINELS),
            infer_schema_length=10_000,
            try_parse_dates=False,
        )
        return [SheetFrame(sheet_name=None, frame=frame)]

    if suffix in EXCEL_SUFFIXES:
        workbook = CalamineWorkbook.from_path(str(source_path))
        results: list[SheetFrame] = []
        for name in workbook.sheet_names:
            sheet = workbook.get_sheet_by_name(name)
            rows: list[list[Any]] = sheet.to_python()
            sheet_frame = _rows_to_frame(rows)
            if sheet_frame is None:
                continue
            results.append(SheetFrame(sheet_name=name, frame=sheet_frame))
        if not results:
            raise IngestError(f"no non-empty sheets in {source_path.name}")
        return results

    raise IngestError(f"unsupported file type: {suffix}")


def _rows_to_frame(rows: list[list[Any]]) -> pl.DataFrame | None:
    if not rows:
        return None
    header_row = rows[0]
    headers = [
        str(h).strip() if h is not None and str(h).strip() else f"col_{i}"
        for i, h in enumerate(header_row)
    ]
    headers = _dedupe_headers(headers)
    data_rows = rows[1:]
    if not data_rows:
        return pl.DataFrame({h: [] for h in headers})
    width = len(headers)
    normalized = [
        list(r) + [None] * (width - len(r)) if len(r) < width else list(r)[:width]
        for r in data_rows
    ]
    return pl.DataFrame(normalized, schema=headers, orient="row", infer_schema_length=10_000)


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for h in headers:
        if h not in seen:
            seen[h] = 0
            out.append(h)
        else:
            seen[h] += 1
            out.append(f"{h}_{seen[h]}")
    return out


def ingest_file(
    *,
    source_path: Path,
    original_filename: str,
    session_id: str,
    settings: Settings,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
) -> list[IngestedFile]:
    if not source_path.exists():
        raise IngestError(f"source not found: {source_path}")

    session_dir = settings.session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    sheets = read_tabular(source_path)
    results: list[IngestedFile] = []
    for sheet in sheets:
        display_name = (
            f"{original_filename}#{sheet.sheet_name}"
            if sheet.sheet_name is not None and len(sheets) > 1
            else original_filename
        )
        file = files_repo.create(
            session_id=session_id,
            filename=display_name,
            parquet_path="",
            row_count=sheet.frame.height,
        )
        parquet_path = settings.parquet_path(session_id, file.id)
        sheet.frame.write_parquet(parquet_path)

        files_repo.conn.execute(
            "UPDATE files SET parquet_path = ? WHERE id = ?",
            (str(parquet_path), file.id),
        )
        file.parquet_path = str(parquet_path)

        schema = build_schema(file_id=file.id, schema_version=1, frame=sheet.frame)
        schemas_repo.replace(schema)

        table_name = slugify_table_name(original_filename, sheet.sheet_name)
        results.append(IngestedFile(file=file, schema=schema, table_name=table_name))
    return results
