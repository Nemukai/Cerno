from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cerno.api.deps import get_conn
from cerno.api.sessions import router as sessions_router
from cerno.config import Settings, get_settings, reset_settings
from cerno.db import connect


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    reset_settings()
    s = Settings(data_root=tmp_path / "cerno")
    s.data_root.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def client(settings: Settings):
    app = FastAPI()
    app.include_router(sessions_router)

    def override_settings() -> Settings:
        return settings

    def override_conn():
        c = connect(settings)
        try:
            yield c
            c.commit()
        finally:
            c.close()

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_conn] = override_conn
    yield TestClient(app)
    reset_settings()


def test_session_lifecycle_upload_and_preview(
    settings: Settings, client: TestClient
) -> None:
    create = client.post("/sessions", json={"name": "demo"})
    assert create.status_code == 200
    session_id = create.json()["id"]

    upload = client.post(
        f"/sessions/{session_id}/files",
        files={
            "uploads": ("orders.csv", io.BytesIO(b"order_id,amount\n1,10.5\n2,20.0\n"), "text/csv"),
        },
    )
    assert upload.status_code == 200, upload.text
    files = upload.json()["files"]
    assert len(files) == 1
    file_id = files[0]["id"]

    listing = client.get(f"/sessions/{session_id}/files")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    discovery = client.get(f"/sessions/{session_id}/discovery")
    assert discovery.status_code == 200
    body = discovery.json()
    assert body["status"] == "empty"
    assert len(body["files"]) == 1
    assert body["files"][0]["file_id"] == file_id

    preview = client.get(f"/files/{file_id}/preview?limit=10")
    assert preview.status_code == 200
    pv = preview.json()
    assert pv["columns"] == ["c0", "c1"]
    assert pv["rows"][0] == ["order_id", "amount"]


def test_approve_schema_reingests_and_returns_columns(
    settings: Settings, client: TestClient
) -> None:
    session_id = client.post("/sessions", json={"name": "demo"}).json()["id"]
    upload = client.post(
        f"/sessions/{session_id}/files",
        files={
            "uploads": (
                "orders.csv",
                io.BytesIO(b"Header garbage\n\norder_id,amount\n1,10.5\n2,20.0\n"),
                "text/csv",
            ),
        },
    )
    file_id = upload.json()["files"][0]["id"]

    body = {
        "files": [
            {
                "file_id": file_id,
                "friendly_name": "Orders",
                "description": "test orders",
                "header_row": 2,
                "columns": [
                    {"column_id": "order_id", "name": "order_id", "description": "PK", "dtype": "int"},
                    {"column_id": "amount", "name": "amount", "description": "$", "dtype": "float"},
                ],
            }
        ],
        "links": [],
        "overview": "single file session",
    }
    approve = client.post(f"/sessions/{session_id}/approve-schema", json=body)
    assert approve.status_code == 200, approve.text
    payload = approve.json()
    assert payload["status"] == "approved"
    assert payload["files"][0]["columns"][0]["dtype"] == "int"

    preview = client.get(f"/files/{file_id}/preview?limit=10")
    assert preview.status_code == 200
    pv = preview.json()
    assert pv["columns"] == ["order_id", "amount"]
    assert pv["rows"][0] == [1, 10.5]


def test_upload_dedups_identical_files(settings: Settings, client: TestClient) -> None:
    session_id = client.post("/sessions", json={"name": "demo"}).json()["id"]
    payload = b"order_id,amount\n1,10.5\n2,20.0\n"

    first = client.post(
        f"/sessions/{session_id}/files",
        files={"uploads": ("orders.csv", io.BytesIO(payload), "text/csv")},
    )
    assert first.status_code == 200
    first_id = first.json()["files"][0]["id"]

    second = client.post(
        f"/sessions/{session_id}/files",
        files={"uploads": ("orders-copy.csv", io.BytesIO(payload), "text/csv")},
    )
    assert second.status_code == 200
    second_id = second.json()["files"][0]["id"]

    assert first_id == second_id, "duplicate hash should return existing file id"

    listing = client.get(f"/sessions/{session_id}/files")
    assert len(listing.json()) == 1


def test_delete_file_removes_record_and_resets_discovery(
    settings: Settings, client: TestClient
) -> None:
    session_id = client.post("/sessions", json={"name": "demo"}).json()["id"]
    upload = client.post(
        f"/sessions/{session_id}/files",
        files={"uploads": ("orders.csv", io.BytesIO(b"order_id,amount\n1,10.5\n"), "text/csv")},
    )
    file_id = upload.json()["files"][0]["id"]

    raw_path = settings.raw_parquet_path(session_id, file_id)
    assert raw_path.exists()

    delete = client.delete(f"/files/{file_id}")
    assert delete.status_code == 204
    assert not raw_path.exists()

    listing = client.get(f"/sessions/{session_id}/files")
    assert listing.json() == []

    missing = client.delete(f"/files/{file_id}")
    assert missing.status_code == 404
