from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CERNO_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_host: str = "127.0.0.1"
    api_port: int = 8765
    frontend_dev_url: str = "http://127.0.0.1:5173"
    frontend_origin: str = "http://127.0.0.1:5173"

    llm_enabled: bool = True
    llm_provider: str = "openai"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-5.4"
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

    chat_max_llm_calls: int = 8

    google_client_id: str = ""
    google_client_secret: str = ""
    session_secret: str = "dev-only-change-me"
    session_cookie_name: str = "cerno_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 30

    operator_emails: str = ""

    per_user_quota_gb: int = 5
    daily_token_cap: int = 200_000

    def operator_email_set(self) -> set[str]:
        return {
            e.strip().lower()
            for e in self.operator_emails.split(",")
            if e.strip()
        }

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

    def db_path(self) -> Path:
        return self.data_root / "cerno.sqlite"


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
