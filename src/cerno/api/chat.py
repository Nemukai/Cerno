from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cerno.api.deps import ConnDep, LLMDep, SettingsDep
from cerno.models import ChatMessage, ChatTurn, Widget
from cerno.repositories import (
    ChatRepository,
    DashboardRepository,
    FileRepository,
    NotebookRepository,
    SchemaRepository,
)
from cerno.services.chat import run_chat_turn

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    turn_id: str
    assistant_message: str
    spawned_page_id: str | None
    widgets: list[Widget]


@router.post("/sessions/{session_id}/chat", response_model=ChatResponse)
async def post_chat(
    session_id: str,
    body: ChatRequest,
    conn: ConnDep,
    settings: SettingsDep,
    llm_client: LLMDep,
) -> ChatResponse:
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    try:
        result = await run_chat_turn(
            session_id=session_id,
            user_message=message,
            settings=settings,
            llm_client=llm_client,
            files_repo=FileRepository(conn),
            schemas_repo=SchemaRepository(conn),
            dashboards_repo=DashboardRepository(conn),
            notebook_repo=NotebookRepository(conn),
            chat_repo=ChatRepository(conn),
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
def get_session_turns(session_id: str, conn: ConnDep) -> list[ChatTurn]:
    return ChatRepository(conn).list_turns(session_id)


@router.get("/turns/{turn_id}/messages", response_model=list[ChatMessage])
def get_turn_messages(turn_id: str, conn: ConnDep) -> list[ChatMessage]:
    return ChatRepository(conn).list_messages(turn_id)
