from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import urlsplit

from common.contracts import (
    REQUEST_TOPIC,
    RUN_CANCEL,
    RUN_DELETED,
    STAGE_ORDER,
    RunConfig,
    Stage,
    StageFinished,
    StageRequest,
    Status,
)
from common.errors import Conflict, InvalidInput, NotFound
from common.events import EventBus
from run_mgt.repository.run_repository import RunRepository

log = logging.getLogger("run_mgt")


def parse_start_url(domain: str) -> str:
    """example.com -> https://example.com/. Rejects anything that is not a plain http(s) site."""
    raw = domain.strip()
    if not raw or any(c.isspace() for c in raw):
        raise InvalidInput("domain must be a non-empty value without spaces")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise InvalidInput("only http and https are supported")
    host = parts.hostname or ""
    try:
        _ = parts.port
    except ValueError as exc:
        raise InvalidInput("invalid port") from exc
    if not host or ("." not in host and host != "localhost"):
        raise InvalidInput(f"{host!r} is not a valid host")
    return parts._replace(fragment="", path=parts.path or "/").geturl()


class RunService:
    def __init__(self, repo: RunRepository, bus: EventBus) -> None:
        self.repo, self.bus = repo, bus

    # -- queries -----------------------------------------------------------------------
    def get(self, run_id: str) -> dict:
        run = self.repo.get(run_id)
        if run is None:
            raise NotFound(f"run {run_id} not found")
        run["active_stage"] = self.repo.active_stage(run)
        return run

    def list(self, **kw: Any) -> dict:
        total, items = self.repo.list(**kw)
        for r in items:
            r["active_stage"] = self.repo.active_stage(r)
        return {"total": total, "items": items}

    # -- commands ----------------------------------------------------------------------
    def create(self, domain: str, config: RunConfig) -> dict:
        start_url = parse_start_url(domain)
        run_id = uuid.uuid4().hex[:12]
        self.repo.create(run_id, domain.strip(), urlsplit(start_url).netloc, start_url, config.model_dump())
        self.repo.add_event(run_id, None, "INFO", "run created", {"start_url": start_url})
        return self.get(run_id)

    def start_pipeline(self, run_id: str) -> dict:
        return self._start(run_id, Stage.crawl, chain=True)

    def start_stage(self, run_id: str, stage: Stage) -> dict:
        run = self.get(run_id)
        if stage == Stage.detect:
            visited = run["summary"].get("crawl", {}).get("visited")
            if run["crawl_status"] not in (Status.completed, Status.partial) or not visited:
                raise Conflict("Detection needs a crawl that visited at least one page")
        if stage == Stage.probe:
            found = run["summary"].get("detect", {}).get("candidates")
            if run["detect_status"] not in (Status.completed, Status.partial) or not found:
                raise Conflict("Probing needs a detection run that found at least one candidate")
        return self._start(run_id, stage, chain=False)

    def _start(self, run_id: str, stage: Stage, chain: bool) -> dict:
        run = self.get(run_id)
        if not self.repo.claim_stage(run_id, stage, auto_chain=chain):
            raise Conflict(f"run {run_id} already has a stage running")
        self._publish_request(run, stage)
        self.repo.add_event(run_id, stage, "INFO", f"{stage} requested" + (" (pipeline)" if chain else ""))
        return self.get(run_id)

    def _publish_request(self, run: dict, stage: Stage) -> None:
        req = StageRequest(run_id=run["run_id"], start_url=run["start_url"], config=RunConfig(**run["config"]))
        self.bus.publish(REQUEST_TOPIC[stage], req.model_dump())

    def cancel(self, run_id: str) -> dict:
        run = self.get(run_id)
        if not run["active_stage"]:
            raise Conflict("nothing is running")
        self.repo.set_auto_chain(run_id, False)
        self.bus.publish(RUN_CANCEL, {"run_id": run_id})
        self.repo.add_event(run_id, run["active_stage"], "INFO", "cancel requested")
        return self.get(run_id)

    def delete(self, run_id: str) -> None:
        run = self.get(run_id)
        if run["active_stage"]:
            raise Conflict("stop the running stage before deleting the run")
        self.repo.delete(run_id)
        self.bus.publish(RUN_DELETED, {"run_id": run_id})

    def events(self, run_id: str, after_id: int, level: str | None, limit: int) -> list[dict]:
        self.get(run_id)
        return self.repo.events(run_id, after_id, level, limit)

    # -- event handlers (called from the bus) --------------------------------------------
    def on_run_event(self, payload: dict[str, Any]) -> None:
        self.repo.add_event(
            payload["run_id"], payload.get("stage"), payload.get("level", "INFO"), payload["message"], payload.get("data")
        )

    def on_stage_finished(self, payload: dict[str, Any]) -> None:
        fin = StageFinished(**payload)
        run = self.repo.get(fin.run_id)
        if run is None:
            return
        self.repo.merge_summary(fin.run_id, fin.stage, fin.stats)
        self.repo.set_stage_status(fin.run_id, fin.stage, fin.status)
        if not run["auto_chain"]:
            return
        idx = STAGE_ORDER.index(fin.stage)
        ok = fin.status in (Status.completed, Status.partial)
        if not ok or idx == len(STAGE_ORDER) - 1:
            self.repo.set_auto_chain(fin.run_id, False)
            return
        nxt = STAGE_ORDER[idx + 1]
        if nxt == Stage.probe and not fin.stats.get("candidates"):
            self.repo.set_stage_status(fin.run_id, Stage.probe, Status.completed, auto_chain=False)
            self.repo.add_event(fin.run_id, "probe", "INFO", "no delete candidates found; nothing to probe")
            return
        self.repo.set_stage_status(fin.run_id, nxt, Status.running)
        self._publish_request(self.repo.get(fin.run_id), nxt)  # type: ignore[arg-type]
        self.repo.add_event(fin.run_id, nxt, "INFO", f"{nxt} requested (pipeline)")
