from __future__ import annotations

from typing import Any

from common.app_factory import build_app
from common.browser import DriverFactory, make_driver_factory
from common.clients import ServiceRegistry
from common.contracts import DETECT_REQUESTED, RunConfig, Stage
from common.db import Database
from common.events import EventBus, create_bus
from common.stage import StageContext, StageResult, StageWorker
from configuration.settings import Settings, get_settings
from detect_mgt.controllers.candidate_controller import router
from detect_mgt.repository.candidate_repository import SCHEMA, CandidateRepository
from detect_mgt.services.detector import Detector

SERVICE = "detect_mgt"


def create_app(settings: Settings | None = None, bus: EventBus | None = None,
               registry: ServiceRegistry | None = None, driver_factory: DriverFactory | None = None):
    """App factory. Run with: uvicorn --factory detect_mgt.detect_mgt_main:create_app"""
    settings = settings or get_settings()
    own_bus = bus is None
    bus = bus or create_bus(settings)
    registry = registry or ServiceRegistry(settings)
    db = Database(settings.db_path(SERVICE), SCHEMA)
    repo = CandidateRepository(db)

    def visited_urls(run_id: str) -> list[dict]:
        return registry.paged("crawl_mgt", f"/runs/{run_id}/urls", page_size=1000, status="visited")

    detector = Detector(repo, driver_factory or make_driver_factory(settings, "dismiss"), visited_urls)

    def run(ctx: StageContext, payload: dict[str, Any]) -> StageResult:
        return detector.run(ctx, RunConfig(**payload["config"]))

    worker = StageWorker(
        stage=Stage.detect, service=SERVICE, bus=bus, db=db, run_fn=run,
        on_run_deleted=repo.delete_run, concurrency=settings.worker_concurrency,
    )
    app = build_app(SERVICE, settings, bus, on_startup=lambda: worker.start(DETECT_REQUESTED), own_bus=own_bus)
    app.state.repo = repo
    app.include_router(router)
    return app
