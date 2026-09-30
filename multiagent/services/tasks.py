# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Task services: groups, tasks, dependencies and the team event log.

Contract fixed by ORQ-T02; implemented by ORQ-T05 on the task store
(``multiagent.db.task_store``, ORQ-T03) and the pure dependency graph
(``multiagent.tasks``, ORQ-T04).

A group is a stage of work: it is ``authorized`` or not and sets the default
``requires`` of its dependencies. A task has a stored ``state`` and an
effective ``status``: an ``open`` task is reported as ``blocked`` or ``ready``,
computed from its dependencies and never stored. Stored states: ``open``,
``claimed``, ``running``, ``paused``, ``submitted``, ``needs_changes``,
``accepted``, ``failed``, ``cancelled`` (the last three are terminal).

A dependency is satisfied when the task it depends on is ``accepted``
(``requires="accepted"``, the default) or, with ``requires="submitted"``,
``submitted`` or ``accepted``. Dependencies never form cycles.

Every state change goes through ``transition``: it checks the source status,
bumps ``revision``, writes one team event with the actor and the cause, and
reports dependents that became ``ready``, all in one transaction. ``accept``
is manual here; gates arrive later without changing this contract.

Actors, assignees and preferred agents are free text (``"user"``,
``"agent:codex"``): the core never names providers. They separate
responsibilities; they do not authenticate.

Every method returns JSON-ready data and raises only ``ServiceError``:
``not_found`` (unknown group, task or dependency), ``invalid_request``
(malformed data, unknown action or status, a dependency cycle, a payload over
2048 bytes) or ``invalid_state`` (the status does not allow the action, the
group is not authorized, a stale ``expected_revision``, or a ``file_scope``
that overlaps another assignee's active task).
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from ..db.task_store import StaleRevision, TaskStore
from ..tasks import graph
from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices

# Stored states; "blocked" and "ready" are computed for "open" tasks only.
STATES = ("open", "claimed", "running", "paused", "submitted", "validating", "needs_changes",
          "accepted", "failed", "cancelled")
TERMINAL_STATES = ("accepted", "failed", "cancelled")
STATUSES = ("blocked", "ready") + STATES[1:]
REQUIRES = ("accepted", "submitted")

# action -> (effective statuses it may start from, stored state it leads to).
TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "claim": (("ready",), "claimed"),
    "unclaim": (("claimed", "paused", "needs_changes"), "open"),
    "start": (("claimed", "needs_changes"), "running"),
    "pause": (("running",), "paused"),
    "resume": (("paused",), "running"),
    "submit": (("running",), "submitted"),
    "accept": (("submitted",), "accepted"),
    "request_changes": (("submitted",), "needs_changes"),
    "fail": (("claimed", "running", "paused", "needs_changes"), "failed"),
    "cancel": (("blocked", "ready", "claimed", "running", "paused", "submitted", "needs_changes"), "cancelled"),
}
# Gate actions (ORQ-2): used only by services.gates through the same function; the public API refuses them.
GATE_TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "gate_start": (("submitted",), "validating"),
    "gate_pass": (("validating",), "accepted"),
    "gate_fail": (("validating",), "needs_changes"),
    "gate_return": (("validating",), "submitted"),
}
# Only the assignee may run these actions on its task.
ASSIGNEE_ONLY = ("start", "pause", "resume", "submit")
_EVENT_OF_ACTION = {
    "claim": "task.claimed", "unclaim": "task.unclaimed", "start": "task.started", "pause": "task.paused",
    "resume": "task.resumed", "submit": "task.submitted", "accept": "task.accepted",
    "request_changes": "task.changes_requested", "fail": "task.failed", "cancel": "task.cancelled",
}
# Stored states whose file_scope is locked against other assignees.
_LOCKING_STATES = ("claimed", "running", "paused", "validating", "needs_changes")

EVENT_TYPES = (
    "group.created", "group.updated", "task.created", "dependency.added", "dependency.removed",
    "task.claimed", "task.unclaimed", "task.started", "task.paused", "task.resumed",
    "task.submitted", "task.accepted", "task.changes_requested", "task.failed",
    "task.cancelled", "task.ready",
)
MAX_EVENT_PAYLOAD_BYTES = 2048

