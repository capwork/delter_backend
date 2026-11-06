"""Fast tests that need no browser: URL rules, verdict logic, run status, event bus, run creation."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from common.contracts import Verdict
from common.events import MemoryBus
from configuration.settings import Settings
from crawl_mgt.services.urls import CrawlScope, looks_non_html, normalize_url
from gateway.gateway_main import resolve
from probe_mgt.services.verdict import decide
from run_mgt.repository.run_repository import derive_run_status
from run_mgt.run_mgt_main import create_app as run_app
from run_mgt.services.run_service import parse_start_url
from common.errors import InvalidInput


def test_normalize_url():
    assert normalize_url("HTTPS://Example.com:443/a/./b/../c/?b=2&a=1&utm_source=x#frag") == "https://example.com/a/c?a=1&b=2"
    assert normalize_url("http://example.com") == "http://example.com/"
    assert normalize_url("http://example.com/a?x=1", include_query=False) == "http://example.com/a"


def test_scope_and_extensions():
    scope = CrawlScope("http://127.0.0.1:8000/", False, None, [r"/logout"])
    assert scope.allows("http://127.0.0.1:8000/a")
    assert not scope.allows("http://127.0.0.1:9000/a")          # another local app
    assert not scope.allows("http://example.org/a")
    assert not scope.allows("http://127.0.0.1:8000/logout")
    assert looks_non_html("http://x.com/a.PDF") and not looks_non_html("http://x.com/a")


@pytest.mark.parametrize("raw,expected", [("example.com", "https://example.com/"),
                                          ("http://localhost:3000/app", "http://localhost:3000/app")])
def test_parse_start_url(raw, expected):
    assert parse_start_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "has space.com", "ftp://x.com", "nodot", "https://x.com:99999"])
def test_parse_start_url_rejects(raw):
    with pytest.raises(InvalidInput):
        parse_start_url(raw)


def test_verdicts():
    assert decide({"dialogs": [{"type": "confirm", "message": "Sure?"}]})[0] == Verdict.native_dialog
    assert decide({"modal": {"is_confirmation": True, "signals": ["aria_dialog"]}})[0] == Verdict.custom_modal
    assert decide({"blocked": [{"kind": "fetch", "method": "DELETE", "url": "/api/x"}]})[0] == Verdict.no_confirmation
    assert decide({"blocked": [{"kind": "navigation", "method": "GET", "url": "/x/delete/5"}]})[0] == Verdict.no_confirmation
    assert decide({"blocked": [{"kind": "navigation", "method": "GET", "url": "/x/confirm"}]})[0] == Verdict.inconclusive
    assert decide({"target_removed": True})[0] == Verdict.no_confirmation
    assert decide({})[0] == Verdict.inconclusive
    # a native dialog wins over everything else
    both = {"dialogs": [{"type": "confirm", "message": "m"}], "modal": {"is_confirmation": True, "signals": []}}
    assert decide(both)[0] == Verdict.native_dialog


def test_run_status_is_never_optimistic():
    assert derive_run_status(["completed"] * 3) == "completed"
    assert derive_run_status(["completed", "partial", "pending"]) == "partial"
    assert derive_run_status(["completed", "failed", "pending"]) == "partial"
    assert derive_run_status(["failed", "pending", "pending"]) == "failed"
    assert derive_run_status(["completed", "running", "pending"]) == "running"
    assert derive_run_status(["pending"] * 3) == "pending"


def test_gateway_routing():
    assert resolve("/runs") == "run_mgt" and resolve("/runs/abc/events") == "run_mgt"
    assert resolve("/runs/abc/urls") == "crawl_mgt"
    assert resolve("/runs/abc/candidates") == "detect_mgt" and resolve("/candidates/cand_1") == "detect_mgt"
    assert resolve("/runs/abc/probes") == "probe_mgt"
    assert resolve("/runs/abc/report/html") == "evidence_mgt" and resolve("/evidence/ev_1/file") == "evidence_mgt"
    assert resolve("/nope") is None


def test_memory_bus_groups_and_broadcast():
    bus = MemoryBus()
    got: dict[str, list] = {"a": [], "b": [], "bc1": [], "bc2": []}
    bus.subscribe("t", "svc_a", got["a"].append)
    bus.subscribe("t", "svc_b", got["b"].append)
    bus.subscribe_broadcast("c", got["bc1"].append)
    bus.subscribe_broadcast("c", got["bc2"].append)
    bus.publish("t", {"n": 1})
    bus.publish("c", {"n": 2})
    time.sleep(0.6)
    bus.close()
    assert got["a"] == [{"n": 1}] and got["b"] == [{"n": 1}]          # each group gets the event once
    assert got["bc1"] == [{"n": 2}] and got["bc2"] == [{"n": 2}]      # every broadcast listener gets it


def test_run_lifecycle_without_workers(tmp_path):
    settings = Settings(data_dir=tmp_path, event_bus="memory", log_level="WARNING")
    with TestClient(run_app(settings, MemoryBus())) as c:
        assert c.post("/runs", json={"domain": "not a host"}).status_code == 422
        run = c.post("/runs", json={"domain": "example.com"}).json()
        assert run["status"] == "pending" and run["start_url"] == "https://example.com/"
        assert c.post(f"/runs/{run['run_id']}/detect").status_code == 409     # prerequisite: crawl first
        assert c.post(f"/runs/{run['run_id']}/probe").status_code == 409
        assert c.post(f"/runs/{run['run_id']}/start").status_code == 202
        assert c.post(f"/runs/{run['run_id']}/crawl").status_code == 409      # one stage at a time
        assert c.delete(f"/runs/{run['run_id']}").status_code == 409          # busy
        assert c.post(f"/runs/{run['run_id']}/cancel").status_code == 202
        assert c.get("/runs", params={"q": "example"}).json()["total"] == 1
        assert c.get("/runs/nope").status_code == 404


def test_crawler_never_follows_destructive_urls():
    from crawl_mgt.services.urls import looks_destructive
    assert looks_destructive("http://x.com/items/5/delete")
    assert looks_destructive("http://x.com/logout")
    assert looks_destructive("http://x.com/a/remove-user")
    assert not looks_destructive("http://x.com/products/deleted-items-report")  # "deleted" is not "/delete"
    assert not looks_destructive("http://x.com/about")
