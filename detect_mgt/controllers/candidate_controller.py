from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from common.errors import NotFound

router = APIRouter(tags=["candidates"])


class CandidateOut(BaseModel):
    candidate_id: str
    run_id: str
    url_id: int
    url: str
    selector: str
    via: list[dict[str, Any]]
    tag: str | None
    text: str | None
    aria_label: str | None
    title: str | None
    href: str | None
    score: float
    reasons: list[dict[str, Any]]
    hints: list[str]
    html: str | None
    rect: dict[str, Any] | None
    detected_at: str


class CandidateList(BaseModel):
    total: int
    items: list[CandidateOut]


class ScanOut(BaseModel):
    run_id: str
    url_id: int
    url: str
    mode: str
    status: str
    candidates: int
    error: str | None
    scanned_at: str


class ScanList(BaseModel):
    total: int
    items: list[ScanOut]


@router.get("/runs/{run_id}/candidates", response_model=CandidateList)
def list_candidates(
    run_id: str,
    request: Request,
    url_id: int | None = None,
    min_score: float = Query(0.0, ge=0, le=1),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    total, items = request.app.state.repo.list(run_id, url_id, min_score, limit, offset)
    return {"total": total, "items": items}


@router.get("/candidates/{candidate_id}", response_model=CandidateOut)
def get_candidate(candidate_id: str, request: Request):
    row = request.app.state.repo.get(candidate_id)
    if row is None:
        raise NotFound(f"candidate {candidate_id} not found")
    return row


@router.get("/runs/{run_id}/scans", response_model=ScanList)
def list_scans(run_id: str, request: Request, limit: int = Query(100, ge=1, le=5000), offset: int = Query(0, ge=0)):
    """One row per scanned page, including pages where nothing was found."""
    total, items = request.app.state.repo.scans(run_id, limit, offset)
    return {"total": total, "items": items}
