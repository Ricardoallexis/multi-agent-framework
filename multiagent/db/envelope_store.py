# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Storage of task context, task results, blobs and event cursors (migration 006).

Persistence only (ORQ-T23): no envelope rules, no reference grammar, no size or
status policy. Those live in the pure envelope validation (ORQ-T24) and in the
envelope services (ORQ-T25), which call the single task transition function.

* A task's ``base_commit``, ``context_refs`` and ``constraints`` are columns of
  ``tasks`` (the services decide when to bump the task revision).
* A blob is content addressed: its bytes live in
  ``<data_dir>/blobs/<sha[:2]>/<sha>`` and its metadata in ``blobs``. The file is
  written first (atomically) and the row second, so a row always has its file; a
  file left by a rolled-back transaction is adopted by the next ``put_blob`` of
  the same content.
* A result is stored as the canonical JSON the caller built, with its sha256;
  ``(task_id, attempt, patch_sha256)`` is unique (``patch_sha256`` is ``""`` for
  a result without a patch), which is the idempotency key.
* A cursor is ``seq`` per ``(consumer, scope)`` and never moves backwards.

Writes take the caller's connection (see ``TaskStore``) so that a result, its
events and the state change commit together; reads open their own connection
unless one is given. Rows come back as plain dicts with the JSON columns decoded.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .database import Database, utcnow

