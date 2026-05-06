from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pandas as pd

from cerno.config import Settings
from cerno.llm import LLMClient, ToolCall, conversation_context_options, run_tool_loop
from cerno.models import ChatTurn, Widget
from cerno.repositories import (
    AssetArtifactRepository,
    ChatArtifactRepository,
    ChatRepository,
    DataDocRepository,
    FileRepository,
    SchemaRepository,
)
from cerno.services.artifact_cache import ensure_file_artifact_cached
from cerno.services.engine import DuckDBEngine
from cerno.services.ingest import slugify_table_name
from cerno.services.tools import ToolContext, build_tool_registry
from cerno.storage import get_object_store

SYSTEM_PROMPT = (
    "You are Cerno, a plain-English data analyst. You are chatting with a non-technical "
    "user about data they just uploaded. Every ingested file is available as a table "
    "(DuckDB view + pandas DataFrame named after the slugified filename). "
    "Back every numeric claim by calling run_python — never guess numbers. "
    "Use read_schema_guide when you need to understand what the data contains, where "
    "fields live, caveats, relationships, or good ways to answer the user's question. "
    "Use list_tables and describe_table when you need exact dataframe columns. "
    "When the answer benefits from a chart, KPI, or table, call render_widget — those "
    "widgets are shown directly in this chat. If the question is purely "
    "conversational and no widget is useful, just reply in text. "
    "Format responses in visually clean markdown. Use bullet points or short paragraphs. "
    "Keep the final message short, grounded, and clean; the widgets carry the detail."
)


@dataclass
class ChatTurnResult:
    turn: ChatTurn
    assistant_message: str
    spawned_page_id: str | None
    widgets: list[Widget]
    tool_calls: int


@dataclass
class ChatRuntime:
    turn: ChatTurn
    conversation_id: str
    engine: DuckDBEngine
    registry: Any
    ctx: ToolContext


async def run_chat_turn(
    *,
    session_id: str,
    user_message: str,
    turn_id: str | None = None,
    settings: Settings,
    llm_client: LLMClient,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    chat_repo: ChatRepository,
    chat_artifacts_repo: ChatArtifactRepository,
    artifacts_repo: AssetArtifactRepository,
    data_docs_repo: DataDocRepository,
) -> ChatTurnResult:
    runtime = await _prepare_chat_runtime(
        session_id=session_id,
        user_message=user_message,
        turn_id=turn_id,
        settings=settings,
        llm_client=llm_client,
        files_repo=files_repo,
        chat_repo=chat_repo,
        artifacts_repo=artifacts_repo,
        data_docs_repo=data_docs_repo,
        schemas_repo=schemas_repo,
    )
    try:
        def on_message(message: dict[str, Any]) -> None:
            _persist_loop_message(chat_repo, runtime.turn.id, message)
            chat_repo.conn.commit()

        try:
            context_options = conversation_context_options(session_id, runtime.turn.id)
            result = await run_tool_loop(
                client=llm_client,
                registry=runtime.registry,
                input=[{"role": "user", "content": user_message}],
                instructions=SYSTEM_PROMPT,
                conversation=runtime.conversation_id,
                **context_options,
                reasoning_effort=settings.llm_reasoning_effort,
                reasoning_summary=settings.llm_reasoning_summary,
                max_calls=settings.chat_max_llm_calls,
                on_message=on_message,
            )
        except Exception:
            chat_repo.set_turn_state(runtime.turn.id, "failed")
            chat_repo.conn.commit()
            raise

        if runtime.ctx.rendered_widgets:
            _persist_widget_artifacts(
                chat_artifacts_repo=chat_artifacts_repo,
                session_id=session_id,
                turn_id=runtime.turn.id,
                widgets=runtime.ctx.rendered_widgets,
            )

        chat_repo.complete_turn(
            turn_id=runtime.turn.id,
            assistant_message=result.final_message,
            spawned_page_id=None,
        )
        _update_turn_metadata(
            chat_repo,
            runtime.turn,
            {
                "openai_conversation_id": runtime.conversation_id,
                "last_response_id": result.response_id,
                "version": 2,
            },
        )
        chat_repo.conn.commit()
        saved_turn = chat_repo.get_turn(runtime.turn.id) or runtime.turn

        return ChatTurnResult(
            turn=saved_turn,
            assistant_message=result.final_message,
            spawned_page_id=None,
            widgets=list(runtime.ctx.rendered_widgets),
            tool_calls=result.call_count,
        )
    finally:
        runtime.engine.close()


