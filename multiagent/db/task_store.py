# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Storage of task groups, tasks, dependencies and team events (migration 004).

Persistence only (ORQ-T03): no transition rules, no readiness and no cycle
checks. Those live in the task services (ORQ-T05) and the pure graph (ORQ-T04).

Writes take the caller's connection so that a state change and its event are
committed together::

    with task_store.transaction() as conn:
        task = task_store.get_task(task_id, conn=conn)
        task_store.update_task(conn, task_id, expected_revision=task["revision"], state="claimed", assignee=actor)
        task_store.append_event(conn, "task.claimed", actor=actor, group_id=task["group_id"], task_id=task_id)

Reads open their own connection unless one is given. Rows come back as plain
dicts with the JSON columns decoded (``requirements``, ``file_scope``,
``exclusive_resources``, ``gate``, ``execution``, ``payload``).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from .database import Database, utcnow

MAX_EVENT_PAYLOAD_BYTES = 2048

_TASK_JSON = {
    "requirements_json": "requirements",
    "file_scope_json": "file_scope",
    "exclusive_resources_json": "exclusive_resources",
    "gate_json": "gate",
    "execution_json": "execution",
}


class StaleRevision(Exception):
    """The task exists but its revision is not the expected one."""

    def __init__(self, task_id: str, expected: int, current: int):
        super().__init__(f"Task {task_id} is at revision {current}, not {expected}")
        self.task_id = task_id
        self.expected = expected
        self.current = current


