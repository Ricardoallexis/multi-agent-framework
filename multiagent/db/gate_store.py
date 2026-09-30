# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Storage of gates and gate results (migration 005).

Persistence only (ORQ-T11): no check rules and no state changes. Those live in
the gate services (ORQ-T13), which run the checks and call the single task
transition function.

A task's own gate is ``tasks.gate_json``; a group's default gate is
``task_groups.default_gate_json``. Each check of each gate run is one row of
``gate_results``, numbered by task, attempt, run and position. Writes take the
caller's connection (see ``TaskStore``); reads open their own unless one is
given.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from .database import Database, utcnow

RESULT_STATUSES = ("passed", "failed", "error", "pending")
OUTPUT_EXCERPT_CHARS = 4000


def excerpt(output: str) -> str:
    """The last ``OUTPUT_EXCERPT_CHARS`` characters of a command's output."""
    return output[-OUTPUT_EXCERPT_CHARS:]


class GateStore:
    def __init__(self, db: Database):
        self.db = db

    @contextmanager
    def _reader(self, conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
        else:
            with self.db.connect() as own:
                yield own

    # -------- declared gates --------
    def set_task_gate(self, conn: sqlite3.Connection, task_id: str, checks: Iterable[str]) -> bool:
        """Store a task's own gate; ``False`` when the task does not exist. Does not bump the revision."""
        cursor = conn.execute("UPDATE tasks SET gate_json=?, updated_at=? WHERE id=?",
                              (json.dumps(list(checks)), utcnow(), task_id))
        return bool(cursor.rowcount)

    def set_group_gate(self, conn: sqlite3.Connection, group_id: str, checks: Iterable[str]) -> bool:
        """Store a group's default gate; ``False`` when the group does not exist."""
        cursor = conn.execute("UPDATE task_groups SET default_gate_json=?, updated_at=? WHERE id=?",
                              (json.dumps(list(checks)), utcnow(), group_id))
        return bool(cursor.rowcount)

    def task_gate(self, task_id: str, *, conn: sqlite3.Connection | None = None) -> list[str] | None:
        with self._reader(conn) as c:
            row = c.execute("SELECT gate_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def group_gate(self, group_id: str, *, conn: sqlite3.Connection | None = None) -> list[str] | None:
        with self._reader(conn) as c:
            row = c.execute("SELECT default_gate_json FROM task_groups WHERE id=?", (group_id,)).fetchone()
        return json.loads(row[0]) if row else None

    # -------- results --------
    def next_run(self, task_id: str, attempt: int, *, conn: sqlite3.Connection | None = None) -> int:
        """The number of the next gate run for this task and attempt (1, 2, ...)."""
        with self._reader(conn) as c:
            row = c.execute("SELECT MAX(run) FROM gate_results WHERE task_id=? AND attempt=?",
                            (task_id, attempt)).fetchone()
        return int(row[0] or 0) + 1

    def insert_result(self, conn: sqlite3.Connection, *, task_id: str, attempt: int, run: int, position: int,
                      check_name: str, status: str, actor: str, started_at: str, ended_at: str,
                      command: str = "", exit_code: int | None = None, duration_ms: int | None = None,
                      output: str = "", report_ref: str = "") -> dict[str, Any]:
        """Store one check result; the output is cut to its last 4000 characters.

        ``ValueError`` for an unknown status; ``sqlite3.IntegrityError`` for a
        repeated (task, attempt, run, position) or an unknown task.
        """
        if status not in RESULT_STATUSES:
            raise ValueError(f"Unknown gate result status: {status!r}")
        cursor = conn.execute(
            "INSERT INTO gate_results(task_id,attempt,run,position,check_name,status,command,exit_code,duration_ms,"
            "output_excerpt,report_ref,actor,started_at,ended_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, attempt, run, position, check_name, status, command, exit_code, duration_ms,
             excerpt(output), report_ref, actor, started_at, ended_at),
        )
        row = conn.execute("SELECT * FROM gate_results WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def results(self, task_id: str, *, attempt: int | None = None,
                conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        """Stored results of a task (optionally one attempt), ordered by attempt, run and position."""
        sql = "SELECT * FROM gate_results WHERE task_id=?"
        params: list[Any] = [task_id]
        if attempt is not None:
            sql += " AND attempt=?"
            params.append(attempt)
        sql += " ORDER BY attempt, run, position"
        with self._reader(conn) as c:
            return [dict(row) for row in c.execute(sql, params)]

    def last_run(self, task_id: str, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        """Results of the latest gate run of the task (latest attempt, then latest run); ``[]`` if none."""
        with self._reader(conn) as c:
            row = c.execute("SELECT attempt, run FROM gate_results WHERE task_id=? ORDER BY attempt DESC, run DESC LIMIT 1",
                            (task_id,)).fetchone()
            if row is None:
                return []
            return [dict(r) for r in c.execute(
                "SELECT * FROM gate_results WHERE task_id=? AND attempt=? AND run=? ORDER BY position",
                (task_id, row["attempt"], row["run"]))]
