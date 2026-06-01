from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from cerno.config import Settings


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredObject:
    backend: str
    object_key: str
    size_bytes: int
    content_type: str | None = None


class ObjectStore:
    backend = "r2"

    def put_path(self, source: Path, object_key: str, *, content_type: str | None = None) -> StoredObject:
        raise NotImplementedError

    def get_to_path(self, object_key: str, destination: Path) -> Path:
        raise NotImplementedError

    def delete(self, object_key: str) -> None:
        raise NotImplementedError

    def list_keys(self, prefix: str) -> list[str]:
        """List object keys under the provided storage prefix."""
        raise NotImplementedError

    def head(self, object_key: str) -> StoredObject:
        raise NotImplementedError

    def presigned_put_url(
        self, object_key: str, *, content_type: str, expires_seconds: int
    ) -> str:
        raise NotImplementedError

    def presigned_get_url(self, object_key: str, *, expires_seconds: int) -> str:
        raise NotImplementedError


class R2ObjectStore(ObjectStore):
    backend = "r2"

    def __init__(self, settings: Settings) -> None:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError("R2 is configured but boto3 is not installed. Run `uv sync` first.") from exc

        self.bucket = settings.r2_bucket_name
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.resolved_r2_endpoint_url(),
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )

    def put_path(self, source: Path, object_key: str, *, content_type: str | None = None) -> StoredObject:
        extra_args = {"ContentType": content_type} if content_type else None
        if extra_args:
            self.client.upload_file(str(source), self.bucket, object_key, ExtraArgs=extra_args)
        else:
            self.client.upload_file(str(source), self.bucket, object_key)
        return StoredObject(
            backend=self.backend,
            object_key=object_key,
            size_bytes=source.stat().st_size,
            content_type=content_type,
        )

    def get_to_path(self, object_key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, object_key, str(destination))
        return destination

    def delete(self, object_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)

    def list_keys(self, prefix: str) -> list[str]:
        """List R2 object keys under the provided storage prefix."""
        paginator = self.client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(str(item["Key"]) for item in page.get("Contents", []))
        return keys

    def head(self, object_key: str) -> StoredObject:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=object_key)
        except Exception as exc:
            raise StorageError(f"object not found: {object_key}") from exc
        return StoredObject(
            backend=self.backend,
            object_key=object_key,
            size_bytes=int(response.get("ContentLength") or 0),
            content_type=response.get("ContentType"),
        )

    def presigned_put_url(
        self, object_key: str, *, content_type: str, expires_seconds: int
    ) -> str:
        return cast(
            str,
            self.client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": object_key,
                    "ContentType": content_type,
                },
                ExpiresIn=expires_seconds,
            ),
        )

    def presigned_get_url(self, object_key: str, *, expires_seconds: int) -> str:
        return cast(
            str,
            self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": object_key},
                ExpiresIn=expires_seconds,
            ),
        )


def get_object_store(settings: Settings) -> ObjectStore:
    if settings.use_r2():
        return R2ObjectStore(settings)
    raise RuntimeError("CERNO_R2_* settings must be set")


def source_object_key(user_id: str, asset_id: str, sha256: str, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    name = f"{sha256}{suffix}" if suffix else sha256
    return f"users/{user_id}/assets/{asset_id}/source/{name}"


def staging_upload_key(user_id: str, session_id: str, intent_id: str, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    name = f"{intent_id}{suffix}" if suffix else intent_id
    return f"users/{user_id}/sessions/{session_id}/uploads/{name}"


def raw_artifact_key(user_id: str, asset_id: str, file_id: str) -> str:
    return f"users/{user_id}/assets/{asset_id}/extractions/{file_id}.raw.parquet"


def processed_artifact_key(user_id: str, session_id: str, file_id: str, schema_version: int) -> str:
    return f"users/{user_id}/sessions/{session_id}/tables/{file_id}/processed-v{schema_version}.parquet"


def document_page_image_key(
    user_id: str,
    session_id: str,
    document_id: str,
    page_number: int,
    *,
    retry: bool = False,
) -> str:
    suffix = "retry" if retry else "initial"
    return (
        f"users/{user_id}/sessions/{session_id}/documents/{document_id}/"
        f"pages/{page_number:04d}-{suffix}.png"
    )
