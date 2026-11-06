"""Stage 1: browser-based breadth-first crawl (so JS-rendered links are found).

The queue is the `urls` table, which makes the crawl resumable: a restarted stage simply keeps
taking `discovered` rows.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.remote.webdriver import WebDriver

from common.browser import DriverFactory, safe_quit, wait_ready
from common.contracts import RunConfig, Status
from common.stage import StageContext, StageResult
from crawl_mgt.repository.url_repository import UrlRepository
from crawl_mgt.services.urls import CrawlScope, looks_destructive, looks_non_html, normalize_url

LINKS_JS = """
return Array.from(document.querySelectorAll('a[href], area[href]'))
  .filter(a => !a.hasAttribute('download'))
  .map(a => a.href)
  .filter(h => typeof h === 'string');
"""
INFO_JS = """
const nav = performance.getEntriesByType('navigation')[0];
return {status: nav && nav.responseStatus ? nav.responseStatus : null,
        type: document.contentType || '', title: (document.title || '').slice(0, 200)};
"""


class Crawler:
    def __init__(self, repo: UrlRepository, driver_factory: DriverFactory) -> None:
        self.repo = repo
        self.driver_factory = driver_factory

    def run(self, ctx: StageContext, start_url: str, cfg: RunConfig) -> StageResult:
        run_id = ctx.run_id
        scope = CrawlScope(start_url, cfg.allow_subdomains, cfg.path_prefix, cfg.exclude_patterns)
        self.repo.add(run_id, start_url, normalize_url(start_url, cfg.include_query_params), 0, None)
        driver: WebDriver | None = None
        stop_reason = "queue exhausted"
        try:
            while True:
                if ctx.cancel.is_set():
                    return self._result(run_id, Status.cancelled, "cancelled")
                counts = self.repo.counts(run_id)
                done = counts.get("visited", 0) + counts.get("failed", 0)
                if cfg.max_pages and done >= cfg.max_pages:
                    stop_reason = "page limit reached"
                    break
                row = self.repo.next_discovered(run_id)
                if row is None:
                    break
                if driver is None:
                    driver = self.driver_factory(cfg)
                    ctx.emit("browser started")
                driver = self._visit(ctx, driver, row, cfg, scope) or driver
                if cfg.request_delay_s and ctx.cancel.wait(cfg.request_delay_s):
                    return self._result(run_id, Status.cancelled, "cancelled")
        finally:
            safe_quit(driver)
        counts = self.repo.counts(run_id)
        if not counts.get("visited"):
            return self._result(run_id, Status.failed, "no page could be crawled (see failed URLs)")
        status = Status.partial if counts.get("failed") else Status.completed
        return self._result(run_id, status, stop_reason)

    def _result(self, run_id: str, status: Status, reason: str) -> StageResult:
        c = self.repo.counts(run_id)
        return StageResult(status, reason, {
            "visited": c.get("visited", 0), "failed": c.get("failed", 0),
            "skipped": c.get("skipped", 0), "discovered": c.get("discovered", 0),
        })

    def _visit(self, ctx: StageContext, driver: WebDriver, row: dict, cfg: RunConfig, scope: CrawlScope):
        url_id, url = row["url_id"], row["url"]
        if looks_non_html(url):
            self.repo.mark(url_id, "skipped", error="non-HTML extension")
            return None
        if looks_destructive(url):  # never GET a URL that may delete data or end the session
            self.repo.mark(url_id, "skipped", error="destructive-looking URL (delete/logout); not visited")
            return None
        try:
            try:
                driver.get(url)
            except TimeoutException:
                ctx.emit(f"page load timed out, using what rendered: {url}", "WARNING")
            wait_ready(driver, cfg.settle_ms)
            final = driver.current_url
            info = driver.execute_script(INFO_JS)
            links = driver.execute_script(LINKS_JS)
        except WebDriverException as exc:
            self.repo.mark(url_id, "failed", error=(exc.msg or str(exc))[:300])
            ctx.emit(f"failed to load {url}", "WARNING", error=(exc.msg or str(exc))[:200])
            # a dead browser would fail every later page: restart it
            safe_quit(driver)
            return self.driver_factory(cfg)
        status = info.get("status")
        if status and status >= 400:
            self.repo.mark(url_id, "failed", http_status=status, final_url=final, error=f"HTTP {status}")
            ctx.emit(f"HTTP {status}: {url}", "WARNING")
            return None
        if "html" not in str(info.get("type", "")).lower():
            self.repo.mark(url_id, "skipped", http_status=status, final_url=final, error="not HTML")
            return None
        final_norm = normalize_url(final, cfg.include_query_params)
        if not scope.allows(final_norm):
            self.repo.mark(url_id, "skipped", http_status=status, final_url=final, error="redirected out of scope")
            return None
        if final_norm != row["normalized_url"] and self.repo.known(ctx.run_id, final_norm):
            self.repo.mark(url_id, "skipped", http_status=status, final_url=final, error="redirect to known URL")
            return None
        depth = row["depth"]
        if cfg.max_depth is None or depth + 1 <= cfg.max_depth:
            self._enqueue(ctx.run_id, links, depth + 1, url_id, cfg, scope)
        self.repo.mark(url_id, "visited", http_status=status, final_url=final, title=info.get("title"))
        return None

    def _enqueue(self, run_id: str, links: list[str], depth: int, parent: int, cfg: RunConfig, scope: CrawlScope):
        for link in links:
            if not link.startswith(("http://", "https://")) or looks_non_html(link):
                continue
            norm = normalize_url(link, cfg.include_query_params)
            if scope.allows(norm):
                self.repo.add(run_id, norm, norm, depth, parent)
