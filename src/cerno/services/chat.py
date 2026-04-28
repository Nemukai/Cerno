from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from cerno.config import Settings
from cerno.llm import LLMClient, run_tool_loop
from cerno.models import ChatTurn, NotebookCell, Widget
from cerno.repositories import (
    ChatRepository,
    DashboardRepository,
    FileRepository,
    NotebookRepository,
    SchemaRepository,
    new_id,
)
from cerno.services.engine import DuckDBEngine
from cerno.services.ingest import slugify_table_name
from cerno.services.tools import ToolContext, build_tool_registry

SYSTEM_PROMPT = (
    "You are Cerno, a plain-English data analyst. You are chatting with a non-technical "
    "user about data they just uploaded. Every ingested file is available as a table "
    "(DuckDB view + pandas DataFrame named after the slugified filename). "
    "Back every numeric claim by calling run_sql or run_python — never guess numbers. "
    "Use list_tables and describe_table when you need to remember the schema. "
    "When the answer benefits from a chart, KPI, or table, call render_widget — those "
    "widgets become a new dashboard page the user can pin. If the question is purely "
    "conversational and no widget is useful, just reply in text. Keep the final message "
    "short and direct; the widgets carry the detail."
)


@dataclass
class ChatTurnResult:
    turn: ChatTurn
    assistant_message: str
    spawned_page_id: str | None
    widgets: list[Widget]
    tool_calls: int


async def run_chat_turn(
    *,
    session_id: str,
    user_message: str,
    settings: Settings,
    llm_client: LLMClient,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    dashboards_repo: DashboardRepository,
    notebook_repo: NotebookRepository,
    chat_repo: ChatRepository,
) -> ChatTurnResult:
    del schemas_repo  # reserved for future schema-aware prompts
    turn = chat_repo.create_turn(session_id=session_id, user_message=user_message)
    chat_repo.append_message(turn_id=turn.id, role="user", content=user_message)

    files = files_repo.list_for_session(session_id)
    engine = DuckDBEngine()
    tables: dict[str, pd.DataFrame] = {}
    try:
        for file in files:
            table_name = slugify_table_name(file.filename)
            engine.register_file(
                table_name=table_name,
                parquet_path=file.parquet_path,
                row_count=file.row_count,
            )
            tables[table_name] = engine.to_pandas(table_name)

        ctx = ToolContext(
            session_id=session_id,
            engine=engine,
            tables=tables,
            notebook_repo=notebook_repo,
        )
        registry = build_tool_registry(ctx)

        def on_message(message: dict[str, Any]) -> None:
            _persist_loop_message(chat_repo, turn.id, message)

        try:
            result = await run_tool_loop(
                client=llm_client,
                registry=registry,
                input=[{"role": "user", "content": user_message}],
                instructions=SYSTEM_PROMPT,
                reasoning_effort=settings.llm_reasoning_effort,
                reasoning_summary=settings.llm_reasoning_summary,
                max_calls=settings.chat_max_llm_calls,
                on_message=on_message,
            )
        except Exception:
            chat_repo.set_turn_state(turn.id, "failed")
            raise

        spawned_page_id: str | None = None
        if ctx.rendered_widgets:
            spawned_page_id = _spawn_dashboard_page(
                dashboards_repo=dashboards_repo,
                notebook_repo=notebook_repo,
                session_id=session_id,
                turn_id=turn.id,
                user_message=user_message,
                widgets=ctx.rendered_widgets,
            )

        chat_repo.complete_turn(
            turn_id=turn.id,
            assistant_message=result.final_message,
            spawned_page_id=spawned_page_id,
        )

        return ChatTurnResult(
            turn=turn,
            assistant_message=result.final_message,
            spawned_page_id=spawned_page_id,
            widgets=list(ctx.rendered_widgets),
            tool_calls=result.call_count,
        )
    finally:
        engine.close()


def _persist_loop_message(
    chat_repo: ChatRepository, turn_id: str, message: dict[str, Any]
) -> None:
    role = message.get("role")
    if role == "assistant":
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function", {})
                args_raw = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    args = {"_raw": args_raw}
                chat_repo.append_message(
                    turn_id=turn_id,
                    role="assistant",
                    content=content,
                    tool_name=fn.get("name"),
                    tool_args=args,
                )
        else:
            chat_repo.append_message(
                turn_id=turn_id, role="assistant", content=content
            )
        return
    if role == "tool":
        raw = message.get("content") or "{}"
        try:
            result = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            result = {"_raw": raw}
        chat_repo.append_message(
            turn_id=turn_id,
            role="tool",
            content=raw if isinstance(raw, str) else json.dumps(raw),
            tool_result=result if isinstance(result, dict) else {"value": result},
        )


def _spawn_dashboard_page(
    *,
    dashboards_repo: DashboardRepository,
    notebook_repo: NotebookRepository,
    session_id: str,
    turn_id: str,
    user_message: str,
    widgets: list[Widget],
) -> str:
    dashboard = dashboards_repo.get_for_session(session_id)
    if dashboard is None:
        dashboard = dashboards_repo.create(session_id)
    existing = dashboards_repo.list_pages(dashboard.id)
    position = max((p.position for p in existing), default=-1) + 1
    title = user_message.strip()[:60] or "Question"
    page = dashboards_repo.add_page(
        dashboard_id=dashboard.id,
        title=title,
        kind="question",
        position=position,
        source_chat_turn_id=turn_id,
    )

    for idx, widget in enumerate(widgets):
        cell = NotebookCell(
            id=new_id(),
            page_id=page.id,
            order_index=idx,
            kind="widget",
            code="",
            output={"widget": widget.model_dump()},
            created_at=datetime.now(UTC),
        )
        notebook_repo.add_cell(cell)
    return page.id