async def stream_chat_turn(
    *,
    session_id: str,
    user_message: str,
    turn_id: str | None = None,
    settings: Settings,
    llm_client: LLMClient,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    chat_repo: ChatRepository,
    chat_artifacts_repo: ChatArtifactRepository,
    artifacts_repo: AssetArtifactRepository,
    data_docs_repo: DataDocRepository,
) -> AsyncIterator[dict[str, Any]]:
    runtime = await _prepare_chat_runtime(
        session_id=session_id,
        user_message=user_message,
        turn_id=turn_id,
        settings=settings,
        llm_client=llm_client,
        files_repo=files_repo,
        chat_repo=chat_repo,
        artifacts_repo=artifacts_repo,
        data_docs_repo=data_docs_repo,
        schemas_repo=schemas_repo,
    )
    yield {
        "type": "turn_started",
        "turn_id": runtime.turn.id,
        "conversation_id": runtime.conversation_id,
        "message": user_message,
    }

    response_id: str | None = None
    final_message = ""
    call_count = 0
    pending_input: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    context_options = conversation_context_options(session_id, runtime.turn.id)

    try:
        while True:
            call_count += 1
            tool_outputs: list[dict[str, Any]] = []
            streamed_calls: dict[str, dict[str, Any]] = {}

            async for event in llm_client.stream_response(
                input=pending_input,
                tools=runtime.registry.schemas(),
                instructions=SYSTEM_PROMPT,
                conversation=runtime.conversation_id,
                context_management=context_options["context_management"],
                prompt_cache_key=context_options["prompt_cache_key"],
                prompt_cache_retention=context_options["prompt_cache_retention"],
                reasoning_effort=settings.llm_reasoning_effort,
                reasoning_summary=settings.llm_reasoning_summary,
            ):
                event_type = str(event.get("type") or "")
                if event_type == "response.created":
                    response = event.get("response") or {}
                    if isinstance(response, dict) and isinstance(response.get("id"), str):
                        response_id = response["id"]
                    continue
                if event_type == "response.output_text.delta":
                    delta = str(event.get("delta") or "")
                    if delta:
                        final_message += delta
                        yield {"type": "assistant_delta", "turn_id": runtime.turn.id, "delta": delta}
                    continue
                if "reasoning" in event_type and event_type.endswith(".delta"):
                    delta = str(event.get("delta") or "")
                    if delta:
                        yield {"type": "reasoning_delta", "turn_id": runtime.turn.id, "delta": delta}
                    continue
                if event_type == "response.function_call_arguments.delta":
                    key = _stream_tool_key(event)
                    call = streamed_calls.setdefault(key, {"arguments": ""})
                    call["arguments"] = str(call.get("arguments") or "") + str(event.get("delta") or "")
                    yield {
                        "type": "tool_call_arguments_delta",
                        "turn_id": runtime.turn.id,
                        "call_id": call.get("call_id") or key,
                        "delta": str(event.get("delta") or ""),
                    }
                    continue
                if event_type == "response.output_item.added":
                    item = event.get("item") or {}
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        key = _stream_tool_key(event, item)
                        streamed_calls[key] = {
                            **streamed_calls.get(key, {}),
                            "call_id": item.get("call_id") or item.get("id") or key,
                            "name": item.get("name") or "",
                            "arguments": item.get("arguments") or streamed_calls.get(key, {}).get("arguments", ""),
                        }
                        yield {
                            "type": "tool_call_started",
                            "turn_id": runtime.turn.id,
                            "call_id": streamed_calls[key]["call_id"],
                            "name": streamed_calls[key]["name"],
                        }
                    continue
                if event_type == "response.output_item.done":
                    item = event.get("item") or {}
                    if not isinstance(item, dict) or item.get("type") != "function_call":
                        continue
                    key = _stream_tool_key(event, item)
                    call = streamed_calls.get(key, {})
                    tool_call = ToolCall(
                        call_id=str(item.get("call_id") or call.get("call_id") or item.get("id") or key),
                        name=str(item.get("name") or call.get("name") or ""),
                        arguments=_parse_tool_arguments(item.get("arguments") or call.get("arguments") or "{}"),
                    )
                    _persist_tool_call(chat_repo, runtime.turn.id, tool_call)
                    chat_repo.conn.commit()
                    yield {
                        "type": "tool_call_done",
                        "turn_id": runtime.turn.id,
                        "call_id": tool_call.call_id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    }
                    tool = runtime.registry.get(tool_call.name)
                    result = await tool.handler(tool_call.arguments)
                    payload = json.dumps(result)
                    chat_repo.append_message(
                        turn_id=runtime.turn.id,
                        role="tool",
                        content=payload,
                        tool_result=result,
                    )
                    chat_repo.conn.commit()
                    yield {
                        "type": "tool_result",
                        "turn_id": runtime.turn.id,
                        "call_id": tool_call.call_id,
                        "name": tool_call.name,
                        "result": result,
                    }
                    tool_outputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": tool_call.call_id,
                            "output": payload,
                        }
                    )
                    continue
                if event_type == "response.completed":
                    response = event.get("response") or {}
                    if isinstance(response, dict):
                        response_id = response.get("id") or response_id

            if tool_outputs and call_count < settings.chat_max_llm_calls:
                pending_input = tool_outputs
                continue
            break

        if final_message:
            chat_repo.append_message(turn_id=runtime.turn.id, role="assistant", content=final_message)
        if runtime.ctx.rendered_widgets:
            _persist_widget_artifacts(
                chat_artifacts_repo=chat_artifacts_repo,
                session_id=session_id,
                turn_id=runtime.turn.id,
                widgets=runtime.ctx.rendered_widgets,
            )
        chat_repo.complete_turn(
            turn_id=runtime.turn.id,
            assistant_message=final_message,
            spawned_page_id=None,
        )
        _update_turn_metadata(
            chat_repo,
            runtime.turn,
            {
                "openai_conversation_id": runtime.conversation_id,
                "last_response_id": response_id,
                "version": 2,
            },
        )
        chat_repo.conn.commit()
        yield {
            "type": "done",
            "turn_id": runtime.turn.id,
            "conversation_id": runtime.conversation_id,
            "assistant_message": final_message,
            "response_id": response_id,
            "widgets": [widget.model_dump(mode="json") for widget in runtime.ctx.rendered_widgets],
        }
    except Exception as exc:
        chat_repo.set_turn_state(runtime.turn.id, "failed")
        chat_repo.conn.commit()
        yield {"type": "error", "turn_id": runtime.turn.id, "message": str(exc)}
    finally:
        runtime.engine.close()


