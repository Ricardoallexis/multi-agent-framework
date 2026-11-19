# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only conversation for a run, built from its persisted events and state."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query

from .runs import _clean, run_view


_WAIT_EVENTS = {
    "checkpoint": "review",
    "human_step_requested": "execute_step",
    "tool_approval_requested": "tool_approval",
    "escalation_requested": "escalation_approval",
    "document_review_requested": "content_review",
}
_REVIEW = {"review", "approval_pending", ""}
_APPROVE_REJECT = {"tool_approval", "escalation_approval", "budget_cut_approval", "budget_exhausted", "content_review",
                   "egress_review", "user_message", "execute_step", "research"}
_FINALS = {"workflow_completed", "workflow_failed", "workflow_cancelled", "human_rejected"}


def _decision(run: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    reason = run.get("waiting_reason") or "review"
    actions = (["approve", "changes", "reject"] if reason in _REVIEW and not run.get("budget_exhausted")
               else ["approve", "reject"] if reason in _REVIEW or reason in _APPROVE_REJECT else [])
    if reason in {"execute_step", "research"}:
        actions = ["human-submit", "reject"]
    decision = {"type": reason, "step_id": run.get("waiting_step") or "",
                "attempt": run.get("waiting_attempt"), "actions": actions}
    if reason == "tool_approval" and view.get("tool_approval"):
        decision["permission_request_id"] = view["tool_approval"]["permission_request_id"]
    return decision


def build_thread(services: Any, run: dict[str, Any], *, after: int = 0) -> dict[str, Any]:
    """Stable entries ordered by per-run event seq; current wait carries server-accepted actions."""
    view = run_view(services, run)
    events = run["events"]
    steps = {step["id"]: step for step in view["steps"]}
    entries: list[dict[str, Any]] = []
    latest_wait: dict[str, Any] | None = None
    current_model: dict[str, dict[str, Any]] = {}
    for event in events:
        seq, ts, kind = event["seq"], event["ts"], event["event_type"]
        payload = event.get("payload") or {}
        step_id = payload.get("step_id") or ""
        entry: dict[str, Any] | None = None
        if kind == "workflow_started":
            team = next((item.get("payload") or {} for item in events
                         if item["event_type"] == "team_version_bound"), {})
            request = run.get("request") or {}
            entry = {"kind": "launched", "text": f"Started {run['workflow_id']}",
                     "workflow_id": run["workflow_id"], "project_name": view["project_name"],
                     "execution_mode": view["execution_mode"], "operating_mode": run.get("operating_mode"),
                     "task_id": run.get("task_id") or request.get("task_id"),
                     "task_href": "#plans" if run.get("task_id") or request.get("task_id") else None,
                     "team": {key: team.get(key) for key in ("team_id", "version_id", "version", "bundle")}
                     if team else None,
                     "team_href": "#teams/" + quote(str(team["bundle"]), safe="")
                     if team.get("bundle") else None}
        elif kind == "model_selected":
            current_model[step_id] = {"kind": "model", "provider": payload.get("provider"),
                                      "model": payload.get("model")}
        elif kind == "human_step_submitted":
            current_model[step_id] = {"kind": "human", "provider": payload.get("provider"),
                                      "model": payload.get("model")}
        elif kind == "step_completed":
            executor = current_model.get(step_id) or steps.get(step_id, {}).get("executor") or {}
            if payload.get("function"):
                executor = {"kind": "function", "function": payload["function"]}
            entry = {"kind": "step", "text": f"{step_id} completed", "step_id": step_id,
                     "attempt": payload.get("attempt"), "status": "completed", "executor": executor or None}
        elif kind in {"user.message", "user.message_consumed"}:
            entry = {"kind": "user_message" if kind == "user.message" else "message_consumed",
                     "text": payload.get("text") or ("Message read" if kind.endswith("consumed") else ""),
                     "message_id": payload.get("message_id"), "message_kind": payload.get("kind"),
                     "actor": payload.get("actor")}
        elif kind in _WAIT_EVENTS or kind == "call_paused" or kind == "budget_exhausted":
            reason = payload.get("waiting_reason") if kind == "call_paused" else _WAIT_EVENTS.get(kind, "budget_exhausted")
            entry = {"kind": "waiting", "text": payload.get("message") or f"Waiting: {reason}",
                     "reason": reason, "step_id": step_id, "attempt": payload.get("attempt"), "decision": None}
        elif kind in _FINALS:
            status = {"workflow_completed": "completed", "workflow_failed": "failed",
                      "workflow_cancelled": "cancelled", "human_rejected": "rejected"}[kind]
            last = next((item for item in reversed(run.get("artifacts") or [])
                         if item.get("step_id") == (view["steps"][-1]["id"] if view["steps"] else None)), None)
            entry = {"kind": "final", "text": f"Run {status}", "status": status,
                     "output": last.get("data") if status == "completed" and last else None,
                     "error": (payload.get("error") or run.get("error") or "") if status == "failed" else ""}
        if entry is not None:
            entries.append({"seq": seq, "ts": ts, **_clean(entry)})
            if entry["kind"] == "waiting":
                latest_wait = entries[-1]
    if run["status"] == "waiting_human":
        decision = _clean(_decision(run, view))
        if latest_wait is not None and (not latest_wait["step_id"] or
                                        latest_wait["step_id"] == decision["step_id"]):
            latest_wait["reason"] = run.get("waiting_reason") or latest_wait["reason"]
            latest_wait["step_id"] = decision["step_id"]
            latest_wait["attempt"] = decision["attempt"]
            latest_wait["decision"] = decision
        elif events:
            last = events[-1]
            entries.append({"seq": last["seq"], "ts": last["ts"], "kind": "waiting",
                            "text": f"Waiting: {run.get('waiting_reason') or 'review'}",
                            "reason": run.get("waiting_reason") or "review",
                            "step_id": run.get("waiting_step") or "", "attempt": run.get("waiting_attempt"),
                            "decision": decision})
    if run["status"] in {"completed", "failed", "cancelled", "rejected"} and not any(
            entry["kind"] == "final" and entry["status"] == run["status"] for entry in entries):
        last = events[-1] if events else {"seq": 0, "ts": run.get("updated_at")}
        artifact = next((item for item in reversed(run.get("artifacts") or [])
                         if item.get("step_id") == (view["steps"][-1]["id"] if view["steps"] else None)), None)
        entries.append({"seq": last["seq"], "ts": last["ts"], "kind": "final",
                        "text": f"Run {run['status']}", "status": run["status"],
                        "output": _clean(artifact.get("data")) if run["status"] == "completed" and artifact else None,
                        "error": _clean(run.get("error") or "") if run["status"] == "failed" else ""})
    return {"run_id": run["id"], "status": run["status"], "cursor": events[-1]["seq"] if events else 0,
            "entries": [entry for entry in entries if entry["seq"] > after]}


def build_team_thread(services: Any, version_id: str) -> dict[str, Any]:
    """CHT-T05: the thread of a team version is one stop. The decision goes through the existing teams routes."""
    version = services.teams.get_card(version_id)
    actions = ["approve", "reject"] if version["status"] == "proposed" else []
    summary = {key: version.get(key) for key in ("id", "bundle", "team_id", "version", "status", "active", "created_by",
                                                 "proposed_by", "proposed_at", "decided_by", "decided_at", "note")}
    entry = {"seq": 1, "ts": version.get("proposed_at") or version.get("created_at"), "kind": "team_version",
             "text": f"Team {version['team_id']} version {version['version']}", "version": summary,
             "card": version["card"], "diff": version["diff"],
             "decision": {"type": "team_approval", "step_id": "", "attempt": None, "actions": actions}}
    return {"version_id": version["id"], "status": version["status"], "cursor": 1, "entries": [entry]}


def add_thread_routes(api: APIRouter, services: Any) -> None:
    @api.get("/threads/runs/{run_id}")
    def thread(run_id: str, after: int = Query(0, ge=0)):
        return build_thread(services, services.runs.get(run_id), after=after)

    @api.get("/threads/teams/{version_id}")   # CHT-T05
    def team_thread(version_id: str):
        return build_team_thread(services, version_id)
