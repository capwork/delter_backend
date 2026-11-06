from __future__ import annotations

from typing import Any

from common.app_factory import build_app
from common.browser import DriverFactory, make_driver_factory
from common.contracts import CRAWL_REQUESTED, RunConfig, Stage
from common.db import Database
from common.events import EventBus, create_bus
from common.stage import StageContext, StageResult, StageWorker
from configuration.settings import Settings, get_settings
from crawl_mgt.controllers.url_controller import router
from crawl_mgt.repository.url_repository import SCHEMA, UrlRepository
from crawl_mgt.services.crawler import Crawler

SERVICE = "crawl_mgt"


def create_app(settings: Settings | None = None, bus: EventBus | None = None,
               driver_factory: DriverFactory | None = None):
    """App factory. Run with: uvicorn --factory crawl_mgt.crawl_mgt_main:create_app"""
    settings = settings or get_settings()
    own_bus = bus is None
    bus = bus or create_bus(settings)
    db = Database(settings.db_path(SERVICE), SCHEMA)
    repo = UrlRepository(db)
    crawler = Crawler(repo, driver_factory or make_driver_factory(settings, "dismiss"))

    def run(ctx: StageContext, payload: dict[str, Any]) -> StageResult:
        return crawler.run(ctx, payload["start_url"], RunConfig(**payload["config"]))

    worker = StageWorker(
        stage=Stage.crawl, service=SERVICE, bus=bus, db=db, run_fn=run,
        on_run_deleted=repo.delete_run, concurrency=settings.worker_concurrency,
    )
    app = build_app(SERVICE, settings, bus, on_startup=lambda: worker.start(CRAWL_REQUESTED), own_bus=own_bus)
    app.state.repo = repo
    app.include_router(router)
    return app
