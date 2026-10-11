"""Application settings, loaded once from environment variables (and an optional .env file).

Every component (web app, worker, CLI) reads configuration through `get_settings()` so there is
exactly one place that knows about environment variable names and defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VERIMIND_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # SQLAlchemy URL. The default matches the `db` service in docker-compose.yml.
    database_url: str = "postgresql+psycopg://verimind:verimind@localhost:5432/verimind"

    # Seconds to wait for a database connection before showing "database unavailable" (instead of hanging).
    db_connect_timeout: int = 5

    # Where uploaded log files are stored on disk.
    storage_dir: Path = Path("storage")

    # Optional folder of extra log-format definitions (*.yaml), for team-specific log styles.
    formats_dir: Path | None = None

    # Extra origins allowed to send data-changing requests, e.g. a reverse proxy's public URL
    # ("https://verimind.example.com"). VeriMind's own address is always allowed.
    allowed_origins: list[str] = []

    # Upload limits protect the server from accidental (or malicious) huge requests.
    max_upload_mb: int = 2048

    # Seconds the worker sleeps between polls for new jobs.
    worker_poll_interval: float = 2.0

    # A running job with no heartbeat for this long is assumed dead and handed to another worker.
    worker_stale_seconds: int = 600

    # Optional Claude integration. Read without the VERIMIND_ prefix to match Anthropic's convention.
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    @property
    def ai_summaries_enabled(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
