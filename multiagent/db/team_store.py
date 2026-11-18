# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Persistence for versioned team definitions and human approvals (migration 015)."""
from __future__ import annotations

import sqlite3
from typing import Any

from .database import Database, utcnow


class TeamStore:
    """SQLite persistence for ``team_versions``."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, version_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        if conn is None:
            with self.db.connect() as own:
                return self.get(version_id, conn=own)
        row = conn.execute("SELECT * FROM team_versions WHERE id=?", (version_id,)).fetchone()
        return dict(row) if row else None

    def list(self, bundle: str, *, status: str | None = None,
             conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        if conn is None:
            with self.db.connect() as own:
                return self.list(bundle, status=status, conn=own)
        clauses = ["bundle=?"]
        params: list[Any] = [bundle]
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses)
        rows = conn.execute(
            "SELECT * FROM team_versions" + where + " ORDER BY version ASC, rowid ASC",
            params,
        )
        return [dict(row) for row in rows]

    def next_version(self, conn: sqlite3.Connection, bundle: str, team_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM team_versions WHERE bundle=? AND team_id=?",
            (bundle, team_id),
        ).fetchone()
        return int(row[0])

    def next_approved_seq(self, conn: sqlite3.Connection, bundle: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(approved_seq), 0) + 1 FROM team_versions WHERE bundle=?",
            (bundle,),
        ).fetchone()
        return int(row[0])

    def latest_approved(self, bundle: str, *, before_seq: int | None = None,
                        conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        if conn is None:
            with self.db.connect() as own:
                return self.latest_approved(bundle, before_seq=before_seq, conn=own)
        if before_seq is None:
            row = conn.execute(
                "SELECT * FROM team_versions WHERE bundle=? AND status='approved' "
                "ORDER BY COALESCE(approved_seq, 0) DESC, decided_at DESC, rowid DESC LIMIT 1",
                (bundle,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM team_versions WHERE bundle=? AND status='approved' AND approved_seq < ? "
                "ORDER BY COALESCE(approved_seq, 0) DESC, decided_at DESC, rowid DESC LIMIT 1",
                (bundle, before_seq),
            ).fetchone()
        return dict(row) if row else None

    def insert(self, conn: sqlite3.Connection, **fields: Any) -> dict[str, Any]:
        fields = {"created_at": utcnow(), **fields}
        names = tuple(fields)
        conn.execute(
            "INSERT INTO team_versions (" + ",".join(names) + ") VALUES (" + ",".join("?" for _ in names) + ")",
            tuple(fields.values()),
        )
        return self.get(fields["id"], conn=conn)  # type: ignore[return-value]

    def update(self, conn: sqlite3.Connection, version_id: str, **fields: Any) -> dict[str, Any]:
        conn.execute(
            "UPDATE team_versions SET " + ",".join(f"{name}=?" for name in fields) + " WHERE id=?",
            (*fields.values(), version_id),
        )
        return self.get(version_id, conn=conn)  # type: ignore[return-value]
