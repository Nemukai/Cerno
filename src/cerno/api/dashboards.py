from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from cerno.api.deps import ConnDep, GrantedUserDep
from cerno.models import Anomaly
from cerno.repositories import (
    AnomalyRepository,
    SessionRepository,
)

router = APIRouter(tags=["insights"])


def _require_session_owned(conn: sqlite3.Connection, session_id: str, user_id: str) -> None:
    if SessionRepository(conn).get(session_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="session not found")


@router.get("/sessions/{session_id}/anomalies", response_model=list[Anomaly])
def get_anomalies(
    session_id: str, conn: ConnDep, user: GrantedUserDep, limit: int = 20
) -> list[Anomaly]:
    _require_session_owned(conn, session_id, user.id)
    return AnomalyRepository(conn).top_for_session(session_id, limit=limit)
