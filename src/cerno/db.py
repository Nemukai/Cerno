from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from cerno.config import Settings

POSTGRES_SCHEMA_VERSION = 3


class DbRow:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._keys = list(data.keys())

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._data[self._keys[key]]
        return self._data[key]

    def keys(self) -> list[str]:
        return list(self._keys)


class DbCursor:
    def __init__(self, cursor: Any, lastrowid: int | None = None) -> None:
        self._cursor = cursor
        self.lastrowid = lastrowid

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    def fetchone(self) -> DbRow | None:
        row = self._cursor.fetchone()
        return _wrap_pg_row(row)

    def fetchall(self) -> list[DbRow]:
        rows: list[DbRow] = []
        for row in self._cursor.fetchall():
            wrapped = _wrap_pg_row(row)
            if wrapped is not None:
                rows.append(wrapped)
        return rows


def connect(settings: Settings) -> PostgresCompatConnection:
    if settings.use_postgres():
        return connect_postgres(settings)
    raise RuntimeError("CERNO_POSTGRES_URL must be set; no local database fallback is supported")


class PostgresCompatConnection:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> DbCursor:
        translated = _translate_sql_for_postgres(sql)
        translated, fetch_lastrowid = _maybe_add_lastrowid_returning(translated)
        cursor = self._conn.cursor()
        cursor.execute(translated, params)
        lastrowid: int | None = None
        if fetch_lastrowid:
            row = cursor.fetchone()
            if row is not None:
                lastrowid = int(row["id"])
        return DbCursor(cursor, lastrowid=lastrowid)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


DbConnection = PostgresCompatConnection


def connect_postgres(settings: Settings) -> PostgresCompatConnection:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "Postgres is configured but psycopg is not installed. Run `uv sync` first."
        ) from exc

    conn = psycopg.connect(settings.postgres_url, row_factory=dict_row)
    wrapped = PostgresCompatConnection(conn)
    _apply_postgres_migrations(wrapped)
    return wrapped


def _wrap_pg_row(row: Any) -> DbRow | None:
    if row is None:
        return None
    if isinstance(row, DbRow):
        return row
    return DbRow(dict(row))


def _translate_sql_for_postgres(sql: str) -> str:
    return sql.replace("?", "%s")


def _maybe_add_lastrowid_returning(sql: str) -> tuple[str, bool]:
    normalized = " ".join(sql.strip().lower().split())
    needs_id = (
        normalized.startswith("insert into link_reviews ")
        or normalized.startswith("insert into audit_events ")
        or normalized.startswith("insert into processing_events ")
    )
    if needs_id and " returning " not in normalized:
        return f"{sql.rstrip()} RETURNING id", True
    return sql, False


def _apply_postgres_migrations(conn: PostgresCompatConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cerno_schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    row = conn.execute("SELECT max(version) AS version FROM cerno_schema_migrations").fetchone()
    current = int(row["version"] or 0) if row else 0
    if current == 0:
        for statement in _POSTGRES_SCHEMA:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO cerno_schema_migrations (version, applied_at) VALUES (%s, %s)",
            (1, datetime.now().isoformat()),
        )
        current = 1
    for version in sorted(_POSTGRES_MIGRATIONS):
        if version <= current:
            continue
        for statement in _POSTGRES_MIGRATIONS[version]:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO cerno_schema_migrations (version, applied_at) VALUES (%s, %s)",
            (version, datetime.now().isoformat()),
        )
    conn.commit()


