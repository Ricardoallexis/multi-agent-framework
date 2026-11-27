# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only conversation for a run, built from its persisted events and state."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..call_guards import cut_by_run_limits
from ..errors import InvalidStateTransition
from ..services.errors import INVALID_REQUEST, INVALID_STATE, ServiceError
from .runs import _clean, run_view
from . import events


_WAIT_EVENTS = {
    "checkpoint": "review",
    "human_step_requested": "execute_step",
    "tool_approval_requested": "tool_approval",
    "escalation_requested": "escalation_approval",
    "document_review_requested": "content_review",
    "function_review_requested": "function_review",
    "function_step_failed": "function_failure",
    "function_failure_restored": "function_failure",
}
_REVIEW = {"review", "approval_pending", ""}
_APPROVE_REJECT = {"tool_approval", "escalation_approval", "budget_cut_approval", "budget_exhausted", "content_review",
                   "egress_review", "user_message", "execute_step", "research"}
_FINALS = {"workflow_completed", "workflow_failed", "workflow_cancelled", "human_rejected"}


class InputCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: dict[str, str]


class AcpStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=20000)


class AcpPromptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20000)


class AcpEmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _input_correction(run: dict[str, Any], view: dict[str, Any], declarations: list[dict[str, Any]]) -> bool:
    index = int(run.get("current_step") or 0)
    if not 0 <= index < len(view["steps"]):
        return False
    step = view["steps"][index]
    if step["id"] != run.get("waiting_step") or not step.get("function"):
        return False
    previous = view["steps"][index - 1] if 0 < index < len(view["steps"]) else None
    return bool(declarations and not (previous and not previous.get("function")
                and any(item["step_id"] == previous["id"] for item in view["artifacts"])))


def _decision(run: dict[str, Any], view: dict[str, Any],
              declarations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    reason = run.get("waiting_reason") or "review"
    exhausted = bool(run.get("budget_exhausted")) or reason == "budget_exhausted"
    actions = (["approve", "changes", "reject"] if (reason in _REVIEW and not run.get("budget_exhausted"))
               or (exhausted and reason in _REVIEW | {"budget_exhausted"} and cut_by_run_limits(run))  # still takes corrections
               else ["approve", "reject"] if reason in _REVIEW or reason in _APPROVE_REJECT else [])
    if reason in {"execute_step", "research"}:
        actions = ["human-submit", "reject"]
    elif reason == "function_review":
        actions = ["approve", "reject"]
    elif reason == "function_failure":
        index = int(run.get("current_step") or 0)
        previous = view["steps"][index - 1] if 0 < index < len(view["steps"]) else None
        actions = (["changes", "reject"] if previous and not previous.get("function")
                   and any(item["step_id"] == previous["id"] for item in view["artifacts"])
                   else ["correct_inputs", "reject"] if _input_correction(run, view, declarations)
                   else ["reject"])
    decision = {"type": reason, "step_id": run.get("waiting_step") or "",
                "attempt": run.get("waiting_attempt"), "actions": actions}
    if reason == "tool_approval" and view.get("tool_approval"):
        decision["permission_request_id"] = view["tool_approval"]["permission_request_id"]
    if reason == "function_failure":
        decision["correction_target"] = ("inputs" if "correct_inputs" in actions else
                                         "output" if "changes" in actions else None)
    return decision


def build_thread(services: Any, run: dict[str, Any], *, after: int = 0) -> dict[str, Any]:
    """Stable entries ordered by per-run event seq; current wait carries server-accepted actions."""
    view = run_view(services, run)
    try:
        declarations = services.definitions.read_workflow(run["workflow_id"], bundle=run.get("bundle")).get("inputs") or []
    except Exception:  # a changed or missing definition cannot offer input correction
        declarations = []
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
                     "project_id": view["project_id"], "project_title": view["project_title"],
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
            if reason == "function_failure":
                sensitive = bool((run.get("request") or {}).get("sensitive")) or run.get("data_class") in {"sensitive", "restricted"}
                entry.update(code=payload.get("code"), detail="" if sensitive else payload.get("detail") or "")
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
        decision = _clean(_decision(run, view, declarations))
        current: dict[str, Any] | None = None
        if latest_wait is not None and (not latest_wait["step_id"] or
                                        latest_wait["step_id"] == decision["step_id"]):
            latest_wait["reason"] = run.get("waiting_reason") or latest_wait["reason"]
            latest_wait["step_id"] = decision["step_id"]
            latest_wait["attempt"] = decision["attempt"]
            latest_wait["decision"] = decision
            current = latest_wait
        elif events:
            last = events[-1]
            entries.append({"seq": last["seq"], "ts": last["ts"], "kind": "waiting",
                            "text": f"Waiting: {run.get('waiting_reason') or 'review'}",
                            "reason": run.get("waiting_reason") or "review",
                            "step_id": run.get("waiting_step") or "", "attempt": run.get("waiting_attempt"),
                            "decision": decision})
            current = entries[-1]
        if current is not None and decision["type"] == "function_review":
            # What the person checks before the function runs, on whichever entry carries the decision.
            previous = {step["id"] for step in view["steps"][:int(run.get("current_step") or 0)]}
            outputs = {}
            for artifact in view["artifacts"]:
                if artifact["step_id"] in previous:
                    outputs[artifact["step_id"]] = artifact["data"]
            sensitive = bool((run.get("request") or {}).get("sensitive")) or run.get("data_class") in {"sensitive", "restricted"}
            current["inputs"] = {} if sensitive else _clean((run.get("request") or {}).get("inputs") or {})
            current["prior_outputs"] = {} if sensitive else _clean(outputs)
        if current is not None and decision["type"] == "function_failure" and decision["correction_target"] == "inputs":
            sensitive = bool((run.get("request") or {}).get("sensitive")) or run.get("data_class") in {"sensitive", "restricted"}
            values = (run.get("request") or {}).get("inputs") or {}
            current["text"] = f"Function step {decision['step_id']!r} failed. Correct its run inputs or reject the run."
            current["input_fields"] = [{"name": item["name"], "label": item.get("label") or item["name"],
                                        "type": item.get("type") or "text", "required": bool(item.get("required")),
                                        "choices": item.get("choices") or [], "sensitive": sensitive,
                                        "value": None if sensitive else values.get(item["name"], "")}
                                       for item in declarations]
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
            "entries": [entry for entry in entries if entry["seq"] > after], "steps": view["steps"]}


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


