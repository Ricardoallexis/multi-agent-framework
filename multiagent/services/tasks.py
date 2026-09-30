# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Task services: groups, tasks, dependencies and the team event log.

Contract fixed by ORQ-T02 (signatures only; every method raises
``NotImplementedError`` until ORQ-T05 implements it on the store of ORQ-T03
and the pure graph of ORQ-T04).

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

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import ApplicationServices

# Stored states; "blocked" and "ready" are computed for "open" tasks only.
STATES = ("open", "claimed", "running", "paused", "submitted", "needs_changes",
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
# Only the assignee may run these actions on its task.
ASSIGNEE_ONLY = ("start", "pause", "resume", "submit")

EVENT_TYPES = (
    "group.created", "group.updated", "task.created", "dependency.added", "dependency.removed",
    "task.claimed", "task.unclaimed", "task.started", "task.paused", "task.resumed",
    "task.submitted", "task.accepted", "task.changes_requested", "task.failed",
    "task.cancelled", "task.ready",
)
MAX_EVENT_PAYLOAD_BYTES = 2048


class TaskServices:
    def __init__(self, services: ApplicationServices):
        self._services = services

    # Groups

    def create_group(self, group_id: str, title: str, *, actor: str, authorized: bool = False,
                     default_requires: str = "accepted") -> dict[str, Any]:
        """Create a group (a stage of work); event ``group.created``.

        Errors: ``invalid_request`` (id, title or ``default_requires``),
        ``invalid_state`` (the group already exists).
        """
        raise NotImplementedError("ORQ-T05")

    def update_group(self, group_id: str, *, actor: str, title: str | None = None,
                     authorized: bool | None = None, default_requires: str | None = None) -> dict[str, Any]:
        """Change the title, the authorization or the default ``requires``; event ``group.updated``.

        Changing ``default_requires`` affects dependencies added afterwards only.
        Errors: ``not_found``, ``invalid_request``.
        """
        raise NotImplementedError("ORQ-T05")

    def get_group(self, group_id: str) -> dict[str, Any]:
        """The group with ``counts`` of its tasks per effective status. Errors: ``not_found``."""
        raise NotImplementedError("ORQ-T05")

    def list_groups(self) -> list[dict[str, Any]]:
        """Every group, oldest first."""
        raise NotImplementedError("ORQ-T05")

    # Tasks and dependencies

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
        raise NotImplementedError("ORQ-T05")

    def get_task(self, task_id: str) -> dict[str, Any]:
        """The task with ``status``, ``blockers``, ``depends_on`` (with ``satisfied``) and ``dependents``.

        Errors: ``not_found``.
        """
        raise NotImplementedError("ORQ-T05")

    def list_tasks(self, *, group_id: str | None = None, status: str | None = None,
                   assignee: str | None = None) -> list[dict[str, Any]]:
        """Tasks filtered by group, effective status and assignee, in creation order.

        Errors: ``invalid_request`` (unknown ``status``).
        """
        raise NotImplementedError("ORQ-T05")

    def ready_tasks(self, *, group_id: str | None = None) -> list[dict[str, Any]]:
        """``ready`` tasks of authorized groups, in creation order."""
        raise NotImplementedError("ORQ-T05")

    def add_dependency(self, task_id: str, depends_on_id: str, *, actor: str,
                       requires: str | None = None) -> dict[str, Any]:
        """Make ``task_id`` depend on ``depends_on_id``; event ``dependency.added``.

        Errors: ``not_found``, ``invalid_request`` (a cycle or an unknown
        ``requires``), ``invalid_state`` (``task_id`` is terminal).
        """
        raise NotImplementedError("ORQ-T05")

    def remove_dependency(self, task_id: str, depends_on_id: str, *, actor: str) -> dict[str, Any]:
        """Remove a dependency; event ``dependency.removed`` and ``task.ready`` if it unblocks the task.

        Errors: ``not_found``.
        """
        raise NotImplementedError("ORQ-T05")

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
        raise NotImplementedError("ORQ-T05")

    # Team event log

    def events(self, *, after: int = 0, group_id: str | None = None, task_id: str | None = None,
               limit: int = 500) -> list[dict[str, Any]]:
        """Team events with ``seq`` greater than ``after``, oldest first, at most ``limit`` (1..1000).

        ``seq`` is one team-wide sequence: unfiltered reads see consecutive
        numbers; filtered reads skip the events of other groups or tasks.
        Errors: ``invalid_request`` (``after`` or ``limit``).
        """
        raise NotImplementedError("ORQ-T05")