def _group(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["authorized"] = bool(item["authorized"])
    # Migration 005 adds the group's default gate; decode it like the task JSON columns.
    if "default_gate_json" in item:
        item["default_gate"] = json.loads(item.pop("default_gate_json"))
    return item


def _task(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for column, key in _TASK_JSON.items():
        item[key] = json.loads(item.pop(column))
    return item


def _event(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = json.loads(item.pop("payload_json"))
    return item


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class TaskStore:
    def __init__(self, db: Database):
        self.db = db

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One write transaction (``BEGIN IMMEDIATE``); committed on success, rolled back on error."""
        with self.db.tx() as conn:
            yield conn

    @contextmanager
    def _reader(self, conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
        else:
            with self.db.connect() as own:
                yield own

    # -------- groups --------
    def insert_group(self, conn: sqlite3.Connection, group_id: str, title: str, *, actor: str,
                     authorized: bool = False, default_requires: str = "accepted") -> dict[str, Any]:
        now = utcnow()
        conn.execute(
            "INSERT INTO task_groups(id,title,authorized,default_requires,created_by,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (group_id, title, int(authorized), default_requires, actor, now, now),
        )
        return self.get_group(group_id, conn=conn)  # type: ignore[return-value]

    def update_group(self, conn: sqlite3.Connection, group_id: str, *, title: str | None = None,
                     authorized: bool | None = None, default_requires: str | None = None) -> dict[str, Any] | None:
        """Change the given fields; ``None`` when the group does not exist."""
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if authorized is not None:
            changes["authorized"] = int(authorized)
        if default_requires is not None:
            changes["default_requires"] = default_requires
        changes["updated_at"] = utcnow()
        assignments = ",".join(f"{column}=?" for column in changes)
        cursor = conn.execute(f"UPDATE task_groups SET {assignments} WHERE id=?", (*changes.values(), group_id))
        return self.get_group(group_id, conn=conn) if cursor.rowcount else None

    def get_group(self, group_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._reader(conn) as c:
            row = c.execute("SELECT * FROM task_groups WHERE id=?", (group_id,)).fetchone()
        return _group(row) if row else None

    def list_groups(self, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        with self._reader(conn) as c:
            return [_group(row) for row in c.execute("SELECT * FROM task_groups ORDER BY rowid")]

    # -------- tasks --------
    def insert_task(self, conn: sqlite3.Connection, task_id: str, group_id: str, title: str, *, actor: str,
                    objective: str = "", preferred: str = "", requirements: Iterable[str] = (),
                    file_scope: Iterable[str] = ()) -> dict[str, Any]:
        """Insert an ``open`` task at revision 1 and attempt 0."""
        now = utcnow()
        conn.execute(
            "INSERT INTO tasks(id,group_id,title,objective,state,preferred,requirements_json,file_scope_json,"
            "created_by,created_at,updated_at) VALUES(?,?,?,?,'open',?,?,?,?,?,?)",
            (task_id, group_id, title, objective, preferred, _dumps(list(requirements)),
             _dumps(list(file_scope)), actor, now, now),
        )
        return self.get_task(task_id, conn=conn)  # type: ignore[return-value]

    def get_task(self, task_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._reader(conn) as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _task(row) if row else None

    def list_tasks(self, *, group_id: str | None = None, states: Iterable[str] | None = None,
                   assignee: str | None = None, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        """Tasks filtered by group, stored states and assignee, in creation order."""
        where: list[str] = []
        params: list[Any] = []
        if group_id is not None:
            where.append("group_id=?")
            params.append(group_id)
        if states is not None:
            wanted = list(states)
            if not wanted:
                return []
            where.append(f"state IN ({','.join('?' * len(wanted))})")
            params.extend(wanted)
        if assignee is not None:
            where.append("assignee=?")
            params.append(assignee)
        sql = "SELECT * FROM tasks" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY rowid"
        with self._reader(conn) as c:
            return [_task(row) for row in c.execute(sql, params)]

    def update_task(self, conn: sqlite3.Connection, task_id: str, *, expected_revision: int,
                    state: str | None = None, assignee: str | None = None,
                    attempt: int | None = None) -> dict[str, Any] | None:
        """Change the given fields and bump ``revision``, only if it is still ``expected_revision``.

        Returns the updated task, ``None`` when it does not exist, or raises
        ``StaleRevision`` when another change came first. With no fields it only
        bumps the revision (a dependency of the task changed).
        """
        changes: dict[str, Any] = {}
        if state is not None:
            changes["state"] = state
        if assignee is not None:
            changes["assignee"] = assignee
        if attempt is not None:
            changes["attempt"] = attempt
        changes["updated_at"] = utcnow()
        assignments = ",".join(f"{column}=?" for column in changes)
        cursor = conn.execute(
            f"UPDATE tasks SET {assignments},revision=revision+1 WHERE id=? AND revision=?",
            (*changes.values(), task_id, expected_revision),
        )
        if cursor.rowcount:
            return self.get_task(task_id, conn=conn)
        current = self.get_task(task_id, conn=conn)
        if current is None:
            return None
        raise StaleRevision(task_id, expected_revision, current["revision"])

    # -------- dependencies --------
    def insert_dependency(self, conn: sqlite3.Connection, task_id: str, depends_on_id: str,
                          requires: str = "accepted") -> None:
        conn.execute(
            "INSERT INTO task_deps(task_id,depends_on_id,requires,created_at) VALUES(?,?,?,?)",
            (task_id, depends_on_id, requires, utcnow()),
        )

    def delete_dependency(self, conn: sqlite3.Connection, task_id: str, depends_on_id: str) -> bool:
        cursor = conn.execute("DELETE FROM task_deps WHERE task_id=? AND depends_on_id=?", (task_id, depends_on_id))
        return bool(cursor.rowcount)

    def dependencies(self, *, task_id: str | None = None, depends_on_id: str | None = None,
                     conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        """Dependency edges ``{task_id, depends_on_id, requires}``, all of them or filtered by one end."""
        where: list[str] = []
        params: list[Any] = []
        if task_id is not None:
            where.append("task_id=?")
            params.append(task_id)
        if depends_on_id is not None:
            where.append("depends_on_id=?")
            params.append(depends_on_id)
        sql = ("SELECT task_id,depends_on_id,requires FROM task_deps"
               + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY rowid")
        with self._reader(conn) as c:
            return [dict(row) for row in c.execute(sql, params)]

    # -------- team events --------
    def append_event(self, conn: sqlite3.Connection, event_type: str, *, actor: str, group_id: str = "",
                     task_id: str = "", cause: str = "", ref: str = "",
                     payload: dict[str, Any] | None = None) -> int:
        """Append one event and return its ``seq``. ``ValueError`` if the payload exceeds 2048 bytes."""
        encoded = _dumps(payload or {})
        if len(encoded.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError(f"Event payload exceeds {MAX_EVENT_PAYLOAD_BYTES} bytes")
        cursor = conn.execute(
            "INSERT INTO team_events(type,group_id,task_id,actor,cause,ref,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (event_type, group_id, task_id, actor, cause, ref, encoded, utcnow()),
        )
        return int(cursor.lastrowid)

    def events(self, *, after: int = 0, group_id: str | None = None, task_id: str | None = None,
               limit: int = 500, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        """Events with ``seq > after``, oldest first, at most ``limit``."""
        where = ["seq>?"]
        params: list[Any] = [after]
        if group_id is not None:
            where.append("group_id=?")
            params.append(group_id)
        if task_id is not None:
            where.append("task_id=?")
            params.append(task_id)
        params.append(limit)
        sql = f"SELECT * FROM team_events WHERE {' AND '.join(where)} ORDER BY seq LIMIT ?"
        with self._reader(conn) as c:
            return [_event(row) for row in c.execute(sql, params)]
