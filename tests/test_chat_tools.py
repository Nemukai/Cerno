from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import unittest
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from cerno.config import Settings
from cerno.models import AssetArtifact, DocumentChunk, DocumentChunkSearchRow, File, WorkspaceTable
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

    def test_tool_handlers_return_clear_errors_for_missing_required_args(self) -> None:
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
        registry = build_tool_registry(ctx)

        describe_result = asyncio.run(registry.get("describe_table").handler({}))
        python_result = asyncio.run(registry.get("run_python").handler({}))
        widget_result = asyncio.run(
            registry.get("render_widget").handler({"kind": "table", "title": "Orders"})
        )

        self.assertFalse(describe_result["ok"])
        self.assertIn("table", describe_result["error"])
        self.assertFalse(python_result["ok"])
        self.assertIn("code", python_result["error"])
        self.assertFalse(widget_result["ok"])
        self.assertIn("data", widget_result["error"])

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
        self.assertIn("Do not write import statements", tool.description)
        self.assertIn("Already available libraries: pandas as pd, numpy as np", tool.description)
        self.assertIn("Not allowed: import statements", tool.description)

    def test_render_widget_description_specifies_data_shape(self) -> None:
        ctx = ToolContext(session_id="s1", tables={})
        tool = build_tool_registry(ctx).get("render_widget")

        self.assertIn("Prioritize this for analytical answers", tool.description)
        self.assertIn("data field is required", tool.description)
        self.assertIn("must be an object", tool.description)
        self.assertIn("columns", tool.description)
        self.assertIn("rows", tool.description)

    def test_search_regulations_returns_scoped_cited_chunks(self) -> None:
        store = FakeRegulationSearchStore(
            rows=[
                _chunk_row("chunk-1", session_id="s1", organization_id="org-1"),
                _chunk_row("chunk-2", session_id="other-session", organization_id="org-1"),
            ]
        )
        ctx = ToolContext(
            session_id="s1",
            tables={},
            organization_id="org-1",
            user_id="user-1",
            settings=Settings(_env_file=None),
            regulation_search_store=store,
            embedding_provider=FakeEmbeddingProvider(),
        )
        registry = build_tool_registry(ctx)

        self.assertIn("search_regulations", registry.names())
        result = asyncio.run(
            registry.get("search_regulations").handler(
                {"query": "maximum shipment value", "top_n": 3}
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_count"], 1)
        chunk = result["chunks"][0]
        self.assertEqual(chunk["chunk_id"], "chunk-1")
        self.assertTrue(chunk["low_confidence"])
        self.assertEqual(chunk["citation"]["document"], "Regulation.pdf")
        self.assertEqual(chunk["citation"]["page"], 7)
        self.assertIn("section 12", chunk["citation"]["label"])
        self.assertEqual(
            store.calls,
            [
                ("bm25", "s1", "org-1", "user-1"),
                ("vector", "s1", "org-1", "user-1"),
            ],
        )

    def test_search_regulations_requires_query(self) -> None:
        result = asyncio.run(
            build_tool_registry(ToolContext(session_id="s1", tables={}))
            .get("search_regulations")
            .handler({})
        )

        self.assertFalse(result["ok"])
        self.assertIn("query", result["error"])

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

    def test_run_python_result_preview_is_json_safe_for_dates(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            tables={
                "orders": ToolTable(
                    name="orders",
                    display_name="Orders",
                    row_count=2,
                    columns=[{"name": "FOR DATE", "type": "date", "nullable": False}],
                    dataframe=pd.DataFrame(
                        {"FOR DATE": [date(2026, 3, 16), date(2026, 3, 17)]}
                    ),
                )
            },
        )
        result = asyncio.run(
            build_tool_registry(ctx)
            .get("run_python")
            .handler({"code": "orders.groupby('FOR DATE').size().reset_index(name='count')"})
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["result_preview"],
            {
                "columns": ["FOR DATE", "count"],
                "rows": [["2026-03-16", 1], ["2026-03-17", 1]],
                "row_count": 2,
                "truncated": False,
            },
        )
        json.dumps(result)


class FakeObjectStore:
    backend = "r2"

    def __init__(self, source: Path) -> None:
        self.source = source

    def get_to_path(self, object_key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source, destination)
        return destination


class FakeEmbeddingProvider:
    name = "fake"
    model = "fake"
    dimension = 3

    async def embed_texts(
        self,
        texts: Sequence[str],
        *,
        llm_client: object | None = None,
    ) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class FakeRegulationSearchStore:
    def __init__(self, rows: Sequence[DocumentChunkSearchRow]) -> None:
        self.rows = list(rows)
        self.calls: list[tuple[str, str, str, str]] = []

    def search_bm25(
        self,
        *,
        query: str,
        session_id: str,
        organization_id: str,
        user_id: str,
        limit: int,
    ) -> list[DocumentChunkSearchRow]:
        self.calls.append(("bm25", session_id, organization_id, user_id))
        return self._filtered(session_id, organization_id)[:limit]

    def search_vector(
        self,
        *,
        embedding: Sequence[float],
        session_id: str,
        organization_id: str,
        user_id: str,
        limit: int,
    ) -> list[DocumentChunkSearchRow]:
        self.calls.append(("vector", session_id, organization_id, user_id))
        return self._filtered(session_id, organization_id)[:limit]

    def _filtered(self, session_id: str, organization_id: str) -> list[DocumentChunkSearchRow]:
        return [
            row
            for row in self.rows
            if row.chunk.session_id == session_id and row.chunk.organization_id == organization_id
        ]


def _chunk_row(
    chunk_id: str,
    *,
    session_id: str,
    organization_id: str,
) -> DocumentChunkSearchRow:
    return DocumentChunkSearchRow(
        chunk=DocumentChunk(
            id=chunk_id,
            document_id="doc-1",
            session_id=session_id,
            organization_id=organization_id,
            chunk_index=0,
            text="Context: Regulation.pdf > Section 12\n\nClause text for shipment values.",
            heading_path=["Section 12"],
            section_no="12",
            clause_no="12.1",
            page_number=7,
            start_char=0,
            end_char=60,
            low_confidence=True,
            created_at=datetime.now(UTC),
        ),
        document_filename="Regulation.pdf",
        rank=0.9,
    )


if __name__ == "__main__":
    unittest.main()