def _blocker_entries(services: Any, blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One message per bundle whose roles lack an approved team; it points at a proposed version when there is one."""
    entries = []
    for blocker in blockers:
        bundle, version_id = blocker.get("bundle"), None
        if bundle:
            try:
                proposed = services.teams.list(bundle, status="proposed")["versions"]
            except Exception:   # noqa: BLE001 - a bundle that cannot be listed still gets its message, linked to Equipos
                proposed = []
            version_id = proposed[-1]["id"] if proposed else None
        entries.append({"bundle": bundle, "roles": list(blocker.get("roles") or []), "team_version_id": version_id})
    return entries


def build_plan_thread(services: Any, proposal_id: str) -> dict[str, Any]:
    """CHT-T06: the thread of a plan proposal. The decision goes through the existing plan-proposal routes.

    Entries: the proposal (tasks with their inputs and the ``revision`` the decision must send), one message per bundle
    that still lacks an approved team (it disables approving, with the reason), and, once approved, one message per task
    with its run.
    """
    proposal = services.plan_proposals.get(proposal_id)
    document = proposal["proposal"]
    specs = (document.get("plan") or {}).get("tasks") or {}
    tasks = [{"id": task["id"], "title": task.get("title") or task["id"], "objective": task.get("objective") or "",
              "depends_on": list(task.get("depends_on") or []), "workflow": task.get("workflow"),
              "bundle": task.get("bundle"), "preferred": task.get("preferred") or "",
              "inputs": dict((specs.get(task["id"]) or {}).get("inputs") or task.get("inputs") or {})}
             for task in document.get("tasks") or []]
    summary = {key: proposal.get(key) for key in ("id", "group_id", "goal", "planner", "status", "revision", "created_by",
                                                  "created_at", "decided_by", "decided_at")}
    summary["rationale"] = document.get("rationale") or ""
    missing = _blocker_entries(services, proposal.get("blockers") or [])
    open_ = proposal["status"] == "proposed"
    decision = {"type": "plan_approval", "step_id": "", "attempt": None, "revision": proposal["revision"],
                "actions": ["approve", "reject"] if open_ else [],
                "approve_blocked": ({"reason": "team_not_approved", "blockers": missing} if open_ and missing else None)}
    ts = proposal.get("created_at")
    entries: list[dict[str, Any]] = [{"seq": 1, "ts": ts, "kind": "plan", "text": f"Plan {proposal['goal']}",
                                      "proposal": summary, "tasks": tasks, "decision": decision}]
    if open_:
        entries += [{"seq": 2 + index, "ts": ts, "kind": "team_missing", "text": f"Team missing for {item['bundle']}",
                     **item} for index, item in enumerate(missing)]
    if proposal["status"] == "approved":
        try:
            status = services.plans.status(document["plan"])
            by_id = {item["id"]: item for item in status["tasks"]}
        except Exception:   # noqa: BLE001 - the thread still shows the plan when its runs cannot be read
            by_id = {}
        for index, task in enumerate(tasks):
            view = by_id.get(task["id"]) or {}
            run = view.get("run") or None
            entries.append({"seq": 2 + index, "ts": proposal.get("decided_at"), "kind": "task_run",
                            "text": f"Task {task['id']}", "task_id": task["id"], "title": task["title"],
                            "state": view.get("state"), "run_id": run["id"] if run else None,
                            "run_status": run["status"] if run else None})
    can_advance = False
    if proposal["status"] == "approved":
        events = _plan_events(services, proposal["group_id"], proposal["id"])
        for item in events:
            entries.append({"seq": len(entries) + 2, **item})
        can_advance = bool(events) and events[-1]["event"] == "plan.advance_failed"   # ARR3-T03
    return {"proposal_id": proposal["id"], "status": proposal["status"], "revision": proposal["revision"],
            "can_advance": can_advance, "cursor": entries[-1]["seq"], "entries": entries}


def _plan_events(services: Any, group_id: str, proposal_id: str) -> list[dict[str, Any]]:
    """ARR3-T03: what the plan did by itself (team events ``plan.advanced`` and ``plan.advance_failed``), oldest first."""
    try:
        raw = services.tasks.events(group_id=group_id, limit=1000)
    except Exception:   # noqa: BLE001 - the thread still shows the plan when its events cannot be read
        return []
    out = []
    for event in raw:
        payload = event.get("payload") or {}
        if event.get("type") not in ("plan.advanced", "plan.advance_failed") or payload.get("proposal_id") != proposal_id:
            continue
        if event["type"] == "plan.advanced":
            started = [item.get("task_id") for item in payload.get("advanced") or [] if item.get("run_id")]
            moved = [str(item.get("task_id")) for item in payload.get("advanced") or []]
            text = ("The plan advanced: started " + ", ".join(map(str, started)) + "." if started
                    else "The plan advanced: updated " + (", ".join(moved) or "a task") + ".")
        else:
            text = "Could not advance the plan: " + str(payload.get("reason") or "unknown error") + "."
        out.append({"kind": "plan_event", "event": event["type"], "text": text, "ts": event.get("created_at")})
    return out


def add_thread_routes(api: APIRouter, services: Any) -> None:
    @api.get("/threads/acp")
    def list_acp_history():
        return {"conversations": services.runs.acp.list_history()}

    @api.post("/threads/acp/delete-all")
    def delete_all_acp_history():
        return services.runs.acp.delete_all_history()

    @api.post("/threads/acp/{session_id}/delete")
    def delete_acp_history(session_id: str):
        return services.runs.acp.delete_history(session_id)

    @api.post("/threads/acp")
    async def start_acp(body: AcpStartBody):
        return await anyio.to_thread.run_sync(services.runs.acp.start, body.bundle, body.text)

    @api.post("/threads/acp/{session_id}/prompts")
    async def prompt_acp(session_id: str, body: AcpPromptBody):
        return await anyio.to_thread.run_sync(services.runs.acp.prompt, session_id, body.text)

    @api.get("/threads/acp/{session_id}")
    def acp_thread(session_id: str, after: int = Query(0, ge=0)):
        return services.runs.acp.page(session_id, after)

    @api.post("/threads/acp/{session_id}/cancel")
    async def cancel_acp(session_id: str, body: AcpEmptyBody):
        return await anyio.to_thread.run_sync(services.runs.acp.cancel, session_id)

    @api.get("/threads/acp/{session_id}/stream")
    def acp_stream(session_id: str, after: int = Query(0, ge=0),
                   last_event_id: str | None = Header(None, alias="Last-Event-ID")):
        page = services.runs.acp.page(session_id, after)  # validate before opening the stream
        cursor = max(after, int(last_event_id)) if last_event_id and last_event_id.isdecimal() else after
        if page.get("saved"):
            cursor = 0
        settings = services.settings
        body = events.stream_acp(lambda position: services.runs.acp.page(session_id, position), after=cursor,
                                 heartbeat=settings.gui_heartbeat_seconds, poll=settings.gui_poll_seconds,
                                 max_seconds=settings.gui_stream_max_seconds)
        return StreamingResponse(body, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @api.get("/threads/runs/{run_id}")
    def thread(run_id: str, after: int = Query(0, ge=0)):
        return build_thread(services, services.runs.get(run_id), after=after)

    @api.post("/threads/runs/{run_id}/function-inputs")
    def correct_function_inputs(run_id: str, body: InputCorrection):
        """Correct declared run inputs for a held function and queue one retry."""
        runtime = services.runs._find(run_id)
        try:
            run = runtime.service.correct_function_inputs(run_id, body.inputs, by="user:operator", channel="gui")
        except ValueError as exc:
            raise ServiceError(INVALID_REQUEST, str(exc), status=422) from exc
        except InvalidStateTransition as exc:
            raise ServiceError(INVALID_STATE, str(exc), status=409) from exc
        return build_thread(services, services.runs._tag(runtime, run))

    @api.get("/threads/teams/{version_id}")   # CHT-T05
    def team_thread(version_id: str):
        return build_team_thread(services, version_id)

    @api.get("/threads/plans/{proposal_id}")   # CHT-T06
    def plan_thread(proposal_id: str):
        return build_plan_thread(services, proposal_id)
