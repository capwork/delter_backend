from __future__ import annotations

import json
import uuid

from common.db import Database
from common.stage import JOBS_SCHEMA, now

SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS probes (
    probe_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    candidate_id    TEXT NOT NULL,
    url             TEXT NOT NULL,
    selector        TEXT NOT NULL,
    status          TEXT NOT NULL,              -- completed|failed
    verdict         TEXT,                       -- native_dialog|custom_modal|no_confirmation|inconclusive
    reason          TEXT,
    dialogs         TEXT NOT NULL DEFAULT '[]', -- native dialogs: [{type, message}]
    modal           TEXT,                       -- themed confirmation details
    blocked         TEXT NOT NULL DEFAULT '[]', -- requests the safety layer stopped
    final_url       TEXT,
    evidence_ids    TEXT NOT NULL DEFAULT '[]',
    error           TEXT,
    attempts        INTEGER NOT NULL DEFAULT 1,
    duration_ms     INTEGER,
    probed_at       TEXT NOT NULL,
    UNIQUE(run_id, candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_probe_run ON probes(run_id, verdict);
"""
    + JOBS_SCHEMA
)
JSON_COLS = {"dialogs": [], "modal": None, "blocked": [], "evidence_ids": []}


def _decode(row) -> dict:
    d = dict(row)
    for c, default in JSON_COLS.items():
        d[c] = json.loads(d[c]) if d.get(c) else default
    return d


class ProbeRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, run_id: str, cand: dict, *, status: str, verdict: str | None, reason: str | None,
             dialogs: list, modal: dict | None, blocked: list, final_url: str | None, evidence_ids: list[str],
             error: str | None, attempts: int, duration_ms: int) -> None:
        self.db.execute(
            "INSERT INTO probes(probe_id,run_id,candidate_id,url,selector,status,verdict,reason,dialogs,modal,blocked,"
            "final_url,evidence_ids,error,attempts,duration_ms,probed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id,candidate_id) DO UPDATE SET status=excluded.status, verdict=excluded.verdict, "
            "reason=excluded.reason, dialogs=excluded.dialogs, modal=excluded.modal, blocked=excluded.blocked, "
            "final_url=excluded.final_url, evidence_ids=excluded.evidence_ids, error=excluded.error, "
            "attempts=excluded.attempts, duration_ms=excluded.duration_ms, probed_at=excluded.probed_at",
            (
                "probe_" + uuid.uuid4().hex[:10], run_id, cand["candidate_id"], cand["url"], cand["selector"], status,
                verdict, reason, json.dumps(dialogs), json.dumps(modal) if modal else None, json.dumps(blocked),
                final_url, json.dumps(evidence_ids), error, attempts, duration_ms, now(),
            ),
        )

    def completed_candidate_ids(self, run_id: str) -> set[str]:
        rows = self.db.query("SELECT candidate_id FROM probes WHERE run_id=? AND status='completed'", (run_id,))
        return {r["candidate_id"] for r in rows}

    def list(self, run_id: str, verdict: str | None, status: str | None, limit: int, offset: int):
        where, params = "WHERE run_id=?", [run_id]
        if verdict:
            where += " AND verdict=?"
            params.append(verdict)
        if status:
            where += " AND status=?"
            params.append(status)
        total = self.db.one(f"SELECT COUNT(*) c FROM probes {where}", tuple(params))["c"]
        rows = self.db.query(f"SELECT * FROM probes {where} ORDER BY probed_at, probe_id LIMIT ? OFFSET ?",
                             (*params, limit, offset))
        return total, [_decode(r) for r in rows]

    def get(self, run_id: str, candidate_id: str) -> dict | None:
        row = self.db.one("SELECT * FROM probes WHERE run_id=? AND candidate_id=?", (run_id, candidate_id))
        return _decode(row) if row else None

    def counts(self, run_id: str) -> dict[str, int]:
        out = {"probed": 0, "failed": 0, "native_dialog": 0, "custom_modal": 0, "no_confirmation": 0,
               "inconclusive": 0}
        for r in self.db.query("SELECT status, verdict, COUNT(*) c FROM probes WHERE run_id=? GROUP BY status, verdict",
                               (run_id,)):
            if r["status"] == "failed":
                out["failed"] += r["c"]
            else:
                out["probed"] += r["c"]
                out[r["verdict"]] += r["c"]
        return out

    def delete_run(self, run_id: str) -> None:
        self.db.execute("DELETE FROM probes WHERE run_id=?", (run_id,))