_GROUP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _invalid(field: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": field, **details})


def _conflict(message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_STATE, message, status=409, details=details)


def _missing(kind: str, identifier: str) -> ServiceError:
    return ServiceError(NOT_FOUND, f"{kind} not found: {identifier}", status=404, details={kind.lower(): identifier})


def _text(field: str, value: Any, *, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise _invalid(field, f"{field} must be text")
    value = value.strip()
    if required and not value:
        raise _invalid(field, f"{field} is required")
    if len(value) > maximum:
        raise _invalid(field, f"{field} is longer than {maximum} characters")
    return value


def _identifier(field: str, value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _invalid(field, f"Invalid {field}: {value!r}")
    return value


def _requires(value: Any, field: str = "requires") -> str:
    if value not in REQUIRES:
        raise _invalid(field, f"{field} must be one of {', '.join(REQUIRES)}")
    return value


def _paths(value: Iterable[str]) -> list[str]:
    if isinstance(value, str):
        raise _invalid("file_scope", "file_scope must be a list of paths")
    paths: list[str] = []
    for raw in value:
        path = _text("file_scope", raw, maximum=300).rstrip("/")
        parts = path.split("/")
        if "\\" in path or path.startswith("/") or ":" in path or ".." in parts or "" in parts:
            raise _invalid("file_scope", f"Invalid path (relative, '/' separators, no '..'): {raw!r}")
        if path not in paths:
            paths.append(path)
    if len(paths) > 100:
        raise _invalid("file_scope", "file_scope has more than 100 paths")
    return paths


def _labels(value: Iterable[str]) -> list[str]:
    if isinstance(value, str):
        raise _invalid("requirements", "requirements must be a list")
    labels = [_text("requirements", item, maximum=100) for item in value]
    if len(labels) > 50:
        raise _invalid("requirements", "requirements has more than 50 items")
    return list(dict.fromkeys(labels))


def _overlaps(one: str, other: str) -> bool:
    return one == other or other.startswith(one + "/") or one.startswith(other + "/")


class TaskServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._store: TaskStore | None = None

    @property
    def store(self) -> TaskStore:
        # Created on first use: the facade can be built around a system without a database.
        if self._store is None:
            self._store = TaskStore(self._services.system.db)
        return self._store

    # -------- helpers --------
    def _snapshot(self, conn: sqlite3.Connection) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        tasks = {task["id"]: task for task in self.store.list_tasks(conn=conn)}
        return tasks, self.store.dependencies(conn=conn)

    @staticmethod
    def _states(tasks: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
        return {task_id: task["state"] for task_id, task in tasks.items()}

    def _view(self, task: Mapping[str, Any], states: Mapping[str, str], deps: list[dict[str, Any]]) -> dict[str, Any]:
        own = [dep for dep in deps if dep["task_id"] == task["id"]]
        return {
            "id": task["id"],
            "group_id": task["group_id"],
            "title": task["title"],
            "objective": task["objective"],
            "state": task["state"],
            "status": graph.effective_status(task["id"], states, deps),
            "blockers": graph.blockers(task["id"], states, deps),
            "depends_on": [{"task_id": dep["depends_on_id"], "requires": dep["requires"],
                            "satisfied": graph.is_satisfied(dep["requires"], states.get(dep["depends_on_id"]))}
                           for dep in own],
            "dependents": graph.dependents(task["id"], deps),
            "assignee": task["assignee"],
            "preferred": task["preferred"],
            "requirements": task["requirements"],
            "file_scope": task["file_scope"],
            "attempt": task["attempt"],
            "revision": task["revision"],
            "created_by": task["created_by"],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
        }

    def _task_view(self, task_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
        tasks, deps = self._snapshot(conn)
        return self._view(tasks[task_id], self._states(tasks), deps)

    def _group_view(self, group: Mapping[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
        tasks, deps = self._snapshot(conn)
        states = self._states(tasks)
        counts: dict[str, int] = {}
        for task_id, task in tasks.items():
            if task["group_id"] == group["id"]:
                status = graph.effective_status(task_id, states, deps)
                counts[status] = counts.get(status, 0) + 1
        return {**group, "counts": counts}

    def _group_or_missing(self, group_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
        group = self.store.get_group(group_id, conn=conn)
        if group is None:
            raise _missing("Group", group_id)
        return group

    def _bump(self, conn: sqlite3.Connection, task: Mapping[str, Any]) -> None:
        self.store.update_task(conn, task["id"], expected_revision=task["revision"])

    def _announce_ready(self, conn: sqlite3.Connection, changed: str, before: Mapping[str, str],
                        after: Mapping[str, str], deps: list[dict[str, Any]], tasks: Mapping[str, Mapping[str, Any]],
                        actor: str, cause: str) -> None:
        for dependent in graph.newly_ready(changed, before, after, deps):
            self.store.append_event(conn, "task.ready", actor=actor, group_id=tasks[dependent]["group_id"],
                                    task_id=dependent, cause=cause, payload={"from": "blocked", "to": "ready"})

    # -------- groups --------
    @service_errors
    def create_group(self, group_id: str, title: str, *, actor: str, authorized: bool = False,
                     default_requires: str = "accepted") -> dict[str, Any]:
        """Create a group (a stage of work); event ``group.created``.

        Errors: ``invalid_request`` (id, title or ``default_requires``),
        ``invalid_state`` (the group already exists).
        """
        group_id = _identifier("group_id", group_id, _GROUP_ID)
        title = _text("title", title, maximum=200)
        actor = _text("actor", actor, maximum=100)
        default_requires = _requires(default_requires, "default_requires")
        try:
            with self.store.transaction() as conn:
                if self.store.get_group(group_id, conn=conn) is not None:
                    raise _conflict(f"Group already exists: {group_id}", group_id=group_id)
                group = self.store.insert_group(conn, group_id, title, actor=actor, authorized=bool(authorized),
                                                default_requires=default_requires)
                self.store.append_event(conn, "group.created", actor=actor, group_id=group_id,
                                        payload={"authorized": group["authorized"], "default_requires": default_requires})
                return self._group_view(group, conn)
        except sqlite3.IntegrityError:
            raise _conflict(f"Group already exists: {group_id}", group_id=group_id) from None

    @service_errors
    def update_group(self, group_id: str, *, actor: str, title: str | None = None,
                     authorized: bool | None = None, default_requires: str | None = None) -> dict[str, Any]:
        """Change the title, the authorization or the default ``requires``; event ``group.updated``.

        Changing ``default_requires`` affects dependencies added afterwards only.
        Errors: ``not_found``, ``invalid_request``.
        """
        actor = _text("actor", actor, maximum=100)
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = _text("title", title, maximum=200)
        if authorized is not None:
            changes["authorized"] = bool(authorized)
        if default_requires is not None:
            changes["default_requires"] = _requires(default_requires, "default_requires")
        with self.store.transaction() as conn:
            self._group_or_missing(group_id, conn)
            group = self.store.update_group(conn, group_id, **changes)
            self.store.append_event(conn, "group.updated", actor=actor, group_id=group_id, payload=changes)
            return self._group_view(group, conn)  # type: ignore[arg-type]

    @service_errors
    def get_group(self, group_id: str) -> dict[str, Any]:
        """The group with ``counts`` of its tasks per effective status. Errors: ``not_found``."""
        with self.store.db.connect() as conn:
            return self._group_view(self._group_or_missing(group_id, conn), conn)

    @service_errors
    def list_groups(self) -> list[dict[str, Any]]:
        """Every group, oldest first, with ``counts``."""
        with self.store.db.connect() as conn:
            return [self._group_view(group, conn) for group in self.store.list_groups(conn=conn)]

    # -------- tasks and dependencies --------
    @service_errors
    def create_task(self, group_id: str, task_id: str, title: str, *, actor: str, objective: str = "",
                    depends_on: Iterable[str | Mapping[str, str]] = (), file_scope: Iterable[str] = (),
                    requirements: Iterable[str] = (), preferred: str = "") -> dict[str, Any]:
        """Create an ``open`` task; events ``task.created`` (with its initial status) and ``dependency.added``.

        ``depends_on`` holds task ids or ``{"task_id": ..., "requires": ...}``;
        without ``requires`` the group's default applies. ``file_scope`` holds
        relative paths or folders (``/`` separators, no ``..``).
        Errors: ``not_found`` (group or dependency), ``invalid_request`` (ids,
        formats, a cycle), ``invalid_state`` (the task already exists).
        """
        task_id = _identifier("task_id", task_id, _TASK_ID)
        title = _text("title", title, maximum=200)
        actor = _text("actor", actor, maximum=100)
        objective = _text("objective", objective, maximum=4000, required=False)
        preferred = _text("preferred", preferred, maximum=100, required=False)
        scope = _paths(file_scope)
        labels = _labels(requirements)
        if isinstance(depends_on, (str, Mapping)):
            raise _invalid("depends_on", "depends_on must be a list")
        wanted: list[tuple[str, str | None]] = []
        for item in depends_on:
            if isinstance(item, Mapping):
                dep_id, requires = item.get("task_id"), item.get("requires")
            else:
                dep_id, requires = item, None
            dep_id = _identifier("depends_on", dep_id, _TASK_ID)
            if requires is not None:
                requires = _requires(requires)
            if dep_id in {existing for existing, _ in wanted}:
                raise _invalid("depends_on", f"Repeated dependency: {dep_id}")
            wanted.append((dep_id, requires))
        try:
            with self.store.transaction() as conn:
                group = self._group_or_missing(group_id, conn)
                if self.store.get_task(task_id, conn=conn) is not None:
                    raise _conflict(f"Task already exists: {task_id}", task_id=task_id)
                for dep_id, _ in wanted:
                    if dep_id == task_id:
                        raise _invalid("depends_on", "A task cannot depend on itself", cycle=[task_id, task_id])
                    if self.store.get_task(dep_id, conn=conn) is None:
                        raise _missing("Task", dep_id)
                self.store.insert_task(conn, task_id, group_id, title, actor=actor, objective=objective,
                                       preferred=preferred, requirements=labels, file_scope=scope)
                added = []
                for dep_id, requires in wanted:
                    requires = requires or group["default_requires"]
                    self.store.insert_dependency(conn, task_id, dep_id, requires)
                    added.append((dep_id, requires))
                view = self._task_view(task_id, conn)
                self.store.append_event(conn, "task.created", actor=actor, group_id=group_id, task_id=task_id,
                                        payload={"status": view["status"]})
                for dep_id, requires in added:
                    self.store.append_event(conn, "dependency.added", actor=actor, group_id=group_id,
                                            task_id=task_id, payload={"depends_on_id": dep_id, "requires": requires})
                return view
        except sqlite3.IntegrityError:
            raise _conflict(f"Task already exists: {task_id}", task_id=task_id) from None

    @service_errors
    def get_task(self, task_id: str) -> dict[str, Any]:
        """The task with ``status``, ``blockers``, ``depends_on`` (with ``satisfied``) and ``dependents``.

        Errors: ``not_found``.
        """
        with self.store.db.connect() as conn:
            tasks, deps = self._snapshot(conn)
        if task_id not in tasks:
            raise _missing("Task", task_id)
        return self._view(tasks[task_id], self._states(tasks), deps)

    @service_errors
    def list_tasks(self, *, group_id: str | None = None, status: str | None = None,
                   assignee: str | None = None) -> list[dict[str, Any]]:
        """Tasks filtered by group, effective status and assignee, in creation order.

        Errors: ``invalid_request`` (unknown ``status``).
        """
        if status is not None and status not in STATUSES:
            raise _invalid("status", f"status must be one of {', '.join(STATUSES)}")
        with self.store.db.connect() as conn:
            tasks, deps = self._snapshot(conn)
        states = self._states(tasks)
        views = [self._view(task, states, deps) for task in tasks.values()]
        return [view for view in views
                if (group_id is None or view["group_id"] == group_id)
                and (status is None or view["status"] == status)
                and (assignee is None or view["assignee"] == assignee)]

    @service_errors
    def ready_tasks(self, *, group_id: str | None = None) -> list[dict[str, Any]]:
        """``ready`` tasks of authorized groups, in creation order."""
        with self.store.db.connect() as conn:
            authorized = {group["id"] for group in self.store.list_groups(conn=conn) if group["authorized"]}
        return [task for task in self.list_tasks(group_id=group_id, status="ready") if task["group_id"] in authorized]

    @service_errors
    def add_dependency(self, task_id: str, depends_on_id: str, *, actor: str,
                       requires: str | None = None) -> dict[str, Any]:
        """Make ``task_id`` depend on ``depends_on_id``; event ``dependency.added``.

        Errors: ``not_found``, ``invalid_request`` (a cycle or an unknown
        ``requires``), ``invalid_state`` (``task_id`` is terminal).
        """
        actor = _text("actor", actor, maximum=100)
        if requires is not None:
            requires = _requires(requires)
        with self.store.transaction() as conn:
            tasks, deps = self._snapshot(conn)
            for identifier in (task_id, depends_on_id):
                if identifier not in tasks:
                    raise _missing("Task", identifier)
            task = tasks[task_id]
            if task["state"] in TERMINAL_STATES:
                raise _conflict(f"Task {task_id} is {task['state']}", task_id=task_id, state=task["state"])
            if any(d["task_id"] == task_id and d["depends_on_id"] == depends_on_id for d in deps):
                raise _conflict(f"{task_id} already depends on {depends_on_id}", task_id=task_id,
                                depends_on_id=depends_on_id)
            cycle = graph.would_create_cycle(deps, task_id, depends_on_id)
            if cycle:
                raise _invalid("depends_on_id", "Dependency cycle: " + " -> ".join(cycle), cycle=cycle)
            requires = requires or self._group_or_missing(task["group_id"], conn)["default_requires"]
            self.store.insert_dependency(conn, task_id, depends_on_id, requires)
            self._bump(conn, task)
            self.store.append_event(conn, "dependency.added", actor=actor, group_id=task["group_id"], task_id=task_id,
                                    payload={"depends_on_id": depends_on_id, "requires": requires})
            return self._task_view(task_id, conn)

    @service_errors
    def remove_dependency(self, task_id: str, depends_on_id: str, *, actor: str) -> dict[str, Any]:
        """Remove a dependency; event ``dependency.removed`` and ``task.ready`` if it unblocks the task.

        Errors: ``not_found``.
        """
        actor = _text("actor", actor, maximum=100)
        with self.store.transaction() as conn:
            tasks, deps = self._snapshot(conn)
            if task_id not in tasks:
                raise _missing("Task", task_id)
            task = tasks[task_id]
            states = self._states(tasks)
            was = graph.effective_status(task_id, states, deps)
            if not self.store.delete_dependency(conn, task_id, depends_on_id):
                raise ServiceError(NOT_FOUND, f"{task_id} does not depend on {depends_on_id}", status=404,
                                   details={"task_id": task_id, "depends_on_id": depends_on_id})
            self._bump(conn, task)
            self.store.append_event(conn, "dependency.removed", actor=actor, group_id=task["group_id"],
                                    task_id=task_id, payload={"depends_on_id": depends_on_id})
            remaining = [d for d in deps if not (d["task_id"] == task_id and d["depends_on_id"] == depends_on_id)]
            if was == "blocked" and graph.effective_status(task_id, states, remaining) == "ready":
                self.store.append_event(conn, "task.ready", actor=actor, group_id=task["group_id"], task_id=task_id,
                                        cause=f"dependency {depends_on_id} removed",
                                        payload={"from": "blocked", "to": "ready"})
            return self._task_view(task_id, conn)

    @service_errors
    def transition(self, task_id: str, action: str, *, actor: str, cause: str = "",
                   expected_revision: int | None = None) -> dict[str, Any]:
        """The single entry point for state changes (see ``TRANSITIONS``).

        ``claim`` also requires an authorized group and a ``file_scope`` that
        does not overlap an active task of another assignee (same path, or one
        contains the other). ``start``, ``pause``, ``resume`` and ``submit``
        are for the assignee only. ``start`` increments ``attempt``. After the
        change, every dependent that became ``ready`` gets a ``task.ready``
        event. With ``expected_revision``, a different current revision fails.
        Errors: ``not_found``, ``invalid_request`` (unknown action),
        ``invalid_state`` (status, authorization, assignee, overlap or revision;
        ``details`` names what failed).
        """
        if action not in TRANSITIONS:
            raise _invalid("action", f"action must be one of {', '.join(TRANSITIONS)}")
        actor = _text("actor", actor, maximum=100)
        cause = _text("cause", cause, maximum=500, required=False)
        if expected_revision is not None and (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)):
            raise _invalid("expected_revision", "expected_revision must be an integer")
        with self.store.transaction() as conn:
            return self._apply(conn, task_id, action, actor=actor, cause=cause, expected_revision=expected_revision)

    def _apply(self, conn: sqlite3.Connection, task_id: str, action: str, *, actor: str, cause: str = "",
               expected_revision: int | None = None, gate: bool = False, event: str | None = None,
               payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Apply one transition inside the caller's transaction (the body of ``transition``).

        ``gate=True`` allows the internal gate actions (``GATE_TRANSITIONS``);
        the gate services use it so that results, events and the state change
        commit together. ``event`` and ``payload`` override the event type and
        extend its payload for gate actions.
        """
        table = GATE_TRANSITIONS if gate else TRANSITIONS
        if action not in table:
            raise _invalid("action", f"action must be one of {', '.join(table)}")
        allowed, target = table[action]
        tasks, deps = self._snapshot(conn)
        if task_id not in tasks:
            raise _missing("Task", task_id)
        task = tasks[task_id]
        before = self._states(tasks)
        status = graph.effective_status(task_id, before, deps)
        if expected_revision is not None and expected_revision != task["revision"]:
            raise _conflict(f"Task {task_id} is at revision {task['revision']}", task_id=task_id,
                            expected_revision=expected_revision, revision=task["revision"])
        if status not in allowed:
            raise _conflict(f"Cannot {action} a task that is {status}", task_id=task_id, status=status,
                            action=action, allowed=list(allowed))
        if action in ASSIGNEE_ONLY and actor != task["assignee"]:
            raise _conflict(f"Only the assignee ({task['assignee'] or 'none'}) can {action} {task_id}",
                            task_id=task_id, assignee=task["assignee"], actor=actor)
        assignee = task["assignee"]
        attempt = task["attempt"]
        if action == "accept":
            # With a gate (ORQ-2), passing it is the only way to accept.
            checks = task["gate"] or self._group_or_missing(task["group_id"], conn).get("default_gate", [])
            if checks:
                raise _conflict(f"{task_id} has a gate; run it to accept the task", task_id=task_id, gate=checks)
        if action == "claim":
            group = self._group_or_missing(task["group_id"], conn)
            if not group["authorized"]:
                raise _conflict(f"Group {group['id']} is not authorized", task_id=task_id, group_id=group["id"])
            conflicts = []
            for other in tasks.values():
                if other["id"] == task_id or other["state"] not in _LOCKING_STATES or other["assignee"] == actor:
                    continue
                shared = [path for path in other["file_scope"]
                          if any(_overlaps(path, mine) for mine in task["file_scope"])]
                if shared:
                    conflicts.append({"task_id": other["id"], "assignee": other["assignee"],
                                      "status": other["state"], "paths": shared})
            if conflicts:
                raise _conflict(f"Files of {task_id} are locked by other tasks", task_id=task_id, conflicts=conflicts)
            assignee = actor
        elif action == "unclaim":
            assignee = ""
        elif action == "start":
            attempt += 1
        try:
            self.store.update_task(conn, task_id, expected_revision=task["revision"], state=target,
                                   assignee=assignee, attempt=attempt)
        except StaleRevision as stale:
            raise _conflict(str(stale), task_id=task_id, revision=stale.current) from None
        after = {**before, task_id: target}
        self.store.append_event(conn, event or _EVENT_OF_ACTION[action], actor=actor, group_id=task["group_id"],
                                task_id=task_id, cause=cause,
                                payload={"from": status, "to": graph.effective_status(task_id, after, deps),
                                         **(payload or {})})
        self._announce_ready(conn, task_id, before, after, deps, tasks, actor, f"dependency {task_id} {target}")
        return self._task_view(task_id, conn)

    # -------- team event log --------
    @service_errors
    def events(self, *, after: int = 0, group_id: str | None = None, task_id: str | None = None,
               limit: int = 500) -> list[dict[str, Any]]:
        """Team events with ``seq`` greater than ``after``, oldest first, at most ``limit`` (1..1000).

        ``seq`` is one team-wide sequence: unfiltered reads see consecutive
        numbers; filtered reads skip the events of other groups or tasks.
        Errors: ``invalid_request`` (``after`` or ``limit``).
        """
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise _invalid("after", "after must be an integer >= 0", after=repr(after))
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise _invalid("limit", "limit must be an integer between 1 and 1000", limit=repr(limit))
        return self.store.events(after=after, group_id=group_id, task_id=task_id, limit=limit)
