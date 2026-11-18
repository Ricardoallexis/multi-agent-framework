# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Persistence for the decision register (migration 016)."""
from __future__ import annotations

import sqlite3
from typing import Any

from .database import Database, utcnow


class DecisionStore:
    def __init__(self, db: Database):
        self.db = db

    def get(self, decision_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        if conn is None:
            with self.db.connect() as own:
                return self.get(decision_id, conn=own)
        row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
        return dict(row) if row else None

    def list(self, *, project: str | None = None, run_id: str | None = None,
             conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        if conn is None:
            with self.db.connect() as own:
                return self.list(project=project, run_id=run_id, conn=own)
        clauses, params = [], []
        if project is not None:
            clauses.append("project=?")
            params.append(project)
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return [dict(row) for row in conn.execute("SELECT * FROM decisions" + where + " ORDER BY created_at, rowid", params)]

    def insert(self, conn: sqlite3.Connection, **fields: Any) -> dict[str, Any]:
        fields = {**fields, "created_at": utcnow()}
        names = tuple(fields)
        conn.execute("INSERT INTO decisions (" + ",".join(names) + ") VALUES (" + ",".join("?" for _ in names) + ")",
                     tuple(fields.values()))
        return self.get(fields["id"], conn=conn)  # type: ignore[return-value]

    def update(self, conn: sqlite3.Connection, decision_id: str, **fields: Any) -> dict[str, Any]:
        conn.execute("UPDATE decisions SET " + ",".join(f"{name}=?" for name in fields) + " WHERE id=?",
                     (*fields.values(), decision_id))
        return self.get(decision_id, conn=conn)  # type: ignore[return-value]
