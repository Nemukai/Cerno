from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from cerno.api.deps import ConnDep, GrantedUserDep, LLMDep, OrgDep, SettingsDep
from cerno.config import Settings
from cerno.db import DbConnection
from cerno.llm import LLMTokenBudget
from cerno.models import ChatArtifact, ChatMessage, ChatTurn, Widget
from cerno.repositories import (
    AnalyticsRepository,
    AssetArtifactRepository,
    ChatArtifactRepository,
    ChatRepository,
    DataDocRepository,
    FileRepository,
    LLMUsageRepository,
    SchemaRepository,
    SessionRepository,
    UsageLimitRepository,
)
from cerno.services.chat import run_chat_turn, stream_chat_turn

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)
USER_SAFE_CHAT_ERROR = "Something went wrong. Please try again."


def _require_session_owned(
    conn: DbConnection,
    session_id: str,
    user_id: str,
    organization_id: str | None = None,
) -> None:
    if SessionRepository(conn).get(
        session_id,
        user_id=user_id,
        organization_id=organization_id,
    ) is None:
        raise HTTPException(status_code=404, detail="session not found")


def _require_turn_owned(
    conn: DbConnection,
    turn_id: str,
    user_id: str,
    organization_id: str | None = None,
) -> ChatTurn:
    turn = ChatRepository(conn).get_turn(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="turn not found")
    if SessionRepository(conn).get(
        turn.session_id,
        user_id=user_id,
        organization_id=organization_id,
    ) is None:
        raise HTTPException(status_code=404, detail="turn not found")
    return turn


def _llm_budget(
    conn: DbConnection,
    settings: Settings,
    organization_id: str,
    user_id: str,
) -> LLMTokenBudget:
    limits = UsageLimitRepository(conn).effective_for(
        organization_id=organization_id,
        user_id=user_id,
    )
    daily_cap = (
        UsageLimitRepository.hard_cap(limits.daily_token_limit, limits)
        if limits.daily_token_limit is not None
        else settings.daily_token_cap
    )
    return LLMTokenBudget(
        user_daily_token_cap=daily_cap,
        user_monthly_token_cap=UsageLimitRepository.hard_cap(
            limits.user_monthly_token_limit,
            limits,
        ),
        organization_monthly_token_cap=UsageLimitRepository.hard_cap(
            limits.organization_monthly_token_limit,
            limits,
        ),
    )


class ChatRequest(BaseModel):
    message: str
    turn_id: str | None = None


class ChatResponse(BaseModel):
    turn_id: str
    assistant_message: str
    spawned_page_id: str | None
    widgets: list[Widget]


class ChatFeedTurn(BaseModel):
    turn: ChatTurn
    messages: list[ChatMessage]
    artifacts: list[ChatArtifact]


class ChatUpdateRequest(BaseModel):
    title: str | None = None
    metadata: dict[str, Any] | None = None


