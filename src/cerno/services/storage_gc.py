from __future__ import annotations

import logging
from dataclasses import dataclass, field

from cerno.config import Settings
from cerno.db import DbConnection
from cerno.storage import get_object_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageCleanupResult:
    """Summarize unused object cleanup for a single user prefix."""

    scanned_keys: int
    deleted_keys: list[str] = field(default_factory=list)
    failed_keys: dict[str, str] = field(default_factory=dict)
    pruned_source_asset_ids: list[str] = field(default_factory=list)


def cleanup_unused_storage_for_user(
    conn: DbConnection,
    settings: Settings,
    user_id: str,
) -> StorageCleanupResult:
    """Delete R2 objects that are no longer referenced by live sessions.

    Args:
        conn: Open database connection.
        settings: Application settings used to resolve the object store.
        user_id: User whose storage prefix should be checked.

    Returns:
        A summary of scanned, deleted, failed, and pruned records.
    """
    object_store = get_object_store(settings)
    prefix = f"users/{user_id}/"
    live_keys = _live_object_keys(conn, user_id, object_store.backend)
    stored_keys = object_store.list_keys(prefix)
    deleted_keys: list[str] = []
    failed_keys: dict[str, str] = {}

    for object_key in stored_keys:
        if object_key in live_keys:
            continue
        try:
            object_store.delete(object_key)
        except Exception as exc:  # pragma: no cover - backend specific
            failed_keys[object_key] = str(exc)
            logger.warning(
                "event=storage_gc.delete_failed user_id=%s object_key=%s error=%s",
                user_id,
                object_key,
                exc,
            )
            continue
        deleted_keys.append(object_key)

    pruned_source_asset_ids = _prune_unreferenced_source_assets(conn, user_id)
    return StorageCleanupResult(
        scanned_keys=len(stored_keys),
        deleted_keys=deleted_keys,
        failed_keys=failed_keys,
        pruned_source_asset_ids=pruned_source_asset_ids,
    )


def _live_object_keys(
    conn: DbConnection,
    user_id: str,
    storage_backend: str,
) -> set[str]:
    """Return object keys still reachable from live user sessions."""
    keys: set[str] = set()
    for sql, params in _live_object_key_queries(user_id, storage_backend):
        rows = conn.execute(sql, params).fetchall()
        keys.update(str(row["object_key"]) for row in rows if row["object_key"])
    return keys


def _live_object_key_queries(
    user_id: str,
    storage_backend: str,
) -> list[tuple[str, tuple[str, ...]]]:
    """Build queries for each table that may reference object storage."""
    return [
        (
            """
            SELECT DISTINCT sa.object_key
            FROM source_assets sa
            JOIN workspace_assets wa ON wa.source_asset_id = sa.id
            JOIN sessions s ON s.id = wa.session_id
            WHERE COALESCE(s.created_by_user_id, s.user_id) = ?
              AND sa.storage_backend = ?
            """,
            (user_id, storage_backend),
        ),
        (
            """
            SELECT DISTINCT aa.object_key
            FROM asset_artifacts aa
            LEFT JOIN sessions s ON s.id = aa.session_id
            LEFT JOIN workspace_assets wa ON wa.source_asset_id = aa.source_asset_id
            LEFT JOIN sessions ws ON ws.id = wa.session_id
            WHERE aa.user_id = ?
              AND aa.storage_backend = ?
              AND (
                (
                  aa.session_id IS NOT NULL
                  AND COALESCE(s.created_by_user_id, s.user_id) = ?
                )
                OR (
                  aa.session_id IS NULL
                  AND aa.source_asset_id IS NOT NULL
                  AND COALESCE(ws.created_by_user_id, ws.user_id) = ?
                )
              )
            """,
            (user_id, storage_backend, user_id, user_id),
        ),
        (
            """
            SELECT DISTINCT ui.object_key
            FROM upload_intents ui
            JOIN sessions s ON s.id = ui.session_id
            WHERE ui.user_id = ? AND ui.storage_backend = ?
            """,
            (user_id, storage_backend),
        ),
        (
            """
            SELECT DISTINCT ca.object_key
            FROM chat_artifacts ca
            JOIN sessions s ON s.id = ca.session_id
            WHERE COALESCE(s.created_by_user_id, s.user_id) = ?
              AND ca.storage_backend = ?
              AND ca.object_key IS NOT NULL
            """,
            (user_id, storage_backend),
        ),
        (
            """
            SELECT DISTINCT dp.image_object_key AS object_key
            FROM document_pages dp
            JOIN documents d ON d.id = dp.document_id
            JOIN sessions s ON s.id = d.session_id
            WHERE COALESCE(s.created_by_user_id, s.user_id) = ?
              AND dp.image_object_key IS NOT NULL
            """,
            (user_id,),
        ),
    ]


def _prune_unreferenced_source_assets(
    conn: DbConnection,
    user_id: str,
) -> list[str]:
    """Delete source asset DB rows that no live workspace references."""
    rows = conn.execute(
        """
        SELECT sa.id
        FROM source_assets sa
        WHERE sa.user_id = ?
          AND NOT EXISTS (
            SELECT 1
            FROM workspace_assets wa
            JOIN sessions s ON s.id = wa.session_id
            WHERE wa.source_asset_id = sa.id
              AND COALESCE(s.created_by_user_id, s.user_id) = ?
          )
        """,
        (user_id, user_id),
    ).fetchall()
    source_asset_ids = [str(row["id"]) for row in rows]
    for source_asset_id in source_asset_ids:
        conn.execute("DELETE FROM source_assets WHERE id = ?", (source_asset_id,))
    return source_asset_ids
