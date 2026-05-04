from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from cerno.llm import Tool, ToolRegistry
from cerno.models import DataDoc, Widget
from cerno.repositories import NotebookRepository
from cerno.services.engine import DuckDBEngine
from cerno.services.sandbox import run_python


@dataclass
class ToolContext:
    session_id: str
    engine: DuckDBEngine
    tables: dict[str, pd.DataFrame]
    notebook_repo: NotebookRepository
    data_doc: DataDoc | None = None
    rendered_widgets: list[Widget] = field(default_factory=list)


def build_tool_registry(ctx: ToolContext) -> ToolRegistry:
    registry = ToolRegistry()

    async def list_tables(_args: dict[str, Any]) -> dict[str, Any]:
        tables = []
        for info in ctx.engine.list_tables():
            described = ctx.engine.describe_table(info.name)
            tables.append(
                {
                    "name": info.name,
                    "row_count": info.row_count,
                    "columns": [c["column"] for c in described],
                }
            )
        return {"tables": tables}

    async def describe_table(args: dict[str, Any]) -> dict[str, Any]:
        table = str(args["table"])
        described = ctx.engine.describe_table(table)
        return {
            "columns": [
                {"name": c["column"], "type": c["type"], "nullable": c["nullable"]}
                for c in described
            ]
        }

    async def run_python_handler(args: dict[str, Any]) -> dict[str, Any]:
        code = str(args["code"])
        return run_python(code, tables=ctx.tables).to_dict()

    async def render_widget(args: dict[str, Any]) -> dict[str, Any]:
        widget = Widget(
            kind=args["kind"],
            title=str(args["title"]),
            data=args["data"],
            options=args.get("options") or {},
            caption=args.get("caption"),
        )
        ctx.rendered_widgets.append(widget)
        return {"widget": widget.model_dump()}

    async def read_cells(args: dict[str, Any]) -> dict[str, Any]:
        page_id = str(args["page_id"])
        cells = ctx.notebook_repo.list_for_page(page_id)
        return {"cells": [c.model_dump(mode="json") for c in cells]}

    async def read_data_docs(_args: dict[str, Any]) -> dict[str, Any]:
        if ctx.data_doc is None:
            return {"docs": None}
        return {"docs": ctx.data_doc.model_dump(mode="json")}

    registry.register(
        Tool(
            name="list_tables",
            description="List every dataframe available for Python analysis in this session.",
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
                "Run Python in a restricted sandbox. Every ingested file is pre-bound "
                "as a pandas DataFrame named after the slugified filename. `pd` and `np` "
                "are available. The value of the last expression is returned in result_preview."
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
                "Emit a widget for the dashboard page spawned by this chat turn. "
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
            name="read_cells",
            description="Read all notebook cells on a dashboard page (read-only).",
            parameters={
                "type": "object",
                "properties": {"page_id": {"type": "string"}},
                "required": ["page_id"],
                "additionalProperties": False,
            },
            handler=read_cells,
        )
    )
    registry.register(
        Tool(
            name="read_data_docs",
            description=(
                "Read Cerno's internal documentation for this session: file meanings, "
                "grain, key fields, relationships, caveats, glossary, and starter questions."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=read_data_docs,
        )
    )

    return registry
