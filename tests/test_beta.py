from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cerno.api.deps import cookie_serializer
from cerno.config import Settings, reset_settings
from cerno.db import connect
from cerno.main import create_app
from cerno.repositories import BetaCodeRepository, UserRepository


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


def _make_pending_user(settings: Settings, *, google_sub: str, email: str):
    conn = connect(settings)
    try:
        user = UserRepository(conn).upsert_from_google(
            google_sub=google_sub, email=email, name=None, picture=None
        )
        conn.commit()
    finally:
        conn.close()
    return user


def _make_code(
    settings: Settings,
    *,
    code: str,
    max_uses: int = 1,
    expires_at: datetime | None = None,
):
    conn = connect(settings)
    try:
        bc = BetaCodeRepository(conn).create(
            code=code, note=None, max_uses=max_uses, expires_at=expires_at
        )
        conn.commit()
    finally:
        conn.close()
    return bc


def _client_for(settings: Settings, user_id: str) -> TestClient:
    client = TestClient(create_app())
    token = cookie_serializer(settings).dumps({"user_id": user_id})
    client.cookies.set(settings.session_cookie_name, token)
    return client


def test_pending_user_blocked_from_data_plane(app_settings: Settings):
    user = _make_pending_user(
        app_settings, google_sub="p1", email="p1@cerno.local"
    )
    client = _client_for(app_settings, user.id)
    assert client.get("/auth/me").status_code == 200
    assert client.get("/sessions").status_code == 403


def test_redeem_grants_access(app_settings: Settings):
    user = _make_pending_user(
        app_settings, google_sub="r1", email="r1@cerno.local"
    )
    _make_code(app_settings, code="CERNO-AAAA-BBBB", max_uses=1)
    client = _client_for(app_settings, user.id)

    response = client.post(
        "/auth/redeem", json={"code": "cerno-aaaa-bbbb"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_status"] == "granted"

    # Now data-plane is reachable
    assert client.get("/sessions").status_code == 200


def test_redeem_invalid_code(app_settings: Settings):
    user = _make_pending_user(
        app_settings, google_sub="r2", email="r2@cerno.local"
    )
    client = _client_for(app_settings, user.id)
    response = client.post("/auth/redeem", json={"code": "NOT-A-REAL-CODE"})
    assert response.status_code == 403


def test_redeem_expired_code(app_settings: Settings):
    user = _make_pending_user(
        app_settings, google_sub="r3", email="r3@cerno.local"
    )
    yesterday = datetime.now(UTC) - timedelta(days=1)
    _make_code(app_settings, code="EXPIRED-1", expires_at=yesterday)
    client = _client_for(app_settings, user.id)
    response = client.post("/auth/redeem", json={"code": "EXPIRED-1"})
    assert response.status_code == 403


def test_single_use_code_rejects_second_user(app_settings: Settings):
    a = _make_pending_user(
        app_settings, google_sub="A", email="a@cerno.local"
    )
    b = _make_pending_user(
        app_settings, google_sub="B", email="b@cerno.local"
    )
    _make_code(app_settings, code="SINGLE", max_uses=1)

    client_a = _client_for(app_settings, a.id)
    assert (
        client_a.post("/auth/redeem", json={"code": "SINGLE"}).status_code
        == 200
    )

    client_b = _client_for(app_settings, b.id)
    assert (
        client_b.post("/auth/redeem", json={"code": "SINGLE"}).status_code
        == 403
    )


def test_multi_use_code_allows_multiple_users(app_settings: Settings):
    a = _make_pending_user(
        app_settings, google_sub="A", email="a@cerno.local"
    )
    b = _make_pending_user(
        app_settings, google_sub="B", email="b@cerno.local"
    )
    c = _make_pending_user(
        app_settings, google_sub="C", email="c@cerno.local"
    )
    _make_code(app_settings, code="THREE", max_uses=3)

    for u in (a, b, c):
        client = _client_for(app_settings, u.id)
        r = client.post("/auth/redeem", json={"code": "THREE"})
        assert r.status_code == 200, (u.email, r.text)


def test_redeem_idempotent_for_already_granted(app_settings: Settings):
    user = _make_pending_user(
        app_settings, google_sub="idem", email="idem@cerno.local"
    )
    _make_code(app_settings, code="ONCE", max_uses=1)
    client = _client_for(app_settings, user.id)
    first = client.post("/auth/redeem", json={"code": "ONCE"})
    assert first.status_code == 200
    # Already granted; further redeem calls succeed without consuming a use
    second = client.post("/auth/redeem", json={"code": "DOES-NOT-EXIST"})
    assert second.status_code == 200
    assert second.json()["access_status"] == "granted"


def test_revoked_user_is_blocked_and_cannot_redeem(app_settings: Settings):
    user = _make_pending_user(
        app_settings, google_sub="rev", email="rev@cerno.local"
    )
    conn = connect(app_settings)
    try:
        UserRepository(conn).set_access_status(user.id, "revoked")
        conn.commit()
    finally:
        conn.close()
    _make_code(app_settings, code="ANY", max_uses=99)
    client = _client_for(app_settings, user.id)
    assert client.get("/sessions").status_code == 403
    assert client.post("/auth/redeem", json={"code": "ANY"}).status_code == 403


def test_operator_email_is_auto_granted(
    app_settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CERNO_OPERATOR_EMAILS", "boss@cerno.local")
    reset_settings()
    from cerno.config import get_settings

    settings = get_settings()
    conn = connect(settings)
    try:
        user = UserRepository(conn).upsert_from_google(
            google_sub="boss",
            email="boss@cerno.local",
            name=None,
            picture=None,
            operator_emails=settings.operator_email_set(),
        )
        conn.commit()
    finally:
        conn.close()
    assert user.access_status == "granted"
    assert user.access_code_used == "OPERATOR"
