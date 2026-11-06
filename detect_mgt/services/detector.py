"""Stage 2: find every control that probably deletes something.

For each visited page we run the heuristic detector script (detect.js). Controls that are
hidden until something is clicked or hovered are found by exploring:

* standard   - visible controls + one level of menu openers (kebab / "more" / dropdowns)
* aggressive - also nested menus, accordions/tabs/<details>, and hovering rows and cards

Every exploration step is recorded in the candidate's `via` list so the probe can replay it.
"""
from __future__ import annotations

import time
from pathlib import Path

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver

from common.browser import DriverFactory, find, replay, safe_quit, wait_ready
from common.contracts import RunConfig, Status
from common.stage import StageContext, StageResult
from detect_mgt.repository.candidate_repository import CandidateRepository

DETECT_JS = (Path(__file__).parent / "scripts" / "detect.js").read_text(encoding="utf-8")
CAPS = {"standard": {"triggers": 12, "depth": 1, "expanders": 0, "hover": 0},
        "aggressive": {"triggers": 25, "depth": 2, "expanders": 20, "hover": 25}}


class Detector:
    def __init__(self, repo: CandidateRepository, driver_factory: DriverFactory, fetch_urls) -> None:
        self.repo = repo
        self.driver_factory = driver_factory
        self.fetch_urls = fetch_urls  # callable(run_id) -> list of visited URL rows from crawl_mgt

    # -- stage entry ---------------------------------------------------------------------
    def run(self, ctx: StageContext, cfg: RunConfig) -> StageResult:
        urls = self.fetch_urls(ctx.run_id)
        done = self.repo.scanned_url_ids(ctx.run_id)
        todo = [u for u in urls if u["url_id"] not in done]
        ctx.emit(f"{len(urls)} pages to scan ({len(done)} already done), mode={cfg.detection_mode}")
        driver: WebDriver | None = None
        scanned = failed = 0
        try:
            for u in todo:
                if ctx.cancel.is_set():
                    return self._result(ctx.run_id, Status.cancelled, "cancelled", len(urls), failed)
                if driver is None:
                    driver = self.driver_factory(cfg)
                try:
                    n = self.scan_page(driver, ctx.run_id, u, cfg)
                    self.repo.save_scan(ctx.run_id, u["url_id"], u["url"], cfg.detection_mode, "completed", n)
                    scanned += 1
                    ctx.emit(f"{n} candidate(s): {u['url']}", pages_scanned=scanned)
                except WebDriverException as exc:
                    msg = (exc.msg or str(exc))[:300]
                    self.repo.save_scan(ctx.run_id, u["url_id"], u["url"], cfg.detection_mode, "failed", 0, msg)
                    failed += 1
                    ctx.emit(f"scan failed: {u['url']}", "WARNING", error=msg[:200])
                    safe_quit(driver)
                    driver = None
        finally:
            safe_quit(driver)
        total = len(urls)
        if total and failed == total:
            return self._result(ctx.run_id, Status.failed, "no page could be scanned", total, failed)
        return self._result(ctx.run_id, Status.partial if failed else Status.completed,
                            f"{scanned + len(done)} of {total} pages scanned", total, failed)

    def _result(self, run_id: str, status: Status, reason: str, pages: int, failed: int) -> StageResult:
        return StageResult(status, reason, {"pages": pages, "pages_failed": failed,
                                            "candidates": self.repo.count(run_id)})

    # -- one page ------------------------------------------------------------------------
    def _js(self, driver: WebDriver, mode: str, cfg: RunConfig, exclude=(), max_=None):
        return driver.execute_script(DETECT_JS, {
            "mode": mode, "strong": cfg.strong_keywords, "weak": cfg.weak_keywords, "minScore": cfg.min_score,
            "max": max_ or cfg.max_candidates_per_page, "exclude": list(exclude),
        })

    def _load(self, driver: WebDriver, url: str, cfg: RunConfig) -> None:
        try:
            driver.get(url)
        except TimeoutException:
            pass  # use whatever rendered
        wait_ready(driver, cfg.settle_ms)

    def scan_page(self, driver: WebDriver, run_id: str, row: dict, cfg: RunConfig) -> int:
        url = row.get("final_url") or row["url"]
        caps = CAPS[cfg.detection_mode]
        self._load(driver, url, cfg)
        found: dict[str, dict] = {}      # selector -> candidate (first sighting wins)
        count = 0

        def record(cands: list[dict], via: list[dict]) -> None:
            nonlocal count
            for c in cands:
                if c["selector"] in found or count >= cfg.max_candidates_per_page:
                    continue
                found[c["selector"]] = c
                if self.repo.add(run_id, row["url_id"], url, c, via):
                    count += 1

        record(self._js(driver, "scan", cfg), [])
        tried: set[str] = set()
        self._explore_menus(driver, url, cfg, [], 0, caps, tried, record)
        if caps["expanders"]:
            for ex in self._js(driver, "expanders", cfg, max_=caps["expanders"]):
                self._load(driver, url, cfg)
                step = [{"action": "click", "selector": ex["selector"]}]
                if replay(driver, step, cfg.settle_ms):
                    continue
                record(self._js(driver, "scan", cfg), step)
                self._explore_menus(driver, url, cfg, step, 1, caps, tried, record)
        if caps["hover"]:
            self._load(driver, url, cfg)
            for tgt in self._js(driver, "hover_targets", cfg, max_=caps["hover"]):
                el = find(driver, tgt["selector"])
                if el is None:
                    continue
                try:
                    ActionChains(driver).move_to_element(el).perform()
                except WebDriverException:
                    continue
                time.sleep(0.15)
                record(self._js(driver, "scan", cfg), [{"action": "hover", "selector": tgt["selector"]}])
        return count

    def _explore_menus(self, driver, url, cfg, steps, depth, caps, tried, record) -> None:
        if depth >= caps["depth"]:
            return
        self._reset(driver, url, cfg, steps)
        triggers = self._js(driver, "triggers", cfg, exclude=tried, max_=caps["triggers"])
        for t in triggers:
            tried.add(t["selector"])
            self._reset(driver, url, cfg, steps)
            step = steps + [{"action": "click", "selector": t["selector"]}]
            if replay(driver, step[len(steps):], cfg.settle_ms):
                continue
            record(self._js(driver, "scan", cfg), step)
            self._explore_menus(driver, url, cfg, step, depth + 1, caps, tried, record)
            try:  # close whatever opened, so it cannot leak into the next iteration
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            except WebDriverException:
                pass

    def _reset(self, driver: WebDriver, url: str, cfg: RunConfig, steps: list[dict]) -> None:
        """Back to a fresh page with `steps` applied."""
        self._load(driver, url, cfg)
        replay(driver, steps, cfg.settle_ms)
