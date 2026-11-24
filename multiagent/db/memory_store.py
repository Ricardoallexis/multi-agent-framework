# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Storage of project memory entries (migration 014, CAP-T20).

Persistence only: the rules (who may write or delete, the limits, how trust is computed) live in
``multiagent.services.memory``. Writes take the caller's connection (see ``TaskStore``); reads open their own
unless one is given. Rows come back as plain dicts.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .database import Database, utcnow


class MemoryStore:
    def __init__(self, db: Database):
        self.db = db

    @contextmanager
    def _reader(self, conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
        else:
            with self.db.connect() as own:
                yield own

    def insert(self, conn: sqlite3.Connection, *, entry_id: str, project_id: str, kind: str, title: str, body: str,
               confidence: str, source_kind: str, source_actor: str, source_ref: str, trust: str,
               sha256: str) -> dict[str, Any]:
        conn.execute(
            "INSERT INTO project_memory(id,project_id,kind,title,body,confidence,source_kind,source_actor,"
            "source_ref,trust,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry_id, project_id, kind, title, body, confidence, source_kind, source_actor, source_ref, trust,
             sha256, utcnow()))
        return self.get(entry_id, conn=conn)  # type: ignore[return-value]

    def get(self, entry_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._reader(conn) as c:
            row = c.execute("SELECT * FROM project_memory WHERE id=?", (entry_id,)).fetchone()
        return dict(row) if row else None

    def list(self, project_id: str, *, kind: str | None = None, conn: sqlite3.Connection | None = None,
             limit: int | None = None) -> list[dict[str, Any]]:
        """The entries of a project, oldest first (``rowid`` breaks ties of the same instant)."""
        sql, params = "SELECT * FROM project_memory WHERE project_id=?", [project_id]
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY created_at, rowid"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._reader(conn) as c:
            return [dict(row) for row in c.execute(sql, params)]

    def count(self, project_id: str, *, conn: sqlite3.Connection | None = None) -> int:
        with self._reader(conn) as c:
            return int(c.execute("SELECT COUNT(*) FROM project_memory WHERE project_id=?",
                                 (project_id,)).fetchone()[0])

    def delete(self, conn: sqlite3.Connection, entry_id: str) -> bool:
        return bool(conn.execute("DELETE FROM project_memory WHERE id=?", (entry_id,)).rowcount)

    def set_trust(self, conn: sqlite3.Connection, entry_id: str, trust: str, *, confidence: str | None = None) -> bool:
        """Rewrite the trust of one entry (and its confidence when given): MEM-T01, after a person's approval."""
        if confidence is None:
            return bool(conn.execute("UPDATE project_memory SET trust=? WHERE id=?", (trust, entry_id)).rowcount)
        return bool(conn.execute("UPDATE project_memory SET trust=?, confidence=? WHERE id=?",
                                 (trust, confidence, entry_id)).rowcount)

    def project_id(self, project: str, *, conn: sqlite3.Connection | None = None) -> str | None:
        """The id of the project that has this id or this name, or ``None``."""
        with self._reader(conn) as c:
            row = c.execute("SELECT id FROM projects WHERE id=? OR name=? ORDER BY (id=?) DESC LIMIT 1",
                            (project, project, project)).fetchone()
        return row["id"] if row else None
