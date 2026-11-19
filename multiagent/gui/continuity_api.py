# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected routes of continuity in the GUI (CNT-T06) under ``/gui/api``.

A task that hit a usage limit waits with a *hold* (``continuity.hold`` in ``tasks.execution_json``, CNT-T04, ``services/hold.py``).
The GUI shows that wait as a decision card (who reached which limit, when it resets and from what source, the saved work, the
hand-offs used) and lets a person confirm one of two choices. The routes only read the services and write the person's decision:

``GET  /continuity/policy``                          mode, wait threshold, hand-off limit and the executors with their policy
``GET  /continuity/decisions``                       the card of every task held by a usage limit
``GET  /continuity/session/{session_id}``            the card of the task held on that session (``held`` false when none)
``POST /tasks/{task_id}/continuity/decision``        ``{decision_id, decision: wait|delegate, executor_id?, expected_revision}``

What a decision does (the GUI never acts for the person: each choice is confirmed on the card, and this route needs the
revision the card showed):

- ``wait`` records that the person accepted the wait (``hold.decision = "wait"``). The task resumes by itself when the hold is
  due; it also undoes an earlier ``delegate``.
- ``delegate`` records the candidate the person chose (``hold.decision = "delegate"``, ``hold.candidate``) and blocks the
  automatic resume (``hold.blocked = "delegation_requested"``: a person decides). It starts no other executor and does not
  change the assignee: the hand-off itself belongs to CNT-T05 and is not part of this task, so ``executed`` is always false.

Both write the event ``continuity.decided`` (``ref`` is the ``decision_id``), so repeating a decision returns the first result.
``/sessions/{id}/children`` and its live stream also carry one node per task held on the session (``held_children``): the agent
tree shows it as «En espera · hasta HH:MM». Nothing here reads a session transcript, writes to an application or buys anything.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from datetime import timezone
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..services import hold as _hold
from ..services.errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError
from ..services.executors import CHECKPOINT_EVENT

_logger = logging.getLogger(__name__)
DECIDED_EVENT = "continuity.decided"
BLOCK_DELEGATION = "delegation_requested"
MODES = ("manual", "semi_auto", "auto")
CANDIDATE_POLICIES = ("no_ask", "ask", "never")
_POLICY_SETTINGS = ("continuity_mode", "continuity_wait_threshold_hours", "continuity_max_handoffs", "continuity_candidates")
APPS = {"claude_cli": ("Claude", "claude"), "codex_cli": ("Codex", "codex"), "copilot_cli": ("Copilot", "copilot"),
        "antigravity_cli": ("Antigravity", "antigravity")}
LEVELS = ("T1", "T2", "T3", "T4")
_SESSION_ID_CHARS = 100

LevelOf = Callable[[str | None], str | None]


# --------------------------------------------------------------------------- reading

def _view_hold(view: Mapping[str, Any]) -> dict[str, Any] | None:
    """The hold of a task *view* (``services.tasks`` shows the continuity block as ``continuity``)."""
    block = view.get("continuity")
    hold = block.get("hold") if isinstance(block, Mapping) else None
    return dict(hold) if isinstance(hold, Mapping) and hold else None


def _level_of(services: Any) -> LevelOf:
    """Model name or catalog id -> level (T1..T4) when the catalog classifies it (the table the session service uses)."""
    models = getattr(getattr(services.system, "catalog", None), "models", None) or {}
    table: dict[str, str] = {}
    for model_id, spec in models.items():
        level = getattr(spec, "level", None)
        if level in LEVELS:
            table[str(model_id).casefold()] = level
            name = getattr(spec, "model", None)
            if isinstance(name, str) and name:
                table.setdefault(name.casefold(), level)
    return lambda model: table.get(model.casefold()) if isinstance(model, str) and model else None


def _app_of(surface: str | None, executor_id: str) -> tuple[str, str | None]:
    """(label, session source) of an executor: the known apps by surface, else the executor id and no source."""
    return APPS.get(str(surface), (executor_id, None))


