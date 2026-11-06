from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


class DelterError(Exception):
    status_code = 400


class NotFound(DelterError):
    status_code = 404


class Conflict(DelterError):
    status_code = 409


class InvalidInput(DelterError):
    status_code = 422


class UpstreamError(DelterError):
    """Another service is unreachable or answered with an error."""

    status_code = 502


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DelterError)
    async def _delter(_: Request, exc: DelterError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": str(exc), "type": type(exc).__name__}
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(
            status_code=500, content={"detail": f"{type(exc).__name__}: {exc}", "type": "InternalError"}
        )
