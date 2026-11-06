from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from common.contracts import RunConfig


class CreateRunRequest(BaseModel):
    domain: str = Field(min_length=1, description="e.g. example.com or https://example.com/app")
    config: RunConfig = Field(default_factory=RunConfig)


class RunOut(BaseModel):
    run_id: str
    domain: str
    host: str
    start_url: str
    created_at: str
    updated_at: str
    status: str
    crawl_status: str
    detect_status: str
    probe_status: str
    active_stage: str | None = None
    auto_chain: bool
    config: RunConfig
    summary: dict[str, Any]


class RunList(BaseModel):
    total: int
    items: list[RunOut]


class EventOut(BaseModel):
    event_id: int
    run_id: str
    ts: str
    stage: str | None
    level: str
    message: str
    data: dict[str, Any] | None = None
