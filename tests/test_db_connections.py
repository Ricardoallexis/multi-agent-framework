# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Connection reuse must preserve SQLite transaction and Windows cleanup behavior."""
from __future__ import annotations

import gc
import sqlite3
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from multiagent.config import Settings
from multiagent.db import database as database_module
from multiagent.db.database import Database
from session_fixtures import make_services, make_stores


def make_db(tmp_path, **options):
    settings = Settings(_env_file=None, local_dir=tmp_path, db_path=tmp_path / "test.db", **options)
    return Database(settings)


def test_utcnow_orders_repeated_or_backward_clock_ticks(monkeypatch):
    ticks = iter((
        datetime(2026, 11, 27, 12, tzinfo=timezone.utc),
        datetime(2026, 11, 27, 12, tzinfo=timezone.utc),
        datetime(2026, 11, 27, 11, tzinfo=timezone.utc),
    ))

    class Clock:
        @staticmethod
        def now(zone):
            assert zone is timezone.utc
            return next(ticks)

    monkeypatch.setattr(database_module, "datetime", Clock)
    monkeypatch.setattr(database_module, "_LAST_UTCNOW", None)
    stamps = [database_module.utcnow() for _ in range(3)]
    assert stamps == [
        "2026-11-27T12:00:00+00:00",
        "2026-11-27T12:00:00.000001+00:00",
        "2026-11-27T12:00:00.000002+00:00",
    ]


def test_sequential_leases_reuse_one_handle_and_initialize_once(tmp_path, monkeypatch):
    opened, pragmas = [], []
    real_connect = sqlite3.connect

    def counted(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        conn.set_trace_callback(lambda sql: pragmas.append(sql) if sql.startswith("PRAGMA") else None)
        return conn

    monkeypatch.setattr(database_module.sqlite3, "connect", counted)
    db = make_db(tmp_path)
    with db.connect() as first:
        first.execute("SELECT 1").fetchone()
    with db.connect() as second:
        assert second is first
    assert len(opened) == 1
    assert len(pragmas) == 3


def test_nested_read_is_a_separate_committed_snapshot(tmp_path):
    db = make_db(tmp_path)
    with db.tx() as conn:
        conn.execute("CREATE TABLE items (value INTEGER)")
    with db.tx() as writer:
        writer.execute("INSERT INTO items VALUES (1)")
        with db.connect() as reader:
            assert reader is not writer
            assert reader.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    with db.connect() as reader:
        assert reader.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_uncommitted_lease_rolls_back_and_other_thread_can_write(tmp_path):
    db = make_db(tmp_path)
    with db.tx() as conn:
        conn.execute("CREATE TABLE items (value INTEGER)")
    with db.connect() as conn:
        conn.execute("INSERT INTO items VALUES (1)")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: _insert(db, 2)).result(timeout=5)
    with db.connect() as conn:
        assert [row[0] for row in conn.execute("SELECT value FROM items")] == [2]


def _insert(db, value):
    with db.tx() as conn:
        conn.execute("INSERT INTO items VALUES (?)", (value,))


def test_tx_exception_rolls_back_and_reuses_handle(tmp_path):
    db = make_db(tmp_path)
    with db.tx() as conn:
        conn.execute("CREATE TABLE items (value INTEGER)")
    with pytest.raises(ValueError):
        with db.tx() as first:
            first.execute("INSERT INTO items VALUES (1)")
            raise ValueError("abort")
    with db.tx() as second:
        assert second is first
        assert second.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_nested_tx_times_out_without_deadlock(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    monkeypatch.setattr(database_module, "_TX_TIMEOUT_SECONDS", 0.01)
    with db.tx():
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            with db.tx():
                pass


def test_close_releases_files_and_later_reopens(tmp_path):
    db = make_db(tmp_path)
    with db.tx() as conn:
        conn.execute("CREATE TABLE items (value INTEGER)")
    path = db.path
    db.close()
    for suffix in ("-shm", "-wal", ""):
        file = path.with_name(path.name + suffix)
        if file.exists():
            file.unlink()
    with db.connect() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_cross_thread_close_retires_active_lease(tmp_path):
    db = make_db(tmp_path)
    entered, proceed = threading.Event(), threading.Event()
    retained, errors = [], []

    def use():
        try:
            with db.connect() as conn:
                retained.append(conn)
                entered.set()
                assert proceed.wait(5)
                assert conn.execute("SELECT 1").fetchone()[0] == 1
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=use)
    thread.start()
    assert entered.wait(5)
    db.close()
    proceed.set()
    thread.join(5)
    assert not thread.is_alive()
    assert errors == []
    with pytest.raises(sqlite3.ProgrammingError):
        retained[0].execute("SELECT 1")


