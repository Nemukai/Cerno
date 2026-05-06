from __future__ import annotations

from cerno.config import Settings
from cerno.models import File
from cerno.repositories import AssetArtifactRepository
from cerno.storage import ObjectStore


def ensure_file_artifact_cached(
    *,
    file: File,
    artifact_type: str,
    settings: Settings,
    artifacts_repo: AssetArtifactRepository,
    object_store: ObjectStore,
) -> str | None:
    artifact = artifacts_repo.latest_for_file(file.id, artifact_type)
    if artifact is None or artifact.storage_backend != object_store.backend:
        return None
    destination = settings.object_cache_path(artifact.object_key)
    object_store.get_to_path(artifact.object_key, destination)
    return str(destination)
