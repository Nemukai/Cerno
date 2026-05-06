from __future__ import annotations

import asyncio
import unittest

import pandas as pd

from cerno.services.engine import DuckDBEngine
from cerno.services.tools import ToolContext, build_tool_registry


class ChatToolRegistryTests(unittest.TestCase):
    def test_list_tables_falls_back_to_bound_dataframes(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            engine=DuckDBEngine(),
            tables={"orders": pd.DataFrame({"order_id": [1, 2], "amount": [10.0, 20.0]})},
        )
        try:
            tool = build_tool_registry(ctx).get("list_tables")
            result = asyncio.run(tool.handler({}))
        finally:
            ctx.engine.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["table_count"], 1)
        self.assertEqual(result["python_dataframe_names"], ["orders"])
        self.assertEqual(result["tables"][0]["columns"], ["order_id", "amount"])

    def test_read_schema_guide_returns_actionable_fallback_when_missing(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            engine=DuckDBEngine(),
            tables={"orders": pd.DataFrame({"order_id": [1]})},
        )
        try:
            tool = build_tool_registry(ctx).get("read_schema_guide")
            result = asyncio.run(tool.handler({}))
        finally:
            ctx.engine.close()

        self.assertFalse(result["ok"])
        self.assertIsNone(result["schema_guide"])
        self.assertEqual(result["fallback_tables"], ["orders"])
        self.assertIn("list_tables", result["message"])

    def test_describe_table_uses_bound_dataframe_fallback(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            engine=DuckDBEngine(),
            tables={"orders": pd.DataFrame({"order_id": [1], "amount": [12.5]})},
        )
        try:
            tool = build_tool_registry(ctx).get("describe_table")
            result = asyncio.run(tool.handler({"table": "orders"}))
        finally:
            ctx.engine.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["table"], "orders")
        self.assertEqual(result["row_count"], 1)
        self.assertEqual([column["name"] for column in result["columns"]], ["order_id", "amount"])

    def test_run_python_executes_against_bound_dataframe(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            engine=DuckDBEngine(),
            tables={"orders": pd.DataFrame({"order_id": [1, 2], "amount": [10.0, 20.0]})},
        )
        try:
            tool = build_tool_registry(ctx).get("run_python")
            result = asyncio.run(tool.handler({"code": "orders['amount'].sum()"}))
        finally:
            ctx.engine.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_preview"], {"value": 30.0})
        self.assertEqual(result["tables_used"], ["orders"])

    def test_render_widget_records_widget_for_chat_turn(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            engine=DuckDBEngine(),
            tables={"orders": pd.DataFrame({"order_id": [1]})},
        )
        try:
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
        finally:
            ctx.engine.close()

        self.assertEqual(len(ctx.rendered_widgets), 1)
        self.assertEqual(result["widget"]["kind"], "kpi")
        self.assertEqual(result["widget"]["title"], "Orders")

    def test_run_python_description_lists_allowed_environment(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            engine=DuckDBEngine(),
            tables={"orders": pd.DataFrame({"order_id": [1]})},
        )
        try:
            tool = build_tool_registry(ctx).get("run_python")
        finally:
            ctx.engine.close()

        self.assertIn("Available pandas DataFrames: orders", tool.description)
        self.assertIn("pd (pandas) and np (numpy)", tool.description)
        self.assertIn("Not allowed: imports", tool.description)


if __name__ == "__main__":
    unittest.main()
