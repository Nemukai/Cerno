from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from cerno.config import Settings
from cerno.llm import LLMClient
from cerno.models import DashboardPage, NotebookCell, Widget
from cerno.repositories import (
    AnomalyRepository,
    DashboardRepository,
    FileRepository,
    LinkRepository,
    NotebookRepository,
    SchemaRepository,
    SessionRepository,
    new_id,
)
from cerno.services.dashboard_visuals import (
    DASHBOARD_MAX_WIDGETS,
    DashboardCandidate,
    build_dashboard_candidates,
    candidate_catalog,
    load_file_profiles,
    profile_catalog,
    select_fallback_candidates,
)

DASHBOARD_SYSTEM_PROMPT = """You are Cerno's dashboard planner.
Choose the most useful default dashboard widgets from the provided candidate list.

Rules:
- Do not invent chart code, SQL, columns, or widget ids.
- Select widgets that would help a non-technical operator understand the dataset quickly.
- Prefer business KPIs, time trends, segment mixes, anomaly attention, and file relationships.
- Avoid redundant widgets that show the same idea in a different shape.
- Return only valid JSON matching the schema."""

DASHBOARD_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "selected": {
            "type": "array",
            "maxItems": DASHBOARD_MAX_WIDGETS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "candidate_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["candidate_id", "reason"],
            },
        },
    },
    "required": ["summary", "selected"],
}


def _add_widget_cell(
    *,
    notebook_repo: NotebookRepository,
    page_id: str,
    order_index: int,
    widget: Widget,
    code: str,
    bound_file_ids: list[str],
    bound_schema_versions: dict[str, int],
) -> NotebookCell:
    cell = NotebookCell(
        id=new_id(),
        page_id=page_id,
        order_index=order_index,
        kind="widget",
        code=code,
        output={"widget": widget.model_dump()},
        bound_file_ids=bound_file_ids,
        bound_schema_versions=bound_schema_versions,
        created_at=datetime.now(UTC),
    )
    return notebook_repo.add_cell(cell)


async def generate_overview(
    *,
    session_id: str,
    settings: Settings,
    sessions_repo: SessionRepository,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    links_repo: LinkRepository,
    anomalies_repo: AnomalyRepository,
    dashboards_repo: DashboardRepository,
    notebook_repo: NotebookRepository,
    llm_client: LLMClient | None = None,
) -> DashboardPage:
    dashboard = dashboards_repo.get_for_session(session_id)
    if dashboard is None:
        dashboard = dashboards_repo.create(session_id)

    for existing in dashboards_repo.list_pages(dashboard.id):
        if existing.kind == "overview":
            dashboards_repo.delete_page(existing.id)

    page = dashboards_repo.add_page(
        dashboard_id=dashboard.id,
        title="Overview",
        kind="overview",
        position=0,
        pinned=True,
    )

    files = files_repo.list_for_session(session_id)
    schemas_by_file = {
        file.id: schema
        for file in files
        if (schema := schemas_repo.get(file.id, file.schema_version)) is not None
    }
    profiles = load_file_profiles(files, schemas_by_file)
    links = links_repo.list_for_session(session_id)
    anomalies = anomalies_repo.top_for_session(session_id, limit=10_000)
    session = sessions_repo.get(session_id)
    candidates = build_dashboard_candidates(
        profiles=profiles,
        links=links,
        anomalies=anomalies,
        session_overview=session.overview if session else None,
    )
    selected = await _select_candidates(
        candidates=candidates,
        profiles=profiles,
        links_count=len(links),
        anomalies_count=len(anomalies),
        settings=settings,
        llm_client=llm_client,
    )

    for order, candidate in enumerate(selected):
        _add_widget_cell(
            notebook_repo=notebook_repo,
            page_id=page.id,
            order_index=order,
            widget=candidate.widget,
            code=candidate.code,
            bound_file_ids=candidate.bound_file_ids,
            bound_schema_versions=candidate.bound_schema_versions,
        )

    return page


async def _select_candidates(
    *,
    candidates: list[DashboardCandidate],
    profiles: list[Any],
    links_count: int,
    anomalies_count: int,
    settings: Settings,
    llm_client: LLMClient | None,
) -> list[DashboardCandidate]:
    fallback = select_fallback_candidates(candidates)
    if not settings.llm_enabled or not settings.llm_api_key or llm_client is None:
        return fallback

    candidate_by_id = {candidate.id: candidate for candidate in candidates}
    prompt = {
        "files": profile_catalog(profiles),
        "relationship_count": links_count,
        "anomaly_count": anomalies_count,
        "candidate_widgets": candidate_catalog(candidates[:32]),
        "max_widgets": DASHBOARD_MAX_WIDGETS,
    }
    try:
        response = await llm_client.respond(
            input=[{"role": "user", "content": json.dumps(prompt)}],
            instructions=DASHBOARD_SYSTEM_PROMPT,
            reasoning_effort="high",
            reasoning_summary="auto",
            response_format={
                "type": "json_schema",
                "name": "dashboard_plan",
                "schema": DASHBOARD_RESPONSE_SCHEMA,
                "strict": True,
            },
        )
        payload = _parse_json(response.content)
    except Exception:
        return fallback

    selected: list[DashboardCandidate] = []
    seen: set[str] = set()
    raw_items = payload.get("selected", [])
    if not isinstance(raw_items, list):
        return fallback
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        candidate_id = item.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id in seen:
            continue
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            continue
        selected.append(candidate)
        seen.add(candidate_id)
        if len(selected) >= DASHBOARD_MAX_WIDGETS:
            break

    if len(selected) < min(4, len(fallback)):
        for candidate in fallback:
            if candidate.id not in seen:
                selected.append(candidate)
                seen.add(candidate.id)
            if len(selected) >= min(DASHBOARD_MAX_WIDGETS, len(fallback)):
                break
    return selected or fallback


def _parse_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("dashboard planner did not return a JSON object")
    return payload
