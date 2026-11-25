# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure assembly of the versioned office scene document."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from ..tasks.labels import load as load_task_labels
from ..tasks.labels import task_label_key
from .plan import is_office_group
from .schema import RELATION_LABELS

SCENE_SCHEMA = "maf.office-scene/1"
SCENE_WINDOW_HOURS = 24
SCENE_RUNS_MAX = 500
STATE_PRIORITY = ("review", "changes", "failed", "doing", "blocked", "ready", "todo", "done", "cancelled")
IDLE = "idle"
IDLE_LABEL = "Libre"

_ACTIVE_RUNS = {"queued", "running", "waiting_human", "paused"}
_RUN_STATES = {
    "running": "doing",
    "queued": "ready",
    "paused": "doing",
    "waiting_human": "review",
    "failed": "failed",
    "interrupted": "failed",
    "completed": "done",
    "cancelled": "cancelled",
    "rejected": "cancelled",
}


def run_state(run: Mapping[str, Any]) -> str:
    """Map a persisted run status to the corresponding scene state."""
    status = run.get("status")
    if status == "queued" and run.get("revised"):
        return "changes"
    try:
        return _RUN_STATES[str(status)]
    except KeyError as exc:
        raise ValueError(f"Unknown run status: {status!r}") from exc


def winning_state(states: Iterable[str]) -> str:
    """Return the most attention-worthy state, or ``idle`` when there are no sources."""
    found = set(states)
    unknown = found.difference((*STATE_PRIORITY, IDLE))
    if unknown:
        raise ValueError(f"Unknown scene state: {sorted(unknown)[0]!r}")
    return next((state for state in STATE_PRIORITY if state in found), IDLE)


def _as_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _state_label(state: str, labels: Mapping[str, Any]) -> str:
    if state == IDLE:
        return IDLE_LABEL
    return str(labels["task"][state]["text"])


def _winner(candidates: list[dict[str, Any]], labels: Mapping[str, Any]) -> dict[str, Any]:
    state = winning_state(item["state"] for item in candidates)
    if state == IDLE:
        return {"state": IDLE, "label": IDLE_LABEL, "worker": "—", "since": None}
    matching = [item for item in candidates if item["state"] == state]
    chosen = max(matching, key=lambda item: _as_datetime(item.get("since")) or datetime.min.replace(tzinfo=timezone.utc))
    return {"state": state, "label": _state_label(state, labels),
            "worker": chosen.get("worker") or "—", "since": chosen.get("since")}


def _candidate(state: str, since: Any, worker: Any = None) -> dict[str, Any]:
    return {"state": state, "since": since if isinstance(since, str) else None,
            "worker": worker if isinstance(worker, str) and worker else "—"}


def _task_key(task: Mapping[str, Any], status: str, *, approved: bool) -> str:
    label = task.get("label")
    released = (isinstance(label, Mapping) and label.get("key") == "done"
                and task.get("state") == "submitted")
    return task_label_key(str(task.get("state") or "open"), status, authorized=approved, released=released)


