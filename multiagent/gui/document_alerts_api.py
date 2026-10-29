# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""``GET /gui/api/document-alerts``: the document alerts and quarantines of runs, for the GUI extension (BDI-T05).

Read-only. It decides nothing and applies no policy: it turns the events the engine already records
(``document_quarantined``, ``document_review_requested`` and ``document_review_resolved``, BDI-T01/T02/T04) into a
short list the screen can draw, with what a person needs to see: the source, the quarantined fragments (the short
excerpt the event carries, never the original text, which stays in the blob store), the policy that was applied and
whether a review is still waiting. Nothing here settles a review: that is the run approval path, and only a person
uses it.

A review is open until a *later* ``approved`` resolution with the same four keys settles it (``document_review``,
the rule the content guard uses); a ``rejected`` one is shown as rejected, and a request without a usable key stays
open because nothing can settle it. ``tests/test_gui_document_alerts.py`` checks that this reading agrees with
``document_review.unresolved_requests``.

``?run_id=`` reads one run through the run service (a bundle run lives in another store, and an unknown run is a 404
like on ``/gui/api/runs/{id}``). Without it the answer lists the latest alerts of the system store only, like the base
event stream (``events``); a bundle run is read by opening it. ``?limit=`` bounds the list (newest first).
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter, Query

from ..document_review import DECISION_APPROVED, DECISION_REJECTED, review_key
from ..document_trust import (
    ACTION_PAUSE,
    ACTION_QUARANTINE,
    EVENT_QUARANTINED,
    EVENT_REVIEW_REQUESTED,
    EVENT_REVIEW_RESOLVED,
)

STATE_QUARANTINED = "quarantined"       # the content was cut and the work went on (policy quarantine_warn)
STATE_OPEN = "open"                     # a review is waiting for a person (policy pause)
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_TEXT = 200
MAX_FRAGMENTS = 20
MAX_SIGNALS = 50
MAX_ORIGINS = 10
MAX_DEPTH = 4
_ALERT_EVENTS = (EVENT_QUARANTINED, EVENT_REVIEW_REQUESTED)
_ACTION_OF = {EVENT_QUARANTINED: ACTION_QUARANTINE, EVENT_REVIEW_REQUESTED: ACTION_PAUSE}


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _int(value: Any, default: int = 0) -> int:
    return value if type(value) is int else default


def _texts(value: Any, limit: int) -> list[str]:
    return [_text(item, 80) for item in value[:limit] if isinstance(item, str)] if isinstance(value, list) else []


def _origins(source: Mapping[str, Any], depth: int = 0) -> list[dict[str, str]]:
    """What a derived source comes from (``derived_from`` of its provenance), flattened and bounded."""
    found: list[dict[str, str]] = []
    parents = source.get("derived_from")
    for parent in parents if isinstance(parents, list) and depth < MAX_DEPTH else ():
        if isinstance(parent, Mapping) and len(found) < MAX_ORIGINS:
            found.append({"kind": _text(parent.get("kind"), 40), "label": _text(parent.get("label")),
                          "ref": _text(parent.get("ref"))})
            found.extend(_origins(parent, depth + 1)[:MAX_ORIGINS - len(found)])
    return found


def _source(payload: Mapping[str, Any]) -> tuple[dict[str, str], list[dict[str, str]]]:
    raw = payload.get("source")
    source = raw if isinstance(raw, Mapping) else {}
    shown = {"kind": _text(source.get("kind"), 40) or "unknown", "trust": _text(source.get("trust"), 20) or "untrusted",
             "ref": _text(source.get("ref")), "sha256": _text(source.get("sha256"), 64),
             "label": _text(source.get("label"))}
    return shown, _origins(source)


