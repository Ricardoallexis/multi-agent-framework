# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Gate services: checks that must pass before a submitted task is accepted.

Contract fixed by ORQ-T10; implemented by ORQ-T13 on the gate store
(``multiagent.db.gate_store``, ORQ-T11), the project commands
(``multiagent.tasks.commands``, ORQ-T12) and the single task transition
function of ``services.tasks``.

A gate is an ordered list of checks declared on a task or, by default, on its
group. A task's own non-empty gate wins over its group's default; with neither,
the task has no gate and is accepted manually as before.

Check kinds:

* ``command:<name>`` runs a project command and passes with exit code 0.
  ``tests`` and ``commit`` are aliases of ``command:test`` and
  ``command:commit``. Project commands come only from the local, trusted file
  ``settings.project_commands_file`` (``<local_dir>/project_commands.toml`` by
  default; name -> argv without a shell, timeout, working directory); a task or
  a request only names them.
* ``review`` passes when an actor other than the assignee approved the current
  attempt after its last rejection.

``run_gate`` is triggered manually on a ``submitted`` task: ``submitted ->
validating``, then every check in order, outside any database transaction. All
passing leads to ``accepted`` and releases the dependents; the first failing
check (a non-zero exit code or a timeout) leads to ``needs_changes``; an error
(an unknown or unstartable command, a malformed commands file) or a missing
review returns the task to ``submitted``. Each executed check is stored per
task, attempt and gate run, with the last 4000 characters of its output. With a
gate, the manual ``accept`` action is refused.

Every method returns JSON-ready data and raises only ``ServiceError``:
``not_found``, ``invalid_request`` (an unknown check or a malformed commands
file) or ``invalid_state`` (the task's status does not allow the operation, or
the reviewer is the assignee).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Iterator

