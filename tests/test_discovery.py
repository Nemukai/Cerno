from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from cerno.config import Settings
from cerno.db import connect_memory
from cerno.llm import LLMClient
from cerno.repositories import (
    FileRepository,
    LinkRepository,
    ProcessingEventRepository,
    SessionRepository,
)
from cerno.services.discovery import DiscoveryError, run_discovery
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


def _resp(payload: dict[str, Any]) -> httpx.Response:
    body = {
        "id": "resp_1",
        "model": "gpt-5.4",
        "status": "completed",
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(payload)}],
            }
        ],
    }
    return httpx.Response(
        status_code=200, json=body, request=httpx.Request("POST", "http://test")
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path / "cerno",
        llm_api_key="test-key",
        llm_base_url="https://example.test/api/v1",
        llm_model="test-model",
    )


@pytest.fixture
def conn():
    c = connect_memory()
    yield c
    c.close()


def _seed_csv(
    settings: Settings, conn, tmp_path: Path, name: str, content: str
) -> str:
    files_repo = FileRepository(conn)
    sessions = SessionRepository(conn)
    if not sessions.get("S1"):
        sessions.create("test", session_id="S1")
    csv = tmp_path / name
    csv.write_text(content, encoding="utf-8")
    results = ingest_file(
        source_path=csv,
        original_filename=name,
        session_id="S1",
        settings=settings,
        files_repo=files_repo,
    )
    return results[0].file.id


async def test_run_discovery_persists_metadata_and_links(
    settings: Settings, conn, tmp_path: Path
) -> None:
    o_id = _seed_csv(
        settings,
        conn,
        tmp_path,
        "orders.csv",
        "Q1 Report\n\norder_id,customer_id\n1,10\n2,11\n",
    )
    c_id = _seed_csv(
        settings, conn, tmp_path, "customers.csv", "id,name\n10,Ada\n11,Bea\n"
    )

    payload = {
        "files": [
            {
                "file_id": o_id,
                "friendly_name": "Orders",
                "description": "Order lines",
                "header_row": 2,
                "columns": [
                    {"column_id": "order_id", "name": "order_id", "description": "PK", "dtype": "int"},
                    {"column_id": "customer_id", "name": "customer_id", "description": "FK", "dtype": "int"},
                ],
            },
            {
                "file_id": c_id,
                "friendly_name": "Customers",
                "description": "Customer master",
                "header_row": 0,
                "columns": [
                    {"column_id": "id", "name": "id", "description": "PK", "dtype": "int"},
                    {"column_id": "name", "name": "name", "description": "name", "dtype": "string"},
                ],
            },
        ],
        "links": [
            {
                "file_a_id": o_id,
                "col_a": "customer_id",
                "file_b_id": c_id,
                "col_b": "id",
                "direction": "many_to_one",
                "summary": "orders -> customers",
            }
        ],
        "overview": "Orders reference customers.",
    }

    transport = FakeTransport([_resp(payload)])
    llm = LLMClient(settings=settings, transport=transport)

    result = await run_discovery(
        session_id="S1",
        settings=settings,
        files_repo=FileRepository(conn),
        sessions_repo=SessionRepository(conn),
        links_repo=LinkRepository(conn),
        events_repo=ProcessingEventRepository(conn),
        llm_client=llm,
    )

    assert len(result.files) == 2
    assert result.overview == "Orders reference customers."

    files_repo = FileRepository(conn)
    o = files_repo.get(o_id)
    assert o is not None
    assert o.header_row == 2
    assert o.friendly_name == "Orders"

    links = LinkRepository(conn).list_for_session("S1")
    assert len(links) == 1
    assert links[0].direction == "many_to_one"

    session = SessionRepository(conn).get("S1")
    assert session is not None
    assert session.discovery_status == "pending_review"

    events = ProcessingEventRepository(conn).list_for_session("S1")
    kinds = [e.kind for e in events]
    assert "started" in kinds
    assert "calling_llm" in kinds
    assert "done" in kinds


async def test_run_discovery_marks_failed_when_llm_returns_no_files(
    settings: Settings, conn, tmp_path: Path
) -> None:
    _seed_csv(settings, conn, tmp_path, "orders.csv", "id\n1\n2\n")
    transport = FakeTransport(
        [_resp({"files": [{"file_id": "ghost", "friendly_name": "x", "description": "", "header_row": 0, "columns": []}], "links": [], "overview": ""})]
    )
    llm = LLMClient(settings=settings, transport=transport)

    with pytest.raises(DiscoveryError):
        await run_discovery(
            session_id="S1",
            settings=settings,
            files_repo=FileRepository(conn),
            sessions_repo=SessionRepository(conn),
            links_repo=LinkRepository(conn),
            events_repo=ProcessingEventRepository(conn),
            llm_client=llm,
        )

    session = SessionRepository(conn).get("S1")
    assert session is not None
    assert session.discovery_status == "failed"
