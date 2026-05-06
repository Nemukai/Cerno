from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CernoConfigError(ValueError):
    """Raised when the user-editable Cerno config file is invalid."""


@dataclass(frozen=True)
class DiscoveryProcessingConfig:
    model: str = "gpt-5.4"
    reasoning_effort: str = "high"
    reasoning_summary: str = "auto"


@dataclass(frozen=True)
class ProcessingConfig:
    discovery: DiscoveryProcessingConfig = DiscoveryProcessingConfig()


_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
_REASONING_SUMMARIES = {"off", "none", "auto", "concise", "detailed"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CERNO_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    api_root_path: str = ""
    frontend_dev_url: str = "http://127.0.0.1:5173"
    frontend_origin: str = "http://127.0.0.1:5173"

    llm_enabled: bool = True
    llm_provider: str = "openai"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-5.5"
    llm_timeout_seconds: float = 300.0
    link_max_llm_candidates: int = 30
    llm_site_url: str = "http://localhost"
    llm_app_name: str = "Cerno"
    llm_reasoning_effort: str = "medium"
    llm_reasoning_summary: str = "auto"

    schema_confidence_threshold: float = 0.85

    link_overlap_threshold: float = 0.6
    link_sample_rows: int = 5
    link_max_avg_length: int = 100
    link_min_distinct: int = 3

    anomaly_mad_threshold: float = 3.5
    anomaly_rare_threshold: float = 0.01
    anomaly_key_overlap_min: float = 0.6
    anomaly_per_detector_limit: int = 100

    chat_max_llm_calls: int = 20

    google_client_id: str = ""
    google_client_secret: str = ""
    session_secret: str = "dev-only-change-me"
    session_cookie_name: str = "cerno_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 30

    operator_emails: str = ""

    per_user_quota_gb: int = 5
    daily_token_cap: int = 200_000

    postgres_url: str = ""
    log_level: str = "INFO"
    worker_id: str = ""
    worker_poll_interval_seconds: float = 2.0
    worker_lock_seconds: int = 1800
    upload_url_expires_seconds: int = 900

    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    r2_endpoint_url: str = ""

    def validate_runtime_safety(self) -> None:
        production_like = self.app_env.lower() in {"prod", "production"}
        public_auth = bool(self.google_client_id or self.google_client_secret)
        https_frontend = self.frontend_origin.startswith("https://")
        if self.session_secret == "dev-only-change-me" and (
            production_like or public_auth or https_frontend
        ):
            raise RuntimeError(
                "CERNO_SESSION_SECRET must be set to a strong secret outside local development"
            )

    def per_user_quota_bytes(self) -> int:
        return self.per_user_quota_gb * 1024 * 1024 * 1024

    def use_postgres(self) -> bool:
        return bool(self.postgres_url.strip())

    def use_r2(self) -> bool:
        return bool(
            self.r2_bucket_name.strip()
            and self.r2_access_key_id.strip()
            and self.r2_secret_access_key.strip()
            and (self.r2_endpoint_url.strip() or self.r2_account_id.strip())
        )

    def resolved_r2_endpoint_url(self) -> str:
        if self.r2_endpoint_url.strip():
            return self.r2_endpoint_url.strip()
        return f"https://{self.r2_account_id.strip()}.r2.cloudflarestorage.com"

    def operator_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.operator_emails.split(",") if e.strip()}

    data_root: Path = Field(default_factory=lambda: Path.home() / ".cerno")

    def users_dir(self) -> Path:
        return self.data_root / "users"

    def user_dir(self, user_id: str) -> Path:
        return self.users_dir() / user_id

    def session_dir(self, user_id: str, session_id: str) -> Path:
        return self.user_dir(user_id) / "sessions" / session_id

    def parquet_path(self, user_id: str, session_id: str, file_id: str) -> Path:
        return self.session_dir(user_id, session_id) / f"{file_id}.parquet"

    def raw_parquet_path(self, user_id: str, session_id: str, file_id: str) -> Path:
        return self.session_dir(user_id, session_id) / f"{file_id}.raw.parquet"

    def cache_dir(self) -> Path:
        return self.data_root / "cache"

    def config_path(self) -> Path:
        return self.data_root / "config.toml"

    @property
    def processing(self) -> ProcessingConfig:
        return load_processing_config(self.config_path())

    def object_cache_path(self, object_key: str) -> Path:
        return self.cache_dir() / object_key

    def db_path(self) -> Path:
        return self.data_root / "cerno.sqlite"


def load_processing_config(path: Path) -> ProcessingConfig:
    if not path.exists():
        return ProcessingConfig()

    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise CernoConfigError(f"Invalid Cerno config at {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise CernoConfigError(f"Invalid Cerno config at {path}: expected a TOML table")

    processing = _optional_table(raw, "processing", path)
    discovery = _optional_table(processing, "discovery", path)
    defaults = DiscoveryProcessingConfig()

    model = _string_value(discovery, "model", defaults.model, path)
    reasoning_effort = _string_value(
        discovery,
        "reasoning_effort",
        defaults.reasoning_effort,
        path,
    ).lower()
    reasoning_summary = _string_value(
        discovery,
        "reasoning_summary",
        defaults.reasoning_summary,
        path,
    ).lower()

    if reasoning_effort not in _REASONING_EFFORTS:
        allowed = ", ".join(sorted(_REASONING_EFFORTS))
        raise CernoConfigError(
            f"Invalid processing.discovery.reasoning_effort in {path}: "
            f"{reasoning_effort!r}. Expected one of: {allowed}."
        )
    if reasoning_summary not in _REASONING_SUMMARIES:
        allowed = ", ".join(sorted(_REASONING_SUMMARIES))
        raise CernoConfigError(
            f"Invalid processing.discovery.reasoning_summary in {path}: "
            f"{reasoning_summary!r}. Expected one of: {allowed}."
        )

    return ProcessingConfig(
        discovery=DiscoveryProcessingConfig(
            model=model,
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
        )
    )


def _optional_table(parent: dict[str, object], key: str, path: Path) -> dict[str, object]:
    value = parent.get(key, {})
    if not isinstance(value, dict):
        raise CernoConfigError(f"Invalid {key} section in {path}: expected a TOML table")
    return value


def _string_value(
    table: dict[str, object],
    key: str,
    default: str,
    path: Path,
) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise CernoConfigError(
            f"Invalid processing.discovery.{key} in {path}: expected a non-empty string"
        )
    return value.strip()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.data_root.mkdir(parents=True, exist_ok=True)
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
