# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only data for the GUI work map (GUI2-T12)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .errors import ServiceError



_PROJECT_FIELDS = ("id", "name", "title", "visibility", "archived", "progress", "counts")
_STAGE_FIELDS = ("group_id", "kind", "code", "title", "position", "label", "authorized",
                 "lifecycle_state", "phases", "planned_start", "planned_end", "closed_at", "counts", "progress")
_TASK_FIELDS = ("id", "group_id", "kind", "title", "state", "status", "label", "phase", "due_at",
                "assignee", "preferred", "assignee_info", "attempt", "revision", "depends_on")
_CHILD_FIELDS = ("id", "source", "kind", "name", "model", "level", "status", "note",
                 "started_at", "last_activity_at", "tokens", "link_id")


def _pick(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: value[key] for key in fields if key in value}


def _safe_project(project: dict[str, Any]) -> dict[str, Any]:
    safe = _pick(project, _PROJECT_FIELDS)
    stages = []
    for stage in project.get("stages", []):
        stage_view = _pick(stage, _STAGE_FIELDS)
        stage_view["tasks"] = [_pick(task, _TASK_FIELDS) for task in stage.get("tasks", [])
                               if isinstance(task, dict)]
        stages.append(stage_view)
    safe["stages"] = stages

    def safe_ref(ref: dict[str, Any], *, blockers: bool = False) -> dict[str, Any]:
        result = _pick(ref, _TASK_FIELDS)
        if blockers and isinstance(ref.get("blockers"), list):
            result["blockers"] = [_pick(item, ("task_id", "state", "requires"))
                                  for item in ref["blockers"] if isinstance(item, dict)]
        return result

    attention = project.get("attention")
    if isinstance(attention, dict):
        safe["attention"] = {
            "reviews": [safe_ref(item) for item in attention.get("reviews", []) if isinstance(item, dict)],
            "verifications": [_pick(item, _STAGE_FIELDS) for item in attention.get("verifications", [])
                              if isinstance(item, dict)],
            "mine": [safe_ref(item) for item in attention.get("mine", []) if isinstance(item, dict)],
            "blocked": [safe_ref(item, blockers=True) for item in attention.get("blocked", [])
                        if isinstance(item, dict)],
        }
    return safe


def _task_ids(project: dict[str, Any]) -> list[str]:
    return [task["id"] for stage in project.get("stages", [])
            for task in stage.get("tasks", []) if isinstance(task, dict) and task.get("id")]


def _session_view(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "parent_id": summary.get("parent_id"),
        "source": summary.get("source"),
        "model": summary.get("models") or None,
        "name": None,
        "status": summary.get("status"),
        "tokens": summary.get("tokens"),
        "started_at": summary.get("created_at"),
        "last_activity_at": summary.get("updated_at"),
        "children": [],
    }


def _all_summaries(sessions_service, warnings: list[str]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    cursor = None
    while True:
        page = sessions_service.list_sessions(limit=500, after=cursor)
        summaries.extend(page.get("sessions", []))
        warnings.extend(page.get("warnings", []))
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return summaries


def build_work_map(services, *, project_id: str | None = None, only: str = "active") -> dict[str, Any]:
    """Build a safe project map and the allowed sessions tree from existing read APIs."""
    if only not in {"active", "all"}:
        raise ServiceError("invalid_request", "only must be active or all", status=422,
                           details={"field": "only"})
    if project_id:
        raw_projects = [services.projects.get(project_id)]
    else:
        project_refs = services.projects.list(archived=False)
        if only == "all":
            project_refs += services.projects.list(archived=True)
        raw_projects = [services.projects.get(item["id"]) for item in project_refs]
    projects = [_safe_project(project) for project in raw_projects]

    links: set[tuple[str, str]] = set()
    task_ids = {task_id for project in raw_projects for task_id in _task_ids(project)}
    for task_id in sorted(task_ids):
        for row in services.projects.store.session_links(task_id):
            sid = row.get("session_id")
            if sid:
                links.add((task_id, sid))
    for runtime in services.runs._runtimes():
        with runtime.store.db.connect() as conn:
            rows = conn.execute("SELECT id,task_id FROM runs WHERE task_id IS NOT NULL").fetchall()
        for row in rows:
            if row["task_id"] in task_ids:
                links.add((row["task_id"], "framework:" + row["id"]))

    warnings: list[str] = []
    summaries = _all_summaries(services.sessions, warnings)
    summary_by_id = {summary.get("id"): summary for summary in summaries if isinstance(summary.get("id"), str)}
    # Ensure linked framework runs appear even when external session roots
    # are not configured and list_sessions therefore returns its hint.
    for _, sid in sorted(links):
        if sid in summary_by_id:
            continue
        try:
            summary_by_id[sid] = services.sessions.get_session(sid)
        except ServiceError as exc:
            warnings.append("session_unavailable:" + sid + ":" + exc.code)
    # Session summaries define session-to-session edges. This also includes
    # allowed sessions without task links and children without link_id.
    summaries = list(summary_by_id.values())
    if only == "active":
        # Session Status distinguishes archived from idle; this active scope
        # includes idle sessions, like the children active group.
        summaries = [summary for summary in summaries if summary.get("status") in {"active", "idle"}]
    sessions: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        sid = summary.get("id")
        if isinstance(sid, str):
            sessions[sid] = _session_view(summary)

    for sid in list(sessions):
        try:
            result = services.sessions.list_children(sid, status="active" if only == "active" else None)
        except ServiceError as exc:
            warnings.append("children_unavailable:" + sid + ":" + exc.code)
            continue
        warnings.extend(result.get("warnings", []))
        sessions[sid]["children"] = [
            _pick(child, _CHILD_FIELDS) for child in result.get("children", []) if isinstance(child, dict)
        ]

    payload = {
        "projects": projects,
        "sessions": {key: sessions[key] for key in sorted(sessions)},
        "links": [{"task_id": task, "session_id": session} for task, session in sorted(links)],
        "warnings": sorted(set(warnings)),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {"version": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16], **payload}
