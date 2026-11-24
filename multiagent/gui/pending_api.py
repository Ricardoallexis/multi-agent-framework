# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only aggregation for the GUI pending inbox."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter

from .continuity_api import build_cards

TEAM_FILE = "team.yaml"
MAX_ITEMS = 500


def _short(value: Any, fallback: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return fallback
    return text if len(text) <= 160 else text[:157].rstrip() + "…"


def _item(kind: str, source_id: str, title: str, reason: str, since: Any, href: str) -> dict[str, Any]:
    return {"id": f"{kind}:{source_id}", "type": kind, "title": _short(title, kind),
            "reason": _short(reason, ""), "since": since if isinstance(since, str) else None, "href": href}


# MEM-T01: what a person reads in «Pendientes» for the two memory approvals (the entry title is data, shown as text)
_MEMORY_REASONS = {"memory_trust": "Confirma que este apunte es tuyo para que se trate como fiable",
                   "memory_delete": "Confirma que se borre este apunte; no se puede deshacer"}
# MEM-T02: a new apunte that contradicts one the person confirmed
_MEMORY_CLASH_REASON = ("Este apunte contradice uno que confirmaste. Si lo apruebas pasa a fiable "
                        "y el tuyo queda en Pendientes para borrarlo")


def _approval_item(approval: dict[str, Any]) -> dict[str, Any]:
    approval_id = str(approval["id"])
    kind = str(approval["kind"])
    title, reason = "Aprobación requerida", kind
    if kind in _MEMORY_REASONS:
        location = approval.get("location")
        entry = location.get("title") if isinstance(location, dict) else None
        title = "Apunte del proyecto" + (": " + entry if isinstance(entry, str) and entry.strip() else "")
        reason = _MEMORY_REASONS[kind]
        if kind == "memory_trust" and isinstance(location, dict) and location.get("conflicts_with"):
            reason = _MEMORY_CLASH_REASON
    return _item("approval", approval_id, title, reason, approval.get("created_at"),
                 "#approvals/" + quote(approval_id, safe=""))


def _team_bundles(services: Any) -> list[str]:
    names = []
    for bundle in services.definitions.list_bundles():
        name = bundle["name"]
        try:
            has_team = (services.bundle_path(name) / TEAM_FILE).is_file()
        except Exception:
            has_team = False
        if has_team:
            names.append(name)
    return names


def pending_items(services: Any) -> list[dict[str, Any]]:
    """Project existing read services into the compact shape used by the inbox."""
    items = []
    for approval in services.user_approvals.pending():
        items.append(_approval_item(approval))
    for run in services.runs.list(limit=MAX_ITEMS, status="waiting_human"):
        if run.get("status") != "waiting_human":
            continue
        run_id = str(run["id"])
        items.append(_item("run", run_id, run.get("project_name") or run.get("workflow_id") or run_id,
                           run.get("waiting_reason") or "Revisión humana",
                           run.get("updated_at") or run.get("created_at"),
                           "#new/run/" + quote(run_id, safe="")))

    for bundle in _team_bundles(services):
        for version in services.teams.list(bundle, status="proposed")["versions"]:
            version_id = str(version["id"])
            title = f"{bundle} · v{version.get('version') or version_id}"
            items.append(_item("team", version_id, title, "Versión de equipo propuesta",
                               version.get("proposed_at") or version.get("created_at"),
                               # CHT-T05: the team card thread, one stop with Approve and Reject.
                               "#new/team/" + quote(version_id, safe="")))

    for proposal in services.plan_proposals.list(status="proposed"):
        proposal_id = str(proposal["id"])
        # CHT-T06: the plan thread, one stop with Approve and Reject.
        items.append(_item("plan", proposal_id, proposal.get("goal") or proposal_id, "Plan propuesto",
                           proposal.get("created_at"), "#new/plan/" + quote(proposal_id, safe="")))

    for card in build_cards(services):
        if card.get("decision", "pending") != "pending":
            continue
        task_id = str(card["task_id"])
        reason = "Límite de uso" + (f" · {card['reason']}" if card.get("reason") else "")
        items.append(_item("continuity", task_id, card.get("title") or task_id, reason, card.get("since"), "#continuity"))

    # This is the same task read and user-wait filter used by the task_waiting API/view.
    for task in services.tasks.list_tasks():
        waiting = task.get("waiting")
        if not isinstance(waiting, dict) or waiting.get("who") != "user":
            continue
        task_id = str(task["id"])
        items.append(_item("task", task_id, task.get("title") or task_id,
                           waiting.get("question") or "Espera tu decisión", waiting.get("since"), "#task_waiting"))

    return items


def add_pending_routes(api: APIRouter, services: Any) -> None:
    """Register the token-protected, read-only pending inbox endpoint."""

    @api.get("/pending")
    def pending():
        items = pending_items(services)
        return {"items": items, "count": len(items)}

    # AVA-T05: active runs have their own section; the existing pending contract stays intact.
    @api.get("/pending/background")
    def background_runs():
        runs = [*services.runs.list(limit=MAX_ITEMS, status="running"),
                *services.runs.list(limit=MAX_ITEMS, status="queued")]
        items = [_item("run", str(run["id"]), run.get("project_name") or run.get("workflow_id") or str(run["id"]),
                       "En ejecución" if run.get("status") == "running" else "En cola",
                       run.get("updated_at") or run.get("created_at"),
                       "#new/run/" + quote(str(run["id"]), safe=""))
                 for run in runs if run.get("status") in {"running", "queued"}]
        return {"items": items, "count": len(items)}


__all__ = ["add_pending_routes", "pending_items"]
