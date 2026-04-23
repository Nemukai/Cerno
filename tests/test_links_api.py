from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from cerno.api.deps import get_conn, get_llm_client
from cerno.config import Settings, get_settings, reset_settings
from cerno.db import connect
from cerno.llm import LLMClient
from cerno.main import create_app
from cerno.repositories import (
    FileRepository,
    LinkRepository,
    SchemaRepository,
    SessionRepository,
)
from cerno.services.ingest import ingest_file


class FakeTransport:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        self.requests.append({"url": url, "body": json, "headers": headers})
        return self._responses.pop(0)


def _assistant_response(content: str) -> httpx.Response:
    request = httpx.Request("POST", "http://test")
    body = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ]
    }
    return httpx.Response(status_code=200, json=body, request=request)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    reset_settings()
    s = Settings(
        data_root=tmp_path / "cerno",
        llm_api_key="test-key",
        llm_base_url="https://example.test/api/v1",
        llm_model="test-model",
    )
    s.data_root.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def client(settings: Settings, tmp_path: Path):
    app = create_app()

    def override_settings() -> Settings:
        return settings

    def override_conn():
        conn = connect(settings)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_conn] = override_conn
    yield app, TestClient(app)
    reset_settings()


def _seed_session_with_files(settings: Settings, tmp_path: Path) -> tuple[str, str, str]:
    conn = connect(settings)
    try:
        sessions = SessionRepository(conn)
        files_repo = FileRepository(conn)
        schemas_repo = SchemaRepository(conn)
        session = sessions.create("s")

        customers = tmp_path / "customers.csv"
        customers.write_text(
            "id,name\n" + "\n".join(f"{i},Name{i}" for i in range(1, 31)) + "\n",
            encoding="utf-8",
        )
        orders = tmp_path / "orders.csv"
        orders.write_text(
            "order_id,customer_id\n"
            + "\n".join(f"{1000+i},{(i % 30) + 1}" for i in range(60))
            + "\n",
            encoding="utf-8",
        )

        cust_file = ingest_file(
            source_path=customers,
            original_filename="customers.csv",
            session_id=session.id,
            settings=settings,
            files_repo=files_repo,
            schemas_repo=schemas_repo,
        )[0].file
        order_file = ingest_file(
            source_path=orders,
            original_filename="orders.csv",
            session_id=session.id,
            settings=settings,
            files_repo=files_repo,
            schemas_repo=schemas_repo,
        )[0].file
        conn.commit()
        return session.id, cust_file.id, order_file.id
    finally:
        conn.close()


def test_discover_links_endpoint_creates_links(
    settings: Settings, tmp_path: Path, client
) -> None:
    app, http = client
    session_id, cust_id, order_id = _seed_session_with_files(settings, tmp_path)

    body = json.dumps(
        {
            "links": [
                {
                    "file_a_id": order_id,
                    "col_a": "customer_id",
                    "file_b_id": cust_id,
                    "col_b": "id",
                    "direction": "many_to_one",
                    "summary": "Each order references one customer.",
                    "confidence": 0.96,
                }
            ]
        }
    )
    transport = FakeTransport([_assistant_response(body)])
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(
        settings=settings, transport=transport
    )

    response = http.post(f"/sessions/{session_id}/discover-links")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["summary"] == "Each order references one customer."


def test_review_endpoint_records_action(
    settings: Settings, tmp_path: Path, client
) -> None:
    _app, http = client
    session_id, cust_id, order_id = _seed_session_with_files(settings, tmp_path)

    # create a link directly
    conn = connect(settings)
    try:
        link = LinkRepository(conn).create(
            session_id=session_id,
            file_a=order_id,
            col_a="customer_id",
            file_b=cust_id,
            col_b="id",
            overlap=0.9,
            direction="many_to_one",
            score=0.9,
        )
        conn.commit()
    finally:
        conn.close()

    response = http.post(
        f"/links/{link.id}/review", json={"action": "reject", "notes": "nope"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "reject"
    assert body["notes"] == "nope"


def test_skip_review_endpoint_auto_confirms(
    settings: Settings, tmp_path: Path, client
) -> None:
    _app, http = client
    session_id, cust_id, order_id = _seed_session_with_files(settings, tmp_path)

    conn = connect(settings)
    try:
        links_repo = LinkRepository(conn)
        for i in range(2):
            links_repo.create(
                session_id=session_id,
                file_a=order_id,
                col_a=f"ka_{i}",
                file_b=cust_id,
                col_b=f"kb_{i}",
                overlap=0.9,
                direction="many_to_one",
                score=0.9,
            )
        conn.commit()
    finally:
        conn.close()

    response = http.post(f"/sessions/{session_id}/skip-review")
    assert response.status_code == 200
    assert response.json()["auto_confirmed_count"] == 2


def test_list_links_endpoint(
    settings: Settings, tmp_path: Path, client
) -> None:
    _app, http = client
    session_id, cust_id, order_id = _seed_session_with_files(settings, tmp_path)

    conn = connect(settings)
    try:
        LinkRepository(conn).create(
            session_id=session_id,
            file_a=order_id,
            col_a="customer_id",
            file_b=cust_id,
            col_b="id",
            overlap=0.96,
            direction="many_to_one",
            score=0.96,
            summary="one order per customer row",
        )
        conn.commit()
    finally:
        conn.close()

    response = http.get(f"/sessions/{session_id}/links")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["summary"] == "one order per customer row"
