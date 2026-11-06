from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request, Response

from common.contracts import Stage
from run_mgt.models.schemas import CreateRunRequest, EventOut, RunList, RunOut
from run_mgt.services.run_service import RunService

router = APIRouter(tags=["runs"])


def svc(request: Request) -> RunService:
    return request.app.state.run_service


@router.post("/runs", response_model=RunOut, status_code=201)
def create_run(body: CreateRunRequest, request: Request):
    return svc(request).create(body.domain, body.config)


@router.get("/runs", response_model=RunList)
def list_runs(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    host: str | None = None,
    sort: Literal["created_desc", "created_asc", "domain", "flagged"] = "created_desc",
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return svc(request).list(q=q, status=status, host=host, sort=sort, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, request: Request):
    return svc(request).get(run_id)


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: str, request: Request) -> Response:
    svc(request).delete(run_id)
    return Response(status_code=204)


@router.post("/runs/{run_id}/start", response_model=RunOut, status_code=202)
def start_pipeline(run_id: str, request: Request):
    """Whole pipeline: crawl -> detect -> probe."""
    return svc(request).start_pipeline(run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunOut, status_code=202)
def cancel_run(run_id: str, request: Request):
    return svc(request).cancel(run_id)


@router.post("/runs/{run_id}/{stage}", response_model=RunOut, status_code=202)
def start_stage(run_id: str, stage: Stage, request: Request):
    """Run one stage on its own (crawl, detect or probe)."""
    return svc(request).start_stage(run_id, stage)


@router.get("/runs/{run_id}/events", response_model=list[EventOut])
def run_events(
    run_id: str,
    request: Request,
    after_id: int = 0,
    level: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
):
    return svc(request).events(run_id, after_id, level, limit)