def _suggest_model(descriptor: Any, level_of: LevelOf, ceiling: str | None) -> tuple[str | None, str | None]:
    """A model the executor declares, with its level: the highest not above ``ceiling`` (the cheapest when it is unknown).

    ``(None, None)`` when the executor declares no model that the catalog classifies: the card then says the model is chosen later.
    """
    support = getattr(descriptor, "model_support", None)
    state = getattr(getattr(support, "status", None), "value", None)
    names = sorted(getattr(support, "values", None) or ()) if state == "supported" else []
    ranked = [(LEVELS.index(level), name) for name in names if (level := level_of(name)) in LEVELS]
    if ceiling in LEVELS:
        ranked = sorted((item for item in ranked if item[0] <= LEVELS.index(ceiling)), key=lambda item: (-item[0], item[1]))
    else:
        ranked.sort()
    return (ranked[0][1], LEVELS[ranked[0][0]]) if ranked else (None, None)


def _sends(checkpoint: Mapping[str, Any]) -> str:
    """What a hand-off would send, in words (the task envelope and the saved patch); never any file content."""
    if not checkpoint.get("patch"):
        return "el sobre de la tarea (sin parche)"
    files = checkpoint.get("files_total")
    return "el sobre de la tarea y el parche" + (f" de {files} archivo{'' if files == 1 else 's'}" if files else "")


def _checkpoint(services: Any, task_id: str, hold: Mapping[str, Any]) -> dict[str, Any]:
    """What the last checkpoint saved: whether there is a patch, its size and the number of files; no text of the agent."""
    with services.tasks.store.db.connect() as conn:
        row = conn.execute("SELECT payload_json FROM team_events WHERE type=? AND task_id=? ORDER BY seq DESC LIMIT 1",
                           (CHECKPOINT_EVENT, task_id)).fetchone()
    payload: dict[str, Any] = {}
    if row is not None:
        try:
            parsed = json.loads(row["payload_json"])
        except ValueError:
            parsed = None
        payload = parsed if isinstance(parsed, dict) else {}
    patch = payload.get("patch") or hold.get("checkpoint")
    files = payload.get("files_total")
    return {"saved": row is not None or bool(hold.get("checkpoint")), "patch": bool(patch),
            "patch_size": payload.get("patch_size") if patch else None,
            "files_total": files if isinstance(files, int) and not isinstance(files, bool) else None,
            "reason": None if patch else payload.get("patch_reason")}


def _candidate_policy(policies: Mapping[str, str], executor_id: str, surface: Any) -> str:
    """``no_ask``, ``ask`` or ``never`` as the policy in force says it: by the executor id, else by its surface, else ``ask``."""
    found = policies.get(executor_id) or policies.get(str(surface))
    return found if found in CANDIDATE_POLICIES else "ask"


def _candidates(services: Any, hold: Mapping[str, Any], continuity: Mapping[str, Any], level_of: LevelOf,
                checkpoint: Mapping[str, Any], statuses: list[dict[str, Any]], task_id: str) -> list[dict[str, Any]]:
    """The executors a person may delegate to: enabled, not the one that hit the limit and not already in the chain."""
    policies = services.executors.continuity_policy(task_id)["candidates"]       # the policy in force, the task's included
    chain = {str(item) for item in (continuity.get("chain") or [])} | {str(hold.get("executor_id") or "")}
    ceiling = level_of((hold.get("settings") or {}).get("model"))
    found = []
    for row in statuses:
        executor_id = str(row["executor_id"])
        if executor_id in chain or row.get("enabled") is not True:
            continue
        model, level = _suggest_model(services.executors.registry.get(executor_id), level_of, ceiling)
        found.append({"executor_id": executor_id, "app": _app_of(row.get("surface"), executor_id)[0], "model": model,
                      "level": level, "mode": row.get("mode"), "policy": _candidate_policy(policies, executor_id, row.get("surface")),
                      "available": bool(row.get("available")), "paused_until": row.get("paused_until"),
                      "sends": _sends(checkpoint)})
    return found