from ..db.gate_store import GateStore
from ..tasks.commands import COMMAND_NAME, CommandConfigError, CommandResult, load_commands, run_command
from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors
from .tasks import TERMINAL_STATES, _text

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
    "gate.passed", "gate.failed", "gate.error", "gate.pending", "gate.aborted", "gate.discarded",
    "review.approved", "review.rejected",
)
_CHECK_EVENT = {"passed": "gate.check_passed", "failed": "gate.check_failed", "error": "gate.check_error"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _invalid(field: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": field, **details})


def _conflict(message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_STATE, message, status=409, details=details)


def _missing(kind: str, identifier: str) -> ServiceError:
    return ServiceError(NOT_FOUND, f"{kind} not found: {identifier}", status=404, details={kind.lower(): identifier})


def normalize_checks(checks: Iterable[str]) -> list[str]:
    """Expand aliases and validate a gate: 0 to 10 known, distinct checks."""
    if isinstance(checks, str):
        raise _invalid("checks", "checks must be a list")
    result: list[str] = []
    for raw in checks:
        if not isinstance(raw, str):
            raise _invalid("checks", "every check must be text")
        check = CHECK_ALIASES.get(raw.strip(), raw.strip())
        if check != "review" and not (check.startswith("command:") and COMMAND_NAME.fullmatch(check[len("command:"):])):
            raise _invalid("checks", f"Unknown check {raw!r}: use review, tests, commit or command:<name>", check=raw)
        if check in result:
            raise _invalid("checks", f"Repeated check {check!r}", check=check)
        result.append(check)
    if len(result) > MAX_CHECKS:
        raise _invalid("checks", f"A gate has at most {MAX_CHECKS} checks")
    return result


class GateServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._store: GateStore | None = None

    @property
    def store(self) -> GateStore:
        if self._store is None:
            self._store = GateStore(self._services.system.db)
        return self._store

    @property
    def _tasks(self):
        return self._services.tasks

    # -------- helpers --------
    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            with self._tasks.store.transaction() as conn:
                yield conn
        except sqlite3.IntegrityError:
            raise _conflict("Gate operation conflicts with stored state") from None

    def _next_run(self, task_id: str, attempt: int, conn: sqlite3.Connection) -> int:
        # A start reserves its number even when it never stores results. Include
        # legacy result rows, and read every start without the event API's limit.
        run = self.store.next_run(task_id, attempt, conn=conn)
        for row in conn.execute("SELECT payload_json FROM team_events WHERE task_id=? AND type='gate.started'",
                                (task_id,)):
            payload = json.loads(row[0])
            if payload.get("attempt") == attempt:
                run = max(run, payload.get("run", 0) + 1)
        return run

    @staticmethod
    def _latest_start(task_id: str, conn: sqlite3.Connection) -> int | None:
        row = conn.execute("SELECT MAX(seq) FROM team_events WHERE task_id=? AND type='gate.started'",
                           (task_id,)).fetchone()
        return row[0]

    def _is_current(self, task_id: str, attempt: int, started_seq: int,
                    conn: sqlite3.Connection) -> bool:
        task = self._task_or_missing(task_id, conn)
        return (task["state"] == "validating" and task["attempt"] == attempt
                and self._latest_start(task_id, conn) == started_seq)

    def _discard(self, conn: sqlite3.Connection, task: dict[str, Any], *, actor: str,
                 attempt: int, run: int, reason: str) -> None:
        self._tasks.store.append_event(conn, "gate.discarded", actor=actor, group_id=task["group_id"],
                                       task_id=task["id"], payload={"run": run, "attempt": attempt, "reason": reason})

    def _commands(self):
        settings = self._services.settings
        return load_commands(settings.project_commands_file, default_cwd=settings.local_dir.parent)

    def _task_or_missing(self, task_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
        task = self._tasks.store.get_task(task_id, conn=conn)
        if task is None:
            raise _missing("Task", task_id)
        return task

    def _effective(self, task: dict[str, Any], conn: sqlite3.Connection) -> tuple[list[str], str]:
        if task["gate"]:
            return list(task["gate"]), "task"
        group_gate = self.store.group_gate(task["group_id"], conn=conn) or []
        return (list(group_gate), "group") if group_gate else ([], "none")

    def _review_outcome(self, task: dict[str, Any], attempt: int) -> str:
        reviews = [event for event in self._tasks.store.events(task_id=task["id"], limit=10_000)
                   if event["type"] in ("review.approved", "review.rejected")
                   and event["payload"].get("attempt") == attempt]
        if not reviews:
            return "pending"
        return "passed" if reviews[-1]["type"] == "review.approved" else "failed"

    def _run_check(self, check: str, task: dict[str, Any], attempt: int, commands) -> CommandResult:
        started = _now()
        if check == "review":
            status = self._review_outcome(task, attempt)
            note = {"passed": "approved by a reviewer", "failed": "rejected by a reviewer",
                    "pending": "no review of this attempt yet"}[status]
            return CommandResult("review", status, None, 0, note, "" if status == "passed" else status, started, _now())
        name = check[len("command:"):]
        if isinstance(commands, CommandConfigError):
            return CommandResult(name, "error", None, 0, f"Invalid project commands file: {commands}",
                                 "invalid_config", started, _now())
        return run_command(commands, name, {"task_id": task["id"], "group_id": task["group_id"], "attempt": str(attempt)})

    # -------- declared gates --------
    @service_errors
    def set_task_gate(self, task_id: str, checks: Iterable[str], *, actor: str) -> dict[str, Any]:
        """Declare the task's own gate (``[]`` removes it); event ``gate.updated``.

        Errors: ``not_found``, ``invalid_request`` (an unknown check or more
        than 10), ``invalid_state`` (the task is terminal or ``validating``).
        """
        actor = _text("actor", actor, maximum=100)
        normalized = normalize_checks(checks)
        with self._transaction() as conn:
            task = self._task_or_missing(task_id, conn)
            if normalized and task.get("kind") == "activity":
                raise _invalid("checks", "An activity has no gate: nobody reviews it", task_id=task_id)
            if task["state"] in TERMINAL_STATES or task["state"] == "validating":
                raise _conflict(f"Task {task_id} is {task['state']}", task_id=task_id, state=task["state"])
            self.store.set_task_gate(conn, task_id, normalized)
            self._tasks.store.update_task(conn, task_id, expected_revision=task["revision"])
            self._tasks.store.append_event(conn, "gate.updated", actor=actor, group_id=task["group_id"],
                                           task_id=task_id, payload={"scope": "task", "checks": normalized})
        return self.get_gate(task_id)

    @service_errors
    def set_group_gate(self, group_id: str, checks: Iterable[str], *, actor: str) -> dict[str, Any]:
        """Declare the group's default gate; event ``gate.updated``. Errors: ``not_found``, ``invalid_request``."""
        actor = _text("actor", actor, maximum=100)
        normalized = normalize_checks(checks)
        with self._transaction() as conn:
            group = self._tasks.store.get_group(group_id, conn=conn)
            if normalized and group is not None and group.get("kind") == "activities":
                raise _invalid("checks", "The activities of a project have no gate: nobody reviews them",
                               group_id=group_id)
            if not self.store.set_group_gate(conn, group_id, normalized):
                raise _missing("Group", group_id)
            self._tasks.store.append_event(conn, "gate.updated", actor=actor, group_id=group_id,
                                           payload={"scope": "group", "checks": normalized})
        return self._tasks.get_group(group_id)

    @service_errors
    def get_gate(self, task_id: str) -> dict[str, Any]:
        """The effective gate: ``{task_id, checks, source: "task"|"group"|"none", last_run}``. Errors: ``not_found``."""
        with self._tasks.store.db.connect() as conn:
            task = self._task_or_missing(task_id, conn)
            checks, source = self._effective(task, conn)
            return {"task_id": task_id, "checks": checks, "source": source,
                    "last_run": self.store.last_run(task_id, conn=conn)}

    # -------- reviews --------
    @service_errors
    def review(self, task_id: str, *, actor: str, approved: bool, comment: str = "") -> dict[str, Any]:
        """Record ``review.approved`` or ``review.rejected`` for the current attempt; the state does not change.

        Errors: ``not_found``, ``invalid_state`` (the task is not ``submitted``
        or ``validating``, or the actor is the assignee).
        """
        actor = _text("actor", actor, maximum=100)
        comment = _text("comment", comment, maximum=500, required=False)
        if not isinstance(approved, bool):
            raise _invalid("approved", "approved must be true or false")
        with self._transaction() as conn:
            task = self._task_or_missing(task_id, conn)
            if task["state"] not in ("submitted", "validating"):
                raise _conflict(f"Only a submitted task can be reviewed; {task_id} is {task['state']}",
                                task_id=task_id, state=task["state"])
            if actor == task["assignee"]:
                raise _conflict("The assignee cannot review its own task", task_id=task_id, assignee=task["assignee"])
            self._tasks.store.append_event(
                conn, "review.approved" if approved else "review.rejected", actor=actor, group_id=task["group_id"],
                task_id=task_id, payload={"attempt": task["attempt"], "comment": comment})
        return self._tasks.get_task(task_id)

    # -------- running the gate --------
    @service_errors
    def run_gate(self, task_id: str, *, actor: str, cause: str = "") -> dict[str, Any]:
        """Run the effective gate of a ``submitted`` task and return ``{outcome, run, results, task}``.

        Commands run outside the database transaction, each with its timeout.
        Errors: ``not_found``, ``invalid_state`` (not ``submitted``, no gate,
        obsolete execution or a database integrity conflict). Obsolete runs
        emit ``gate.discarded`` without storing results or changing the task.
        """
        actor = _text("actor", actor, maximum=100)
        cause = _text("cause", cause, maximum=500, required=False)
        with self._transaction() as conn:
            task = self._task_or_missing(task_id, conn)
            checks, source = self._effective(task, conn)
            if not checks:
                raise _conflict(f"{task_id} has no gate; accept it manually", task_id=task_id)
            if task["state"] != "submitted":
                raise _conflict(f"Only a submitted task can run its gate; {task_id} is {task['state']}",
                                task_id=task_id, state=task["state"])
            attempt = task["attempt"]
            run = self._next_run(task_id, attempt, conn)
            self._tasks._apply(conn, task_id, "gate_start", actor=actor, cause=cause, gate=True, event="gate.started",
                               payload={"run": run, "attempt": attempt, "checks": checks, "source": source})
            started_seq = self._latest_start(task_id, conn)
            assert started_seq is not None
        try:
            try:
                commands: Any = self._commands() if any(c.startswith("command:") for c in checks) else {}
            except CommandConfigError as exc:
                commands = exc
            executed: list[tuple[str, CommandResult]] = []
            for check in checks:
                result = self._run_check(check, task, attempt, commands)
                executed.append((check, result))
                if result.status != "passed":
                    break
            last = executed[-1][1]
            outcome = "passed" if last.status == "passed" else last.status
            action, event = {"passed": ("gate_pass", "gate.passed"), "failed": ("gate_fail", "gate.failed"),
                             "error": ("gate_return", "gate.error"), "pending": ("gate_return", "gate.pending")}[outcome]
            with self._transaction() as conn:
                obsolete = not self._is_current(task_id, attempt, started_seq, conn)
                rows = []
                if obsolete:
                    self._discard(conn, task, actor=actor, attempt=attempt, run=run, reason="superseded")
                else:
                    for position, (check, result) in enumerate(executed, start=1):
                        rows.append(self.store.insert_result(
                            conn, task_id=task_id, attempt=attempt, run=run, position=position, check_name=check,
                            status=result.status, actor=actor, started_at=result.started_at, ended_at=result.ended_at,
                            command="" if check == "review" else result.name, exit_code=result.exit_code,
                            duration_ms=result.duration_ms, output=result.output))
                        if result.status in _CHECK_EVENT:
                            self._tasks.store.append_event(
                                conn, _CHECK_EVENT[result.status], actor=actor, group_id=task["group_id"], task_id=task_id,
                                payload={"run": run, "check": check, "status": result.status, "reason": result.reason,
                                         "exit_code": result.exit_code, "duration_ms": result.duration_ms})
                    view = self._tasks._apply(conn, task_id, action, actor=actor, cause=cause, gate=True, event=event,
                                              payload={"run": run, "check": executed[-1][0], "reason": last.reason})
        except BaseException as exc:
            # Recovery owns the same identity check as finalization: an old
            # runner's exception must never abort a replacement run.
            with self._transaction() as conn:
                if self._is_current(task_id, attempt, started_seq, conn):
                    self._tasks._apply(conn, task_id, "gate_return", actor=actor, cause="gate interrupted", gate=True,
                                       event="gate.aborted", payload={"run": run, "attempt": attempt})
                else:
                    self._discard(conn, task, actor=actor, attempt=attempt, run=run, reason="interrupted_obsolete")
            if isinstance(exc, sqlite3.IntegrityError):
                raise _conflict("Gate operation conflicts with stored state", task_id=task_id) from None
            raise
        if obsolete:
            # Raise after committing the discard event, without reporting a
            # passing outcome or returning results from an invalidated run.
            raise _conflict("Gate execution is no longer current", task_id=task_id, attempt=attempt, run=run)
        if outcome == "passed":
            from .plan_autoadvance import notify_task_accepted   # ARR3-T03: the passed gate accepted the task; after the commit
            notify_task_accepted(self._services, task_id)
        return {"outcome": outcome, "run": run, "results": rows, "task": view}

    @service_errors
    def abort_gate(self, task_id: str, *, actor: str, cause: str) -> dict[str, Any]:
        """Return a ``validating`` task to ``submitted`` (for example after a crash); event ``gate.aborted``.

        This invalidates a live runner; its commands may finish, but its results
        and exception recovery cannot change this task or a later execution.

        Errors: ``not_found``, ``invalid_request`` (empty cause), ``invalid_state`` (not ``validating``).
        """
        actor = _text("actor", actor, maximum=100)
        cause = _text("cause", cause, maximum=500)
        with self._transaction() as conn:
            self._task_or_missing(task_id, conn)
            return self._tasks._apply(conn, task_id, "gate_return", actor=actor, cause=cause, gate=True,
                                      event="gate.aborted")

    @service_errors
    def results(self, task_id: str, *, attempt: int | None = None) -> list[dict[str, Any]]:
        """Stored check results of the task, by attempt, gate run and position. Errors: ``not_found``."""
        if attempt is not None and (isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0):
            raise _invalid("attempt", "attempt must be an integer >= 0")
        with self._tasks.store.db.connect() as conn:
            self._task_or_missing(task_id, conn)
            return self.store.results(task_id, attempt=attempt, conn=conn)

    @service_errors
    def project_commands(self) -> list[dict[str, Any]]:
        """Configured command names with their timeout and working directory (never the full argv).

        Errors: ``invalid_request`` (the local commands file is malformed; ``details`` says why).
        """
        try:
            commands = self._commands()
        except CommandConfigError as exc:
            raise _invalid(exc.field or "project_commands", f"Invalid project commands file: {exc}") from None
        return [{"name": c.name, "timeout": c.timeout, "cwd": str(c.cwd)} for c in commands.values()]
