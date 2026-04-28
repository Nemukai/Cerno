from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from cerno.config import Settings
from cerno.db import connect_memory
from cerno.repositories import (
    FileRepository,
    LinkRepository,
    ProcessingEventRepository,
    SchemaRepository,
    SessionRepository,
)
from cerno.services.ingest import ingest_file
from cerno.services.reingest import (
    ApprovalPayload,
    ColumnSpec,
    FileSpec,
    LinkSpec,
    ReingestError,
    apply_approval,
    preview_rows,
    reingest_file,
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
        google_sub="reingest-test", email="reingest@x", name=None, picture=None
    )
    return user.id


def _seed_csv(
    settings: Settings,
    conn,
    tmp_path: Path,
    name: str,
    content: str,
    *,
    user_id: str,
) -> str:
    sessions = SessionRepository(conn)
    if not sessions.get("S1"):
        sessions.create("test", user_id=user_id, session_id="S1")
    csv = tmp_path / name
    csv.write_text(content, encoding="utf-8")
    results = ingest_file(
        source_path=csv,
        original_filename=name,
        user_id=user_id,
        session_id="S1",
        settings=settings,
        files_repo=FileRepository(conn),
    )
    return results[0].file.id


def test_reingest_skips_metadata_rows_and_casts(
    settings: Settings, conn, tmp_path: Path, user_id: str
) -> None:
    fid = _seed_csv(
        settings,
        conn,
        tmp_path,
        "orders.csv",
        "Quarterly Report\n\norder_id,amount\n1,10.5\n2,20.0\n,\n",
        user_id=user_id,
    )

    spec = FileSpec(
        file_id=fid,
        header_row=2,
        friendly_name="Orders",
        description="Q1 orders",
        columns=[
            ColumnSpec(column_id="order_id", name="order_id", dtype="int", description="PK"),
            ColumnSpec(column_id="amount", name="amount", dtype="float", description="$"),
        ],
    )
    reingest_file(
        file_id=fid,
        spec=spec,
        user_id=user_id,
        settings=settings,
        files_repo=FileRepository(conn),
        schemas_repo=SchemaRepository(conn),
    )

    file = FileRepository(conn).get(fid)
    assert file is not None
    assert file.parquet_path
    frame = pl.read_parquet(file.parquet_path)
    assert frame.height == 2  # blank trailing row dropped
    assert frame.columns == ["order_id", "amount"]
    assert frame.schema["order_id"] == pl.Int64
    assert frame.schema["amount"] == pl.Float64
    assert frame["amount"].to_list() == [10.5, 20.0]
    assert file.friendly_name == "Orders"
    assert file.header_row == 2


def test_reingest_rejects_header_row_beyond_file(
    settings: Settings, conn, tmp_path: Path, user_id: str
) -> None:
    fid = _seed_csv(settings, conn, tmp_path, "x.csv", "id\n1\n", user_id=user_id)
    spec = FileSpec(
        file_id=fid,
        header_row=99,
        friendly_name="x",
        description="",
        columns=[ColumnSpec(column_id="id", name="id", dtype="int", description="")],
    )
    with pytest.raises(ReingestError):
        reingest_file(
            file_id=fid,
            spec=spec,
            user_id=user_id,
            settings=settings,
            files_repo=FileRepository(conn),
            schemas_repo=SchemaRepository(conn),
        )


def test_apply_approval_persists_links_and_marks_session_ready(
    settings: Settings, conn, tmp_path: Path, user_id: str
) -> None:
    o_id = _seed_csv(
        settings,
        conn,
        tmp_path,
        "orders.csv",
        "order_id,customer_id\n1,10\n2,11\n",
        user_id=user_id,
    )
    c_id = _seed_csv(
        settings,
        conn,
        tmp_path,
        "customers.csv",
        "id,name\n10,Ada\n11,Bea\n",
        user_id=user_id,
    )

    payload = ApprovalPayload(
        files=[
            FileSpec(
                file_id=o_id,
                header_row=0,
                friendly_name="Orders",
                description="",
                columns=[
                    ColumnSpec(column_id="order_id", name="order_id", dtype="int", description=""),
                    ColumnSpec(column_id="customer_id", name="customer_id", dtype="int", description=""),
                ],
            ),
            FileSpec(
                file_id=c_id,
                header_row=0,
                friendly_name="Customers",
                description="",
                columns=[
                    ColumnSpec(column_id="id", name="id", dtype="int", description=""),
                    ColumnSpec(column_id="name", name="name", dtype="string", description=""),
                ],
            ),
        ],
        links=[
            LinkSpec(
                file_a_id=o_id,
                col_a="customer_id",
                file_b_id=c_id,
                col_b="id",
                direction="many_to_one",
                summary="orders -> customers",
            )
        ],
        overview="Two related tables.",
    )

    apply_approval(
        session_id="S1",
        user_id=user_id,
        payload=payload,
        settings=settings,
        files_repo=FileRepository(conn),
        schemas_repo=SchemaRepository(conn),
        links_repo=LinkRepository(conn),
        sessions_repo=SessionRepository(conn),
        events_repo=ProcessingEventRepository(conn),
    )

    session = SessionRepository(conn).get("S1")
    assert session is not None
    assert session.discovery_status == "approved"
    assert session.status == "ready"
    assert session.overview == "Two related tables."

    links = LinkRepository(conn).list_for_session("S1")
    assert len(links) == 1
    assert links[0].direction == "many_to_one"


def test_preview_rows_falls_back_to_raw(
    settings: Settings, conn, tmp_path: Path, user_id: str
) -> None:
    fid = _seed_csv(
        settings, conn, tmp_path, "x.csv", "id,name\n1,Ada\n2,Bea\n", user_id=user_id
    )
    data = preview_rows(file_id=fid, limit=10, files_repo=FileRepository(conn))
    assert data["file_id"] == fid
    assert data["columns"] == ["c0", "c1"]
    assert data["rows"][0] == ["id", "name"]
    assert data["rows"][1] == ["1", "Ada"]
