from __future__ import annotations

from common.db import Database
from common.stage import JOBS_SCHEMA, now

SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS urls (
    url_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    url             TEXT NOT NULL,
    normalized_url  TEXT NOT NULL,
    final_url       TEXT,
    parent_url_id   INTEGER,
    depth           INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'discovered',  -- discovered|visited|failed|skipped
    http_status     INTEGER,
    error           TEXT,
    title           TEXT,
    discovered_at   TEXT NOT NULL,
    visited_at      TEXT,
    UNIQUE(run_id, normalized_url)
);
CREATE INDEX IF NOT EXISTS idx_urls_queue ON urls(run_id, status, depth, url_id);
"""
    + JOBS_SCHEMA
)


class UrlRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, run_id: str, url: str, normalized: str, depth: int, parent: int | None) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO urls(run_id,url,normalized_url,parent_url_id,depth,discovered_at) VALUES(?,?,?,?,?,?)",
            (run_id, url, normalized, parent, depth, now()),
        )
        return cur.rowcount > 0

    def known(self, run_id: str, normalized: str) -> bool:
        return self.db.one("SELECT 1 FROM urls WHERE run_id=? AND normalized_url=?", (run_id, normalized)) is not None

    def next_discovered(self, run_id: str) -> dict | None:
        row = self.db.one(
            "SELECT * FROM urls WHERE run_id=? AND status='discovered' ORDER BY depth, url_id LIMIT 1", (run_id,)
        )
        return dict(row) if row else None

    def mark(self, url_id: int, status: str, *, http_status: int | None = None, final_url: str | None = None,
             error: str | None = None, title: str | None = None) -> None:
        self.db.execute(
            "UPDATE urls SET status=?, http_status=?, final_url=?, error=?, title=?, visited_at=? WHERE url_id=?",
            (status, http_status, final_url, error, title, now(), url_id),
        )

    def counts(self, run_id: str) -> dict[str, int]:
        rows = self.db.query("SELECT status, COUNT(*) c FROM urls WHERE run_id=? GROUP BY status", (run_id,))
        return {r["status"]: r["c"] for r in rows}

    def list(self, run_id: str, status: str | None, limit: int, offset: int) -> tuple[int, list[dict]]:
        where, params = "WHERE run_id=?", [run_id]
        if status:
            where += " AND status=?"
            params.append(status)
        total = self.db.one(f"SELECT COUNT(*) c FROM urls {where}", tuple(params))["c"]
        rows = self.db.query(f"SELECT * FROM urls {where} ORDER BY depth, url_id LIMIT ? OFFSET ?",
                             (*params, limit, offset))
        return total, [dict(r) for r in rows]

    def get(self, run_id: str, url_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM urls WHERE run_id=? AND url_id=?", (run_id, url_id))
        return dict(row) if row else None

    def delete_run(self, run_id: str) -> None:
        self.db.execute("DELETE FROM urls WHERE run_id=?", (run_id,))
