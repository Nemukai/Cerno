from __future__ import annotations

from datetime import UTC, datetime

from cerno.config import Settings
from cerno.models import DashboardPage, NotebookCell, Widget
from cerno.repositories import (
    AnomalyRepository,
    DashboardRepository,
    FileRepository,
    LinkRepository,
    NotebookRepository,
    SchemaRepository,
    new_id,
)

OVERVIEW_BAR_LIMIT = 10

_BAR_COLORS = [
    "#ef4444", "#f97316", "#f59e0b", "#eab308", "#84cc16",
    "#22c55e", "#10b981", "#14b8a6", "#06b6d4", "#3b82f6",
]


def _add_widget_cell(
    *,
    notebook_repo: NotebookRepository,
    page_id: str,
    order_index: int,
    widget: Widget,
) -> NotebookCell:
    cell = NotebookCell(
        id=new_id(),
        page_id=page_id,
        order_index=order_index,
        kind="widget",
        code=f"Widget(kind={widget.kind!r}, title={widget.title!r})",
        output={"widget": widget.model_dump()},
        created_at=datetime.now(UTC),
    )
    return notebook_repo.add_cell(cell)


def generate_overview(
    *,
    session_id: str,
    settings: Settings,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    links_repo: LinkRepository,
    anomalies_repo: AnomalyRepository,
    dashboards_repo: DashboardRepository,
    notebook_repo: NotebookRepository,
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
    all_links = links_repo.list_for_session(session_id)
    confirmed_links = []
    for link in all_links:
        review = links_repo.latest_review(link.id)
        if review is not None and review.action == "confirm":
            confirmed_links.append(link)
    files_by_id = {f.id: f for f in files}

    order = 0
    for file in files:
        kpi = Widget(
            kind="kpi",
            title=file.filename,
            data={"value": file.row_count, "label": file.filename},
            caption=f"{file.row_count} rows",
        )
        _add_widget_cell(
            notebook_repo=notebook_repo, page_id=page.id, order_index=order, widget=kpi
        )
        order += 1

    links_kpi = Widget(
        kind="kpi",
        title="Confirmed links",
        data={"value": len(confirmed_links), "label": "confirmed links"},
    )
    _add_widget_cell(
        notebook_repo=notebook_repo, page_id=page.id, order_index=order, widget=links_kpi
    )
    order += 1

    top_anomalies = anomalies_repo.top_for_session(session_id, limit=OVERVIEW_BAR_LIMIT)
    categories: list[str] = []
    values: list[float] = []
    for anomaly in top_anomalies:
        owner = files_by_id.get(anomaly.file_id)
        label = owner.filename if owner else anomaly.file_id
        categories.append(f"{label} row {anomaly.row_id}")
        values.append(anomaly.score_normalized)
    bar = Widget(
        kind="bar",
        title="Top anomalies",
        data={
            "categories": categories,
            "values": values,
            "colors": _BAR_COLORS[: len(categories)],
        },
        caption="Top 10 flagged rows by anomaly score.",
    )
    _add_widget_cell(
        notebook_repo=notebook_repo, page_id=page.id, order_index=order, widget=bar
    )
    order += 1

    link_rows = []
    for link in confirmed_links:
        a = files_by_id.get(link.file_a)
        b = files_by_id.get(link.file_b)
        link_rows.append(
            [
                f"{a.filename if a else link.file_a}.{link.col_a}",
                f"{b.filename if b else link.file_b}.{link.col_b}",
                link.direction,
                link.summary or "",
            ]
        )
    table_widget = Widget(
        kind="table",
        title="Confirmed links",
        data={
            "columns": ["from", "to", "direction", "summary"],
            "rows": link_rows,
        },
    )
    _add_widget_cell(
        notebook_repo=notebook_repo, page_id=page.id, order_index=order, widget=table_widget
    )
    order += 1

    total_anomalies = len(anomalies_repo.top_for_session(session_id, limit=10_000))
    summary_widget = Widget(
        kind="markdown",
        title="Session summary",
        data={
            "text": (
                f"{len(files)} files, {len(confirmed_links)} confirmed links, "
                f"{total_anomalies} anomalies flagged."
            )
        },
    )
    _add_widget_cell(
        notebook_repo=notebook_repo,
        page_id=page.id,
        order_index=order,
        widget=summary_widget,
    )

    return page
