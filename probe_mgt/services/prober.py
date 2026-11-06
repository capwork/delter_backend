"""Stage 3: click every candidate and observe what the platform does.

Safety (always on, not configurable):
  * the page hook (hook.js) answers every native confirm()/prompt() with "Cancel" and, once armed,
    blocks fetch/XHR/beacon with a non-GET method, form submissions and link navigations;
  * Chrome is told to refuse requests to /delete*, /destroy* and /remove* URLs while the click happens;
  * nothing in a confirmation dialog or modal is ever clicked.
These layers reduce the chance of real deletion but cannot guarantee it (e.g. a programmatic
location change to an unusual URL), so point the tool at staging or data you can afford to lose.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoAlertPresentException,
    TimeoutException,
    UnexpectedAlertPresentException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

from common.browser import DriverFactory, find, replay, safe_quit, wait_ready
from common.clients import ServiceRegistry
from common.contracts import RunConfig, Status, Verdict
from common.errors import DelterError
from common.stage import StageContext, StageResult
from probe_mgt.repository.probe_repository import ProbeRepository
from probe_mgt.services.verdict import decide

HOOK_JS = (Path(__file__).parent / "scripts" / "hook.js").read_text(encoding="utf-8")
BLOCKED_URL_PATTERNS = ["*/delete*", "*/destroy*", "*/remove*"]
HIGHLIGHT = "arguments[0].style.setProperty('outline','3px solid #e11d48','important');" \
            "arguments[0].style.setProperty('outline-offset','2px','important');"
UNHIGHLIGHT = "arguments[0].style.removeProperty('outline');arguments[0].style.removeProperty('outline-offset');"


class EvidenceClient:
    """Uploads screenshots and details to evidence_mgt over REST."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    def upload(self, run_id: str, candidate_id: str, kind: str, filename: str, data: bytes,
               content_type: str, meta: dict | None = None) -> str:
        body = self.registry.json(
            "evidence_mgt", "POST", f"/runs/{run_id}/evidence",
            data={"candidate_id": candidate_id, "kind": kind, "meta": json.dumps(meta or {})},
            files={"file": (filename, data, content_type)},
        )
        return body["evidence_id"]


