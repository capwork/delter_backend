"""Environment-driven settings shared by every service (prefix DELTER_, or a .env file)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_PORTS: dict[str, int] = {
    "gateway": 8000,
    "run_mgt": 8001,
    "crawl_mgt": 8002,
    "detect_mgt": 8003,
    "probe_mgt": 8004,
    "evidence_mgt": 8005,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DELTER_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./delter-data")
    log_level: str = "INFO"
    bind_host: str = "127.0.0.1"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    # --- messaging -------------------------------------------------------------------
    event_bus: Literal["redis", "memory"] = "redis"
    redis_url: str = "redis://localhost:6379/0"
    stream_maxlen: int = 10_000
    # consumer threads per subscription = how many stage jobs one instance runs in parallel
    worker_concurrency: int = 1

    # --- service discovery (override per environment, e.g. docker-compose service names) ---
    run_mgt_url: str = f"http://127.0.0.1:{SERVICE_PORTS['run_mgt']}"
    crawl_mgt_url: str = f"http://127.0.0.1:{SERVICE_PORTS['crawl_mgt']}"
    detect_mgt_url: str = f"http://127.0.0.1:{SERVICE_PORTS['detect_mgt']}"
    probe_mgt_url: str = f"http://127.0.0.1:{SERVICE_PORTS['probe_mgt']}"
    evidence_mgt_url: str = f"http://127.0.0.1:{SERVICE_PORTS['evidence_mgt']}"
    upstream_timeout_s: float = 60.0

    # --- browser -----------------------------------------------------------------------
    chrome_binary: str | None = None  # None = let Selenium Manager find Chrome

    def service_url(self, name: str) -> str:
        return getattr(self, f"{name}_url")

    def db_path(self, service: str) -> Path:
        return self.data_dir / "database" / f"{service}.sqlite3"


@lru_cache
def get_settings() -> Settings:
    return Settings()
