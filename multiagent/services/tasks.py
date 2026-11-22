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

Activities (amendment 1 of PRY-T01): a task and a group carry a ``kind``. An ``activity`` is a lightweight task, a
chat or a small piece of work linked to a project, that lives in the project's ``<key>-ACT`` group (kind
``activities``). It has no dependencies on either end, an empty ``file_scope`` (read-only) and no gate, and ``submit``
accepts it in the same transaction (event ``task.accepted``, cause ``activity_closed``): nobody reviews it. They are
created with ``services.activities.start``, never with ``create_task``.

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
import hashlib
import json
import uuid
from datetime import datetime, timezone
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from ..db.task_store import StaleRevision, TaskStore
from ..tasks import graph, labels, release as release_policy
from ..tasks.envelopes import (EnvelopeError, MAX_CONTEXT_REFS, MAX_CONSTRAINTS, MAX_CONSTRAINT_CHARS,
                               parse_reference, validate_reference)
from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors
from .hold import continuity_of, merge_continuity

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
    "reopen": (("submitted", "accepted", "failed", "cancelled"), "open"),
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
    "request_changes": "task.changes_requested", "fail": "task.failed", "cancel": "task.cancelled", "reopen": "task.reopened",
}
# Stored states whose file_scope is locked against other assignees.
_LOCKING_STATES = ("claimed", "running", "paused", "validating", "needs_changes")

EVENT_TYPES = (
    "group.created", "group.updated", "task.created", "dependency.added", "dependency.removed",
    "group.release_mode_changed", "legacy_dependency.added", "legacy_dependency.removed",
    "release.delivery_recorded", "release.test_recorded", "release.integration_recorded", "release.facts_reset",
    "task.claimed", "task.unclaimed", "task.started", "task.paused", "task.resumed",
    "task.submitted", "task.accepted", "task.changes_requested", "task.failed",
    "task.cancelled", "task.reopened", "task.scope_updated", "group.lifecycle_changed", "task.ready",
    # PRY (contract PRY-T01 section 8): projects, the plans of stages and tasks, and the links to chats.
    "project.created", "project.updated", "project.archived", "project.unarchived", "project.stage_attached",
    "project.stages_reordered", "project.remote_model_acknowledged", "group.plan_updated", "task.plan_updated",
    "task.session_linked", "task.session_unlinked",
    "task.wait_set", "task.wait_cleared", "task.wait_resolved",
)
MAX_EVENT_PAYLOAD_BYTES = 2048

_GROUP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SNAPSHOT_STATES = ("open", "claimed", "paused", "needs_changes", "submitted")
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


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


def _human_wait_actor(actor: str) -> bool:
    """Human labels using the shared actor vocabulary; these do not authenticate."""
    from .projects import actor_kind
    return actor_kind(actor) in ("user", "person")


def _wait_payload(value: dict[str, Any]) -> dict[str, Any]:
    if len(_canonical_json(value).encode("utf-8")) > 1400:
        raise _invalid("waiting", "waiting metadata or decision exceeds 1400 UTF-8 bytes")
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


MAX_CONTINUITY_BYTES = 16384


