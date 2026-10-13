# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Activity services (PRY-T09): lightweight tasks linked to a project, outside its stages.

Contract: amendment 1 of PRY-T01 (P-518, approved). An *activity* is a task with ``kind = "activity"`` that lives in
the project's group ``<key>-ACT`` (kind ``activities``, no position, always authorized). It is the unit of work of a
chat or of a small job that no stage plans: started on purpose by a person or an orchestrator (never created from a
session or a run), read-only (an empty ``file_scope``), without dependencies on either end, without a gate and without
review: ``submit`` accepts it in the same transaction. It reuses the states, the events, the links to chats
(``projects.link_session``) and the results of any other task; it adds no table.

``start(project_id, title, actor=, executor=, idempotency_key=)``
    Creates the activity (and the project's activities group the first time) in one transaction. Without ``executor``
    the actor takes it at once: it is ``claimed`` and ``running`` with the actor as assignee (events ``task.created``,
    ``task.claimed`` and ``task.started``). With ``executor`` the activity is only prepared (``ready``, ``preferred`` =
    ``executor:<id>``) for ``executors.run_task`` to claim and start it. Ids are ``<key>-ACT-T<nn>``. A repeated
    ``idempotency_key`` of the same project gives back the same activity (``created`` false) and writes nothing.
``close(activity_id, summary, actor=)``
    Stores ``{"summary": summary}`` as the result of the running attempt and accepts the activity (events
    ``task.submitted``, ``result.submitted`` and ``task.accepted`` with cause ``activity_closed``). Only the assignee
    closes a running activity, as for any ``submit``. An executor closes its own with the result envelope it submits.
``list(project_id, status=, since=)``
    The activities of a project in creation order, optionally of one label (``doing``, ``done``...) and changed since a
    moment.

Events carry ids, the state and ``kind``, never titles or summaries (they are typed by the user and may be private).
USO and the tracking views count activities apart: every item says ``kind``, and ``projects.get`` carries an
``activities`` block with their own counts and progress; they are never part of a stage, of its progress or of «what
you have to do».

Every method returns JSON-ready data and raises only ``ServiceError``: ``not_found`` (project, activity),
``invalid_request`` (malformed data, a task that is not an activity) or ``invalid_state`` (an archived project, an
activities group id taken by something else, a status that does not allow the action, or another assignee).
"""
from __future__ import annotations

import datetime
import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from ..tasks import labels
from ..tasks.envelopes import MAX_SUMMARY_CHARS
from .errors import ServiceError, service_errors
from .projects import _actor, activity_group_id, actor_kind, actor_view
from .tasks import _TASK_ID, _conflict, _identifier, _invalid, _missing, _text

if TYPE_CHECKING:
    from . import ApplicationServices

MAX_TITLE_CHARS = 200
MAX_OBJECTIVE_CHARS = 4000
MAX_KEY_CHARS = 200
_TERMINAL = ("accepted", "failed", "cancelled")
# The labels an activity can have: it is never reviewed, never blocked and never waits for a verification.
ACTIVITY_LABELS = ("todo", "ready", "doing", "done", "failed", "cancelled")


def _executor_ref(value: Any) -> str:
    """``executor:<id>`` for an executor id, checked with the same form the actors use."""
    ref = f"executor:{_text('executor', value, maximum=64)}"
    if actor_kind(ref) != "executor":
        raise _invalid("executor", "executor must be an executor id: letters, digits, '_', '.' or '-'")
    return ref


def _moment(value: Any) -> datetime.datetime:
    """A day (``AAAA-MM-DD``) or an ISO date and time as an aware UTC moment (naive text is read as UTC)."""
    text = _text("since", value, maximum=40)
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise _invalid("since", "since must be a day AAAA-MM-DD or an ISO date and time") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc)


class ActivityServices:
    def __init__(self, services: ApplicationServices):
        self._services = services

    @property
    def _tasks(self):
        return self._services.tasks

    @property
    def _projects(self):
        return self._services.projects

    # ------------------------------------------------------------------ views
    def _item(self, conn: sqlite3.Connection, task: Mapping[str, Any], group: Mapping[str, Any],
              ctx: Mapping[str, Any]) -> dict[str, Any]:
        """One activity as the interfaces show it. ``summary`` is the result it was closed with, else ``None``."""
        status = ctx["statuses"][task["id"]]
        key = self._tasks._label_key(task, status, group, ctx["release"])
        who = task["assignee"] or task["preferred"]
        summary = None
        if task["state"] == "accepted":
            latest = self._services.envelopes.store.latest_result(task["id"], conn=conn)
            summary = latest["result"]["summary"] if latest else None
        return {
            "id": task["id"], "project_id": group["project_id"], "group_id": group["id"], "kind": task["kind"],
            "title": task["title"], "objective": task["objective"], "state": task["state"], "status": status,
            "label": labels.label("task", key, task["state"]), "assignee": task["assignee"],
            "preferred": task["preferred"],
            "executor": task["preferred"].split(":", 1)[1] if task["preferred"].startswith("executor:") else None,
            "assignee_info": actor_view(who, ctx["level_of"]), "attempt": task["attempt"],
            "revision": task["revision"], "created_by": task["created_by"], "created_at": task["created_at"],
            "updated_at": task["updated_at"],
            "closed_at": task["updated_at"] if task["state"] in _TERMINAL else None,
            "summary": summary,
            "sessions": [{"session_id": row["session_id"], "linked_by": row["linked_by"],
                          "created_at": row["created_at"]}
                         for row in self._projects.store.session_links(task["id"], conn=conn)],
        }

    def _view_of(self, conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        task = self._tasks.store.get_task(task_id, conn=conn)
        group = self._tasks.store.get_group(task["group_id"], conn=conn)
        ctx = self._projects._context(conn, [group["id"]])
        return self._item(conn, task, group, ctx)

    # ------------------------------------------------------------------ the activities group
    def _group_in(self, conn: sqlite3.Connection, project: Mapping[str, Any], actor: str) -> dict[str, Any]:
        """The project's activities group, created authorized and outside the stages the first time."""
        store = self._tasks.store
        group_id = activity_group_id(project["id"])
        group = store.get_group(group_id, conn=conn)
        if group is None:
            group = store.insert_group(conn, group_id, "Activities", actor=actor, authorized=True,
                                       project_id=project["id"], position=None, kind="activities")
            store.append_event(conn, "group.created", actor=actor, group_id=group_id, cause=f"project:{project['id']}",
                               payload={"authorized": True, "default_requires": "accepted", "kind": "activities"})
        elif group.get("kind") != "activities" or group.get("project_id") != project["id"]:
            raise _conflict(f"Group {group_id} exists and is not the activities group of {project['id']}",
                            group_id=group_id, project_id=project["id"])
        return group

    @staticmethod
    def _next_id(group_id: str, existing: Iterable[Mapping[str, Any]]) -> str:
        pattern = re.compile(rf"^{re.escape(group_id)}-T(\d+)$")
        used = [int(match[1]) for task in existing if (match := pattern.fullmatch(task["id"]))]
        return f"{group_id}-T{max(used, default=0) + 1:02d}"

    # ------------------------------------------------------------------ operations
    @service_errors
    def start(self, project_id: str, title: str, *, actor: str, executor: str | None = None,
              idempotency_key: str | None = None, objective: str = "") -> dict[str, Any]:
        """Create an activity of the project and, without ``executor``, start it as ``actor``; returns it plus ``created``.

        Any valid actor may start one (``user:...``, ``person:...``, ``agent:...`` or ``executor:...``). Without
        ``executor`` the activity is taken at once (assignee ``actor``, ``running``). With ``executor`` it is left
        ``ready`` for that executor (``preferred = executor:<id>``); nothing is launched here. ``idempotency_key``
        (for example the id of the chat it starts from) makes a repeated call give back the same activity with
        ``created`` false and change nothing. The title and the objective are typed by the caller; they are never
        read from a session. Events: ``group.created`` (the first time), ``task.created`` (with ``kind``),
        ``task.claimed`` and ``task.started``.
        Errors: ``not_found`` (project), ``invalid_request`` (title, objective, executor, key or actor),
        ``invalid_state`` (archived project, or ``<key>-ACT`` taken by another group).
        """
        title = _text("title", title, maximum=MAX_TITLE_CHARS)
        objective = _text("objective", objective, maximum=MAX_OBJECTIVE_CHARS, required=False)
        actor = _actor(actor)
        preferred = _executor_ref(executor) if executor is not None else ""
        key = _text("idempotency_key", idempotency_key, maximum=MAX_KEY_CHARS) if idempotency_key is not None else None
        store = self._tasks.store
        with store.transaction() as conn:
            project = self._projects._project_or_missing(conn, project_id)
            self._projects._active(project)
            group = self._group_in(conn, project, actor)
            existing = store.list_tasks(group_id=group["id"], kind="activity", conn=conn)
            if key is not None:
                for task in existing:
                    if (task.get("execution") or {}).get("activity", {}).get("idempotency_key") == key:
                        return {**self._view_of(conn, task["id"]), "created": False}
            task_id = self._next_id(group["id"], existing)
            store.insert_task(conn, task_id, group["id"], title, actor=actor, objective=objective, preferred=preferred,
                              kind="activity", execution={"activity": {"idempotency_key": key}} if key else None)
            cause = f"project:{project['id']}"
            status = self._tasks._task_view(task_id, conn)["status"]
            store.append_event(conn, "task.created", actor=actor, group_id=group["id"], task_id=task_id, cause=cause,
                               payload={"status": status, "kind": "activity"})
            if not preferred:
                self._tasks._apply(conn, task_id, "claim", actor=actor, cause=cause)
                self._tasks._apply(conn, task_id, "start", actor=actor, cause=cause)
            return {**self._view_of(conn, task_id), "created": True}

    @service_errors
    def close(self, activity_id: str, summary: str, *, actor: str) -> dict[str, Any]:
        """Close a running activity with a short result; returns the activity (``accepted``, with its ``summary``).

        ``summary`` (at most 1500 characters) becomes the ``result.v1`` of the running attempt, and submitting it
        accepts the activity in the same transaction (events ``task.submitted``, ``result.submitted`` and
        ``task.accepted``, cause ``activity_closed``). ``actor`` must be the assignee. Errors: ``not_found``,
        ``invalid_request`` (summary, actor, or the task is not an activity), ``invalid_state`` (not running, or
        another assignee).
        """
        activity_id = _identifier("activity_id", activity_id, _TASK_ID)
        summary = _text("summary", summary, maximum=MAX_SUMMARY_CHARS)
        actor = _actor(actor)
        with self._tasks.store.db.connect() as conn:
            task = self._tasks.store.get_task(activity_id, conn=conn)
        if task is None:
            raise _missing("Activity", activity_id)
        if task["kind"] != "activity":
            raise _invalid("activity_id", f"{activity_id} is not an activity", task_id=activity_id)
        self._services.envelopes.submit_result(activity_id, {"summary": summary}, actor=actor)
        with self._tasks.store.db.connect() as conn:
            return self._view_of(conn, activity_id)

    @service_errors
    def list(self, project_id: str, *, status: str | None = None, since: str | None = None) -> list[dict[str, Any]]:
        """The activities of a project in creation order (empty when it has none).

        ``status`` keeps one label (``todo``, ``ready``, ``doing``, ``done``, ``failed`` or ``cancelled``). ``since``
        (a day ``AAAA-MM-DD`` or an ISO date and time, read as UTC when it has no zone) keeps those that changed on or
        after it. Errors: ``not_found`` (project), ``invalid_request`` (``status`` or ``since``).
        """
        if status is not None and status not in ACTIVITY_LABELS:
            raise _invalid("status", f"status must be one of {', '.join(ACTIVITY_LABELS)}")
        floor = _moment(since) if since is not None else None
        with self._tasks.store.db.connect() as conn:
            project = self._projects._project_or_missing(conn, project_id)
            groups = self._tasks.store.list_groups(project_id=project["id"], kind="activities", conn=conn)
            if not groups:
                return []
            ctx = self._projects._context(conn, [group["id"] for group in groups])
            items = [self._item(conn, task, group, ctx) for group in groups for task in ctx["tasks"].values()
                     if task["group_id"] == group["id"]]
        return [item for item in items
                if (status is None or item["label"]["key"] == status)
                and (floor is None or datetime.datetime.fromisoformat(item["updated_at"]) >= floor)]
