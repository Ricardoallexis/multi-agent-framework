from __future__ import annotations

import json
import sqlite3
import threading
import weakref
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ..config import Settings


_CLOCK_LOCK = threading.Lock()
_LAST_UTCNOW: datetime | None = None


def utcnow() -> str:
    """Keep timestamps ordered when the system clock repeats a microsecond."""
    global _LAST_UTCNOW
    with _CLOCK_LOCK:
        now = datetime.now(timezone.utc)
        if _LAST_UTCNOW is not None and now <= _LAST_UTCNOW:
            now = _LAST_UTCNOW + timedelta(microseconds=1)
        _LAST_UTCNOW = now
        return now.isoformat()


_DATABASES: weakref.WeakSet[Database] = weakref.WeakSet()
_WRITE_LOCKS: weakref.WeakValueDictionary[Path, Any] = weakref.WeakValueDictionary()
_WRITE_LOCKS_GUARD = threading.Lock()
_TX_TIMEOUT_SECONDS = 30
_POOL_LIMIT = 32


def _write_lock(path: Path) -> Any:
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(path)
        if lock is None:
            lock = threading.Lock()
            _WRITE_LOCKS[path] = lock
        return lock


class _PooledConnection(sqlite3.Connection):
    """Remember cursors so a returned handle cannot keep a WAL read snapshot."""

    def cursor(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        cursor = super().cursor(*args, **kwargs)
        self._cursors.add(cursor)
        return cursor

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any) -> sqlite3.Cursor:
        return self.cursor().executemany(sql, parameters)

    def executescript(self, script: str) -> sqlite3.Cursor:
        return self.cursor().executescript(script)

    def release_cursors(self) -> None:
        for cursor in tuple(self._cursors):
            cursor.close()
        self._cursors.clear()


class _Entry:
    def __init__(self, conn: _PooledConnection, owner: threading.Thread):
        self.conn = conn
        self.owner = weakref.ref(owner)
        self.busy = True
        self.retire = False


class _Pool:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries: list[_Entry] = []
        self.generation = 0

    def close_all(self) -> None:
        with self.lock:
            self.generation += 1
            retained = []
            for entry in self.entries:
                owner = entry.owner()
                if entry.busy and owner is not None and owner.is_alive():
                    entry.retire = True
                    retained.append(entry)
                else:
                    entry.conn.close()
            self.entries = retained


class Database:
    """Simple SQLite access without premature repository abstractions."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._pool = _Pool()
        self._finalizer = weakref.finalize(self, self._pool.close_all)
        _DATABASES.add(self)
        # Called with (run_id, event_type, payload, event_id) after a run event is stored (CAP-T21: the OpenTelemetry
        # spans follow the domain events). Best effort: whatever a listener raises never reaches the writer.
        self.event_listeners: list[Callable[[str, str, dict[str, Any], int | None], None]] = []

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Lease a thread-owned handle; nested leases use separate handles."""
        pooled = getattr(self.settings, "db_reuse_connections", True)
        if not pooled:
            self._pool.close_all()
        entry = None
        if pooled:
            owner = threading.current_thread()
            with self._pool.lock:
                for stale in tuple(self._pool.entries):
                    thread = stale.owner()
                    if thread is None or not thread.is_alive():
                        stale.conn.close()
                        self._pool.entries.remove(stale)
                for candidate in self._pool.entries:
                    if candidate.owner() is owner and not candidate.busy:
                        entry = candidate
                        entry.busy = True
                        break
                generation = self._pool.generation
        if entry is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False,
                                   factory=_PooledConnection if pooled else sqlite3.Connection)
            if pooled:
                conn._cursors = weakref.WeakSet()
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            except BaseException:
                conn.close()
                raise
            if pooled:
                entry = _Entry(conn, owner)
                with self._pool.lock:
                    entry.retire = generation != self._pool.generation or len(self._pool.entries) >= _POOL_LIMIT
                    self._pool.entries.append(entry)
        else:
            conn = entry.conn
        try:
            yield conn
        finally:
            if pooled:
                try:
                    conn.release_cursors()
                    if conn.in_transaction:
                        conn.rollback()
                finally:
                    with self._pool.lock:
                        if entry.retire:
                            conn.close()
                            self._pool.entries.remove(entry)
                        else:
                            entry.busy = False
            else:
                conn.close()

    def close(self) -> None:
        """Close idle handles and retire active ones when their leases end."""
        self._pool.close_all()

    @staticmethod
    def close_all() -> None:
        """Release handles of all live databases (used at test teardown)."""
        for database in tuple(_DATABASES):
            database.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        lock = _write_lock(self.path)
        if not lock.acquire(timeout=_TX_TIMEOUT_SECONDS):
            raise sqlite3.OperationalError("database is locked")
        try:
            with self.connect() as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    yield conn
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
        finally:
            lock.release()

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

    def log_event(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> int | None:
        data = payload or {}
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                (run_id, utcnow(), event_type, json.dumps(data, ensure_ascii=False)),
            )
            event_id = cursor.lastrowid
            conn.commit()
        for listener in tuple(self.event_listeners):
            try:
                listener(run_id, event_type, data, event_id)
            except Exception:
                pass
        return event_id