def _card(services: Any, view: Mapping[str, Any], hold: Mapping[str, Any], statuses: list[dict[str, Any]],
          level_of: LevelOf) -> dict[str, Any]:
    """The decision card of one held task."""
    continuity = view.get("continuity") or {}
    executor_id = str(hold.get("executor_id") or view.get("assignee") or "")
    surface = next((row.get("surface") for row in statuses if row["executor_id"] == executor_id), None)
    label, source = _app_of(surface, executor_id)
    model = (hold.get("settings") or {}).get("model")
    checkpoint = _checkpoint(services, str(view["id"]), hold)
    handoffs = int(continuity.get("handoffs") or 0)
    maximum = int(continuity.get("max_handoffs") or services.settings.continuity_max_handoffs)
    session_id = hold.get("session_id") or None
    open_ended = bool(hold.get("open_ended")) or hold.get("resume_after") is None
    blocked = hold.get("blocked")
    return {
        "task_id": view["id"], "group_id": view.get("group_id"), "title": view.get("title") or view["id"],
        "revision": view["revision"], "attempt": view.get("attempt"),
        "executor_id": executor_id, "app": label, "model": model, "level": level_of(model),
        "session_id": session_id, "session_ref": f"{source}:{session_id}" if source and session_id else None,
        "reason": hold.get("reason"), "window": hold.get("window"), "mode": hold.get("mode"), "outcome": hold.get("outcome"),
        "reset": {"at": hold.get("reset_at"), "source": hold.get("reset_source"), "confidence": hold.get("reset_confidence"),
                  "zone": hold.get("reset_zone"), "resume_after": hold.get("resume_after"), "open_ended": open_ended,
                  "reported_at": hold.get("reported_reset_at")},
        "retry": hold.get("retry"), "since": hold.get("since"),
        "checkpoint": checkpoint,
        "handoffs": {"used": handoffs, "max": maximum},
        "decision": hold.get("decision") or "pending", "candidate": hold.get("candidate"),
        "decided_at": hold.get("decided_at"), "blocked": blocked,
        "can_wait": not open_ended and blocked in (None, BLOCK_DELEGATION),
        "candidates": [] if handoffs >= maximum or _hold.is_condition_hold(hold)
        else _candidates(services, hold, continuity, level_of, checkpoint, statuses, str(view["id"])),
        "handoffs_exhausted": handoffs >= maximum,
    }


