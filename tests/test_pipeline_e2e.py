"""End-to-end: all services in one process, real Chrome, local fixture website."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.browser

FAST = {"request_delay_s": 0, "settle_ms": 150, "observe_ms": 700, "max_pages": 20}


def run_pipeline(stack, site, start_path="", **config):
    run = stack.run.post("/runs", json={"domain": site.base + start_path, "config": {**FAST, **config}}).json()
    assert stack.run.post(f"/runs/{run['run_id']}/start").status_code == 202
    return stack.wait_run(run["run_id"])


def by_page(stack, run_id):
    probes = stack.probe.get(f"/runs/{run_id}/probes", params={"limit": 500}).json()["items"]
    cands = {c["candidate_id"]: c for c in
             stack.detect.get(f"/runs/{run_id}/candidates", params={"limit": 500}).json()["items"]}
    out: dict[str, list[dict]] = {}
    for p in probes:
        path = "/" + cands[p["candidate_id"]]["url"].split("/", 3)[-1].split("?")[0]
        out.setdefault(path, []).append({**p, "cand": cands[p["candidate_id"]]})
    return out


def test_standard_pipeline(stack, site):
    run = run_pipeline(stack, site)
    rid = run["run_id"]
    # /missing is a 404, so the crawl is `partial` and the run can never claim `completed`
    assert (run["status"], run["crawl_status"], run["detect_status"], run["probe_status"]) == (
        "partial", "partial", "completed", "completed"), run
    assert run["summary"]["crawl"]["visited"] == 7        # home + six fixture pages
    urls = stack.crawl.get(f"/runs/{rid}/urls").json()["items"]
    assert any(u["status"] == "failed" and u["http_status"] == 404 for u in urls)       # /missing
    skipped = {u["url"].rsplit("/", 1)[-1]: u["error"] for u in urls if u["status"] == "skipped"}
    assert "destructive" in skipped["delete"]       # the GET-delete link was seen but never requested
    assert "file.pdf" not in [u["url"].rsplit("/", 1)[-1] for u in urls]                 # non-HTML never queued

    pages = by_page(stack, rid)
    verdicts = {path: {p["verdict"] for p in items} for path, items in pages.items()}
    assert verdicts["/native"] == {"native_dialog"}, verdicts
    assert verdicts["/themed"] == {"custom_modal"}, verdicts
    assert verdicts["/none"] == {"no_confirmation"}, verdicts
    assert verdicts["/menu"] == {"native_dialog"}, verdicts         # found by opening the kebab menu
    assert "/hover" not in verdicts                                  # hover reveal is aggressive-only
    assert "/plain" not in verdicts                                  # "Clear filters" must not be flagged

    menu_probe = pages["/menu"][0]
    assert menu_probe["cand"]["via"] and menu_probe["cand"]["via"][0]["action"] == "click"
    native = pages["/native"][0]
    assert native["dialogs"][0]["type"] == "confirm"

    # SAFETY: no delete ever reached the server
    assert site.mutations() == [], site.mutations()

    # evidence: before/after screenshots + details for every probe
    ev = stack.evidence.get(f"/runs/{rid}/evidence", params={"limit": 500}).json()
    kinds = {e["kind"] for e in ev["items"]}
    assert kinds == {"before", "after", "details"}
    png = next(e for e in ev["items"] if e["kind"] == "after")
    body = stack.evidence.get(f"/evidence/{png['evidence_id']}/file")
    assert body.status_code == 200 and body.content[:4] == b"\x89PNG"

    # report in all three formats
    report = stack.evidence.get(f"/runs/{rid}/report").json()
    s = report["summary"]
    assert s["native_dialog"] >= 3 and s["custom_modal"] == 1 and s["no_confirmation"] == 1
    assert s["needs_attention"] == s["native_dialog"] + s["no_confirmation"]
    csv_text = stack.evidence.get(f"/runs/{rid}/report/csv").text
    assert csv_text.splitlines()[0].startswith("run_id,page_url") and "native_dialog" in csv_text
    page = stack.evidence.get(f"/runs/{rid}/report/html").text
    assert "Browser-native dialog" in page and "data:image/png;base64" in page

    # events from all services reached run_mgt in one place
    stages = {e["stage"] for e in stack.run.get(f"/runs/{rid}/events").json()}
    assert {"crawl", "detect", "probe"} <= stages

    # deleting the run removes every service's data
    assert stack.run.delete(f"/runs/{rid}").status_code == 204
    import time
    time.sleep(1)
    assert stack.detect.get(f"/runs/{rid}/candidates").json()["total"] == 0
    assert stack.probe.get(f"/runs/{rid}/probes").json()["total"] == 0
    assert stack.evidence.get(f"/runs/{rid}/evidence").json()["total"] == 0


def test_aggressive_mode_finds_hover_only_controls(stack, site):
    run = run_pipeline(stack, site, "/hover", detection_mode="aggressive", path_prefix="/hover")
    rid = run["run_id"]
    assert run["status"] == "completed", run
    pages = by_page(stack, rid)
    assert {p["verdict"] for p in pages["/hover"]} == {"native_dialog"}, pages
    assert pages["/hover"][0]["cand"]["via"][0]["action"] == "hover"
    assert site.mutations() == []


def test_stages_can_run_separately_and_resume(stack, site):
    run = stack.run.post("/runs", json={"domain": site.base + "/native", "config": {**FAST, "path_prefix": "/native"}}).json()
    rid = run["run_id"]
    assert stack.run.post(f"/runs/{rid}/crawl").status_code == 202
    assert stack.wait_run(rid)["crawl_status"] == "completed"
    assert stack.run.post(f"/runs/{rid}/detect").status_code == 202
    assert stack.wait_run(rid)["detect_status"] == "completed"
    assert stack.run.post(f"/runs/{rid}/probe").status_code == 202
    done = stack.wait_run(rid)
    assert done["status"] == "completed" and done["summary"]["probe"]["native_dialog"] == 2
    # a second probe run resumes: everything is already done, nothing is probed twice
    assert stack.run.post(f"/runs/{rid}/probe").status_code == 202
    again = stack.wait_run(rid)
    assert again["summary"]["probe"]["probed"] == 2