def _fragments(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("fragments")
    shown = []
    for item in raw[:MAX_FRAGMENTS] if isinstance(raw, list) else ():
        if isinstance(item, Mapping):
            excerpt = _text(item.get("excerpt"))
            shown.append({"ref": _text(item.get("ref"), 100), "sha256": _text(item.get("sha256"), 64),
                          "start": _int(item.get("start")), "end": _int(item.get("end")),
                          "length": _int(item.get("length"), len(excerpt)), "signals": _texts(item.get("signals"), 20),
                          "excerpt": excerpt})
    return shown


def _signals(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    raw = payload.get("signals")
    return [{"code": _text(item.get("code"), 60), "severity": _text(item.get("severity"), 20),
             "region": _text(item.get("region"), 30)}
            for item in raw[:MAX_SIGNALS] if isinstance(item, Mapping)] if isinstance(raw, list) else []


def alerts_from_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The alerts in ``events`` (``{"id", "run_id", "ts", "event_type", "payload"}``, the events of a run in order),
    newest first. An event whose payload is not a mapping is skipped, and every value that is shown is read defensively."""
    by_run: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        if isinstance(event, Mapping) and isinstance(event.get("payload"), Mapping):
            by_run[str(event.get("run_id") or "")].append(event)
    alerts: list[dict[str, Any]] = []
    for run_id, run_events in by_run.items():
        resolutions: dict[Any, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
        for position, event in enumerate(run_events):
            key = review_key(event["payload"])
            if event.get("event_type") == EVENT_REVIEW_RESOLVED and key is not None:
                resolutions[key].append((position, event["payload"]))
        for position, event in enumerate(run_events):
            kind, payload = event.get("event_type"), event["payload"]
            if kind not in _ALERT_EVENTS:
                continue
            state, settled = STATE_QUARANTINED, None
            if kind == EVENT_REVIEW_REQUESTED:
                key = review_key(payload)
                later = [item for at, item in resolutions.get(key, ()) if at > position] if key is not None else []
                approved = [item for item in later if item.get("decision") == DECISION_APPROVED]
                rejected = [item for item in later if item.get("decision") == DECISION_REJECTED]
                settled = approved[-1] if approved else (rejected[-1] if rejected else None)
                state = STATE_APPROVED if approved else STATE_REJECTED if rejected else STATE_OPEN
            source, origins = _source(payload)
            fragments = _fragments(payload)
            alerts.append({
                "id": _int(event.get("id"), _int(event.get("seq"))), "run_id": run_id, "ts": _text(event.get("ts"), 40),
                "event_type": kind, "state": state,
                "policy": _text(payload.get("policy"), 40),
                "action": _text(payload.get("action"), 20) or _ACTION_OF[kind],
                "step_id": _text(payload.get("step_id"), 120), "attempt": _int(payload.get("attempt")),
                "part": _text(payload.get("part"), 120), "part_kind": _text(payload.get("part_kind"), 60),
                "source": source, "origins": origins, "original_sha256": _text(payload.get("original_sha256"), 64),
                "detector_version": _int(payload.get("detector_version")),
                "max_severity": _text(payload.get("max_severity"), 20),
                "signals": _signals(payload), "fragments": fragments,
                "fragment_count": _int(payload.get("fragment_count"), len(fragments)),
                "decision": settled.get("decision") if state in (STATE_APPROVED, STATE_REJECTED) else None,
                "decided_by": _text(settled.get("by"), 100) if settled is not None else "",
                "channel": _text(settled.get("channel"), 20) if settled is not None else "",
            })
    alerts.sort(key=lambda alert: alert["id"], reverse=True)
    return alerts


def _rows_to_events(rows) -> list[dict[str, Any]]:
    events = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        events.append({"id": row["id"], "run_id": row["run_id"], "ts": row["ts"], "event_type": row["event_type"],
                       "payload": payload})
    return events


def latest_system_events(store, limit: int) -> list[dict[str, Any]]:
    """The latest ``limit`` alert events of the system store, and every alert and review event of the runs they belong to."""
    marks = ",".join("?" for _ in _ALERT_EVENTS)
    with store.db.connect() as conn:
        latest = conn.execute(
            f"SELECT DISTINCT run_id FROM (SELECT id,run_id FROM run_events WHERE event_type IN ({marks}) "
            "ORDER BY id DESC LIMIT ?)", (*_ALERT_EVENTS, limit)).fetchall()
        run_ids = sorted(row["run_id"] for row in latest)
        if not run_ids:
            return []
        runs = ",".join("?" for _ in run_ids)
        return _rows_to_events(conn.execute(
            f"SELECT id,run_id,ts,event_type,payload_json FROM run_events WHERE run_id IN ({runs}) "
            "AND event_type IN (?,?,?) ORDER BY id", (*run_ids, *_ALERT_EVENTS, EVENT_REVIEW_RESOLVED)).fetchall())


def add_document_alerts_routes(api: APIRouter, services) -> None:
    """Add ``GET /document-alerts`` to the GUI router, which already requires the token."""

    @api.get("/document-alerts")
    def document_alerts(run_id: str | None = Query(None, min_length=1, max_length=120),
                        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)):
        if run_id is None:
            events = latest_system_events(services.system.store, limit + 1)
        else:
            events = [{**event, "run_id": run_id} for event in services.runs.get(run_id)["events"]]
        alerts = alerts_from_events(events)
        return {"alerts": alerts[:limit], "truncated": len(alerts) > limit, "scope": "system" if run_id is None else "run"}
