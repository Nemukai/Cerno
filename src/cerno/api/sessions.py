from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from cerno.api.deps import ConnDep, GrantedUserDep, LLMDep, OrgDep, SettingsDep
from cerno.config import Settings
from cerno.db import DbConnection, session_scope
from cerno.models import (
    ChatArtifact,
    ChatMessage,
    ChatTurn,
    DataDoc,
    DataDocColumn,
    DataDocFile,
    DataDocGlossaryItem,
    DataDocRelationship,
    Link,
    ProcessingEvent,
    ProcessingJob,
    ProcessingJobKind,
    Session,
)
from cerno.models import File as FileModel
from cerno.repositories import (
    AnalyticsRepository,
    AssetArtifactRepository,
    AuditRepository,
    ChatArtifactRepository,
    ChatRepository,
    DataDocRepository,
    DocumentRepository,
    FileRepository,
    LinkRepository,
    OrganizationRepository,
    ProcessingEventRepository,
    ProcessingJobRepository,
    SchemaRepository,
    SessionRepository,
    SourceAssetRepository,
    UploadIntentRepository,
    UsageLimitRepository,
    WorkspaceAssetRepository,
    WorkspaceTableRepository,
    new_id,
)
from cerno.services.discovery_validation import decode_confidence_reasons
from cerno.services.document_ingest import is_supported_document
from cerno.services.ingest import IngestedFile, IngestError, ingest_file
from cerno.services.reingest import (
    ApprovalPayload,
    ColumnSpec,
    FileSpec,
    LinkSpec,
    ReingestError,
    apply_approval,
    preview_rows,
)
from cerno.services.schema_corrections import (
    CorrectionOperation,
    SchemaCorrectionError,
    SchemaCorrectionPatch,
    apply_operations_to_payload,
    interpret_schema_correction,
    minor_operations,
    normalize_patch,
    selected_structural_operations,
)
from cerno.services.storage_gc import cleanup_unused_storage_for_user
from cerno.storage import StorageError, get_object_store, staging_upload_key

router = APIRouter(tags=["sessions"])
logger = logging.getLogger(__name__)


