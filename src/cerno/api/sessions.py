from __future__ import annotations

import shutil
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from cerno.api.deps import ConnDep, LLMDep, SettingsDep
from cerno.models import File as FileModel
from cerno.models import Link, ProcessingEvent, Session
from cerno.repositories import (
    FileRepository,
    LinkRepository,
    ProcessingEventRepository,
    SchemaRepository,
    SessionRepository,
)
from cerno.services.discovery import DiscoveryError, run_discovery
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

router = APIRouter(tags=["sessions"])


class CreateSessionBody(BaseModel):
    name: str


@router.post("/sessions", response_model=Session)
def create_session(body: CreateSessionBody, conn: ConnDep) -> Session:
    return SessionRepository(conn).create(body.name)


@router.get("/sessions", response_model=list[Session])
def list_sessions(conn: ConnDep) -> list[Session]:
    return SessionRepository(conn).list()


@router.get("/sessions/{session_id}", response_model=Session)
def get_session(session_id: str, conn: ConnDep) -> Session:
    session = SessionRepository(conn).get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, conn: ConnDep, settings: SettingsDep) -> None:
    deleted = SessionRepository(conn).delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    session_dir = settings.session_dir(session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)


class FileUploadResponse(BaseModel):
    files: list[FileModel]


@router.post("/sessions/{session_id}/files", response_model=FileUploadResponse)
def upload_files(
    session_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    uploads: Annotated[list[UploadFile], File()],
) -> FileUploadResponse:
    sessions_repo = SessionRepository(conn)
    session = sessions_repo.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not uploads:
        raise HTTPException(status_code=400, detail="no files provided")

    tmp_dir = settings.session_dir(session_id)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    files_repo = FileRepository(conn)

    files_out: list[FileModel] = []
    any_new = False
    for upload in uploads:
        original = upload.filename or "upload.csv"
        tmp_path = tmp_dir / f"__upload_{original}"
        with tmp_path.open("wb") as dest:
            shutil.copyfileobj(upload.file, dest)
        try:
            ingested: list[IngestedFile] = ingest_file(
                source_path=tmp_path,
                original_filename=original,
                session_id=session_id,
                settings=settings,
                files_repo=files_repo,
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
def delete_file(file_id: str, conn: ConnDep, settings: SettingsDep) -> None:
    files_repo = FileRepository(conn)
    file = files_repo.get(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    session_id = file.session_id
    raw = settings.raw_parquet_path(session_id, file_id)
    processed = settings.parquet_path(session_id, file_id)
    files_repo.delete(file_id)
    for path in (raw, processed):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    sessions_repo = SessionRepository(conn)
    session = sessions_repo.get(session_id)
    if session is not None and session.discovery_status != "empty":
        sessions_repo.set_discovery_status(session_id, "empty")
        sessions_repo.set_overview(session_id, None)


@router.get("/sessions/{session_id}/files", response_model=list[FileModel])
def list_files(session_id: str, conn: ConnDep) -> list[FileModel]:
    return FileRepository(conn).list_for_session(session_id)


@router.get("/files/{file_id}", response_model=FileModel)
def get_file(file_id: str, conn: ConnDep) -> FileModel:
    file = FileRepository(conn).get(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    return file


class FilePreviewResponse(BaseModel):
    file_id: str
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int


@router.get("/files/{file_id}/preview", response_model=FilePreviewResponse)
def get_file_preview(
    file_id: str, conn: ConnDep, limit: int = 100
) -> FilePreviewResponse:
    try:
        data = preview_rows(
            file_id=file_id, limit=limit, files_repo=FileRepository(conn)
        )
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
    session_id: str, conn: sqlite3.Connection
) -> DiscoveryResponse:
    sessions_repo = SessionRepository(conn)
    files_repo = FileRepository(conn)
    schemas_repo = SchemaRepository(conn)
    links_repo = LinkRepository(conn)

    session = sessions_repo.get(session_id)
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


@router.post("/sessions/{session_id}/process", response_model=DiscoveryResponse)
async def post_process(
    session_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    llm_client: LLMDep,
) -> DiscoveryResponse:
    sessions_repo = SessionRepository(conn)
    if sessions_repo.get(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")

    files_repo = FileRepository(conn)
    links_repo = LinkRepository(conn)
    events_repo = ProcessingEventRepository(conn)

    try:
        result = await run_discovery(
            session_id=session_id,
            settings=settings,
            files_repo=files_repo,
            sessions_repo=sessions_repo,
            links_repo=links_repo,
            events_repo=events_repo,
            llm_client=llm_client,
        )
    except DiscoveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return DiscoveryResponse(
        session_id=session_id,
        status="pending_review",
        files=[
            DiscoveredFileBody(
                file_id=df.file_id,
                friendly_name=df.friendly_name,
                description=df.description,
                header_row=df.header_row,
                columns=[
                    DiscoveredColumnBody(
                        column_id=c.column_id,
                        name=c.name,
                        description=c.description,
                        dtype=c.dtype,
                    )
                    for c in df.columns
                ],
            )
            for df in result.files
        ],
        links=[
            DiscoveredLinkBody(
                file_a_id=link.file_a_id,
                col_a=link.col_a,
                file_b_id=link.file_b_id,
                col_b=link.col_b,
                direction=link.direction,
                summary=link.summary,
            )
            for link in result.links
        ],
        overview=result.overview,
    )


@router.get("/sessions/{session_id}/discovery", response_model=DiscoveryResponse)
def get_discovery(session_id: str, conn: ConnDep) -> DiscoveryResponse:
    return _build_discovery_response(session_id, conn)


class ApprovalBody(BaseModel):
    files: list[DiscoveredFileBody]
    links: list[DiscoveredLinkBody]
    overview: str = ""


@router.post(
    "/sessions/{session_id}/approve-schema", response_model=DiscoveryResponse
)
def post_approve(
    session_id: str,
    body: ApprovalBody,
    conn: ConnDep,
    settings: SettingsDep,
) -> DiscoveryResponse:
    sessions_repo = SessionRepository(conn)
    if sessions_repo.get(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")

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
        apply_approval(
            session_id=session_id,
            payload=payload,
            settings=settings,
            files_repo=FileRepository(conn),
            schemas_repo=SchemaRepository(conn),
            links_repo=LinkRepository(conn),
            sessions_repo=sessions_repo,
            events_repo=ProcessingEventRepository(conn),
        )
    except ReingestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _build_discovery_response(session_id, conn)


@router.get(
    "/sessions/{session_id}/processing", response_model=list[ProcessingEvent]
)
def get_processing_events(
    session_id: str, conn: ConnDep
) -> list[ProcessingEvent]:
    return ProcessingEventRepository(conn).list_for_session(session_id)


@router.get("/sessions/{session_id}/links", response_model=list[Link])
def get_session_links(session_id: str, conn: ConnDep) -> list[Link]:
    return LinkRepository(conn).list_for_session(session_id)
