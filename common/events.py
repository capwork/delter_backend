"""Event bus between services.

* `RedisBus`   - Redis Streams. Competing consumers per (topic, group) so extra instances of a
                 service share the work; `subscribe_broadcast` reaches every instance (cancel).
* `MemoryBus`  - in-process, for tests and single-process development.

Handlers must never raise to the bus: failures are logged and the message is acknowledged
(the stage runner records the failure in its own database and reports it via `stage.finished`).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from configuration.settings import Settings

log = logging.getLogger(__name__)
Handler = Callable[[dict[str, Any]], None]


class EventBus(Protocol):
    def publish(self, topic: str, payload: dict[str, Any]) -> None: ...
    def subscribe(self, topic: str, group: str, handler: Handler, concurrency: int = 1) -> None: ...
    def subscribe_broadcast(self, topic: str, handler: Handler) -> None: ...
    def ping(self) -> bool: ...
    def close(self) -> None: ...


def _safe(handler: Handler, payload: dict[str, Any], topic: str) -> None:
    try:
        handler(payload)
    except Exception:  # noqa: BLE001 - a bad message must not kill the consumer loop
        log.exception("event handler failed for topic %s", topic)


class MemoryBus:
    def __init__(self) -> None:
        self._subs: list[tuple[str, str | None, queue.Queue]] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def _spawn(self, topic: str, q: queue.Queue, handler: Handler) -> None:
        def loop() -> None:
            while not self._stop.is_set():
                try:
                    payload = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                _safe(handler, payload, topic)

        t = threading.Thread(target=loop, daemon=True, name=f"bus-{topic}")
        t.start()
        self._threads.append(t)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        data = json.loads(json.dumps(payload, default=str))  # same serialisation rules as Redis
        with self._lock:
            targets = [q for t, _, q in self._subs if t == topic]
        for q in targets:
            q.put(data)

    def subscribe(self, topic: str, group: str, handler: Handler, concurrency: int = 1) -> None:
        # one queue per group, `concurrency` competing consumer threads on it
        with self._lock:
            existing = next((q for t, g, q in self._subs if t == topic and g == group), None)
            q = existing or queue.Queue()
            if existing is None:
                self._subs.append((topic, group, q))
        for _ in range(max(1, concurrency)):
            self._spawn(topic, q, handler)

    def subscribe_broadcast(self, topic: str, handler: Handler) -> None:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.append((topic, None, q))
        self._spawn(topic, q, handler)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        self._stop.set()


class RedisBus:
    def __init__(self, url: str, maxlen: int = 10_000, client: Any = None) -> None:
        import redis

        self._r = client or redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=5)
        self._maxlen = maxlen
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    @staticmethod
    def _key(topic: str) -> str:
        return f"delter:{topic}"

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self._r.xadd(self._key(topic), {"data": json.dumps(payload, default=str)},
                     maxlen=self._maxlen, approximate=True)

    def _start(self, target: Callable[[], None], name: str) -> None:
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        self._threads.append(t)

    def subscribe(self, topic: str, group: str, handler: Handler, concurrency: int = 1) -> None:
        import redis

        key = self._key(topic)
        try:
            self._r.xgroup_create(key, group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        for _ in range(max(1, concurrency)):
            consumer = f"{group}-{uuid.uuid4().hex[:8]}"

            def loop(consumer: str = consumer) -> None:
                while not self._stop.is_set():
                    try:
                        resp = self._r.xreadgroup(group, consumer, {key: ">"}, count=1, block=1000)
                    except Exception:  # noqa: BLE001
                        log.exception("redis read failed for %s; retrying", topic)
                        self._stop.wait(2)
                        continue
                    for _, messages in resp or []:
                        for msg_id, fields in messages:
                            _safe(handler, json.loads(fields["data"]), topic)
                            self._r.xack(key, group, msg_id)

            self._start(loop, f"bus-{topic}-{consumer}")

    def subscribe_broadcast(self, topic: str, handler: Handler) -> None:
        key = self._key(topic)
        last_id = "$"

        def loop() -> None:
            nonlocal last_id
            while not self._stop.is_set():
                try:
                    resp = self._r.xread({key: last_id}, count=10, block=1000)
                except Exception:  # noqa: BLE001
                    log.exception("redis read failed for %s; retrying", topic)
                    self._stop.wait(2)
                    continue
                for _, messages in resp or []:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        _safe(handler, json.loads(fields["data"]), topic)

        # resolve "$" now so messages published between subscribe and first read are not lost
        info = self._r.xinfo_stream(key) if self._r.exists(key) else None
        last_id = info["last-generated-id"] if info else "0-0"
        self._start(loop, f"bus-{topic}-broadcast")

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=3)
        self._r.close()


def create_bus(settings: Settings) -> EventBus:
    if settings.event_bus == "memory":
        return MemoryBus()
    return RedisBus(settings.redis_url, settings.stream_maxlen)
