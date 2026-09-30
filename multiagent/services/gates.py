# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Gate services: checks that must pass before a submitted task is accepted.

Contract fixed by ORQ-T10 (signatures only; every method raises
``NotImplementedError`` until ORQ-T13 implements it on the gate store of
ORQ-T11 and the project commands of ORQ-T12).

A gate is an ordered list of checks declared on a task or, by default, on its
group. A task's own non-empty gate wins over its group's default; with neither,
the task has no gate and is accepted manually as before.

Check kinds:

* ``command:<name>`` runs a project command and passes with exit code 0.
  ``tests`` and ``commit`` are aliases of ``command:test`` and
  ``command:commit``. Project commands come only from the local, trusted file
  ``<local_dir>/project_commands.toml`` (name -> argv without a shell,
  timeout, working directory); a task or a request only names them.
* ``review`` passes when an actor other than the assignee approved the current
  attempt after its last rejection.

``run_gate`` is triggered manually on a ``submitted`` task: ``submitted ->
validating``, then every check in order. All passing leads to ``accepted`` and
releases the dependents; the first failing check (a non-zero exit code or a
timeout) leads to ``needs_changes``; an error (an unknown or unstartable
command) or a missing review returns the task to ``submitted``. Each check is
stored per task, attempt and gate run, with the last 4000 characters of its
output. With a gate, the manual ``accept`` action is refused.

Every method returns JSON-ready data and raises only ``ServiceError``:
``not_found``, ``invalid_request`` (an unknown check or a malformed commands
file) or ``invalid_state`` (the task's status does not allow the operation, or
the reviewer is the assignee).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from . import ApplicationServices

CHECK_ALIASES = {"tests": "command:test", "commit": "command:commit"}
MAX_CHECKS = 10
OUTPUT_EXCERPT_CHARS = 4000
DEFAULT_TIMEOUT_SECONDS = 600
MAX_TIMEOUT_SECONDS = 7200
RESULT_STATUSES = ("passed", "failed", "error", "pending")
GATE_OUTCOMES = ("passed", "failed", "error", "pending")
EVENT_TYPES = (
    "gate.updated", "gate.started", "gate.check_passed", "gate.check_failed", "gate.check_error",
    "gate.passed", "gate.failed", "gate.error", "gate.pending", "gate.aborted",
    "review.approved", "review.rejected",
)


class GateServices:
    def __init__(self, services: ApplicationServices):
        self._services = services

    def set_task_gate(self, task_id: str, checks: Iterable[str], *, actor: str) -> dict[str, Any]:
        """Declare the task's own gate (``[]`` removes it); event ``gate.updated``.

        Errors: ``not_found``, ``invalid_request`` (an unknown check or more
        than 10), ``invalid_state`` (the task is terminal or ``validating``).
        """
        raise NotImplementedError("ORQ-T13")

    def set_group_gate(self, group_id: str, checks: Iterable[str], *, actor: str) -> dict[str, Any]:
        """Declare the group's default gate; event ``gate.updated``. Errors: ``not_found``, ``invalid_request``."""
        raise NotImplementedError("ORQ-T13")

    def get_gate(self, task_id: str) -> dict[str, Any]:
        """The effective gate: ``{checks, source: "task"|"group"|"none", last_run}``. Errors: ``not_found``."""
        raise NotImplementedError("ORQ-T13")

    def review(self, task_id: str, *, actor: str, approved: bool, comment: str = "") -> dict[str, Any]:
        """Record ``review.approved`` or ``review.rejected`` for the current attempt; the state does not change.

        Errors: ``not_found``, ``invalid_state`` (the task is not ``submitted``
        or ``validating``, or the actor is the assignee).
        """
        raise NotImplementedError("ORQ-T13")

    def run_gate(self, task_id: str, *, actor: str, cause: str = "") -> dict[str, Any]:
        """Run the effective gate of a ``submitted`` task and return ``{outcome, run, results, task}``.

        Commands run outside the database transaction, each with its timeout.
        Errors: ``not_found``, ``invalid_state`` (not ``submitted``, or no gate).
        """
        raise NotImplementedError("ORQ-T13")

    def abort_gate(self, task_id: str, *, actor: str, cause: str) -> dict[str, Any]:
        """Return a ``validating`` task to ``submitted`` (for example after a crash); event ``gate.aborted``.

        Errors: ``not_found``, ``invalid_request`` (empty cause), ``invalid_state`` (not ``validating``).
        """
        raise NotImplementedError("ORQ-T13")

    def results(self, task_id: str, *, attempt: int | None = None) -> list[dict[str, Any]]:
        """Stored check results of the task, by attempt, gate run and position. Errors: ``not_found``."""
        raise NotImplementedError("ORQ-T13")

    def project_commands(self) -> list[dict[str, Any]]:
        """Configured command names with their timeout and working directory (never the full argv).

        Errors: ``invalid_request`` (the local commands file is malformed; ``details`` says why).
        """
        raise NotImplementedError("ORQ-T13")
