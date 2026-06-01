from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SessionStatus = Literal["new", "ingesting", "analyzing", "ready", "archived"]
DiscoveryStatus = Literal["empty", "discovering", "pending_review", "approved", "failed"]
ProcessingEventKind = Literal[
    "queued",
    "uploading",
    "ingesting_file",
    "reading_document",
    "ocr_page",
    "quality_review",
    "started",
    "loading_artifacts",
    "reading_files",
    "python_analysis",
    "calling_llm",
    "parsing_response",
    "saving_schema",
    "resolving_links",
    "applying_schema",
    "reingesting_file",
    "done",
    "error",
]
ProcessingJobKind = Literal["ingest_upload", "ingest_document", "discovery"]
ProcessingJobStatus = Literal["queued", "running", "succeeded", "failed"]
UploadIntentStatus = Literal["pending", "uploaded", "processing", "processed", "failed"]
DocumentStatus = Literal["processing", "processed", "failed"]
DocumentPageSource = Literal["text_layer", "ocr", "ocr_retry"]
InferredKind = Literal["string", "int", "float", "date", "datetime", "bool", "category"]
LinkDirection = Literal["many_to_one", "one_to_one", "many_to_many"]
WidgetKind = Literal[
    "kpi",
    "bar",
    "horizontal_bar",
    "grouped_bar",
    "stacked_bar",
    "line",
    "area",
    "stacked_area",
    "pie",
    "histogram",
    "scatter",
    "heatmap",
    "boxplot",
    "waterfall",
    "sankey",
    "timeline",
    "table",
    "markdown",
]
LinkSource = Literal["discovered", "user_added"]
LinkAction = Literal["confirm", "reject", "edit"]
ReviewStatus = Literal["dismissed", "escalated"]
PageKind = Literal["overview", "file", "question"]
CellKind = Literal["python", "sql", "widget"]
RunStatus = Literal["ok", "error", "stale"]
TurnState = Literal["pending", "tool_running", "rendering", "complete", "failed"]
MessageRole = Literal["user", "assistant", "tool", "system"]


AccessStatus = Literal["pending", "granted", "revoked"]
OrganizationStatus = Literal["active", "suspended", "archived"]
OrganizationRole = Literal["admin", "member", "viewer"]
MembershipStatus = Literal["active", "revoked"]
InviteStatus = Literal["pending", "accepted", "revoked", "expired"]
ContractStatus = Literal["trial", "active", "paused", "suspended", "archived"]
SiteRole = Literal["user", "site_owner"]
NumberSystem = Literal["international", "indian"]


class Organization(BaseModel):
    id: str
    name: str
    slug: str
    status: OrganizationStatus = "active"
    created_at: datetime
    updated_at: datetime


class OrganizationMember(BaseModel):
    organization_id: str
    user_id: str
    role: OrganizationRole = "member"
    status: MembershipStatus = "active"
    created_at: datetime
    updated_at: datetime


class OrganizationInvite(BaseModel):
    id: str
    organization_id: str
    email: str
    role: OrganizationRole = "member"
    invited_by_user_id: str | None = None
    status: InviteStatus = "pending"
    token: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    accepted_at: datetime | None = None


class OrganizationEntitlements(BaseModel):
    organization_id: str
    plan_name: str = "manual"
    contract_status: ContractStatus = "trial"
    seat_limit: int = 1
    daily_token_limit: int | None = None
    monthly_token_limit: int = 6_000_000
    storage_quota_bytes: int = 5 * 1024 * 1024 * 1024
    monthly_upload_bytes: int | None = None
    max_file_size_bytes: int | None = None
    max_workspaces: int | None = None
    max_concurrent_jobs: int | None = None
    soft_limit_percent: int = 100
    hard_limit_percent: int = 120
    feature_flags: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class UserOrganizationLimits(BaseModel):
    organization_id: str
    user_id: str
    daily_token_limit: int | None = None
    monthly_token_limit: int | None = None
    storage_quota_bytes: int | None = None
    monthly_upload_bytes: int | None = None
    max_file_size_bytes: int | None = None
    max_sessions: int | None = None
    max_concurrent_jobs: int | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class EffectiveUsageLimits(BaseModel):
    organization_id: str
    user_id: str
    daily_token_limit: int | None = None
    user_monthly_token_limit: int | None = None
    organization_monthly_token_limit: int | None = None
    user_storage_quota_bytes: int | None = None
    organization_storage_quota_bytes: int | None = None
    user_monthly_upload_bytes: int | None = None
    organization_monthly_upload_bytes: int | None = None
    user_max_file_size_bytes: int | None = None
    organization_max_file_size_bytes: int | None = None
    user_max_sessions: int | None = None
    organization_max_sessions: int | None = None
    user_max_concurrent_jobs: int | None = None
    organization_max_concurrent_jobs: int | None = None
    soft_limit_percent: int = 100
    hard_limit_percent: int = 120


class User(BaseModel):
    id: str
    google_sub: str
    email: str
    name: str | None = None
    picture: str | None = None
    access_status: AccessStatus = "pending"
    access_granted_at: datetime | None = None
    access_code_used: str | None = None
    number_system: NumberSystem = "international"
    created_at: datetime
    last_seen_at: datetime


class LLMUsage(BaseModel):
    user_id: str
    day: str
    tokens_used: int


class BetaCode(BaseModel):
    code: str
    note: str | None = None
    max_uses: int = 1
    uses_count: int = 0
    created_at: datetime
    expires_at: datetime | None = None


class ApprovedEmail(BaseModel):
    email: str
    note: str | None = None
    created_at: datetime


