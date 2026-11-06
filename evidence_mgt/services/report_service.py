"""Builds the per-run report from the other services' data and renders it as JSON, CSV or HTML."""
from __future__ import annotations

import base64
import csv
import html
import io
import json
from typing import Any

from common.clients import ServiceRegistry
from common.errors import NotFound
from common.storage import Storage
from evidence_mgt.repository.evidence_repository import EvidenceRepository

VERDICTS = ["native_dialog", "no_confirmation", "custom_modal", "inconclusive"]
VERDICT_LABEL = {
    "native_dialog": "Browser-native dialog (violation)",
    "no_confirmation": "No confirmation",
    "custom_modal": "Themed confirmation (OK)",
    "inconclusive": "Inconclusive",
}


class ReportService:
    def __init__(self, registry: ServiceRegistry, evidence: EvidenceRepository, storage: Storage) -> None:
        self.registry, self.evidence, self.storage = registry, evidence, storage

    # -- data ------------------------------------------------------------------------------
    def build(self, run_id: str) -> dict[str, Any]:
        run = self.registry.json("run_mgt", "GET", f"/runs/{run_id}")  # 404 -> NotFound
        scans = self.registry.paged("detect_mgt", f"/runs/{run_id}/scans", page_size=1000)
        candidates = self.registry.paged("detect_mgt", f"/runs/{run_id}/candidates", page_size=1000)
        probes = {p["candidate_id"]: p for p in self.registry.paged("probe_mgt", f"/runs/{run_id}/probes", page_size=1000)}
        _, ev_rows = self.evidence.list(run_id, None, None, 100_000, 0)
        ev_by_cand: dict[str, list[dict]] = {}
        for e in ev_rows:
            ev_by_cand.setdefault(e["candidate_id"], []).append(
                {"evidence_id": e["evidence_id"], "kind": e["kind"], "content_type": e["content_type"],
                 "size_bytes": e["size_bytes"], "url": f"/evidence/{e['evidence_id']}/file"})

        pages: dict[int, dict] = {
            s["url_id"]: {"url_id": s["url_id"], "url": s["url"], "scan_status": s["status"],
                          "scan_error": s["error"], "findings": []} for s in scans}
        counts = dict.fromkeys(VERDICTS, 0)
        for c in candidates:
            probe = probes.get(c["candidate_id"])
            verdict = probe["verdict"] if probe and probe["status"] == "completed" else None
            if verdict:
                counts[verdict] += 1
            page = pages.setdefault(c["url_id"], {"url_id": c["url_id"], "url": c["url"], "scan_status": "completed",
                                                  "scan_error": None, "findings": []})
            page["findings"].append({
                "candidate_id": c["candidate_id"], "selector": c["selector"], "via": c["via"], "tag": c["tag"],
                "text": c["text"], "score": c["score"], "reasons": c["reasons"], "hints": c["hints"],
                "element_html": c["html"],
                "probe_status": probe["status"] if probe else "not_probed",
                "verdict": verdict, "reason": probe["reason"] if probe else None,
                "dialogs": probe["dialogs"] if probe else [], "modal": probe["modal"] if probe else None,
                "blocked_requests": probe["blocked"] if probe else [], "probe_error": probe["error"] if probe else None,
                "evidence": sorted(ev_by_cand.get(c["candidate_id"], []), key=lambda e: e["kind"]),
            })
        page_list = sorted(pages.values(), key=lambda p: (
            -sum(1 for f in p["findings"] if f["verdict"] == "native_dialog"), p["url_id"]))
        return {
            "run": {k: run[k] for k in ("run_id", "domain", "host", "start_url", "status", "created_at", "config")},
            "summary": {
                "pages_scanned": sum(1 for s in scans if s["status"] == "completed"),
                "pages_scan_failed": sum(1 for s in scans if s["status"] == "failed"),
                "pages_with_candidates": sum(1 for p in page_list if p["findings"]),
                "candidates": len(candidates),
                "probed": sum(counts.values()),
                **counts,
                "needs_attention": counts["native_dialog"] + counts["no_confirmation"],
            },
            "pages": page_list,
        }

    # -- renderers ---------------------------------------------------------------------------
    def to_csv(self, report: dict[str, Any]) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["run_id", "page_url", "candidate_id", "selector", "via", "element_text", "score", "reasons",
                    "verdict", "reason", "dialog_type", "dialog_message", "modal_signals", "blocked_requests",
                    "before_screenshot", "after_screenshot", "details_file"])
        rid = report["run"]["run_id"]
        for page in report["pages"]:
            for f in page["findings"]:
                ev = {e["kind"]: e["url"] for e in f["evidence"]}
                d = f["dialogs"][0] if f["dialogs"] else {}
                w.writerow([
                    rid, page["url"], f["candidate_id"], f["selector"], json.dumps(f["via"]) if f["via"] else "",
                    f["text"], f["score"], "; ".join(f"{r['signal']}={r['detail']}" for r in f["reasons"]),
                    f["verdict"] or f["probe_status"], f["reason"] or "", d.get("type", ""), d.get("message", ""),
                    ",".join((f["modal"] or {}).get("signals", [])),
                    "; ".join(f"{b['method']} {b['url']}" for b in f["blocked_requests"]),
                    ev.get("before", ""), ev.get("after", ""), ev.get("details", ""),
                ])
        return buf.getvalue()

    def _image_src(self, evidence_id: str, embed: bool) -> str:
        if not embed:
            return f"/evidence/{evidence_id}/file"
        row = self.evidence.get(evidence_id)
        if row is None:
            return ""
        try:
            data = self.storage.read(row["file_path"])
        except NotFound:
            return ""
        return f"data:{row['content_type']};base64,{base64.b64encode(data).decode()}"

    def to_html(self, report: dict[str, Any], embed_images: bool = True) -> str:
        e = html.escape
        run, s = report["run"], report["summary"]
        cards = "".join(
            f'<div class="card {k}"><b>{s[k]}</b><span>{e(VERDICT_LABEL[k])}</span></div>' for k in VERDICTS)
        body = []
        for page in report["pages"]:
            if not page["findings"] and not page["scan_error"]:
                continue
            items = []
            for f in page["findings"]:
                imgs = "".join(
                    f'<figure><img loading="lazy" src="{self._image_src(ev["evidence_id"], embed_images)}" '
                    f'alt="{e(ev["kind"])}"><figcaption>{e(ev["kind"])}</figcaption></figure>'
                    for ev in f["evidence"] if ev["kind"] in ("before", "after"))
                verdict = f["verdict"] or f["probe_status"]
                dlg = "".join(f'<li>window.{e(d["type"])}(): {e(d["message"])}</li>' for d in f["dialogs"])
                blocked = "".join(f'<li>{e(b["method"])} {e(b["url"])}</li>' for b in f["blocked_requests"])
                why = "; ".join(f'{r["signal"]}: {r["detail"]}' for r in f["reasons"])
                items.append(
                    f'<article class="finding {e(verdict)}"><header><span class="pill {e(verdict)}">'
                    f'{e(VERDICT_LABEL.get(verdict, verdict))}</span> <b>{e(f["text"] or f["tag"] or "")}</b> '
                    f'<small>score {f["score"]}</small></header><p class="reason">{e(f["reason"] or f["probe_error"] or "")}</p>'
                    f'<p><code>{e(f["selector"])}</code>'
                    f'{" (revealed via " + e(json.dumps(f["via"])) + ")" if f["via"] else ""}</p>'
                    f'<p class="why">Detected because: {e(why)}</p>'
                    f'{"<ul>" + dlg + "</ul>" if dlg else ""}{"<p>Blocked requests:</p><ul>" + blocked + "</ul>" if blocked else ""}'
                    f'<div class="shots">{imgs}</div></article>')
            err = f'<p class="err">Scan failed: {e(page["scan_error"])}</p>' if page["scan_error"] else ""
            body.append(f'<section><h2>{e(page["url"])}</h2>{err}{"".join(items)}</section>')
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Delter report - {e(run["domain"])}</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1b1f24}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px}} h1{{margin:0 0 4px}} .meta{{color:#59636e;margin-bottom:20px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}}
.card{{background:#fff;border-radius:8px;padding:12px 18px;min-width:150px;box-shadow:0 1px 2px #0002}}
.card b{{display:block;font-size:28px}} .card.native_dialog b{{color:#c0152f}} .card.no_confirmation b{{color:#b45309}}
.card.custom_modal b{{color:#15803d}} section{{background:#fff;border-radius:8px;padding:8px 18px 18px;margin-bottom:18px;box-shadow:0 1px 2px #0002}}
h2{{font-size:16px;word-break:break-all}} .finding{{border-top:1px solid #e5e7eb;padding-top:12px;margin-top:12px}}
.pill{{padding:2px 10px;border-radius:99px;font-size:12px;font-weight:600;background:#e5e7eb}}
.pill.native_dialog{{background:#fde2e4;color:#c0152f}} .pill.no_confirmation{{background:#fef3c7;color:#b45309}}
.pill.custom_modal{{background:#dcfce7;color:#15803d}} code{{background:#f0f1f3;padding:1px 5px;border-radius:4px;word-break:break-all}}
.why,.reason{{color:#59636e;margin:4px 0}} .shots{{display:flex;gap:12px;flex-wrap:wrap}} figure{{margin:0}}
img{{max-width:100%;width:520px;border:1px solid #d0d7de;border-radius:6px}} figcaption{{font-size:12px;color:#59636e}}
.err{{color:#c0152f}}
</style></head><body><main>
<h1>Delete-confirmation report</h1>
<div class="meta">{e(run["domain"])} &middot; run {e(run["run_id"])} &middot; {e(run["status"])} &middot; {e(run["created_at"])}
 &middot; {s["pages_scanned"]} pages scanned, {s["candidates"]} delete controls, {s["needs_attention"]} need attention</div>
<div class="cards">{cards}</div>
{"".join(body) or "<p>No delete controls were found on the scanned pages.</p>"}
</main></body></html>"""
