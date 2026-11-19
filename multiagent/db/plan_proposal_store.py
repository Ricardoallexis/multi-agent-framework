# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SQLite persistence for plan proposals and their history."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .database import Database, utcnow


class PlanProposalStore:
    def __init__(self, db: Database):
        self.db = db

    def get(self, proposal_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        if conn is None:
            with self.db.connect() as own:
                return self.get(proposal_id, conn=own)
        row = conn.execute("SELECT * FROM plan_proposals WHERE id=?", (proposal_id,)).fetchone()
        return dict(row) if row else None

    def list(self, *, group_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if group_id is not None:
            clauses.append("group_id=?")
            params.append(group_id)
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM plan_proposals" + where + " ORDER BY created_at, rowid", params)]

    def history(self, proposal_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM plan_proposal_history WHERE proposal_id=? ORDER BY id", (proposal_id,))]

    def insert(self, conn: sqlite3.Connection, **fields: Any) -> None:
        fields = {"created_at": utcnow(), **fields}
        names = tuple(fields)
        conn.execute("INSERT INTO plan_proposals (" + ",".join(names) + ") VALUES (" +
                     ",".join("?" for _ in names) + ")", tuple(fields.values()))

    def update(self, conn: sqlite3.Connection, proposal_id: str, **fields: Any) -> None:
        conn.execute("UPDATE plan_proposals SET " + ",".join(f"{name}=?" for name in fields) +
                     " WHERE id=?", (*fields.values(), proposal_id))

    def append(self, conn: sqlite3.Connection, proposal_id: str, *, kind: str, actor: str,
               channel: str, note: str, revision: int, changes: dict[str, Any] | None = None) -> None:
        conn.execute("INSERT INTO plan_proposal_history "
                     "(proposal_id,kind,actor,channel,at,note,revision,changes_json) VALUES (?,?,?,?,?,?,?,?)",
                     (proposal_id, kind, actor, channel, utcnow(), note, revision,
                      json.dumps(changes or {}, ensure_ascii=False, sort_keys=True)))
