from __future__ import annotations

import json
import uuid

from common.db import Database
from common.stage import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id  TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    kind         TEXT NOT NULL,            -- before|after|details
    file_path    TEXT NOT NULL,            -- relative to the storage root
    content_type TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    meta         TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    UNIQUE(run_id, candidate_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence(run_id, candidate_id);
"""


def _decode(row) -> dict:
    d = dict(row)
    d["meta"] = json.loads(d["meta"])
    return d


class EvidenceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, run_id: str, candidate_id: str, kind: str, file_path: str, content_type: str, size: int,
               meta: dict) -> dict:
        with self.db.tx() as conn:  # a re-probe replaces the earlier file but keeps the evidence id
            conn.execute(
                "INSERT INTO evidence(evidence_id,run_id,candidate_id,kind,file_path,content_type,size_bytes,meta,"
                "created_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,candidate_id,kind) DO UPDATE SET "
                "file_path=excluded.file_path, content_type=excluded.content_type, size_bytes=excluded.size_bytes, "
                "meta=excluded.meta, created_at=excluded.created_at",
                ("ev_" + uuid.uuid4().hex[:10], run_id, candidate_id, kind, file_path, content_type, size,
                 json.dumps(meta), now()),
            )
            row = conn.execute("SELECT * FROM evidence WHERE run_id=? AND candidate_id=? AND kind=?",
                               (run_id, candidate_id, kind)).fetchone()
        return _decode(row)

    def get(self, evidence_id: str) -> dict | None:
        row = self.db.one("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,))
        return _decode(row) if row else None

    def list(self, run_id: str, candidate_id: str | None, kind: str | None, limit: int, offset: int):
        where, params = "WHERE run_id=?", [run_id]
        if candidate_id:
            where += " AND candidate_id=?"
            params.append(candidate_id)
        if kind:
            where += " AND kind=?"
            params.append(kind)
        total = self.db.one(f"SELECT COUNT(*) c FROM evidence {where}", tuple(params))["c"]
        rows = self.db.query(f"SELECT * FROM evidence {where} ORDER BY candidate_id, kind LIMIT ? OFFSET ?",
                             (*params, limit, offset))
        return total, [_decode(r) for r in rows]

    def size_bytes(self, run_id: str) -> int:
        return self.db.one("SELECT COALESCE(SUM(size_bytes),0) s FROM evidence WHERE run_id=?", (run_id,))["s"]

    def delete_run(self, run_id: str) -> None:
        self.db.execute("DELETE FROM evidence WHERE run_id=?", (run_id,))