def _proposal_tasks(proposal: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    document = proposal.get("proposal")
    planned = document.get("tasks", []) if isinstance(document, Mapping) else []
    if not isinstance(planned, list):
        return [], {}
    saved = proposal.get("scene_tasks")
    actual = saved if isinstance(saved, Mapping) else {}
    tasks: list[dict[str, Any]] = []
    states: dict[str, str] = {}
    approved = proposal.get("status") == "approved"
    for item in planned:
        if not isinstance(item, Mapping):
            continue
        task_id = item.get("id")
        if not isinstance(task_id, str):
            continue
        view = actual.get(task_id)
        if isinstance(view, Mapping):
            state = _task_key(view, str(view.get("status") or "open"), approved=approved)
            updated_at = view.get("updated_at")
        else:
            state = task_label_key("open", "open", authorized=False)
            updated_at = proposal.get("created_at")
        tasks.append({"id": task_id, "bundle": item.get("bundle"), "state": state,
                      "updated_at": updated_at if isinstance(updated_at, str) else None})
        states[task_id] = state
    return tasks, states


def _agent_of_step(area: Mapping[str, Any], run: Mapping[str, Any], members: list[Mapping[str, Any]],
                   assignments: Mapping[str, set[str]]) -> tuple[str | None, str | None]:
    """The agent member that runs a step with no role, and its role when it has only one.

    The step's agent comes from ``area["step_agents"]`` (``{workflow_id: {step_id: agent}}``, read from the area's
    workflow file). Exactly one agent member of the team must run that agent; otherwise there is no match.
    """
    table = area.get("step_agents")
    agents = table.get(str(run.get("workflow_id"))) if isinstance(table, Mapping) else None
    agent = agents.get(str(run.get("step_id"))) if isinstance(agents, Mapping) else None
    if not isinstance(agent, str) or not agent:
        return None, None
    matching = [str(member["id"]) for member in members
                if member.get("kind") == "agent" and member.get("agent") == agent and member.get("id") is not None]
    if len(matching) != 1:
        return None, None
    roles = assignments.get(matching[0], set())
    return matching[0], (next(iter(roles)) if len(roles) == 1 else None)


def build_scene(office: dict, areas: list[dict], proposals: list[dict], *, now: datetime) -> dict:
    """Build ``maf.office-scene/1`` from already-read office, team, task, proposal and run data."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(hours=SCENE_WINDOW_HOURS)
    labels = load_task_labels()
    area_ids = {str(area.get("id")) for area in areas}
    by_bundle = {str(area.get("bundle")): area for area in areas if area.get("bundle")}
    area_candidates: dict[str, list[dict[str, Any]]] = {str(area.get("id")): [] for area in areas}
    member_candidates: dict[tuple[str, str], list[dict[str, Any]]] = {}
    run_truncated = False

    for area in areas:
        area_id = str(area.get("id"))
        team = area.get("team") if isinstance(area.get("team"), Mapping) else {}
        members = [member for member in team.get("members", []) if isinstance(member, Mapping)]
        member_by_id = {str(member["id"]): member for member in members if member.get("id") is not None}
        people = {member_id for member_id, member in member_by_id.items() if member.get("kind") == "person"}
        assignments: dict[str, set[str]] = {}
        for item in team.get("assignments", []):
            if isinstance(item, Mapping) and item.get("member") in member_by_id and isinstance(item.get("role"), str):
                assignments.setdefault(str(item["member"]), set()).add(str(item["role"]))
        for run in area.get("runs", []):
            if not isinstance(run, Mapping):
                continue
            status = str(run.get("status") or "")
            updated = _as_datetime(run.get("updated_at"))
            if status not in _ACTIVE_RUNS and (updated is None or updated < cutoff):
                continue
            run_truncated = run_truncated or bool(area.get("truncated"))
            candidate = _candidate(run_state(run), run.get("updated_at"), run.get("step_model_id"))
            can_assign = area.get("package_state", "ok") == "ok"
            member_id = run.get("step_member")
            inferred_role = None
            if not (isinstance(member_id, str) and member_id in member_by_id):
                member_id, inferred_role = _agent_of_step(area, run, members, assignments)

            if not can_assign:
                area_candidates[area_id].append(candidate)
                continue
            if status != "waiting_human":
                if isinstance(member_id, str) and member_id in member_by_id:
                    member_candidates.setdefault((area_id, member_id), []).append(candidate)
                else:
                    area_candidates[area_id].append(candidate)
                continue

            assigned_agent = isinstance(member_id, str) and member_id in member_by_id
            if assigned_agent:
                member_candidates.setdefault((area_id, member_id), []).append(candidate)
            waiting_role = run.get("step_role") or inferred_role
            reviewer_roles = {
                str(relation.get("from"))
                for relation in team.get("relationships", [])
                if isinstance(relation, Mapping) and relation.get("type") == "reviews"
                and relation.get("to") == waiting_role
            }
            reviewers = {
                member_id for member_id in people
                if assignments.get(member_id, set()) & reviewer_roles
            }
            if not reviewers:
                reviewers = people
            if reviewers:
                for person_id in reviewers:
                    member_candidates.setdefault((area_id, person_id), []).append(candidate)
            else:
                area_candidates[area_id].append(candidate)

    for proposal in proposals:
        if not isinstance(proposal, Mapping) or not is_office_group(
                str(proposal.get("group_id") or ""), str(office.get("id") or "")):
            continue
        if proposal.get("status") == "rejected":
            continue
        planned_tasks, task_states = _proposal_tasks(proposal)
        plan_open = (proposal.get("status") == "proposed"
                     or (proposal.get("status") == "approved"
                         and any(state not in {"done", "cancelled"} for state in task_states.values())))
        for task in planned_tasks:
            target_area = by_bundle.get(str(task.get("bundle") or ""))
            if target_area is None:
                continue
            updated_at = task.get("updated_at")
            task_updated = _as_datetime(updated_at)
            if not plan_open and (task_updated is None or task_updated < cutoff):
                continue
            area_candidates[str(target_area["id"])].append(_candidate(task["state"], updated_at))

    scene_areas = []
    for area in areas:
        area_id = str(area.get("id"))
        team = area.get("team") if isinstance(area.get("team"), Mapping) else {}
        members = [member for member in team.get("members", []) if isinstance(member, Mapping)]
        roles_by_member: dict[str, list[str]] = {}
        for item in team.get("assignments", []):
            if isinstance(item, Mapping) and isinstance(item.get("member"), str) and isinstance(item.get("role"), str):
                roles_by_member.setdefault(item["member"], []).append(item["role"])
        member_rows = []
        for member in members:
            member_id = str(member.get("id") or "")
            result = _winner(member_candidates.get((area_id, member_id), []), labels)
            member_rows.append({
                "id": member_id,
                "kind": member.get("kind"),
                "name": member.get("name") or member.get("agent") or member_id,
                "role": ", ".join(sorted(set(roles_by_member.get(member_id, [])))),
                **result,
            })
        area_sources = area_candidates[area_id]
        member_sources = [
            candidate
            for (candidate_area, _), candidates in member_candidates.items()
            if candidate_area == area_id
            for candidate in candidates
        ]
        result = _winner([*area_sources, *member_sources], labels)
        result["worker"] = _winner(area_sources, labels)["worker"]
        scene_areas.append({
            "id": area.get("id"), "name": area.get("name"), "bundle": area.get("bundle"),
            "data_class": area.get("data_class"), "degraded": list(area.get("degraded") or []),
            **result, "members": member_rows,
        })

    plan_candidates = []
    for proposal in proposals:
        if not isinstance(proposal, Mapping) or not is_office_group(
                str(proposal.get("group_id") or ""), str(office.get("id") or "")):
            continue
        status = proposal.get("status")
        if status == "rejected":
            continue
        task_rows, task_states = _proposal_tasks(proposal)
        if not task_rows:
            continue
        if status == "proposed":
            state = "review"
        elif status == "approved":
            state = "done" if all(value in {"done", "cancelled"} for value in task_states.values()) else "doing"
        else:
            continue
        task_times = [_as_datetime(task.get("updated_at")) for task in task_rows]
        task_times = [value for value in task_times if value is not None]
        decided = _as_datetime(proposal.get("decided_at"))
        created = _as_datetime(proposal.get("created_at"))
        recent = max(task_times + [value for value in (decided, created) if value is not None],
                     default=None)
        if state == "done" and (recent is None or recent < cutoff):
            continue
        since = recent.isoformat() if recent is not None else proposal.get("created_at")
        plan = {"proposal_id": proposal.get("id"), "status": status, "tasks": len(task_rows),
                "done": sum(value == "done" for value in task_states.values()),
                "href": f"#new/plan/{proposal.get('id')}"}
        plan_candidates.append({"state": state, "since": since, "plan": plan})

    orchestrator_state = _winner([_candidate(item["state"], item.get("since")) for item in plan_candidates], labels)
    plan = None
    if orchestrator_state["state"] != IDLE:
        priority = STATE_PRIORITY.index(orchestrator_state["state"])
        winner = max(
            (item for item in plan_candidates if STATE_PRIORITY.index(item["state"]) == priority),
            key=lambda item: _as_datetime(item.get("since")) or datetime.min.replace(tzinfo=timezone.utc),
        )
        plan = winner["plan"]
    orchestrator = office.get("orchestrator") or {}
    relations = []
    for relation in office.get("relations") or []:
        kind = relation.get("kind") or relation.get("type")
        from_area, to_area = relation.get("from"), relation.get("to")
        relations.append({
            "from": from_area, "to": to_area, "kind": kind,
            "label": RELATION_LABELS[kind],
            "warning": "office_relation_unknown_area" if from_area not in area_ids or to_area not in area_ids else None,
        })

    return {
        "schema": SCENE_SCHEMA,
        "generated_at": now.isoformat(),
        "office": {"id": office.get("id"), "name": office.get("name"), "state": office.get("state")},
        "orchestrator": {
            "enabled": bool(orchestrator.get("enabled")),
            "name": orchestrator.get("name"),
            "state": orchestrator_state["state"],
            "label": orchestrator_state["label"],
            "since": orchestrator_state["since"],
            "plan": plan,
        },
        "areas": scene_areas,
        "relations": relations,
        "truncated": run_truncated or any(
            len(area.get("runs", [])) >= SCENE_RUNS_MAX for area in areas
        ),
    }
