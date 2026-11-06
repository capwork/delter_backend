from __future__ import annotations

from typing import Any

from common.app_factory import build_app
from common.browser import DriverFactory, make_driver_factory
from common.clients import ServiceRegistry
from common.contracts import PROBE_REQUESTED, RunConfig, Stage
from common.db import Database
from common.events import EventBus, create_bus
from common.stage import StageContext, StageResult, StageWorker
from configuration.settings import Settings, get_settings
from probe_mgt.controllers.probe_controller import router
from probe_mgt.repository.probe_repository import SCHEMA, ProbeRepository
from probe_mgt.services.prober import EvidenceClient, Prober

SERVICE = "probe_mgt"


def create_app(settings: Settings | None = None, bus: EventBus | None = None,
               registry: ServiceRegistry | None = None, driver_factory: DriverFactory | None = None):
    """App factory. Run with: uvicorn --factory probe_mgt.probe_mgt_main:create_app"""
    settings = settings or get_settings()
    own_bus = bus is None
    bus = bus or create_bus(settings)
    registry = registry or ServiceRegistry(settings)
    db = Database(settings.db_path(SERVICE), SCHEMA)
    repo = ProbeRepository(db)

    def candidates(run_id: str) -> list[dict]:
        return registry.paged("detect_mgt", f"/runs/{run_id}/candidates", page_size=1000)

    # "ignore": native dialogs are handled by the page hook and, as a backstop, by the prober itself
    prober = Prober(repo, driver_factory or make_driver_factory(settings, "ignore"), EvidenceClient(registry), candidates)

    def run(ctx: StageContext, payload: dict[str, Any]) -> StageResult:
        return prober.run(ctx, RunConfig(**payload["config"]))

    worker = StageWorker(
        stage=Stage.probe, service=SERVICE, bus=bus, db=db, run_fn=run,
        on_run_deleted=repo.delete_run, concurrency=settings.worker_concurrency,
    )
    app = build_app(SERVICE, settings, bus, on_startup=lambda: worker.start(PROBE_REQUESTED), own_bus=own_bus)
    app.state.repo = repo
    app.include_router(router)
    return app
