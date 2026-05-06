from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from cerno.config import Settings
from cerno.models import AssetArtifact, File, WorkspaceTable
from cerno.services.chat import _file_spec_from_raw_header
from cerno.services.tools import ToolContext, ToolTable, build_tool_registry


class ChatToolRegistryTests(unittest.TestCase):
    def test_list_tables_uses_postgres_catalog_entries(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            tables={
                "orders": ToolTable(
                    name="orders",
                    display_name="Orders",
                    row_count=2,
                    columns=[
                        {"name": "order_id", "type": "int", "nullable": False},
                        {"name": "amount", "type": "float", "nullable": False},
                    ],
                )
            },
        )
        tool = build_tool_registry(ctx).get("list_tables")
        result = asyncio.run(tool.handler({}))

        self.assertTrue(result["ok"])
        self.assertEqual(result["table_count"], 1)
        self.assertEqual(result["python_dataframe_names"], ["orders"])
        self.assertEqual(result["tables"][0]["columns"], ["order_id", "amount"])

    def test_read_schema_guide_returns_actionable_fallback_when_missing(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            tables={
                "orders": ToolTable(
                    name="orders",
                    display_name="Orders",
                    row_count=1,
                    columns=[{"name": "order_id", "type": "int", "nullable": False}],
                )
            },
        )
        tool = build_tool_registry(ctx).get("read_schema_guide")
        result = asyncio.run(tool.handler({}))

        self.assertFalse(result["ok"])
        self.assertIsNone(result["schema_guide"])
        self.assertEqual(result["fallback_tables"], ["orders"])
        self.assertIn("list_tables", result["message"])

    def test_describe_table_uses_catalog_metadata(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            tables={
                "orders": ToolTable(
                    name="orders",
                    display_name="Orders",
                    row_count=1,
                    columns=[
                        {"name": "order_id", "type": "int", "nullable": False},
                        {"name": "amount", "type": "float", "nullable": False},
                    ],
                )
            },
        )
        tool = build_tool_registry(ctx).get("describe_table")
        result = asyncio.run(tool.handler({"table": "orders"}))

        self.assertTrue(result["ok"])
        self.assertEqual(result["table"], "orders")
        self.assertEqual(result["row_count"], 1)
        self.assertEqual([column["name"] for column in result["columns"]], ["order_id", "amount"])

    def test_run_python_executes_against_bound_dataframe(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            tables={
                "orders": ToolTable(
                    name="orders",
                    display_name="Orders",
                    row_count=2,
                    columns=[
                        {"name": "order_id", "type": "int", "nullable": False},
                        {"name": "amount", "type": "float", "nullable": False},
                    ],
                    dataframe=pd.DataFrame({"order_id": [1, 2], "amount": [10.0, 20.0]}),
                )
            },
        )
        tool = build_tool_registry(ctx).get("run_python")
        result = asyncio.run(tool.handler({"code": "orders['amount'].sum()"}))

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_preview"], {"value": 30.0})
        self.assertEqual(result["tables_used"], ["orders"])

    def test_render_widget_records_widget_for_chat_turn(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            tables={
                "orders": ToolTable(
                    name="orders",
                    display_name="Orders",
                    row_count=1,
                    columns=[{"name": "order_id", "type": "int", "nullable": False}],
                )
            },
        )
        tool = build_tool_registry(ctx).get("render_widget")
        result = asyncio.run(
            tool.handler(
                {
                    "kind": "kpi",
                    "title": "Orders",
                    "data": {"value": 1},
                    "options": {},
                    "caption": "Total orders",
                }
            )
        )

        self.assertEqual(len(ctx.rendered_widgets), 1)
        self.assertEqual(result["widget"]["kind"], "kpi")
        self.assertEqual(result["widget"]["title"], "Orders")

    def test_run_python_requires_loaded_dataframe(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            tables={
                "orders": ToolTable(
                    name="orders",
                    display_name="Orders",
                    row_count=1,
                    columns=[{"name": "order_id", "type": "int", "nullable": False}],
                )
            },
        )
        tool = build_tool_registry(ctx).get("run_python")
        result = asyncio.run(tool.handler({"code": "orders.head()"}))

        self.assertFalse(result["ok"])
        self.assertEqual(result["available_tables"], ["orders"])

    def test_run_python_description_lists_allowed_environment(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            tables={
                "orders": ToolTable(
                    name="orders",
                    display_name="Orders",
                    row_count=1,
                    columns=[{"name": "order_id", "type": "int", "nullable": False}],
                    dataframe=pd.DataFrame({"order_id": [1]}),
                )
            },
        )
        tool = build_tool_registry(ctx).get("run_python")

        self.assertIn("Available pandas DataFrames: orders", tool.description)
        self.assertIn("pd (pandas) and np (numpy)", tool.description)
        self.assertIn("Not allowed: imports", tool.description)

    def test_raw_header_fallback_builds_reingest_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_path = root / "raw.parquet"
            pd.DataFrame(
                {
                    "c0": ["Report", "FOR DATE", "2026-03-16", "2026-03-16"],
                    "c1": ["", "SHIFT", "1", "2"],
                    "c2": ["", "Rate", "50.5", "75.0"],
                }
            ).to_parquet(raw_path, index=False)
            settings = Settings(data_root=root, _env_file=None)
            spec = _file_spec_from_raw_header(
                file=File(
                    id="file-1",
                    session_id="session-1",
                    filename="tolls.xlsx",
                    parquet_path="",
                    row_count=2,
                    schema_version=1,
                    header_row=1,
                    created_at=datetime.now(UTC),
                ),
                table=WorkspaceTable(
                    id="table-1",
                    session_id="session-1",
                    legacy_file_id="file-1",
                    display_name="Tolls",
                    row_count=2,
                    created_at=datetime.now(UTC),
                ),
                raw_artifact=AssetArtifact(
                    id="artifact-1",
                    user_id="user-1",
                    session_id="session-1",
                    file_id="file-1",
                    artifact_type="raw_parquet",
                    storage_backend="r2",
                    object_key="raw.parquet",
                    created_at=datetime.now(UTC),
                ),
                settings=settings,
                object_store=FakeObjectStore(raw_path),
            )

        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual([column.name for column in spec.columns], ["FOR DATE", "SHIFT", "Rate"])
        self.assertEqual([column.dtype for column in spec.columns], ["date", "int", "float"])

class FakeObjectStore:
    backend = "r2"

    def __init__(self, source: Path) -> None:
        self.source = source

    def get_to_path(self, object_key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source, destination)
        return destination


if __name__ == "__main__":
    unittest.main()
