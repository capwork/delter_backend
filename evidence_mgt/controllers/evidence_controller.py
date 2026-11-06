from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from pydantic import BaseModel

from common.errors import InvalidInput, NotFound

router = APIRouter(tags=["evidence"])
EXT = {"image/png": "png", "application/json": "json"}
MAX_UPLOAD = 25 * 1024 * 1024


class EvidenceOut(BaseModel):
    evidence_id: str
    run_id: str
    candidate_id: str
    kind: str
    file_path: str
    content_type: str
    size_bytes: int
    meta: dict[str, Any]
    created_at: str


class EvidenceList(BaseModel):
    total: int
    items: list[EvidenceOut]


@router.post("/runs/{run_id}/evidence", response_model=EvidenceOut, status_code=201)
async def upload_evidence(
    run_id: str,
    request: Request,
    candidate_id: Annotated[str, Form()],
    kind: Annotated[Literal["before", "after", "details"], Form()],
    file: Annotated[UploadFile, File()],
    meta: Annotated[str, Form()] = "{}",
):
    ctype = file.content_type or "application/octet-stream"
    if ctype not in EXT:
        raise InvalidInput(f"unsupported content type {ctype}")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise InvalidInput("file too large")
    try:
        meta_obj = json.loads(meta)
    except json.JSONDecodeError as exc:
        raise InvalidInput("meta must be JSON") from exc
    rel = f"{run_id}/{candidate_id}/{kind}.{EXT[ctype]}"
    request.app.state.storage.save(rel, data)
    return request.app.state.repo.upsert(run_id, candidate_id, kind, rel, ctype, len(data), meta_obj)


@router.get("/runs/{run_id}/evidence", response_model=EvidenceList)
def list_evidence(
    run_id: str,
    request: Request,
    candidate_id: str | None = None,
    kind: str | None = None,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    total, items = request.app.state.repo.list(run_id, candidate_id, kind, limit, offset)
    return {"total": total, "items": items}


@router.get("/evidence/{evidence_id}", response_model=EvidenceOut)
def get_evidence(evidence_id: str, request: Request):
    row = request.app.state.repo.get(evidence_id)
    if row is None:
        raise NotFound(f"evidence {evidence_id} not found")
    return row


@router.get("/evidence/{evidence_id}/file")
def get_evidence_file(evidence_id: str, request: Request) -> Response:
    row = request.app.state.repo.get(evidence_id)
    if row is None:
        raise NotFound(f"evidence {evidence_id} not found")
    data = request.app.state.storage.read(row["file_path"])
    return Response(content=data, media_type=row["content_type"], headers={"Cache-Control": "private, max-age=3600"})


# -- reports -----------------------------------------------------------------------------------
@router.get("/runs/{run_id}/report")
def report_json(run_id: str, request: Request) -> dict[str, Any]:
    """Everything about the run: summary, pages, delete controls, verdicts and evidence links."""
    return request.app.state.reports.build(run_id)


@router.get("/runs/{run_id}/report/csv")
def report_csv(run_id: str, request: Request, download: bool = True) -> Response:
    svc = request.app.state.reports
    headers = {"Content-Disposition": f'attachment; filename="delter-{run_id}.csv"'} if download else {}
    return Response(svc.to_csv(svc.build(run_id)), media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/runs/{run_id}/report/html")
def report_html(run_id: str, request: Request, embed_images: bool = True, download: bool = False) -> Response:
    """Self-contained page (screenshots embedded) that can be shared without the app."""
    svc = request.app.state.reports
    headers = {"Content-Disposition": f'attachment; filename="delter-{run_id}.html"'} if download else {}
    return Response(svc.to_html(svc.build(run_id), embed_images), media_type="text/html; charset=utf-8",
                    headers=headers)
