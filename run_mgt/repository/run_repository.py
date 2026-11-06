from __future__ import annotations

import json
from typing import Any

from common.contracts import STAGE_ORDER, Stage, Status
from common.db import Database
from common.stage import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    domain        TEXT NOT NULL,
    host          TEXT NOT NULL,
    start_url     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    status        TEXT NOT NULL,
    crawl_status  TEXT NOT NULL,
    detect_status TEXT NOT NULL,
    probe_status  TEXT NOT NULL,
    auto_chain    INTEGER NOT NULL DEFAULT 0,
    config        TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_runs_host ON runs(host);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    ts       TEXT NOT NULL,
    stage    TEXT,
    level    TEXT NOT NULL,
    message  TEXT NOT NULL,
    data     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, event_id);
"""


def derive_run_status(stages: list[str]) -> str:
    """A run is completed only when every stage completed. Failed or partial work never looks like success."""
    s = set(stages)
    if Status.running in s:
        return Status.running
    if s == {Status.pending}:
        return Status.pending
    if s == {Status.completed}:
        return Status.completed
    if Status.cancelled in s:
        return Status.cancelled
    if s <= {Status.failed, Status.pending}:
        return Status.failed
    return Status.partial


def _decode(row) -> dict[str, Any]:
    d = dict(row)
    d["config"] = json.loads(d["config"])
    d["summary"] = json.loads(d["summary"])
    d["auto_chain"] = bool(d["auto_chain"])
    return d


class RunRepository:
    SORTS = {
        "created_desc": "created_at DESC",
        "created_asc": "created_at ASC",
        "domain": "domain ASC, created_at DESC",
        "flagged": "COALESCE(json_extract(summary,'$.probe.native_dialog'),0) DESC, created_at DESC",
    }

    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, run_id: str, domain: str, host: str, start_url: str, config: dict) -> dict:
        ts = now()
        self.db.execute(
            "INSERT INTO runs(run_id,domain,host,start_url,created_at,updated_at,status,crawl_status,"
            "detect_status,probe_status,config) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, domain, host, start_url, ts, ts, *([Status.pending] * 4), json.dumps(config)),
        )
        return self.get(run_id)  # type: ignore[return-value]

    def get(self, run_id: str) -> dict | None:
        row = self.db.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        return _decode(row) if row else None

    def list(self, *, q: str | None, status: str | None, host: str | None, sort: str, limit: int, offset: int):
        where, params = [], []
        if q:
            where.append("(domain LIKE ? OR run_id LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if status:
            where.append("status=?")
            params.append(status)
        if host:
            where.append("host=?")
            params.append(host)
        w = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.db.one(f"SELECT COUNT(*) c FROM runs {w}", tuple(params))["c"]
        rows = self.db.query(
            f"SELECT * FROM runs {w} ORDER BY {self.SORTS.get(sort, self.SORTS['created_desc'])} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return total, [_decode(r) for r in rows]

    def active_stage(self, run: dict) -> str | None:
        return next((s.value for s in STAGE_ORDER if run[f"{s.value}_status"] == Status.running), None)

    def claim_stage(self, run_id: str, stage: Stage, auto_chain: bool) -> bool:
        """Atomically mark a stage running unless the run already has a running stage."""
        with self.db.tx() as conn:
            run = conn.execute(
                "SELECT crawl_status,detect_status,probe_status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None or Status.running in tuple(run):
                return False
            self._set_stage(conn, run_id, stage, Status.running, auto_chain=auto_chain)
            return True

    def set_stage_status(self, run_id: str, stage: Stage, status: Status, auto_chain: bool | None = None) -> None:
        with self.db.tx() as conn:
            self._set_stage(conn, run_id, stage, status, auto_chain=auto_chain)

    def _set_stage(self, conn, run_id: str, stage: Stage, status: Status, auto_chain: bool | None) -> None:
        conn.execute(
            f"UPDATE runs SET {stage.value}_status=?, updated_at=? WHERE run_id=?", (status, now(), run_id)
        )
        if auto_chain is not None:
            conn.execute("UPDATE runs SET auto_chain=? WHERE run_id=?", (int(auto_chain), run_id))
        row = conn.execute(
            "SELECT crawl_status,detect_status,probe_status FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        conn.execute("UPDATE runs SET status=? WHERE run_id=?", (derive_run_status(list(row)), run_id))

    def set_auto_chain(self, run_id: str, value: bool) -> None:
        self.db.execute("UPDATE runs SET auto_chain=? WHERE run_id=?", (int(value), run_id))

    def merge_summary(self, run_id: str, stage: Stage, stats: dict) -> None:
        with self.db.tx() as conn:
            row = conn.execute("SELECT summary FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return
            summary = json.loads(row["summary"])
            summary[stage.value] = stats
            conn.execute("UPDATE runs SET summary=? WHERE run_id=?", (json.dumps(summary), run_id))

    def delete(self, run_id: str) -> None:
        self.db.execute("DELETE FROM runs WHERE run_id=?", (run_id,))

    # -- events ------------------------------------------------------------------------
    def add_event(self, run_id: str, stage: str | None, level: str, message: str, data: dict | None = None) -> None:
        if self.db.one("SELECT 1 FROM runs WHERE run_id=?", (run_id,)) is None:
            return  # run deleted while events were in flight
        self.db.execute(
            "INSERT INTO events(run_id,ts,stage,level,message,data) VALUES(?,?,?,?,?,?)",
            (run_id, now(), stage, level.upper(), message, json.dumps(data) if data else None),
        )

    def events(self, run_id: str, after_id: int, level: str | None, limit: int) -> list[dict]:
        sql, params = "SELECT * FROM events WHERE run_id=? AND event_id>?", [run_id, after_id]
        if level:
            sql += " AND level=?"
            params.append(level.upper())
        rows = self.db.query(sql + " ORDER BY event_id LIMIT ?", (*params, limit))
        return [{**dict(r), "data": json.loads(r["data"]) if r["data"] else None} for r in rows]
