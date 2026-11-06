"""Contracts shared between services: enums, run configuration and event payloads.

Services never import each other's code; they only agree on these shapes.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Stage(StrEnum):
    crawl = "crawl"
    detect = "detect"
    probe = "probe"


STAGE_ORDER = [Stage.crawl, Stage.detect, Stage.probe]


class Status(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


class Verdict(StrEnum):
    native_dialog = "native_dialog"        # browser's own confirm()/alert()  -> the violation
    custom_modal = "custom_modal"          # platform-themed confirmation
    no_confirmation = "no_confirmation"    # destructive action fired with no confirmation at all
    inconclusive = "inconclusive"          # could not tell (not clickable, no visible effect, ...)


# ---- event topics -----------------------------------------------------------------------
CRAWL_REQUESTED = "crawl.requested"
DETECT_REQUESTED = "detect.requested"
PROBE_REQUESTED = "probe.requested"
STAGE_FINISHED = "stage.finished"
RUN_EVENT = "run.event"          # diagnostic log lines from any service -> run_mgt
RUN_CANCEL = "run.cancel"        # broadcast
RUN_DELETED = "run.deleted"      # every service drops its data for the run

REQUEST_TOPIC = {
    Stage.crawl: CRAWL_REQUESTED,
    Stage.detect: DETECT_REQUESTED,
    Stage.probe: PROBE_REQUESTED,
}

# Words that mark an action as destructive. Strong = near-certain, weak = ambiguous ("clear").
DEFAULT_STRONG_KEYWORDS = ["delete", "remove", "discard", "destroy", "erase", "trash", "purge"]
DEFAULT_WEAK_KEYWORDS = ["clear", "deactivate", "wipe", "terminate", "revoke", "unlink"]


class RunConfig(BaseModel):
    """Stored with the run so results are reproducible."""

    # crawl
    max_pages: int | None = Field(50, ge=1, description="None = unlimited")
    max_depth: int | None = Field(3, ge=0, description="None = unlimited")
    allow_subdomains: bool = False
    include_query_params: bool = True
    path_prefix: str | None = None
    exclude_patterns: list[str] = Field(default_factory=list)
    request_delay_s: float = Field(0.3, ge=0, le=30)
    # detect
    detection_mode: Literal["standard", "aggressive"] = "standard"
    min_score: float = Field(0.5, ge=0.1, le=1.0)
    max_candidates_per_page: int = Field(60, ge=1, le=500)
    extra_strong_keywords: list[str] = Field(default_factory=list)
    extra_weak_keywords: list[str] = Field(default_factory=list)
    # probe
    observe_ms: int = Field(900, ge=100, le=10_000)
    probe_max_retries: int = Field(1, ge=0, le=5)
    # browser
    viewport_width: int = Field(1366, ge=320, le=3840)
    viewport_height: int = Field(768, ge=240, le=2160)
    headless: bool = True
    page_load_timeout_s: int = Field(30, ge=5, le=300)
    settle_ms: int = Field(400, ge=0, le=10_000)

    @field_validator("exclude_patterns")
    @classmethod
    def _valid_regex(cls, v: list[str]) -> list[str]:
        for p in v:
            try:
                re.compile(p)
            except re.error as exc:
                raise ValueError(f"invalid regex {p!r}: {exc}") from exc
        return v

    @property
    def strong_keywords(self) -> list[str]:
        return sorted({*DEFAULT_STRONG_KEYWORDS, *(k.lower() for k in self.extra_strong_keywords)})

    @property
    def weak_keywords(self) -> list[str]:
        return sorted({*DEFAULT_WEAK_KEYWORDS, *(k.lower() for k in self.extra_weak_keywords)})


# ---- event payloads ---------------------------------------------------------------------
class StageRequest(BaseModel):
    run_id: str
    start_url: str
    config: RunConfig


class StageFinished(BaseModel):
    run_id: str
    stage: Stage
    status: Status
    reason: str = ""
    stats: dict[str, Any] = Field(default_factory=dict)


class RunEvent(BaseModel):
    run_id: str
    stage: str | None = None
    level: str = "INFO"
    message: str
    data: dict[str, Any] | None = None
