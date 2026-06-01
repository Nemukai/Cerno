from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path

from cerno.config import Settings, get_settings
from cerno.db import DbConnection, connect
from cerno.llm import LLMClient, LLMTokenBudget
from cerno.log_config import configure_logging
from cerno.models import ProcessingJob
from cerno.providers import resolve_embedding_provider, resolve_ocr_engine
from cerno.repositories import (
    AnalyticsRepository,
    AssetArtifactRepository,
    DataDocRepository,
    DocumentChunkRepository,
    DocumentRepository,
    FileRepository,
    LinkRepository,
    LLMUsageRepository,
    ProcessingEventRepository,
    ProcessingJobRepository,
    SessionRepository,
    SourceAssetRepository,
    UploadIntentRepository,
    UsageLimitRepository,
    WorkspaceAssetRepository,
    WorkspaceTableRepository,
)
from cerno.services.discovery import DiscoveryError, run_discovery
from cerno.services.document_ingest import ingest_document
from cerno.services.document_search import index_document_chunks
from cerno.services.ingest import hash_file, ingest_file
from cerno.storage import get_object_store

logger = logging.getLogger(__name__)


def _worker_id(settings: Settings) -> str:
    configured = settings.worker_id.strip()
    if configured:
        return configured
    return f"{socket.gethostname()}:{os.getpid()}"


def _append_event(
    events_repo: ProcessingEventRepository,
    *,
    job: ProcessingJob,
    kind: str,
    step_key: str,
    message: str,
    progress: int,
    level: str = "info",
    details: dict[str, object] | None = None,
) -> None:
    events_repo.append(
        session_id=job.session_id,
        job_id=job.id,
        kind=kind,  # type: ignore[arg-type]
        step_key=step_key,
        level=level,
        progress=progress,
        message=message,
        details=details,
    )


def _job_duration_ms(job: ProcessingJob) -> int:
    started_at = job.started_at or job.created_at
    return max(0, round((datetime.now(UTC) - started_at).total_seconds() * 1000))


