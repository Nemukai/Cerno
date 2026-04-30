from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import polars as pl

from cerno.api.chat import router as chat_router
from cerno.api.deps import get_conn, get_current_user, get_llm_client
from cerno.config import Settings, get_settings, reset_settings
from cerno.db import connect
from cerno.llm import LLMClient
from cerno.repositories import (
    ChatRepository,
    DashboardRepository,
    NotebookRepository,
    SessionRepository,
    UserRepository,
)

from .conftest import install_file


class FakeTransport:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        self.requests.append({"url": url, "body": json, "headers": headers})
        return self._responses.pop(0)


_RESP_COUNTER = {"n": 0}


def _response(body: dict[str, Any], status: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "http://test")
    return httpx.Response(status_code=status, json=body, request=request)


def _next_response_id() -> str:
    _RESP_COUNTER["n"] += 1
    return f"resp_{_RESP_COUNTER['n']}"


def _assistant_body(
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    finish: str = "completed",
) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    if tool_calls:
        for tc in tool_calls:
            output.append(
                {
                    "id": f"fc_{tc['call_id']}",
                    "type": "function_call",
                    "call_id": tc["call_id"],
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                    "status": "completed",
                }
            )
    if content:
        output.append(
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}],
            }
        )
    return {
        "id": _next_response_id(),
        "model": "test-model",
        "status": finish,
        "output": output,
    }


def _tc(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"call_id": call_id, "name": name, "arguments": json.dumps(args)}


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
def test_user_id(settings: Settings) -> str:
    conn = connect(settings)
    try:
        repo = UserRepository(conn)
        user = repo.upsert_from_google(
            google_sub="chat-test", email="chat@x", name=None, picture=None
        )
        repo.mark_granted(user.id, "TEST")
        conn.commit()
        return user.id
    finally:
        conn.close()


@pytest.fixture
def client(settings: Settings, test_user_id: str):
    app = FastAPI()
    app.include_router(chat_router)

    def override_settings() -> Settings:
        return settings

    def override_conn():
        conn = connect(settings)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def override_user():
        with connect(settings) as _:
            pass
        conn = connect(settings)
        try:
            user = UserRepository(conn).get(test_user_id)
            assert user is not None
            return user
        finally:
            conn.close()

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_conn] = override_conn
    app.dependency_overrides[get_current_user] = override_user
    yield app, TestClient(app)
    reset_settings()


def _seed_session_with_files(
    settings: Settings, tmp_path: Path, user_id: str
) -> str:
    conn = connect(settings)
    try:
        sessions = SessionRepository(conn)
        session = sessions.create("s", user_id=user_id)

        customers_frame = pl.DataFrame(
            {"id": list(range(1, 6)), "name": [f"Name{i}" for i in range(1, 6)]}
        )
        orders_frame = pl.DataFrame(
            {
                "order_id": [1000 + i for i in range(12)],
                "customer_id": [(i % 5) + 1 for i in range(12)],
            }
        )
        install_file(
            conn=conn,
            settings=settings,
            user_id=user_id,
            session_id=session.id,
            filename="customers.csv",
            frame=customers_frame,
        )
        install_file(
            conn=conn,
            settings=settings,
            user_id=user_id,
            session_id=session.id,
            filename="orders.csv",
            frame=orders_frame,
        )
        conn.commit()
        return session.id
    finally:
        conn.close()


def test_chat_spawns_dashboard_page_with_widget(
    settings: Settings, tmp_path: Path, client, test_user_id: str
) -> None:
    app, http = client
    session_id = _seed_session_with_files(settings, tmp_path, test_user_id)

    transport = FakeTransport(
        [
            _response(
                _assistant_body(
                    tool_calls=[_tc("c1", "list_tables", {})],
                    finish="tool_calls",
                )
            ),
            _response(
                _assistant_body(
                    tool_calls=[
                        _tc(
                            "c2",
                            "run_sql",
                            {"sql": "SELECT COUNT(*) AS n FROM orders"},
                        )
                    ],
                    finish="tool_calls",
                )
            ),
            _response(
                _assistant_body(
                    tool_calls=[
                        _tc(
                            "c3",
                            "render_widget",
                            {
                                "kind": "kpi",
                                "title": "Total orders",
                                "data": {"value": 12},
                            },
                        )
                    ],
                    finish="tool_calls",
                )
            ),
            _response(_assistant_body(content="You have 12 orders.")),
        ]
    )
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(
        settings=settings, transport=transport
    )

    response = http.post(
        f"/sessions/{session_id}/chat",
        json={"message": "How many orders do I have?"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant_message"] == "You have 12 orders."
    assert payload["spawned_page_id"] is not None
    assert len(payload["widgets"]) == 1
    assert payload["widgets"][0]["kind"] == "kpi"

    conn = connect(settings)
    try:
        dashboards = DashboardRepository(conn)
        notebook = NotebookRepository(conn)
        chat = ChatRepository(conn)

        dashboard = dashboards.get_for_session(session_id)
        assert dashboard is not None
        pages = dashboards.list_pages(dashboard.id)
        assert len(pages) == 1
        assert pages[0].id == payload["spawned_page_id"]
        assert pages[0].kind == "question"

        cells = notebook.list_for_page(pages[0].id)
        assert len(cells) == 1
        assert cells[0].kind == "widget"
        assert cells[0].output is not None
        assert cells[0].output["widget"]["kind"] == "kpi"

        turns = chat.list_turns(session_id)
        assert len(turns) == 1
        assert turns[0].state == "complete"
        assert turns[0].spawned_page_id == payload["spawned_page_id"]

        messages = chat.list_messages(turns[0].id)
        roles = [m.role for m in messages]
        # user + 4 assistant + 3 tool
        assert roles.count("user") == 1
        assert roles.count("assistant") == 4
        assert roles.count("tool") == 3

        tool_messages = [m for m in messages if m.role == "tool"]
        run_sql_result = next(
            m for m in tool_messages if m.tool_result and "rows" in m.tool_result
        )
        assert run_sql_result.tool_result is not None
        assert run_sql_result.tool_result["rows"][0][0] == 12
    finally:
        conn.close()


def test_chat_text_only_reply_no_page(
    settings: Settings, tmp_path: Path, client, test_user_id: str
) -> None:
    app, http = client
    session_id = _seed_session_with_files(settings, tmp_path, test_user_id)

    transport = FakeTransport(
        [_response(_assistant_body(content="Two files: customers and orders."))]
    )
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(
        settings=settings, transport=transport
    )

    response = http.post(
        f"/sessions/{session_id}/chat", json={"message": "What files do I have?"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["spawned_page_id"] is None
    assert payload["widgets"] == []


def test_list_turns_and_messages_endpoints(
    settings: Settings, tmp_path: Path, client, test_user_id: str
) -> None:
    app, http = client
    session_id = _seed_session_with_files(settings, tmp_path, test_user_id)

    transport = FakeTransport([_response(_assistant_body(content="hi"))])
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(
        settings=settings, transport=transport
    )
    http.post(f"/sessions/{session_id}/chat", json={"message": "hello"})

    turns_response = http.get(f"/sessions/{session_id}/turns")
    assert turns_response.status_code == 200
    turns = turns_response.json()
    assert len(turns) == 1
    turn_id = turns[0]["id"]

    messages_response = http.get(f"/turns/{turn_id}/messages")
    assert messages_response.status_code == 200
    messages = messages_response.json()
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert "assistant" in roles
