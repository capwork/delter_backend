# Delter backend

Finds delete actions that confirm with the **browser's native dialog** (`window.confirm`) instead of the
platform's own themed confirmation. Internal QA tool; microservices on FastAPI.

## What it does

1. **crawl** - browser-based breadth-first crawl of one site (finds JS-rendered links).
2. **detect** - heuristics find every control that probably deletes something (text, aria-label, icon classes,
   `data-*`, URL, `DELETE` method...). Each gets a score and the reasons for it. Controls hidden in menus
   (kebab / "more") are found by opening them; *aggressive* mode also expands accordions/tabs and hovers rows.
3. **probe** - clicks each control and records what happened: `native_dialog` (the violation), `custom_modal`,
   `no_confirmation` or `inconclusive`, with before/after screenshots, dialog text and blocked requests.

Reports for every run: JSON, CSV and a self-contained HTML page.

## Safety (always on)

A probe never confirms anything. The page hook answers every native `confirm()` with Cancel and blocks
non-GET `fetch`/XHR/beacon, form submits and link navigations after the click; Chrome refuses `*/delete*`,
`*/destroy*`, `*/remove*` requests; the crawler never GETs delete/logout-looking URLs. This lowers the risk but
cannot make it zero - **point it at staging or data you can afford to lose.**

## Services

| Service | Port | Owns |
|---|---|---|
| gateway | 8000 | single entry point for the frontend (routing + CORS), aggregated `/health` |
| run_mgt | 8001 | runs, config, pipeline orchestration, cancel, unified event log |
| crawl_mgt | 8002 | discovered URLs |
| detect_mgt | 8003 | delete-control candidates, per-page scan records |
| probe_mgt | 8004 | click results and verdicts |
| evidence_mgt | 8005 | screenshots/details files, reports |

Each service: `controllers/ models/ repository/ services/ <name>_main.py`, its own SQLite file, no imports from
other services. They talk through REST (`common/clients.py`) and events on Redis Streams (`common/events.py`):

```
POST /runs/{id}/start -> run_mgt --crawl.requested--> crawl_mgt --stage.finished--> run_mgt
   --detect.requested--> detect_mgt --stage.finished--> run_mgt --probe.requested--> probe_mgt --stage.finished--> run_mgt
```
`run.event` (log lines), `run.cancel` (broadcast) and `run.deleted` (each service drops its data) complete the set.
Shared code: `configuration/` (settings, logging) and `common/` (contracts, db, events, storage, clients, stage runner).

## Run it

```bash
uv sync
uv run pytest                      # unit tests + real-Chrome end-to-end (needs Chrome)
uv run python main.py              # all services locally; needs Redis on DELTER_REDIS_URL
docker compose up --build          # everything incl. Redis; gateway on :8000
```
Swagger UI: http://localhost:8000/docs (gateway) - each service also serves `/docs` on its own port.

## API (via the gateway)

| | |
|---|---|
| `POST /runs` `{domain, config?}` | create a run (config: see `common/contracts.py::RunConfig`) |
| `POST /runs/{id}/start` | whole pipeline; `/crawl` `/detect` `/probe` run one stage; `/cancel` stops it |
| `GET /runs`, `/runs/{id}`, `/runs/{id}/events?after_id=` | status, summary, event log (poll) |
| `GET /runs/{id}/urls` `/candidates` `/scans` `/probes` | data of each stage (`{total, items}`, `limit`/`offset`) |
| `GET /runs/{id}/evidence`, `/evidence/{id}/file` | screenshots (PNG) and `details.json` |
| `GET /runs/{id}/report` `/report/csv` `/report/html` | the report |
| `DELETE /runs/{id}` | delete the run in every service |

Run status is `completed` only if crawl, detect and probe all completed (a single 404 page makes the crawl, and so
the run, `partial`). A `native_dialog` verdict is a finding to fix; `no_confirmation` needs a human look.

## Known limits

No auth (crawl or API); confirmations inside iframes/shadow DOM are not seen; a delete button that only exists
for specific data is found only if that data is on the page; very custom UI toolkits may need extra keywords
(`extra_strong_keywords` in the run config); the safety layer is best-effort (see above).

## Scaling later

Services are stateless apart from their database and the evidence folder, configured by environment variables.
To run several instances of a worker (`DELTER_WORKER_CONCURRENCY`, `--scale probe_mgt=3`): replace the SQLite
repositories with Postgres (only `repository/` changes) and `LocalStorage` with S3/MinIO (same `Storage`
interface); Redis consumer groups already split the work and cancel/delete are broadcast to every instance.
