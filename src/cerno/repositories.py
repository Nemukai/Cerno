from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from cerno.db import dumps_json, loads_json
from cerno.models import (
    Anomaly,
    AuditEvent,
    ChatMessage,
    ChatTurn,
    Dashboard,
    DashboardPage,
    File,
    FileSchema,
    Link,
    LinkReview,
    MessageRole,
    NotebookCell,
    ReviewStatus,
    RunStatus,
    SchemaColumn,
    Session,
    SessionStatus,
    TurnState,
)


def new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class SessionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, name: str, session_id: str | None = None) -> Session:
        sid = session_id or new_id()
        created_at = _now()
        self.conn.execute(
            "INSERT INTO sessions (id, name, status, created_at) VALUES (?, ?, ?, ?)",
            (sid, name, "new", created_at.isoformat()),
        )
        return Session(id=sid, name=name, status="new", created_at=created_at)

    def get(self, session_id: str) -> Session | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _row_to_session(row) if row else None

    def list(self) -> list[Session]:
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC"
        ).fetchall()
        return [_row_to_session(r) for r in rows]

    def set_status(self, session_id: str, status: SessionStatus) -> None:
        self.conn.execute(
            "UPDATE sessions SET status = ? WHERE id = ?", (status, session_id)
        )


class FileRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        session_id: str,
        filename: str,
        parquet_path: str,
        row_count: int,
        file_id: str | None = None,
    ) -> File:
        fid = file_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO files (id, session_id, filename, parquet_path, row_count, schema_version, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (fid, session_id, filename, parquet_path, row_count, created_at.isoformat()),
        )
        return File(
            id=fid,
            session_id=session_id,
            filename=filename,
            parquet_path=parquet_path,
            row_count=row_count,
            schema_version=1,
            created_at=created_at,
        )

    def get(self, file_id: str) -> File | None:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return _row_to_file(row) if row else None

    def list_for_session(self, session_id: str) -> list[File]:
        rows = self.conn.execute(
            "SELECT * FROM files WHERE session_id = ? ORDER BY created_at", (session_id,)
        ).fetchall()
        return [_row_to_file(r) for r in rows]

    def bump_schema_version(self, file_id: str) -> int:
        cursor = self.conn.execute(
            "UPDATE files SET schema_version = schema_version + 1 WHERE id = ? RETURNING schema_version",
            (file_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(f"file not found: {file_id}")
        return int(row[0])


class SchemaRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def replace(self, schema: FileSchema) -> None:
        self.conn.execute(
            "DELETE FROM schema_columns WHERE file_id = ? AND schema_version = ?",
            (schema.file_id, schema.schema_version),
        )
        for col in schema.columns:
            self.conn.execute(
                """INSERT INTO schema_columns
                   (file_id, schema_version, name, dtype, inferred_kind, confidence, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    col.file_id,
                    col.schema_version,
                    col.name,
                    col.dtype,
                    col.inferred_kind,
                    col.confidence,
                    col.position,
                ),
            )

    def get(self, file_id: str, schema_version: int) -> FileSchema | None:
        rows = self.conn.execute(
            """SELECT * FROM schema_columns
               WHERE file_id = ? AND schema_version = ?
               ORDER BY position""",
            (file_id, schema_version),
        ).fetchall()
        if not rows:
            return None
        return FileSchema(
            file_id=file_id,
            schema_version=schema_version,
            columns=[_row_to_schema_column(r) for r in rows],
        )


class LinkRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        session_id: str,
        file_a: str,
        col_a: str,
        file_b: str,
        col_b: str,
        overlap: float,
        direction: str,
        score: float,
        summary: str | None = None,
        source: str = "discovered",
        link_id: str | None = None,
    ) -> Link:
        lid = link_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO links
               (id, session_id, file_a, col_a, file_b, col_b, overlap, direction, score, summary, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lid,
                session_id,
                file_a,
                col_a,
                file_b,
                col_b,
                overlap,
                direction,
                score,
                summary,
                source,
                created_at.isoformat(),
            ),
        )
        return Link(
            id=lid,
            session_id=session_id,
            file_a=file_a,
            col_a=col_a,
            file_b=file_b,
            col_b=col_b,
            overlap=overlap,
            direction=direction,  # type: ignore[arg-type]
            score=score,
            summary=summary,
            source=source,  # type: ignore[arg-type]
            created_at=created_at,
        )

    def get(self, link_id: str) -> Link | None:
        row = self.conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
        return _row_to_link(row) if row else None

    def latest_review(self, link_id: str) -> LinkReview | None:
        row = self.conn.execute(
            """SELECT * FROM link_reviews WHERE link_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (link_id,),
        ).fetchone()
        if row is None:
            return None
        return LinkReview(
            id=row["id"],
            link_id=row["link_id"],
            action=row["action"],
            notes=row["notes"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_for_session(self, session_id: str) -> list[Link]:
        rows = self.conn.execute(
            "SELECT * FROM links WHERE session_id = ? ORDER BY score DESC",
            (session_id,),
        ).fetchall()
        return [_row_to_link(r) for r in rows]

    def add_review(
        self, *, link_id: str, action: str, notes: str | None = None
    ) -> LinkReview:
        created_at = _now()
        cursor = self.conn.execute(
            """INSERT INTO link_reviews (link_id, action, notes, created_at)
               VALUES (?, ?, ?, ?)""",
            (link_id, action, notes, created_at.isoformat()),
        )
        return LinkReview(
            id=cursor.lastrowid,
            link_id=link_id,
            action=action,  # type: ignore[arg-type]
            notes=notes,
            created_at=created_at,
        )


class AnomalyRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, anomaly: Anomaly) -> Anomaly:
        self.conn.execute(
            """INSERT INTO anomalies
               (id, session_id, file_id, row_id, detector, reason_plain, reason_technical,
                score_normalized, score_raw, source_code, review_status, reviewed_at, reviewed_by,
                notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                anomaly.id,
                anomaly.session_id,
                anomaly.file_id,
                anomaly.row_id,
                anomaly.detector,
                anomaly.reason_plain,
                anomaly.reason_technical,
                anomaly.score_normalized,
                anomaly.score_raw,
                anomaly.source_code,
                anomaly.review_status,
                anomaly.reviewed_at.isoformat() if anomaly.reviewed_at else None,
                anomaly.reviewed_by,
                anomaly.notes,
                anomaly.created_at.isoformat(),
            ),
        )
        return anomaly

    def top_for_session(self, session_id: str, limit: int = 20) -> list[Anomaly]:
        rows = self.conn.execute(
            """SELECT * FROM anomalies
               WHERE session_id = ? AND review_status IS NULL
               ORDER BY score_normalized DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [_row_to_anomaly(r) for r in rows]

    def set_review(
        self,
        *,
        anomaly_id: str,
        status: ReviewStatus | None,
        reviewed_by: str | None,
        notes: str | None,
    ) -> None:
        self.conn.execute(
            """UPDATE anomalies
               SET review_status = ?, reviewed_at = ?, reviewed_by = ?, notes = ?
               WHERE id = ?""",
            (
                status,
                _now().isoformat() if status else None,
                reviewed_by,
                notes,
                anomaly_id,
            ),
        )


class DashboardRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, session_id: str, dashboard_id: str | None = None) -> Dashboard:
        did = dashboard_id or new_id()
        created_at = _now()
        self.conn.execute(
            "INSERT INTO dashboards (id, session_id, created_at) VALUES (?, ?, ?)",
            (did, session_id, created_at.isoformat()),
        )
        return Dashboard(id=did, session_id=session_id, created_at=created_at)

    def get_for_session(self, session_id: str) -> Dashboard | None:
        row = self.conn.execute(
            "SELECT * FROM dashboards WHERE session_id = ?", (session_id,)
        ).fetchone()
        return (
            Dashboard(
                id=row["id"],
                session_id=row["session_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            if row
            else None
        )

    def add_page(
        self,
        *,
        dashboard_id: str,
        title: str,
        kind: str,
        position: int,
        source_chat_turn_id: str | None = None,
        pinned: bool = False,
        page_id: str | None = None,
    ) -> DashboardPage:
        pid = page_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO dashboard_pages
               (id, dashboard_id, title, kind, source_chat_turn_id, pinned, position, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, dashboard_id, title, kind, source_chat_turn_id, int(pinned), position, created_at.isoformat()),
        )
        return DashboardPage(
            id=pid,
            dashboard_id=dashboard_id,
            title=title,
            kind=kind,  # type: ignore[arg-type]
            source_chat_turn_id=source_chat_turn_id,
            pinned=pinned,
            position=position,
            created_at=created_at,
        )

    def list_pages(self, dashboard_id: str) -> list[DashboardPage]:
        rows = self.conn.execute(
            "SELECT * FROM dashboard_pages WHERE dashboard_id = ? ORDER BY position",
            (dashboard_id,),
        ).fetchall()
        return [_row_to_page(r) for r in rows]

    def delete_page(self, page_id: str) -> None:
        self.conn.execute("DELETE FROM dashboard_pages WHERE id = ?", (page_id,))


class NotebookRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add_cell(self, cell: NotebookCell) -> NotebookCell:
        self.conn.execute(
            """INSERT INTO notebook_cells
               (id, page_id, order_index, kind, code, output, bound_file_ids,
                bound_schema_versions, threshold_snapshot, last_run_at, last_run_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cell.id,
                cell.page_id,
                cell.order_index,
                cell.kind,
                cell.code,
                dumps_json(cell.output) if cell.output is not None else None,
                dumps_json(cell.bound_file_ids),
                dumps_json(cell.bound_schema_versions),
                dumps_json(cell.threshold_snapshot),
                cell.last_run_at.isoformat() if cell.last_run_at else None,
                cell.last_run_status,
                cell.created_at.isoformat(),
            ),
        )
        return cell

    def list_for_page(self, page_id: str) -> list[NotebookCell]:
        rows = self.conn.execute(
            "SELECT * FROM notebook_cells WHERE page_id = ? ORDER BY order_index",
            (page_id,),
        ).fetchall()
        return [_row_to_cell(r) for r in rows]

    def set_output(
        self,
        *,
        cell_id: str,
        output: dict[str, Any] | None,
        status: RunStatus,
    ) -> None:
        self.conn.execute(
            """UPDATE notebook_cells
               SET output = ?, last_run_at = ?, last_run_status = ?
               WHERE id = ?""",
            (
                dumps_json(output) if output is not None else None,
                _now().isoformat(),
                status,
                cell_id,
            ),
        )


class ChatRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_turn(
        self, *, session_id: str, user_message: str, turn_id: str | None = None
    ) -> ChatTurn:
        tid = turn_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO chat_turns (id, session_id, user_message, state, created_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (tid, session_id, user_message, created_at.isoformat()),
        )
        return ChatTurn(
            id=tid,
            session_id=session_id,
            user_message=user_message,
            state="pending",
            created_at=created_at,
        )

    def set_turn_state(self, turn_id: str, state: TurnState) -> None:
        self.conn.execute(
            "UPDATE chat_turns SET state = ? WHERE id = ?", (state, turn_id)
        )

    def complete_turn(
        self,
        *,
        turn_id: str,
        assistant_message: str,
        spawned_page_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """UPDATE chat_turns
               SET assistant_message = ?, spawned_page_id = ?, state = 'complete'
               WHERE id = ?""",
            (assistant_message, spawned_page_id, turn_id),
        )

    def append_message(
        self,
        *,
        turn_id: str,
        role: MessageRole,
        content: str,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> ChatMessage:
        mid = new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO chat_messages
               (id, turn_id, role, content, tool_name, tool_args, tool_result, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mid,
                turn_id,
                role,
                content,
                tool_name,
                dumps_json(tool_args) if tool_args is not None else None,
                dumps_json(tool_result) if tool_result is not None else None,
                created_at.isoformat(),
            ),
        )
        return ChatMessage(
            id=mid,
            turn_id=turn_id,
            role=role,
            content=content,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            created_at=created_at,
        )

    def list_turns(self, session_id: str) -> list[ChatTurn]:
        rows = self.conn.execute(
            "SELECT * FROM chat_turns WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [_row_to_turn(r) for r in rows]

    def list_messages(self, turn_id: str) -> list[ChatMessage]:
        rows = self.conn.execute(
            "SELECT * FROM chat_messages WHERE turn_id = ? ORDER BY created_at",
            (turn_id,),
        ).fetchall()
        return [_row_to_message(r) for r in rows]


class AuditRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def log(
        self,
        *,
        kind: str,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        created_at = _now()
        cursor = self.conn.execute(
            """INSERT INTO audit_events (session_id, kind, details, created_at)
               VALUES (?, ?, ?, ?)""",
            (session_id, kind, dumps_json(details or {}), created_at.isoformat()),
        )
        return AuditEvent(
            id=cursor.lastrowid,
            session_id=session_id,
            kind=kind,
            details=details or {},
            created_at=created_at,
        )


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        name=row["name"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_file(row: sqlite3.Row) -> File:
    return File(
        id=row["id"],
        session_id=row["session_id"],
        filename=row["filename"],
        parquet_path=row["parquet_path"],
        row_count=row["row_count"],
        schema_version=row["schema_version"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_schema_column(row: sqlite3.Row) -> SchemaColumn:
    return SchemaColumn(
        file_id=row["file_id"],
        schema_version=row["schema_version"],
        name=row["name"],
        dtype=row["dtype"],
        inferred_kind=row["inferred_kind"],
        confidence=row["confidence"],
        position=row["position"],
    )


def _row_to_link(row: sqlite3.Row) -> Link:
    return Link(
        id=row["id"],
        session_id=row["session_id"],
        file_a=row["file_a"],
        col_a=row["col_a"],
        file_b=row["file_b"],
        col_b=row["col_b"],
        overlap=row["overlap"],
        direction=row["direction"],
        score=row["score"],
        summary=row["summary"],
        source=row["source"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_anomaly(row: sqlite3.Row) -> Anomaly:
    return Anomaly(
        id=row["id"],
        session_id=row["session_id"],
        file_id=row["file_id"],
        row_id=row["row_id"],
        detector=row["detector"],
        reason_plain=row["reason_plain"],
        reason_technical=row["reason_technical"],
        score_normalized=row["score_normalized"],
        score_raw=row["score_raw"],
        source_code=row["source_code"],
        review_status=row["review_status"],
        reviewed_at=_parse_dt(row["reviewed_at"]),
        reviewed_by=row["reviewed_by"],
        notes=row["notes"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_page(row: sqlite3.Row) -> DashboardPage:
    return DashboardPage(
        id=row["id"],
        dashboard_id=row["dashboard_id"],
        title=row["title"],
        kind=row["kind"],
        source_chat_turn_id=row["source_chat_turn_id"],
        pinned=bool(row["pinned"]),
        position=row["position"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_cell(row: sqlite3.Row) -> NotebookCell:
    return NotebookCell(
        id=row["id"],
        page_id=row["page_id"],
        order_index=row["order_index"],
        kind=row["kind"],
        code=row["code"],
        output=loads_json(row["output"]),
        bound_file_ids=loads_json(row["bound_file_ids"], default=[]),
        bound_schema_versions=loads_json(row["bound_schema_versions"], default={}),
        threshold_snapshot=loads_json(row["threshold_snapshot"], default={}),
        last_run_at=_parse_dt(row["last_run_at"]),
        last_run_status=row["last_run_status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_turn(row: sqlite3.Row) -> ChatTurn:
    return ChatTurn(
        id=row["id"],
        session_id=row["session_id"],
        user_message=row["user_message"],
        assistant_message=row["assistant_message"],
        spawned_page_id=row["spawned_page_id"],
        state=row["state"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_message(row: sqlite3.Row) -> ChatMessage:
    return ChatMessage(
        id=row["id"],
        turn_id=row["turn_id"],
        role=row["role"],
        content=row["content"],
        tool_name=row["tool_name"],
        tool_args=loads_json(row["tool_args"]),
        tool_result=loads_json(row["tool_result"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
