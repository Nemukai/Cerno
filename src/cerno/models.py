from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SessionStatus = Literal["new", "ingesting", "analyzing", "ready", "archived"]
InferredKind = Literal["string", "int", "float", "date", "datetime", "bool", "category"]
LinkDirection = Literal["many_to_one", "one_to_one", "many_to_many"]
WidgetKind = Literal["kpi", "bar", "line", "pie", "table", "markdown"]
LinkSource = Literal["discovered", "user_added"]
LinkAction = Literal["confirm", "reject", "edit"]
ReviewStatus = Literal["dismissed", "escalated"]
PageKind = Literal["overview", "file", "question"]
CellKind = Literal["python", "sql", "widget"]
RunStatus = Literal["ok", "error", "stale"]
TurnState = Literal["pending", "tool_running", "rendering", "complete", "failed"]
MessageRole = Literal["user", "assistant", "tool", "system"]


class Session(BaseModel):
    id: str
    name: str
    status: SessionStatus = "new"
    created_at: datetime


class File(BaseModel):
    id: str
    session_id: str
    filename: str
    parquet_path: str
    row_count: int
    schema_version: int = 1
    created_at: datetime


class SchemaColumn(BaseModel):
    file_id: str
    schema_version: int
    name: str
    dtype: str
    inferred_kind: InferredKind
    confidence: float
    position: int


class FileSchema(BaseModel):
    file_id: str
    schema_version: int
    columns: list[SchemaColumn]


class Link(BaseModel):
    id: str
    session_id: str
    file_a: str
    col_a: str
    file_b: str
    col_b: str
    overlap: float
    direction: LinkDirection
    score: float
    summary: str | None = None
    source: LinkSource = "discovered"
    created_at: datetime


class LinkReview(BaseModel):
    id: int | None = None
    link_id: str
    action: LinkAction
    notes: str | None = None
    created_at: datetime


class Anomaly(BaseModel):
    id: str
    session_id: str
    file_id: str
    row_id: int
    detector: str
    reason_plain: str
    reason_technical: str
    score_normalized: float = Field(ge=0, le=100)
    score_raw: float
    source_code: str
    review_status: ReviewStatus | None = None
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    notes: str | None = None
    created_at: datetime


class Dashboard(BaseModel):
    id: str
    session_id: str
    created_at: datetime


class DashboardPage(BaseModel):
    id: str
    dashboard_id: str
    title: str
    kind: PageKind
    source_chat_turn_id: str | None = None
    pinned: bool = False
    position: int
    created_at: datetime


class NotebookCell(BaseModel):
    id: str
    page_id: str
    order_index: int
    kind: CellKind
    code: str
    output: dict[str, Any] | None = None
    bound_file_ids: list[str] = Field(default_factory=list)
    bound_schema_versions: dict[str, int] = Field(default_factory=dict)
    threshold_snapshot: dict[str, Any] = Field(default_factory=dict)
    last_run_at: datetime | None = None
    last_run_status: RunStatus | None = None
    created_at: datetime


class ChatTurn(BaseModel):
    id: str
    session_id: str
    user_message: str
    assistant_message: str | None = None
    spawned_page_id: str | None = None
    state: TurnState = "pending"
    created_at: datetime


class ChatMessage(BaseModel):
    id: str
    turn_id: str
    role: MessageRole
    content: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    created_at: datetime


class AuditEvent(BaseModel):
    id: int | None = None
    session_id: str | None
    kind: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class Widget(BaseModel):
    kind: WidgetKind
    title: str
    data: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)
    caption: str | None = None
