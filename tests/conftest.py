from __future__ import annotations

import time
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from common.clients import ServiceRegistry
from common.events import MemoryBus
from configuration.settings import Settings
from crawl_mgt.crawl_mgt_main import create_app as crawl_app
from detect_mgt.detect_mgt_main import create_app as detect_app
from evidence_mgt.evidence_mgt_main import create_app as evidence_app
from gateway.gateway_main import create_app as gateway_app  # noqa: F401  (imported for completeness)
from probe_mgt.probe_mgt_main import create_app as probe_app
from run_mgt.run_mgt_main import create_app as run_app
from tests.fixture_server import FixtureSite


@pytest.fixture(scope="session")
def site():
    s = FixtureSite().start()
    yield s
    s.stop()


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", event_bus="memory", log_level="WARNING")


@dataclass
class Stack:
    bus: MemoryBus
    run: TestClient
    crawl: TestClient
    detect: TestClient
    probe: TestClient
    evidence: TestClient

    def wait_run(self, run_id: str, *, timeout: float = 200) -> dict:
        """Poll run_mgt until nothing is running any more."""
        end = time.monotonic() + timeout
        time.sleep(0.3)
        while time.monotonic() < end:
            run = self.run.get(f"/runs/{run_id}").json()
            if run["active_stage"] is None:
                return run
            time.sleep(0.5)
        raise TimeoutError(f"run {run_id} still running: {run}")


@pytest.fixture
def stack(settings):
    """All five backend services in one process, wired through an in-memory bus and TestClients."""
    bus = MemoryBus()
    registry = ServiceRegistry(settings)
    apps = {
        "run_mgt": run_app(settings, bus),
        "crawl_mgt": crawl_app(settings, bus),
        "detect_mgt": detect_app(settings, bus, registry),
        "probe_mgt": probe_app(settings, bus, registry),
        "evidence_mgt": evidence_app(settings, bus, registry),
    }
    clients = {name: TestClient(app) for name, app in apps.items()}
    for c in clients.values():
        c.__enter__()  # runs the lifespan: subscribes workers to the bus
    registry._clients.update(clients)  # service-to-service calls stay in-process
    yield Stack(bus, clients["run_mgt"], clients["crawl_mgt"], clients["detect_mgt"], clients["probe_mgt"],
                clients["evidence_mgt"])
    for c in clients.values():
        c.__exit__(None, None, None)
    bus.close()
