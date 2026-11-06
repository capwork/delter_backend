"""RedisBus against fakeredis (same Streams commands as a real Redis)."""
from __future__ import annotations

import time

import fakeredis

from common.events import RedisBus


def make_bus(server):
    return RedisBus("unused", client=fakeredis.FakeRedis(server=server, decode_responses=True))


def wait_for(cond, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_competing_consumers_share_work_and_groups_each_get_everything():
    server = fakeredis.FakeServer()
    bus1, bus2 = make_bus(server), make_bus(server)       # two instances of the same service
    other = make_bus(server)                              # a different service
    a1, a2, b = [], [], []
    bus1.subscribe("crawl.requested", "crawl_mgt", a1.append)
    bus2.subscribe("crawl.requested", "crawl_mgt", a2.append)
    other.subscribe("crawl.requested", "audit", b.append)
    for n in range(6):
        bus1.publish("crawl.requested", {"n": n})
    assert wait_for(lambda: len(a1) + len(a2) == 6 and len(b) == 6)
    assert sorted(m["n"] for m in a1 + a2) == list(range(6))     # no duplicates inside a group
    assert sorted(m["n"] for m in b) == list(range(6))           # other group sees all
    for bus in (bus1, bus2, other):
        bus.close()


def test_broadcast_reaches_every_instance_but_only_new_messages():
    server = fakeredis.FakeServer()
    old = make_bus(server)
    old.publish("run.cancel", {"run_id": "before"})
    x, y = [], []
    bx, by = make_bus(server), make_bus(server)
    bx.subscribe_broadcast("run.cancel", x.append)
    by.subscribe_broadcast("run.cancel", y.append)
    time.sleep(0.3)
    old.publish("run.cancel", {"run_id": "after"})
    assert wait_for(lambda: x and y)
    assert x == [{"run_id": "after"}] and y == [{"run_id": "after"}]
    for bus in (old, bx, by):
        bus.close()


def test_failing_handler_does_not_kill_the_consumer():
    server = fakeredis.FakeServer()
    bus = make_bus(server)
    seen = []

    def handler(p):
        if p["n"] == 0:
            raise RuntimeError("boom")
        seen.append(p["n"])

    bus.subscribe("t", "svc", handler)
    bus.publish("t", {"n": 0})
    bus.publish("t", {"n": 1})
    assert wait_for(lambda: seen == [1])
    assert bus.ping()
    bus.close()
