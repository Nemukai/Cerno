from __future__ import annotations

import sqlite3

from cerno.config import Settings
from cerno.db import SCHEMA_VERSION, _apply_migrations, connect, connect_memory


def test_connect_creates_schema(settings: Settings) -> None:
    conn = connect(settings)
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_version == SCHEMA_VERSION

        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for expected in [
            "sessions",
            "files",
            "schema_columns",
            "links",
            "link_reviews",
            "anomalies",
            "dashboards",
            "dashboard_pages",
            "notebook_cells",
            "chat_turns",
            "chat_messages",
            "audit_events",
        ]:
            assert expected in tables, f"missing table: {expected}"
    finally:
        conn.close()


def test_migrations_are_idempotent(settings: Settings) -> None:
    conn = connect(settings)
    try:
        _apply_migrations(conn)
        _apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_foreign_keys_enforced() -> None:
    conn = connect_memory()
    try:
        result = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert result == 1

        try:
            conn.execute(
                "INSERT INTO files (id, session_id, filename, parquet_path, row_count, schema_version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("f1", "missing-session", "foo.csv", "/tmp/foo", 10, 1, "2026-01-01T00:00:00"),
            )
            raise AssertionError("expected FK violation")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()