def _require_session(
    conn: DbConnection,
    session_id: str,
    user_id: str,
    organization_id: str | None = None,
) -> Session:
    if organization_id is not None:
        _require_private_workspace_role(conn, organization_id=organization_id, user_id=user_id)
    session = SessionRepository(conn).get(
        session_id,
        user_id=user_id,
        organization_id=organization_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _require_file_for_user(
    conn: DbConnection,
    file_id: str,
    user_id: str,
    organization_id: str | None = None,
) -> tuple[FileModel, Session]:
    if organization_id is not None:
        _require_private_workspace_role(conn, organization_id=organization_id, user_id=user_id)
    file = FileRepository(conn).get(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    session = SessionRepository(conn).get(
        file.session_id,
        user_id=user_id,
        organization_id=organization_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="file not found")
    return file, session


def _min_cap(*values: int | None) -> int | None:
    caps = [value for value in values if value is not None]
    return min(caps) if caps else None


def _require_private_workspace_role(
    conn: DbConnection,
    *,
    organization_id: str,
    user_id: str,
) -> None:
    membership = OrganizationRepository(conn).get_member(
        organization_id=organization_id,
        user_id=user_id,
    )
    if membership is None or membership.status != "active":
        raise HTTPException(status_code=403, detail="organization access denied")
    if membership.role not in ("admin", "member"):
        raise HTTPException(status_code=403, detail="organization member access required")


def _ensure_session_limit(conn: DbConnection, *, organization_id: str, user_id: str) -> None:
    limits = UsageLimitRepository(conn).effective_for(
        organization_id=organization_id,
        user_id=user_id,
    )
    sessions_repo = SessionRepository(conn)
    org_cap = UsageLimitRepository.hard_cap(limits.organization_max_sessions, limits)
    if org_cap is not None and sessions_repo.count_for_organization(organization_id) >= org_cap:
        raise HTTPException(status_code=429, detail="organization session limit exceeded")
    user_cap = UsageLimitRepository.hard_cap(limits.user_max_sessions, limits)
    if user_cap is not None and sessions_repo.count_for_user(user_id, organization_id) >= user_cap:
        raise HTTPException(status_code=429, detail="user session limit exceeded")


def _ensure_upload_allowed(
    conn: DbConnection,
    *,
    organization_id: str,
    user_id: str,
    size_bytes: int,
    filename: str,
    reserved_bytes: int = 0,
) -> None:
    limits = UsageLimitRepository(conn).effective_for(
        organization_id=organization_id,
        user_id=user_id,
    )
    file_cap = _min_cap(
        UsageLimitRepository.hard_cap(limits.organization_max_file_size_bytes, limits),
        UsageLimitRepository.hard_cap(limits.user_max_file_size_bytes, limits),
    )
    if file_cap is not None and size_bytes > file_cap:
        raise HTTPException(status_code=413, detail=f"{filename}: file size limit exceeded")

    source_assets_repo = SourceAssetRepository(conn)
    org_storage_cap = UsageLimitRepository.hard_cap(
        limits.organization_storage_quota_bytes,
        limits,
    )
    if (
        org_storage_cap is not None
        and source_assets_repo.total_size_for_organization(organization_id)
        + reserved_bytes
        + size_bytes
        > org_storage_cap
    ):
        raise HTTPException(status_code=413, detail="organization storage quota exceeded")

    user_storage_cap = UsageLimitRepository.hard_cap(limits.user_storage_quota_bytes, limits)
    if (
        user_storage_cap is not None
        and source_assets_repo.total_size_for_user(user_id) + reserved_bytes + size_bytes
        > user_storage_cap
    ):
        raise HTTPException(status_code=413, detail="user storage quota exceeded")

    analytics = AnalyticsRepository(conn)
    org_upload_cap = UsageLimitRepository.hard_cap(
        limits.organization_monthly_upload_bytes,
        limits,
    )
    if org_upload_cap is not None:
        used = analytics.monthly_usage_amount(
            event_type="upload_completed",
            resource_type="bytes",
            organization_id=organization_id,
        )
        if used + reserved_bytes + size_bytes > org_upload_cap:
            raise HTTPException(status_code=413, detail="organization monthly upload limit exceeded")

    user_upload_cap = UsageLimitRepository.hard_cap(limits.user_monthly_upload_bytes, limits)
    if user_upload_cap is not None:
        used = analytics.monthly_usage_amount(
            event_type="upload_completed",
            resource_type="bytes",
            organization_id=organization_id,
            user_id=user_id,
        )
        if used + reserved_bytes + size_bytes > user_upload_cap:
            raise HTTPException(status_code=413, detail="user monthly upload limit exceeded")


def _ensure_job_limit(conn: DbConnection, *, organization_id: str, user_id: str) -> None:
    limits = UsageLimitRepository(conn).effective_for(
        organization_id=organization_id,
        user_id=user_id,
    )
    jobs_repo = ProcessingJobRepository(conn)
    org_cap = UsageLimitRepository.hard_cap(limits.organization_max_concurrent_jobs, limits)
    if org_cap is not None and jobs_repo.active_count(organization_id=organization_id) >= org_cap:
        raise HTTPException(status_code=429, detail="organization concurrent job limit exceeded")
    user_cap = UsageLimitRepository.hard_cap(limits.user_max_concurrent_jobs, limits)
    if (
        user_cap is not None
        and jobs_repo.active_count(organization_id=organization_id, user_id=user_id) >= user_cap
    ):
        raise HTTPException(status_code=429, detail="user concurrent job limit exceeded")


def _record_upload_completed(
    conn: DbConnection,
    *,
    organization_id: str,
    user_id: str,
    session_id: str,
    size_bytes: int,
    count: int,
    source: str,
) -> None:
    analytics = AnalyticsRepository(conn)
    analytics.record_usage_event(
        event_type="upload_completed",
        resource_type="bytes",
        amount=size_bytes,
        organization_id=organization_id,
        user_id=user_id,
        session_id=session_id,
        metadata={"source": source, "count": count},
    )
    analytics.record_usage_event(
        event_type="upload_completed",
        resource_type="count",
        amount=count,
        organization_id=organization_id,
        user_id=user_id,
        session_id=session_id,
        metadata={"source": source, "bytes": size_bytes},
    )
    analytics.record_product_event(
        event_name="upload_completed",
        organization_id=organization_id,
        user_id=user_id,
        session_id=session_id,
        metric_value=float(size_bytes),
        metadata={"source": source, "count": count},
    )


class CreateSessionBody(BaseModel):
    name: str


@router.post("/sessions", response_model=Session)
def create_session(
    body: CreateSessionBody,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> Session:
    _require_private_workspace_role(conn, organization_id=organization.id, user_id=user.id)
    _ensure_session_limit(conn, organization_id=organization.id, user_id=user.id)
    session = SessionRepository(conn).create(
        body.name,
        user_id=user.id,
        organization_id=organization.id,
    )
    AnalyticsRepository(conn).record_product_event(
        event_name="session_created",
        organization_id=organization.id,
        user_id=user.id,
        session_id=session.id,
    )
    return session


@router.get("/sessions", response_model=list[Session])
def list_sessions(conn: ConnDep, user: GrantedUserDep, organization: OrgDep) -> list[Session]:
    membership = OrganizationRepository(conn).get_member(
        organization_id=organization.id,
        user_id=user.id,
    )
    if membership is None or membership.status != "active":
        raise HTTPException(status_code=403, detail="organization access denied")
    if membership.role not in ("admin", "member"):
        return []
    return SessionRepository(conn).list(user_id=user.id, organization_id=organization.id)


@router.get("/sessions/{session_id}", response_model=Session)
def get_session(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> Session:
    return _require_session(conn, session_id, user.id, organization.id)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> None:
    deleted = SessionRepository(conn).delete(
        session_id,
        user_id=user.id,
        organization_id=organization.id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    AnalyticsRepository(conn).record_product_event(
        event_name="session_deleted",
        organization_id=organization.id,
        user_id=user.id,
        metadata={"session_id": session_id},
    )
    conn.commit()
    session_dir = settings.session_dir(user.id, session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)
    background_tasks.add_task(_cleanup_unused_storage, settings, user.id)


def _cleanup_unused_storage(settings: Settings, user_id: str) -> None:
    """Run unused object cleanup outside the request transaction."""
    try:
        with session_scope(settings) as conn:
            result = cleanup_unused_storage_for_user(conn, settings, user_id)
        logger.info(
            "event=storage_gc.completed user_id=%s scanned=%s deleted=%s "
            "failed=%s pruned_source_assets=%s",
            user_id,
            result.scanned_keys,
            len(result.deleted_keys),
            len(result.failed_keys),
            len(result.pruned_source_asset_ids),
        )
    except Exception:
        logger.exception("event=storage_gc.failed user_id=%s", user_id)


class FileUploadResponse(BaseModel):
    files: list[FileModel]


class UploadIntentFileBody(BaseModel):
    filename: str
    size_bytes: int = Field(ge=0)
    content_type: str | None = None


class CreateUploadIntentsBody(BaseModel):
    files: list[UploadIntentFileBody] = Field(min_length=1)


class UploadIntentBody(BaseModel):
    intent_id: str
    object_key: str
    upload_url: str
    method: str = "PUT"
    headers: dict[str, str]
    expires_in_seconds: int


class CreateUploadIntentsResponse(BaseModel):
    intents: list[UploadIntentBody]


class ProcessingJobBody(BaseModel):
    job_id: str
    session_id: str
    kind: str
    job_status: str
    discovery_status: str
    events: list[ProcessingEvent] = Field(default_factory=list)


def _build_job_response(
    *, conn: DbConnection, job: ProcessingJob, discovery_status: str
) -> ProcessingJobBody:
    return ProcessingJobBody(
        job_id=job.id,
        session_id=job.session_id,
        kind=job.kind,
        job_status=job.status,
        discovery_status=discovery_status,
        events=ProcessingEventRepository(conn).list_for_session(job.session_id),
    )


@router.post(
    "/sessions/{session_id}/upload-intents", response_model=CreateUploadIntentsResponse
)
def create_upload_intents(
    session_id: str,
    body: CreateUploadIntentsBody,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> CreateUploadIntentsResponse:
    _require_session(conn, session_id, user.id, organization.id)
    object_store = get_object_store(settings)
    if object_store.backend != "r2":
        raise HTTPException(status_code=501, detail="direct uploads require R2 storage")

    intents: list[UploadIntentBody] = []
    reserved_bytes = 0
    for item in body.files:
        _ensure_upload_allowed(
            conn,
            organization_id=organization.id,
            user_id=user.id,
            size_bytes=item.size_bytes,
            filename=item.filename,
            reserved_bytes=reserved_bytes,
        )
        reserved_bytes += item.size_bytes
        content_type = item.content_type or "application/octet-stream"
        intent_id = new_id()
        object_key = staging_upload_key(user.id, session_id, intent_id, item.filename)
        intent = UploadIntentRepository(conn).create(
            user_id=user.id,
            organization_id=organization.id,
            session_id=session_id,
            original_filename=item.filename,
            mime_type=item.content_type,
            expected_size_bytes=item.size_bytes,
            storage_backend=object_store.backend,
            object_key=object_key,
            intent_id=intent_id,
        )
        try:
            upload_url = object_store.presigned_put_url(
                object_key,
                content_type=content_type,
                expires_seconds=settings.upload_url_expires_seconds,
            )
        except StorageError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        intents.append(
            UploadIntentBody(
                intent_id=intent.id,
                object_key=object_key,
                upload_url=upload_url,
                headers={"Content-Type": content_type},
                expires_in_seconds=settings.upload_url_expires_seconds,
            )
        )
    conn.commit()
    AnalyticsRepository(conn, auto_commit=True).record_product_event(
        event_name="upload_intents_created",
        organization_id=organization.id,
        user_id=user.id,
        session_id=session_id,
        metric_value=float(sum(file.size_bytes for file in body.files)),
        metadata={"count": len(body.files)},
    )
    logger.info(
        "event=upload_intents.created user_id=%s session_id=%s count=%s",
        user.id,
        session_id,
        len(intents),
    )
    return CreateUploadIntentsResponse(intents=intents)


@router.post(
    "/sessions/{session_id}/upload-intents/{intent_id}/complete",
    response_model=ProcessingJobBody,
)
def complete_upload_intent(
    session_id: str,
    intent_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> ProcessingJobBody:
    session = _require_session(conn, session_id, user.id, organization.id)
    intent_repo = UploadIntentRepository(conn)
    intent = intent_repo.get(intent_id, user_id=user.id)
    if intent is None or intent.session_id != session_id:
        raise HTTPException(status_code=404, detail="upload intent not found")
    jobs_repo = ProcessingJobRepository(conn)
    is_document = is_supported_document(intent.original_filename, intent.mime_type)
    job_kind: ProcessingJobKind = "ingest_document" if is_document else "ingest_upload"
    idempotency_key = f"{job_kind}:{intent.id}"
    existing_job = jobs_repo.get_by_idempotency_key(idempotency_key)
    if existing_job is not None and intent.status in {"uploaded", "processing", "processed"}:
        return _build_job_response(
            conn=conn, job=existing_job, discovery_status=session.discovery_status
        )
    object_store = get_object_store(settings)
    try:
        stored = object_store.head(intent.object_key)
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if stored.size_bytes != intent.expected_size_bytes:
        raise HTTPException(status_code=400, detail="uploaded object size did not match intent")
    _ensure_upload_allowed(
        conn,
        organization_id=session.organization_id or organization.id,
        user_id=user.id,
        size_bytes=stored.size_bytes,
        filename=intent.original_filename,
    )
    _ensure_job_limit(conn, organization_id=session.organization_id or organization.id, user_id=user.id)

    intent_repo.mark_uploaded(intent.id, observed_size_bytes=stored.size_bytes)
    SessionRepository(conn).set_status(session_id, "ingesting")
    events_repo = ProcessingEventRepository(conn)
    events_repo.clear(session_id)
    job = jobs_repo.create(
        user_id=user.id,
        organization_id=session.organization_id or organization.id,
        session_id=session_id,
        kind=job_kind,
        idempotency_key=idempotency_key,
        checkpoint_json={"upload_intent_id": intent.id},
    )
    events_repo.append(
        session_id=session_id,
        job_id=job.id,
        kind="queued",
        step_key="queued",
        level="info",
        progress=0,
        message=(
            f"{intent.original_filename} uploaded to R2 and queued for "
            f"{'document ingest' if is_document else 'ingest'}"
        ),
    )
    AnalyticsRepository(conn).record_product_event(
        event_name="processing_job_queued",
        organization_id=session.organization_id or organization.id,
        user_id=user.id,
        session_id=session_id,
        metadata={"kind": job_kind, "job_id": job.id},
    )
    _record_upload_completed(
        conn,
        organization_id=session.organization_id or organization.id,
        user_id=user.id,
        session_id=session_id,
        size_bytes=stored.size_bytes,
        count=1,
        source="direct",
    )
    conn.commit()
    logger.info(
        "event=upload_intent.completed user_id=%s session_id=%s intent_id=%s job_id=%s size_bytes=%s",
        user.id,
        session_id,
        intent.id,
        job.id,
        stored.size_bytes,
    )
    fresh = SessionRepository(conn).get(
        session.id,
        user_id=user.id,
        organization_id=organization.id,
    ) or session
    return _build_job_response(conn=conn, job=job, discovery_status=fresh.discovery_status)


@router.post("/sessions/{session_id}/files", response_model=FileUploadResponse)
def upload_files(
    session_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
    organization: OrgDep,
    uploads: Annotated[list[UploadFile], File()],
) -> FileUploadResponse:
    sessions_repo = SessionRepository(conn)
    _require_session(conn, session_id, user.id, organization.id)
    if not uploads:
        raise HTTPException(status_code=400, detail="no files provided")

    tmp_dir = settings.session_dir(user.id, session_id)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    files_repo = FileRepository(conn)
    source_assets_repo = SourceAssetRepository(conn)
    workspace_assets_repo = WorkspaceAssetRepository(conn)
    artifacts_repo = AssetArtifactRepository(conn)
    tables_repo = WorkspaceTableRepository(conn)
    object_store = get_object_store(settings)

    files_out: list[FileModel] = []
    any_new = False
    total_uploaded_bytes = 0
    uploaded_count = 0
    for upload in uploads:
        original = upload.filename or "upload.csv"
        tmp_path = tmp_dir / f"__upload_{original}"
        with tmp_path.open("wb") as dest:
            shutil.copyfileobj(upload.file, dest)
        upload_size = tmp_path.stat().st_size
        try:
            _ensure_upload_allowed(
                conn,
                organization_id=organization.id,
                user_id=user.id,
                size_bytes=upload_size,
                filename=original,
            )
        except HTTPException:
            tmp_path.unlink(missing_ok=True)
            raise
        try:
            ingested: list[IngestedFile] = ingest_file(
                source_path=tmp_path,
                original_filename=original,
                original_content_type=upload.content_type,
                original_size_bytes=upload_size,
                user_id=user.id,
                organization_id=organization.id,
                session_id=session_id,
                settings=settings,
                files_repo=files_repo,
                source_assets_repo=source_assets_repo,
                workspace_assets_repo=workspace_assets_repo,
                artifacts_repo=artifacts_repo,
                tables_repo=tables_repo,
                object_store=object_store,
            )
        except IngestError as exc:
            raise HTTPException(status_code=400, detail=f"{original}: {exc}") from exc
        finally:
            tmp_path.unlink(missing_ok=True)
        for item in ingested:
            files_out.append(item.file)
            if not item.duplicate:
                any_new = True
        total_uploaded_bytes += upload_size
        uploaded_count += 1

    if any_new:
        sessions_repo.set_discovery_status(session_id, "empty")
    if uploaded_count:
        _record_upload_completed(
            conn,
            organization_id=organization.id,
            user_id=user.id,
            session_id=session_id,
            size_bytes=total_uploaded_bytes,
            count=uploaded_count,
            source="legacy",
        )
    return FileUploadResponse(files=files_out)


@router.delete("/files/{file_id}", status_code=204)
def delete_file(
    file_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> None:
    file, session = _require_file_for_user(conn, file_id, user.id, organization.id)
    files_repo = FileRepository(conn)
    raw = settings.raw_parquet_path(user.id, session.id, file.id)
    processed = settings.parquet_path(user.id, session.id, file.id)
    files_repo.delete(file.id)
    for path in (raw, processed):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    sessions_repo = SessionRepository(conn)
    fresh = sessions_repo.get(session.id, user_id=user.id, organization_id=organization.id)
    if fresh is not None and fresh.discovery_status != "empty":
        sessions_repo.set_discovery_status(session.id, "empty")
        sessions_repo.set_overview(session.id, None)


@router.get("/sessions/{session_id}/files", response_model=list[FileModel])
def list_files(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> list[FileModel]:
    _require_session(conn, session_id, user.id, organization.id)
    return FileRepository(conn).list_for_session(session_id)


@router.get("/files/{file_id}", response_model=FileModel)
def get_file(
    file_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> FileModel:
    file, _ = _require_file_for_user(conn, file_id, user.id, organization.id)
    return file


class FilePreviewResponse(BaseModel):
    file_id: str
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int


@router.get("/files/{file_id}/preview", response_model=FilePreviewResponse)
def get_file_preview(
    file_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
    organization: OrgDep,
    limit: int = 100,
) -> FilePreviewResponse:
    _require_file_for_user(conn, file_id, user.id, organization.id)
    try:
        data = preview_rows(
            file_id=file_id,
            limit=limit,
            files_repo=FileRepository(conn),
            settings=settings,
            artifacts_repo=AssetArtifactRepository(conn),
            object_store=get_object_store(settings),
        )
    except ReingestError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FilePreviewResponse(**data)


class DiscoveredColumnBody(BaseModel):
    column_id: str
    name: str
    description: str = ""
    dtype: str = "string"
    confidence: float = 1.0
    low_confidence_reasons: list[str] = Field(default_factory=list)


class DiscoveredFileBody(BaseModel):
    file_id: str
    friendly_name: str = ""
    description: str = ""
    header_row: int = 0
    columns: list[DiscoveredColumnBody] = Field(default_factory=list)


class DiscoveredLinkBody(BaseModel):
    file_a_id: str
    col_a: str
    file_b_id: str
    col_b: str
    direction: str = "many_to_many"
    summary: str = ""
    confidence: float = 1.0
    low_confidence_reasons: list[str] = Field(default_factory=list)


class DiscoveryResponse(BaseModel):
    session_id: str
    status: str
    files: list[DiscoveredFileBody]
    links: list[DiscoveredLinkBody]
    overview: str


class SchemaCorrectionInterpretRequest(BaseModel):
    instruction: str


class SchemaCorrectionApplyRequest(BaseModel):
    patch: SchemaCorrectionPatch
    approved_op_ids: list[str] = Field(default_factory=list)


class SchemaCorrectionApplyResponse(BaseModel):
    discovery: DiscoveryResponse
    data_doc: DataDoc | None = None


class ChatFeedTurn(BaseModel):
    turn: ChatTurn
    messages: list[ChatMessage]
    artifacts: list[ChatArtifact]


class WorkspaceDocumentPageBody(BaseModel):
    id: str
    page_number: int
    source: str
    char_count: int
    quality_score: float
    low_confidence: bool
    quality_reasons: list[str] = Field(default_factory=list)
    has_review_image: bool = False


class WorkspaceDocumentBody(BaseModel):
    id: str
    filename: str
    page_count: int
    status: str
    created_at: datetime
    pages: list[WorkspaceDocumentPageBody] = Field(default_factory=list)


class DocumentPageDetailBody(WorkspaceDocumentPageBody):
    markdown: str
    review_image_url: str | None = None


class DocumentDetailBody(BaseModel):
    id: str
    filename: str
    page_count: int
    status: str
    created_at: datetime
    pages: list[DocumentPageDetailBody] = Field(default_factory=list)


class WorkspaceResponse(BaseModel):
    session: Session
    files: list[FileModel]
    documents: list[WorkspaceDocumentBody] = Field(default_factory=list)
    links: list[Link]
    discovery: DiscoveryResponse
    events: list[ProcessingEvent]
    chat_feed: list[ChatFeedTurn]
    data_doc: DataDoc | None = None


_INFERRED_TO_SIMPLE = {
    "string": "string",
    "int": "int",
    "float": "float",
    "date": "date",
    "datetime": "datetime",
    "bool": "bool",
    "category": "category",
}


def _workspace_document_body(document_repo: DocumentRepository, document_id: str) -> WorkspaceDocumentBody | None:
    document = document_repo.get(document_id)
    if document is None:
        return None
    return WorkspaceDocumentBody(
        id=document.id,
        filename=document.filename,
        page_count=document.page_count,
        status=document.status,
        created_at=document.created_at,
        pages=[
            WorkspaceDocumentPageBody(
                id=page.id,
                page_number=page.page_number,
                source=page.source,
                char_count=page.char_count,
                quality_score=page.quality_score,
                low_confidence=page.low_confidence,
                quality_reasons=page.quality_reasons,
                has_review_image=page.image_object_key is not None,
            )
            for page in document_repo.list_pages(document.id)
        ],
    )


def _workspace_documents(document_repo: DocumentRepository, session_id: str) -> list[WorkspaceDocumentBody]:
    documents: list[WorkspaceDocumentBody] = []
    for document in document_repo.list_for_session(session_id):
        body = _workspace_document_body(document_repo, document.id)
        if body is not None:
            documents.append(body)
    return documents


def _build_discovery_response(
    session_id: str,
    conn: DbConnection,
    user_id: str,
    organization_id: str | None = None,
) -> DiscoveryResponse:
    if organization_id is not None:
        _require_private_workspace_role(conn, organization_id=organization_id, user_id=user_id)
    sessions_repo = SessionRepository(conn)
    files_repo = FileRepository(conn)
    schemas_repo = SchemaRepository(conn)
    links_repo = LinkRepository(conn)

    session = sessions_repo.get(
        session_id,
        user_id=user_id,
        organization_id=organization_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    files = files_repo.list_for_session(session_id)
    file_bodies: list[DiscoveredFileBody] = []
    for f in files:
        schema = schemas_repo.get(f.id, f.schema_version)
        cols: list[DiscoveredColumnBody] = []
        if schema is not None:
            cols = [
                DiscoveredColumnBody(
                    column_id=c.column_id or c.name,
                    name=c.name,
                    description=c.description or "",
                    dtype=_INFERRED_TO_SIMPLE.get(c.inferred_kind, "string"),
                    confidence=c.confidence,
                    low_confidence_reasons=decode_confidence_reasons(c.confidence_reason),
                )
                for c in schema.columns
            ]
        file_bodies.append(
            DiscoveredFileBody(
                file_id=f.id,
                friendly_name=f.friendly_name or f.filename,
                description=f.description or "",
                header_row=f.header_row if f.header_row is not None else 0,
                columns=cols,
            )
        )
    link_bodies = [
        DiscoveredLinkBody(
            file_a_id=link.file_a,
            col_a=link.col_a,
            file_b_id=link.file_b,
            col_b=link.col_b,
            direction=link.direction,
            summary=link.summary or "",
            confidence=link.score,
            low_confidence_reasons=(
                [] if link.score >= 0.85 else ["link_confidence_below_threshold"]
            ),
        )
        for link in links_repo.list_for_session(session_id)
    ]
    return DiscoveryResponse(
        session_id=session_id,
        status=session.discovery_status,
        files=file_bodies,
        links=link_bodies,
        overview=session.overview or "",
    )


def _schema_context_from_discovery(
    discovery: DiscoveryResponse, data_doc: DataDoc | None
) -> dict[str, Any]:
    return {
        "session_id": discovery.session_id,
        "overview": discovery.overview,
        "files": [file.model_dump(mode="json") for file in discovery.files],
        "links": [link.model_dump(mode="json") for link in discovery.links],
        "data_doc": data_doc.model_dump(mode="json") if data_doc else None,
    }


def _approval_payload_from_discovery(discovery: DiscoveryResponse) -> ApprovalPayload:
    return ApprovalPayload(
        files=[
            FileSpec(
                file_id=file.file_id,
                header_row=file.header_row,
                friendly_name=file.friendly_name,
                description=file.description,
                columns=[
                    ColumnSpec(
                        column_id=column.column_id,
                        name=column.name,
                        dtype=column.dtype,
                        description=column.description,
                    )
                    for column in file.columns
                ],
            )
            for file in discovery.files
        ],
        links=[
            LinkSpec(
                file_a_id=link.file_a_id,
                col_a=link.col_a,
                file_b_id=link.file_b_id,
                col_b=link.col_b,
                direction=link.direction,
                summary=link.summary,
            )
            for link in discovery.links
        ],
        overview=discovery.overview,
    )


def _apply_minor_schema_corrections(
    *,
    conn: DbConnection,
    session_id: str,
    operations: list[CorrectionOperation],
    user_id: str,
) -> None:
    files_repo = FileRepository(conn)
    data_docs_repo = DataDocRepository(conn)
    files_by_id = {file.id: file for file in files_repo.list_for_session(session_id)}
    data_doc = data_docs_repo.get(session_id)
    for operation in operations:
        target = operation.target
        if operation.op_type == "set_friendly_name":
            file_id = target.get("file_id")
            if file_id:
                files_repo.set_metadata(file_id=file_id, friendly_name=str(operation.after_value))
        elif operation.op_type == "set_file_description":
            file_id = target.get("file_id")
            if file_id:
                files_repo.set_metadata(file_id=file_id, description=str(operation.after_value))
        elif operation.op_type == "set_column_description":
            file_id = target.get("file_id")
            column_id = target.get("column_id")
            file = files_by_id.get(file_id or "")
            if file is not None and column_id:
                conn.execute(
                    """UPDATE schema_columns
                       SET description = ?
                       WHERE file_id = ?
                         AND schema_version = ?
                         AND (column_id = ? OR name = ?)""",
                    (
                        str(operation.after_value),
                        file_id,
                        file.schema_version,
                        column_id,
                        column_id,
                    ),
                )
        elif operation.op_type in {"set_usage_note", "set_glossary_term", "set_starter_question"}:
            data_doc = _apply_data_doc_minor_operation(data_doc, session_id, operation)
        AuditRepository(conn).log(
            session_id=session_id,
            kind="schema_correction_applied",
            details={
                "user_id": user_id,
                "classification": operation.classification,
                "op_type": operation.op_type,
                "target": operation.target,
                "before": operation.before_value,
                "after": operation.after_value,
            },
        )
    if data_doc is not None:
        data_docs_repo.replace(data_doc)


def _apply_data_doc_minor_operation(
    data_doc: DataDoc | None, session_id: str, operation: CorrectionOperation
) -> DataDoc:
    now = datetime.now(UTC)
    doc = data_doc or DataDoc(
        session_id=session_id,
        overview="",
        files=[],
        relationships=[],
        glossary=[],
        created_at=now,
        updated_at=now,
    )
    if operation.op_type == "set_usage_note":
        doc.usage_notes = [str(operation.after_value)]
    elif operation.op_type == "set_starter_question":
        doc.starter_questions = [str(operation.after_value)]
    elif operation.op_type == "set_glossary_term":
        after = operation.after_value if isinstance(operation.after_value, dict) else {}
        term = str(after.get("term") or operation.target.get("term") or "")
        meaning = str(after.get("meaning") or after.get("description") or "")
        if term:
            existing = [item for item in doc.glossary if item.term != term]
            doc.glossary = [*existing, DataDocGlossaryItem(term=term, meaning=meaning)]
    doc.updated_at = now
    return doc


@router.post("/sessions/{session_id}/process", response_model=ProcessingJobBody, status_code=202)
def post_process(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> ProcessingJobBody:
    session = _require_session(conn, session_id, user.id, organization.id)
    sessions_repo = SessionRepository(conn)
    files_repo = FileRepository(conn)
    events_repo = ProcessingEventRepository(conn)
    jobs_repo = ProcessingJobRepository(conn)
    active_job = jobs_repo.active_for_session(session_id=session_id, kind="discovery")
    if active_job is not None:
        return _build_job_response(
            conn=conn, job=active_job, discovery_status=session.discovery_status
        )
    if not files_repo.list_for_session(session_id):
        raise HTTPException(status_code=400, detail="no files in session")
    _ensure_job_limit(conn, organization_id=organization.id, user_id=user.id)

    sessions_repo.set_discovery_status(session_id, "discovering")
    events_repo.clear(session_id)
    job = jobs_repo.create(
        user_id=user.id,
        organization_id=organization.id,
        session_id=session_id,
        kind="discovery",
        idempotency_key=f"discovery:{session_id}:{datetime.now(UTC).isoformat()}",
        checkpoint_json={},
    )
    events_repo.append(
        session_id=session_id,
        job_id=job.id,
        kind="queued",
        step_key="queued",
        level="info",
        progress=0,
        message="schema discovery queued",
    )
    AnalyticsRepository(conn).record_product_event(
        event_name="processing_job_queued",
        organization_id=organization.id,
        user_id=user.id,
        session_id=session_id,
        metadata={"kind": "discovery", "job_id": job.id},
    )
    conn.commit()
    logger.info(
        "event=processing.enqueue user_id=%s session_id=%s job_id=%s kind=discovery file_count=%s",
        user.id,
        session_id,
        job.id,
        len(files_repo.list_for_session(session_id)),
    )
    return _build_job_response(
        conn=conn, job=job, discovery_status="discovering"
    )


@router.get("/sessions/{session_id}/discovery", response_model=DiscoveryResponse)
def get_discovery(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> DiscoveryResponse:
    return _build_discovery_response(session_id, conn, user.id, organization.id)


@router.post(
    "/sessions/{session_id}/schema/interpret-correction",
    response_model=SchemaCorrectionPatch,
)
async def post_interpret_schema_correction(
    session_id: str,
    body: SchemaCorrectionInterpretRequest,
    conn: ConnDep,
    settings: SettingsDep,
    llm_client: LLMDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> SchemaCorrectionPatch:
    _require_session(conn, session_id, user.id, organization.id)
    if not settings.llm_api_key:
        raise HTTPException(status_code=400, detail="schema correction requires an LLM")
    discovery = _build_discovery_response(session_id, conn, user.id, organization.id)
    data_doc = DataDocRepository(conn).get(session_id)
    try:
        return await interpret_schema_correction(
            instruction=body.instruction,
            schema_context=_schema_context_from_discovery(discovery, data_doc),
            llm_client=llm_client,
            config=settings.processing.discovery,
        )
    except SchemaCorrectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/sessions/{session_id}/schema/apply-correction",
    response_model=SchemaCorrectionApplyResponse,
)
def post_apply_schema_correction(
    session_id: str,
    body: SchemaCorrectionApplyRequest,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> SchemaCorrectionApplyResponse:
    _require_session(conn, session_id, user.id, organization.id)
    patch = normalize_patch(body.patch)
    approved_op_ids = set(body.approved_op_ids)
    minor = minor_operations(patch)
    structural = selected_structural_operations(patch, approved_op_ids)

    if minor:
        _apply_minor_schema_corrections(
            conn=conn,
            session_id=session_id,
            operations=minor,
            user_id=user.id,
        )

    data_docs_repo = DataDocRepository(conn)
    if structural:
        discovery = _build_discovery_response(session_id, conn, user.id, organization.id)
        payload = apply_operations_to_payload(
            _approval_payload_from_discovery(discovery),
            structural,
        )
        files_repo = FileRepository(conn)
        apply_approval(
            session_id=session_id,
            user_id=user.id,
            organization_id=organization.id,
            payload=payload,
            settings=settings,
            files_repo=files_repo,
            schemas_repo=SchemaRepository(conn),
            links_repo=LinkRepository(conn),
            sessions_repo=SessionRepository(conn),
            events_repo=ProcessingEventRepository(conn),
            artifacts_repo=AssetArtifactRepository(conn),
            tables_repo=WorkspaceTableRepository(conn),
            object_store=get_object_store(settings),
        )
        _refresh_docs_from_approval(
            session_id=session_id,
            payload=payload,
            files_repo=files_repo,
            data_docs_repo=data_docs_repo,
        )
        for operation in structural:
            AuditRepository(conn).log(
                session_id=session_id,
                kind="schema_correction_applied",
                details={
                    "user_id": user.id,
                    "classification": operation.classification,
                    "op_type": operation.op_type,
                    "target": operation.target,
                    "before": operation.before_value,
                    "after": operation.after_value,
                    "transform": operation.transform,
                },
            )
    elif minor:
        discovery = _build_discovery_response(session_id, conn, user.id, organization.id)
        _refresh_docs_from_approval(
            session_id=session_id,
            payload=_approval_payload_from_discovery(discovery),
            files_repo=FileRepository(conn),
            data_docs_repo=data_docs_repo,
        )

    discovery = _build_discovery_response(session_id, conn, user.id, organization.id)
    return SchemaCorrectionApplyResponse(
        discovery=discovery,
        data_doc=data_docs_repo.get(session_id),
    )


@router.get("/sessions/{session_id}/workspace", response_model=WorkspaceResponse)
def get_workspace(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> WorkspaceResponse:
    session = _require_session(conn, session_id, user.id, organization.id)
    files = FileRepository(conn).list_for_session(session_id)
    document_repo = DocumentRepository(conn)
    documents = _workspace_documents(document_repo, session_id)
    links = LinkRepository(conn).list_for_session(session_id)
    discovery = _build_discovery_response(session_id, conn, user.id, organization.id)
    events = ProcessingEventRepository(conn).list_for_session(session_id)

    chat_repo = ChatRepository(conn)
    artifact_repo = ChatArtifactRepository(conn)
    messages_by_turn = chat_repo.list_messages_for_session(session_id)
    artifacts_by_turn = artifact_repo.list_for_session(session_id)
    chat_feed = [
        ChatFeedTurn(
            turn=turn,
            messages=messages_by_turn.get(turn.id, []),
            artifacts=artifacts_by_turn.get(turn.id, []),
        )
        for turn in chat_repo.list_turns(session_id)
    ]

    data_doc: DataDoc | None = None
    if session.discovery_status in {"pending_review", "approved"}:
        data_docs_repo = DataDocRepository(conn)
        data_doc = data_docs_repo.get(session_id)
        if data_doc is None:
            _synthesize_docs_from_current_schema(
                session_id=session_id,
                user_id=user.id,
                organization_id=organization.id,
                conn=conn,
                data_docs_repo=data_docs_repo,
            )
            conn.commit()
            data_doc = data_docs_repo.get(session_id)

    return WorkspaceResponse(
        session=session,
        files=files,
        documents=documents,
        links=links,
        discovery=discovery,
        events=events,
        chat_feed=chat_feed,
        data_doc=data_doc,
    )


@router.get("/sessions/{session_id}/documents", response_model=list[WorkspaceDocumentBody])
def list_documents(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> list[WorkspaceDocumentBody]:
    _require_session(conn, session_id, user.id, organization.id)
    return _workspace_documents(DocumentRepository(conn), session_id)


@router.get(
    "/sessions/{session_id}/documents/{document_id}",
    response_model=DocumentDetailBody,
)
def get_document_detail(
    session_id: str,
    document_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> DocumentDetailBody:
    _require_session(conn, session_id, user.id, organization.id)
    document_repo = DocumentRepository(conn)
    document = document_repo.get(document_id)
    if (
        document is None
        or document.session_id != session_id
        or document.user_id != user.id
        or document.organization_id != organization.id
    ):
        raise HTTPException(status_code=404, detail="document not found")
    object_store = None
    pages: list[DocumentPageDetailBody] = []
    for page in document_repo.list_pages(document.id):
        review_url = None
        if page.image_object_key:
            if object_store is None:
                object_store = get_object_store(settings)
            review_url = object_store.presigned_get_url(
                page.image_object_key,
                expires_seconds=settings.upload_url_expires_seconds,
            )
        pages.append(
            DocumentPageDetailBody(
                id=page.id,
                page_number=page.page_number,
                source=page.source,
                char_count=page.char_count,
                quality_score=page.quality_score,
                low_confidence=page.low_confidence,
                quality_reasons=page.quality_reasons,
                has_review_image=page.image_object_key is not None,
                markdown=page.markdown,
                review_image_url=review_url,
            )
        )
    return DocumentDetailBody(
        id=document.id,
        filename=document.filename,
        page_count=document.page_count,
        status=document.status,
        created_at=document.created_at,
        pages=pages,
    )


@router.get("/sessions/{session_id}/docs", response_model=DataDoc)
def get_data_docs(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> DataDoc:
    session = _require_session(conn, session_id, user.id, organization.id)
    if session.discovery_status not in {"pending_review", "approved"}:
        raise HTTPException(status_code=404, detail="data docs not generated yet")

    data_docs_repo = DataDocRepository(conn)
    doc = data_docs_repo.get(session_id)
    if doc is None:
        _synthesize_docs_from_current_schema(
            session_id=session_id,
            user_id=user.id,
            organization_id=organization.id,
            conn=conn,
            data_docs_repo=data_docs_repo,
        )
        conn.commit()
        doc = data_docs_repo.get(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="data docs not found")
    return doc


class ApprovalBody(BaseModel):
    files: list[DiscoveredFileBody]
    links: list[DiscoveredLinkBody]
    overview: str = ""


@router.post("/sessions/{session_id}/approve-schema", response_model=DiscoveryResponse)
def post_approve(
    session_id: str,
    body: ApprovalBody,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> DiscoveryResponse:
    _require_session(conn, session_id, user.id, organization.id)

    payload = ApprovalPayload(
        files=[
            FileSpec(
                file_id=f.file_id,
                header_row=f.header_row,
                friendly_name=f.friendly_name,
                description=f.description,
                columns=[
                    ColumnSpec(
                        column_id=c.column_id,
                        name=c.name,
                        dtype=c.dtype,
                        description=c.description,
                    )
                    for c in f.columns
                ],
            )
            for f in body.files
        ],
        links=[
            LinkSpec(
                file_a_id=link.file_a_id,
                col_a=link.col_a,
                file_b_id=link.file_b_id,
                col_b=link.col_b,
                direction=link.direction,
                summary=link.summary,
            )
            for link in body.links
        ],
        overview=body.overview,
    )
    try:
        files_repo = FileRepository(conn)
        data_docs_repo = DataDocRepository(conn)
        apply_approval(
            session_id=session_id,
            user_id=user.id,
            organization_id=organization.id,
            payload=payload,
            settings=settings,
            files_repo=files_repo,
            schemas_repo=SchemaRepository(conn),
            links_repo=LinkRepository(conn),
            sessions_repo=SessionRepository(conn),
            events_repo=ProcessingEventRepository(conn),
            artifacts_repo=AssetArtifactRepository(conn),
            tables_repo=WorkspaceTableRepository(conn),
            object_store=get_object_store(settings),
        )
        _refresh_docs_from_approval(
            session_id=session_id,
            payload=payload,
            files_repo=files_repo,
            data_docs_repo=data_docs_repo,
        )
        conn.commit()
    except ReingestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _build_discovery_response(session_id, conn, user.id, organization.id)


def _synthesize_docs_from_current_schema(
    *,
    session_id: str,
    user_id: str,
    organization_id: str | None = None,
    conn: DbConnection,
    data_docs_repo: DataDocRepository,
) -> None:
    discovery = _build_discovery_response(
        session_id,
        conn,
        user_id=user_id,
        organization_id=organization_id,
    )
    if not discovery.files:
        return
    payload = ApprovalPayload(
        files=[
            FileSpec(
                file_id=file.file_id,
                header_row=file.header_row,
                friendly_name=file.friendly_name,
                description=file.description,
                columns=[
                    ColumnSpec(
                        column_id=column.column_id,
                        name=column.name,
                        dtype=column.dtype,
                        description=column.description,
                    )
                    for column in file.columns
                ],
            )
            for file in discovery.files
        ],
        links=[
            LinkSpec(
                file_a_id=link.file_a_id,
                col_a=link.col_a,
                file_b_id=link.file_b_id,
                col_b=link.col_b,
                direction=link.direction,
                summary=link.summary,
            )
            for link in discovery.links
        ],
        overview=discovery.overview,
    )
    _refresh_docs_from_approval(
        session_id=session_id,
        payload=payload,
        files_repo=FileRepository(conn),
        data_docs_repo=data_docs_repo,
    )


def _refresh_docs_from_approval(
    *,
    session_id: str,
    payload: ApprovalPayload,
    files_repo: FileRepository,
    data_docs_repo: DataDocRepository,
) -> None:
    existing = data_docs_repo.get(session_id)
    existing_files = {file.file_id: file for file in existing.files} if existing else {}
    existing_relationships = {
        (rel.left_file_id, rel.left_column, rel.right_file_id, rel.right_column): rel
        for rel in existing.relationships
    } if existing else {}
    file_docs: list[DataDocFile] = []
    for spec in payload.files:
        file = files_repo.get(spec.file_id)
        old = existing_files.get(spec.file_id)
        old_cols = {col.name: col for col in old.columns} if old else {}
        file_docs.append(
            DataDocFile(
                file_id=spec.file_id,
                name=spec.friendly_name,
                description=spec.description,
                grain=old.grain if old else "One row in this file.",
                row_count=file.row_count if file else 0,
                columns=[
                    DataDocColumn(
                        name=col.name,
                        dtype=col.dtype,
                        meaning=col.description,
                        role=old_col.role if (old_col := old_cols.get(col.name)) else None,
                    )
                    for col in spec.columns
                ],
                key_columns=old.key_columns if old else [],
                date_columns=old.date_columns if old else [],
                measure_columns=old.measure_columns if old else [],
                category_columns=old.category_columns if old else [],
                caveats=old.caveats if old else [],
            )
        )
    relationships = []
    for link in payload.links:
        key = (link.file_a_id, link.col_a, link.file_b_id, link.col_b)
        old_rel = existing_relationships.get(key)
        relationships.append(
            DataDocRelationship(
                left_file_id=link.file_a_id,
                left_column=link.col_a,
                right_file_id=link.file_b_id,
                right_column=link.col_b,
                explanation=link.summary or (old_rel.explanation if old_rel else ""),
            )
        )
    now = existing.updated_at if existing else datetime.now(UTC)
    data_docs_repo.replace(
        DataDoc(
            session_id=session_id,
            overview=payload.overview or (existing.overview if existing else ""),
            files=file_docs,
            relationships=relationships,
            glossary=existing.glossary if existing else [],
            usage_notes=existing.usage_notes if existing else [],
            starter_questions=existing.starter_questions if existing else [],
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
    )


@router.get("/sessions/{session_id}/processing", response_model=list[ProcessingEvent])
def get_processing_events(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> list[ProcessingEvent]:
    _require_session(conn, session_id, user.id, organization.id)
    return ProcessingEventRepository(conn).list_for_session(session_id)


@router.get("/sessions/{session_id}/links", response_model=list[Link])
def get_session_links(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> list[Link]:
    _require_session(conn, session_id, user.id, organization.id)
    return LinkRepository(conn).list_for_session(session_id)
