"""JSON-lines logging to stderr and <data_dir>/logs/<service>.log."""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler

from configuration.settings import Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "service": getattr(record, "service", record.name),
            "msg": record.getMessage(),
        }
        for key in ("run_id", "stage"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def setup_logging(service: str, settings: Settings) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_delter_service", None) == service for h in root.handlers):
        return
    root.setLevel(settings.log_level.upper())
    fmt = JsonFormatter()
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        RotatingFileHandler(log_dir / f"{service}.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
    ]
    for h in handlers:
        h.setFormatter(fmt)
        h._delter_service = service  # type: ignore[attr-defined]
        root.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
