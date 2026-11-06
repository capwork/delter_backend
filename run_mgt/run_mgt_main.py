from __future__ import annotations

from common.app_factory import build_app
from common.contracts import RUN_EVENT, STAGE_FINISHED
from common.db import Database
from common.events import EventBus, create_bus
from configuration.settings import Settings, get_settings
from run_mgt.controllers.run_controller import router
from run_mgt.repository.run_repository import SCHEMA, RunRepository
from run_mgt.services.run_service import RunService

SERVICE = "run_mgt"


def create_app(settings: Settings | None = None, bus: EventBus | None = None):
    """App factory. Run with: uvicorn --factory run_mgt.run_mgt_main:create_app"""
    settings = settings or get_settings()
    own_bus = bus is None
    bus = bus or create_bus(settings)
    repo = RunRepository(Database(settings.db_path(SERVICE), SCHEMA))
    service = RunService(repo, bus)

    def start() -> None:
        bus.subscribe(STAGE_FINISHED, SERVICE, service.on_stage_finished)
        bus.subscribe(RUN_EVENT, SERVICE, service.on_run_event)

    app = build_app(SERVICE, settings, bus, on_startup=start, own_bus=own_bus)
    app.state.run_service = service
    app.include_router(router)
    return app
