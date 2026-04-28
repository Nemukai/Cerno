from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from openpyxl import Workbook

from cerno.config import Settings
from cerno.db import connect_memory
from cerno.repositories import FileRepository, SessionRepository
from cerno.services.ingest import (
    IngestError,
    first_n_raw_rows,
    ingest_file,
    read_raw_sheets,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_root=tmp_path / "cerno")


@pytest.fixture
def conn():
    c = connect_memory()
    yield c
    c.close()


@pytest.fixture
def user_id(conn) -> str:
    from cerno.repositories import UserRepository

    user = UserRepository(conn).upsert_from_google(
        google_sub="ingest-test", email="ingest@x", name=None, picture=None
    )
    return user.id


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


def test_read_csv_keeps_all_rows_including_metadata(tmp_path: Path) -> None:
    csv = tmp_path / "customers.csv"
    _write_csv(csv, "report header\n\nid,name\n1,Alice\n2,Bob\n")
    sheets = read_raw_sheets(csv)
    assert len(sheets) == 1
    assert sheets[0].sheet_name is None
    assert len(sheets[0].rows) == 5
    assert sheets[0].rows[0][0] == "report header"
    assert sheets[0].rows[2] == ["id", "name"]


def test_read_xlsx_keeps_metadata_rows(tmp_path: Path) -> None:
    xlsx = tmp_path / "report.xlsx"
    _write_xlsx(
        xlsx,
        {
            "Sales": [
                ["Quarterly Report"],
                [None, None],
                ["id", "amount"],
                [1, 100.5],
                [2, 200.0],
            ],
        },
    )
    sheets = read_raw_sheets(xlsx)
    assert len(sheets) == 1
    rows = sheets[0].rows
    assert rows[0][0] == "Quarterly Report"
    assert rows[2] == ["id", "amount"]


def test_read_xlsx_multiple_sheets(tmp_path: Path) -> None:
    xlsx = tmp_path / "mix.xlsx"
    _write_xlsx(
        xlsx,
        {
            "Sales": [["id", "amount"], [1, 100.5]],
            "Returns": [["id", "reason"], [1, "damaged"]],
        },
    )
    sheets = read_raw_sheets(xlsx)
    assert {s.sheet_name for s in sheets} == {"Sales", "Returns"}


def test_unsupported_suffix_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "notes.txt"
    bogus.write_text("hi", encoding="utf-8")
    with pytest.raises(IngestError):
        read_raw_sheets(bogus)


def test_ingest_writes_raw_parquet_only(
    tmp_path: Path, settings: Settings, conn, user_id: str
) -> None:
    sessions = SessionRepository(conn)
    files_repo = FileRepository(conn)
    session = sessions.create("s", user_id=user_id)

    csv = tmp_path / "customers.csv"
    _write_csv(csv, "id,name\n1,Alice\n2,Bob\n")

    results = ingest_file(
        source_path=csv,
        original_filename="customers.csv",
        user_id=user_id,
        session_id=session.id,
        settings=settings,
        files_repo=files_repo,
    )
    assert len(results) == 1
    file = results[0].file
    assert file.raw_parquet_path is not None
    assert Path(file.raw_parquet_path).exists()
    raw = pl.read_parquet(file.raw_parquet_path)
    assert raw.height == 3  # header row + 2 data rows
    assert file.parquet_path == ""  # processed parquet not yet written


def test_first_n_raw_rows_returns_lists(
    tmp_path: Path, settings: Settings, conn, user_id: str
) -> None:
    sessions = SessionRepository(conn)
    files_repo = FileRepository(conn)
    session = sessions.create("s", user_id=user_id)

    csv = tmp_path / "x.csv"
    _write_csv(csv, "id,name\n1,Alice\n2,Bob\n3,Carol\n")
    results = ingest_file(
        source_path=csv,
        original_filename="x.csv",
        user_id=user_id,
        session_id=session.id,
        settings=settings,
        files_repo=files_repo,
    )
    rows = first_n_raw_rows(Path(results[0].file.raw_parquet_path), limit=2)
    assert rows[0] == ["id", "name"]
    assert rows[1] == ["1", "Alice"]


def test_ingest_xlsx_one_file_per_sheet(
    tmp_path: Path, settings: Settings, conn, user_id: str
) -> None:
    sessions = SessionRepository(conn)
    files_repo = FileRepository(conn)
    session = sessions.create("s", user_id=user_id)
    xlsx = tmp_path / "mix.xlsx"
    _write_xlsx(
        xlsx,
        {
            "Sales": [["id", "amount"], [1, 100.5]],
            "Returns": [["id", "reason"], [1, "damaged"]],
        },
    )
    results = ingest_file(
        source_path=xlsx,
        original_filename="mix.xlsx",
        user_id=user_id,
        session_id=session.id,
        settings=settings,
        files_repo=files_repo,
    )
    assert len(results) == 2
    assert {r.file.filename for r in results} == {
        "mix.xlsx#Sales",
        "mix.xlsx#Returns",
    }


def test_ingest_missing_source_raises(
    tmp_path: Path, settings: Settings, conn, user_id: str
) -> None:
    sessions = SessionRepository(conn)
    files_repo = FileRepository(conn)
    session = sessions.create("s", user_id=user_id)
    with pytest.raises(IngestError):
        ingest_file(
            source_path=tmp_path / "nope.csv",
            original_filename="nope.csv",
            user_id=user_id,
            session_id=session.id,
            settings=settings,
            files_repo=files_repo,
        )