_POSTGRES_MIGRATIONS = {
    2: [
        """
        CREATE TABLE IF NOT EXISTS upload_intents (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            original_filename TEXT NOT NULL,
            mime_type TEXT,
            expected_size_bytes INTEGER NOT NULL,
            observed_size_bytes INTEGER,
            storage_backend TEXT NOT NULL,
            object_key TEXT NOT NULL,
            status TEXT NOT NULL,
            source_asset_id TEXT REFERENCES source_assets(id) ON DELETE SET NULL,
            error_message TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_upload_intents_session ON upload_intents(session_id, created_at)",
        """
        CREATE TABLE IF NOT EXISTS processing_jobs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            locked_by TEXT,
            locked_until TEXT,
            heartbeat_at TEXT,
            checkpoint_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_processing_jobs_claim ON processing_jobs(status, locked_until, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_processing_jobs_session ON processing_jobs(session_id, kind, status)",
        "ALTER TABLE processing_events ADD COLUMN IF NOT EXISTS job_id TEXT",
        "ALTER TABLE processing_events ADD COLUMN IF NOT EXISTS step_key TEXT",
        "ALTER TABLE processing_events ADD COLUMN IF NOT EXISTS level TEXT",
        "ALTER TABLE processing_events ADD COLUMN IF NOT EXISTS progress INTEGER",
        "ALTER TABLE processing_events ADD COLUMN IF NOT EXISTS details TEXT NOT NULL DEFAULT '{}'",
    ],
    3: [
        "ALTER TABLE chat_turns ADD COLUMN IF NOT EXISTS title TEXT",
        "ALTER TABLE chat_turns ADD COLUMN IF NOT EXISTS metadata TEXT NOT NULL DEFAULT '{}'",
    ],
}


_POSTGRES_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        google_sub TEXT UNIQUE NOT NULL,
        email TEXT NOT NULL,
        name TEXT,
        picture TEXT,
        access_status TEXT NOT NULL DEFAULT 'pending',
        access_granted_at TEXT,
        access_code_used TEXT,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_users_access_status ON users(access_status)",
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new',
        discovery_status TEXT NOT NULL DEFAULT 'empty',
        overview TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
    """
    CREATE TABLE IF NOT EXISTS files (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        filename TEXT NOT NULL,
        parquet_path TEXT NOT NULL,
        raw_parquet_path TEXT,
        original_size_bytes INTEGER,
        row_count INTEGER NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1,
        header_row INTEGER,
        friendly_name TEXT,
        description TEXT,
        content_hash TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_files_session ON files(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_files_session_hash ON files(session_id, content_hash)",
    """
    CREATE TABLE IF NOT EXISTS schema_columns (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        schema_version INTEGER NOT NULL,
        name TEXT NOT NULL,
        dtype TEXT NOT NULL,
        inferred_kind TEXT NOT NULL,
        confidence DOUBLE PRECISION NOT NULL,
        position INTEGER NOT NULL,
        column_id TEXT,
        description TEXT,
        UNIQUE(file_id, schema_version, name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_schema_columns_file ON schema_columns(file_id, schema_version)",
    """
    CREATE TABLE IF NOT EXISTS links (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        file_a TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        col_a TEXT NOT NULL,
        file_b TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        col_b TEXT NOT NULL,
        overlap DOUBLE PRECISION NOT NULL,
        direction TEXT NOT NULL,
        score DOUBLE PRECISION NOT NULL,
        summary TEXT,
        source TEXT NOT NULL DEFAULT 'discovered',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_links_session ON links(session_id)",
    """
    CREATE TABLE IF NOT EXISTS link_reviews (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
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
        score_normalized DOUBLE PRECISION NOT NULL,
        score_raw DOUBLE PRECISION NOT NULL,
        source_code TEXT NOT NULL,
        review_status TEXT,
        reviewed_at TEXT,
        reviewed_by TEXT,
        notes TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_anomalies_session ON anomalies(session_id, score_normalized DESC)",
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
    "CREATE INDEX IF NOT EXISTS idx_pages_dashboard ON dashboard_pages(dashboard_id, position)",
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
    "CREATE INDEX IF NOT EXISTS idx_cells_page ON notebook_cells(page_id, order_index)",
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
    "CREATE INDEX IF NOT EXISTS idx_turns_session ON chat_turns(session_id, created_at)",
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
    "CREATE INDEX IF NOT EXISTS idx_messages_turn ON chat_messages(turn_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        session_id TEXT,
        kind TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events(session_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS processing_events (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_processing_events_session ON processing_events(session_id, id)",
    """
    CREATE TABLE IF NOT EXISTS data_docs (
        session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS beta_codes (
        code TEXT PRIMARY KEY,
        note TEXT,
        max_uses INTEGER NOT NULL DEFAULT 1,
        uses_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        expires_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_usage (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        tokens_used INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, day)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_assets (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        sha256 TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        mime_type TEXT,
        size_bytes INTEGER NOT NULL,
        storage_backend TEXT NOT NULL,
        object_key TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, sha256)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_source_assets_user ON source_assets(user_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS workspace_assets (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        source_asset_id TEXT NOT NULL REFERENCES source_assets(id) ON DELETE RESTRICT,
        display_name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(session_id, source_asset_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_workspace_assets_session ON workspace_assets(session_id)",
    """
    CREATE TABLE IF NOT EXISTS asset_artifacts (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
        source_asset_id TEXT REFERENCES source_assets(id) ON DELETE CASCADE,
        file_id TEXT REFERENCES files(id) ON DELETE CASCADE,
        artifact_type TEXT NOT NULL,
        storage_backend TEXT NOT NULL,
        object_key TEXT NOT NULL,
        content_hash TEXT,
        size_bytes INTEGER NOT NULL DEFAULT 0,
        mime_type TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_asset_artifacts_source ON asset_artifacts(source_asset_id)",
    "CREATE INDEX IF NOT EXISTS idx_asset_artifacts_file ON asset_artifacts(file_id)",
    """
    CREATE TABLE IF NOT EXISTS tables (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        workspace_asset_id TEXT REFERENCES workspace_assets(id) ON DELETE CASCADE,
        legacy_file_id TEXT UNIQUE REFERENCES files(id) ON DELETE CASCADE,
        sheet_name TEXT,
        table_index INTEGER NOT NULL DEFAULT 0,
        display_name TEXT NOT NULL,
        row_count INTEGER NOT NULL DEFAULT 0,
        current_schema_version INTEGER NOT NULL DEFAULT 1,
        processed_artifact_id TEXT REFERENCES asset_artifacts(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tables_session ON tables(session_id)",
    """
    CREATE TABLE IF NOT EXISTS chat_threads (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_threads_session ON chat_threads(session_id, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS chat_artifacts (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        turn_id TEXT REFERENCES chat_turns(id) ON DELETE CASCADE,
        message_id TEXT REFERENCES chat_messages(id) ON DELETE SET NULL,
        artifact_type TEXT NOT NULL,
        title TEXT NOT NULL,
        inline_payload TEXT,
        storage_backend TEXT,
        object_key TEXT,
        size_bytes INTEGER NOT NULL DEFAULT 0,
        mime_type TEXT,
        order_index INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_artifacts_turn ON chat_artifacts(turn_id, order_index)",
]


@contextmanager
def session_scope(settings: Settings) -> Iterator[PostgresCompatConnection]:
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