class Session(BaseModel):
    id: str
    organization_id: str | None = None
    user_id: str | None = None
    created_by_user_id: str | None = None
    name: str
    status: SessionStatus = "new"
    discovery_status: DiscoveryStatus = "empty"
    overview: str | None = None
    created_at: datetime


class File(BaseModel):
    id: str
    session_id: str
    filename: str
    parquet_path: str
    raw_parquet_path: str | None = None
    original_size_bytes: int | None = None
    row_count: int
    schema_version: int = 1
    header_row: int | None = None
    friendly_name: str | None = None
    description: str | None = None
    content_hash: str | None = None
    created_at: datetime


class Document(BaseModel):
    id: str
    session_id: str
    user_id: str
    organization_id: str
    filename: str
    content_hash: str
    page_count: int
    status: DocumentStatus
    created_at: datetime


class DocumentPage(BaseModel):
    id: str
    document_id: str
    page_number: int
    source: DocumentPageSource
    markdown: str
    char_count: int
    quality_score: float
    low_confidence: bool
    quality_reasons: list[str] = Field(default_factory=list)
    image_object_key: str | None = None


class DocumentChunk(BaseModel):
    id: str
    document_id: str
    session_id: str
    organization_id: str
    chunk_index: int
    text: str
    heading_path: list[str] = Field(default_factory=list)
    section_no: str | None = None
    clause_no: str | None = None
    page_number: int
    start_char: int
    end_char: int
    low_confidence: bool = False
    created_at: datetime


class DocumentChunkSearchRow(BaseModel):
    chunk: DocumentChunk
    document_filename: str
    rank: float = 0.0


class SourceAsset(BaseModel):
    id: str
    organization_id: str | None = None
    user_id: str
    sha256: str
    original_filename: str
    mime_type: str | None = None
    size_bytes: int
    storage_backend: str
    object_key: str
    created_at: datetime


class WorkspaceAsset(BaseModel):
    id: str
    session_id: str
    source_asset_id: str
    display_name: str
    created_at: datetime


class AssetArtifact(BaseModel):
    id: str
    organization_id: str | None = None
    user_id: str
    session_id: str | None = None
    source_asset_id: str | None = None
    file_id: str | None = None
    artifact_type: str
    storage_backend: str
    object_key: str
    content_hash: str | None = None
    size_bytes: int = 0
    mime_type: str | None = None
    created_at: datetime


class WorkspaceTable(BaseModel):
    id: str
    session_id: str
    workspace_asset_id: str | None = None
    legacy_file_id: str | None = None
    sheet_name: str | None = None
    table_index: int = 0
    display_name: str
    row_count: int = 0
    current_schema_version: int = 1
    processed_artifact_id: str | None = None
    created_at: datetime


class UploadIntent(BaseModel):
    id: str
    organization_id: str | None = None
    user_id: str
    session_id: str
    original_filename: str
    mime_type: str | None = None
    expected_size_bytes: int
    observed_size_bytes: int | None = None
    storage_backend: str
    object_key: str
    status: UploadIntentStatus
    source_asset_id: str | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ProcessingJob(BaseModel):
    id: str
    organization_id: str | None = None
    user_id: str
    session_id: str
    kind: ProcessingJobKind
    status: ProcessingJobStatus
    attempts: int = 0
    locked_by: str | None = None
    locked_until: datetime | None = None
    heartbeat_at: datetime | None = None
    checkpoint_json: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    idempotency_key: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SchemaColumn(BaseModel):
    file_id: str
    schema_version: int
    name: str
    dtype: str
    inferred_kind: InferredKind
    confidence: float
    position: int
    column_id: str | None = None
    description: str | None = None
    confidence_reason: str | None = None


class FileSchema(BaseModel):
    file_id: str
    schema_version: int
    columns: list[SchemaColumn]


class DataDocColumn(BaseModel):
    name: str
    dtype: str
    meaning: str
    role: str | None = None
    confidence: float = 1.0
    low_confidence_reasons: list[str] = Field(default_factory=list)


class DataDocFile(BaseModel):
    file_id: str
    name: str
    description: str
    grain: str
    row_count: int
    columns: list[DataDocColumn] = Field(default_factory=list)
    key_columns: list[str] = Field(default_factory=list)
    date_columns: list[str] = Field(default_factory=list)
    measure_columns: list[str] = Field(default_factory=list)
    category_columns: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class DataDocRelationship(BaseModel):
    left_file_id: str
    left_column: str
    right_file_id: str
    right_column: str
    explanation: str
    confidence: float = 1.0
    low_confidence_reasons: list[str] = Field(default_factory=list)


class DataDocGlossaryItem(BaseModel):
    term: str
    meaning: str


class DataDoc(BaseModel):
    session_id: str
    overview: str
    files: list[DataDocFile] = Field(default_factory=list)
    relationships: list[DataDocRelationship] = Field(default_factory=list)
    glossary: list[DataDocGlossaryItem] = Field(default_factory=list)
    usage_notes: list[str] = Field(default_factory=list)
    starter_questions: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ProcessingEvent(BaseModel):
    id: int | None = None
    session_id: str
    kind: ProcessingEventKind
    message: str
    created_at: datetime
    job_id: str | None = None
    step_key: str | None = None
    level: str | None = None
    progress: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


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


class DashboardCell(BaseModel):
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
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    state: TurnState = "pending"
    created_at: datetime


class ChatMessage(BaseModel):
    id: str
    turn_id: str
    role: MessageRole
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    created_at: datetime


class ChatArtifact(BaseModel):
    id: str
    session_id: str
    turn_id: str | None = None
    message_id: str | None = None
    artifact_type: str
    title: str
    inline_payload: dict[str, Any] | None = None
    storage_backend: str | None = None
    object_key: str | None = None
    size_bytes: int = 0
    mime_type: str | None = None
    order_index: int = 0
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
