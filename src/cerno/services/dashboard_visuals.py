from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl

from cerno.models import Anomaly, File, FileSchema, Link, SchemaColumn, Widget

DASHBOARD_MAX_WIDGETS = 9
TOP_CATEGORY_LIMIT = 12
METRIC_NAME_HINTS = {
    "amount",
    "balance",
    "charge",
    "cost",
    "fare",
    "fee",
    "price",
    "quantity",
    "rate",
    "revenue",
    "sales",
    "sum",
    "tax",
    "total",
    "value",
    "weight",
}
IDENTIFIER_NAME_HINTS = {
    "id",
    "number",
    "no",
    "code",
    "phone",
    "mobile",
    "transaction",
    "vehicle",
    "plate",
}


@dataclass(frozen=True)
class FileProfile:
    file: File
    schema: FileSchema | None
    frame: pl.DataFrame


@dataclass(frozen=True)
class DashboardCandidate:
    id: str
    priority: float
    rationale: str
    widget: Widget
    code: str
    bound_file_ids: list[str]
    bound_schema_versions: dict[str, int]


def load_file_profiles(files: list[File], schemas_by_file: dict[str, FileSchema]) -> list[FileProfile]:
    profiles: list[FileProfile] = []
    for file in files:
        profiles.append(
            FileProfile(
                file=file,
                schema=schemas_by_file.get(file.id),
                frame=pl.read_parquet(file.parquet_path),
            )
        )
    return profiles


def build_dashboard_candidates(
    *,
    profiles: list[FileProfile],
    links: list[Link],
    anomalies: list[Anomaly],
    session_overview: str | None,
) -> list[DashboardCandidate]:
    candidates: list[DashboardCandidate] = []
    total_rows = sum(profile.file.row_count for profile in profiles)

    if session_overview:
        candidates.append(
            _candidate(
                "overview_summary",
                priority=100,
                rationale="Explains what the approved schema says this data contains.",
                widget=markdown_widget(
                    title="Generated overview",
                    text=session_overview,
                    caption="Generated from the approved schema.",
                ),
                code="markdown_widget(title='Generated overview', text=session_overview)",
            )
        )

    candidates.append(
        _candidate(
            "total_rows",
            priority=98,
            rationale="Shows the size of the modeled dataset.",
            widget=kpi_widget(
                title="Total rows",
                value=_format_number(total_rows),
                label="rows across uploaded files",
            ),
            code="kpi_widget(title='Total rows', value=total_rows)",
        )
    )
    candidates.append(
        _candidate(
            "file_count",
            priority=92,
            rationale="Shows how many source files are included.",
            widget=kpi_widget(
                title="Files modeled",
                value=len(profiles),
                label="approved sources",
            ),
            code="kpi_widget(title='Files modeled', value=len(files))",
        )
    )
    candidates.append(
        _candidate(
            "relationship_count",
            priority=88,
            rationale="Shows how connected the source files are.",
            widget=kpi_widget(
                title="Relationships",
                value=len(links),
                label="schema links",
            ),
            code="kpi_widget(title='Relationships', value=len(links))",
        )
    )
    candidates.append(
        _candidate(
            "anomaly_count",
            priority=82,
            rationale="Shows how many rows need attention.",
            widget=kpi_widget(
                title="Flagged rows",
                value=len(anomalies),
                label="possible anomalies",
            ),
            code="kpi_widget(title='Flagged rows', value=len(anomalies))",
        )
    )

    for profile in profiles:
        candidates.extend(_metric_candidates(profile))
        candidates.extend(_category_candidates(profile))
        candidates.extend(_time_series_candidates(profile))

    if anomalies:
        top = sorted(anomalies, key=lambda item: item.score_normalized, reverse=True)[:10]
        file_names = {profile.file.id: profile.file.friendly_name or profile.file.filename for profile in profiles}
        candidates.append(
            _candidate(
                "top_anomalies",
                priority=86,
                rationale="Highlights the highest-risk rows first.",
                widget=bar_chart_widget(
                    title="Top flagged rows",
                    items=[
                        {
                            "name": f"{file_names.get(item.file_id, item.file_id)} row {item.row_id}",
                            "value": round(item.score_normalized, 2),
                        }
                        for item in top
                    ],
                    caption="Highest anomaly scores from the default detectors.",
                    horizontal=True,
                ),
                code="bar_chart_widget(title='Top flagged rows', items=top_anomalies)",
            )
        )

    if links:
        file_names = {profile.file.id: profile.file.friendly_name or profile.file.filename for profile in profiles}
        candidates.append(
            _candidate(
                "relationship_table",
                priority=76,
                rationale="Shows the columns that connect files.",
                widget=table_widget(
                    title="Detected relationships",
                    columns=["from", "to", "type", "evidence"],
                    rows=[
                        [
                            f"{file_names.get(link.file_a, link.file_a)}.{link.col_a}",
                            f"{file_names.get(link.file_b, link.file_b)}.{link.col_b}",
                            link.direction.replace("_", " "),
                            f"{round(link.score * 100)}%",
                        ]
                        for link in links[:20]
                    ],
                    caption="Relationships from schema discovery and value-overlap checks.",
                ),
                code="table_widget(title='Detected relationships', rows=links)",
            )
        )

    return sorted(candidates, key=lambda item: item.priority, reverse=True)