# Protects memory: the services apply the (smaller, configurable) limit of the contract.
HARD_MAX_BLOB_BYTES = 64 * 1024 * 1024


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _result(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["result"] = json.loads(item.pop("result_json"))
    return item


class EnvelopeStore:
    def __init__(self, db: Database):
        self.db = db
        self.blobs_dir = Path(db.settings.data_dir) / "blobs"

    @contextmanager
    def _reader(self, conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
        else:
            with self.db.connect() as own:
                yield own

    # -------- task context --------
    def set_task_context(self, conn: sqlite3.Connection, task_id: str, *, base_commit: str | None = None,
                         context_refs: Iterable[str] | None = None,
                         constraints: Iterable[str] | None = None) -> bool:
        """Store the given context fields; ``False`` when the task does not exist. Does not bump the revision."""
        changes: dict[str, Any] = {}
        if base_commit is not None:
            changes["base_commit"] = base_commit
        if context_refs is not None:
            changes["context_refs_json"] = _dumps(list(context_refs))
        if constraints is not None:
            changes["constraints_json"] = _dumps(list(constraints))
        changes["updated_at"] = utcnow()
        assignments = ",".join(f"{column}=?" for column in changes)
        cursor = conn.execute(f"UPDATE tasks SET {assignments} WHERE id=?", (*changes.values(), task_id))
        return bool(cursor.rowcount)

    def task_context(self, task_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        """``{base_commit, context_refs, constraints}`` of a task, or ``None`` when it does not exist."""
        with self._reader(conn) as c:
            row = c.execute("SELECT base_commit, context_refs_json, constraints_json FROM tasks WHERE id=?",
                            (task_id,)).fetchone()
        if row is None:
            return None
        return {"base_commit": row["base_commit"], "context_refs": json.loads(row["context_refs_json"]),
                "constraints": json.loads(row["constraints_json"])}

    # -------- blobs --------
    def blob_path(self, sha256: str) -> Path:
        return self.blobs_dir / sha256[:2] / sha256

    def put_blob(self, conn: sqlite3.Connection, content: bytes, *, actor: str,
                 media_type: str = "application/octet-stream") -> tuple[dict[str, Any], bool]:
        """Store content by its sha256 and return ``(row, created)``; ``created`` is false when the blob already existed.

        ``ValueError`` when the content is over ``HARD_MAX_BLOB_BYTES``.
        """
        if len(content) > HARD_MAX_BLOB_BYTES:
            raise ValueError(f"Blob exceeds {HARD_MAX_BLOB_BYTES} bytes")
        sha256 = hashlib.sha256(content).hexdigest()
        existing = self.get_blob(sha256, conn=conn)
        if existing is not None and self.blob_path(sha256).is_file():
            return existing, False
        self._write_file(sha256, content)
        conn.execute(
            "INSERT OR IGNORE INTO blobs(sha256,size,media_type,created_by,created_at) VALUES(?,?,?,?,?)",
            (sha256, len(content), media_type, actor, utcnow()),
        )
        return self.get_blob(sha256, conn=conn), existing is None  # type: ignore[return-value]

    def _write_file(self, sha256: str, content: bytes) -> None:
        target = self.blob_path(sha256)
        if target.is_file() and target.stat().st_size == len(content):
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".blob-")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(content)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def get_blob(self, sha256: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._reader(conn) as c:
            row = c.execute("SELECT * FROM blobs WHERE sha256=?", (sha256,)).fetchone()
        return dict(row) if row else None

    def read_blob(self, sha256: str, *, offset: int = 0, length: int | None = None) -> bytes:
        """Bytes of a blob from ``offset`` (at most ``length``, or to the end). ``FileNotFoundError`` if its file is gone."""
        with self.blob_path(sha256).open("rb") as source:
            source.seek(offset)
            return source.read() if length is None else source.read(length)

    # -------- results --------
    def insert_result(self, conn: sqlite3.Connection, *, task_id: str, attempt: int, agent: str,
                      result_json: str, result_sha256: str, patch_sha256: str = "") -> dict[str, Any]:
        """Store one result; ``result_json`` is the canonical JSON the caller built and ``result_sha256`` its sha256.

        ``ValueError`` when the sha256 is not the one of ``result_json``;
        ``sqlite3.IntegrityError`` for a repeated (task, attempt, patch) or an unknown task.
        """
        if hashlib.sha256(result_json.encode("utf-8")).hexdigest() != result_sha256:
            raise ValueError("result_sha256 does not match result_json")
        cursor = conn.execute(
            "INSERT INTO task_results(task_id,attempt,patch_sha256,agent,result_json,result_sha256,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (task_id, attempt, patch_sha256, agent, result_json, result_sha256, utcnow()),
        )
        row = conn.execute("SELECT * FROM task_results WHERE id=?", (cursor.lastrowid,)).fetchone()
        return _result(row)

    def find_result(self, task_id: str, attempt: int, patch_sha256: str = "", *,
                    conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        """The result with this idempotency key, or ``None``."""
        with self._reader(conn) as c:
            row = c.execute("SELECT * FROM task_results WHERE task_id=? AND attempt=? AND patch_sha256=?",
                            (task_id, attempt, patch_sha256)).fetchone()
        return _result(row) if row else None

    def results(self, task_id: str, *, attempt: int | None = None,
                conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        """Results of a task (optionally one attempt), most recent attempt first, then most recent stored."""
        sql = "SELECT * FROM task_results WHERE task_id=?"
        params: list[Any] = [task_id]
        if attempt is not None:
            sql += " AND attempt=?"
            params.append(attempt)
        sql += " ORDER BY attempt DESC, id DESC"
        with self._reader(conn) as c:
            return [_result(row) for row in c.execute(sql, params)]

    def latest_result(self, task_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        found = self.results(task_id, conn=conn)
        return found[0] if found else None

    # -------- cursors --------
    def get_cursor(self, consumer: str, scope: str = "", *, conn: sqlite3.Connection | None = None) -> int:
        """The consumer's cursor in this scope; 0 when it has none."""
        with self._reader(conn) as c:
            row = c.execute("SELECT seq FROM event_cursors WHERE consumer=? AND scope=?", (consumer, scope)).fetchone()
        return int(row[0]) if row else 0

    def set_cursor(self, conn: sqlite3.Connection, consumer: str, scope: str, seq: int) -> int:
        """Move the cursor to ``seq`` and return the resulting value; it never moves backwards."""
        conn.execute(
            "INSERT INTO event_cursors(consumer,scope,seq,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(consumer,scope) DO UPDATE SET seq=MAX(seq,excluded.seq), updated_at=excluded.updated_at",
            (consumer, scope, seq, utcnow()),
        )
        return self.get_cursor(consumer, scope, conn=conn)

    def event_head(self, *, conn: sqlite3.Connection | None = None) -> int:
        """The highest ``seq`` of the team event log; 0 when it is empty."""
        with self._reader(conn) as c:
            row = c.execute("SELECT COALESCE(MAX(seq),0) FROM team_events").fetchone()
        return int(row[0])