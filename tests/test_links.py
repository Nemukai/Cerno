from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

from cerno.config import Settings
from cerno.db import connect_memory
from cerno.llm import LLMClient
from cerno.repositories import (
    FileRepository,
    LinkRepository,
    SchemaRepository,
    SessionRepository,
)
from cerno.services.ingest import ingest_file
from cerno.services.links import (
    LinkDiscoveryError,
    build_column_profiles,
    compute_candidates,
    discover_links,
    parse_llm_response,
)


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


def _write_csv(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _seed_three_file_session(
    tmp_path: Path, settings: Settings, conn
) -> tuple[str, dict[str, Any]]:
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    schemas = SchemaRepository(conn)
    session = sessions.create("s")

    customers = tmp_path / "customers.csv"
    _write_csv(
        customers,
        "id,name,city\n" + "\n".join(f"{i},Name{i},Delhi" for i in range(1, 31)) + "\n",
    )
    orders = tmp_path / "orders.csv"
    _write_csv(
        orders,
        "order_id,customer_id,amount\n"
        + "\n".join(f"{1000 + i},{(i % 30) + 1},{i * 10.5}" for i in range(60))
        + "\n",
    )
    regions = tmp_path / "regions.csv"
    _write_csv(
        regions,
        "region_id,region_name\n1,North\n2,South\n3,East\n4,West\n5,Central\n6,NE\n",
    )

    customer_file = ingest_file(
        source_path=customers,
        original_filename="customers.csv",
        session_id=session.id,
        settings=settings,
        files_repo=files,
        schemas_repo=schemas,
    )[0]
    order_file = ingest_file(
        source_path=orders,
        original_filename="orders.csv",
        session_id=session.id,
        settings=settings,
        files_repo=files,
        schemas_repo=schemas,
    )[0]
    region_file = ingest_file(
        source_path=regions,
        original_filename="regions.csv",
        session_id=session.id,
        settings=settings,
        files_repo=files,
        schemas_repo=schemas,
    )[0]

    return session.id, {
        "customers": customer_file,
        "orders": order_file,
        "regions": region_file,
    }


def test_build_profiles_excludes_floats_and_sparse(
    tmp_path: Path, settings: Settings, conn
) -> None:
    _, files = _seed_three_file_session(tmp_path, settings, conn)
    schemas_repo = SchemaRepository(conn)
    schemas = {
        f.file.id: schemas_repo.get(f.file.id, 1) for f in files.values()
    }
    frames = {f.file.id: pl.read_parquet(f.file.parquet_path) for f in files.values()}
    profiles = build_column_profiles(
        files=[f.file for f in files.values()],
        schemas={k: v for k, v in schemas.items() if v is not None},  # type: ignore[misc]
        frames=frames,
        settings=settings,
    )
    pairs = {(p.filename, p.column) for p in profiles}
    # amount is float, must be excluded
    assert ("orders.csv", "amount") not in pairs
    # id columns must be present
    assert ("customers.csv", "id") in pairs
    assert ("orders.csv", "customer_id") in pairs


def test_compute_candidates_finds_customer_orders_link(
    tmp_path: Path, settings: Settings, conn
) -> None:
    _, files = _seed_three_file_session(tmp_path, settings, conn)
    schemas_repo = SchemaRepository(conn)
    schemas = {
        f.file.id: schemas_repo.get(f.file.id, 1) for f in files.values()
    }
    frames = {f.file.id: pl.read_parquet(f.file.parquet_path) for f in files.values()}
    files_by_id = {f.file.id: f.file for f in files.values()}
    profiles = build_column_profiles(
        files=[f.file for f in files.values()],
        schemas={k: v for k, v in schemas.items() if v is not None},  # type: ignore[misc]
        frames=frames,
        settings=settings,
    )
    candidates = compute_candidates(
        profiles=profiles, files_by_id=files_by_id, settings=settings
    )
    by_cols = {(c.col_a, c.col_b) for c in candidates}
    by_cols_rev = {(c.col_b, c.col_a) for c in candidates}
    assert ("customer_id", "id") in by_cols or ("id", "customer_id") in by_cols_rev


def test_compute_candidates_infers_many_to_one_direction(
    tmp_path: Path, settings: Settings, conn
) -> None:
    _, files = _seed_three_file_session(tmp_path, settings, conn)
    schemas_repo = SchemaRepository(conn)
    schemas = {
        f.file.id: schemas_repo.get(f.file.id, 1) for f in files.values()
    }
    frames = {f.file.id: pl.read_parquet(f.file.parquet_path) for f in files.values()}
    files_by_id = {f.file.id: f.file for f in files.values()}
    profiles = build_column_profiles(
        files=[f.file for f in files.values()],
        schemas={k: v for k, v in schemas.items() if v is not None},  # type: ignore[misc]
        frames=frames,
        settings=settings,
    )
    candidates = compute_candidates(
        profiles=profiles, files_by_id=files_by_id, settings=settings
    )
    orders_to_customers = [
        c
        for c in candidates
        if {c.file_a.filename, c.file_b.filename} == {"orders.csv", "customers.csv"}
    ]
    assert orders_to_customers
    cand = orders_to_customers[0]
    assert cand.direction == "many_to_one"
    assert cand.file_b.filename == "customers.csv"
    assert cand.col_b == "id"


def test_parse_llm_response_strips_fence() -> None:
    body = (
        "```json\n"
        '{"links": [{"file_a_id": "a", "col_a": "x", "file_b_id": "b", '
        '"col_b": "y", "direction": "many_to_one", "summary": "ok", "confidence": 0.9}]}'
        "\n```"
    )
    entries = parse_llm_response(body)
    assert len(entries) == 1
    assert entries[0]["col_a"] == "x"


def test_parse_llm_response_raises_on_invalid_json() -> None:
    with pytest.raises(LinkDiscoveryError):
        parse_llm_response("not json at all")


def test_parse_llm_response_raises_when_missing_links_key() -> None:
    with pytest.raises(LinkDiscoveryError):
        parse_llm_response('{"something": []}')


async def test_discover_links_end_to_end(
    tmp_path: Path, settings: Settings, conn
) -> None:
    session_id, files = _seed_three_file_session(tmp_path, settings, conn)
    customer_id = files["customers"].file.id
    order_id = files["orders"].file.id

    llm_body = json.dumps(
        {
            "links": [
                {
                    "file_a_id": order_id,
                    "col_a": "customer_id",
                    "file_b_id": customer_id,
                    "col_b": "id",
                    "direction": "many_to_one",
                    "summary": "Each order references one customer.",
                    "confidence": 0.95,
                }
            ]
        }
    )
    transport = FakeTransport([_assistant_response(llm_body)])
    llm_client = LLMClient(settings=settings, transport=transport)

    links = await discover_links(
        session_id=session_id,
        settings=settings,
        files_repo=FileRepository(conn),
        schemas_repo=SchemaRepository(conn),
        links_repo=LinkRepository(conn),
        llm_client=llm_client,
    )
    assert len(links) == 1
    link = links[0]
    assert link.col_a == "customer_id"
    assert link.col_b == "id"
    assert link.direction == "many_to_one"
    assert link.summary == "Each order references one customer."
    assert link.score >= 0.9

    sent_body = transport.requests[0]["body"]
    user_msg = sent_body["messages"][1]["content"]
    assert "customers.csv" in user_msg
    assert "orders.csv" in user_msg
    assert "sample_rows" in user_msg


async def test_discover_links_requires_api_key(
    tmp_path: Path, conn
) -> None:
    settings_no_key = Settings(data_root=tmp_path / "cerno", llm_api_key="")
    sessions = SessionRepository(conn)
    session = sessions.create("s")
    llm_client = LLMClient(settings=settings_no_key, transport=FakeTransport([]))
    with pytest.raises(LinkDiscoveryError):
        await discover_links(
            session_id=session.id,
            settings=settings_no_key,
            files_repo=FileRepository(conn),
            schemas_repo=SchemaRepository(conn),
            links_repo=LinkRepository(conn),
            llm_client=llm_client,
        )


async def test_discover_links_returns_empty_for_single_file(
    tmp_path: Path, settings: Settings, conn
) -> None:
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    schemas = SchemaRepository(conn)
    session = sessions.create("s")

    only = tmp_path / "only.csv"
    _write_csv(only, "id\n1\n2\n3\n4\n5\n")
    ingest_file(
        source_path=only,
        original_filename="only.csv",
        session_id=session.id,
        settings=settings,
        files_repo=files,
        schemas_repo=schemas,
    )

    llm_client = LLMClient(settings=settings, transport=FakeTransport([]))
    links = await discover_links(
        session_id=session.id,
        settings=settings,
        files_repo=files,
        schemas_repo=schemas,
        links_repo=LinkRepository(conn),
        llm_client=llm_client,
    )
    assert links == []


async def test_discover_links_skips_llm_hallucinations(
    tmp_path: Path, settings: Settings, conn
) -> None:
    session_id, _files = _seed_three_file_session(tmp_path, settings, conn)
    body = json.dumps(
        {
            "links": [
                {
                    "file_a_id": "bogus-a",
                    "col_a": "nope",
                    "file_b_id": "bogus-b",
                    "col_b": "nope",
                    "direction": "many_to_one",
                    "summary": "fake",
                    "confidence": 0.9,
                }
            ]
        }
    )
    transport = FakeTransport([_assistant_response(body)])
    llm_client = LLMClient(settings=settings, transport=transport)
    links = await discover_links(
        session_id=session_id,
        settings=settings,
        files_repo=FileRepository(conn),
        schemas_repo=SchemaRepository(conn),
        links_repo=LinkRepository(conn),
        llm_client=llm_client,
    )
    assert links == []