async def _prepare_chat_runtime(
    *,
    session_id: str,
    user_message: str,
    turn_id: str | None,
    settings: Settings,
    llm_client: LLMClient,
    files_repo: FileRepository,
    chat_repo: ChatRepository,
    artifacts_repo: AssetArtifactRepository,
    data_docs_repo: DataDocRepository,
    schemas_repo: SchemaRepository,
) -> ChatRuntime:
    del schemas_repo  # reserved for future schema-aware prompts
    turn = chat_repo.get_turn(turn_id) if turn_id else None
    if turn is not None and turn.session_id != session_id:
        raise ValueError("chat turn does not belong to this session")
    if turn is None:
        turn = chat_repo.create_turn(
            session_id=session_id,
            user_message=user_message,
            title=_default_turn_title(user_message),
            metadata={"version": 2},
        )
    else:
        chat_repo.set_turn_state(turn.id, "pending")

    conversation_id = str(turn.metadata.get("openai_conversation_id") or "")
    if not conversation_id:
        conversation_id = await llm_client.create_conversation(
            metadata={"session_id": session_id, "turn_id": turn.id}
        )
        _update_turn_metadata(
            chat_repo,
            turn,
            {"openai_conversation_id": conversation_id, "version": 2},
        )
        refreshed = chat_repo.get_turn(turn.id)
        if refreshed is not None:
            turn = refreshed

    chat_repo.append_message(turn_id=turn.id, role="user", content=user_message)
    chat_repo.conn.commit()

    files = files_repo.list_for_session(session_id)
    engine = DuckDBEngine()
    object_store = get_object_store(settings)
    tables: dict[str, pd.DataFrame] = {}
    for file in files:
        table_name = slugify_table_name(file.filename)
        parquet_path = ensure_file_artifact_cached(
            file=file,
            artifact_type="processed_parquet",
            local_path=file.parquet_path,
            settings=settings,
            artifacts_repo=artifacts_repo,
            object_store=object_store,
        )
        if not parquet_path:
            continue
        engine.register_file(
            table_name=table_name,
            parquet_path=parquet_path,
            row_count=file.row_count,
        )
        tables[table_name] = engine.to_pandas(table_name)

    ctx = ToolContext(
        session_id=session_id,
        engine=engine,
        tables=tables,
        data_doc=data_docs_repo.get(session_id),
    )
    return ChatRuntime(
        turn=turn,
        conversation_id=conversation_id,
        engine=engine,
        registry=build_tool_registry(ctx),
        ctx=ctx,
    )


def _persist_loop_message(chat_repo: ChatRepository, turn_id: str, message: dict[str, Any]) -> None:
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
            chat_repo.append_message(turn_id=turn_id, role="assistant", content=content)
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


def _persist_tool_call(chat_repo: ChatRepository, turn_id: str, call: ToolCall) -> None:
    chat_repo.append_message(
        turn_id=turn_id,
        role="assistant",
        content="",
        tool_name=call.name,
        tool_args=call.arguments,
    )


def _update_turn_metadata(
    chat_repo: ChatRepository,
    turn: ChatTurn,
    updates: dict[str, Any],
) -> None:
    metadata = {**turn.metadata}
    for key, value in updates.items():
        if value is not None:
            metadata[key] = value
    chat_repo.update_turn(turn_id=turn.id, metadata=metadata)
    turn.metadata = metadata


def _stream_tool_key(event: dict[str, Any], item: dict[str, Any] | None = None) -> str:
    if item:
        for key in ("call_id", "id"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    for key in ("item_id", "output_item_id", "call_id"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    output_index = event.get("output_index")
    if isinstance(output_index, int):
        return f"output:{output_index}"
    return "tool_call"


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _default_turn_title(user_message: str) -> str:
    title = " ".join(user_message.strip().split())
    return title[:80] or "New chat"


def _persist_widget_artifacts(
    *,
    chat_artifacts_repo: ChatArtifactRepository,
    session_id: str,
    turn_id: str,
    widgets: list[Widget],
) -> None:
    for idx, widget in enumerate(widgets):
        chat_artifacts_repo.create(
            session_id=session_id,
            turn_id=turn_id,
            artifact_type=f"widget:{widget.kind}",
            title=widget.title,
            inline_payload={"widget": widget.model_dump()},
            order_index=idx,
        )
