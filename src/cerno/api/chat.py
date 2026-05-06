from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cerno.api.deps import ConnDep, GrantedUserDep, LLMDep, SettingsDep
from cerno.models import ChatArtifact, ChatMessage, ChatTurn, Widget
from cerno.repositories import (
    AssetArtifactRepository,
    ChatArtifactRepository,
    ChatRepository,
    DataDocRepository,
    FileRepository,
    LLMUsageRepository,
    SchemaRepository,
    SessionRepository,
)
from cerno.services.chat import run_chat_turn

router = APIRouter(tags=["chat"])


def _require_session_owned(conn: sqlite3.Connection, session_id: str, user_id: str) -> None:
    if SessionRepository(conn).get(session_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="session not found")


def _require_turn_owned(conn: sqlite3.Connection, turn_id: str, user_id: str) -> ChatTurn:
    turn = ChatRepository(conn).get_turn(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="turn not found")
    if SessionRepository(conn).get(turn.session_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="turn not found")
    return turn


class ChatRequest(BaseModel):
    message: str


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
) -> ChatResponse:
    _require_session_owned(conn, session_id, user.id)
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    try:
        result = await run_chat_turn(
            session_id=session_id,
            user_message=message,
            settings=settings,
            llm_client=llm_client.with_usage(LLMUsageRepository(conn, auto_commit=True), user.id),
            files_repo=FileRepository(conn),
            schemas_repo=SchemaRepository(conn),
            chat_repo=ChatRepository(conn),
            chat_artifacts_repo=ChatArtifactRepository(conn),
            artifacts_repo=AssetArtifactRepository(conn),
            data_docs_repo=DataDocRepository(conn),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ChatResponse(
        turn_id=result.turn.id,
        assistant_message=result.assistant_message,
        spawned_page_id=result.spawned_page_id,
        widgets=result.widgets,
    )


@router.get("/sessions/{session_id}/turns", response_model=list[ChatTurn])
def get_session_turns(session_id: str, conn: ConnDep, user: GrantedUserDep) -> list[ChatTurn]:
    _require_session_owned(conn, session_id, user.id)
    return ChatRepository(conn).list_turns(session_id)


@router.get("/sessions/{session_id}/chat-feed", response_model=list[ChatFeedTurn])
def get_session_chat_feed(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
) -> list[ChatFeedTurn]:
    _require_session_owned(conn, session_id, user.id)
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
def get_turn_messages(turn_id: str, conn: ConnDep, user: GrantedUserDep) -> list[ChatMessage]:
    _require_turn_owned(conn, turn_id, user.id)
    return ChatRepository(conn).list_messages(turn_id)


@router.patch("/turns/{turn_id}", response_model=ChatTurn)
def update_turn(
    turn_id: str,
    body: ChatUpdateRequest,
    conn: ConnDep,
    user: GrantedUserDep,
) -> ChatTurn:
    _require_turn_owned(conn, turn_id, user.id)
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
def delete_turn(turn_id: str, conn: ConnDep, user: GrantedUserDep) -> None:
    _require_turn_owned(conn, turn_id, user.id)
    ChatRepository(conn).delete_turn(turn_id)
    conn.commit()
