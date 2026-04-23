from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from openpyxl import Workbook

from cerno.config import Settings
from cerno.db import connect_memory
from cerno.repositories import FileRepository, SchemaRepository, SessionRepository
from cerno.services.ingest import (
    IngestError,
    ingest_file,
    read_tabular,
    slugify_table_name,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_root=tmp_path / "cerno")


@pytest.fixture
def conn():
    c = connect_memory()
    yield c
    c.close()


def _write_csv(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _write_xlsx(path: Path, sheets: dict[str, list[list[object]]]) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(str(path))


def test_slugify_plain() -> None:
    assert slugify_table_name("customers.csv") == "customers"


def test_slugify_punctuation_and_case() -> None:
    assert slugify_table_name("Customer Data 2024.xlsx") == "customer_data_2024"


def test_slugify_leading_digit() -> None:
    assert slugify_table_name("2024_sales.csv") == "t_2024_sales"


def test_slugify_with_sheet() -> None:
    assert slugify_table_name("report.xlsx", sheet="Q1 Summary") == "report_q1_summary"


def test_slugify_empty_fallback() -> None:
    assert slugify_table_name("---.csv") == "table"


def test_read_csv(tmp_path: Path) -> None:
    csv = tmp_path / "customers.csv"
    _write_csv(csv, "id,name,city\n1,Alice,Delhi\n2,Bob,Mumbai\n3,Carol,\n")
    sheets = read_tabular(csv)
    assert len(sheets) == 1
    assert sheets[0].sheet_name is None
    frame = sheets[0].frame
    assert frame.columns == ["id", "name", "city"]
    assert frame.height == 3
    assert frame["city"].to_list()[2] is None


def test_read_xlsx_multiple_sheets(tmp_path: Path) -> None:
    xlsx = tmp_path / "report.xlsx"
    _write_xlsx(
        xlsx,
        {
            "Sales": [["id", "amount"], [1, 100.5], [2, 200.0]],
            "Returns": [["id", "reason"], [1, "damaged"], [2, "wrong item"]],
        },
    )
    sheets = read_tabular(xlsx)
    assert len(sheets) == 2
    names = {s.sheet_name for s in sheets}
    assert names == {"Sales", "Returns"}


def test_read_xlsx_dedupes_headers(tmp_path: Path) -> None:
    xlsx = tmp_path / "dup.xlsx"
    _write_xlsx(xlsx, {"S1": [["id", "id", "name"], [1, 2, "a"]]})
    sheets = read_tabular(xlsx)
    cols = sheets[0].frame.columns
    assert cols == ["id", "id_1", "name"]


def test_read_xlsx_fills_missing_header_names(tmp_path: Path) -> None:
    xlsx = tmp_path / "missing.xlsx"
    _write_xlsx(xlsx, {"S1": [["id", None, "name"], [1, 2, "a"]]})
    sheets = read_tabular(xlsx)
    assert sheets[0].frame.columns == ["id", "col_1", "name"]


def test_unsupported_suffix_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "notes.txt"
    bogus.write_text("hi", encoding="utf-8")
    with pytest.raises(IngestError):
        read_tabular(bogus)


def test_ingest_csv_writes_parquet_and_schema(
    tmp_path: Path, settings: Settings, conn
) -> None:
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    schemas = SchemaRepository(conn)
    session = sessions.create("s")

    csv = tmp_path / "customers.csv"
    _write_csv(
        csv,
        "id,name,city\n" + "\n".join(f"{i},Person{i},Delhi" for i in range(50)) + "\n",
    )

    results = ingest_file(
        source_path=csv,
        original_filename="customers.csv",
        session_id=session.id,
        settings=settings,
        files_repo=files,
        schemas_repo=schemas,
    )
    assert len(results) == 1
    ingested = results[0]
    assert ingested.table_name == "customers"
    assert ingested.file.row_count == 50
    assert Path(ingested.file.parquet_path).exists()

    stored = pl.read_parquet(ingested.file.parquet_path)
    assert stored.columns == ["id", "name", "city"]
    assert stored.height == 50

    schema = schemas.get(ingested.file.id, 1)
    assert schema is not None
    kinds = {c.name: c.inferred_kind for c in schema.columns}
    assert kinds["id"] == "int"


def test_ingest_xlsx_creates_one_file_per_sheet(
    tmp_path: Path, settings: Settings, conn
) -> None:
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    schemas = SchemaRepository(conn)
    session = sessions.create("s")

    xlsx = tmp_path / "mix.xlsx"
    _write_xlsx(
        xlsx,
        {
            "Sales": [["id", "amount"]] + [[i, i * 1.5] for i in range(15)],
            "Returns": [["id", "reason"]] + [[i, "x"] for i in range(10)],
        },
    )

    results = ingest_file(
        source_path=xlsx,
        original_filename="mix.xlsx",
        session_id=session.id,
        settings=settings,
        files_repo=files,
        schemas_repo=schemas,
    )
    assert len(results) == 2
    names = {r.file.filename for r in results}
    assert names == {"mix.xlsx#Sales", "mix.xlsx#Returns"}
    table_names = {r.table_name for r in results}
    assert table_names == {"mix_sales", "mix_returns"}
    for r in results:
        assert Path(r.file.parquet_path).exists()


def test_ingest_xlsx_single_sheet_keeps_filename(
    tmp_path: Path, settings: Settings, conn
) -> None:
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    schemas = SchemaRepository(conn)
    session = sessions.create("s")

    xlsx = tmp_path / "one.xlsx"
    _write_xlsx(xlsx, {"OnlySheet": [["id"], [1], [2]]})

    results = ingest_file(
        source_path=xlsx,
        original_filename="one.xlsx",
        session_id=session.id,
        settings=settings,
        files_repo=files,
        schemas_repo=schemas,
    )
    assert len(results) == 1
    assert results[0].file.filename == "one.xlsx"


def test_ingest_missing_source_raises(
    tmp_path: Path, settings: Settings, conn
) -> None:
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    schemas = SchemaRepository(conn)
    session = sessions.create("s")
    with pytest.raises(IngestError):
        ingest_file(
            source_path=tmp_path / "nope.csv",
            original_filename="nope.csv",
            session_id=session.id,
            settings=settings,
            files_repo=files,
            schemas_repo=schemas,
        )
