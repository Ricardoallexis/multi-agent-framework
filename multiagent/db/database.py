from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from ..config import Settings


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Simple SQLite access without premature repository abstractions."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Always open and close a SQLite connection.

        The native ``sqlite3.Connection`` context manager only performs
        commit/rollback; it does not close the handle. On Windows, that can keep
        the file locked and trigger ``WinError 32`` when deleting a temporary DB.
        The application uses this wrapper to guarantee handle cleanup.
        """
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def migrate(self) -> list[str]:
        applied: list[str] = []
        with self.tx() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
            existing = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
            for path in sorted(self.settings.migrations_dir.glob("*.sql")):
                prefix = path.stem.split("_", 1)[0]
                if not prefix.isdigit():
                    continue
                version = int(prefix)
                if version in existing:
                    continue
                script = path.read_text(encoding="utf-8")
                # executescript performs an implicit commit; that is acceptable here because
                # migrations are local, numbered, and applied once.
                conn.executescript(script)
                conn.execute(
                    "INSERT INTO schema_version(version,name,applied_at) VALUES(?,?,?)",
                    (version, path.name, utcnow()),
                )
                applied.append(path.name)
        return applied

    def schema_version(self) -> int:
        with self.connect() as conn:
            try:
                row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
                return int(row["v"] or 0)
            except sqlite3.OperationalError:
                return 0

    def log_event(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                (run_id, utcnow(), event_type, json.dumps(payload or {}, ensure_ascii=False)),
            )
            conn.commit()
