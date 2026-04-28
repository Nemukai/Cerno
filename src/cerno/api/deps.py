from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from cerno.config import Settings, get_settings
from cerno.db import connect
from cerno.llm import LLMClient
from cerno.models import User
from cerno.repositories import UserRepository


def get_conn(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[sqlite3.Connection]:
    conn = connect(settings)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_llm_client(settings: Annotated[Settings, Depends(get_settings)]) -> LLMClient:
    return LLMClient(settings=settings)


def cookie_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="cerno.session")


def get_current_user(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="not signed in")
    try:
        payload = cookie_serializer(settings).loads(
            token, max_age=settings.session_max_age_seconds
        )
    except SignatureExpired as exc:
        raise HTTPException(status_code=401, detail="session expired") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=401, detail="invalid session") from exc
    user_id = payload.get("user_id") if isinstance(payload, dict) else None
    if not user_id:
        raise HTTPException(status_code=401, detail="invalid session")
    user = UserRepository(conn).get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user


SettingsDep = Annotated[Settings, Depends(get_settings)]
ConnDep = Annotated[sqlite3.Connection, Depends(get_conn)]
LLMDep = Annotated[LLMClient, Depends(get_llm_client)]
UserDep = Annotated[User, Depends(get_current_user)]