def _continuity_changes(value: Any) -> dict[str, Any] | None:
    """The changes to the continuity block, checked: a mapping of text keys to JSON data (``None`` removes a key)."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or not all(isinstance(key, str) and key for key in value):
        raise _invalid("continuity", "continuity must be a mapping with non-empty text keys")
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise _invalid("continuity", "continuity must hold JSON data") from None
    if len(encoded.encode("utf-8")) > MAX_CONTINUITY_BYTES:
        raise _invalid("continuity", f"continuity is larger than {MAX_CONTINUITY_BYTES} bytes")
    return dict(value)


def _overlaps(one: str, other: str) -> bool:
    return one == other or other.startswith(one + "/") or one.startswith(other + "/")


class TaskServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._store: TaskStore | None = None
        self.wait_clock = lambda: datetime.now(timezone.utc)

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

    def _release_context(self, conn: sqlite3.Connection) -> dict[str, Any]:
        groups = {group["id"]: group for group in self.store.list_groups(conn=conn)}
        return {"groups": groups,
                "dependencies": self.store.legacy_release_dependencies(conn=conn),
                "facts": self.store.release_facts(conn=conn)}

    @staticmethod
    def _release_edge_view(edge: Mapping[str, Any], tasks: Mapping[str, Mapping[str, Any]],
                           states: Mapping[str, str], context: Mapping[str, Any]) -> dict[str, Any]:
        dependent = tasks.get(edge["task_id"], {})
        group = context["groups"].get(dependent.get("group_id"), {})
        mode = group.get("legacy_release_mode")
        facts = context["facts"].get(edge["depends_on_id"], release_policy.empty_facts(edge["depends_on_id"]))
        satisfied, reason, refs = release_policy.evaluate(mode, facts)
        prerequisite = edge["depends_on_id"]
        return {"kind": "legacy_release", "task_id": prerequisite, "threshold": mode,
                "source_ref": edge["source_ref"], "prerequisite": {"state": states.get(prerequisite),
                "status": states.get(prerequisite)}, "satisfied": satisfied, "reason": reason,
                "evidence": refs}

    @classmethod
    def _effective_status(cls, task_id: str, states: Mapping[str, str], native: list[dict[str, Any]],
                          tasks: Mapping[str, Mapping[str, Any]], context: Mapping[str, Any] | None = None) -> str:
        state = states[task_id]
        if state != "open":
            return state
        if graph.blockers(task_id, states, native):
            return "blocked"
        if context is not None:
            if any(edge["task_id"] == task_id and not cls._release_edge_view(edge, tasks, states, context)["satisfied"]
                   for edge in context["dependencies"]):
                return "blocked"
        return "ready"

    @classmethod
    def _blockers(cls, task_id: str, states: Mapping[str, str], native: list[dict[str, Any]],
                  tasks: Mapping[str, Mapping[str, Any]], context: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        found = graph.blockers(task_id, states, native)
        if context is not None:
            for edge in context["dependencies"]:
                if edge["task_id"] == task_id:
                    item = cls._release_edge_view(edge, tasks, states, context)
                    if not item["satisfied"]:
                        found.append(item)
        return found

    @classmethod
    def _status_map(cls, tasks: Mapping[str, Mapping[str, Any]], native: list[dict[str, Any]],
                    context: Mapping[str, Any] | None = None) -> dict[str, str]:
        states = cls._states(tasks)
        return {task_id: cls._effective_status(task_id, states, native, tasks, context) for task_id in tasks}

    def _emit_newly_ready(self, conn: sqlite3.Connection, before: Mapping[str, str], after: Mapping[str, str],
                          tasks: Mapping[str, Mapping[str, Any]], actor: str, cause: str) -> None:
        for task_id, status in after.items():
            if before.get(task_id) == "blocked" and status == "ready":
                self.store.append_event(conn, "task.ready", actor=actor, group_id=tasks[task_id]["group_id"],
                                        task_id=task_id, cause=cause, payload={"from": "blocked", "to": "ready"})

    @staticmethod
    def _states(tasks: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
        return {task_id: task["state"] for task_id, task in tasks.items()}

    @staticmethod
    def _released(task: Mapping[str, Any], group: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
        """Whether a ``submitted`` task counts as done: its group has a legacy release mode and the evidence releases."""
        mode = group.get("legacy_release_mode")
        if task["state"] != "submitted" or mode is None:
            return False
        facts = context["facts"].get(task["id"], release_policy.empty_facts(task["id"]))
        return release_policy.evaluate(mode, facts)[0]

    def _label_key(self, task: Mapping[str, Any], status: str, group: Mapping[str, Any],
                   context: Mapping[str, Any]) -> str:
        return labels.task_label_key(task["state"], status, authorized=bool(group.get("authorized")),
                                     released=self._released(task, group, context))

    def _view(self, task: Mapping[str, Any], states: Mapping[str, str], deps: list[dict[str, Any]],
              context: Mapping[str, Any] | None = None,
              tasks: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
        tasks = tasks or {str(task["id"]): task}
        status = self._effective_status(task["id"], states, deps, tasks, context)
        group = (context["groups"].get(task["group_id"]) if context is not None else None)             or self.store.get_group(task["group_id"]) or {}
        label_context = context if context is not None else {"facts": {}}
        own = [dep for dep in deps if dep["task_id"] == task["id"]]
        legacy = ([edge for edge in context["dependencies"] if edge["task_id"] == task["id"]]
                  if context is not None else [])
        view = {
            "id": task["id"],
            "group_id": task["group_id"],
            "kind": task.get("kind", "task"),
            "title": task["title"],
            "objective": task["objective"],
            "state": task["state"],
            "status": status,
            "label": labels.task_label(task["state"], status, authorized=bool(group.get("authorized")),
                                       released=self._released(task, group, label_context)),
            "blockers": self._blockers(task["id"], states, deps, tasks, context),
            "depends_on": [{"task_id": dep["depends_on_id"], "requires": dep["requires"],
                            "satisfied": graph.is_satisfied(dep["requires"], states.get(dep["depends_on_id"]))}
                           for dep in own],
            "dependents": list(dict.fromkeys(graph.dependents(task["id"], deps) +
                                               ([edge["task_id"] for edge in context["dependencies"]
                                                 if edge["depends_on_id"] == task["id"]]
                                                if context is not None else []))),
            "release_dependencies": [self._release_edge_view(edge, tasks, states, context)
                                     for edge in legacy],
            "assignee": task["assignee"],
            "preferred": task["preferred"],
            "requirements": task["requirements"],
            "file_scope": task["file_scope"],
            "lock_policy": task.get("lock_policy", {}),
            "workspace_domain": task.get("workspace_domain", ""),
            "attempt": task["attempt"],
            "revision": task["revision"],
            "created_by": task["created_by"],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
            "waiting": task.get("waiting") or None,
        }
        continuity = continuity_of(task)
        if continuity:
            # The wait of a task that hit a limit (CNT-T04): the hold and the counters of the continuity block.
            view["continuity"] = continuity
        provenance = self.store.snapshot_provenance("task", str(task["id"]))
        if provenance:
            view["provenance"] = provenance
            view["attempt_history"] = "unknown"
        return view

    def _task_view(self, task_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
        tasks, deps = self._snapshot(conn)
        return self._view(tasks[task_id], self._states(tasks), deps, self._release_context(conn), tasks)

    def _group_view(self, group: Mapping[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
        tasks, deps = self._snapshot(conn)
        context = self._release_context(conn)
        states = self._states(tasks)
        counts: dict[str, int] = {}
        keys: list[str] = []
        for task_id, task in tasks.items():
            if task["group_id"] == group["id"]:
                status = self._effective_status(task_id, states, deps, tasks, context)
                counts[status] = counts.get(status, 0) + 1
                keys.append(self._label_key(task, status, group, context))
        if group.get("kind") == "activities":
            # The activities group is never verified or closed: only the unfinished ones decide its label.
            keys = [key for key in keys if key not in ("done", "cancelled", "failed")]
            label = labels.stage_label("open", bool(group["authorized"]), keys)
        else:
            label = labels.stage_label(group["lifecycle_state"], bool(group["authorized"]), keys)
        result = {**group, "counts": counts, "label": label}
        provenance = self.store.snapshot_provenance("group", str(group["id"]), conn=conn)
        if provenance:
            result["provenance"] = provenance
        return result

    def _group_or_missing(self, group_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
        group = self.store.get_group(group_id, conn=conn)
        if group is None:
            raise _missing("Group", group_id)
        return group

    @staticmethod
    def _assert_project_active(conn: sqlite3.Connection, group: Mapping[str, Any]) -> None:
        """An archived project admits no claim and no structure change in its stages (PRY-T01 section 2)."""
        project_id = group.get("project_id")
        if not project_id:
            return
        row = conn.execute("SELECT archived_at FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is not None and row["archived_at"] is not None:
            raise _conflict(f"Project {project_id} is archived", project_id=project_id, group_id=group["id"])

    @staticmethod
    def _reject_activity_edge(*tasks: Mapping[str, Any]) -> None:
        """A dependency never touches an activity, on either end (amendment 1 of PRY-T01): ``invalid_request``."""
        for task in tasks:
            if task.get("kind") == "activity":
                raise _invalid("depends_on", f"{task['id']} is an activity; activities have no dependencies",
                               task_id=task["id"], kind="activity")

    def _bump(self, conn: sqlite3.Connection, task: Mapping[str, Any]) -> None:
        self.store.update_task(conn, task["id"], expected_revision=task["revision"])

    def _announce_ready(self, conn: sqlite3.Connection, changed: str, before: Mapping[str, str],
                        after: Mapping[str, str], deps: list[dict[str, Any]], tasks: Mapping[str, Mapping[str, Any]],
                        actor: str, cause: str) -> None:
        for dependent in graph.newly_ready(changed, before, after, deps):
            self.store.append_event(conn, "task.ready", actor=actor, group_id=tasks[dependent]["group_id"],
                                    task_id=dependent, cause=cause, payload={"from": "blocked", "to": "ready"})

    # -------- native snapshots (ORQ-4 A) --------
    def _normalize_snapshot_release_facts(self, task_id: str, raw: Any) -> dict[str, Any] | None:
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise _invalid("delivery", "delivery must be null or an evidence object", task_id=task_id)
        allowed = {"delivery_ref", "delivery_sha256", "has_patch", "patchset_sha256", "test_verdict",
                   "test_patchset_sha256", "test_ref", "integration_commit", "integration_ref"}
        if set(raw) - allowed or not {"delivery_ref", "delivery_sha256", "has_patch", "patchset_sha256"} <= set(raw):
            raise _invalid("delivery", "delivery requires delivery ref/hash, has_patch and patchset hash",
                           task_id=task_id, fields=sorted(set(raw) - allowed))
        delivery_ref = self._release_ref("delivery_ref", raw["delivery_ref"])
        delivery_sha256 = self._release_sha("delivery_sha256", raw["delivery_sha256"])
        has_patch = raw["has_patch"]
        if not isinstance(has_patch, bool):
            raise _invalid("has_patch", "has_patch must be boolean", task_id=task_id)
        patchset_sha256 = self._release_sha("patchset_sha256", raw["patchset_sha256"], required=has_patch)
        if not has_patch and patchset_sha256:
            raise _invalid("patchset_sha256", "patchset_sha256 must be empty when has_patch is false", task_id=task_id)
        verdict = raw.get("test_verdict")
        test_patchset = raw.get("test_patchset_sha256", "")
        test_ref = raw.get("test_ref", "")
        if verdict is None:
            if test_patchset or test_ref:
                raise _invalid("test_verdict", "test evidence fields require a verdict", task_id=task_id)
            test_patchset = ""
            test_ref = ""
        else:
            if verdict not in ("passed", "failed"):
                raise _invalid("test_verdict", "test_verdict must be passed or failed", task_id=task_id)
            if not has_patch:
                raise _invalid("test_verdict", "test evidence requires a delivery with a patch", task_id=task_id)
            test_patchset = self._release_sha("test_patchset_sha256", test_patchset)
            if test_patchset != patchset_sha256:
                raise _invalid("test_patchset_sha256", "test evidence must match the delivery patchset", task_id=task_id)
            test_ref = self._release_ref("test_ref", test_ref)
        integration_commit = raw.get("integration_commit", "")
        integration_ref = raw.get("integration_ref", "")
        if bool(integration_commit) != bool(integration_ref):
            raise _invalid("integration_commit", "integration_commit and integration_ref must be provided together",
                           task_id=task_id)
        if integration_commit:
            integration_commit = _text("integration_commit", integration_commit, maximum=40)
            if not _COMMIT.fullmatch(integration_commit):
                raise _invalid("integration_commit", "integration_commit must be 7 to 40 lowercase hex characters",
                               task_id=task_id)
            integration_ref = self._release_ref("integration_ref", integration_ref)
        return {"delivery_ref": delivery_ref, "delivery_sha256": delivery_sha256, "has_patch": has_patch,
                "patchset_sha256": patchset_sha256, "test_verdict": verdict,
                "test_patchset_sha256": test_patchset, "test_ref": test_ref,
                "integration_commit": integration_commit, "integration_ref": integration_ref}

    def _normalize_snapshot(self, snapshot: Mapping[str, Any], *, source_key: str, source_ref: str,
                            snapshot_sha256: str, actor: str, contract_version: int = 2
                            ) -> tuple[dict[str, Any], str]:
        from .gates import normalize_checks
        if not isinstance(snapshot, Mapping):
            raise _invalid("snapshot", "snapshot must be an object")
        if contract_version not in (1, 2):
            raise _invalid("contract_version", "unsupported snapshot contract version")
        for field, value in (("source_key", source_key), ("source_ref", source_ref), ("actor", actor)):
            _text(field, value, maximum=500)
        required = {"groups", "tasks", "dependencies"}
        if set(snapshot) != required:
            raise _invalid("snapshot", "snapshot must contain exactly groups, tasks and dependencies",
                           expected=sorted(required), actual=sorted(map(str, snapshot.keys())))
        body = {"groups": snapshot["groups"], "tasks": snapshot["tasks"],
                "dependencies": snapshot["dependencies"]}
        if any(not isinstance(body[key], list) for key in required):
            raise _invalid("snapshot", "groups, tasks and dependencies must be lists")
        try:
            digest = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            raise _invalid("snapshot", "snapshot must be strict JSON data") from None
        if not isinstance(snapshot_sha256, str) or snapshot_sha256.lower() != digest:
            raise _invalid("snapshot_sha256", "snapshot_sha256 does not match canonical snapshot",
                           expected=digest)
        groups: list[dict[str, Any]] = []
        tasks: list[dict[str, Any]] = []
        deps: list[dict[str, str]] = []
        legacy_deps: list[dict[str, str]] = []
        seen_groups: set[str] = set()
        seen_tasks: set[str] = set()
        for raw in body["groups"]:
            if not isinstance(raw, Mapping):
                raise _invalid("groups", "each group must be an object")
            allowed_group = {"id", "title", "authorized", "default_requires", "default_gate", "source_id",
                             "source_state"}
            if contract_version >= 2:
                allowed_group.update({"legacy_release_mode", "lifecycle_state"})
            if set(raw) - allowed_group:
                raise _invalid("groups", "unsupported group fields", fields=sorted(set(raw) - allowed_group))
            gid = _identifier("group_id", raw.get("id"), _GROUP_ID)
            if gid in seen_groups:
                raise _invalid("groups", f"Repeated group id: {gid}")
            seen_groups.add(gid)
            title = _text("title", raw.get("title"), maximum=200)
            authorized = raw.get("authorized")
            if not isinstance(authorized, bool):
                raise _invalid("authorized", "group authorized must be boolean")
            req = _requires(raw.get("default_requires", "accepted"), "default_requires")
            gate = raw.get("default_gate", [])
            if not isinstance(gate, list):
                raise _invalid("default_gate", "default_gate must be a list")
            gate = normalize_checks(gate)
            mode = raw.get("legacy_release_mode", "probada") if contract_version >= 2 else None
            if mode is not None and mode not in release_policy.RELEASE_MODES:
                raise _invalid("legacy_release_mode", "legacy_release_mode must be null or a known release mode", group_id=gid)
            lifecycle_state = raw.get("lifecycle_state", "open") if contract_version >= 2 else "open"
            if lifecycle_state not in ("open", "closed"):
                raise _invalid("lifecycle_state", "lifecycle_state must be open or closed", group_id=gid)
            groups.append({"id": gid, "title": title, "authorized": authorized,
                           "lifecycle_state": lifecycle_state,
                           "default_requires": req, "default_gate": gate,
                           "legacy_release_mode": mode,
                           "source_id": _text("source_id", raw.get("source_id", ""), maximum=200, required=False),
                           "source_state": _text("source_state", raw.get("source_state", ""), maximum=100, required=False)})
        for raw in body["tasks"]:
            if not isinstance(raw, Mapping):
                raise _invalid("tasks", "each task must be an object")
            allowed_task = {"id", "group_id", "title", "objective", "state", "assignee", "preferred", "requirements",
                            "file_scope", "lock_policy", "workspace_domain", "base_commit", "context_refs", "constraints",
                            "gate", "source_id", "source_state"}
            if contract_version >= 2:
                allowed_task.add("delivery")
            if set(raw) - allowed_task:
                raise _invalid("tasks", "unsupported task fields", fields=sorted(set(raw) - allowed_task))
            tid = _identifier("task_id", raw.get("id"), _TASK_ID)
            if tid in seen_tasks:
                raise _invalid("tasks", f"Repeated task id: {tid}")
            seen_tasks.add(tid)
            if raw.get("state") not in _SNAPSHOT_STATES:
                raise _invalid("state", "snapshot state must be open, claimed, paused, needs_changes or submitted",
                               task_id=tid)
            gid = _identifier("group_id", raw.get("group_id"), _GROUP_ID)
            state = raw["state"]
            scope = _paths(raw.get("file_scope", []))
            raw_assignee = raw.get("assignee", "")
            assignee = "" if raw_assignee is None else _text("assignee", raw_assignee, maximum=100, required=False)
            if state in ("claimed", "paused", "needs_changes") and not assignee:
                raise _invalid("assignee", f"{state} snapshot task requires its current assignee", task_id=tid)
            raw_domain = raw.get("workspace_domain", "")
            domain = "" if raw_domain is None else _text("workspace_domain", raw_domain, maximum=200, required=False)
            policy = raw.get("lock_policy")
            if policy is None:
                policy = ({"shared_scope": [], "workspace_scope": []} if state in ("open", "needs_changes", "submitted") else
                          {"shared_scope": scope if state == "claimed" else [],
                           "workspace_scope": scope if state == "paused" else []})
            if not isinstance(policy, Mapping) or set(policy) != {"shared_scope", "workspace_scope"}:
                raise _invalid("lock_policy", "lock_policy requires shared_scope and workspace_scope", task_id=tid)
            shared, workspace = _paths(policy["shared_scope"]), _paths(policy["workspace_scope"])
            if any(not any(p == declared or p.startswith(declared + "/") for declared in scope)
                   for p in shared + workspace):
                raise _invalid("lock_policy", "lock scopes must be contained in declared file_scope", task_id=tid)
            if workspace and not domain:
                raise _invalid("workspace_domain", "workspace_domain is required for workspace locks", task_id=tid)
            if state == "claimed" and (shared != scope or workspace):
                raise _invalid("lock_policy", "claimed snapshots must retain the complete global file_scope lock", task_id=tid)
            if state == "paused" and set(shared + workspace) != set(scope):
                raise _invalid("lock_policy", "paused snapshots must partition their complete file_scope", task_id=tid)
            if state == "paused" and set(shared).intersection(workspace):
                raise _invalid("lock_policy", "a paused path cannot be both shared and workspace-scoped", task_id=tid)
            if state in ("open", "needs_changes", "submitted") and (shared or workspace):
                raise _invalid("lock_policy", f"{state} snapshots must not retain locks", task_id=tid)
            base_commit = raw.get("base_commit", "")
            base_commit = _text("base_commit", base_commit, maximum=40, required=False)
            if base_commit and not _COMMIT.fullmatch(base_commit):
                raise _invalid("base_commit", "base_commit must be empty or 7 to 40 lowercase hex characters", task_id=tid)
            refs, constraints = raw.get("context_refs", []), raw.get("constraints", [])
            if not isinstance(refs, list) or len(refs) > MAX_CONTEXT_REFS:
                raise _invalid("context_refs", f"context_refs must be a list of at most {MAX_CONTEXT_REFS}", task_id=tid)
            try:
                refs = list(dict.fromkeys(validate_reference(ref, field="context_refs") for ref in refs))
            except EnvelopeError as exc:
                raise _invalid(exc.field, str(exc), task_id=tid) from None
            if not isinstance(constraints, list) or len(constraints) > MAX_CONSTRAINTS:
                raise _invalid("constraints", f"constraints must be a list of at most {MAX_CONSTRAINTS}", task_id=tid)
            constraints = [_text("constraints", x, maximum=MAX_CONSTRAINT_CHARS) for x in constraints]
            gate = raw.get("gate", [])
            if not isinstance(gate, list):
                raise _invalid("gate", "gate must be a list", task_id=tid)
            gate = normalize_checks(gate)
            release_facts = (self._normalize_snapshot_release_facts(tid, raw.get("delivery"))
                             if contract_version >= 2 else None)
            if contract_version >= 2 and (state == "submitted") != (release_facts is not None):
                raise _invalid("delivery", "submitted snapshots require current delivery evidence and other states must not carry it",
                               task_id=tid)
            tasks.append({"id": tid, "group_id": gid, "title": _text("title", raw.get("title"), maximum=200),
                          "objective": _text("objective", raw.get("objective", ""), maximum=4000, required=False),
                          "state": state, "assignee": assignee,
                          "preferred": _text("preferred", raw.get("preferred", ""), maximum=100, required=False),
                          "requirements": _labels(raw.get("requirements", [])), "file_scope": scope,
                          "lock_policy": {"shared_scope": shared, "workspace_scope": workspace},
                          "workspace_domain": domain, "epoch_pending": state == "paused",
                          "base_commit": base_commit, "context_refs": refs, "constraints": constraints, "gate": gate,
                          "release_facts": release_facts,
                          "source_id": _text("source_id", raw.get("source_id", ""), maximum=200, required=False),
                          "source_state": _text("source_state", raw.get("source_state", ""), maximum=100, required=False)})
        seen_edges: set[tuple[str, str]] = set()
        for raw in body["dependencies"]:
            if not isinstance(raw, Mapping):
                raise _invalid("dependencies", "each dependency must be an object")
            if not {"task_id", "depends_on_id"} <= set(raw):
                raise _invalid("dependencies", "dependency requires task_id and depends_on_id")
            tid = _identifier("task_id", raw["task_id"], _TASK_ID)
            depid = _identifier("depends_on_id", raw["depends_on_id"], _TASK_ID)
            edge = (tid, depid)
            if edge in seen_edges:
                raise _invalid("dependencies", f"Repeated dependency: {tid} -> {depid}")
            seen_edges.add(edge)
            if set(raw) == {"task_id", "depends_on_id", "requires"}:
                req = _requires(raw["requires"])
                deps.append({"task_id": tid, "depends_on_id": depid, "requires": req})
            elif (contract_version >= 2 and set(raw) == {"task_id", "depends_on_id", "kind", "source_ref"}
                  and raw["kind"] == "legacy_release"):
                legacy_deps.append({"task_id": tid, "depends_on_id": depid, "source_ref": self._release_ref("source_ref", raw["source_ref"])})
            else:
                raise _invalid("dependencies", "native dependencies need requires; legacy edges need kind=legacy_release and source_ref")
        normalized = {"groups": groups, "tasks": tasks, "dependencies": deps,
                      "legacy_dependencies": legacy_deps}
        return normalized, digest

    def _check_snapshot_collisions(self, conn: sqlite3.Connection, batch: Mapping[str, Any]) -> None:
        for group in batch["groups"]:
            if self.store.get_group(group["id"], conn=conn):
                raise _conflict(f"Group already exists: {group['id']}", group_id=group["id"])
        for task in batch["tasks"]:
            if self.store.get_task(task["id"], conn=conn):
                raise _conflict(f"Task already exists: {task['id']}", task_id=task["id"])
            if task["group_id"] not in {g["id"] for g in batch["groups"]} and not self.store.get_group(task["group_id"], conn=conn):
                raise _missing("Group", task["group_id"])
        task_ids = {t["id"] for t in batch["tasks"]}
        all_dependencies = batch["dependencies"] + batch["legacy_dependencies"]
        for dep in all_dependencies:
            if dep["task_id"] not in task_ids or dep["depends_on_id"] not in task_ids:
                raise _invalid("dependencies", "snapshot dependencies must connect tasks in this batch")
        try:
            graph.validate_union(task_ids, batch["dependencies"], batch["legacy_dependencies"])
        except graph.GraphError as error:
            raise _invalid("dependencies", str(error), cycle=getattr(error, "cycle", None)) from None
        candidates = [t for t in batch["tasks"] if t["state"] in _LOCKING_STATES]
        existing = self.store.list_tasks(conn=conn)
        pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        for candidate in candidates:
            pairs.extend((candidate, row) for row in existing if row["state"] in _LOCKING_STATES)
        for index, candidate in enumerate(candidates):
            pairs.extend((candidate, other) for other in candidates[index + 1:])
        for candidate, other in pairs:
            if candidate.get("assignee") and candidate.get("assignee") == other.get("assignee"):
                continue
            cpolicy = candidate.get("lock_policy") or {"shared_scope": candidate["file_scope"], "workspace_scope": []}
            opolicy = other.get("lock_policy") or {"shared_scope": other["file_scope"], "workspace_scope": []}
            if "shared_scope" not in opolicy and "workspace_scope" not in opolicy:
                opolicy = {"shared_scope": other["file_scope"], "workspace_scope": []}
            if candidate["state"] == "paused" and other["state"] == "paused":
                overlap = self._policies_conflict(cpolicy, candidate.get("workspace_domain") or None,
                                                  opolicy, other.get("workspace_domain") or None)
            elif candidate["state"] == "paused":
                overlap = self._policies_conflict(cpolicy, candidate.get("workspace_domain") or None,
                                                  opolicy, other.get("workspace_domain") or None, paused_owner=1)
            elif other["state"] == "paused":
                overlap = self._policies_conflict(cpolicy, candidate.get("workspace_domain") or None,
                                                  opolicy, other.get("workspace_domain") or None, paused_owner=2)
            else:
                opolicy = {"shared_scope": list(opolicy.get("shared_scope", []))
                           + list(opolicy.get("workspace_scope", [])), "workspace_scope": []}
                overlap = self._policies_conflict(cpolicy, candidate.get("workspace_domain") or None,
                                                  opolicy, other.get("workspace_domain") or None)
            if overlap:
                raise _conflict("Snapshot contains conflicting active locks", task_id=candidate["id"],
                                conflicts=[{"task_id": other["id"], "paths": overlap}])

    @service_errors
    def plan_snapshot(self, snapshot: Mapping[str, Any], *, source_key: str, source_ref: str,
                      snapshot_sha256: str, actor: str) -> dict[str, Any]:
        batch, digest = self._normalize_snapshot(snapshot, source_key=source_key, source_ref=source_ref,
                                                  snapshot_sha256=snapshot_sha256, actor=actor)
        with self.store.db.connect() as conn:
            self._check_snapshot_collisions(conn, batch)
        return {"status": "planned", "source_key": source_key, "source_ref": source_ref,
                "snapshot_sha256": digest, "groups": [g["id"] for g in batch["groups"]],
                "tasks": [t["id"] for t in batch["tasks"]],
                "dependencies": batch["dependencies"] +
                                [{"task_id": d["task_id"], "depends_on_id": d["depends_on_id"],
                                  "kind": "legacy_release", "source_ref": d["source_ref"]}
                                 for d in batch["legacy_dependencies"]],
                "warnings": ["Imported history is unknown; no transitions, gate results or task results are created."]}

    @service_errors
    def import_snapshot(self, snapshot: Mapping[str, Any], *, source_key: str, source_ref: str,
                        snapshot_sha256: str, actor: str) -> dict[str, Any]:
        actor = _text("actor", actor, maximum=100)
        with self.store.transaction() as conn:
            prior = self.store.snapshot(source_key, conn=conn)
            contract_version = int(prior.get("contract_version", 1)) if prior else 2
            batch, digest = self._normalize_snapshot(snapshot, source_key=source_key, source_ref=source_ref,
                                                      snapshot_sha256=snapshot_sha256, actor=actor,
                                                      contract_version=contract_version)
            if prior:
                if prior["snapshot_sha256"] != digest:
                    raise _conflict("source_key already identifies a different snapshot", source_key=source_key)
                return self._snapshot_receipt(prior, conn)
            raw_snapshot = _canonical_json({"groups": snapshot["groups"], "tasks": snapshot["tasks"],
                                            "dependencies": snapshot["dependencies"]})
            self._check_snapshot_collisions(conn, batch)
            for group in batch["groups"]:
                self.store.insert_group(conn, group["id"], group["title"], actor=actor,
                                        authorized=group["authorized"], default_requires=group["default_requires"],
                                        default_gate=group["default_gate"],
                                        legacy_release_mode=group["legacy_release_mode"],
                                        lifecycle_state=group["lifecycle_state"])
            for task in batch["tasks"]:
                self.store.insert_snapshot_task(conn, task, actor=actor)
                for name in ("context_refs", "constraints"):
                    if not isinstance(task[name], list):
                        raise _invalid(name, f"{name} must be a list", task_id=task["id"])
                self.store.db  # keep the store's transaction boundary explicit
                conn.execute("UPDATE tasks SET base_commit=?,context_refs_json=?,constraints_json=? WHERE id=?",
                             (task["base_commit"], json.dumps(task["context_refs"], ensure_ascii=False, separators=(",", ":")),
                              json.dumps(task["constraints"], ensure_ascii=False, separators=(",", ":")), task["id"]))
                if task["release_facts"] is not None:
                    self.store.write_release_fact(conn, task["id"], actor=actor, **task["release_facts"])
            for dep in batch["dependencies"]:
                self.store.insert_dependency(conn, dep["task_id"], dep["depends_on_id"], dep["requires"])
            for dep in batch["legacy_dependencies"]:
                self.store.insert_legacy_release_dependency(conn, dep["task_id"], dep["depends_on_id"],
                                                            dep["source_ref"])
            seq = self.store.append_event(conn, "snapshot.imported", actor=actor, ref=source_ref,
                                          payload={"source_key": source_key, "snapshot_sha256": digest,
                                                   "groups": len(batch["groups"]), "tasks": len(batch["tasks"]),
                                                   "dependencies": len(batch["dependencies"]) + len(batch["legacy_dependencies"])})
            self.store.insert_snapshot(conn, source_key=source_key, source_ref=source_ref,
                                       snapshot_sha256=digest, actor=actor, snapshot_json=raw_snapshot, event_seq=seq,
                                       contract_version=2)
            for group in batch["groups"]:
                self.store.add_snapshot_object(conn, source_key=source_key, object_type="group",
                                               object_id=group["id"], source_id=group["source_id"],
                                               source_state=group["source_state"])
            for task in batch["tasks"]:
                self.store.add_snapshot_object(conn, source_key=source_key, object_type="task",
                                               object_id=task["id"], source_id=task["source_id"],
                                               source_state=task["source_state"])
            return self._snapshot_receipt(self.store.snapshot(source_key, conn=conn), conn)

    def _snapshot_receipt(self, receipt: Mapping[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
        batch = receipt["snapshot"]
        return {"source_key": receipt["source_key"], "source_ref": receipt["source_ref"],
                "snapshot_sha256": receipt["snapshot_sha256"], "actor": receipt["actor"],
                "imported_at": receipt["imported_at"], "event_seq": receipt["event_seq"],
                "state": receipt["state"], "rollback_actor": receipt["rollback_actor"],
                "rolled_back_at": receipt["rolled_back_at"], "rollback_event_seq": receipt["rollback_event_seq"],
                "groups": [g["id"] for g in batch["groups"]], "tasks": [t["id"] for t in batch["tasks"]],
                "dependencies": batch["dependencies"]}

    @service_errors
    def get_snapshot(self, source_key: str) -> dict[str, Any]:
        with self.store.db.connect() as conn:
            receipt = self.store.snapshot(source_key, conn=conn)
            if receipt is None:
                raise _missing("Snapshot", source_key)
            return self._snapshot_receipt(receipt, conn)

    @service_errors
    def rollback_snapshot(self, source_key: str, *, actor: str) -> dict[str, Any]:
        actor = _text("actor", actor, maximum=100)
        with self.store.transaction() as conn:
            receipt = self.store.snapshot(source_key, conn=conn)
            if receipt is None:
                raise _missing("Snapshot", source_key)
            if receipt["state"] == "rolled_back":
                return self._snapshot_receipt(receipt, conn)
            ids = [row[0] for row in conn.execute(
                "SELECT object_id FROM task_snapshot_objects WHERE source_key=? AND object_type='task'", (source_key,))]
            gids = [row[0] for row in conn.execute(
                "SELECT object_id FROM task_snapshot_objects WHERE source_key=? AND object_type='group'", (source_key,))]
            marker = receipt["event_seq"]
            if ids or gids:
                task_marks = ",".join("?" * len(ids)) or "''"
                group_marks = ",".join("?" * len(gids)) or "''"
                later = conn.execute(
                    f"SELECT 1 FROM team_events WHERE seq>? AND "
                    f"(task_id IN ({task_marks}) OR group_id IN ({group_marks})) LIMIT 1",
                    (marker, *ids, *gids),
                ).fetchone()
                if later:
                    raise _conflict("Snapshot objects have later activity; rollback is unsafe", source_key=source_key)
            if conn.execute("SELECT 1 FROM event_cursors WHERE seq>=? LIMIT 1", (marker,)).fetchone():
                raise _conflict("Snapshot event has been consumed; rollback is unsafe", source_key=source_key)
            if ids:
                marks = ",".join("?" * len(ids))
                for row in conn.execute(f"SELECT id,context_refs_json FROM tasks WHERE id NOT IN ({marks})", ids):
                    try:
                        refs = json.loads(row["context_refs_json"])
                        dangling = [ref for ref in refs if parse_reference(ref).scheme == "result"
                                    and parse_reference(ref).task_id in ids]
                    except (ValueError, TypeError):
                        raise _conflict("An external task has unreadable references; rollback is unsafe",
                                        source_key=source_key, task_id=row["id"]) from None
                    if dangling:
                        raise _conflict("External tasks reference snapshot results; rollback is unsafe",
                                        source_key=source_key, task_id=row["id"], references=dangling)
                for row in conn.execute(
                        f"SELECT task_id,delivery_ref,test_ref,integration_ref FROM legacy_release_facts "
                        f"WHERE task_id NOT IN ({marks})", ids):
                    try:
                        dangling = []
                        for field in ("delivery_ref", "test_ref", "integration_ref"):
                            ref = row[field]
                            if not ref:
                                continue
                            parsed = parse_reference(ref)
                            if parsed.scheme == "result" and parsed.task_id in ids:
                                dangling.append(ref)
                    except (ValueError, TypeError):
                        raise _conflict("An external release fact has an unreadable reference; rollback is unsafe",
                                        source_key=source_key, task_id=row["task_id"]) from None
                    if dangling:
                        raise _conflict("External release facts reference snapshot results; rollback is unsafe",
                                        source_key=source_key, task_id=row["task_id"], references=dangling)
            batch, _ = self._normalize_snapshot(receipt["snapshot"], source_key=source_key,
                                                source_ref=receipt["source_ref"],
                                                snapshot_sha256=receipt["snapshot_sha256"], actor=receipt["actor"],
                                                contract_version=int(receipt.get("contract_version", 1)))
            for expected in batch["tasks"]:
                current = self.store.get_task(expected["id"], conn=conn)
                untouched = current is not None and (
                    current["revision"] == 1 and current["attempt"] == 0 and current["state"] == expected["state"]
                    and current["assignee"] == expected["assignee"] and current["title"] == expected["title"]
                    and current["objective"] == expected["objective"] and current["preferred"] == expected["preferred"]
                    and current["requirements"] == expected["requirements"] and current["file_scope"] == expected["file_scope"]
                    and current["gate"] == expected["gate"] and current["base_commit"] == expected["base_commit"]
                    and json.loads(current["context_refs_json"]) == expected["context_refs"]
                    and json.loads(current["constraints_json"]) == expected["constraints"]
                    and current["lock_policy"] == expected["lock_policy"]
                    and current["workspace_domain"] == expected["workspace_domain"]
                    and bool(current["snapshot_epoch_pending"]) == bool(expected["epoch_pending"])
                )
                if not untouched:
                    raise _conflict("Snapshot task differs from its import baseline; rollback is unsafe",
                                    source_key=source_key, task_id=expected["id"])
            for expected in batch["groups"]:
                current = self.store.get_group(expected["id"], conn=conn)
                if (current is None or current["title"] != expected["title"]
                        or current["authorized"] != expected["authorized"]
                        or current["default_requires"] != expected["default_requires"]
                        or current.get("default_gate", []) != expected["default_gate"]
                        or current.get("legacy_release_mode") != expected["legacy_release_mode"]
                        or current.get("lifecycle_state", "open") != expected["lifecycle_state"]
                        or current.get("revision", 1) != 1):
                    raise _conflict("Snapshot group differs from its import baseline; rollback is unsafe",
                                    source_key=source_key, group_id=expected["id"])
            expected_edges = sorted((d["task_id"], d["depends_on_id"], d["requires"])
                                    for d in batch["dependencies"])
            actual_edges = sorted((d["task_id"], d["depends_on_id"], d["requires"])
                                  for tid in ids for d in self.store.dependencies(task_id=tid, conn=conn))
            if actual_edges != expected_edges:
                raise _conflict("Snapshot dependencies differ from their import baseline; rollback is unsafe",
                                source_key=source_key)
            expected_legacy_edges = sorted((d["task_id"], d["depends_on_id"], d["source_ref"])
                                           for d in batch["legacy_dependencies"])
            actual_legacy_edges = sorted((d["task_id"], d["depends_on_id"], d["source_ref"])
                                         for tid in ids for d in self.store.legacy_release_dependencies(task_id=tid, conn=conn))
            if actual_legacy_edges != expected_legacy_edges:
                raise _conflict("Snapshot legacy release dependencies differ from their import baseline; rollback is unsafe",
                                source_key=source_key)
            for expected in batch["tasks"]:
                expected_fact = expected["release_facts"]
                actual_fact = self.store.release_fact(expected["id"], conn=conn)
                fact_fields = ("delivery_ref", "delivery_sha256", "has_patch", "patchset_sha256", "test_verdict",
                               "test_patchset_sha256", "test_ref", "integration_commit", "integration_ref")
                if ((expected_fact is None and actual_fact is not None)
                        or (expected_fact is not None and (actual_fact is None or any(
                            actual_fact.get(key) != expected_fact[key] for key in fact_fields
                        ) or actual_fact.get("updated_by") != receipt["actor"]))):
                    raise _conflict("Snapshot release facts differ from their import baseline; rollback is unsafe",
                                    source_key=source_key, task_id=expected["id"])
            if ids:
                marks = ",".join("?" * len(ids))
                for table, col in (("task_results", "task_id"), ("gate_results", "task_id")):
                    if conn.execute(f"SELECT 1 FROM {table} WHERE {col} IN ({marks}) LIMIT 1", ids).fetchone():
                        raise _conflict("Snapshot tasks have downstream evidence; rollback is unsafe", source_key=source_key)
                if conn.execute(f"SELECT 1 FROM task_deps WHERE depends_on_id IN ({marks}) AND task_id NOT IN ({marks}) LIMIT 1",
                                (*ids, *ids)).fetchone():
                    raise _conflict("Snapshot tasks have external dependents; rollback is unsafe", source_key=source_key)
                if conn.execute(f"SELECT 1 FROM legacy_release_dependencies WHERE depends_on_id IN ({marks}) AND task_id NOT IN ({marks}) LIMIT 1",
                                (*ids, *ids)).fetchone():
                    raise _conflict("Snapshot tasks have external legacy release dependents; rollback is unsafe",
                                    source_key=source_key)
                conn.execute(f"DELETE FROM task_deps WHERE task_id IN ({marks}) OR depends_on_id IN ({marks})", (*ids, *ids))
                conn.execute(f"DELETE FROM legacy_release_dependencies WHERE task_id IN ({marks}) OR depends_on_id IN ({marks})",
                             (*ids, *ids))
                conn.execute(f"DELETE FROM legacy_release_facts WHERE task_id IN ({marks})", ids)
                conn.execute(f"DELETE FROM tasks WHERE id IN ({marks})", ids)
            if gids:
                marks = ",".join("?" * len(gids))
                if conn.execute(f"SELECT 1 FROM tasks WHERE group_id IN ({marks}) LIMIT 1", gids).fetchone():
                    raise _conflict("Snapshot groups have tasks outside the batch; rollback is unsafe", source_key=source_key)
                conn.execute(f"DELETE FROM task_groups WHERE id IN ({marks})", gids)
            rollback_seq = self.store.append_event(conn, "snapshot.rolled_back", actor=actor, ref=receipt["source_ref"],
                                                   payload={"source_key": source_key, "snapshot_sha256": receipt["snapshot_sha256"]})
            conn.execute("UPDATE task_snapshots SET state='rolled_back',rollback_actor=?,rolled_back_at=?,rollback_event_seq=? WHERE source_key=?",
                         (actor, __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), rollback_seq, source_key))
            return self._snapshot_receipt(self.store.snapshot(source_key, conn=conn), conn)

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
            prior = self._group_or_missing(group_id, conn)
            group = self.store.update_group(conn, group_id, **changes)
            if group["revision"] != prior["revision"]:
                self.store.append_event(conn, "group.updated", actor=actor, group_id=group_id, payload=changes)
            return self._group_view(group, conn)  # type: ignore[arg-type]

    @service_errors
    def set_group_state(self, group_id: str, state: str, *, actor: str, cause: str,
                        expected_revision: int) -> dict[str, Any]:
        if state not in ("open", "closed"):
            raise _invalid("state", "state must be open or closed")
        actor = _text("actor", actor, maximum=100)
        cause = _text("cause", cause, maximum=500)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise _invalid("expected_revision", "expected_revision must be a positive integer")
        with self.store.transaction() as conn:
            return self._apply_group_state(conn, group_id, state, actor=actor, cause=cause,
                                           expected_revision=expected_revision)

    def _apply_group_state(self, conn: sqlite3.Connection, group_id: str, state: str, *, actor: str, cause: str,
                           expected_revision: int, ignore_cancelled: bool = False) -> dict[str, Any]:
        """The body of ``set_group_state`` inside the caller's transaction (``projects.close_stage`` shares it).

        ``ignore_cancelled`` lets a group close with cancelled tasks, which the project labels do not count
        as work; at least one task that is not cancelled must remain.
        """
        group = self._group_or_missing(group_id, conn)
        if group["revision"] != expected_revision:
            raise _conflict("Stale group revision", group_id=group_id, expected_revision=expected_revision,
                            revision=group["revision"])
        if group["lifecycle_state"] == state:
            return self._group_view(group, conn)
        if state == "closed" and group.get("kind") == "activities":
            raise _conflict("The activities group of a project never closes; close each activity instead",
                            group_id=group_id, kind="activities")
        if state == "closed":
            tasks = self.store.list_tasks(group_id=group_id, conn=conn)
            if ignore_cancelled:
                tasks = [t for t in tasks if t["state"] != "cancelled"]
            if not tasks or any(t["state"] not in ("submitted", "accepted") for t in tasks):
                raise _conflict("A group can close only when non-empty and every task is submitted or accepted",
                                group_id=group_id)
            for task in tasks:
                facts = self.store.release_fact(task["id"], conn=conn)
                if not facts or not facts.get("delivery_sha256"):
                    raise _conflict("Every task needs valid delivery evidence before group closure",
                                    group_id=group_id, task_id=task["id"])
                if facts.get("has_patch") and not (facts.get("integration_commit") and facts.get("integration_ref")):
                    raise _conflict("Patched tasks need verified integration before group closure",
                                    group_id=group_id, task_id=task["id"])
        updated = self.store.set_group_lifecycle(conn, group_id, state=state, expected_revision=expected_revision)
        if updated is None:
            raise _missing("Group", group_id)
        if updated["revision"] != group["revision"]:
            self.store.append_event(conn, "group.lifecycle_changed", actor=actor, group_id=group_id, cause=cause,
                                    payload={"from": group["lifecycle_state"], "to": state,
                                             "revision": updated["revision"]})
        return self._group_view(updated, conn)

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
                    requirements: Iterable[str] = (), preferred: str = "",
                    release_dependencies: Iterable[Mapping[str, str]] = (), reopen_group: bool = False,
                    expected_group_revision: int | None = None, cause: str = "",
                    context_refs: Iterable[str] = (), continuity: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Create an ``open`` task; events ``task.created`` (with its initial status) and ``dependency.added``.

        ``depends_on`` holds task ids or ``{"task_id": ..., "requires": ...}``;
        without ``requires`` the group's default applies. ``file_scope`` holds
        relative paths or folders (``/`` separators, no ``..``).
        Errors: ``not_found`` (group or dependency), ``invalid_request`` (ids,
        formats, a cycle), ``invalid_state`` (the task already exists).
        ``release_dependencies`` is an optional atomic set of legacy release
        edges, each ``{"depends_on_id", "source_ref"}``. Native and legacy
        edges are checked together for duplicate pairs and cycles.
        """
        task_id = _identifier("task_id", task_id, _TASK_ID)
        title = _text("title", title, maximum=200)
        actor = _text("actor", actor, maximum=100)
        objective = _text("objective", objective, maximum=4000, required=False)
        preferred = _text("preferred", preferred, maximum=100, required=False)
        initial_continuity = _continuity_changes(continuity)
        if expected_group_revision is not None and (isinstance(expected_group_revision, bool) or not isinstance(expected_group_revision, int)):
            raise _invalid("expected_group_revision", "expected_group_revision must be an integer")
        if reopen_group:
            cause = _text("cause", cause, maximum=500)
            if expected_group_revision is None:
                raise _invalid("expected_group_revision", "reopening a group requires its revision")
        scope = _paths(file_scope)
        labels = _labels(requirements)
        if isinstance(context_refs, (str, bytes)):
            raise _invalid("context_refs", "context_refs must be a list")
        refs: list[str] = []
        try:
            for raw in context_refs:
                ref = validate_reference(raw, field="context_refs")
                if ref not in refs:
                    refs.append(ref)
        except EnvelopeError as error:
            raise _invalid(error.field, str(error)) from None
        if len(refs) > MAX_CONTEXT_REFS:
            raise _invalid("context_refs", f"context_refs has more than {MAX_CONTEXT_REFS} references")
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
        if isinstance(release_dependencies, (str, bytes, Mapping)):
            raise _invalid("release_dependencies", "release_dependencies must be a list")
        wanted_release: list[tuple[str, str]] = []
        for item in release_dependencies:
            if not isinstance(item, Mapping) or set(item) != {"depends_on_id", "source_ref"}:
                raise _invalid("release_dependencies", "each release dependency requires depends_on_id and source_ref")
            dep_id = _identifier("depends_on_id", item["depends_on_id"], _TASK_ID)
            try:
                source_ref = validate_reference(item["source_ref"], field="source_ref")
            except EnvelopeError as error:
                raise _invalid(error.field, str(error)) from None
            if dep_id in {existing for existing, _ in wanted_release}:
                raise _invalid("release_dependencies", f"Repeated release dependency: {dep_id}")
            if dep_id in {existing for existing, _ in wanted}:
                raise _invalid("release_dependencies", f"Dependency pair has multiple kinds: {task_id} -> {dep_id}")
            wanted_release.append((dep_id, source_ref))
        try:
            with self.store.transaction() as conn:
                group = self._group_or_missing(group_id, conn)
                if group.get("kind") == "activities":
                    raise _invalid("group_id", f"{group_id} holds the activities of a project; start them with "
                                   "activities.start", group_id=group_id, kind="activities")
                self._assert_project_active(conn, group)
                if self.store.get_task(task_id, conn=conn) is not None:
                    raise _conflict(f"Task already exists: {task_id}", task_id=task_id)
                if group["lifecycle_state"] == "closed":
                    if not reopen_group:
                        raise _conflict("Cannot create a task in a closed group", group_id=group_id,
                                        revision=group["revision"])
                    if group["revision"] != expected_group_revision:
                        raise _conflict("Stale group revision", group_id=group_id,
                                        expected_revision=expected_group_revision, revision=group["revision"])
                elif reopen_group:
                    raise _conflict("reopen_group applies only to a closed group", group_id=group_id)
                for dep_id, _ in [*wanted, *wanted_release]:
                    if dep_id == task_id:
                        raise _invalid("depends_on", "A task cannot depend on itself", cycle=[task_id, task_id])
                    target = self.store.get_task(dep_id, conn=conn)
                    if target is None:
                        raise _missing("Task", dep_id)
                    self._reject_activity_edge(target)
                current_tasks, current_native = self._snapshot(conn)
                current_legacy = self.store.legacy_release_dependencies(conn=conn)
                planned_native = current_native + [{"task_id": task_id, "depends_on_id": dep_id,
                                                    "requires": req or group["default_requires"]}
                                                   for dep_id, req in wanted]
                planned_legacy = current_legacy + [{"task_id": task_id, "depends_on_id": dep_id,
                                                    "source_ref": ref}
                                                   for dep_id, ref in wanted_release]
                try:
                    graph.validate_union([*current_tasks, task_id], planned_native, planned_legacy)
                except graph.GraphError as error:
                    raise _invalid("depends_on", str(error), cycle=getattr(error, "cycle", None)) from None
                if group["lifecycle_state"] == "closed":
                    group = self.store.set_group_lifecycle(conn, group_id, state="open",
                                                          expected_revision=expected_group_revision)
                    self.store.append_event(conn, "group.lifecycle_changed", actor=actor, group_id=group_id,
                                            cause=cause, payload={"from": "closed", "to": "open",
                                                                  "revision": group["revision"]})
                self.store.insert_task(conn, task_id, group_id, title, actor=actor, objective=objective,
                                       preferred=preferred, requirements=labels, file_scope=scope, context_refs=refs,
                                       execution=merge_continuity(None, initial_continuity) if initial_continuity else None)
                added = []
                for dep_id, requires in wanted:
                    requires = requires or group["default_requires"]
                    self.store.insert_dependency(conn, task_id, dep_id, requires)
                    added.append((dep_id, requires))
                for dep_id, source_ref in wanted_release:
                    self.store.insert_legacy_release_dependency(conn, task_id, dep_id, source_ref)
                view = self._task_view(task_id, conn)
                self.store.append_event(conn, "task.created", actor=actor, group_id=group_id, task_id=task_id,
                                        payload={"status": view["status"]})
                for dep_id, requires in added:
                    self.store.append_event(conn, "dependency.added", actor=actor, group_id=group_id,
                                            task_id=task_id, payload={"depends_on_id": dep_id, "requires": requires})
                for dep_id, source_ref in wanted_release:
                    self.store.append_event(conn, "legacy_dependency.added", actor=actor, group_id=group_id,
                                            task_id=task_id, ref=source_ref,
                                            payload={"depends_on_id": dep_id, "kind": "legacy_release"})
                return view
        except sqlite3.IntegrityError:
            raise _conflict(f"Task already exists: {task_id}", task_id=task_id) from None

    @service_errors
    def update_scope(self, task_id: str, *, file_scope: Iterable[str], actor: str,
                     expected_revision: int, lock_policy: Mapping[str, Any] | None = None,
                     workspace_domain: str | None = None) -> dict[str, Any]:
        task_id = _identifier("task_id", task_id, _TASK_ID)
        actor = _text("actor", actor, maximum=100)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise _invalid("expected_revision", "expected_revision must be an integer")
        scope = _paths(file_scope)
        domain = _text("workspace_domain", workspace_domain, maximum=200, required=False) if workspace_domain is not None else None
        if lock_policy is not None:
            if not isinstance(lock_policy, Mapping) or set(lock_policy) != {"shared_scope", "workspace_scope"}:
                raise _invalid("lock_policy", "lock_policy requires shared_scope and workspace_scope")
            shared = _paths(lock_policy["shared_scope"]); workspace = _paths(lock_policy["workspace_scope"])
        else:
            shared, workspace = [], []
        if any(path not in scope for path in shared + workspace) or set(shared) & set(workspace):
            raise _invalid("lock_policy", "lock scopes must be disjoint and contained in file_scope")
        with self.store.transaction() as conn:
            task = self.store.get_task(task_id, conn=conn)
            if task is None:
                raise _missing("Task", task_id)
            if task["revision"] != expected_revision:
                raise _conflict("Stale task revision", task_id=task_id, expected_revision=expected_revision,
                                revision=task["revision"])
            if task.get("kind") == "activity" and scope:
                raise _invalid("file_scope", "An activity is read-only: its file_scope stays empty", task_id=task_id)
            state = task["state"]
            if state in ("claimed", "paused", "needs_changes") and actor != task["assignee"]:
                raise _conflict("Only the assignee may change the scope of assigned work",
                                task_id=task_id, assignee=task["assignee"], actor=actor)
            if lock_policy is None:
                if state in ("claimed", "needs_changes"):
                    shared = list(scope)
                elif state == "paused":
                    if scope == task["file_scope"]:
                        current = task.get("lock_policy") or {}
                        shared = list(current.get("shared_scope", task["file_scope"]))
                        workspace = list(current.get("workspace_scope", []))
                    else:
                        # A scope change without a partition stays globally locked.
                        shared, workspace = list(scope), []
            if state not in ("open", "needs_changes", "claimed", "paused"):
                raise _conflict("Scope can change only while open, needs_changes, claimed or paused",
                                task_id=task_id, state=state)
            if state == "open" and (shared or workspace):
                raise _invalid("lock_policy", "open tasks cannot retain locks")
            if state in ("claimed", "needs_changes") and (shared != scope or workspace):
                raise _invalid("lock_policy", f"{state} tasks require a global lock over file_scope")
            if state == "paused" and set(shared + workspace) != set(scope):
                raise _invalid("lock_policy", "paused lock partitions must cover file_scope")
            new_domain = domain if domain is not None else task.get("workspace_domain", "")
            policy = {"shared_scope": shared, "workspace_scope": workspace}
            conflicts = self._lock_conflicts(scope, task, self.store.list_tasks(conn=conn), actor=actor,
                                             actor_domain=new_domain or None, requested_policy=policy,
                                             requested_state=state)
            if conflicts:
                raise _conflict("Updated file_scope overlaps another active task", task_id=task_id,
                                conflicts=conflicts)
            current_policy = task.get("lock_policy") or {"shared_scope": [], "workspace_scope": []}
            if (scope == task["file_scope"] and policy == current_policy
                    and new_domain == task.get("workspace_domain", "")):
                return self._task_view(task_id, conn)
            try:
                self.store.update_task(conn, task_id, expected_revision=task["revision"], file_scope=scope,
                                       lock_policy=policy, workspace_domain=new_domain)
            except StaleRevision as stale:
                raise _conflict(str(stale), task_id=task_id, revision=stale.current) from None
            self.store.append_event(conn, "task.scope_updated", actor=actor, group_id=task["group_id"],
                                    task_id=task_id, cause="scope updated", payload={
                                        "paths_count": len(scope),
                                        "file_scope_sha256": hashlib.sha256(_canonical_json(scope).encode()).hexdigest(),
                                        "shared_count": len(shared), "workspace_count": len(workspace),
                                        "workspace_domain": bool(new_domain)})
            return self._task_view(task_id, conn)

    def _wait_current(self, conn: sqlite3.Connection, task_id: str, expected_revision: int) -> dict[str, Any]:
        task_id = _identifier("task_id", task_id, _TASK_ID)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise _invalid("expected_revision", "expected_revision must be a positive integer")
        task = self.store.get_task(task_id, conn=conn)
        if task is None:
            raise _missing("Task", task_id)
        if task["revision"] != expected_revision:
            raise _conflict("The task revision changed", task_id=task_id, revision=task["revision"])
        return task

    @service_errors
    def set_wait(self, task_id: str, *, who: str, question: str, actor: str, expected_revision: int,
                 target: str = "", reference: str = "", reset_at: str | None = None) -> dict[str, Any]:
        """Attach a decision wait without changing state, assignee, hold, gate or attempt.

        ``who``: user, agent, task, quota or window. Quota needs an aware reset_at;
        agent/task/window need a target. Each replacement gets a new id so a late
        reply cannot resolve a different question. Actors are labels, not credentials.
        """
        who = _text("who", who, maximum=16)
        if who not in ("user", "agent", "task", "quota", "window"):
            raise _invalid("who", "who must be user, agent, task, quota or window")
        actor = _text("actor", actor, maximum=100)
        question = _text("question", question, maximum=500)
        target = _text("target", target, maximum=128, required=who in ("agent", "task", "window"))
        reference = _text("reference", reference, maximum=256, required=False)
        now = self.wait_clock().astimezone(timezone.utc)
        reset = None
        if who == "quota":
            try:
                if not isinstance(reset_at, str):
                    raise ValueError
                reset = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
                if reset.tzinfo is None or reset <= now:
                    raise ValueError
                reset = reset.astimezone(timezone.utc).isoformat()
            except ValueError:
                raise _invalid("reset_at", "quota needs a future ISO timestamp with timezone") from None
        elif reset_at is not None:
            raise _invalid("reset_at", "reset_at is only valid for a quota wait")
        waiting = _wait_payload({"id": str(uuid.uuid4()), "who": who, "target": target, "question": question,
                                 "since": now.isoformat(), "reference": reference, "reset_at": reset,
                                 "created_by": actor, "label": "waiting",
                                 "kind": "approval_pending" if who == "user" else "waiting"})
        with self.store.transaction() as conn:
            task = self._wait_current(conn, task_id, expected_revision)
            if task["state"] in TERMINAL_STATES:
                raise _conflict("A terminal task cannot wait for a decision", task_id=task_id)
            if task["assignee"] and actor != task["assignee"] and not _human_wait_actor(actor):
                raise _conflict("Only the assignee or a human may set a task wait", task_id=task_id)
            if who == "task":
                _identifier("target", target, _TASK_ID)
                if target == task_id:
                    raise _invalid("target", "a task cannot wait for itself")
                if self.store.get_task(target, conn=conn) is None:
                    raise _missing("Task", target)
            self.store.update_task(conn, task_id, expected_revision=expected_revision, waiting=waiting)
            self.store.append_event(conn, "task.wait_set", actor=actor, group_id=task["group_id"], task_id=task_id,
                                    payload={"waiting": waiting})
            return self._task_view(task_id, conn)

    def _end_wait(self, task_id: str, *, wait_id: str, actor: str, expected_revision: int,
                  decision: str | None) -> dict[str, Any]:
        actor = _text("actor", actor, maximum=100)
        wait_id = _text("wait_id", wait_id, maximum=100)
        if decision is not None:
            decision = _text("decision", decision, maximum=1000)
        with self.store.transaction() as conn:
            task = self._wait_current(conn, task_id, expected_revision)
            waiting = task.get("waiting") or {}
            if not waiting or waiting.get("id") != wait_id:
                raise _conflict("The task wait changed", task_id=task_id)
            human = _human_wait_actor(actor)
            if decision is None:
                if actor not in (waiting["created_by"], task["assignee"]) and not human:
                    raise _conflict("Only its creator, assignee or a human may withdraw a wait", task_id=task_id)
            else:
                who = waiting["who"]
                if who == "user" and not human:
                    raise _conflict("A user decision must be recorded by a human actor", task_id=task_id)
                if who == "agent" and actor != waiting["target"] and not human:
                    raise _conflict("The requested agent must record its decision", task_id=task_id)
                if who == "window" and not human:
                    raise _conflict("A human must confirm the integration window", task_id=task_id)
                if who == "task":
                    dependency = self.store.get_task(waiting["target"], conn=conn)
                    if dependency is None or dependency["state"] != "accepted":
                        raise _conflict("The awaited task is not accepted", task_id=task_id)
                if who == "quota" and self.wait_clock() < datetime.fromisoformat(waiting["reset_at"]):
                    raise _conflict("The quota reset time has not arrived", task_id=task_id)
                if who in ("task", "quota") and actor not in (waiting["created_by"], task["assignee"]) and not human:
                    raise _conflict("Only the creator, assignee or a human may resolve this wait", task_id=task_id)
            payload = _wait_payload({"wait_id": wait_id, "who": waiting["who"], "reference": waiting["reference"],
                                     "decision": decision, "outcome": "resolved" if decision is not None else "withdrawn"})
            self.store.update_task(conn, task_id, expected_revision=expected_revision, waiting={})
            self.store.append_event(conn, "task.wait_resolved" if decision is not None else "task.wait_cleared",
                                    actor=actor, group_id=task["group_id"], task_id=task_id, payload=payload)
            return self._task_view(task_id, conn)

    @service_errors
    def clear_wait(self, task_id: str, *, wait_id: str, actor: str, expected_revision: int) -> dict[str, Any]:
        """Withdraw a question; this records no approval and invokes no execution."""
        return self._end_wait(task_id, wait_id=wait_id, actor=actor, expected_revision=expected_revision, decision=None)

    @service_errors
    def resolve_wait(self, task_id: str, *, wait_id: str, actor: str, expected_revision: int,
                     decision: str) -> dict[str, Any]:
        """Record an explicit reply and clear its exact wait atomically. Never accept/resume the task."""
        return self._end_wait(task_id, wait_id=wait_id, actor=actor, expected_revision=expected_revision, decision=decision)

    @service_errors
    def get_task(self, task_id: str) -> dict[str, Any]:
        """The task with ``status``, ``blockers``, ``depends_on`` (with ``satisfied``) and ``dependents``.

        Errors: ``not_found``.
        """
        with self.store.db.connect() as conn:
            tasks, deps = self._snapshot(conn)
            if task_id not in tasks:
                raise _missing("Task", task_id)
            return self._view(tasks[task_id], self._states(tasks), deps, self._release_context(conn), tasks)

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
            release_context = self._release_context(conn)
            views = [self._view(task, states, deps, release_context, tasks) for task in tasks.values()]
        return [view for view in views
                if (group_id is None or view["group_id"] == group_id)
                and (status is None or view["status"] == status)
                and (assignee is None or view["assignee"] == assignee)]

    @service_errors
    def ready_tasks(self, *, group_id: str | None = None) -> list[dict[str, Any]]:
        """``ready`` tasks of authorized groups, in creation order.

        Activities are left out: they are started by a person or an orchestrator on purpose and never taken from
        this queue (amendment 1 of PRY-T01).
        """
        with self.store.db.connect() as conn:
            authorized = {group["id"] for group in self.store.list_groups(conn=conn) if group["authorized"]}
        return [task for task in self.list_tasks(group_id=group_id, status="ready")
                if task["group_id"] in authorized and task["kind"] != "activity"]

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
            legacy = self.store.legacy_release_dependencies(conn=conn)
            for identifier in (task_id, depends_on_id):
                if identifier not in tasks:
                    raise _missing("Task", identifier)
            task = tasks[task_id]
            self._reject_activity_edge(task, tasks[depends_on_id])
            if task["state"] in TERMINAL_STATES:
                raise _conflict(f"Task {task_id} is {task['state']}", task_id=task_id, state=task["state"])
            self._assert_project_active(conn, self._group_or_missing(task["group_id"], conn))
            if any(d["task_id"] == task_id and d["depends_on_id"] == depends_on_id for d in deps):
                raise _conflict(f"{task_id} already depends on {depends_on_id}", task_id=task_id,
                                depends_on_id=depends_on_id)
            if any(d["task_id"] == task_id and d["depends_on_id"] == depends_on_id for d in legacy):
                raise _conflict("Dependency pair already exists as a legacy release edge", task_id=task_id,
                                depends_on_id=depends_on_id)
            requires = requires or self._group_or_missing(task["group_id"], conn)["default_requires"]
            try:
                graph.validate_union(tasks, deps + [{"task_id": task_id, "depends_on_id": depends_on_id,
                                                      "requires": requires}], legacy)
            except graph.GraphError as error:
                raise _invalid("depends_on_id", str(error), cycle=getattr(error, "cycle", None)) from None
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
            context = self._release_context(conn)
            if task_id not in tasks:
                raise _missing("Task", task_id)
            task = tasks[task_id]
            self._assert_project_active(conn, self._group_or_missing(task["group_id"], conn))
            states = self._states(tasks)
            before = self._status_map(tasks, deps, context)
            if not self.store.delete_dependency(conn, task_id, depends_on_id):
                raise ServiceError(NOT_FOUND, f"{task_id} does not depend on {depends_on_id}", status=404,
                                   details={"task_id": task_id, "depends_on_id": depends_on_id})
            self._bump(conn, task)
            self.store.append_event(conn, "dependency.removed", actor=actor, group_id=task["group_id"],
                                    task_id=task_id, payload={"depends_on_id": depends_on_id})
            updated_tasks, updated_native = self._snapshot(conn)
            after = self._status_map(updated_tasks, updated_native, self._release_context(conn))
            self._emit_newly_ready(conn, before, after, updated_tasks, actor, f"dependency {depends_on_id} removed")
            return self._task_view(task_id, conn)

    def _release_facts_view(self, task_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
        task = self.store.get_task(task_id, conn=conn)
        if task is None:
            raise _missing("Task", task_id)
        group = self._group_or_missing(task["group_id"], conn)
        return {**release_policy.facts_view(task_id, group.get("legacy_release_mode"),
                                            self.store.release_fact(task_id, conn=conn)),
                "group_id": task["group_id"]}

    @service_errors
    def get_release_facts(self, task_id: str) -> dict[str, Any]:
        with self.store.db.connect() as conn:
            return self._release_facts_view(task_id, conn)

    @service_errors
    def set_release_mode(self, group_id: str, mode: str, *, actor: str) -> dict[str, Any]:
        group_id = _identifier("group_id", group_id, _GROUP_ID)
        mode = _text("mode", mode, maximum=20)
        if mode not in release_policy.RELEASE_MODES:
            raise _invalid("mode", f"mode must be one of {', '.join(release_policy.RELEASE_MODES)}")
        actor = _text("actor", actor, maximum=100)
        with self.store.transaction() as conn:
            group = self._group_or_missing(group_id, conn)
            if group.get("legacy_release_mode") == mode:
                return {"group_id": group_id, "legacy_release_mode": mode}
            tasks, native = self._snapshot(conn)
            before = self._status_map(tasks, native, self._release_context(conn))
            previous = group.get("legacy_release_mode")
            if not self.store.set_legacy_release_mode(conn, group_id, mode):
                raise _missing("Group", group_id)
            self.store.append_event(conn, "group.release_mode_changed", actor=actor, group_id=group_id,
                                    payload={"from": previous, "to": mode})
            release_context = self._release_context(conn)
            after = self._status_map(tasks, native, release_context)
            self._emit_newly_ready(conn, before, after, tasks, actor, "legacy release mode changed")
            return {"group_id": group_id, "legacy_release_mode": mode}

    @service_errors
    def add_release_dependency(self, task_id: str, depends_on_id: str, *, actor: str,
                               source_ref: str) -> dict[str, Any]:
        task_id = _identifier("task_id", task_id, _TASK_ID)
        depends_on_id = _identifier("depends_on_id", depends_on_id, _TASK_ID)
        actor = _text("actor", actor, maximum=100)
        try:
            source_ref = validate_reference(source_ref, field="source_ref")
        except EnvelopeError as error:
            raise _invalid(error.field, str(error)) from None
        with self.store.transaction() as conn:
            tasks, native = self._snapshot(conn)
            if task_id not in tasks:
                raise _missing("Task", task_id)
            if depends_on_id not in tasks:
                raise _missing("Task", depends_on_id)
            task = tasks[task_id]
            self._reject_activity_edge(task, tasks[depends_on_id])
            if task["state"] in TERMINAL_STATES:
                raise _conflict(f"Task {task_id} is {task['state']}", task_id=task_id, state=task["state"])
            existing = next((edge for edge in self.store.legacy_release_dependencies(task_id=task_id, conn=conn)
                             if edge["depends_on_id"] == depends_on_id), None)
            if existing:
                if existing["source_ref"] != source_ref:
                    raise _conflict("Release dependency already exists with a different source_ref",
                                    task_id=task_id, depends_on_id=depends_on_id)
                return {"task_id": task_id, "depends_on_id": depends_on_id, "kind": "legacy_release",
                        "source_ref": source_ref, "action": "added"}
            legacy = self.store.legacy_release_dependencies(conn=conn)
            try:
                graph.validate_union(tasks, native, legacy + [{"task_id": task_id,
                                    "depends_on_id": depends_on_id, "source_ref": source_ref}])
            except graph.GraphError as error:
                raise _invalid("depends_on_id", str(error), cycle=getattr(error, "cycle", None)) from None
            before = self._status_map(tasks, native, self._release_context(conn))
            self.store.insert_legacy_release_dependency(conn, task_id, depends_on_id, source_ref)
            self._bump(conn, task)
            self.store.append_event(conn, "legacy_dependency.added", actor=actor, group_id=task["group_id"],
                                    task_id=task_id, ref=source_ref,
                                    payload={"depends_on_id": depends_on_id, "kind": "legacy_release"})
            return {"task_id": task_id, "depends_on_id": depends_on_id, "kind": "legacy_release",
                    "source_ref": source_ref, "action": "added"}

    @service_errors
    def remove_release_dependency(self, task_id: str, depends_on_id: str, *, actor: str) -> dict[str, Any]:
        task_id = _identifier("task_id", task_id, _TASK_ID)
        depends_on_id = _identifier("depends_on_id", depends_on_id, _TASK_ID)
        actor = _text("actor", actor, maximum=100)
        with self.store.transaction() as conn:
            tasks, native = self._snapshot(conn)
            task = tasks.get(task_id)
            if task is None:
                raise _missing("Task", task_id)
            existing = next((edge for edge in self.store.legacy_release_dependencies(task_id=task_id, conn=conn)
                             if edge["depends_on_id"] == depends_on_id), None)
            if existing is None:
                raise ServiceError(NOT_FOUND, f"{task_id} has no legacy release dependency on {depends_on_id}",
                                   status=404, details={"task_id": task_id, "depends_on_id": depends_on_id})
            before = self._status_map(tasks, native, self._release_context(conn))
            self.store.delete_legacy_release_dependency(conn, task_id, depends_on_id)
            self._bump(conn, task)
            self.store.append_event(conn, "legacy_dependency.removed", actor=actor, group_id=task["group_id"],
                                    task_id=task_id, ref=existing["source_ref"],
                                    payload={"depends_on_id": depends_on_id, "kind": "legacy_release"})
            updated_tasks, updated_native = self._snapshot(conn)
            after = self._status_map(updated_tasks, updated_native, self._release_context(conn))
            self._emit_newly_ready(conn, before, after, updated_tasks, actor,
                                   f"legacy dependency {depends_on_id} removed")
            return {"task_id": task_id, "depends_on_id": depends_on_id, "kind": "legacy_release",
                    "source_ref": existing["source_ref"], "action": "removed"}

    @staticmethod
    def _release_sha(field: str, value: Any, *, required: bool = True) -> str:
        if not isinstance(value, str) or (required and not re.fullmatch(r"[0-9a-f]{64}", value)):
            raise _invalid(field, f"{field} must be a lowercase SHA-256 hex digest")
        if not required and value and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise _invalid(field, f"{field} must be empty or a lowercase SHA-256 hex digest")
        return value

    def _release_ref(self, field: str, value: Any) -> str:
        try:
            return validate_reference(value, field=field)
        except EnvelopeError as error:
            raise _invalid(error.field, str(error)) from None

    def _write_release_fact(self, conn: sqlite3.Connection, task_id: str, *, actor: str,
                            fact: Mapping[str, Any], event: str, ref: str, cause: str,
                            event_cause: str = "") -> dict[str, Any]:
        task = self.store.get_task(task_id, conn=conn)
        if task is None:
            raise _missing("Task", task_id)
        tasks, native = self._snapshot(conn)
        before = self._status_map(tasks, native, self._release_context(conn))
        self.store.write_release_fact(conn, task_id, actor=actor, **fact)
        self._bump(conn, task)
        self.store.append_event(conn, event, actor=actor, group_id=task["group_id"], task_id=task_id, ref=ref,
                                cause=event_cause,
                                payload={key: value for key, value in fact.items()
                                         if key in {"delivery_sha256", "has_patch", "patchset_sha256",
                                                    "test_verdict", "test_patchset_sha256", "integration_commit"}})
        updated_tasks, updated_native = self._snapshot(conn)
        after = self._status_map(updated_tasks, updated_native, self._release_context(conn))
        self._emit_newly_ready(conn, before, after, updated_tasks, actor, cause)
        return self._release_facts_view(task_id, conn)

    @service_errors
    def record_delivery_fact(self, task_id: str, *, delivery_ref: str, delivery_sha256: str,
                             has_patch: bool, patchset_sha256: str, actor: str) -> dict[str, Any]:
        task_id = _identifier("task_id", task_id, _TASK_ID)
        actor = _text("actor", actor, maximum=100)
        delivery_ref = self._release_ref("delivery_ref", delivery_ref)
        delivery_sha256 = self._release_sha("delivery_sha256", delivery_sha256)
        if not isinstance(has_patch, bool):
            raise _invalid("has_patch", "has_patch must be boolean")
        patchset_sha256 = self._release_sha("patchset_sha256", patchset_sha256, required=has_patch)
        if not has_patch and patchset_sha256:
            raise _invalid("patchset_sha256", "patchset_sha256 must be empty when has_patch is false")
        fact = {"delivery_ref": delivery_ref, "delivery_sha256": delivery_sha256, "has_patch": has_patch,
                "patchset_sha256": patchset_sha256, "test_verdict": None, "test_patchset_sha256": "",
                "test_ref": "", "integration_commit": "", "integration_ref": ""}
        with self.store.transaction() as conn:
            task = self.store.get_task(task_id, conn=conn)
            if task is None:
                raise _missing("Task", task_id)
            current = self.store.release_fact(task_id, conn=conn)
            delivery_fields = ("delivery_ref", "delivery_sha256", "has_patch", "patchset_sha256")
            if current and all(current.get(key) == fact[key] for key in delivery_fields):
                return self._release_facts_view(task_id, conn)
            return self._write_release_fact(conn, task_id, actor=actor, fact=fact,
                                            event="release.delivery_recorded", ref=delivery_ref,
                                            cause="legacy delivery evidence recorded")

    @service_errors
    def record_test_verdict(self, task_id: str, *, test_verdict: str, test_patchset_sha256: str,
                            test_ref: str, actor: str) -> dict[str, Any]:
        task_id = _identifier("task_id", task_id, _TASK_ID)
        actor = _text("actor", actor, maximum=100)
        if test_verdict not in ("passed", "failed"):
            raise _invalid("test_verdict", "test_verdict must be passed or failed")
        test_patchset_sha256 = self._release_sha("test_patchset_sha256", test_patchset_sha256)
        test_ref = self._release_ref("test_ref", test_ref)
        with self.store.transaction() as conn:
            task = self.store.get_task(task_id, conn=conn)
            if task is None:
                raise _missing("Task", task_id)
            current = self.store.release_fact(task_id, conn=conn)
            if current is None:
                raise _conflict("A current delivery is required before recording a test verdict", task_id=task_id)
            if not current["has_patch"]:
                raise _conflict("A test verdict requires a delivery with a patch", task_id=task_id)
            if test_patchset_sha256 != current["patchset_sha256"]:
                raise _conflict("Test evidence must match the current delivery patchset", task_id=task_id,
                                expected_patchset_sha256=current["patchset_sha256"],
                                actual_patchset_sha256=test_patchset_sha256)
            fact = {key: current[key] for key in ("delivery_ref", "delivery_sha256", "has_patch", "patchset_sha256",
                                                  "integration_commit", "integration_ref")}
            fact.update(test_verdict=test_verdict, test_patchset_sha256=test_patchset_sha256, test_ref=test_ref)
            if all(current.get(key) == value for key, value in fact.items()):
                return self._release_facts_view(task_id, conn)
            return self._write_release_fact(conn, task_id, actor=actor, fact=fact,
                                            event="release.test_recorded", ref=test_ref,
                                            cause="legacy test evidence recorded")

    @service_errors
    def record_integration_fact(self, task_id: str, *, integration_commit: str,
                                integration_ref: str, actor: str) -> dict[str, Any]:
        task_id = _identifier("task_id", task_id, _TASK_ID)
        actor = _text("actor", actor, maximum=100)
        integration_commit = _text("integration_commit", integration_commit, maximum=40)
        if not _COMMIT.fullmatch(integration_commit):
            raise _invalid("integration_commit", "integration_commit must be 7 to 40 lowercase hex characters")
        integration_ref = self._release_ref("integration_ref", integration_ref)
        with self.store.transaction() as conn:
            task = self.store.get_task(task_id, conn=conn)
            if task is None:
                raise _missing("Task", task_id)
            current = self.store.release_fact(task_id, conn=conn)
            if current is None:
                raise _conflict("A current delivery is required before recording integration", task_id=task_id)
            fact = {key: current[key] for key in ("delivery_ref", "delivery_sha256", "has_patch", "patchset_sha256",
                                                  "test_verdict", "test_patchset_sha256", "test_ref")}
            fact.update(integration_commit=integration_commit, integration_ref=integration_ref)
            if current["integration_commit"] == integration_commit and current["integration_ref"] == integration_ref:
                return self._release_facts_view(task_id, conn)
            return self._write_release_fact(conn, task_id, actor=actor, fact=fact,
                                            event="release.integration_recorded", ref=integration_ref,
                                            cause="legacy integration evidence recorded")

    @service_errors
    def reset_release_facts(self, task_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        task_id = _identifier("task_id", task_id, _TASK_ID)
        actor = _text("actor", actor, maximum=100)
        reason = _text("reason", reason, maximum=500)
        with self.store.transaction() as conn:
            task = self.store.get_task(task_id, conn=conn)
            if task is None:
                raise _missing("Task", task_id)
            if self.store.release_fact(task_id, conn=conn) is not None:
                self.store.reset_release_fact(conn, task_id)
                self._bump(conn, task)
                self.store.append_event(conn, "release.facts_reset", actor=actor, group_id=task["group_id"],
                                        task_id=task_id, cause=reason, payload={"reason": reason})
            return self._release_facts_view(task_id, conn)

    @service_errors
    def transition(self, task_id: str, action: str, *, actor: str, cause: str = "",
                   expected_revision: int | None = None, actor_domain: str | None = None,
                   continuity: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """The single entry point for state changes (see ``TRANSITIONS``).

        ``submit`` on an activity also accepts it (see above). ``claim`` also requires an authorized group and a ``file_scope`` that
        does not overlap an active task of another assignee (same path, or one
        contains the other). ``start``, ``pause``, ``resume`` and ``submit``
        are for the assignee only. ``start`` increments ``attempt``. After the
        change, every dependent that became ``ready`` gets a ``task.ready``
        event. With ``expected_revision``, a different current revision fails.
        ``continuity`` (CNT-T04) is merged into the ``continuity`` block of the task's ``execution`` in the same
        update, so a task paused by a limit and its hold commit together (a ``None`` value removes that key; see
        ``_edit_continuity``).
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
        if action == "reopen":
            if expected_revision is None:
                raise _invalid("expected_revision", "reopen requires expected_revision")
            cause = _text("cause", cause, maximum=500)
        changes = _continuity_changes(continuity)
        with self.store.transaction() as conn:
            if actor_domain is not None:
                actor_domain = _text("actor_domain", actor_domain, maximum=200)
            result = self._apply(conn, task_id, action, actor=actor, cause=cause,
                                 expected_revision=expected_revision, actor_domain=actor_domain, continuity=changes)
        if action == "accept":
            from .plan_autoadvance import notify_task_accepted   # ARR3-T03: after the commit, never inside it
            notify_task_accepted(self._services, task_id)
        return result

    @service_errors
    def _hand_over(self, conn: sqlite3.Connection, task_id: str, to_assignee: str, *, actor: str, cause: str = "",
                   continuity: Mapping[str, Any] | None = None, expected_revision: int | None = None) -> dict[str, Any]:
        """Give a ``paused`` task to another assignee inside the caller's transaction (CNT-T05): ``unclaim`` by ``actor``,
        then ``claim`` by ``to_assignee``, both through the lifecycle's single function (two events, one commit).

        The caller opens the transaction (``store.transaction()``) so that its own records commit with the change; if either
        step is refused (the files are locked by another task, the task is blocked again, a stale ``expected_revision``) the
        exception leaves the transaction to roll back and nothing changed. ``continuity`` is merged into the task in the
        same update as the ``claim``, as in ``transition``. The new assignee is ``claimed``, not started: it starts the task
        itself. Returns the task view. Errors: ``not_found``, ``invalid_request``, ``invalid_state`` (the task is not
        ``paused``, or the ``claim`` is refused).
        """
        actor = _text("actor", actor, maximum=100)
        to_assignee = _text("to_assignee", to_assignee, maximum=100)
        cause = _text("cause", cause, maximum=500, required=False)
        if expected_revision is not None and (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)):
            raise _invalid("expected_revision", "expected_revision must be an integer")
        changes = _continuity_changes(continuity)
        task = self.store.get_task(task_id, conn=conn)
        if task is None:
            raise _missing("Task", task_id)
        if task["state"] != "paused":
            raise _conflict(f"Only a paused task is handed over; {task_id} is {task['state']}", task_id=task_id,
                            status=task["state"])
        self._apply(conn, task_id, "unclaim", actor=actor, cause=cause, expected_revision=expected_revision)
        return self._apply(conn, task_id, "claim", actor=to_assignee, cause=cause, continuity=changes)

    @service_errors
    def _edit_continuity(self, task_id: str, changes: Mapping[str, Any], *, actor: str,
                          expected_revision: int | None = None) -> dict[str, Any]:
        """Merge ``changes`` into the ``continuity`` block of the task's ``execution`` (CNT-T04); no state changes.

        A key with the value ``None`` is removed. The other blocks of ``execution`` are kept. ``revision`` goes up (the
        caller writes the event that tells why). Returns the task view, which shows the block as ``continuity``.
        Errors: ``not_found``, ``invalid_request`` (not a mapping of JSON data, or too big) and ``invalid_state`` (the
        task is ``accepted``, ``failed`` or ``cancelled``, or ``expected_revision`` is stale).
        """
        actor = _text("actor", actor, maximum=100)
        edited = _continuity_changes(changes)
        if not edited:
            raise _invalid("changes", "Give at least one key of the continuity block")
        if expected_revision is not None and (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)):
            raise _invalid("expected_revision", "expected_revision must be an integer")
        with self.store.transaction() as conn:
            task = self.store.get_task(task_id, conn=conn)
            if task is None:
                raise _missing("Task", task_id)
            if task["state"] in TERMINAL_STATES:
                raise _conflict(f"Cannot change the continuity of a task that is {task['state']}", task_id=task_id,
                                status=task["state"])
            try:
                self.store.update_task(conn, task_id, execution=merge_continuity(task.get("execution"), edited),
                                       expected_revision=task["revision"] if expected_revision is None
                                       else expected_revision)
            except StaleRevision as stale:
                raise _conflict(str(stale), task_id=task_id, revision=stale.current) from None
            return self._task_view(task_id, conn)

    def _apply(self, conn: sqlite3.Connection, task_id: str, action: str, *, actor: str, cause: str = "",
               expected_revision: int | None = None, gate: bool = False, event: str | None = None,
               payload: Mapping[str, Any] | None = None, actor_domain: str | None = None,
               continuity: Mapping[str, Any] | None = None, auto_accept: bool = True) -> dict[str, Any]:
        """Apply one transition inside the caller's transaction (the body of ``transition``).

        ``gate=True`` allows the internal gate actions (``GATE_TRANSITIONS``);
        the gate services use it so that results, events and the state change
        commit together. ``event`` and ``payload`` override the event type and
        extend its payload for gate actions. ``continuity`` (already checked by ``_continuity_changes``) is merged into
        the task's ``execution`` in the same update (CNT-T04). Submitting an activity accepts it right away unless
        ``auto_accept`` is false (``envelopes.submit_result`` stores the result first and then calls ``_accept_activity``).
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
        before_statuses = self._status_map(tasks, deps, self._release_context(conn))
        if gate and action == "gate_start" and task["attempt"] < 1:
            raise _conflict("A task imported as submitted has no current execution epoch; start it before running a gate",
                            task_id=task_id, attempt=task["attempt"], historical_attempts="unknown")
        status = self._effective_status(task_id, before, deps, tasks, self._release_context(conn))
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
        lock_policy_update = None
        epoch_pending_update = None
        workspace_domain_update = None
        epoch_started = action == "resume" and bool(task.get("snapshot_epoch_pending"))
        if action == "reopen":
            assignee = ""
            lock_policy_update = {"shared_scope": [], "workspace_scope": []}
            workspace_domain_update = ""
            epoch_pending_update = False
        if action == "accept":
            # With a gate (ORQ-2), passing it is the only way to accept.
            checks = task["gate"] or self._group_or_missing(task["group_id"], conn).get("default_gate", [])
            if checks:
                raise _conflict(f"{task_id} has a gate; run it to accept the task", task_id=task_id, gate=checks)
        if action == "claim":
            group = self._group_or_missing(task["group_id"], conn)
            self._assert_project_active(conn, group)
            if not group["authorized"]:
                raise _conflict(f"Group {group['id']} is not authorized", task_id=task_id, group_id=group["id"])
            conflicts = self._lock_conflicts(task["file_scope"], task, tasks.values(), actor=actor,
                                             actor_domain=actor_domain,
                                             requested_policy={"shared_scope": task["file_scope"], "workspace_scope": []},
                                             requested_state=target)
            if conflicts:
                raise _conflict(f"Files of {task_id} are locked by other tasks", task_id=task_id, conflicts=conflicts)
            assignee = actor
            lock_policy_update = {"shared_scope": task["file_scope"], "workspace_scope": []}
            workspace_domain_update = actor_domain or ""
        elif action == "unclaim":
            assignee = ""
        elif action == "start":
            if task["state"] == "claimed" or task["state"] == "needs_changes" or task.get("snapshot_epoch_pending"):
                self._assert_global_lock(task, tasks.values(), actor=actor)
            if task["state"] == "needs_changes" or task.get("snapshot_epoch_pending"):
                lock_policy_update = {"shared_scope": task["file_scope"], "workspace_scope": []}
                workspace_domain_update = actor_domain if actor_domain is not None else task.get("workspace_domain", "")
                epoch_pending_update = False
            attempt += 1
        elif action == "resume":
            self._assert_global_lock(task, tasks.values(), actor=actor)
            if task.get("snapshot_epoch_pending"):
                attempt += 1
                lock_policy_update = {"shared_scope": task["file_scope"], "workspace_scope": []}
                epoch_pending_update = False
        if action in ("start", "resume"):
            fresh_tasks, fresh_native = self._snapshot(conn)
            fresh_context = self._release_context(conn)
            blockers = self._blockers(task_id, self._states(fresh_tasks), fresh_native, fresh_tasks, fresh_context)
            if blockers:
                raise _conflict(f"Cannot {action}; task has unresolved blockers", task_id=task_id, blockers=blockers)
        cleared_wait = task.get("waiting") if (target in TERMINAL_STATES or
                         (action == "request_changes" and _human_wait_actor(actor))) else None
        try:
            self.store.update_task(conn, task_id, expected_revision=task["revision"], state=target,
                                   assignee=assignee, attempt=attempt, lock_policy=lock_policy_update,
                                   workspace_domain=workspace_domain_update,
                                   snapshot_epoch_pending=epoch_pending_update,
                                   execution=(merge_continuity(task.get("execution"), continuity)
                                              if continuity else None), waiting={} if cleared_wait else None)
        except StaleRevision as stale:
            raise _conflict(str(stale), task_id=task_id, revision=stale.current) from None
        if cleared_wait:
            self.store.append_event(conn, "task.wait_cleared", actor=actor, group_id=task["group_id"], task_id=task_id,
                                    payload={"wait_id": cleared_wait["id"], "outcome": "task_closed" if
                                             target in TERMINAL_STATES else "task_decision", "action": action})
        reset_release = action in ("claim", "unclaim", "request_changes", "gate_fail", "reopen") or (
            action == "start" and task["state"] == "needs_changes")
        release_facts_reset = self.store.reset_release_fact(conn, task_id) if reset_release else False
        updated_tasks, updated_native = self._snapshot(conn)
        after_context = self._release_context(conn)
        after_statuses = self._status_map(updated_tasks, updated_native, after_context)
        after_state = self._states(updated_tasks)
        self.store.append_event(conn, event or _EVENT_OF_ACTION[action], actor=actor, group_id=task["group_id"],
                                task_id=task_id, cause=cause,
                                payload={"from": status, "to": after_statuses[task_id],
                                         **(payload or {}), **({"epoch_start": True} if epoch_started else {}),
                                         **({"release_facts_reset": True} if release_facts_reset else {})})
        self._emit_newly_ready(conn, before_statuses, after_statuses, updated_tasks, actor,
                               f"dependency {task_id} {target}")
        if action == "submit" and auto_accept and task.get("kind") == "activity":
            return self._accept_activity(conn, task_id, actor)
        return self._task_view(task_id, conn)

    def _accept_activity(self, conn: sqlite3.Connection, task_id: str, actor: str) -> dict[str, Any]:
        """Accept a submitted activity in the caller's transaction: nobody reviews it (amendment 1 of PRY-T01)."""
        return self._apply(conn, task_id, "accept", actor=actor, cause="activity_closed")

    @staticmethod
    def _policies_conflict(one: Mapping[str, Any], one_domain: str | None,
                           two: Mapping[str, Any], two_domain: str | None,
                           *, paused_owner: int = 0) -> list[str]:
        overlaps = lambda left, right: [q for q in right if any(_overlaps(p, q) for p in left)]
        found = overlaps(one.get("shared_scope", []), two.get("shared_scope", []))
        same_or_unknown = not one_domain or not two_domain or one_domain == two_domain
        if paused_owner == 2:
            found += overlaps(one.get("workspace_scope", []), two.get("shared_scope", []))
            if same_or_unknown:
                found += overlaps(one.get("shared_scope", []), two.get("workspace_scope", []))
                found += overlaps(one.get("workspace_scope", []), two.get("workspace_scope", []))
        elif paused_owner == 1:
            # The first policy is paused and the second is active. Its shared
            # partition is global; its workspace partition only conflicts in
            # the same or an unknown domain with an active request.
            if same_or_unknown:
                found += overlaps(one.get("workspace_scope", []), two.get("shared_scope", []))
                found += overlaps(one.get("workspace_scope", []), two.get("workspace_scope", []))
        else:
            found += overlaps(one.get("shared_scope", []), two.get("workspace_scope", []))
            found += overlaps(one.get("workspace_scope", []), two.get("shared_scope", []))
            if same_or_unknown:
                found += overlaps(one.get("workspace_scope", []), two.get("workspace_scope", []))
        return list(dict.fromkeys(found))

    @staticmethod
    def _lock_conflicts(requested: list[str], task: Mapping[str, Any], others: Iterable[Mapping[str, Any]], *,
                        actor: str, actor_domain: str | None = None,
                        requested_policy: Mapping[str, Any] | None = None,
                        requested_state: str | None = None) -> list[dict[str, Any]]:
        conflicts = []
        requested_policy = requested_policy or {"shared_scope": requested, "workspace_scope": []}
        requested_state = requested_state or str(task["state"])
        for other in others:
            if other["id"] == task["id"] or other["state"] not in _LOCKING_STATES or other["assignee"] == actor:
                continue
            policy = other.get("lock_policy") or {}
            if "shared_scope" not in policy and "workspace_scope" not in policy:
                policy = {"shared_scope": other["file_scope"], "workspace_scope": []}
            if other["state"] == "paused":
                paths = TaskServices._policies_conflict(requested_policy, actor_domain, policy,
                                                        other.get("workspace_domain") or None, paused_owner=2)
            else:
                # Workspace-scoped locks on an active claimed/running task must
                # behave globally; domain metadata cannot bypass active locks.
                active_policy = {"shared_scope": list(policy.get("shared_scope", []))
                                 + list(policy.get("workspace_scope", [])), "workspace_scope": []}
                paths = TaskServices._policies_conflict(
                    requested_policy, actor_domain, active_policy, other.get("workspace_domain") or None,
                    paused_owner=1 if requested_state == "paused" else 0)
            if paths:
                conflicts.append({"task_id": other["id"], "assignee": other["assignee"],
                                  "status": other["state"], "paths": list(dict.fromkeys(paths))})
        return conflicts

    def _assert_global_lock(self, task: Mapping[str, Any], others: Iterable[Mapping[str, Any]], *, actor: str) -> None:
        conflicts = self._lock_conflicts(task["file_scope"], task, others, actor=actor,
                                         actor_domain=task.get("workspace_domain") or None,
                                         requested_policy={"shared_scope": task["file_scope"], "workspace_scope": []},
                                         requested_state="running")
        if conflicts:
            raise _conflict(f"Files of {task['id']} are locked by other tasks", task_id=task["id"], conflicts=conflicts)

    # -------- team event log --------
    @service_errors
    def events(self, *, after: int = 0, group_id: str | None = None, task_id: str | None = None,
               limit: int = 500, project_id: str | None = None) -> list[dict[str, Any]]:
        """Team events with ``seq`` greater than ``after``, oldest first, at most ``limit`` (1..1000).

        ``seq`` is one team-wide sequence: unfiltered reads see consecutive
        numbers; filtered reads skip the events of other groups or tasks.
        ``project_id`` keeps the events about that project (``ref=project:<id>``) and those of its stages.
        Errors: ``invalid_request`` (``after`` or ``limit``).
        """
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise _invalid("after", "after must be an integer >= 0", after=repr(after))
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise _invalid("limit", "limit must be an integer between 1 and 1000", limit=repr(limit))
        return self.store.events(after=after, group_id=group_id, task_id=task_id, limit=limit, project_id=project_id)
