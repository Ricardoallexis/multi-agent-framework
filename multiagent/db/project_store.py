# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Storage of projects, their stages and the task/session links (migration 011).

Persistence only (PRY-T02): the rules (who may change what, the plan, the labels) live in
``multiagent.services.projects``. A project is a row of the ``projects`` table that every run already
pointed to by name; a stage is a ``task_groups`` row with a ``project_id`` and a ``position`` (``1..n``
without gaps, unique per project). A ``task_session_links`` row ties a chat of another application to a task.

Writes take the caller's connection (see ``TaskStore``) so that a change and its event commit together;
reads open their own connection unless one is given. Rows come back as plain dicts.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from .database import Database, utcnow

_REORDER_SHIFT = 1000


class StaleProjectRevision(Exception):
    """The project exists but its revision is not the expected one."""

    def __init__(self, project_id: str, expected: int, current: int):
        super().__init__(f"Project {project_id} is at revision {current}, not {expected}")
        self.project_id = project_id
        self.expected = expected
        self.current = current


def _project(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["archived"] = item["archived_at"] is not None
    return item


class ProjectStore:
    def __init__(self, db: Database):
        self.db = db

    @contextmanager
    def _reader(self, conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
        else:
            with self.db.connect() as own:
                yield own

    # -------- projects --------
    def get_project(self, project_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._reader(conn) as c:
            row = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return _project(row) if row else None

    def name_taken(self, name: str, *, conn: sqlite3.Connection | None = None) -> bool:
        """Whether a project already has this id or this name (a run may have created it by name)."""
        with self._reader(conn) as c:
            return c.execute("SELECT 1 FROM projects WHERE id=? OR name=?", (name, name)).fetchone() is not None

    def list_projects(self, *, archived: bool | None = False,
                      conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        """Projects in creation order: the active ones (``False``), the archived ones (``True``) or all (``None``)."""
        where = "" if archived is None else (" WHERE archived_at IS NOT NULL" if archived else " WHERE archived_at IS NULL")
        with self._reader(conn) as c:
            return [_project(row) for row in c.execute(f"SELECT * FROM projects{where} ORDER BY rowid")]

    def insert_project(self, conn: sqlite3.Connection, project_id: str, title: str, *, visibility: str,
                       created_by: str) -> dict[str, Any]:
        """A new project at revision 1 with ``id = name = project_id``."""
        conn.execute(
            "INSERT INTO projects(id,name,created_at,title,visibility,created_by) VALUES(?,?,?,?,?,?)",
            (project_id, project_id, utcnow(), title, visibility, created_by),
        )
        return self.get_project(project_id, conn=conn)  # type: ignore[return-value]

    def update_project(self, conn: sqlite3.Connection, project_id: str, *, expected_revision: int,
                       title: str | None = None, visibility: str | None = None,
                       archived: bool | None = None) -> dict[str, Any] | None:
        """Change the given fields and bump ``revision``, only if it is still ``expected_revision``.

        Returns the updated project, ``None`` when it does not exist, or raises ``StaleProjectRevision``.
        With no field it only bumps the revision (the stages changed).
        """
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if visibility is not None:
            changes["visibility"] = visibility
        if archived is not None:
            changes["archived_at"] = utcnow() if archived else None
        assignments = ",".join(f"{column}=?" for column in changes)
        cursor = conn.execute(
            f"UPDATE projects SET {assignments + ',' if assignments else ''}revision=revision+1 WHERE id=? AND revision=?",
            (*changes.values(), project_id, expected_revision),
        )
        if cursor.rowcount:
            return self.get_project(project_id, conn=conn)
        current = self.get_project(project_id, conn=conn)
        if current is None:
            return None
        raise StaleProjectRevision(project_id, expected_revision, current["revision"])

    # -------- stages (the groups themselves are read with TaskStore.list_groups(project_id=...)) --------
    def attach_group(self, conn: sqlite3.Connection, group_id: str, project_id: str, position: int) -> bool:
        cursor = conn.execute("UPDATE task_groups SET project_id=?,position=?,updated_at=? WHERE id=?",
                              (project_id, position, utcnow(), group_id))
        return bool(cursor.rowcount)

    def write_positions(self, conn: sqlite3.Connection, project_id: str, group_ids: Iterable[str]) -> None:
        """Give the stages positions ``1..n`` in this order, inside the caller's transaction.

        The positions are unique per project, so the current ones first move out of the way (``+1000``)
        and the final ones are written afterwards.
        """
        conn.execute("UPDATE task_groups SET position=position+? WHERE project_id=?", (_REORDER_SHIFT, project_id))
        for position, group_id in enumerate(group_ids, start=1):
            conn.execute("UPDATE task_groups SET position=? WHERE id=? AND project_id=?",
                         (position, group_id, project_id))

    def closed_at(self, group_id: str, *, conn: sqlite3.Connection | None = None) -> str | None:
        """When the group last went to ``closed`` (its ``group.lifecycle_changed`` event), or ``None``."""
        with self._reader(conn) as c:
            rows = c.execute(
                "SELECT created_at,payload_json FROM team_events WHERE type='group.lifecycle_changed' AND group_id=? "
                "ORDER BY seq DESC", (group_id,)).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("to") == "closed":
                return row["created_at"]
        return None

    # -------- task/session links --------
    def add_session_link(self, conn: sqlite3.Connection, task_id: str, session_id: str, *, linked_by: str) -> bool:
        """Link a session to a task; ``False`` when the link already existed."""
        cursor = conn.execute(
            "INSERT OR IGNORE INTO task_session_links(task_id,session_id,linked_by,created_at) VALUES(?,?,?,?)",
            (task_id, session_id, linked_by, utcnow()))
        return bool(cursor.rowcount)

    def remove_session_link(self, conn: sqlite3.Connection, task_id: str, session_id: str) -> bool:
        cursor = conn.execute("DELETE FROM task_session_links WHERE task_id=? AND session_id=?", (task_id, session_id))
        return bool(cursor.rowcount)

    def session_links(self, task_id: str, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        with self._reader(conn) as c:
            return [dict(row) for row in c.execute(
                "SELECT * FROM task_session_links WHERE task_id=? ORDER BY created_at,session_id", (task_id,))]

    def task_ids_by_session(self, session_ids: Iterable[str], *,
                            conn: sqlite3.Connection | None = None) -> dict[str, list[str]]:
        """``session_id -> [task_id]`` for the linked sessions among ``session_ids``."""
        wanted = list(dict.fromkeys(session_ids))
        found: dict[str, list[str]] = {}
        with self._reader(conn) as c:
            for start in range(0, len(wanted), 500):
                chunk = wanted[start:start + 500]
                marks = ",".join("?" * len(chunk))
                for row in c.execute(
                        f"SELECT session_id,task_id FROM task_session_links WHERE session_id IN ({marks}) "
                        f"ORDER BY task_id", chunk):
                    found.setdefault(row["session_id"], []).append(row["task_id"])
        return found
