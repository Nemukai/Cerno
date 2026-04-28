from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from cerno.db import connect_memory
from cerno.models import Widget
from cerno.repositories import DashboardRepository, NotebookRepository, SessionRepository, new_id
from cerno.services.engine import DuckDBEngine
from cerno.services.tools import ToolContext, build_tool_registry


@pytest.fixture
def two_parquets(tmp_path: Path) -> tuple[Path, Path]:
    customers = pl.DataFrame({"id": [1, 2, 3], "name": ["Ada", "Bea", "Cal"]})
    orders = pl.DataFrame(
        {"order_id": [10, 11, 12, 13], "customer_id": [1, 1, 2, 3]}
    )
    p1 = tmp_path / "customers.parquet"
    p2 = tmp_path / "orders.parquet"
    customers.write_parquet(p1)
    orders.write_parquet(p2)
    return p1, p2


@pytest.fixture
def tool_ctx(two_parquets: tuple[Path, Path]):
    p1, p2 = two_parquets
    engine = DuckDBEngine()
    engine.register_file(table_name="customers", parquet_path=p1, row_count=3)
    engine.register_file(table_name="orders", parquet_path=p2, row_count=4)
    tables = {
        "customers": engine.to_pandas("customers"),
        "orders": engine.to_pandas("orders"),
    }
    conn = connect_memory()
    SessionRepository(conn).create("s", session_id="sess")
    ctx = ToolContext(
        session_id="sess",
        engine=engine,
        tables=tables,
        notebook_repo=NotebookRepository(conn),
    )
    try:
        yield ctx, conn
    finally:
        engine.close()
        conn.close()


async def _call(registry: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return await registry.get(name).handler(args)


async def test_list_tables_tool(tool_ctx: Any) -> None:
    ctx, _ = tool_ctx
    registry = build_tool_registry(ctx)
    result = await _call(registry, "list_tables", {})
    assert "tables" in result
    names = sorted(t["name"] for t in result["tables"])
    assert names == ["customers", "orders"]
    cust = next(t for t in result["tables"] if t["name"] == "customers")
    assert cust["row_count"] == 3
    assert set(cust["columns"]) == {"id", "name"}


async def test_describe_table_tool(tool_ctx: Any) -> None:
    ctx, _ = tool_ctx
    registry = build_tool_registry(ctx)
    result = await _call(registry, "describe_table", {"table": "orders"})
    assert "columns" in result
    cols = {c["name"] for c in result["columns"]}
    assert cols == {"order_id", "customer_id"}
    assert all({"name", "type", "nullable"} <= set(c) for c in result["columns"])


async def test_run_sql_tool(tool_ctx: Any) -> None:
    ctx, _ = tool_ctx
    registry = build_tool_registry(ctx)
    result = await _call(
        registry,
        "run_sql",
        {"sql": "SELECT COUNT(*) AS n FROM orders", "max_rows": 10},
    )
    assert result["columns"] == ["n"]
    assert result["rows"][0][0] == 4
    assert result["row_count"] == 1
    assert result["truncated"] is False


async def test_run_python_tool(tool_ctx: Any) -> None:
    ctx, _ = tool_ctx
    registry = build_tool_registry(ctx)
    result = await _call(
        registry, "run_python", {"code": "int(customers.shape[0])"}
    )
    assert result["ok"] is True
    assert result["result_preview"] == {"value": 3}
    assert "customers" in result["tables_used"]


async def test_render_widget_tool_collects(tool_ctx: Any) -> None:
    ctx, _ = tool_ctx
    registry = build_tool_registry(ctx)
    result = await _call(
        registry,
        "render_widget",
        {
            "kind": "kpi",
            "title": "Total orders",
            "data": {"value": 4},
        },
    )
    assert "widget" in result
    assert result["widget"]["kind"] == "kpi"
    assert len(ctx.rendered_widgets) == 1
    assert isinstance(ctx.rendered_widgets[0], Widget)


async def test_read_cells_tool(tool_ctx: Any) -> None:
    ctx, conn = tool_ctx
    dashboards = DashboardRepository(conn)
    dashboard = dashboards.create("sess")
    page = dashboards.add_page(
        dashboard_id=dashboard.id, title="t", kind="question", position=0
    )
    from datetime import UTC, datetime

    from cerno.models import NotebookCell

    ctx.notebook_repo.add_cell(
        NotebookCell(
            id=new_id(),
            page_id=page.id,
            order_index=0,
            kind="widget",
            code="",
            output={"widget": {"kind": "kpi", "title": "x", "data": {"value": 1}}},
            created_at=datetime.now(UTC),
        )
    )
    registry = build_tool_registry(ctx)
    result = await _call(registry, "read_cells", {"page_id": page.id})
    assert "cells" in result
    assert len(result["cells"]) == 1
    assert result["cells"][0]["kind"] == "widget"


async def test_tool_schemas_are_responses_shaped(tool_ctx: Any) -> None:
    ctx, _ = tool_ctx
    registry = build_tool_registry(ctx)
    schemas = registry.schemas()
    names = {s["name"] for s in schemas}
    assert names == {
        "list_tables",
        "describe_table",
        "run_sql",
        "run_python",
        "render_widget",
        "read_cells",
    }
    for s in schemas:
        assert s["type"] == "function"
        assert "parameters" in s
        assert "function" not in s
