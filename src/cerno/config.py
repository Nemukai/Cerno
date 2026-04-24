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

    llm_enabled: bool = False
    llm_provider: str = "openrouter"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_model: str = "z-ai/glm-4.7-flash"
    llm_timeout_seconds: float = 45.0
    llm_site_url: str = "http://localhost"
    llm_app_name: str = "Cerno"

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

    data_root: Path = Field(default_factory=lambda: Path.home() / ".cerno")

    def session_dir(self, session_id: str) -> Path:
        return self.data_root / "sessions" / session_id

    def parquet_path(self, session_id: str, file_id: str) -> Path:
        return self.session_dir(session_id) / f"{file_id}.parquet"

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
