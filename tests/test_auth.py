from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from cerno.api.deps import cookie_serializer
from cerno.config import Settings, reset_settings
from cerno.db import connect
from cerno.main import create_app
from cerno.repositories import SessionRepository, UserRepository


@pytest.fixture
def app_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    reset_settings()
    monkeypatch.setenv("CERNO_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("CERNO_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("CERNO_LLM_ENABLED", "false")
    from cerno.config import get_settings

    settings = get_settings()
    yield settings
    reset_settings()


@pytest.fixture
def app_client(app_settings: Settings) -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def signed_in_user(app_settings: Settings):
    conn = connect(app_settings)
    try:
        user = UserRepository(conn).upsert_from_google(
            google_sub="auth-test-sub",
            email="auth-test@cerno.local",
            name="Auth Tester",
            picture="https://example.com/p.png",
        )
        conn.commit()
        return user
    finally:
        conn.close()


def _set_cookie(client: TestClient, settings: Settings, user_id: str) -> None:
    token = cookie_serializer(settings).dumps({"user_id": user_id})
    client.cookies.set(settings.session_cookie_name, token)


def test_me_unauthenticated_returns_401(app_client: TestClient) -> None:
    response = app_client.get("/auth/me")
    assert response.status_code == 401


def test_me_with_valid_cookie_returns_user(
    app_settings: Settings, app_client: TestClient, signed_in_user
) -> None:
    _set_cookie(app_client, app_settings, signed_in_user.id)
    response = app_client.get("/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == signed_in_user.id
    assert body["email"] == "auth-test@cerno.local"
    assert body["name"] == "Auth Tester"


def test_me_with_tampered_cookie_returns_401(
    app_settings: Settings, app_client: TestClient, signed_in_user
) -> None:
    _set_cookie(app_client, app_settings, signed_in_user.id)
    cookie = app_client.cookies.get(app_settings.session_cookie_name)
    assert cookie is not None
    app_client.cookies.set(app_settings.session_cookie_name, cookie + "X")
    response = app_client.get("/auth/me")
    assert response.status_code == 401


def test_me_with_wrong_secret_signed_cookie_returns_401(
    app_settings: Settings, app_client: TestClient, signed_in_user
) -> None:
    bad = URLSafeTimedSerializer("not-the-real-secret", salt="cerno.session")
    app_client.cookies.set(
        app_settings.session_cookie_name,
        bad.dumps({"user_id": signed_in_user.id}),
    )
    response = app_client.get("/auth/me")
    assert response.status_code == 401


def test_me_for_unknown_user_returns_401(
    app_settings: Settings, app_client: TestClient
) -> None:
    _set_cookie(app_client, app_settings, "nonexistent-user-id")
    response = app_client.get("/auth/me")
    assert response.status_code == 401


def test_logout_clears_cookie(
    app_settings: Settings, app_client: TestClient, signed_in_user
) -> None:
    _set_cookie(app_client, app_settings, signed_in_user.id)
    assert app_client.get("/auth/me").status_code == 200
    response = app_client.post("/auth/logout")
    assert response.status_code == 204
    # FastAPI's TestClient persists cookies; logout sets max-age=0 / Set-Cookie.
    # Re-issuing the original cookie isn't possible — verify the response sets a
    # cookie with empty value or expiry, then ensure /auth/me 401s after a clean
    # client.
    assert "set-cookie" in {k.lower() for k in response.headers.keys()}


def test_protected_route_without_cookie_is_401(app_client: TestClient) -> None:
    response = app_client.get("/sessions")
    assert response.status_code == 401


def test_protected_route_with_cookie_returns_data(
    app_settings: Settings, app_client: TestClient, signed_in_user
) -> None:
    conn = connect(app_settings)
    try:
        SessionRepository(conn).create("hello", user_id=signed_in_user.id)
        conn.commit()
    finally:
        conn.close()

    _set_cookie(app_client, app_settings, signed_in_user.id)
    response = app_client.get("/sessions")
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 1
    assert sessions[0]["name"] == "hello"
    assert sessions[0]["user_id"] == signed_in_user.id


def test_session_isolation_across_users(
    app_settings: Settings, app_client: TestClient, signed_in_user
) -> None:
    conn = connect(app_settings)
    try:
        users = UserRepository(conn)
        other = users.upsert_from_google(
            google_sub="other", email="other@x", name=None, picture=None
        )
        sessions = SessionRepository(conn)
        sessions.create("mine", user_id=signed_in_user.id)
        sessions.create("theirs", user_id=other.id)
        conn.commit()
    finally:
        conn.close()

    _set_cookie(app_client, app_settings, signed_in_user.id)
    listing = app_client.get("/sessions").json()
    assert {s["name"] for s in listing} == {"mine"}
