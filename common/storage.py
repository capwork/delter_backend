"""File storage behind an interface. Today: a local directory. Tomorrow: S3/MinIO."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from common.errors import NotFound


class Storage(Protocol):
    def save(self, rel_path: str, data: bytes) -> str: ...
    def read(self, rel_path: str) -> bytes: ...
    def exists(self, rel_path: str) -> bool: ...
    def delete_prefix(self, rel_prefix: str) -> None: ...
    def size_prefix(self, rel_prefix: str) -> int: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root != p and self.root not in p.parents:
            raise NotFound("invalid path")
        return p

    def save(self, rel_path: str, data: bytes) -> str:
        p = self._resolve(rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return rel_path

    def read(self, rel_path: str) -> bytes:
        p = self._resolve(rel_path)
        if not p.is_file():
            raise NotFound("file not found")
        return p.read_bytes()

    def exists(self, rel_path: str) -> bool:
        return self._resolve(rel_path).is_file()

    def delete_prefix(self, rel_prefix: str) -> None:
        shutil.rmtree(self._resolve(rel_prefix), ignore_errors=True)

    def size_prefix(self, rel_prefix: str) -> int:
        p = self._resolve(rel_prefix)
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0
