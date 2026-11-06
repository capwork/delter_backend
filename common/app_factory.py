"""Builds the FastAPI app every service shares: CORS, error handlers, /health, lifespan hooks."""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common.errors import install_error_handlers
from common.events import EventBus
from configuration.logging_setup import setup_logging
from configuration.settings import Settings

__version__ = "0.1.0"


def build_app(
    service: str,
    settings: Settings,
    bus: EventBus,
    *,
    on_startup: Callable[[], None] | None = None,
    on_shutdown: Callable[[], None] | None = None,
    health_extra: Callable[[], dict[str, Any]] | None = None,
    own_bus: bool = False,
) -> FastAPI:
    setup_logging(service, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if on_startup:
            on_startup()
        logging.getLogger(service).info("started")
        yield
        if on_shutdown:
            on_shutdown()
        if own_bus:
            bus.close()

    app = FastAPI(title=f"Delter {service}", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, Any]:
        out: dict[str, Any] = {"service": service, "status": "ok", "version": __version__,
                               "bus": "ok" if bus.ping() else "unreachable"}
        if health_extra:
            out.update(health_extra())
        return out

    return app
