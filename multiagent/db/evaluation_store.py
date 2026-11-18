# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Persistence for saved evaluation runs and baselines (migration 017)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .database import Database, utcnow

_SUMMARY = "id, set_name, set_version, set_sha256, live, score, actor, created_at"


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    return {**dict(row), "live": bool(row["live"])}


class EvaluationStore:
    def __init__(self, db: Database):
        self.db = db

    def insert_run(self, conn: sqlite3.Connection, *, run_id: str, report: dict[str, Any], live: bool,
                   actor: str | None) -> None:
        fingerprint = report.get("meta", {}).get("fingerprint", {})
        conn.execute(
            "INSERT INTO evaluation_runs (id,set_name,set_version,set_sha256,live,fingerprint_json,report_json,"
            "score,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, report["name"], report["version"], fingerprint.get("set_sha256", ""), int(live),
             json.dumps(fingerprint, ensure_ascii=False, sort_keys=True),
             json.dumps(report, ensure_ascii=False), report["score"], actor, utcnow()))

    def get_run(self, run_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        if conn is None:
            with self.db.connect() as own:
                return self.get_run(run_id, conn=own)
        row = conn.execute("SELECT * FROM evaluation_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        data = _summary(row)
        data["fingerprint"] = json.loads(data.pop("fingerprint_json"))
        data["report"] = json.loads(data.pop("report_json"))
        return data

    def list_runs(self, *, set_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where, params = ("WHERE set_name=? ", (set_name,)) if set_name is not None else ("", ())
        with self.db.connect() as conn:
            return [_summary(row) for row in conn.execute(
                f"SELECT {_SUMMARY} FROM evaluation_runs {where}ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*params, limit))]

    def set_baseline(self, conn: sqlite3.Connection, *, set_name: str, run_id: str, actor: str | None) -> None:
        conn.execute(
            "INSERT INTO evaluation_baselines (set_name,run_id,actor,created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(set_name) DO UPDATE SET run_id=excluded.run_id, actor=excluded.actor, "
            "created_at=excluded.created_at", (set_name, run_id, actor, utcnow()))

    def get_baseline(self, set_name: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        if conn is None:
            with self.db.connect() as own:
                return self.get_baseline(set_name, conn=own)
        row = conn.execute("SELECT * FROM evaluation_baselines WHERE set_name=?", (set_name,)).fetchone()
        return dict(row) if row else None