def test_dead_thread_handle_is_reaped_on_next_connect(tmp_path):
    db = make_db(tmp_path)
    retained = []

    def use():
        with db.connect() as conn:
            retained.append(conn)

    thread = threading.Thread(target=use)
    thread.start()
    thread.join(5)
    with db.connect():
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        retained[0].execute("SELECT 1")


def test_returned_cursor_cannot_keep_an_old_wal_snapshot(tmp_path):
    db = make_db(tmp_path)
    with db.tx() as conn:
        conn.execute("CREATE TABLE items (value INTEGER)")
        conn.executemany("INSERT INTO items VALUES (?)", [(1,), (2,)])
    with db.connect() as conn:
        cursor = conn.execute("SELECT value FROM items")
        assert cursor.fetchone()[0] == 1
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: _insert(db, 3)).result(timeout=5)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3
    with pytest.raises(sqlite3.ProgrammingError):
        cursor.fetchone()


def test_pool_limit_retires_extra_nested_handle(tmp_path):
    db = make_db(tmp_path)
    with ExitStack() as leases:
        for _ in range(database_module._POOL_LIMIT + 1):
            leases.enter_context(db.connect())
        assert len(db._pool.entries) == database_module._POOL_LIMIT + 1
    assert len(db._pool.entries) == database_module._POOL_LIMIT


def test_event_listener_runs_after_lease_returns(tmp_path):
    db = make_db(tmp_path)
    with db.tx() as conn:
        conn.execute("CREATE TABLE run_events (id INTEGER PRIMARY KEY, run_id TEXT, ts TEXT, event_type TEXT, payload_json TEXT)")
    with db.connect() as conn:
        original = conn
    observed = []

    def listener(*_args):
        with db.connect() as conn:
            observed.append((conn is original, conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0]))

    db.event_listeners.append(listener)
    db.log_event("run", "started")
    assert observed == [(True, 1)]


def test_eight_writers_serialize_exactly(tmp_path):
    db = make_db(tmp_path)
    with db.tx() as conn:
        conn.execute("CREATE TABLE counter (n INTEGER)")
        conn.execute("INSERT INTO counter VALUES (0)")

    def increment(_):
        for _ in range(50):
            with db.tx() as conn:
                conn.execute("UPDATE counter SET n=n+1")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(increment, range(8)))
    with db.connect() as conn:
        assert conn.execute("SELECT n FROM counter").fetchone()[0] == 400


def test_reservations_from_two_threads_admit_only_one(tmp_path):
    services = make_services(make_stores(tmp_path), usage_budgets=[
        {"id": "cap", "scope": "month", "amount": 1}])

    def reserve(_):
        with services.usage.reserving() as hold:
            if hold.reserved.get("cap", Decimal(0)):
                return False
            hold.reserve({"run_id": "synthetic", "step_id": "one", "attempt": 1,
                          "model_id": "test", "currency": "USD", "amount": "1", "budgets": ["cap"]})
            return True

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(reserve, range(2))) == [False, True]
    finally:
        services.runs.close()


def test_creation_is_lazy_and_orphaned_database_is_finalized(tmp_path, monkeypatch):
    opened = []
    real_connect = sqlite3.connect

    def counted(*args, **kwargs):
        opened.append(1)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(database_module.sqlite3, "connect", counted)
    db = make_db(tmp_path)
    assert opened == []
    with db.connect() as conn:
        retained = conn
    ref = weakref.ref(db)
    del db
    gc.collect()
    assert ref() is None
    with pytest.raises(sqlite3.ProgrammingError):
        retained.execute("SELECT 1")


def test_facade_construction_does_not_create_database(monkeypatch):
    from multiagent.services import ApplicationServices

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Database must remain lazy")

    monkeypatch.setattr(Database, "__init__", forbidden)
    monkeypatch.setattr(Settings, "__init__", forbidden)
    for name in ("exists", "is_file", "read_text", "open", "mkdir"):
        monkeypatch.setattr(Path, name, forbidden)
    ApplicationServices(object())


def test_switch_restores_fresh_handle_per_call(tmp_path, monkeypatch):
    opened = []
    real_connect = sqlite3.connect

    def counted(*args, **kwargs):
        opened.append(1)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(database_module.sqlite3, "connect", counted)
    db = make_db(tmp_path, db_reuse_connections=False)
    with db.connect() as first:
        first.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute("SELECT 1")
    with db.connect():
        pass
    assert len(opened) == 2


def test_disabling_reuse_closes_existing_idle_handles(tmp_path):
    db = make_db(tmp_path)
    with db.connect() as pooled:
        pooled.execute("SELECT 1")
    db.settings.db_reuse_connections = False
    with db.connect() as fresh:
        assert fresh is not pooled
    with pytest.raises(sqlite3.ProgrammingError):
        pooled.execute("SELECT 1")
