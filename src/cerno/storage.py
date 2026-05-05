from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from cerno.config import Settings


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredObject:
    backend: str
    object_key: str
    size_bytes: int


class ObjectStore:
    backend = "local"

    def put_path(self, source: Path, object_key: str, *, content_type: str | None = None) -> StoredObject:
        raise NotImplementedError

    def get_to_path(self, object_key: str, destination: Path) -> Path:
        raise NotImplementedError

    def delete(self, object_key: str) -> None:
        raise NotImplementedError


class LocalObjectStore(ObjectStore):
    backend = "local"

    def __init__(self, settings: Settings) -> None:
        self.root = settings.data_root / "objects"

    def put_path(self, source: Path, object_key: str, *, content_type: str | None = None) -> StoredObject:
        del content_type
        destination = self.root / object_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return StoredObject(
            backend=self.backend,
            object_key=object_key,
            size_bytes=destination.stat().st_size,
        )

    def get_to_path(self, object_key: str, destination: Path) -> Path:
        source = self.root / object_key
        if not source.exists():
            raise StorageError(f"object not found: {object_key}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def delete(self, object_key: str) -> None:
        (self.root / object_key).unlink(missing_ok=True)


class R2ObjectStore(ObjectStore):
    backend = "r2"

    def __init__(self, settings: Settings) -> None:
        try:
            import boto3
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
        )

    def get_to_path(self, object_key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, object_key, str(destination))
        return destination

    def delete(self, object_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)


def get_object_store(settings: Settings) -> ObjectStore:
    if settings.use_r2():
        return R2ObjectStore(settings)
    return LocalObjectStore(settings)


def source_object_key(user_id: str, asset_id: str, sha256: str, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    name = f"{sha256}{suffix}" if suffix else sha256
    return f"users/{user_id}/assets/{asset_id}/source/{name}"


def raw_artifact_key(user_id: str, asset_id: str, file_id: str) -> str:
    return f"users/{user_id}/assets/{asset_id}/extractions/{file_id}.raw.parquet"


def processed_artifact_key(user_id: str, session_id: str, file_id: str, schema_version: int) -> str:
    return f"users/{user_id}/sessions/{session_id}/tables/{file_id}/processed-v{schema_version}.parquet"
