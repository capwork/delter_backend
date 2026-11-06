"""Shared machinery for the stage-worker services (crawl_mgt, detect_mgt, probe_mgt).

Every worker has the same lifecycle:
  event `<stage>.requested` -> job row `running` -> do the work -> job row final ->
  event `stage.finished` (run_mgt reacts to it).
Cancellation is cooperative through a `threading.Event` fed by the broadcast `run.cancel` topic.
A job still `running` when the service starts means the previous process died; it is reported
as `partial` so the stage can be restarted and resumes where it stopped.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from common.contracts import (
    RUN_CANCEL,
    RUN_DELETED,
    RUN_EVENT,
    STAGE_FINISHED,
    RunEvent,
    Stage,
    StageFinished,
    Status,
)
from common.db import Database
from common.errors import DelterError
from common.events import EventBus

JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    run_id      TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class StageResult:
    status: Status
    reason: str = ""
    stats: dict[str, Any] = field(default_factory=dict)


class Jobs:
    def __init__(self, db: Database) -> None:
        self.db = db

    def start(self, run_id: str) -> None:
        self.db.execute(
            "INSERT INTO jobs(run_id,status,started_at) VALUES(?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, reason='', "
            "started_at=excluded.started_at, finished_at=NULL",
            (run_id, Status.running, now()),
        )

    def finish(self, run_id: str, status: Status, reason: str) -> None:
        self.db.execute(
            "UPDATE jobs SET status=?, reason=?, finished_at=? WHERE run_id=?",
            (status, reason, now(), run_id),
        )

    def is_running(self, run_id: str) -> bool:
        row = self.db.one("SELECT status FROM jobs WHERE run_id=?", (run_id,))
        return bool(row and row["status"] == Status.running)

    def interrupted(self) -> list[str]:
        return [r["run_id"] for r in self.db.query("SELECT run_id FROM jobs WHERE status=?", (Status.running,))]

    def delete(self, run_id: str) -> None:
        self.db.execute("DELETE FROM jobs WHERE run_id=?", (run_id,))


class CancelRegistry:
    def __init__(self) -> None:
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def get(self, run_id: str) -> threading.Event:
        with self._lock:
            return self._events.setdefault(run_id, threading.Event())

    def cancel(self, run_id: str) -> None:
        with self._lock:
            ev = self._events.get(run_id)
        if ev:
            ev.set()

    def discard(self, run_id: str) -> None:
        with self._lock:
            self._events.pop(run_id, None)


class StageContext:
    """What a stage implementation gets: a cancel flag and a way to emit run events."""

    def __init__(self, bus: EventBus, stage: Stage, run_id: str, cancel: threading.Event, log: logging.Logger):
        self.bus, self.stage, self.run_id, self.cancel, self.log = bus, stage, run_id, cancel, log

    def emit(self, message: str, level: str = "INFO", **data: Any) -> None:
        self.log.log(getattr(logging, level, logging.INFO), message, extra={"run_id": self.run_id, "stage": self.stage})
        self.bus.publish(
            RUN_EVENT,
            RunEvent(run_id=self.run_id, stage=self.stage, level=level, message=message, data=data or None).model_dump(),
        )


class StageWorker:
    def __init__(
        self,
        *,
        stage: Stage,
        service: str,
        bus: EventBus,
        db: Database,
        run_fn: Callable[[StageContext, dict[str, Any]], StageResult],
        on_run_deleted: Callable[[str], None],
        concurrency: int = 1,
    ) -> None:
        self.stage, self.service, self.bus = stage, service, bus
        self.jobs = Jobs(db)
        self.cancels = CancelRegistry()
        self._run_fn = run_fn
        self._on_deleted = on_run_deleted
        self._concurrency = concurrency
        self.log = logging.getLogger(service)

    # -- wiring ------------------------------------------------------------------------
    def start(self, request_topic: str) -> None:
        self._recover()
        self.bus.subscribe(request_topic, self.service, self.handle_request, self._concurrency)
        self.bus.subscribe_broadcast(RUN_CANCEL, lambda p: self.cancels.cancel(p["run_id"]))
        self.bus.subscribe_broadcast(RUN_DELETED, self._deleted)

    def _recover(self) -> None:
        for run_id in self.jobs.interrupted():
            reason = "service restarted while the stage was running; restart the stage to resume"
            self.jobs.finish(run_id, Status.partial, reason)
            self.log.warning(reason, extra={"run_id": run_id})
            self._publish_finished(run_id, StageResult(Status.partial, reason))

    def _deleted(self, payload: dict[str, Any]) -> None:
        run_id = payload["run_id"]
        self.cancels.cancel(run_id)
        self.jobs.delete(run_id)
        self._on_deleted(run_id)

    # -- execution ---------------------------------------------------------------------
    def handle_request(self, payload: dict[str, Any]) -> None:
        run_id = payload["run_id"]
        if self.jobs.is_running(run_id):
            self.log.warning("duplicate request ignored", extra={"run_id": run_id})
            return
        self.cancels.discard(run_id)
        cancel = self.cancels.get(run_id)
        ctx = StageContext(self.bus, self.stage, run_id, cancel, self.log)
        self.jobs.start(run_id)
        ctx.emit(f"{self.stage} started")
        try:
            result = self._run_fn(ctx, payload)
        except DelterError as exc:
            result = StageResult(Status.failed, str(exc))
        except Exception as exc:  # noqa: BLE001 - the worker must never die silently
            self.log.exception("stage crashed", extra={"run_id": run_id, "stage": self.stage})
            result = StageResult(Status.failed, f"{type(exc).__name__}: {exc}")
        finally:
            self.cancels.discard(run_id)
        self.jobs.finish(run_id, result.status, result.reason)
        ctx.emit(f"{self.stage} {result.status}: {result.reason}".rstrip(": "), "INFO" if result.status in
                 (Status.completed, Status.cancelled) else "WARNING")
        self._publish_finished(run_id, result)

    def _publish_finished(self, run_id: str, result: StageResult) -> None:
        self.bus.publish(
            STAGE_FINISHED,
            StageFinished(
                run_id=run_id, stage=self.stage, status=result.status, reason=result.reason, stats=result.stats
            ).model_dump(),
        )
