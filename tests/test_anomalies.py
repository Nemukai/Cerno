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

from .conftest import install_file


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
        google_sub="anom-test", email="anom@x", name=None, picture=None
    )
    return user.id


def _seed(settings: Settings, conn, user_id: str) -> tuple[str, dict[str, str]]:
    sessions = SessionRepository(conn)
    session = sessions.create("s", user_id=user_id)

    order_ids = list(range(1000, 1020)) + [1099]
    customer_ids = [i + 1 for i in range(20)] + [1]
    amounts = [10.0 + i * 0.1 for i in range(20)] + [9999.0]
    orders_frame = pl.DataFrame(
        {"order_id": order_ids, "customer_id": customer_ids, "amount": amounts}
    )

    cust_ids = [i + 1 for i in range(20)]
    cust_names = [f"Name{i + 1}" for i in range(20)]
    cust_tiers = ["N"] * 19 + ["Z"]
    customers_frame = pl.DataFrame(
        {"id": cust_ids, "name": cust_names, "tier": cust_tiers}
    )

    o, _ = install_file(
        conn=conn,
        settings=settings,
        user_id=user_id,
        session_id=session.id,
        filename="orders.csv",
        frame=orders_frame,
    )
    c, _ = install_file(
        conn=conn,
        settings=settings,
        user_id=user_id,
        session_id=session.id,
        filename="customers.csv",
        frame=customers_frame,
        column_overrides={"tier": "category"},
    )
    return session.id, {"orders": o.id, "customers": c.id}


def test_numeric_mad_flags_outlier(
    tmp_path: Path, settings: Settings, conn, user_id: str
) -> None:
    session_id, files = _seed(settings, conn, user_id)
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
    tmp_path: Path, settings: Settings, conn, user_id: str
) -> None:
    session_id, files = _seed(settings, conn, user_id)
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
    tmp_path: Path, settings: Settings, conn, user_id: str
) -> None:
    _seed(settings, conn, user_id)

    sessions = SessionRepository(conn)
    files_repo = FileRepository(conn)
    schemas_repo = SchemaRepository(conn)
    links_repo = LinkRepository(conn)
    session2 = sessions.create("s2", user_id=user_id)

    order_ids = list(range(1000, 1019)) + [1099]
    customer_ids = [i + 1 for i in range(19)] + [999]
    amounts = [10.0 + i * 0.1 for i in range(19)] + [50.0]
    orders_frame = pl.DataFrame(
        {"order_id": order_ids, "customer_id": customer_ids, "amount": amounts}
    )
    customers_frame = pl.DataFrame(
        {"id": [i + 1 for i in range(20)], "name": [f"Name{i + 1}" for i in range(20)]}
    )

    o, _ = install_file(
        conn=conn,
        settings=settings,
        user_id=user_id,
        session_id=session2.id,
        filename="orders.csv",
        frame=orders_frame,
    )
    c, _ = install_file(
        conn=conn,
        settings=settings,
        user_id=user_id,
        session_id=session2.id,
        filename="customers.csv",
        frame=customers_frame,
    )

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
    tmp_path: Path, settings: Settings, conn, user_id: str
) -> None:
    session_id, _files = _seed(settings, conn, user_id)
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
