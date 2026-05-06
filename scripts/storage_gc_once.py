from __future__ import annotations

import json

import boto3

from cerno.config import get_settings
from cerno.db import connect

LIVE_OBJECT_QUERIES = [
    (
        """
        SELECT DISTINCT sa.object_key
        FROM source_assets sa
        JOIN workspace_assets wa ON wa.source_asset_id = sa.id
        JOIN sessions s ON s.id = wa.session_id
        WHERE s.user_id = ? AND sa.storage_backend = ?
        """,
        2,
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
            (aa.session_id IS NOT NULL AND s.user_id = ?)
            OR (
              aa.session_id IS NULL
              AND aa.source_asset_id IS NOT NULL
              AND ws.user_id = ?
            )
          )
        """,
        4,
    ),
    (
        """
        SELECT DISTINCT ui.object_key
        FROM upload_intents ui
        JOIN sessions s ON s.id = ui.session_id
        WHERE ui.user_id = ? AND ui.storage_backend = ?
        """,
        2,
    ),
    (
        """
        SELECT DISTINCT ca.object_key
        FROM chat_artifacts ca
        JOIN sessions s ON s.id = ca.session_id
        WHERE s.user_id = ?
          AND ca.storage_backend = ?
          AND ca.object_key IS NOT NULL
        """,
        2,
    ),
]


def list_keys(client, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(str(item["Key"]) for item in page.get("Contents", []))
    return keys


def cleanup_user(conn, client, bucket: str, user_id: str, email: str) -> dict[str, object]:
    live_keys: set[str] = set()
    for sql, arity in LIVE_OBJECT_QUERIES:
        params = (user_id, "r2") if arity == 2 else (user_id, "r2", user_id, user_id)
        rows = conn.execute(sql, params).fetchall()
        live_keys.update(str(row["object_key"]) for row in rows if row["object_key"])

    stored_keys = list_keys(client, bucket, f"users/{user_id}/")
    deleted_keys: list[str] = []
    failed_keys: dict[str, str] = {}
    for object_key in stored_keys:
        if object_key in live_keys:
            continue
        try:
            client.delete_object(Bucket=bucket, Key=object_key)
        except Exception as exc:
            failed_keys[object_key] = str(exc)
        else:
            deleted_keys.append(object_key)

    stale_rows = conn.execute(
        """
        SELECT sa.id
        FROM source_assets sa
        WHERE sa.user_id = ?
          AND NOT EXISTS (
            SELECT 1
            FROM workspace_assets wa
            JOIN sessions s ON s.id = wa.session_id
            WHERE wa.source_asset_id = sa.id
              AND s.user_id = ?
          )
        """,
        (user_id, user_id),
    ).fetchall()
    pruned_source_asset_ids = [str(row["id"]) for row in stale_rows]
    for source_asset_id in pruned_source_asset_ids:
        conn.execute("DELETE FROM source_assets WHERE id = ?", (source_asset_id,))

    return {
        "email": email,
        "user_id": user_id,
        "scanned": len(stored_keys),
        "live": len(live_keys),
        "deleted": len(deleted_keys),
        "failed": len(failed_keys),
        "pruned_source_assets": len(pruned_source_asset_ids),
    }


def main() -> None:
    settings = get_settings()
    conn = connect(settings)
    client = boto3.client(
        "s3",
        endpoint_url=settings.resolved_r2_endpoint_url(),
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )
    try:
        rows = conn.execute("SELECT id, email FROM users ORDER BY email").fetchall()
        results = [
            cleanup_user(conn, client, settings.r2_bucket_name, str(row["id"]), str(row["email"]))
            for row in rows
        ]
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
