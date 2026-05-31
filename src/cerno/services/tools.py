from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pandas as pd

from cerno.llm import Tool, ToolRegistry
from cerno.models import DataDoc, Widget, WidgetKind
from cerno.services.sandbox import run_python


@dataclass
class ToolTable:
    name: str
    display_name: str
    row_count: int
    columns: list[dict[str, Any]]
    dataframe: pd.DataFrame | None = None
    load_error: str | None = None


@dataclass
class ToolContext:
    session_id: str
    tables: dict[str, ToolTable]
    data_doc: DataDoc | None = None
    rendered_widgets: list[Widget] = field(default_factory=list)


def build_tool_registry(ctx: ToolContext) -> ToolRegistry:
    registry = ToolRegistry()
    available_table_names = sorted(ctx.tables)

    async def list_tables(_args: dict[str, Any]) -> dict[str, Any]:
        tables = [
            {
                "name": table.name,
                "display_name": table.display_name,
                "row_count": table.row_count,
                "columns": [column["name"] for column in table.columns],
                "analysis_ready": table.dataframe is not None,
                "load_error": table.load_error,
            }
            for table in (ctx.tables[name] for name in available_table_names)
        ]
        return {
            "ok": bool(tables),
            "table_count": len(tables),
            "python_dataframe_names": [table["name"] for table in tables],
            "tables": tables,
            "message": (
                "Use these names exactly in run_python."
                if tables
                else "No processed tables are currently available to chat tools. Ask the user to process and approve files first."
            ),
        }

    async def describe_table(args: dict[str, Any]) -> dict[str, Any]:
        table_arg = args.get("table")
        if table_arg is None:
            return {
                "ok": False,
                "error": "describe_table missing required field: table",
                "available_tables": available_table_names,
            }
        table_name = str(table_arg)
        table = ctx.tables.get(table_name)
        if table is None:
            return {
                "ok": False,
                "error": f"Unknown table: {table_name}",
                "available_tables": available_table_names,
            }
        return {
            "ok": True,
            "table": table.name,
            "display_name": table.display_name,
            "row_count": table.row_count,
            "analysis_ready": table.dataframe is not None,
            "load_error": table.load_error,
            "column_count": len(table.columns),
            "profile": _table_profile(table),
            "columns": _profiled_columns(table),
        }

    async def run_python_handler(args: dict[str, Any]) -> dict[str, Any]:
        dataframes = {
            name: table.dataframe
            for name, table in ctx.tables.items()
            if table.dataframe is not None
        }
        if not dataframes:
            return {
                "ok": False,
                "error": "No pandas DataFrames are currently available from processed R2 artifacts.",
                "available_tables": available_table_names,
            }
        code_arg = args.get("code")
        if code_arg is None:
            return {"ok": False, "error": "run_python missing required field: code"}
        code = str(code_arg)
        return run_python(code, tables=dataframes).to_dict()

    async def render_widget(args: dict[str, Any]) -> dict[str, Any]:
        missing = [key for key in ("kind", "title", "data") if key not in args]
        if missing:
            return {
                "ok": False,
                "error": f"render_widget missing required field(s): {', '.join(missing)}",
                "received_fields": sorted(args),
            }
        kind, options = _normalize_widget_args(args)
        widget = Widget(
            kind=kind,
            title=str(args["title"]),
            data=args["data"],
            options=options,
            caption=args.get("caption"),
        )
        ctx.rendered_widgets.append(widget)
        return {"widget": widget.model_dump()}

    async def read_schema_guide(_args: dict[str, Any]) -> dict[str, Any]:
        if ctx.data_doc is None:
            return {
                "ok": False,
                "schema_guide": None,
                "message": (
                    "No schema guide has been generated for this session yet. "
                    "Use list_tables and describe_table as the fallback."
                ),
                "fallback_tables": available_table_names,
            }
        return {
            "ok": True,
            "schema_guide": ctx.data_doc.model_dump(mode="json"),
            "available_tables": available_table_names,
        }

    registry.register(
        Tool(
            name="list_tables",
            description=(
                "List every Postgres-registered table available for analysis in this session. "
                "Use the returned python_dataframe_names exactly when writing run_python code."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=list_tables,
        )
    )
    registry.register(
        Tool(
            name="describe_table",
            description=(
                "Return the schema and compact data profile for a single table: "
                "column descriptions, semantic kinds, null/distinct counts, numeric/date ranges, "
                "and top categorical values. Does not return sample rows."
            ),
            parameters={
                "type": "object",
                "properties": {"table": {"type": "string"}},
                "required": ["table"],
                "additionalProperties": False,
            },
            handler=describe_table,
        )
    )
    registry.register(
        Tool(
            name="run_python",
            description=(
                "Run Python in a restricted, read-only sandbox for analysis. "
                f"Available pandas DataFrames: {', '.join(available_table_names) if available_table_names else '(none currently available)'}. "
                "Do not write import statements; imports will fail. "
                "Already available libraries: pandas as pd, numpy as np through a curated read-only helper surface. "
                "No other libraries are available. "
                "Use only those dataframe variable names plus pd and np. "
                "Allowed: pandas/numpy calculations, filtering, grouping, sorting, joins/merges, simple statistics, "
                "creating local variables, and printing concise diagnostics. "
                "Not allowed: import statements, file/network access, subprocesses, OS/system calls, database writes, package installs, "
                "open/read/write files, eval/exec/compile, or mutating external state. "
                "Return the answer as the last expression whenever possible; the last expression is captured in result_preview. "
                "If you need to inspect available dataframe names first, call list_tables before run_python."
            ),
            parameters={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
                "additionalProperties": False,
            },
            handler=run_python_handler,
        )
    )
    registry.register(
        Tool(
            name="render_widget",
            description=(
                "Emit a widget for this chat turn. "
                "Prioritize this for analytical answers: KPI totals, grouped result tables, rankings, comparisons, distributions, and time trends should be rendered as widgets, not only described in text. "
                "Call this for every chart, KPI, or table you want the user to see before writing the final answer. "
                "The data field is required and must be an object, never a raw list and never Python code. "
                "First use run_python to compute compact aggregates from the available pandas DataFrames, then pass the returned concrete values into this tool. "
                "For kpi, pass either data={value, label, delta} or up to four KPI cards as data={items: [{title, value, label, delta}, ...]}. "
                "Available visual kinds are bar, horizontal_bar, grouped_bar, stacked_bar, line, area, stacked_area, pie, histogram, scatter, heatmap, boxplot, waterfall, sankey, and timeline. "
                "For these chart and diagram widgets, prefer data={columns: [...], rows: [[...], ...]} with compact aggregated rows. "
                "Use the exact kind for the requested visual: horizontal_bar for horizontal bars; grouped_bar for side-by-side grouped series; stacked_bar for stacked/proportion bars; stacked_area for stacked time-series areas; histogram for numeric distributions; scatter for x/y point plots; heatmap for matrix intensity; boxplot for spread/outliers; waterfall for cumulative deltas; sankey for source-target flows; timeline for dated events. "
                "Do not encode a requested chart/diagram as kind=table, and do not encode a requested specialized chart as kind=bar or kind=line. "
                "For charts, set options.x/category, options.y/value, options.series, options.source, options.target, options.time, or options.label when the default column inference is not obvious. "
                "The current app can display all enum kinds in this schema; never tell the user that only the old line/bar/table/pie/KPI set is supported."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "kpi",
                            "bar",
                            "horizontal_bar",
                            "grouped_bar",
                            "stacked_bar",
                            "line",
                            "area",
                            "stacked_area",
                            "pie",
                            "histogram",
                            "scatter",
                            "heatmap",
                            "boxplot",
                            "waterfall",
                            "sankey",
                            "timeline",
                            "table",
                            "markdown",
                        ],
                    },
                    "title": {"type": "string"},
                    "data": {"type": "object"},
                    "options": {"type": "object"},
                    "caption": {"type": ["string", "null"]},
                },
                "required": ["kind", "title", "data"],
                "additionalProperties": False,
            },
            handler=render_widget,
        )
    )
    registry.register(
        Tool(
            name="read_schema_guide",
            description=(
                "Read Cerno's schema guide for this session: file meanings, "
                "grain, key fields, relationships, caveats, glossary, and starter questions."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=read_schema_guide,
        )
    )

    return registry


def _normalize_widget_args(args: dict[str, Any]) -> tuple[WidgetKind, dict[str, Any]]:
    kind = str(args["kind"])
    options = dict(args.get("options") or {})
    hint_text = " ".join(
        str(value)
        for value in (
            args.get("title"),
            args.get("caption"),
            options.get("chartType"),
            options.get("variant"),
            options.get("layout"),
            options.get("orientation"),
            options.get("type"),
        )
        if value is not None
    ).lower()
    requested = {
        str(options.get(key, "")).strip().lower()
        for key in ("chartType", "variant", "layout", "orientation", "type")
        if options.get(key) is not None
    }

    hinted_kind = _hinted_widget_kind(hint_text)
    if hinted_kind is not None and kind in {"bar", "line", "table"}:
        kind = hinted_kind

    if kind == "table" and _looks_like_sankey_data(args.get("data")):
        kind = "sankey"

    if kind == "bar":
        if {"horizontal", "horizontal_bar"} & requested:
            kind = "horizontal_bar"
        elif {"stacked", "stacked_bar"} & requested or options.get("stacked") is True:
            kind = "stacked_bar"
        elif _looks_like_percentage_series(args.get("data")):
            kind = "stacked_bar"
        elif {"grouped", "grouped_bar"} & requested or _looks_like_grouped_bar_data(args.get("data")):
            kind = "grouped_bar"

    if kind == "horizontal_bar":
        options["horizontal"] = True

    return cast(WidgetKind, kind), options


def _hinted_widget_kind(text: str) -> WidgetKind | None:
    hints = (
        ("stacked area", "stacked_area"),
        ("area chart", "area"),
        ("histogram", "histogram"),
        ("scatterplot", "scatter"),
        ("scatter plot", "scatter"),
        ("heatmap", "heatmap"),
        ("heat map", "heatmap"),
        ("boxplot", "boxplot"),
        ("box plot", "boxplot"),
        ("waterfall", "waterfall"),
        ("sankey", "sankey"),
        ("timeline", "timeline"),
        ("horizontal bar", "horizontal_bar"),
        ("grouped bar", "grouped_bar"),
        ("stacked bar", "stacked_bar"),
        ("stacked chart", "stacked_bar"),
    )
    for phrase, kind in hints:
        if phrase in text:
            return cast(WidgetKind, kind)
    return None


def _looks_like_sankey_data(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    columns = data.get("columns")
    if not isinstance(columns, list):
        return False
    normalized = {str(column).strip().lower() for column in columns}
    return {"source", "target"}.issubset(normalized)


def _looks_like_percentage_series(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    columns = data.get("columns")
    if not isinstance(columns, list):
        return False
    return any(
        any(term in str(column).lower() for term in ("pct", "percent", "share", "proportion"))
        for column in columns
    )


def _looks_like_grouped_bar_data(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("series"), list) and len(data["series"]) > 1:
        return True
    columns = data.get("columns")
    rows = data.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return False
    if len(columns) >= 3:
        has_text_series = any(
            isinstance(row, list)
            and len(row) > 1
            and _numeric_value(row[1]) is None
            and row[1] not in (None, "")
            for row in rows
        )
        has_numeric_value = any(
            isinstance(row, list)
            and len(row) > 2
            and _numeric_value(row[2]) is not None
            for row in rows
        )
        if has_text_series and has_numeric_value:
            return True
    numeric_columns = 0
    for index in range(1, len(columns)):
        if any(_numeric_value(row[index] if isinstance(row, list) and index < len(row) else None) is not None for row in rows):
            numeric_columns += 1
    return numeric_columns > 1


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return None


def _table_profile(table: ToolTable) -> dict[str, Any]:
    frame = table.dataframe
    if frame is None:
        return {
            "ok": False,
            "message": table.load_error or "No dataframe is loaded for this table.",
        }
    return {
        "ok": True,
        "row_count": len(frame.index),
        "column_count": len(frame.columns),
        "profiled_column_count": min(len(frame.columns), len(table.columns) or len(frame.columns)),
        "notes": [
            "This profile is computed from the processed dataframe available to run_python.",
            "No sample rows are included.",
            "Top values are capped and intended for orientation, not exhaustive enumeration.",
        ],
    }


def _profiled_columns(table: ToolTable) -> list[dict[str, Any]]:
    frame = table.dataframe
    schema_columns = table.columns
    if frame is None:
        return schema_columns

    if schema_columns:
        columns = schema_columns
    else:
        columns = [{"name": str(name), "type": str(frame[name].dtype)} for name in frame.columns]

    return [_profile_column(column, frame) for column in columns]


def _profile_column(column: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    name = str(column.get("name") or "")
    profiled = dict(column)
    if name not in frame.columns:
        profiled["profile"] = {"ok": False, "message": "Column is documented but not present in dataframe."}
        return profiled

    series = frame[name]
    row_count = len(series.index)
    null_count = int(series.isna().sum())
    non_null_count = row_count - null_count
    distinct_count = int(series.nunique(dropna=True))
    profile: dict[str, Any] = {
        "ok": True,
        "pandas_dtype": str(series.dtype),
        "non_null_count": non_null_count,
        "null_count": null_count,
        "null_percent": _round_percent(null_count, row_count),
        "distinct_count": distinct_count,
        "distinct_percent": _round_percent(distinct_count, non_null_count),
        "hints": _column_hints(
            row_count=row_count,
            non_null_count=non_null_count,
            null_count=null_count,
            distinct_count=distinct_count,
            name=name,
        ),
    }

    numeric = pd.to_numeric(series, errors="coerce")
    numeric_non_null = int(numeric.notna().sum())
    if numeric_non_null:
        profile["numeric"] = {
            "count": numeric_non_null,
            "min": _json_safe_scalar(numeric.min()),
            "max": _json_safe_scalar(numeric.max()),
            "mean": _json_safe_scalar(numeric.mean()),
            "median": _json_safe_scalar(numeric.median()),
        }

    temporal = _temporal_profile(name, column, series)
    if temporal is not None:
        profile.update(temporal)

    profile["top_values"] = _top_values(series)
    profiled["profile"] = profile
    return profiled


def _column_hints(
    *,
    row_count: int,
    non_null_count: int,
    null_count: int,
    distinct_count: int,
    name: str,
) -> list[str]:
    hints: list[str] = []
    lowered = name.lower()
    if row_count and null_count == row_count:
        hints.append("all_null")
    elif row_count and null_count / row_count >= 0.5:
        hints.append("mostly_null")
    if non_null_count and distinct_count == 1:
        hints.append("constant")
    if non_null_count and distinct_count / non_null_count >= 0.95:
        hints.append("high_cardinality")
    if "id" in lowered or "number" in lowered or lowered.endswith("no"):
        hints.append("identifier_like")
    return hints


def _top_values(series: pd.Series, *, limit: int = 5) -> list[dict[str, Any]]:
    counts = series.dropna().value_counts().head(limit)
    return [
        {
            "value": _json_safe_scalar(value),
            "count": int(count),
        }
        for value, count in counts.items()
    ]


def _temporal_profile(name: str, column: dict[str, Any], series: pd.Series) -> dict[str, Any] | None:
    dtype = str(column.get("type") or column.get("kind") or column.get("inferred_kind") or "").lower()
    lowered = name.lower()
    is_date = "date" in dtype or "date" in lowered
    is_time = "time" in dtype or "time" in lowered
    if not (is_date or is_time or pd.api.types.is_datetime64_any_dtype(series)):
        return None

    if is_time and not is_date:
        parsed = pd.to_datetime(series, format="%H:%M:%S", errors="coerce")
        parsed_count = int(parsed.notna().sum())
        if not parsed_count:
            parsed = pd.to_datetime(series, errors="coerce", format="mixed")
            parsed_count = int(parsed.notna().sum())
        if not parsed_count:
            return None
        return {
            "time": {
                "count": parsed_count,
                "min": parsed.min().strftime("%H:%M:%S"),
                "max": parsed.max().strftime("%H:%M:%S"),
            }
        }

    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    parsed_count = int(parsed.notna().sum())
    if not parsed_count:
        return None
    return {
        "datetime": {
            "count": parsed_count,
            "min": _json_safe_scalar(parsed.min()),
            "max": _json_safe_scalar(parsed.max()),
        }
    }


def _round_percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def _json_safe_scalar(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            value = value.item()
        except ValueError:
            pass
    if isinstance(value, float):
        return round(value, 6)
    text = str(value)
    return text[:120] if len(text) > 120 else value
