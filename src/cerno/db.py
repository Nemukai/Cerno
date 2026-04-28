from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from cerno.config import Settings

SCHEMA_VERSION = 4

_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            parquet_path TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_files_session ON files(session_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS schema_columns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            schema_version INTEGER NOT NULL,
            name TEXT NOT NULL,
            dtype TEXT NOT NULL,
            inferred_kind TEXT NOT NULL,
            confidence REAL NOT NULL,
            position INTEGER NOT NULL,
            UNIQUE(file_id, schema_version, name)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_schema_columns_file ON schema_columns(file_id, schema_version)
        """,
        """
        CREATE TABLE IF NOT EXISTS links (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            file_a TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            col_a TEXT NOT NULL,
            file_b TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            col_b TEXT NOT NULL,
            overlap REAL NOT NULL,
            direction TEXT NOT NULL,
            score REAL NOT NULL,
            source TEXT NOT NULL DEFAULT 'discovered',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_links_session ON links(session_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS link_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id TEXT NOT NULL REFERENCES links(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS anomalies (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            row_id INTEGER NOT NULL,
            detector TEXT NOT NULL,
            reason_plain TEXT NOT NULL,
            reason_technical TEXT NOT NULL,
            score_normalized REAL NOT NULL,
            score_raw REAL NOT NULL,
            source_code TEXT NOT NULL,
            review_status TEXT,
            reviewed_at TEXT,
            reviewed_by TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_anomalies_session ON anomalies(session_id, score_normalized DESC)
        """,
        """
        CREATE TABLE IF NOT EXISTS dashboards (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dashboard_pages (
            id TEXT PRIMARY KEY,
            dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            source_chat_turn_id TEXT,
            pinned INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_pages_dashboard ON dashboard_pages(dashboard_id, position)
        """,
        """
        CREATE TABLE IF NOT EXISTS notebook_cells (
            id TEXT PRIMARY KEY,
            page_id TEXT NOT NULL REFERENCES dashboard_pages(id) ON DELETE CASCADE,
            order_index INTEGER NOT NULL,
            kind TEXT NOT NULL,
            code TEXT NOT NULL,
            output TEXT,
            bound_file_ids TEXT NOT NULL DEFAULT '[]',
            bound_schema_versions TEXT NOT NULL DEFAULT '{}',
            threshold_snapshot TEXT NOT NULL DEFAULT '{}',
            last_run_at TEXT,
            last_run_status TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_cells_page ON notebook_cells(page_id, order_index)
        """,
        """
        CREATE TABLE IF NOT EXISTS chat_turns (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            user_message TEXT NOT NULL,
            assistant_message TEXT,
            spawned_page_id TEXT REFERENCES dashboard_pages(id) ON DELETE SET NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_turns_session ON chat_turns(session_id, created_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_name TEXT,
            tool_args TEXT,
            tool_result TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_messages_turn ON chat_messages(turn_id, created_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            kind TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events(session_id, created_at)
        """,
    ],
    2: [
        "ALTER TABLE links ADD COLUMN summary TEXT",
    ],
    3: [
        "ALTER TABLE sessions ADD COLUMN discovery_status TEXT NOT NULL DEFAULT 'empty'",
        "ALTER TABLE sessions ADD COLUMN overview TEXT",
        "ALTER TABLE files ADD COLUMN raw_parquet_path TEXT",
        "ALTER TABLE files ADD COLUMN header_row INTEGER",
        "ALTER TABLE files ADD COLUMN friendly_name TEXT",
        "ALTER TABLE files ADD COLUMN description TEXT",
        "ALTER TABLE schema_columns ADD COLUMN description TEXT",
        "ALTER TABLE schema_columns ADD COLUMN column_id TEXT",
        """
        CREATE TABLE IF NOT EXISTS processing_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_processing_events_session ON processing_events(session_id, id)",
    ],
    4: [
        "ALTER TABLE files ADD COLUMN content_hash TEXT",
        "CREATE INDEX IF NOT EXISTS idx_files_session_hash ON files(session_id, content_hash)",
    ],
}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    cursor = conn.execute("PRAGMA user_version")
    current = cursor.fetchone()[0]
    for version in sorted(_MIGRATIONS):
        if version <= current:
            continue
        for statement in _MIGRATIONS[version]:
            conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


def _register_adapters() -> None:
    sqlite3.register_adapter(datetime, lambda v: v.isoformat())


_register_adapters()


def connect(settings: Settings) -> sqlite3.Connection:
    path = settings.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _apply_migrations(conn)
    return conn


def connect_memory() -> sqlite3.Connection:
    conn = sqlite3.connect(
        ":memory:", detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _apply_migrations(conn)
    return conn


@contextmanager
def session_scope(settings: Settings) -> Iterator[sqlite3.Connection]:
    conn = connect(settings)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dumps_json(value: Any) -> str:
    return json.dumps(value, default=_json_default, separators=(",", ":"))


def loads_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
