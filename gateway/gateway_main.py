"""API gateway: the single URL the React frontend talks to. Routes by path to the owning service."""
from __future__ import annotations

import asyncio
import logging
import re

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from common.app_factory import __version__
from common.errors import install_error_handlers
from configuration.logging_setup import setup_logging
from configuration.settings import SERVICE_PORTS, Settings, get_settings

SERVICE = "gateway"
ROUTES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/runs/[^/]+/urls(/|$)"), "crawl_mgt"),
    (re.compile(r"^/runs/[^/]+/(candidates|scans)(/|$)"), "detect_mgt"),
    (re.compile(r"^/candidates/"), "detect_mgt"),
    (re.compile(r"^/runs/[^/]+/(probes|probe-stats)(/|$)"), "probe_mgt"),
    (re.compile(r"^/runs/[^/]+/(evidence|report)(/|$)"), "evidence_mgt"),
    (re.compile(r"^/evidence/"), "evidence_mgt"),
    (re.compile(r"^/runs(/|$)"), "run_mgt"),
]
HOP_BY_HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer"}
log = logging.getLogger(SERVICE)


def resolve(path: str) -> str | None:
    return next((svc for rx, svc in ROUTES if rx.search(path)), None)


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(SERVICE, settings)
    client = httpx.AsyncClient(transport=transport, timeout=settings.upstream_timeout_s)
    app = FastAPI(title="Delter gateway", version=__version__)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        names = [n for n in SERVICE_PORTS if n != SERVICE]

        async def probe(name: str) -> tuple[str, str]:
            try:
                r = await client.get(f"{settings.service_url(name)}/health", timeout=3)
                return name, "ok" if r.status_code == 200 else f"http {r.status_code}"
            except httpx.HTTPError as exc:
                return name, f"unreachable: {type(exc).__name__}"

        services = dict(await asyncio.gather(*(probe(n) for n in names)))
        ok = all(v == "ok" for v in services.values())
        return {"service": SERVICE, "status": "ok" if ok else "degraded", "version": __version__,
                "services": services}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def proxy(path: str, request: Request) -> Response:
        full = "/" + path
        target = resolve(full)
        if target is None:
            return Response('{"detail":"unknown route","type":"NotFound"}', 404, media_type="application/json")
        url = settings.service_url(target) + full
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        try:
            upstream = await client.request(request.method, url, params=request.query_params,
                                            content=await request.body(), headers=headers)
        except httpx.HTTPError as exc:
            log.warning("upstream %s unreachable: %s", target, exc)
            return Response(f'{{"detail":"{target} is unreachable","type":"UpstreamError"}}', 502,
                            media_type="application/json")
        out = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP | {"content-encoding"}}
        return Response(upstream.content, upstream.status_code, headers=out)

    @app.on_event("shutdown")
    async def _close() -> None:
        await client.aclose()

    return app