def _held(services: Any) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(task view, hold) of every task held by a usage limit, in creation order."""
    found = []
    for view in services.tasks.list_tasks(status="paused"):
        hold = _view_hold(view)
        if hold is not None and _hold.is_limit_hold(hold):
            found.append((view, hold))
    return found


def build_cards(services: Any) -> list[dict[str, Any]]:
    """The decision card of every task held by a usage limit."""
    held = _held(services)
    if not held:
        return []
    statuses = services.executors.status()["executors"]
    level_of = _level_of(services)
    return [_card(services, view, hold, statuses, level_of) for view, hold in held]


def policy_view(services: Any) -> dict[str, Any]:
    """The policy as the framework applies it: the one in force (``executors.continuity_policy``, the ``continuity_*``
    settings over the defaults of the contract). ``source`` is ``default`` while no setting was given."""
    policy = services.executors.continuity_policy()
    candidates = []
    for row in services.executors.status()["executors"]:
        candidates.append({
            "executor_id": row["executor_id"], "app": _app_of(row.get("surface"), str(row["executor_id"]))[0],
            "enabled": row.get("enabled") is True, "available": bool(row.get("available")),
            "paused_until": row.get("paused_until"), "paused_open_ended": bool(row.get("paused_open_ended")),
            "policy": _candidate_policy(policy["candidates"], str(row["executor_id"]), row.get("surface"))})
    given = bool(set(_POLICY_SETTINGS) & set(services.settings.model_fields_set))
    return {"mode": policy["mode"], "modes": list(MODES), "wait_threshold_hours": policy["wait_threshold_hours"],
            "max_handoffs": policy["max_handoffs"], "max_failed_resumes": _hold.MAX_FAILED_RESUMES,
            "never_during_integration": True, "auto_scope": "project", "source": "settings" if given else "default",
            "candidates": candidates}


def held_children(services: Any, session_id: str) -> list[dict[str, Any]]:
    """One tree node per task held on this session (``<source>:<native id>``), shaped like a ``ChildAgent`` plus ``reset_at``."""
    source, _, native = str(session_id).partition(":")
    if not native or len(session_id) > _SESSION_ID_CHARS or source not in {value[1] for value in APPS.values()}:
        return []
    try:
        held = _held(services)
    except Exception:       # the node is a decoration of the children list: a store that cannot answer must not break it
        _logger.debug("held tasks unavailable for the children of %s", session_id, exc_info=True)
        return []
    level_of = _level_of(services)
    nodes = []
    for view, hold in held:
        if hold.get("session_id") != native:
            continue
        model = (hold.get("settings") or {}).get("model")
        nodes.append({
            "id": f"task:{view['id']}", "source": source, "kind": "task", "name": view["id"], "label": view.get("title") or None,
            "model": model, "level": level_of(model), "status": "idle", "note": "waiting_limit",
            "started_at": hold.get("since"), "last_activity_at": hold.get("since"), "tokens": None, "link_id": None,
            "reset_at": hold.get("reset_at") or hold.get("resume_after")})
    return nodes


def with_extra_children(result: dict[str, Any], extra: list[dict[str, Any]], status: str | None = None) -> dict[str, Any]:
    """``result`` of ``list_children`` with the extra nodes added (they are idle, so they belong to the «active» group).

    ``counts`` always cover every child; ``version`` changes with the nodes, so the live stream tells the page to read again.
    """
    if not extra:
        return result
    merged = dict(result)
    counts = dict(result.get("counts") or {})
    counts["active"] = counts.get("active", 0) + len(extra)
    counts["total"] = counts.get("total", 0) + len(extra)
    merged["counts"] = counts
    if status != "done":
        merged["children"] = [*result.get("children", []), *extra]
    stamp = "|".join(f"{node['id']}:{node['status']}:{node.get('reset_at')}" for node in extra)
    merged["version"] = hashlib.sha1(f"{result.get('version', '')}|{stamp}".encode("utf-8")).hexdigest()[:12]
    return merged


# --------------------------------------------------------------------------- deciding

class ContinuityDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    decision: Literal["wait", "delegate"]
    executor_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    expected_revision: int = Field(ge=0)


def _conflict(reason: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_STATE, message, status=409, details={"reason": reason, **details})


def decide(services: Any, task_id: str, body: ContinuityDecisionBody) -> dict[str, Any]:
    """Record a person's confirmed choice on the hold of ``task_id``; see the module notes for what each choice does."""
    view = services.tasks.get_task(task_id)                       # not_found (404)
    offered: list[dict[str, Any]] = []
    held = _view_hold(view)
    if body.decision == "delegate" and held is not None and _hold.is_limit_hold(held):
        card = _card(services, view, held, services.executors.status()["executors"], _level_of(services))
        offered = [item for item in card["candidates"] if item["available"]]
    store = services.tasks.store
    stamp = services.executors.clock().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with store.transaction() as conn:
        earlier = conn.execute("SELECT task_id FROM team_events WHERE type=? AND ref=? ORDER BY seq LIMIT 1",
                               (DECIDED_EVENT, body.decision_id)).fetchone()
        if earlier is not None:
            if earlier["task_id"] != task_id:
                raise _conflict("decision_id_reused", "That decision id was used for another task", task_id=task_id)
            repeated = True
        else:
            repeated = False
            task = store.get_task(task_id, conn=conn)
            hold = _hold.hold_of(task) if task is not None else None
            if task is None or task.get("state") != "paused" or hold is None or not _hold.is_limit_hold(hold):
                raise _conflict("not_held", "The task is not waiting for a usage limit", task_id=task_id)
            if task["revision"] != body.expected_revision:
                raise _conflict("stale_revision", "The task changed since the card was drawn", task_id=task_id,
                                revision=task["revision"])
            if _hold.is_condition_hold(hold):
                raise _conflict("condition_hold", "This wait ends by a condition, not by a decision", task_id=task_id)
            candidate = sends = None
            if body.decision == "wait":
                if hold.get("open_ended") or hold.get("resume_after") is None:
                    raise _conflict("no_reset_time", "There is no reset time to wait for", task_id=task_id)
                if hold.get("blocked") not in (None, BLOCK_DELEGATION):
                    raise _conflict("blocked", "A person has to resume this task", task_id=task_id, blocked=hold.get("blocked"))
                changes: dict[str, Any] = {"decision": "wait", "candidate": None, "decided_at": stamp, "decided_by": "user"}
                if hold.get("blocked") == BLOCK_DELEGATION:
                    changes["blocked"] = None
            else:
                continuity = _hold.continuity_of(task)
                if int(continuity.get("handoffs") or 0) >= int(continuity.get("max_handoffs")
                                                               or services.settings.continuity_max_handoffs):
                    raise _conflict("handoffs_exhausted", "The hand-offs of this task are used up", task_id=task_id)
                if not body.executor_id:
                    raise ServiceError(INVALID_REQUEST, "Choose the executor to delegate to", status=422,
                                       details={"field": "executor_id"})
                chosen = next((item for item in offered if item["executor_id"] == body.executor_id), None)
                if chosen is None:
                    raise _conflict("candidate_not_available", "That executor cannot take the task", task_id=task_id,
                                    executor_id=body.executor_id)
                candidate, sends = chosen["executor_id"], chosen["sends"]
                changes = {"decision": "delegate", "candidate": candidate, "decided_at": stamp, "decided_by": "user",
                           "blocked": BLOCK_DELEGATION}
            new_hold = {**hold, **changes}
            for key, value in changes.items():          # a key set to None is taken out; the rest of the hold stays as it was
                if value is None:
                    new_hold.pop(key, None)
            store.update_task(conn, task_id, expected_revision=task["revision"],
                              execution=_hold.merge_continuity(task.get("execution"), {"hold": new_hold}))
            payload = {key: value for key, value in {
                "decision": body.decision, "by": "user", "executor_id": hold.get("executor_id"), "candidate": candidate,
                "sends": sends, "reason": hold.get("reason"), "resume_after": hold.get("resume_after"),
                "reset_at": hold.get("reset_at")}.items() if value is not None}
            store.append_event(conn, DECIDED_EVENT, actor="user:gui", group_id=str(task["group_id"]), task_id=task_id,
                               cause="decision on the usage limit", ref=body.decision_id, payload=payload)
    card = next((item for item in build_cards(services) if item["task_id"] == task_id), None)
    return {"ok": True, "executed": False, "repeated": repeated, "decision": body.decision, "task_id": task_id, "card": card}


