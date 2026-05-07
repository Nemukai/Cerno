from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pandas as pd

from cerno.config import Settings
from cerno.llm import LLMClient, ToolCall, conversation_context_options, run_tool_loop
from cerno.models import (
    AssetArtifact,
    ChatMessage,
    ChatTurn,
    DataDoc,
    DataDocFile,
    File,
    FileSchema,
    Widget,
    WorkspaceTable,
)
from cerno.repositories import (
    AssetArtifactRepository,
    ChatArtifactRepository,
    ChatRepository,
    DataDocRepository,
    FileRepository,
    SchemaRepository,
    WorkspaceTableRepository,
)
from cerno.services.ingest import slugify_table_name
from cerno.services.reingest import ColumnSpec, FileSpec, ReingestError, reingest_file
from cerno.services.tools import ToolContext, ToolTable, build_tool_registry
from cerno.storage import ObjectStore, get_object_store

SYSTEM_PROMPT = (
    "You are Cerno, a plain-English data analyst. You are chatting with a non-technical "
    "user about data they just uploaded. Every processed table is registered in Postgres "
    "and made available to run_python as a pandas DataFrame named after the slugified filename. "
    "Back every numeric claim by calling run_python — never guess numbers. "
    "When using run_python, never write import statements: pandas is already available as pd, "
    "numpy is already available as np, and no other libraries are available. "
    "For analytical answers, prefer producing visible widgets over text-only summaries: "
    "call render_widget for KPI totals, grouped result tables, comparisons, distributions, "
    "time trends, rankings, or any answer where a chart/table/card would help the user inspect the result. "
    "If you compute a dataframe or grouped result, render it as a table widget unless another chart is clearly better. "
    "Use read_schema_guide when you need to understand what the data contains, where "
    "fields live, caveats, relationships, or good ways to answer the user's question. "
    "Use list_tables and describe_table when you need exact dataframe columns. "
    "When the answer benefits from a chart, KPI, or table, call render_widget before the final text — those "
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
    final_message_id: str | None = None

    def on_message(message: dict[str, Any]) -> None:
        nonlocal final_message_id
        saved_message = _persist_loop_message(chat_repo, runtime.turn.id, message)
        if (
            saved_message is not None
            and saved_message.role == "assistant"
            and saved_message.tool_name is None
        ):
            final_message_id = saved_message.id
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
            model=settings.chat_model,
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
            message_id=final_message_id,
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
                model=settings.chat_model,
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
                    try:
                        result = await tool.handler(tool_call.arguments)
                    except Exception as exc:
                        result = {
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    payload = json.dumps(result, default=str)
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

        final_chat_message = None
        if final_message:
            final_chat_message = chat_repo.append_message(
                turn_id=runtime.turn.id,
                role="assistant",
                content=final_message,
            )
        if runtime.ctx.rendered_widgets:
            _persist_widget_artifacts(
                chat_artifacts_repo=chat_artifacts_repo,
                session_id=session_id,
                turn_id=runtime.turn.id,
                message_id=final_chat_message.id if final_chat_message else None,
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

    object_store = get_object_store(settings)
    data_doc = data_docs_repo.get(session_id)
    tables = _load_chat_tables(
        session_id=session_id,
        settings=settings,
        files_repo=files_repo,
        schemas_repo=schemas_repo,
        artifacts_repo=artifacts_repo,
        object_store=object_store,
        data_doc=data_doc,
    )

    ctx = ToolContext(
        session_id=session_id,
        tables=tables,
        data_doc=data_doc,
    )
    return ChatRuntime(
        turn=turn,
        conversation_id=conversation_id,
        registry=build_tool_registry(ctx),
        ctx=ctx,
    )


def _load_chat_tables(
    *,
    session_id: str,
    settings: Settings,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    artifacts_repo: AssetArtifactRepository,
    object_store: ObjectStore,
    data_doc: DataDoc | None,
) -> dict[str, ToolTable]:
    files = files_repo.list_for_session(session_id)
    files_by_id = {file.id: file for file in files}
    tables_repo = WorkspaceTableRepository(files_repo.conn)
    workspace_tables = tables_repo.list_for_session(session_id)
    if not workspace_tables:
        workspace_tables = [
            WorkspaceTable(
                id=file.id,
                session_id=session_id,
                legacy_file_id=file.id,
                display_name=file.friendly_name or file.filename,
                row_count=file.row_count,
                current_schema_version=file.schema_version,
                created_at=file.created_at,
            )
            for file in files
        ]

    seen_names: set[str] = set()
    tables: dict[str, ToolTable] = {}
    doc_files = {file.file_id: file for file in data_doc.files} if data_doc else {}
    for workspace_table in workspace_tables:
        file = files_by_id.get(workspace_table.legacy_file_id or "")
        if file is None:
            continue
        table_name = _unique_table_name(slugify_table_name(file.filename), seen_names)
        schema = schemas_repo.get(file.id, workspace_table.current_schema_version)
        doc_file = doc_files.get(file.id)
        artifact = _processed_artifact_for_table(workspace_table, file, artifacts_repo)
        if artifact is None:
            artifact, materialize_error = _materialize_processed_artifact(
                file=file,
                table=workspace_table,
                schema=schema,
                doc_file=doc_file,
                settings=settings,
                files_repo=files_repo,
                schemas_repo=schemas_repo,
                artifacts_repo=artifacts_repo,
                tables_repo=tables_repo,
                object_store=object_store,
            )
        else:
            materialize_error = None
        dataframe, load_error = _load_processed_dataframe(
            artifact=artifact,
            settings=settings,
            object_store=object_store,
        )
        load_error = materialize_error or load_error
        columns = _columns_from_schema(schema)
        if not columns:
            columns = _columns_from_data_doc(doc_file)
        if dataframe is not None and not columns:
            columns = _columns_from_dataframe(dataframe)
        tables[table_name] = ToolTable(
            name=table_name,
            display_name=workspace_table.display_name or file.friendly_name or file.filename,
            row_count=workspace_table.row_count or file.row_count,
            columns=columns,
            dataframe=dataframe,
            load_error=load_error,
        )
    return tables


def _processed_artifact_for_table(
    table: WorkspaceTable,
    file: File,
    artifacts_repo: AssetArtifactRepository,
) -> AssetArtifact | None:
    if table.processed_artifact_id:
        artifact = artifacts_repo.get(table.processed_artifact_id)
        if artifact is not None:
            return artifact
    return artifacts_repo.latest_for_file(file.id, "processed_parquet")


def _materialize_processed_artifact(
    *,
    file: File,
    table: WorkspaceTable,
    schema: FileSchema | None,
    doc_file: DataDocFile | None,
    settings: Settings,
    files_repo: FileRepository,
    schemas_repo: SchemaRepository,
    artifacts_repo: AssetArtifactRepository,
    tables_repo: WorkspaceTableRepository,
    object_store: ObjectStore,
) -> tuple[AssetArtifact | None, str | None]:
    raw_artifact = artifacts_repo.latest_for_file(file.id, "raw_parquet")
    if raw_artifact is None:
        return None, "raw R2 artifact is not registered"
    spec = _file_spec_for_materialization(file=file, table=table, schema=schema, doc_file=doc_file)
    if spec is None:
        spec = _file_spec_from_raw_header(
            file=file,
            table=table,
            raw_artifact=raw_artifact,
            settings=settings,
            object_store=object_store,
        )
    if spec is None:
        return None, "processed R2 artifact is not registered and raw header could not be inferred"
    try:
        reingest_file(
            file_id=file.id,
            spec=spec,
            user_id=raw_artifact.user_id,
            settings=settings,
            files_repo=files_repo,
            schemas_repo=schemas_repo,
            artifacts_repo=artifacts_repo,
            tables_repo=tables_repo,
            object_store=object_store,
        )
        files_repo.conn.commit()
    except ReingestError as exc:
        files_repo.conn.rollback()
        return None, f"processed R2 artifact could not be rebuilt: {exc}"
    except Exception as exc:
        files_repo.conn.rollback()
        return None, f"processed R2 artifact could not be rebuilt: {exc}"
    return artifacts_repo.latest_for_file(file.id, "processed_parquet"), None


def _file_spec_for_materialization(
    *,
    file: File,
    table: WorkspaceTable,
    schema: FileSchema | None,
    doc_file: DataDocFile | None,
) -> FileSpec | None:
    columns: list[ColumnSpec] = []
    if schema is not None and schema.columns:
        columns = [
            ColumnSpec(
                column_id=column.column_id or column.name,
                name=column.name,
                dtype=_simple_dtype(column.inferred_kind or column.dtype),
                description=column.description or "",
            )
            for column in schema.columns
        ]
    elif doc_file is not None and doc_file.columns:
        columns = [
            ColumnSpec(
                column_id=column.name,
                name=column.name,
                dtype=_simple_dtype(column.dtype),
                description=column.meaning or "",
            )
            for column in doc_file.columns
        ]
    if not columns:
        return None
    return FileSpec(
        file_id=file.id,
        header_row=file.header_row or 0,
        friendly_name=(
            table.display_name
            or file.friendly_name
            or (doc_file.name if doc_file is not None else "")
            or file.filename
        ),
        description=file.description or (doc_file.description if doc_file is not None else ""),
        columns=columns,
    )


def _file_spec_from_raw_header(
    *,
    file: File,
    table: WorkspaceTable,
    raw_artifact: AssetArtifact,
    settings: Settings,
    object_store: ObjectStore,
) -> FileSpec | None:
    try:
        destination = settings.object_cache_path(raw_artifact.object_key)
        object_store.get_to_path(raw_artifact.object_key, destination)
        raw = pd.read_parquet(destination)
    except Exception:
        return None
    if raw.empty:
        return None
    header_row = file.header_row or 0
    if header_row >= len(raw.index):
        return None
    header_values = raw.iloc[header_row].tolist()
    if not header_values:
        return None
    names = _dedupe_column_names(
        [
            str(value).strip() if not _is_blank(value) else f"column_{index + 1}"
            for index, value in enumerate(header_values)
        ]
    )
    sample = raw.iloc[header_row + 1 : header_row + 1001]
    columns = [
        ColumnSpec(
            column_id=f"{slugify_table_name(name)}_{index + 1}",
            name=name,
            dtype=_infer_simple_dtype(name, sample.iloc[:, index].tolist()),
            description="",
        )
        for index, name in enumerate(names)
    ]
    return FileSpec(
        file_id=file.id,
        header_row=header_row,
        friendly_name=table.display_name or file.friendly_name or file.filename,
        description=file.description or "",
        columns=columns,
    )


def _load_processed_dataframe(
    *,
    artifact: AssetArtifact | None,
    settings: Settings,
    object_store: ObjectStore,
) -> tuple[pd.DataFrame | None, str | None]:
    if artifact is None:
        return None, "processed R2 artifact is not registered"
    if artifact.storage_backend != object_store.backend:
        return None, f"processed artifact is stored in {artifact.storage_backend}, not {object_store.backend}"
    try:
        destination = settings.object_cache_path(artifact.object_key)
        object_store.get_to_path(artifact.object_key, destination)
        return pd.read_parquet(destination), None
    except Exception as exc:
        return None, str(exc)


def _columns_from_schema(schema: FileSchema | None) -> list[dict[str, Any]]:
    if schema is None:
        return []
    return [
        {
            "name": column.name,
            "type": column.dtype,
            "nullable": None,
            "kind": column.inferred_kind,
            "description": column.description,
        }
        for column in schema.columns
    ]


def _columns_from_data_doc(doc_file: DataDocFile | None) -> list[dict[str, Any]]:
    if doc_file is None:
        return []
    return [
        {
            "name": column.name,
            "type": _simple_dtype(column.dtype),
            "nullable": None,
            "kind": _simple_dtype(column.dtype),
            "description": column.meaning,
        }
        for column in doc_file.columns
    ]


def _simple_dtype(dtype: str) -> str:
    normalized = dtype.strip().lower()
    if normalized in {"int", "integer", "int64", "long"}:
        return "int"
    if normalized in {"float", "float64", "double", "decimal", "number", "numeric"}:
        return "float"
    if normalized in {"bool", "boolean"}:
        return "bool"
    if normalized in {"date"}:
        return "date"
    if normalized in {"datetime", "timestamp"}:
        return "datetime"
    if normalized in {"category", "categorical"}:
        return "category"
    return "string"


def _dedupe_column_names(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    names: list[str] = []
    for header in headers:
        base = header.strip() or "column"
        if base not in seen:
            seen[base] = 0
            names.append(base)
            continue
        seen[base] += 1
        names.append(f"{base}_{seen[base]}")
    return names


def _infer_simple_dtype(name: str, values: list[Any]) -> str:
    lower_name = name.lower()
    if "date" in lower_name:
        return "date"
    cleaned = [str(value).strip() for value in values if not _is_blank(value)]
    if not cleaned:
        return "string"
    lowered = {value.lower() for value in cleaned}
    if lowered <= {"true", "false", "yes", "no", "y", "n", "1", "0", "t", "f"}:
        return "bool"
    numeric = pd.to_numeric(pd.Series(cleaned), errors="coerce")
    if float(numeric.notna().mean()) >= 0.9:
        non_null = numeric.dropna()
        if bool(((non_null % 1) == 0).all()):
            return "int"
        return "float"
    parsed_dates = pd.to_datetime(pd.Series(cleaned), errors="coerce")
    if float(parsed_dates.notna().mean()) >= 0.9:
        return "datetime"
    if len(lowered) <= max(20, len(cleaned) // 20):
        return "category"
    return "string"


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        return False
    return isinstance(value, str) and not value.strip()


def _columns_from_dataframe(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "name": str(name),
            "type": str(dtype),
            "nullable": bool(frame[name].isna().any()),
        }
        for name, dtype in frame.dtypes.items()
    ]


def _unique_table_name(base: str, seen: set[str]) -> str:
    name = base
    index = 2
    while name in seen:
        name = f"{base}_{index}"
        index += 1
    seen.add(name)
    return name


def _persist_loop_message(
    chat_repo: ChatRepository,
    turn_id: str,
    message: dict[str, Any],
) -> ChatMessage | None:
    role = message.get("role")
    if role == "assistant":
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls")
        saved_message: ChatMessage | None = None
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function", {})
                args_raw = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    args = {"_raw": args_raw}
                saved_message = chat_repo.append_message(
                    turn_id=turn_id,
                    role="assistant",
                    content=content,
                    tool_name=fn.get("name"),
                    tool_args=args,
                )
        else:
            saved_message = chat_repo.append_message(turn_id=turn_id, role="assistant", content=content)
        return saved_message
    if role == "tool":
        raw = message.get("content") or "{}"
        try:
            result = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            result = {"_raw": raw}
        return chat_repo.append_message(
            turn_id=turn_id,
            role="tool",
            content=raw if isinstance(raw, str) else json.dumps(raw),
            tool_result=result if isinstance(result, dict) else {"value": result},
        )
    return None


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
    message_id: str | None = None,
    widgets: list[Widget],
) -> None:
    for idx, widget in enumerate(widgets):
        chat_artifacts_repo.create(
            session_id=session_id,
            turn_id=turn_id,
            message_id=message_id,
            artifact_type=f"widget:{widget.kind}",
            title=widget.title,
            inline_payload={"widget": widget.model_dump()},
            order_index=idx,
        )
