from __future__ import annotations

import asyncio
import json
import sqlite3
import unittest
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

from cerno.config import Settings
from cerno.repositories import ChatRepository
from cerno.services.chat import USER_SAFE_CHAT_ERROR, stream_chat_turn
from cerno.services.tools import ToolTable


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE chat_turns (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            user_message TEXT NOT NULL,
            assistant_message TEXT,
            spawned_page_id TEXT,
            title TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE chat_messages (
            id TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_call_id TEXT,
            tool_name TEXT,
            tool_args TEXT,
            tool_result TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


class FakeDataDocsRepo:
    def get(self, session_id: str) -> None:
        return None


class FakeStreamLLM:
    def __init__(self, first_tool_name: str = "describe_table") -> None:
        self.first_tool_name = first_tool_name
        self.calls: list[list[dict[str, Any]]] = []
        self.created_conversation_metadata: list[dict[str, str] | None] = []

    async def create_conversation(self, *, metadata: dict[str, str] | None = None) -> str:
        self.created_conversation_metadata.append(metadata)
        return f"conv_{len(self.created_conversation_metadata)}"

    async def stream_response(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(kwargs["input"])
        if len(self.calls) == 1:
            args = json.dumps({"table": "orders"})
            yield {"type": "response.created", "response": {"id": "resp_1"}}
            yield {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": self.first_tool_name,
                    "arguments": "",
                },
            }
            yield {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "delta": args,
            }
            yield {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": self.first_tool_name,
                    "arguments": args,
                },
            }
            yield {
                "type": "response.completed",
                "response": {
                    "id": "resp_1",
                    "status": "completed",
                    "output": [
                        {
                            "id": "fc_1",
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": self.first_tool_name,
                            "arguments": args,
                        }
                    ],
                },
            }
            return

        yield {"type": "response.created", "response": {"id": "resp_2"}}
        yield {"type": "response.output_text.delta", "delta": "Done."}
        yield {
            "type": "response.completed",
            "response": {"id": "resp_2", "status": "completed", "output": []},
        }


class BrokenStreamLLM(FakeStreamLLM):
    async def stream_response(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(kwargs["input"])
        yield {"type": "response.created", "response": {"id": "resp_broken"}}
        raise RuntimeError("provider said no tool output found for function call call_1")


async def _collect_events(generator: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in generator:
        events.append(event)
    return events


class ChatStreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _make_conn()
        self.chat_repo = ChatRepository(self.conn)
        self.settings = Settings(_env_file=None, chat_max_llm_calls=4)
        self.tables = {
            "orders": ToolTable(
                name="orders",
                display_name="Orders",
                row_count=2,
                columns=[{"name": "amount", "type": "float", "nullable": False}],
            )
        }

    def tearDown(self) -> None:
        self.conn.close()

    def _run_stream(self, llm_client: Any, turn_id: str | None = None) -> list[dict[str, Any]]:
        with (
            patch("cerno.services.chat.get_object_store", return_value=object()),
            patch("cerno.services.chat._load_chat_tables", return_value=self.tables),
        ):
            return asyncio.run(
                _collect_events(
                    stream_chat_turn(
                        session_id="session_1",
                        user_message="describe the orders table",
                        turn_id=turn_id,
                        settings=self.settings,
                        llm_client=llm_client,
                        files_repo=object(),
                        schemas_repo=object(),
                        chat_repo=self.chat_repo,
                        chat_artifacts_repo=object(),
                        artifacts_repo=object(),
                        data_docs_repo=FakeDataDocsRepo(),
                    )
                )
            )

    def test_stream_persists_matching_tool_output_before_continuing(self) -> None:
        llm_client = FakeStreamLLM()

        events = self._run_stream(llm_client)

        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["assistant_message"], "Done.")
        self.assertEqual(llm_client.calls[1][0]["type"], "function_call_output")
        self.assertEqual(llm_client.calls[1][0]["call_id"], "call_1")
        turn_id = events[0]["turn_id"]
        messages = self.chat_repo.list_messages(turn_id)
        tool_call = next(message for message in messages if message.tool_name == "describe_table")
        tool_result = next(message for message in messages if message.role == "tool")
        self.assertEqual(tool_call.tool_call_id, "call_1")
        self.assertEqual(tool_result.tool_call_id, "call_1")
        self.assertTrue(tool_result.tool_result["ok"])

    def test_unknown_tool_is_returned_to_model_as_tool_output(self) -> None:
        llm_client = FakeStreamLLM(first_tool_name="missing_tool")

        with self.assertLogs("cerno.services.chat", level="ERROR"):
            events = self._run_stream(llm_client)

        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(llm_client.calls[1][0]["call_id"], "call_1")
        output = json.loads(llm_client.calls[1][0]["output"])
        self.assertFalse(output["ok"])
        self.assertIn("unknown tool", output["error"])
        turn_id = events[0]["turn_id"]
        tool_result = next(message for message in self.chat_repo.list_messages(turn_id) if message.role == "tool")
        self.assertEqual(tool_result.tool_call_id, "call_1")

    def test_stream_failure_marks_turn_failed_and_sanitizes_user_error(self) -> None:
        llm_client = BrokenStreamLLM()

        with self.assertLogs("cerno.services.chat", level="ERROR"):
            events = self._run_stream(llm_client)

        self.assertEqual(events[-1], {
            "type": "error",
            "turn_id": events[0]["turn_id"],
            "message": USER_SAFE_CHAT_ERROR,
        })
        turn = self.chat_repo.get_turn(events[0]["turn_id"])
        self.assertIsNotNone(turn)
        self.assertEqual(turn.state, "failed")

    def test_failed_turn_restarts_with_fresh_conversation(self) -> None:
        failed_turn = self.chat_repo.create_turn(
            session_id="session_1",
            user_message="old message",
            metadata={"version": 2, "openai_conversation_id": "poisoned_conv"},
        )
        self.chat_repo.set_turn_state(failed_turn.id, "failed")
        self.conn.commit()
        llm_client = FakeStreamLLM()

        events = self._run_stream(llm_client, turn_id=failed_turn.id)

        new_turn_id = events[0]["turn_id"]
        self.assertNotEqual(new_turn_id, failed_turn.id)
        new_turn = self.chat_repo.get_turn(new_turn_id)
        self.assertIsNotNone(new_turn)
        self.assertEqual(new_turn.metadata["recovered_from_turn_id"], failed_turn.id)
        self.assertEqual(new_turn.metadata["openai_conversation_id"], "conv_1")
