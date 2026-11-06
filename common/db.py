"""SQLite access: one connection per thread, WAL, explicit transactions.

Each service owns exactly one database file. Only that service's `repository/` package
uses it. Swapping to Postgres later means re-implementing the repositories, nothing else.
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path: Path | str, schema: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # executescript manages its own transaction, so it must not run inside tx()
        self.conn.executescript(schema)

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
            self._local.depth = 0
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self.conn
        if self._local.depth > 0:  # nested: join the outer transaction
            self._local.depth += 1
            try:
                yield conn
            finally:
                self._local.depth -= 1
            return
        conn.execute("BEGIN IMMEDIATE")
        self._local.depth = 1
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            self._local.depth = 0

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.tx() as conn:
            return conn.execute(sql, params)