@router.post("/sessions/{session_id}/chat", response_model=ChatResponse)
async def post_chat(
    session_id: str,
    body: ChatRequest,
    conn: ConnDep,
    settings: SettingsDep,
    llm_client: LLMDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> ChatResponse:
    _require_session_owned(conn, session_id, user.id, organization.id)
    if body.turn_id is not None:
        turn = _require_turn_owned(conn, body.turn_id, user.id, organization.id)
        if turn.session_id != session_id:
            raise HTTPException(status_code=404, detail="turn not found")
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    started_at = time.perf_counter()
    try:
        result = await run_chat_turn(
            session_id=session_id,
            user_message=message,
            turn_id=body.turn_id,
            settings=settings,
            llm_client=llm_client.with_usage(
                LLMUsageRepository(conn, auto_commit=True),
                user.id,
                organization_id=organization.id,
                token_budget=_llm_budget(conn, settings, organization.id, user.id),
                metadata={"session_id": session_id},
            ),
            files_repo=FileRepository(conn),
            schemas_repo=SchemaRepository(conn),
            chat_repo=ChatRepository(conn),
            chat_artifacts_repo=ChatArtifactRepository(conn),
            artifacts_repo=AssetArtifactRepository(conn),
            data_docs_repo=DataDocRepository(conn),
        )
    except Exception as exc:
        AnalyticsRepository(conn, auto_commit=True).record_product_event(
            event_name="chat_turn_failed",
            organization_id=organization.id,
            user_id=user.id,
            session_id=session_id,
            metric_value=float(round((time.perf_counter() - started_at) * 1000)),
            metadata={"turn_id": body.turn_id, "error": exc.__class__.__name__},
        )
        logger.exception(
            "event=chat.request.failed session_id=%s user_id=%s turn_id=%s",
            session_id,
            user.id,
            body.turn_id,
        )
        raise HTTPException(status_code=500, detail=USER_SAFE_CHAT_ERROR) from exc
    AnalyticsRepository(conn, auto_commit=True).record_product_event(
        event_name="chat_turn_completed",
        organization_id=organization.id,
        user_id=user.id,
        session_id=session_id,
        metric_value=float(round((time.perf_counter() - started_at) * 1000)),
        metadata={"turn_id": result.turn.id, "tool_calls": result.tool_calls},
    )
    return ChatResponse(
        turn_id=result.turn.id,
        assistant_message=result.assistant_message,
        spawned_page_id=result.spawned_page_id,
        widgets=result.widgets,
    )


@router.post("/sessions/{session_id}/chat/stream")
async def stream_chat(
    session_id: str,
    body: ChatRequest,
    conn: ConnDep,
    settings: SettingsDep,
    llm_client: LLMDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> StreamingResponse:
    _require_session_owned(conn, session_id, user.id, organization.id)
    if body.turn_id is not None:
        turn = _require_turn_owned(conn, body.turn_id, user.id, organization.id)
        if turn.session_id != session_id:
            raise HTTPException(status_code=404, detail="turn not found")
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    async def events() -> AsyncIterator[str]:
        started_at = time.perf_counter()
        emitted_turn_id = body.turn_id or ""
        try:
            async for event in stream_chat_turn(
                session_id=session_id,
                user_message=message,
                turn_id=body.turn_id,
                settings=settings,
                llm_client=llm_client.with_usage(
                    LLMUsageRepository(conn, auto_commit=True),
                    user.id,
                    organization_id=organization.id,
                    token_budget=_llm_budget(conn, settings, organization.id, user.id),
                    metadata={"session_id": session_id},
                ),
                files_repo=FileRepository(conn),
                schemas_repo=SchemaRepository(conn),
                chat_repo=ChatRepository(conn),
                chat_artifacts_repo=ChatArtifactRepository(conn),
                artifacts_repo=AssetArtifactRepository(conn),
                data_docs_repo=DataDocRepository(conn),
            ):
                event_name = str(event.get("type") or "message")
                event_turn_id = str(event.get("turn_id") or "")
                if event_turn_id:
                    emitted_turn_id = event_turn_id
                if event_name == "done":
                    AnalyticsRepository(conn, auto_commit=True).record_product_event(
                        event_name="chat_turn_completed",
                        organization_id=organization.id,
                        user_id=user.id,
                        session_id=session_id,
                        metric_value=float(round((time.perf_counter() - started_at) * 1000)),
                        metadata={"turn_id": emitted_turn_id, "stream": True},
                    )
                elif event_name == "error":
                    AnalyticsRepository(conn, auto_commit=True).record_product_event(
                        event_name="chat_turn_failed",
                        organization_id=organization.id,
                        user_id=user.id,
                        session_id=session_id,
                        metric_value=float(round((time.perf_counter() - started_at) * 1000)),
                        metadata={"turn_id": emitted_turn_id, "stream": True},
                    )
                yield f"event: {event_name}\ndata: {json.dumps(event, default=str)}\n\n"
        except Exception:
            AnalyticsRepository(conn, auto_commit=True).record_product_event(
                event_name="chat_turn_failed",
                organization_id=organization.id,
                user_id=user.id,
                session_id=session_id,
                metric_value=float(round((time.perf_counter() - started_at) * 1000)),
                metadata={"turn_id": emitted_turn_id, "stream": True},
            )
            logger.exception(
                "event=chat.stream.unhandled session_id=%s user_id=%s turn_id=%s",
                session_id,
                user.id,
                body.turn_id,
            )
            event = {
                "type": "error",
                "turn_id": body.turn_id or "",
                "message": USER_SAFE_CHAT_ERROR,
            }
            yield f"event: error\ndata: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{session_id}/turns", response_model=list[ChatTurn])
def get_session_turns(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> list[ChatTurn]:
    _require_session_owned(conn, session_id, user.id, organization.id)
    return ChatRepository(conn).list_turns(session_id)


@router.get("/sessions/{session_id}/chat-feed", response_model=list[ChatFeedTurn])
def get_session_chat_feed(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> list[ChatFeedTurn]:
    _require_session_owned(conn, session_id, user.id, organization.id)
    chat_repo = ChatRepository(conn)
    artifact_repo = ChatArtifactRepository(conn)
    messages_by_turn = chat_repo.list_messages_for_session(session_id)
    artifacts_by_turn = artifact_repo.list_for_session(session_id)
    return [
        ChatFeedTurn(
            turn=turn,
            messages=messages_by_turn.get(turn.id, []),
            artifacts=artifacts_by_turn.get(turn.id, []),
        )
        for turn in chat_repo.list_turns(session_id)
    ]


@router.get("/turns/{turn_id}/messages", response_model=list[ChatMessage])
def get_turn_messages(
    turn_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> list[ChatMessage]:
    _require_turn_owned(conn, turn_id, user.id, organization.id)
    return ChatRepository(conn).list_messages(turn_id)


@router.patch("/turns/{turn_id}", response_model=ChatTurn)
def update_turn(
    turn_id: str,
    body: ChatUpdateRequest,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> ChatTurn:
    _require_turn_owned(conn, turn_id, user.id, organization.id)
    title = body.title.strip() if body.title is not None else None
    if body.title is not None and not title:
        raise HTTPException(status_code=400, detail="title cannot be empty")
    updated = ChatRepository(conn).update_turn(
        turn_id=turn_id,
        title=title,
        metadata=body.metadata,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="turn not found")
    conn.commit()
    return updated


@router.delete("/turns/{turn_id}", status_code=204)
def delete_turn(
    turn_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> None:
    _require_turn_owned(conn, turn_id, user.id, organization.id)
    ChatRepository(conn).delete_turn(turn_id)
    conn.commit()
