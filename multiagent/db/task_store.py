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
    "lock_policy_json": "lock_policy",
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
                     authorized: bool = False, default_requires: str = "accepted",
                     default_gate: Iterable[dict[str, Any]] = (),
                     legacy_release_mode: str | None = None) -> dict[str, Any]:
        now = utcnow()
        conn.execute(
            "INSERT INTO task_groups(id,title,authorized,default_requires,created_by,created_at,updated_at,default_gate_json,legacy_release_mode) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (group_id, title, int(authorized), default_requires, actor, now, now, _dumps(list(default_gate)),
             legacy_release_mode),
        )
        return self.get_group(group_id, conn=conn)  # type: ignore[return-value]

    def set_legacy_release_mode(self, conn: sqlite3.Connection, group_id: str, mode: str | None) -> bool:
        cursor = conn.execute("UPDATE task_groups SET legacy_release_mode=?,updated_at=? WHERE id=?",
                              (mode, utcnow(), group_id))
        return bool(cursor.rowcount)

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

    def insert_snapshot_task(self, conn: sqlite3.Connection, task: dict[str, Any], *, actor: str) -> dict[str, Any]:
        now = utcnow()
        conn.execute(
            "INSERT INTO tasks(id,group_id,title,objective,state,assignee,preferred,requirements_json,file_scope_json,gate_json,"
            "attempt,revision,created_by,created_at,updated_at,base_commit,context_refs_json,constraints_json,"
            "lock_policy_json,workspace_domain,snapshot_epoch_pending) VALUES(?,?,?,?,?,?,?,?,?,?,0,1,?,?,?,?,?,?,?,?,?)",
            (task["id"], task["group_id"], task["title"], task.get("objective", ""), task["state"],
             task.get("assignee", ""), task.get("preferred", ""), _dumps(task.get("requirements", [])),
             _dumps(task.get("file_scope", [])), _dumps(task.get("gate", [])), actor, now, now, task.get("base_commit", ""),
             _dumps(task.get("context_refs", [])), _dumps(task.get("constraints", [])),
             _dumps(task["lock_policy"]), task.get("workspace_domain", ""), int(task.get("epoch_pending", False))),
        )
        return self.get_task(task["id"], conn=conn)  # type: ignore[return-value]

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
                    attempt: int | None = None, lock_policy: dict[str, Any] | None = None,
                    workspace_domain: str | None = None,
                    snapshot_epoch_pending: bool | None = None) -> dict[str, Any] | None:
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
        if lock_policy is not None:
            changes["lock_policy_json"] = _dumps(lock_policy)
        if workspace_domain is not None:
            changes["workspace_domain"] = workspace_domain
        if snapshot_epoch_pending is not None:
            changes["snapshot_epoch_pending"] = int(snapshot_epoch_pending)
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

    def insert_snapshot(self, conn: sqlite3.Connection, *, source_key: str, source_ref: str,
                        snapshot_sha256: str, actor: str, snapshot_json: str, event_seq: int,
                        contract_version: int = 2) -> None:
        conn.execute(
            "INSERT INTO task_snapshots(source_key,source_ref,snapshot_sha256,actor,snapshot_json,imported_at,event_seq,contract_version) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (source_key, source_ref, snapshot_sha256, actor, snapshot_json, utcnow(), event_seq, contract_version),
        )

    def snapshot(self, source_key: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._reader(conn) as c:
            row = c.execute("SELECT * FROM task_snapshots WHERE source_key=?", (source_key,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["snapshot"] = json.loads(item.pop("snapshot_json"))
        return item

    def snapshots(self, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        with self._reader(conn) as c:
            rows = c.execute("SELECT * FROM task_snapshots ORDER BY imported_at,source_key").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["snapshot"] = json.loads(item.pop("snapshot_json"))
            items.append(item)
        return items

    def add_snapshot_object(self, conn: sqlite3.Connection, *, source_key: str, object_type: str,
                            object_id: str, source_id: str = "", source_state: str = "") -> None:
        conn.execute(
            "INSERT INTO task_snapshot_objects(source_key,object_type,object_id,source_id,source_state) "
            "VALUES(?,?,?,?,?)", (source_key, object_type, object_id, source_id, source_state),
        )

    def snapshot_provenance(self, object_type: str, object_id: str, *,
                            conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._reader(conn) as c:
            row = c.execute(
                "SELECT o.source_id,o.source_state,s.source_key,s.source_ref,s.snapshot_sha256,s.actor,s.imported_at "
                "FROM task_snapshot_objects o JOIN task_snapshots s USING(source_key) "
                "WHERE o.object_type=? AND o.object_id=? AND s.state='applied'",
                (object_type, object_id),
            ).fetchone()
        return dict(row) if row else None

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

    def insert_legacy_release_dependency(self, conn: sqlite3.Connection, task_id: str, depends_on_id: str,
                                         source_ref: str) -> None:
        conn.execute(
            "INSERT INTO legacy_release_dependencies(task_id,depends_on_id,source_ref,created_at) VALUES(?,?,?,?)",
            (task_id, depends_on_id, source_ref, utcnow()),
        )

    def delete_legacy_release_dependency(self, conn: sqlite3.Connection, task_id: str, depends_on_id: str) -> bool:
        cursor = conn.execute("DELETE FROM legacy_release_dependencies WHERE task_id=? AND depends_on_id=?",
                              (task_id, depends_on_id))
        return bool(cursor.rowcount)

    def legacy_release_dependencies(self, *, task_id: str | None = None, depends_on_id: str | None = None,
                                    conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if task_id is not None:
            where.append("task_id=?")
            params.append(task_id)
        if depends_on_id is not None:
            where.append("depends_on_id=?")
            params.append(depends_on_id)
        sql = ("SELECT task_id,depends_on_id,source_ref FROM legacy_release_dependencies"
               + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY rowid")
        with self._reader(conn) as c:
            return [dict(row) for row in c.execute(sql, params)]

    def release_fact(self, task_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._reader(conn) as c:
            row = c.execute("SELECT * FROM legacy_release_facts WHERE task_id=?", (task_id,)).fetchone()
        item = dict(row) if row else None
        if item is not None:
            item["has_patch"] = bool(item["has_patch"])
        return item

    def release_facts(self, *, conn: sqlite3.Connection | None = None) -> dict[str, dict[str, Any]]:
        with self._reader(conn) as c:
            rows = c.execute("SELECT * FROM legacy_release_facts ORDER BY rowid").fetchall()
        output: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            item["has_patch"] = bool(item["has_patch"])
            output[item["task_id"]] = item
        return output

    def write_release_fact(self, conn: sqlite3.Connection, task_id: str, *, delivery_ref: str,
                           delivery_sha256: str, has_patch: bool, patchset_sha256: str,
                           test_verdict: str | None = None, test_patchset_sha256: str = "", test_ref: str = "",
                           integration_commit: str = "", integration_ref: str = "",
                           actor: str) -> None:
        conn.execute(
            "INSERT INTO legacy_release_facts(task_id,delivery_ref,delivery_sha256,has_patch,patchset_sha256,"
            "test_verdict,test_patchset_sha256,test_ref,integration_commit,integration_ref,updated_by,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
            "delivery_ref=excluded.delivery_ref,delivery_sha256=excluded.delivery_sha256,"
            "has_patch=excluded.has_patch,patchset_sha256=excluded.patchset_sha256,"
            "test_verdict=excluded.test_verdict,test_patchset_sha256=excluded.test_patchset_sha256,"
            "test_ref=excluded.test_ref,integration_commit=excluded.integration_commit,"
            "integration_ref=excluded.integration_ref,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (task_id, delivery_ref, delivery_sha256, int(has_patch), patchset_sha256, test_verdict,
             test_patchset_sha256, test_ref, integration_commit, integration_ref, actor, utcnow()),
        )

    def reset_release_fact(self, conn: sqlite3.Connection, task_id: str) -> bool:
        cursor = conn.execute("DELETE FROM legacy_release_facts WHERE task_id=?", (task_id,))
        return bool(cursor.rowcount)

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