# --------------------------------------------------------------------------- routes

def add_continuity_routes(api: APIRouter, services: Any) -> None:
    """Register the continuity routes on the token-protected GUI router."""

    @api.get("/continuity/policy")
    def continuity_policy():
        """Mode, wait threshold, hand-off limit and the executors with their policy, as the framework applies them."""
        return policy_view(services)

    @api.get("/continuity/decisions")
    def continuity_decisions():
        """The decision card of every task held by a usage limit."""
        return {"decisions": build_cards(services)}

    @api.get("/continuity/session/{session_id}")
    def continuity_session(session_id: str):
        """The card of the task held on this session, or ``{held: false}``."""
        if len(session_id) > _SESSION_ID_CHARS:
            raise ServiceError(INVALID_REQUEST, "session_id is too long", status=422, details={"field": "session_id"})
        native = session_id.partition(":")[2]
        card = next((item for item in build_cards(services) if native and item["session_id"] == native), None)
        return {"held": card is not None, "session_id": session_id, "card": card}

    @api.post("/tasks/{task_id}/continuity/decision")
    def continuity_decision(task_id: str, body: ContinuityDecisionBody):
        """Record the person's confirmed choice (wait or delegate) on the hold; it never starts another executor."""
        if len(task_id) > 128:
            raise ServiceError(NOT_FOUND, "Task not found", status=404, details={"task": task_id[:128]})
        return decide(services, task_id, body)


__all__ = ["BLOCK_DELEGATION", "DECIDED_EVENT", "ContinuityDecisionBody", "add_continuity_routes", "build_cards", "decide",
           "held_children", "policy_view", "with_extra_children"]
