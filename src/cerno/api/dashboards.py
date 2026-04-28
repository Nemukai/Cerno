from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException

from cerno.api.deps import ConnDep, SettingsDep, UserDep
from cerno.models import Anomaly, DashboardPage, NotebookCell
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

router = APIRouter(tags=["dashboard"])


def _require_session_owned(conn, session_id: str, user_id: str) -> None:
    if SessionRepository(conn).get(session_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="session not found")


@router.post("/sessions/{session_id}/build-dashboard")
def post_build_dashboard(
    session_id: str, conn: ConnDep, settings: SettingsDep, user: UserDep
) -> dict[str, object]:
    _require_session_owned(conn, session_id, user.id)
    files_repo = FileRepository(conn)
    schemas_repo = SchemaRepository(conn)
    links_repo = LinkRepository(conn)
    anomalies_repo = AnomalyRepository(conn)
    dashboards_repo = DashboardRepository(conn)
    notebook_repo = NotebookRepository(conn)

    drafts = detect_anomalies(
        session_id=session_id,
        settings=settings,
        files_repo=files_repo,
        schemas_repo=schemas_repo,
        links_repo=links_repo,
    )
    persisted = persist_anomalies(
        anomalies_repo=anomalies_repo, session_id=session_id, drafts=drafts
    )
    page = generate_overview(
        session_id=session_id,
        settings=settings,
        files_repo=files_repo,
        schemas_repo=schemas_repo,
        links_repo=links_repo,
        anomalies_repo=anomalies_repo,
        dashboards_repo=dashboards_repo,
        notebook_repo=notebook_repo,
    )
    widgets = notebook_repo.list_for_page(page.id)
    return {
        "page_id": page.id,
        "anomaly_count": len(persisted),
        "widget_count": len(widgets),
    }


@router.get("/sessions/{session_id}/dashboard")
def get_dashboard(
    session_id: str, conn: ConnDep, user: UserDep
) -> dict[str, object]:
    _require_session_owned(conn, session_id, user.id)
    dashboards_repo = DashboardRepository(conn)
    notebook_repo = NotebookRepository(conn)
    dashboard = dashboards_repo.get_for_session(session_id)
    if dashboard is None:
        return {"pages": [], "cells_by_page": {}}
    pages = dashboards_repo.list_pages(dashboard.id)
    cells_by_page: dict[str, list[NotebookCell]] = {
        page.id: notebook_repo.list_for_page(page.id) for page in pages
    }
    return {
        "pages": [DashboardPage.model_validate(p).model_dump(mode="json") for p in pages],
        "cells_by_page": {
            pid: [NotebookCell.model_validate(c).model_dump(mode="json") for c in cells]
            for pid, cells in cells_by_page.items()
        },
    }


@router.get("/sessions/{session_id}/anomalies", response_model=list[Anomaly])
def get_anomalies(
    session_id: str, conn: ConnDep, user: UserDep, limit: int = 20
) -> list[Anomaly]:
    _require_session_owned(conn, session_id, user.id)
    return AnomalyRepository(conn).top_for_session(session_id, limit=limit)
