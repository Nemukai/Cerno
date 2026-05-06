from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from cerno.api.deps import ConnDep, GrantedUserDep, SettingsDep
from cerno.config import Settings
from cerno.models import (
    DataDoc,
    DataDocColumn,
    DataDocFile,
    DataDocRelationship,
    Link,
    ProcessingEvent,
    ProcessingJob,
    Session,
)
from cerno.models import File as FileModel
from cerno.repositories import (
    AssetArtifactRepository,
    DataDocRepository,
    FileRepository,
    LinkRepository,
    ProcessingEventRepository,
    ProcessingJobRepository,
    SchemaRepository,
    SessionRepository,
    SourceAssetRepository,
    UploadIntentRepository,
    WorkspaceAssetRepository,
    WorkspaceTableRepository,
    new_id,
)
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
from cerno.storage import StorageError, get_object_store, staging_upload_key

router = APIRouter(tags=["sessions"])
logger = logging.getLogger(__name__)


def _user_storage_bytes(settings: Settings, user_id: str) -> int:
    root = settings.user_dir(user_id)
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _require_session(conn: sqlite3.Connection, session_id: str, user_id: str) -> Session:
    session = SessionRepository(conn).get(session_id, user_id=user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _require_file_for_user(
    conn: sqlite3.Connection, file_id: str, user_id: str
) -> tuple[FileModel, Session]:
    file = FileRepository(conn).get(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    session = SessionRepository(conn).get(file.session_id, user_id=user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="file not found")
    return file, session


class CreateSessionBody(BaseModel):
    name: str


@router.post("/sessions", response_model=Session)
def create_session(body: CreateSessionBody, conn: ConnDep, user: GrantedUserDep) -> Session:
    return SessionRepository(conn).create(body.name, user_id=user.id)


@router.get("/sessions", response_model=list[Session])
def list_sessions(conn: ConnDep, user: GrantedUserDep) -> list[Session]:
    return SessionRepository(conn).list(user_id=user.id)


@router.get("/sessions/{session_id}", response_model=Session)
def get_session(session_id: str, conn: ConnDep, user: GrantedUserDep) -> Session:
    return _require_session(conn, session_id, user.id)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str, conn: ConnDep, settings: SettingsDep, user: GrantedUserDep
) -> None:
    deleted = SessionRepository(conn).delete(session_id, user_id=user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    session_dir = settings.session_dir(user.id, session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)


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
    *, conn: sqlite3.Connection, job: ProcessingJob, discovery_status: str
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
) -> CreateUploadIntentsResponse:
    _require_session(conn, session_id, user.id)
    object_store = get_object_store(settings)
    if object_store.backend != "r2":
        raise HTTPException(status_code=501, detail="direct uploads require R2 storage")

    intents_repo = UploadIntentRepository(conn)
    intents: list[UploadIntentBody] = []
    for item in body.files:
        if item.size_bytes > settings.per_user_quota_bytes():
            raise HTTPException(status_code=413, detail=f"{item.filename}: file exceeds quota")
        content_type = item.content_type or "application/octet-stream"
        intent_id = new_id()
        object_key = staging_upload_key(user.id, session_id, intent_id, item.filename)
        intent = intents_repo.create(
            user_id=user.id,
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
) -> ProcessingJobBody:
    session = _require_session(conn, session_id, user.id)
    intent_repo = UploadIntentRepository(conn)
    intent = intent_repo.get(intent_id, user_id=user.id)
    if intent is None or intent.session_id != session_id:
        raise HTTPException(status_code=404, detail="upload intent not found")
    jobs_repo = ProcessingJobRepository(conn)
    idempotency_key = f"ingest_upload:{intent.id}"
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

    intent_repo.mark_uploaded(intent.id, observed_size_bytes=stored.size_bytes)
    SessionRepository(conn).set_status(session_id, "ingesting")
    events_repo = ProcessingEventRepository(conn)
    events_repo.clear(session_id)
    job = jobs_repo.create(
        user_id=user.id,
        session_id=session_id,
        kind="ingest_upload",
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
        message=f"{intent.original_filename} uploaded to R2 and queued for ingest",
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
    fresh = SessionRepository(conn).get(session.id, user_id=user.id) or session
    return _build_job_response(conn=conn, job=job, discovery_status=fresh.discovery_status)


@router.post("/sessions/{session_id}/files", response_model=FileUploadResponse)
def upload_files(
    session_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
    uploads: Annotated[list[UploadFile], File()],
) -> FileUploadResponse:
    sessions_repo = SessionRepository(conn)
    _require_session(conn, session_id, user.id)
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
    for upload in uploads:
        original = upload.filename or "upload.csv"
        tmp_path = tmp_dir / f"__upload_{original}"
        with tmp_path.open("wb") as dest:
            shutil.copyfileobj(upload.file, dest)
        upload_size = tmp_path.stat().st_size
        current_usage = max(0, _user_storage_bytes(settings, user.id) - upload_size)
        if current_usage + upload_size > settings.per_user_quota_bytes():
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=413, detail="per-user storage quota exceeded")
        try:
            ingested: list[IngestedFile] = ingest_file(
                source_path=tmp_path,
                original_filename=original,
                original_content_type=upload.content_type,
                original_size_bytes=upload_size,
                user_id=user.id,
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

    if any_new:
        sessions_repo.set_discovery_status(session_id, "empty")
    return FileUploadResponse(files=files_out)


@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: str, conn: ConnDep, settings: SettingsDep, user: GrantedUserDep) -> None:
    file, session = _require_file_for_user(conn, file_id, user.id)
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
    fresh = sessions_repo.get(session.id, user_id=user.id)
    if fresh is not None and fresh.discovery_status != "empty":
        sessions_repo.set_discovery_status(session.id, "empty")
        sessions_repo.set_overview(session.id, None)


@router.get("/sessions/{session_id}/files", response_model=list[FileModel])
def list_files(session_id: str, conn: ConnDep, user: GrantedUserDep) -> list[FileModel]:
    _require_session(conn, session_id, user.id)
    return FileRepository(conn).list_for_session(session_id)


@router.get("/files/{file_id}", response_model=FileModel)
def get_file(file_id: str, conn: ConnDep, user: GrantedUserDep) -> FileModel:
    file, _ = _require_file_for_user(conn, file_id, user.id)
    return file


class FilePreviewResponse(BaseModel):
    file_id: str
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int


@router.get("/files/{file_id}/preview", response_model=FilePreviewResponse)
def get_file_preview(
    file_id: str, conn: ConnDep, user: GrantedUserDep, limit: int = 100
) -> FilePreviewResponse:
    _require_file_for_user(conn, file_id, user.id)
    try:
        data = preview_rows(file_id=file_id, limit=limit, files_repo=FileRepository(conn))
    except ReingestError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FilePreviewResponse(**data)


class DiscoveredColumnBody(BaseModel):
    column_id: str
    name: str
    description: str = ""
    dtype: str = "string"


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


class DiscoveryResponse(BaseModel):
    session_id: str
    status: str
    files: list[DiscoveredFileBody]
    links: list[DiscoveredLinkBody]
    overview: str


_INFERRED_TO_SIMPLE = {
    "string": "string",
    "int": "int",
    "float": "float",
    "date": "date",
    "datetime": "datetime",
    "bool": "bool",
    "category": "category",
}


def _build_discovery_response(
    session_id: str, conn: sqlite3.Connection, user_id: str
) -> DiscoveryResponse:
    sessions_repo = SessionRepository(conn)
    files_repo = FileRepository(conn)
    schemas_repo = SchemaRepository(conn)
    links_repo = LinkRepository(conn)

    session = sessions_repo.get(session_id, user_id=user_id)
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


@router.post("/sessions/{session_id}/process", response_model=ProcessingJobBody, status_code=202)
def post_process(
    session_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
) -> ProcessingJobBody:
    session = _require_session(conn, session_id, user.id)
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

    sessions_repo.set_discovery_status(session_id, "discovering")
    events_repo.clear(session_id)
    job = jobs_repo.create(
        user_id=user.id,
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
def get_discovery(session_id: str, conn: ConnDep, user: GrantedUserDep) -> DiscoveryResponse:
    return _build_discovery_response(session_id, conn, user.id)


@router.get("/sessions/{session_id}/docs", response_model=DataDoc)
def get_data_docs(session_id: str, conn: ConnDep, user: GrantedUserDep) -> DataDoc:
    session = _require_session(conn, session_id, user.id)
    if session.discovery_status not in {"pending_review", "approved"}:
        raise HTTPException(status_code=404, detail="data docs not generated yet")

    data_docs_repo = DataDocRepository(conn)
    doc = data_docs_repo.get(session_id)
    if doc is None:
        _synthesize_docs_from_current_schema(
            session_id=session_id,
            user_id=user.id,
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
) -> DiscoveryResponse:
    _require_session(conn, session_id, user.id)

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

    return _build_discovery_response(session_id, conn, user.id)


def _synthesize_docs_from_current_schema(
    *,
    session_id: str,
    user_id: str,
    conn: sqlite3.Connection,
    data_docs_repo: DataDocRepository,
) -> None:
    discovery = _build_discovery_response(session_id, conn, user_id=user_id)
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
                        role=(old_cols.get(col.name).role if old_cols.get(col.name) else None),
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
    session_id: str, conn: ConnDep, user: GrantedUserDep
) -> list[ProcessingEvent]:
    _require_session(conn, session_id, user.id)
    return ProcessingEventRepository(conn).list_for_session(session_id)


@router.get("/sessions/{session_id}/links", response_model=list[Link])
def get_session_links(session_id: str, conn: ConnDep, user: GrantedUserDep) -> list[Link]:
    _require_session(conn, session_id, user.id)
    return LinkRepository(conn).list_for_session(session_id)
