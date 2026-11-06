from __future__ import annotations

from typing import Any

from common.app_factory import build_app
from common.clients import ServiceRegistry
from common.contracts import RUN_DELETED
from common.db import Database
from common.events import EventBus, create_bus
from common.storage import LocalStorage, Storage
from configuration.settings import Settings, get_settings
from evidence_mgt.controllers.evidence_controller import router
from evidence_mgt.repository.evidence_repository import SCHEMA, EvidenceRepository
from evidence_mgt.services.report_service import ReportService

SERVICE = "evidence_mgt"


def create_app(settings: Settings | None = None, bus: EventBus | None = None,
               registry: ServiceRegistry | None = None, storage: Storage | None = None):
    """App factory. Run with: uvicorn --factory evidence_mgt.evidence_mgt_main:create_app"""
    settings = settings or get_settings()
    own_bus = bus is None
    bus = bus or create_bus(settings)
    registry = registry or ServiceRegistry(settings)
    storage = storage or LocalStorage(settings.data_dir / "evidence")
    repo = EvidenceRepository(Database(settings.db_path(SERVICE), SCHEMA))

    def on_deleted(payload: dict[str, Any]) -> None:
        repo.delete_run(payload["run_id"])
        storage.delete_prefix(payload["run_id"])

    app = build_app(SERVICE, settings, bus,
                    on_startup=lambda: bus.subscribe_broadcast(RUN_DELETED, on_deleted), own_bus=own_bus)
    app.state.repo, app.state.storage = repo, storage
    app.state.reports = ReportService(registry, repo, storage)
    app.include_router(router)
    return app
