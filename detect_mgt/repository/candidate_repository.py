from __future__ import annotations

import hashlib
import json
import uuid

from common.db import Database
from common.stage import JOBS_SCHEMA, now

SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    url_id       INTEGER NOT NULL,
    url          TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    selector     TEXT NOT NULL,
    via          TEXT NOT NULL DEFAULT '[]',   -- steps to reveal the control: [{action, selector}]
    tag          TEXT,
    text         TEXT,
    aria_label   TEXT,
    title        TEXT,
    href         TEXT,
    score        REAL NOT NULL,
    reasons      TEXT NOT NULL,                -- why the heuristics flagged it
    hints        TEXT NOT NULL DEFAULT '[]',   -- e.g. inline_confirm_call, data_confirm_attr
    html         TEXT,
    rect         TEXT,
    detected_at  TEXT NOT NULL,
    UNIQUE(run_id, url_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_cand_run ON candidates(run_id, score DESC);
CREATE TABLE IF NOT EXISTS page_scans (
    run_id     TEXT NOT NULL,
    url_id     INTEGER NOT NULL,
    url        TEXT NOT NULL,
    mode       TEXT NOT NULL,
    status     TEXT NOT NULL,                  -- completed|failed
    candidates INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    scanned_at TEXT NOT NULL,
    PRIMARY KEY(run_id, url_id)
);
"""
    + JOBS_SCHEMA
)

JSON_COLS = ("via", "reasons", "hints", "rect")


def _decode(row) -> dict:
    d = dict(row)
    for c in JSON_COLS:
        d[c] = json.loads(d[c]) if d.get(c) else ([] if c != "rect" else None)
    return d


def fingerprint(selector: str, via: list[dict]) -> str:
    return hashlib.sha1((selector + json.dumps(via, sort_keys=True)).encode()).hexdigest()[:16]


class CandidateRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, run_id: str, url_id: int, url: str, c: dict, via: list[dict]) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO candidates(candidate_id,run_id,url_id,url,fingerprint,selector,via,tag,text,"
            "aria_label,title,href,score,reasons,hints,html,rect,detected_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "cand_" + uuid.uuid4().hex[:10], run_id, url_id, url, fingerprint(c["selector"], via), c["selector"],
                json.dumps(via), c.get("tag"), c.get("text"), c.get("aria_label"), c.get("title"), c.get("href"),
                c["score"], json.dumps(c["reasons"]), json.dumps(c.get("hints", [])), c.get("html"),
                json.dumps(c.get("rect")), now(),
            ),
        )
        return cur.rowcount > 0

    def list(self, run_id: str, url_id: int | None, min_score: float, limit: int, offset: int):
        where, params = "WHERE run_id=? AND score>=?", [run_id, min_score]
        if url_id is not None:
            where += " AND url_id=?"
            params.append(url_id)
        total = self.db.one(f"SELECT COUNT(*) c FROM candidates {where}", tuple(params))["c"]
        rows = self.db.query(
            f"SELECT * FROM candidates {where} ORDER BY url_id, score DESC, candidate_id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return total, [_decode(r) for r in rows]

    def get(self, candidate_id: str) -> dict | None:
        row = self.db.one("SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,))
        return _decode(row) if row else None

    def count(self, run_id: str) -> int:
        return self.db.one("SELECT COUNT(*) c FROM candidates WHERE run_id=?", (run_id,))["c"]

    # -- page scans (resume + "which pages had nothing") ----------------------------------
    def scanned_url_ids(self, run_id: str) -> set[int]:
        rows = self.db.query("SELECT url_id FROM page_scans WHERE run_id=? AND status='completed'", (run_id,))
        return {r["url_id"] for r in rows}

    def save_scan(self, run_id: str, url_id: int, url: str, mode: str, status: str, candidates: int,
                  error: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO page_scans(run_id,url_id,url,mode,status,candidates,error,scanned_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id,url_id) DO UPDATE SET mode=excluded.mode,status=excluded.status,"
            "candidates=excluded.candidates,error=excluded.error,scanned_at=excluded.scanned_at",
            (run_id, url_id, url, mode, status, candidates, error, now()),
        )

    def scans(self, run_id: str, limit: int, offset: int):
        total = self.db.one("SELECT COUNT(*) c FROM page_scans WHERE run_id=?", (run_id,))["c"]
        rows = self.db.query("SELECT * FROM page_scans WHERE run_id=? ORDER BY url_id LIMIT ? OFFSET ?",
                             (run_id, limit, offset))
        return total, [dict(r) for r in rows]

    def delete_run(self, run_id: str) -> None:
        with self.db.tx() as conn:
            conn.execute("DELETE FROM candidates WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM page_scans WHERE run_id=?", (run_id,))
