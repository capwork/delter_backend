"""Development launcher: starts every Delter service as its own process.

    uv run python main.py                 # all services (needs Redis, or set DELTER_EVENT_BUS=memory ... see below)
    uv run python main.py --only gateway run_mgt

NOTE: DELTER_EVENT_BUS=memory only works when all services share one process (tests). With separate
processes use Redis (docker compose up redis, or any local Redis on DELTER_REDIS_URL).
For a shared environment use docker compose instead.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from configuration.settings import SERVICE_PORTS, get_settings

FACTORIES = {
    "gateway": "gateway.gateway_main:create_app",
    "run_mgt": "run_mgt.run_mgt_main:create_app",
    "crawl_mgt": "crawl_mgt.crawl_mgt_main:create_app",
    "detect_mgt": "detect_mgt.detect_mgt_main:create_app",
    "probe_mgt": "probe_mgt.probe_mgt_main:create_app",
    "evidence_mgt": "evidence_mgt.evidence_mgt_main:create_app",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Delter services locally")
    parser.add_argument("--only", nargs="*", choices=list(FACTORIES), help="start just these services")
    args = parser.parse_args()
    settings = get_settings()
    if settings.event_bus == "memory":
        print("DELTER_EVENT_BUS=memory cannot connect separate processes; use redis.", file=sys.stderr)
        return 2
    procs: list[subprocess.Popen] = []
    try:
        for name in args.only or FACTORIES:
            cmd = [sys.executable, "-m", "uvicorn", "--factory", FACTORIES[name], "--host", settings.bind_host,
                   "--port", str(SERVICE_PORTS[name]), "--log-level", "warning"]
            procs.append(subprocess.Popen(cmd, env=os.environ.copy()))
            print(f"started {name:<13} http://{settings.bind_host}:{SERVICE_PORTS[name]}")
        print("gateway docs: http://%s:%d/docs (per-service docs on each port). Ctrl+C to stop." %
              (settings.bind_host, SERVICE_PORTS["gateway"]))
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print("a service exited; stopping the rest", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    raise SystemExit(main())
