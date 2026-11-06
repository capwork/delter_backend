from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from common.errors import NotFound

router = APIRouter(tags=["probes"])


class ProbeOut(BaseModel):
    probe_id: str
    run_id: str
    candidate_id: str
    url: str
    selector: str
    status: str
    verdict: str | None
    reason: str | None
    dialogs: list[dict[str, Any]]
    modal: dict[str, Any] | None
    blocked: list[dict[str, Any]]
    final_url: str | None
    evidence_ids: list[str]
    error: str | None
    attempts: int
    duration_ms: int | None
    probed_at: str


class ProbeList(BaseModel):
    total: int
    items: list[ProbeOut]


@router.get("/runs/{run_id}/probes", response_model=ProbeList)
def list_probes(
    run_id: str,
    request: Request,
    verdict: str | None = Query(None, description="native_dialog|custom_modal|no_confirmation|inconclusive"),
    status: str | None = Query(None, description="completed|failed"),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    total, items = request.app.state.repo.list(run_id, verdict, status, limit, offset)
    return {"total": total, "items": items}


@router.get("/runs/{run_id}/probes/{candidate_id}", response_model=ProbeOut)
def get_probe(run_id: str, candidate_id: str, request: Request):
    row = request.app.state.repo.get(run_id, candidate_id)
    if row is None:
        raise NotFound(f"no probe for candidate {candidate_id}")
    return row


@router.get("/runs/{run_id}/probe-stats")
def probe_stats(run_id: str, request: Request) -> dict[str, int]:
    return request.app.state.repo.counts(run_id)
