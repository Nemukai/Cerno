from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from cerno.config import Settings
from cerno.db import connect_memory
from cerno.repositories import (
    AnomalyRepository,
    FileRepository,
    LinkRepository,
    SchemaRepository,
    SessionRepository,
)
from cerno.services.anomalies import detect_anomalies, persist_anomalies
from cerno.services.ingest import ingest_file


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_root=tmp_path / "cerno")


@pytest.fixture
def conn():
    c = connect_memory()
    yield c
    c.close()


def _write_csv(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _seed(tmp_path: Path, settings: Settings, conn) -> tuple[str, dict[str, str]]:
    sessions = SessionRepository(conn)
    files_repo = FileRepository(conn)
    schemas_repo = SchemaRepository(conn)
    session = sessions.create("s")

    orders = tmp_path / "orders.csv"
    rows = [f"{1000 + i},{i + 1},{10.0 + i * 0.1}" for i in range(20)]
    rows.append("1099,1,9999.0")
    _write_csv(orders, "order_id,customer_id,amount\n" + "\n".join(rows) + "\n")

    customers = tmp_path / "customers.csv"
    cust_rows = [f"{i + 1},Name{i + 1},{'N' if i < 19 else 'Z'}" for i in range(20)]
    _write_csv(customers, "id,name,tier\n" + "\n".join(cust_rows) + "\n")

    o = ingest_file(
        source_path=orders,
        original_filename="orders.csv",
        session_id=session.id,
        settings=settings,
        files_repo=files_repo,
        schemas_repo=schemas_repo,
    )[0].file
    c = ingest_file(
        source_path=customers,
        original_filename="customers.csv",
        session_id=session.id,
        settings=settings,
        files_repo=files_repo,
        schemas_repo=schemas_repo,
    )[0].file
    return session.id, {"orders": o.id, "customers": c.id}


def test_numeric_mad_flags_outlier(
    tmp_path: Path, settings: Settings, conn
) -> None:
    session_id, files = _seed(tmp_path, settings, conn)
    drafts = detect_anomalies(
        session_id=session_id,
        settings=settings,
        files_repo=FileRepository(conn),
        schemas_repo=SchemaRepository(conn),
        links_repo=LinkRepository(conn),
    )
    mad = [d for d in drafts if d.detector == "numeric_mad"]
    assert mad, "expected at least one numeric_mad flag"
    orders_id = files["orders"]
    frame = pl.read_parquet(
        FileRepository(conn).get(orders_id).parquet_path  # type: ignore[union-attr]
    )
    outlier_row = frame["amount"].to_list().index(9999.0)
    flagged_rows = {d.row_id for d in mad if d.file_id == orders_id}
    assert outlier_row in flagged_rows
    normal_row = frame["amount"].to_list().index(10.0)
    assert normal_row not in flagged_rows


def test_rare_value_flags_below_threshold(
    tmp_path: Path, settings: Settings, conn
) -> None:
    session_id, files = _seed(tmp_path, settings, conn)
    # At default 1% threshold, tier=Z (1/20=5%) is NOT rare.
    drafts_default = detect_anomalies(
        session_id=session_id,
        settings=settings,
        files_repo=FileRepository(conn),
        schemas_repo=SchemaRepository(conn),
        links_repo=LinkRepository(conn),
    )
    cust_rare_default = [
        d for d in drafts_default
        if d.detector == "rare_value" and d.file_id == files["customers"]
    ]
    assert not any("'Z'" in d.reason_plain for d in cust_rare_default)

    # At 10% threshold, Z at 5% IS rare.
    low_settings = Settings(
        data_root=settings.data_root, anomaly_rare_threshold=0.1
    )
    drafts_low = detect_anomalies(
        session_id=session_id,
        settings=low_settings,
        files_repo=FileRepository(conn),
        schemas_repo=SchemaRepository(conn),
        links_repo=LinkRepository(conn),
    )
    cust_rare_low = [
        d for d in drafts_low
        if d.detector == "rare_value" and d.file_id == files["customers"]
    ]
    assert any("'Z'" in d.reason_plain for d in cust_rare_low)


def test_key_overlap_requires_confirmed_link(
    tmp_path: Path, settings: Settings, conn
) -> None:
    _seed(tmp_path, settings, conn)
    # Add an orphan: orders references customer_id=999 which doesn't exist.
    orders_path = tmp_path / "orders_v2.csv"
    rows = [f"{1000 + i},{i + 1},{10.0 + i * 0.1}" for i in range(19)]
    rows.append("1099,999,50.0")
    _write_csv(orders_path, "order_id,customer_id,amount\n" + "\n".join(rows) + "\n")
    cust_path = tmp_path / "customers_v2.csv"
    cust_rows = [f"{i + 1},Name{i + 1}" for i in range(20)]
    _write_csv(cust_path, "id,name\n" + "\n".join(cust_rows) + "\n")

    sessions = SessionRepository(conn)
    files_repo = FileRepository(conn)
    schemas_repo = SchemaRepository(conn)
    links_repo = LinkRepository(conn)
    session2 = sessions.create("s2")
    o = ingest_file(
        source_path=orders_path,
        original_filename="orders.csv",
        session_id=session2.id,
        settings=settings,
        files_repo=files_repo,
        schemas_repo=schemas_repo,
    )[0].file
    c = ingest_file(
        source_path=cust_path,
        original_filename="customers.csv",
        session_id=session2.id,
        settings=settings,
        files_repo=files_repo,
        schemas_repo=schemas_repo,
    )[0].file

    link = links_repo.create(
        session_id=session2.id,
        file_a=o.id,
        col_a="customer_id",
        file_b=c.id,
        col_b="id",
        overlap=0.95,
        direction="many_to_one",
        score=0.95,
    )

    drafts_no_review = detect_anomalies(
        session_id=session2.id,
        settings=settings,
        files_repo=files_repo,
        schemas_repo=schemas_repo,
        links_repo=links_repo,
    )
    assert not any(d.detector == "key_overlap" for d in drafts_no_review)

    links_repo.add_review(link_id=link.id, action="confirm")
    drafts_confirmed = detect_anomalies(
        session_id=session2.id,
        settings=settings,
        files_repo=files_repo,
        schemas_repo=schemas_repo,
        links_repo=links_repo,
    )
    key_flags = [d for d in drafts_confirmed if d.detector == "key_overlap"]
    assert len(key_flags) == 1
    assert key_flags[0].file_id == o.id
    assert key_flags[0].score_normalized == 80.0


def test_persist_anomalies_assigns_ids(
    tmp_path: Path, settings: Settings, conn
) -> None:
    session_id, _files = _seed(tmp_path, settings, conn)
    drafts = detect_anomalies(
        session_id=session_id,
        settings=settings,
        files_repo=FileRepository(conn),
        schemas_repo=SchemaRepository(conn),
        links_repo=LinkRepository(conn),
    )
    persisted = persist_anomalies(
        anomalies_repo=AnomalyRepository(conn),
        session_id=session_id,
        drafts=drafts,
    )
    assert len(persisted) == len(drafts)
    assert all(a.id for a in persisted)
    assert len({a.id for a in persisted}) == len(persisted)