def candidate_catalog(candidates: list[DashboardCandidate]) -> list[dict[str, Any]]:
    return [
        {
            "id": candidate.id,
            "kind": candidate.widget.kind,
            "title": candidate.widget.title,
            "caption": candidate.widget.caption,
            "rationale": candidate.rationale,
            "priority": candidate.priority,
        }
        for candidate in candidates
    ]


def profile_catalog(profiles: list[FileProfile]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for profile in profiles:
        schema = profile.schema
        payload.append(
            {
                "file_id": profile.file.id,
                "name": profile.file.friendly_name or profile.file.filename,
                "description": profile.file.description,
                "row_count": profile.file.row_count,
                "columns": [
                    {
                        "name": column.name,
                        "kind": column.inferred_kind,
                        "description": column.description,
                    }
                    for column in (schema.columns if schema else [])
                ],
            }
        )
    return payload


def select_fallback_candidates(candidates: list[DashboardCandidate]) -> list[DashboardCandidate]:
    selected: list[DashboardCandidate] = []
    seen_kinds: set[str] = set()
    for candidate in candidates:
        if len(selected) >= DASHBOARD_MAX_WIDGETS:
            break
        kind = candidate.widget.kind
        if kind in {"bar", "line", "pie"} and kind in seen_kinds:
            continue
        selected.append(candidate)
        seen_kinds.add(kind)
    return selected


def kpi_widget(
    *,
    title: str,
    value: str | int | float,
    label: str | None = None,
    caption: str | None = None,
) -> Widget:
    data: dict[str, Any] = {"value": value}
    if label:
        data["label"] = label
    return Widget(kind="kpi", title=title, data=data, caption=caption)


def bar_chart_widget(
    *,
    title: str,
    items: list[dict[str, str | int | float]],
    caption: str | None = None,
    horizontal: bool = False,
) -> Widget:
    return _category_widget(
        kind="bar", title=title, items=items, caption=caption, horizontal=horizontal
    )


def pie_chart_widget(
    *,
    title: str,
    items: list[dict[str, str | int | float]],
    caption: str | None = None,
) -> Widget:
    return _category_widget(kind="pie", title=title, items=items, caption=caption)


def line_chart_widget(
    *,
    title: str,
    items: list[dict[str, str | int | float]],
    caption: str | None = None,
) -> Widget:
    return Widget(
        kind="line",
        title=title,
        data={
            "items": items,
            "categories": [str(item["name"]) for item in items],
            "values": [_to_float(item["value"]) for item in items],
        },
        options={"interactive": True},
        caption=caption,
    )


def table_widget(
    *,
    title: str,
    columns: list[str],
    rows: list[list[str | int | float]],
    caption: str | None = None,
) -> Widget:
    return Widget(
        kind="table",
        title=title,
        data={"columns": columns, "rows": rows},
        options={"searchable": True},
        caption=caption,
    )


def markdown_widget(*, title: str, text: str, caption: str | None = None) -> Widget:
    return Widget(kind="markdown", title=title, data={"text": text}, caption=caption)


def _metric_candidates(profile: FileProfile) -> list[DashboardCandidate]:
    if not profile.schema:
        return []
    candidates: list[DashboardCandidate] = []
    metric_columns = [
        column
        for column in profile.schema.columns
        if column.inferred_kind in {"int", "float"} and _metric_score(column) > 0
    ]
    metric_columns.sort(key=_metric_score, reverse=True)
    for column in metric_columns[:3]:
        numeric = _numeric_values(profile.frame, column.name)
        if not numeric:
            continue
        total = sum(numeric)
        average = total / len(numeric)
        file_name = profile.file.friendly_name or profile.file.filename
        title_base = _humanize(column.name)
        candidates.append(
            _candidate(
                f"sum_{profile.file.id}_{column.name}",
                priority=84 + _metric_score(column),
                rationale=f"Totals the key metric {column.name} from {file_name}.",
                widget=kpi_widget(
                    title=f"Total {title_base}",
                    value=_format_number(total),
                    label=file_name,
                    caption=column.description,
                ),
                code=f"kpi_widget(title='Total {title_base}', value=sum({column.name}))",
                bound_file_ids=[profile.file.id],
                bound_schema_versions={profile.file.id: profile.file.schema_version},
            )
        )
        candidates.append(
            _candidate(
                f"avg_{profile.file.id}_{column.name}",
                priority=74 + _metric_score(column),
                rationale=f"Shows the typical value for {column.name} in {file_name}.",
                widget=kpi_widget(
                    title=f"Average {title_base}",
                    value=_format_number(average),
                    label=file_name,
                ),
                code=f"kpi_widget(title='Average {title_base}', value=avg({column.name}))",
                bound_file_ids=[profile.file.id],
                bound_schema_versions={profile.file.id: profile.file.schema_version},
            )
        )
    return candidates


def _category_candidates(profile: FileProfile) -> list[DashboardCandidate]:
    if not profile.schema:
        return []
    candidates: list[DashboardCandidate] = []
    for column in profile.schema.columns:
        if column.inferred_kind not in {"category", "bool", "string"}:
            continue
        counts = _top_counts(profile.frame, column.name, limit=TOP_CATEGORY_LIMIT)
        if len(counts) < 2:
            continue
        distinct_ratio = len(counts) / max(profile.frame.height, 1)
        if column.inferred_kind == "string" and distinct_ratio > 0.7:
            continue
        file_name = profile.file.friendly_name or profile.file.filename
        title = f"{_humanize(column.name)} mix"
        items = [{"name": name, "value": value} for name, value in counts]
        candidates.append(
            _candidate(
                f"bar_{profile.file.id}_{column.name}",
                priority=72 if column.inferred_kind == "category" else 64,
                rationale=f"Shows the most common values for {column.name} in {file_name}.",
                widget=bar_chart_widget(
                    title=title,
                    items=items,
                    caption=f"Top values in {file_name}.",
                    horizontal=True,
                ),
                code=f"bar_chart_widget(title='{title}', group_by={column.name})",
                bound_file_ids=[profile.file.id],
                bound_schema_versions={profile.file.id: profile.file.schema_version},
            )
        )
        if 2 <= len(items) <= 8:
            candidates.append(
                _candidate(
                    f"pie_{profile.file.id}_{column.name}",
                    priority=62,
                    rationale=f"Shows share of total by {column.name}.",
                    widget=pie_chart_widget(
                        title=f"{_humanize(column.name)} share",
                        items=items,
                        caption=f"Distribution in {file_name}.",
                    ),
                    code=f"pie_chart_widget(title='{title}', group_by={column.name})",
                    bound_file_ids=[profile.file.id],
                    bound_schema_versions={profile.file.id: profile.file.schema_version},
                )
            )
    return candidates


def _time_series_candidates(profile: FileProfile) -> list[DashboardCandidate]:
    if not profile.schema:
        return []
    date_columns = [
        column for column in profile.schema.columns if column.inferred_kind in {"date", "datetime"}
    ]
    metric_columns = [
        column
        for column in profile.schema.columns
        if column.inferred_kind in {"int", "float"} and _metric_score(column) > 0
    ]
    if not date_columns or not metric_columns:
        return []
    metric_columns.sort(key=_metric_score, reverse=True)
    file_name = profile.file.friendly_name or profile.file.filename
    candidates: list[DashboardCandidate] = []
    for date_col in date_columns[:1]:
        for metric_col in metric_columns[:1]:
            items = _time_sum(profile.frame, date_col.name, metric_col.name, limit=18)
            if len(items) < 2:
                continue
            title = f"{_humanize(metric_col.name)} over time"
            candidates.append(
                _candidate(
                    f"line_{profile.file.id}_{date_col.name}_{metric_col.name}",
                    priority=90,
                    rationale=(
                        f"Shows how {metric_col.name} changes across {date_col.name} "
                        f"in {file_name}."
                    ),
                    widget=line_chart_widget(
                        title=title,
                        items=items,
                        caption=f"Grouped by {date_col.name}.",
                    ),
                    code=(
                        f"line_chart_widget(title='{title}', x={date_col.name}, "
                        f"y=sum({metric_col.name}))"
                    ),
                    bound_file_ids=[profile.file.id],
                    bound_schema_versions={profile.file.id: profile.file.schema_version},
                )
            )
    return candidates


def _candidate(
    candidate_id: str,
    *,
    priority: float,
    rationale: str,
    widget: Widget,
    code: str,
    bound_file_ids: list[str] | None = None,
    bound_schema_versions: dict[str, int] | None = None,
) -> DashboardCandidate:
    return DashboardCandidate(
        id=_safe_id(candidate_id),
        priority=priority,
        rationale=rationale,
        widget=widget,
        code=code,
        bound_file_ids=bound_file_ids or [],
        bound_schema_versions=bound_schema_versions or {},
    )


def _category_widget(
    *,
    kind: str,
    title: str,
    items: list[dict[str, str | int | float]],
    caption: str | None,
    horizontal: bool = False,
) -> Widget:
    clean_items = [
        {"name": str(item["name"]), "value": _to_float(item["value"])}
        for item in items
        if "name" in item and "value" in item
    ]
    return Widget(
        kind=kind,  # type: ignore[arg-type]
        title=title,
        data={
            "items": clean_items,
            "categories": [item["name"] for item in clean_items],
            "values": [item["value"] for item in clean_items],
        },
        options={"interactive": True, "horizontal": horizontal},
        caption=caption,
    )


def _metric_score(column: SchemaColumn) -> float:
    name = column.name.lower()
    parts = {part for part in name.replace("-", "_").replace(" ", "_").split("_") if part}
    if parts & IDENTIFIER_NAME_HINTS:
        return 0
    score = 1.0 if column.inferred_kind == "float" else 0.5
    score += 4 * len(parts & METRIC_NAME_HINTS)
    if "total" in parts:
        score += 2
    return score


def _numeric_values(frame: pl.DataFrame, column: str) -> list[float]:
    if column not in frame.columns:
        return []
    try:
        values = frame[column].cast(pl.Float64, strict=False).drop_nulls().to_list()
    except Exception:
        return []
    return [float(value) for value in values if isinstance(value, int | float)]


def _top_counts(frame: pl.DataFrame, column: str, *, limit: int) -> list[tuple[str, int]]:
    if column not in frame.columns:
        return []
    counts: dict[str, int] = {}
    for value in frame[column].cast(pl.String).to_list():
        if value is None or value == "":
            continue
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]


def _time_sum(
    frame: pl.DataFrame, date_column: str, metric_column: str, *, limit: int
) -> list[dict[str, str | float]]:
    if date_column not in frame.columns or metric_column not in frame.columns:
        return []
    try:
        compact = frame.select(
            pl.col(date_column).cast(pl.String).alias("date"),
            pl.col(metric_column).cast(pl.Float64, strict=False).alias("value"),
        ).drop_nulls()
    except Exception:
        return []
    totals: dict[str, float] = {}
    for row in compact.iter_rows(named=True):
        key = str(row["date"])[:10]
        value = row["value"]
        if not isinstance(value, int | float):
            continue
        totals[key] = totals.get(key, 0.0) + float(value)
    return [
        {"name": key, "value": round(value, 2)}
        for key, value in sorted(totals.items())[:limit]
    ]


def _format_number(value: float | int) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def _humanize(value: str) -> str:
    return value.replace("_", " ").replace(".", " ").strip().title() or value


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")


def _to_float(value: str | int | float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
