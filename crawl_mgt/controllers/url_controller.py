from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from common.errors import NotFound

router = APIRouter(tags=["urls"])


class UrlOut(BaseModel):
    url_id: int
    run_id: str
    url: str
    final_url: str | None
    parent_url_id: int | None
    depth: int
    status: str
    http_status: int | None
    error: str | None
    title: str | None


class UrlList(BaseModel):
    total: int
    items: list[UrlOut]


@router.get("/runs/{run_id}/urls", response_model=UrlList)
def list_urls(
    run_id: str,
    request: Request,
    status: str | None = Query(None, description="discovered|visited|failed|skipped"),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    total, items = request.app.state.repo.list(run_id, status, limit, offset)
    return {"total": total, "items": items}


@router.get("/runs/{run_id}/urls/{url_id}", response_model=UrlOut)
def get_url(run_id: str, url_id: int, request: Request):
    row = request.app.state.repo.get(run_id, url_id)
    if row is None:
        raise NotFound(f"url {url_id} not found")
    return row
