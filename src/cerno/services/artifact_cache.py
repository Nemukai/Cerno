from __future__ import annotations

from pathlib import Path

from cerno.config import Settings
from cerno.models import File
from cerno.repositories import AssetArtifactRepository
from cerno.storage import ObjectStore


def ensure_file_artifact_cached(
    *,
    file: File,
    artifact_type: str,
    local_path: str | None,
    settings: Settings,
    artifacts_repo: AssetArtifactRepository,
    object_store: ObjectStore,
) -> str | None:
    if local_path and Path(local_path).exists():
        return local_path
    artifact = artifacts_repo.latest_for_file(file.id, artifact_type)
    if artifact is None:
        return local_path
    destination = settings.object_cache_path(artifact.object_key)
    object_store.get_to_path(artifact.object_key, destination)
    return str(destination)