class Prober:
    def __init__(self, repo: ProbeRepository, driver_factory: DriverFactory, evidence: EvidenceClient,
                 fetch_candidates) -> None:
        self.repo, self.driver_factory, self.evidence = repo, driver_factory, evidence
        self.fetch_candidates = fetch_candidates  # callable(run_id) -> candidates from detect_mgt

    # -- stage entry ---------------------------------------------------------------------
    def run(self, ctx: StageContext, cfg: RunConfig) -> StageResult:
        candidates = self.fetch_candidates(ctx.run_id)
        done = self.repo.completed_candidate_ids(ctx.run_id)
        todo = [c for c in candidates if c["candidate_id"] not in done]
        ctx.emit(f"{len(candidates)} candidates to probe ({len(done)} already done)")
        driver: WebDriver | None = None
        try:
            for i, cand in enumerate(todo, 1):
                if ctx.cancel.is_set():
                    return self._result(ctx.run_id, Status.cancelled, "cancelled")
                outcome = None
                for attempt in range(1, cfg.probe_max_retries + 2):
                    try:
                        if driver is None:
                            driver = self._new_driver(cfg)
                        outcome = self.probe_one(driver, ctx, cand, cfg)
                        outcome["attempts"] = attempt
                        break
                    except WebDriverException as exc:
                        safe_quit(driver)  # a broken browser is replaced before the retry
                        driver = None
                        err = (exc.msg or str(exc))[:300]
                        outcome = {"status": "failed", "error": err, "attempts": attempt}
                        ctx.emit(f"probe attempt {attempt} failed: {cand['selector']}", "WARNING", error=err[:200])
                self._save(ctx.run_id, cand, outcome)
                v = outcome.get("verdict")
                ctx.emit(f"[{i}/{len(todo)}] {v or 'failed'}: {cand['url']} :: {cand.get('text') or cand['selector']}",
                         verdict=v)
        finally:
            safe_quit(driver)
        counts = self.repo.counts(ctx.run_id)
        total = len(candidates)
        if total and counts["probed"] == 0:
            return self._result(ctx.run_id, Status.failed, "no candidate could be probed")
        status = Status.partial if counts["failed"] or counts["probed"] < total else Status.completed
        return self._result(ctx.run_id, status, f"{counts['probed']} of {total} candidates probed")

    def _result(self, run_id: str, status: Status, reason: str) -> StageResult:
        c = self.repo.counts(run_id)
        return StageResult(status, reason, c)

    def _new_driver(self, cfg: RunConfig) -> WebDriver:
        driver = self.driver_factory(cfg)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": HOOK_JS})
        driver.execute_cdp_cmd("Network.enable", {})
        return driver

    def _save(self, run_id: str, cand: dict, o: dict) -> None:
        self.repo.save(
            run_id, cand, status=o["status"], verdict=o.get("verdict"), reason=o.get("reason"),
            dialogs=o.get("dialogs", []), modal=o.get("modal"), blocked=o.get("blocked", []),
            final_url=o.get("final_url"), evidence_ids=o.get("evidence_ids", []), error=o.get("error"),
            attempts=o.get("attempts", 1), duration_ms=o.get("duration_ms", 0),
        )

    # -- one candidate ---------------------------------------------------------------------
    def probe_one(self, driver: WebDriver, ctx: StageContext, cand: dict, cfg: RunConfig) -> dict[str, Any]:
        t0 = time.monotonic()
        evidence_ids: list[str] = []

        def finish(verdict: Verdict, reason: str, state: dict | None = None, **extra: Any) -> dict[str, Any]:
            state = state or {}
            return {"status": "completed", "verdict": verdict, "reason": reason,
                    "dialogs": state.get("dialogs", []), "modal": state.get("modal"),
                    "blocked": state.get("blocked", []), "final_url": state.get("url"),
                    "evidence_ids": evidence_ids, "duration_ms": int((time.monotonic() - t0) * 1000), **extra}

        try:
            driver.get(cand["url"])
        except TimeoutException:
            pass
        wait_ready(driver, cfg.settle_ms)
        problem = replay(driver, cand.get("via") or [], cfg.settle_ms)
        if problem:
            return finish(Verdict.inconclusive, f"could not reach the control: {problem}")
        el = find(driver, cand["selector"]) or self._find_by_text(driver, cand)
        if el is None:
            return finish(Verdict.inconclusive, "control not found on the page (page content changed since detection)")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
        if not (el.is_displayed() and el.is_enabled()):
            return finish(Verdict.inconclusive, "control is not clickable (hidden or disabled)")

        driver.execute_script(HIGHLIGHT, el)
        self._evidence(ctx, cand, evidence_ids, "before", "before.png", driver.get_screenshot_as_png(), "image/png")
        driver.execute_script(UNHIGHLIGHT, el)

        driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": BLOCKED_URL_PATTERNS})
        driver.execute_script("window.__delter.arm(arguments[0])", el)
        selenium_dialogs: list[dict] = []
        try:
            self._click(driver, el, selenium_dialogs)
            state = self._observe(driver, cfg.observe_ms, selenium_dialogs)
        finally:
            try:
                driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": []})
            except WebDriverException:
                pass

        self._evidence(ctx, cand, evidence_ids, "after", "after.png", driver.get_screenshot_as_png(), "image/png")
        verdict, reason = decide(state)
        details = {"candidate": {k: cand.get(k) for k in ("candidate_id", "url", "selector", "via", "text", "score",
                                                          "reasons", "hints", "html")},
                   "observation": state, "verdict": verdict, "reason": reason}
        self._evidence(ctx, cand, evidence_ids, "details", "details.json",
                       json.dumps(details, indent=2, default=str).encode(), "application/json")
        return finish(verdict, reason, state)

    def _click(self, driver: WebDriver, el, seen: list[dict]) -> None:
        try:
            try:
                el.click()
            except (ElementClickInterceptedException, ElementNotInteractableException):
                driver.execute_script("arguments[0].click()", el)
        except UnexpectedAlertPresentException:
            self._take_alert(driver, seen)

    @staticmethod
    def _take_alert(driver: WebDriver, seen: list[dict]) -> None:
        """Backstop for a native dialog the page hook did not intercept (e.g. one raised inside an iframe)."""
        try:
            alert = driver.switch_to.alert
            seen.append({"type": "dialog", "message": alert.text, "source": "selenium"})
            alert.dismiss()
        except (NoAlertPresentException, WebDriverException):
            pass

    def _observe(self, driver: WebDriver, observe_ms: int, selenium_dialogs: list[dict]) -> dict[str, Any]:
        deadline = time.monotonic() + observe_ms / 1000
        state: dict[str, Any] = {}
        while time.monotonic() < deadline:
            self._take_alert(driver, selenium_dialogs)
            try:
                state = driver.execute_script(
                    "return window.__delter ? window.__delter.collect() : {navigated_away: true, url: location.href}")
            except UnexpectedAlertPresentException:
                self._take_alert(driver, selenium_dialogs)
                continue
            if state.get("dialogs") or selenium_dialogs:
                break
            time.sleep(0.1)
        self._take_alert(driver, selenium_dialogs)
        if not state:
            state = {"url": driver.current_url}
        state["dialogs"] = (state.get("dialogs") or []) + selenium_dialogs
        if state.get("navigated_away"):
            state.setdefault("blocked", []).append({"kind": "navigation", "method": "GET", "url": state["url"]})
        return state

    def _find_by_text(self, driver: WebDriver, cand: dict):
        """Fallback when the CSS path no longer matches: same tag and the same visible text."""
        text = (cand.get("text") or "").strip()
        if not text or not cand.get("tag"):
            return None
        for el in driver.find_elements(By.TAG_NAME, cand["tag"]):
            try:
                if el.text.strip() == text and el.is_displayed():
                    return el
            except WebDriverException:
                continue
        return None

    def _evidence(self, ctx: StageContext, cand: dict, ids: list[str], kind: str, filename: str, data: bytes,
                  content_type: str) -> None:
        try:
            ids.append(self.evidence.upload(ctx.run_id, cand["candidate_id"], kind, filename, data, content_type))
        except DelterError as exc:  # losing one screenshot must not lose the verdict
            ctx.emit(f"could not store {kind} evidence: {exc}", "WARNING")
