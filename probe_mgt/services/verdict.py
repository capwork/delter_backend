"""Turns what the browser observed after a click into a verdict. Pure function, easy to test."""
from __future__ import annotations

import re
from typing import Any

from common.contracts import Verdict

DELETE_URL_RX = re.compile(r"/(delete|destroy|remove|erase|purge)(\b|/|_|-|\?|$)", re.I)


def decide(state: dict[str, Any]) -> tuple[Verdict, str]:
    """state = window.__delter.collect() output (plus optional `alert_dialogs` seen via Selenium)."""
    dialogs = state.get("dialogs") or []
    modal = state.get("modal")
    blocked = state.get("blocked") or []
    mutations = [b for b in blocked if b.get("kind") != "navigation"]
    navigations = [b for b in blocked if b.get("kind") == "navigation"]

    if dialogs:
        d = dialogs[0]
        return Verdict.native_dialog, f"Browser-native window.{d['type']}() dialog: {d['message']!r}"
    if modal and modal.get("is_confirmation"):
        return Verdict.custom_modal, "Themed confirmation shown (signals: " + ", ".join(modal["signals"]) + ")"
    if mutations:
        m = mutations[0]
        return Verdict.no_confirmation, f"{m['method']} {m['url']} was sent right after the click with no confirmation"
    delete_gets = [n for n in navigations if DELETE_URL_RX.search(n["url"])]
    if delete_gets:
        return Verdict.no_confirmation, f"the click navigates straight to a delete URL ({delete_gets[0]['url']}) with no confirmation"
    if navigations:
        return Verdict.inconclusive, (f"the click navigates to {navigations[0]['url']} (blocked); "
                                      "it may be a confirmation page, review manually")
    if state.get("target_removed"):
        return Verdict.no_confirmation, "the item disappeared immediately after the click with no confirmation"
    return Verdict.inconclusive, "no observable effect after the click"
