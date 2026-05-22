from __future__ import annotations

import asyncio
import sqlite3
import unittest
from collections.abc import AsyncIterator
from typing import Any

from cerno.config import Settings
from cerno.llm import LLMClient
from cerno.repositories import LLMUsageRepository


def _make_usage_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE llm_usage (
            user_id TEXT NOT NULL,
            day TEXT NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            call_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            total_response_ms INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE llm_usage_by_model (
            user_id TEXT NOT NULL,
            day TEXT NOT NULL,
            model TEXT NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            call_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            total_response_ms INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day, model)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE llm_call_events (
            id TEXT PRIMARY KEY,
            organization_id TEXT,
            user_id TEXT,
            session_id TEXT,
            turn_id TEXT,
            job_id TEXT,
            provider TEXT NOT NULL,
            model TEXT,
            response_id TEXT,
            request_id TEXT,
            status TEXT NOT NULL,
            error_message TEXT,
            duration_ms INTEGER,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


class FakeStreamLLMClient(LLMClient):
    async def _stream_sse(self, url: str, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "response.created", "response": {"id": "resp_1"}}
        yield {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "usage": {
                    "input_tokens": 10,
                    "input_tokens_details": {"cached_tokens": 4},
                    "output_tokens": 27,
                    "output_tokens_details": {"reasoning_tokens": 3},
                    "total_tokens": 37,
                },
            },
        }


class LLMUsageRepositoryTests(unittest.TestCase):
    def test_add_tokens_keeps_daily_aggregate_and_model_breakdown(self) -> None:
        conn = _make_usage_conn()
        try:
            repo = LLMUsageRepository(conn)

            total = repo.add_tokens("user_1", "2026-05-18", 100, model="gpt-5.5")
            total = repo.add_tokens("user_1", "2026-05-18", 30, model="gpt-5.4-mini")
            total = repo.add_tokens("user_1", "2026-05-18", 20, model="gpt-5.5")

            self.assertEqual(total, 150)
            rows = conn.execute(
                """
                SELECT model, tokens_used
                FROM llm_usage_by_model
                ORDER BY model
                """
            ).fetchall()
            self.assertEqual(
                [(row["model"], row["tokens_used"]) for row in rows],
                [("gpt-5.4-mini", 30), ("gpt-5.5", 120)],
            )
        finally:
            conn.close()


class LLMClientUsageTests(unittest.TestCase):
    def test_stream_response_records_completed_usage_for_model(self) -> None:
        conn = _make_usage_conn()
        try:
            settings = Settings(_env_file=None, llm_api_key="test", llm_model="gpt-5.4-mini")
            client = FakeStreamLLMClient(
                settings=settings,
                usage_repo=LLMUsageRepository(conn),
                user_id="user_1",
            )

            events = asyncio.run(_collect(client.stream_response(input="hello")))

            self.assertEqual([event["type"] for event in events], ["response.created", "response.completed"])
            self.assertEqual(
                conn.execute(
                    "SELECT tokens_used FROM llm_usage WHERE user_id = ?",
                    ("user_1",),
                ).fetchone()["tokens_used"],
                37,
            )
            model_row = conn.execute(
                "SELECT model, tokens_used FROM llm_usage_by_model WHERE user_id = ?",
                ("user_1",),
            ).fetchone()
            self.assertEqual((model_row["model"], model_row["tokens_used"]), ("gpt-5.4-mini", 37))
            detail_row = conn.execute(
                "SELECT input_tokens, output_tokens, cached_tokens, reasoning_tokens, call_count FROM llm_usage"
            ).fetchone()
            self.assertEqual(tuple(detail_row), (10, 27, 4, 3, 1))
            event_row = conn.execute(
                "SELECT model, response_id, total_tokens FROM llm_call_events WHERE user_id = ?",
                ("user_1",),
            ).fetchone()
            self.assertEqual(tuple(event_row), ("gpt-5.4-mini", "resp_1", 37))
        finally:
            conn.close()


async def _collect(events: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event async for event in events]
