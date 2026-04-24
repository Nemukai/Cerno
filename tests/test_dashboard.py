from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cerno.api.dashboards import router as dashboards_router
from cerno.api.deps import get_conn
from cerno.config import Settings, get_settings, reset_settings
from cerno.db import connect, connect_memory
from cerno.repositories import (
    AnomalyRepository,
    DashboardRepository,
    FileRepository,
    LinkRepository,
    NotebookRepository,
    SchemaRepository,
    SessionRepository,
)
from cerno.services.anomalies import detect_anomalies, persist_anomalies
from cerno.services.dashboard import generate_overview
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
    links_repo = LinkRepository(conn)
    session = sessions.create("s")

    orders = tmp_path / "orders.csv"
    rows = [f"{1000 + i},{(i % 20) + 1},{10.0 + i * 0.1}" for i in range(20)]
    rows.append("1099,999,9999.0")
    _write_csv(orders, "order_id,customer_id,amount\n" + "\n".join(rows) + "\n")

    customers = tmp_path / "customers.csv"
    cust_rows = [f"{i + 1},Name{i + 1}" for i in range(20)]
    _write_csv(customers, "id,name\n" + "\n".join(cust_rows) + "\n")

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

    link = links_repo.create(
        session_id=session.id,
        file_a=o.id,
        col_a="customer_id",
        file_b=c.id,
        col_b="id",
        overlap=0.95,
        direction="many_to_one",
        score=0.95,
        summary="orders -> customers",
    )
    links_repo.add_review(link_id=link.id, action="confirm")
    return session.id, {"orders": o.id, "customers": c.id}


def _run_pipeline(session_id: str, settings: Settings, conn) -> str:
    drafts = detect_anomalies(
        session_id=session_id,
        settings=settings,
        files_repo=FileRepository(conn),
        schemas_repo=SchemaRepository(conn),
        links_repo=LinkRepository(conn),
    )
    persist_anomalies(
        anomalies_repo=AnomalyRepository(conn),
        session_id=session_id,
        drafts=drafts,
    )
    page = generate_overview(
        session_id=session_id,
        settings=settings,
        files_repo=FileRepository(conn),
        schemas_repo=SchemaRepository(conn),
        links_repo=LinkRepository(conn),
        anomalies_repo=AnomalyRepository(conn),
        dashboards_repo=DashboardRepository(conn),
        notebook_repo=NotebookRepository(conn),
    )
    return page.id


def test_overview_page_widget_order(
    tmp_path: Path, settings: Settings, conn
) -> None:
    session_id, _files = _seed(tmp_path, settings, conn)
    page_id = _run_pipeline(session_id, settings, conn)

    cells = NotebookRepository(conn).list_for_page(page_id)
    kinds = [cell.output["widget"]["kind"] for cell in cells if cell.output]
    # 2 files -> 2 kpi (files) + 1 kpi (links) + bar + table + markdown
    assert kinds == ["kpi", "kpi", "kpi", "bar", "table", "markdown"]

    table_cell = cells[4]
    assert table_cell.output is not None
    table_data = table_cell.output["widget"]["data"]
    assert table_data["columns"] == ["from", "to", "direction", "summary"]
    assert len(table_data["rows"]) == 1
    assert table_data["rows"][0][2] == "many_to_one"

    markdown_cell = cells[5]
    assert markdown_cell.output is not None
    text = markdown_cell.output["widget"]["data"]["text"]
    assert "2 files" in text
    assert "1 confirmed links" in text


def test_overview_is_idempotent(
    tmp_path: Path, settings: Settings, conn
) -> None:
    session_id, _files = _seed(tmp_path, settings, conn)
    first_page = _run_pipeline(session_id, settings, conn)
    second_page = _run_pipeline(session_id, settings, conn)
    assert first_page != second_page
    pages = DashboardRepository(conn).list_pages(
        DashboardRepository(conn).get_for_session(session_id).id  # type: ignore[union-attr]
    )
    assert len([p for p in pages if p.kind == "overview"]) == 1


@pytest.fixture
def api_settings(tmp_path: Path) -> Settings:
    reset_settings()
    s = Settings(data_root=tmp_path / "cerno")
    s.data_root.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def api_client(api_settings: Settings):
    app = FastAPI()
    app.include_router(dashboards_router)

    def override_settings() -> Settings:
        return api_settings

    def override_conn():
        c = connect(api_settings)
        try:
            yield c
            c.commit()
        finally:
            c.close()

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_conn] = override_conn
    yield TestClient(app)
    reset_settings()


def _seed_via_settings(
    tmp_path: Path, api_settings: Settings
) -> tuple[str, dict[str, str]]:
    c = connect(api_settings)
    try:
        session_id, files = _seed(tmp_path, api_settings, c)
        c.commit()
        return session_id, files
    finally:
        c.close()


def test_build_dashboard_endpoint_returns_counts(
    tmp_path: Path, api_settings: Settings, api_client
) -> None:
    session_id, _files = _seed_via_settings(tmp_path, api_settings)
    response = api_client.post(f"/sessions/{session_id}/build-dashboard")
    assert response.status_code == 200
    body = response.json()
    assert "page_id" in body
    assert body["anomaly_count"] >= 1
    assert body["widget_count"] == 6


def test_get_dashboard_returns_pages_and_cells(
    tmp_path: Path, api_settings: Settings, api_client
) -> None:
    session_id, _files = _seed_via_settings(tmp_path, api_settings)
    api_client.post(f"/sessions/{session_id}/build-dashboard")
    response = api_client.get(f"/sessions/{session_id}/dashboard")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["pages"]) == 1
    page = payload["pages"][0]
    assert page["kind"] == "overview"
    cells = payload["cells_by_page"][page["id"]]
    assert len(cells) == 6


def test_anomalies_endpoint_respects_limit(
    tmp_path: Path, api_settings: Settings, api_client
) -> None:
    session_id, _files = _seed_via_settings(tmp_path, api_settings)
    api_client.post(f"/sessions/{session_id}/build-dashboard")
    response = api_client.get(f"/sessions/{session_id}/anomalies?limit=3")
    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 3