def _llm_budget(settings: Settings, conn: DbConnection, job: ProcessingJob) -> LLMTokenBudget:
    if job.organization_id is None:
        return LLMTokenBudget(user_daily_token_cap=settings.daily_token_cap)
    limits = UsageLimitRepository(conn).effective_for(
        organization_id=job.organization_id,
        user_id=job.user_id,
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


async def _run_ingest_upload(settings: Settings, job: ProcessingJob, worker_id: str) -> None:
    conn = connect(settings)
    object_store = get_object_store(settings)
    try:
        jobs_repo = ProcessingJobRepository(conn)
        events_repo = ProcessingEventRepository(conn)
        sessions_repo = SessionRepository(conn)
        intents_repo = UploadIntentRepository(conn)
        intent_id = job.checkpoint_json.get("upload_intent_id")
        if not isinstance(intent_id, str):
            raise RuntimeError("ingest job missing upload_intent_id")
        intent = intents_repo.get(intent_id, user_id=job.user_id)
        if intent is None:
            raise RuntimeError(f"upload intent not found: {intent_id}")

        logger.info(
            "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=ingest_start intent_id=%s filename=%s",
            job.user_id,
            job.session_id,
            job.id,
            intent.id,
            intent.original_filename,
        )
        intents_repo.mark_processing(intent.id)
        sessions_repo.set_status(job.session_id, "ingesting")
        _append_event(
            events_repo,
            job=job,
            kind="ingesting_file",
            step_key="loading_artifacts",
            progress=15,
            message=f"loading {intent.original_filename} from R2",
        )
        conn.commit()

        local_path = settings.object_cache_path(intent.object_key)
        object_store.get_to_path(intent.object_key, local_path)
        jobs_repo.heartbeat(
            job_id=job.id,
            worker_id=worker_id,
            checkpoint_json={**job.checkpoint_json, "phase": "downloaded_upload"},
            lock_seconds=settings.worker_lock_seconds,
        )
        conn.commit()

        _append_event(
            events_repo,
            job=job,
            kind="ingesting_file",
            step_key="reading_files",
            progress=35,
            message=f"parsing {intent.original_filename}",
        )
        conn.commit()

        ingested = ingest_file(
            source_path=Path(local_path),
            original_filename=intent.original_filename,
            original_content_type=intent.mime_type,
            original_size_bytes=intent.observed_size_bytes or intent.expected_size_bytes,
            user_id=job.user_id,
            organization_id=job.organization_id,
            session_id=job.session_id,
            settings=settings,
            files_repo=FileRepository(conn),
            source_assets_repo=SourceAssetRepository(conn),
            workspace_assets_repo=WorkspaceAssetRepository(conn),
            artifacts_repo=AssetArtifactRepository(conn),
            tables_repo=WorkspaceTableRepository(conn),
            object_store=object_store,
        )
        content_hash = hash_file(Path(local_path))
        source_asset = SourceAssetRepository(conn).get_by_hash(
            job.user_id,
            content_hash,
            organization_id=job.organization_id,
        )
        intents_repo.mark_processed(
            intent.id,
            source_asset_id=source_asset.id if source_asset is not None else None,
        )
        sessions_repo.set_status(job.session_id, "new")
        sessions_repo.set_discovery_status(job.session_id, "empty")
        _append_event(
            events_repo,
            job=job,
            kind="done",
            step_key="done",
            progress=100,
            message=f"finished ingesting {len(ingested)} table(s)",
            details={"file_count": len(ingested)},
        )
        jobs_repo.mark_succeeded(job.id)
        AnalyticsRepository(conn).record_product_event(
            event_name="processing_job_completed",
            organization_id=job.organization_id,
            user_id=job.user_id,
            session_id=job.session_id,
            metric_value=float(_job_duration_ms(job)),
            metadata={"kind": "ingest_upload", "job_id": job.id, "file_count": len(ingested)},
        )
        conn.commit()
        try:
            object_store.delete(intent.object_key)
        except Exception:
            logger.warning(
                "event=upload_staging.delete_failed user_id=%s session_id=%s job_id=%s object_key=%s",
                job.user_id,
                job.session_id,
                job.id,
                intent.object_key,
            )
        logger.info(
            "event=processing.complete user_id=%s session_id=%s job_id=%s kind=ingest_upload file_count=%s",
            job.user_id,
            job.session_id,
            job.id,
            len(ingested),
        )
    except Exception as exc:
        conn.rollback()
        try:
            intent_id = job.checkpoint_json.get("upload_intent_id")
            if isinstance(intent_id, str):
                UploadIntentRepository(conn).mark_failed(intent_id, str(exc))
            SessionRepository(conn).set_status(job.session_id, "new")
            ProcessingJobRepository(conn).mark_failed(job.id, str(exc))
            _append_event(
                ProcessingEventRepository(conn),
                job=job,
                kind="error",
                step_key="error",
                level="error",
                progress=100,
                message=f"Upload ingest failed: {exc}",
            )
            conn.commit()
        finally:
            try:
                AnalyticsRepository(conn, auto_commit=True).record_product_event(
                    event_name="processing_job_failed",
                    organization_id=job.organization_id,
                    user_id=job.user_id,
                    session_id=job.session_id,
                    metric_value=float(_job_duration_ms(job)),
                    metadata={"kind": "ingest_upload", "job_id": job.id},
                )
            except Exception:
                logger.warning("event=analytics.processing_job_failed_write_failed job_id=%s", job.id)
            logger.exception(
                "event=processing.failed user_id=%s session_id=%s job_id=%s kind=ingest_upload",
                job.user_id,
                job.session_id,
                job.id,
            )
        raise
    finally:
        conn.close()


async def _run_ingest_document(settings: Settings, job: ProcessingJob, worker_id: str) -> None:
    conn = connect(settings)
    object_store = get_object_store(settings)
    try:
        jobs_repo = ProcessingJobRepository(conn)
        events_repo = ProcessingEventRepository(conn)
        sessions_repo = SessionRepository(conn)
        intents_repo = UploadIntentRepository(conn)
        intent_id = job.checkpoint_json.get("upload_intent_id")
        if not isinstance(intent_id, str):
            raise RuntimeError("document ingest job missing upload_intent_id")
        intent = intents_repo.get(intent_id, user_id=job.user_id)
        if intent is None:
            raise RuntimeError(f"upload intent not found: {intent_id}")
        organization_id = job.organization_id
        if organization_id is None:
            raise RuntimeError("document ingest requires organization_id")

        intents_repo.mark_processing(intent.id)
        sessions_repo.set_status(job.session_id, "ingesting")
        _append_event(
            events_repo,
            job=job,
            kind="reading_document",
            step_key="loading_artifacts",
            progress=10,
            message=f"loading {intent.original_filename} from R2",
        )
        conn.commit()

        local_path = settings.object_cache_path(intent.object_key)
        object_store.get_to_path(intent.object_key, local_path)
        jobs_repo.heartbeat(
            job_id=job.id,
            worker_id=worker_id,
            checkpoint_json={**job.checkpoint_json, "phase": "downloaded_document"},
            lock_seconds=settings.worker_lock_seconds,
        )
        conn.commit()

        def progress(
            kind: str,
            step_key: str,
            message: str,
            progress_value: int,
            details: dict[str, object] | None,
        ) -> None:
            event_details = dict(details or {})
            level = str(event_details.pop("level", "info"))
            _append_event(
                events_repo,
                job=job,
                kind=kind,
                step_key=step_key,
                progress=progress_value,
                message=message,
                level=level,
                details=event_details,
            )
            jobs_repo.heartbeat(
                job_id=job.id,
                worker_id=worker_id,
                checkpoint_json={
                    **job.checkpoint_json,
                    "phase": step_key,
                    "progress": progress_value,
                },
                lock_seconds=settings.worker_lock_seconds,
            )
            conn.commit()

        ocr_client = LLMClient(settings=settings).with_usage(
            LLMUsageRepository(conn, auto_commit=True),
            job.user_id,
            organization_id=organization_id,
            token_budget=_llm_budget(settings, conn, job),
            metadata={"session_id": job.session_id, "job_id": job.id, "kind": "ocr"},
        )
        ingested = await ingest_document(
            source_path=Path(local_path),
            original_filename=intent.original_filename,
            original_content_type=intent.mime_type,
            original_size_bytes=intent.observed_size_bytes or intent.expected_size_bytes,
            user_id=job.user_id,
            organization_id=organization_id,
            session_id=job.session_id,
            settings=settings,
            documents_repo=DocumentRepository(conn),
            source_assets_repo=SourceAssetRepository(conn),
            workspace_assets_repo=WorkspaceAssetRepository(conn),
            artifacts_repo=AssetArtifactRepository(conn),
            object_store=object_store,
            ocr_engine=resolve_ocr_engine(settings),
            llm_client=ocr_client,
            progress=progress,
        )
        _append_event(
            events_repo,
            job=job,
            kind="reading_document",
            step_key="embedding_chunks",
            progress=97,
            message=f"embedding {len(ingested.pages)} document page(s)",
        )
        jobs_repo.heartbeat(
            job_id=job.id,
            worker_id=worker_id,
            checkpoint_json={**job.checkpoint_json, "phase": "embedding_chunks", "progress": 97},
            lock_seconds=settings.worker_lock_seconds,
        )
        await index_document_chunks(
            document=ingested.document,
            pages=ingested.pages,
            chunk_repo=DocumentChunkRepository(conn),
            embedding_provider=resolve_embedding_provider(settings),
            settings=settings,
            llm_client=LLMClient(settings=settings).with_usage(
                LLMUsageRepository(conn, auto_commit=True),
                job.user_id,
                organization_id=organization_id,
                token_budget=_llm_budget(settings, conn, job),
                metadata={"session_id": job.session_id, "job_id": job.id, "kind": "embedding"},
            ),
        )
        content_hash = hash_file(Path(local_path))
        source_asset = SourceAssetRepository(conn).get_by_hash(
            job.user_id,
            content_hash,
            organization_id=organization_id,
        )
        intents_repo.mark_processed(
            intent.id,
            source_asset_id=source_asset.id if source_asset is not None else None,
        )
        sessions_repo.set_status(job.session_id, "new")
        jobs_repo.mark_succeeded(job.id)
        AnalyticsRepository(conn).record_product_event(
            event_name="processing_job_completed",
            organization_id=job.organization_id,
            user_id=job.user_id,
            session_id=job.session_id,
            metric_value=float(_job_duration_ms(job)),
            metadata={
                "kind": "ingest_document",
                "job_id": job.id,
                "document_id": ingested.document.id,
                "page_count": len(ingested.pages),
                "truncated": ingested.truncated,
            },
        )
        conn.commit()
        try:
            object_store.delete(intent.object_key)
        except Exception:
            logger.warning(
                "event=document_upload_staging.delete_failed user_id=%s session_id=%s job_id=%s object_key=%s",
                job.user_id,
                job.session_id,
                job.id,
                intent.object_key,
            )
    except Exception as exc:
        conn.rollback()
        try:
            intent_id = job.checkpoint_json.get("upload_intent_id")
            if isinstance(intent_id, str):
                UploadIntentRepository(conn).mark_failed(intent_id, str(exc))
            SessionRepository(conn).set_status(job.session_id, "new")
            ProcessingJobRepository(conn).mark_failed(job.id, str(exc))
            _append_event(
                ProcessingEventRepository(conn),
                job=job,
                kind="error",
                step_key="error",
                level="error",
                progress=100,
                message=f"Document ingest failed: {exc}",
            )
            conn.commit()
        finally:
            logger.exception(
                "event=processing.failed user_id=%s session_id=%s job_id=%s kind=ingest_document",
                job.user_id,
                job.session_id,
                job.id,
            )
        raise
    finally:
        conn.close()


async def _run_discovery(settings: Settings, job: ProcessingJob) -> None:
    conn = connect(settings)
    try:
        ProcessingJobRepository(conn).heartbeat(
            job_id=job.id,
            worker_id=job.locked_by or "",
            checkpoint_json={**job.checkpoint_json, "phase": "discovery_start"},
            lock_seconds=settings.worker_lock_seconds,
        )
        conn.commit()
        logger.info(
            "event=processing.checkpoint user_id=%s session_id=%s job_id=%s phase=discovery_start",
            job.user_id,
            job.session_id,
            job.id,
        )
        await run_discovery(
            session_id=job.session_id,
            settings=settings,
            files_repo=FileRepository(conn),
            sessions_repo=SessionRepository(conn),
            links_repo=LinkRepository(conn),
            data_docs_repo=DataDocRepository(conn),
            events_repo=ProcessingEventRepository(conn),
            llm_client=LLMClient(settings=settings).with_usage(
                LLMUsageRepository(conn, auto_commit=True),
                job.user_id,
                organization_id=job.organization_id,
                token_budget=_llm_budget(settings, conn, job),
                metadata={"session_id": job.session_id, "job_id": job.id},
            ),
            artifacts_repo=AssetArtifactRepository(conn),
            object_store=get_object_store(settings),
            job_id=job.id,
            user_id=job.user_id,
            clear_events=False,
        )
        ProcessingJobRepository(conn).mark_succeeded(job.id)
        AnalyticsRepository(conn).record_product_event(
            event_name="processing_job_completed",
            organization_id=job.organization_id,
            user_id=job.user_id,
            session_id=job.session_id,
            metric_value=float(_job_duration_ms(job)),
            metadata={"kind": "discovery", "job_id": job.id},
        )
        conn.commit()
    except DiscoveryError as exc:
        conn.rollback()
        _mark_discovery_failed(conn, job, str(exc))
        raise
    except Exception as exc:
        conn.rollback()
        _mark_discovery_failed(conn, job, str(exc))
        raise
    finally:
        conn.close()


def _mark_discovery_failed(conn: DbConnection, job: ProcessingJob, message: str) -> None:
    sessions_repo = SessionRepository(conn)
    events_repo = ProcessingEventRepository(conn)
    jobs_repo = ProcessingJobRepository(conn)
    sessions_repo.set_discovery_status(job.session_id, "failed")
    events_repo.append(
        session_id=job.session_id,
        job_id=job.id,
        kind="error",
        step_key="error",
        level="error",
        progress=100,
        message=message[:1000],
    )
    jobs_repo.mark_failed(job.id, message)
    AnalyticsRepository(conn).record_product_event(
        event_name="processing_job_failed",
        organization_id=job.organization_id,
        user_id=job.user_id,
        session_id=job.session_id,
        metric_value=float(_job_duration_ms(job)),
        metadata={"kind": "discovery", "job_id": job.id, "error": message[:300]},
    )
    conn.commit()
    logger.exception(
        "event=processing.failed user_id=%s session_id=%s job_id=%s kind=discovery error=%s",
        job.user_id,
        job.session_id,
        job.id,
        message,
    )


async def _process_job(settings: Settings, job: ProcessingJob, worker_id: str) -> None:
    if job.kind == "ingest_upload":
        await _run_ingest_upload(settings, job, worker_id)
        return
    if job.kind == "ingest_document":
        await _run_ingest_document(settings, job, worker_id)
        return
    if job.kind == "discovery":
        await _run_discovery(settings, job)
        return
    raise RuntimeError(f"unknown job kind: {job.kind}")


def run_once(settings: Settings, worker_id: str) -> bool:
    conn = connect(settings)
    try:
        job = ProcessingJobRepository(conn).claim_next(
            worker_id=worker_id,
            lock_seconds=settings.worker_lock_seconds,
        )
    finally:
        conn.close()
    if job is None:
        return False
    logger.info(
        "event=processing.claimed user_id=%s session_id=%s job_id=%s kind=%s attempts=%s worker_id=%s",
        job.user_id,
        job.session_id,
        job.id,
        job.kind,
        job.attempts,
        worker_id,
    )
    try:
        asyncio.run(_process_job(settings, job, worker_id))
    except Exception:
        logger.exception(
            "event=processing.job_error user_id=%s session_id=%s job_id=%s kind=%s worker_id=%s",
            job.user_id,
            job.session_id,
            job.id,
            job.kind,
            worker_id,
        )
    return True


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    worker_id = _worker_id(settings)
    logger.info("event=worker.started worker_id=%s", worker_id)
    while True:
        did_work = run_once(settings, worker_id)
        if not did_work:
            time.sleep(settings.worker_poll_interval_seconds)


if __name__ == "__main__":
    main()
