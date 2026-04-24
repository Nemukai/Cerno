from __future__ import annotations

import shutil
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from cerno.api.deps import ConnDep, SettingsDep
from cerno.models import File as FileModel
from cerno.models import FileSchema, Session
from cerno.repositories import FileRepository, SchemaRepository, SessionRepository
from cerno.services.ingest import IngestedFile, IngestError, ingest_file

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


class FileUploadResponse(BaseModel):
    files: list[FileModel]


@router.post("/sessions/{session_id}/files", response_model=FileUploadResponse)
def upload_files(
    session_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    uploads: Annotated[list[UploadFile], File()],
) -> FileUploadResponse:
    session = SessionRepository(conn).get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not uploads:
        raise HTTPException(status_code=400, detail="no files provided")

    tmp_dir = settings.session_dir(session_id)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    files_out: list[FileModel] = []
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
                files_repo=FileRepository(conn),
                schemas_repo=SchemaRepository(conn),
            )
        except IngestError as exc:
            raise HTTPException(status_code=400, detail=f"{original}: {exc}") from exc
        finally:
            tmp_path.unlink(missing_ok=True)
        files_out.extend(i.file for i in ingested)

    return FileUploadResponse(files=files_out)


@router.get("/sessions/{session_id}/files", response_model=list[FileModel])
def list_files(session_id: str, conn: ConnDep) -> list[FileModel]:
    return FileRepository(conn).list_for_session(session_id)


@router.get("/files/{file_id}", response_model=FileModel)
def get_file(file_id: str, conn: ConnDep) -> FileModel:
    file = FileRepository(conn).get(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    return file


class FileSchemaResponse(BaseModel):
    file: FileModel
    schema_: FileSchema

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(**kwargs)
        data["schema"] = data.pop("schema_")
        return data


@router.get("/files/{file_id}/schema")
def get_file_schema(file_id: str, conn: ConnDep) -> dict[str, Any]:
    file = FileRepository(conn).get(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    schema = SchemaRepository(conn).get(file_id, file.schema_version)
    if schema is None:
        raise HTTPException(status_code=404, detail="schema not found")
    return {"file": file.model_dump(), "schema": schema.model_dump()}
