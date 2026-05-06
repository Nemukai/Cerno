from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from cerno.llm import Tool, ToolRegistry
from cerno.models import DataDoc, Widget
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
            "columns": table.columns,
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
        widget = Widget(
            kind=args["kind"],
            title=str(args["title"]),
            data=args["data"],
            options=args.get("options") or {},
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
            description="Return the columns (name, type, nullable) for a single table.",
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
                "Already available libraries: pandas as pd, numpy as np. "
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
                "Call this for every chart, KPI, or table you want the user to see."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["kpi", "bar", "line", "pie", "table", "markdown"],
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
