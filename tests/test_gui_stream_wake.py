# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Database-backed SSE follows local commits and retains its polling fallback."""
from __future__ import annotations

import asyncio
import threading

import pytest

from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.gui import events


@pytest.fixture(autouse=True)
def clear_shutdown():
    events.reset_shutdown()
    yield
    events.reset_shutdown()


def database(tmp_path):
    db = Database(Settings(_env_file=None, local_dir=tmp_path, db_path=tmp_path / "test.db"))
    with db.tx() as conn:
        conn.execute("CREATE TABLE run_events (id INTEGER PRIMARY KEY, run_id TEXT, ts TEXT, "
                     "event_type TEXT, payload_json TEXT)")
        conn.execute("CREATE TABLE project_seq (seq INTEGER)")
        conn.execute("INSERT INTO project_seq VALUES (0)")
    return db


def test_run_stream_wakes_and_unsubscribes(tmp_path, monkeypatch):
    db = database(tmp_path)
    store = type("Store", (), {"db": db})()
    read = threading.Event()
    original = events.read_after
    def observed(*args):
        result = original(*args)
        read.set()
        return result
    monkeypatch.setattr(events, "read_after", observed)

    async def scenario():
        stream = events.stream(store, after=0, poll=30, heartbeat=60, max_seconds=0)
        assert await anext(stream) == ": connected\n\n"
        next_chunk = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(read.wait, 1)
        with db.tx() as conn:
            conn.execute("INSERT INTO run_events VALUES (1, 'run', 'now', 'ready', '{}')")
        assert (await asyncio.wait_for(next_chunk, 2)).startswith("id: 1\nevent: ready")
        await stream.aclose()
        assert not db._subscribers
    asyncio.run(scenario())


def test_project_stream_wakes_and_unsubscribes(tmp_path):
    db = database(tmp_path)
    read = threading.Event()
    def latest():
        with db.connect() as conn:
            seq = conn.execute("SELECT seq FROM project_seq").fetchone()[0]
        read.set()
        return seq

    async def scenario():
        stream = events.stream_project(latest, after=0, interval=30, heartbeat=60,
                                       max_seconds=0, database=db)
        assert await anext(stream) == ": connected\n\n"
        next_chunk = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(read.wait, 1)
        with db.tx() as conn:
            conn.execute("UPDATE project_seq SET seq=1")
        assert await asyncio.wait_for(next_chunk, 2) == 'event: project\ndata: {"seq":1}\n\n'
        await stream.aclose()
        assert not db._subscribers
    asyncio.run(scenario())


@pytest.mark.parametrize("project", [False, True])
def test_poll_and_shutdown_remain_when_subscription_fails(tmp_path, monkeypatch, project):
    db = database(tmp_path)
    monkeypatch.setattr(db, "subscribe", lambda callback: (_ for _ in ()).throw(RuntimeError("off")))
    read = threading.Event()
    def latest():
        with db.connect() as conn:
            result = conn.execute("SELECT seq FROM project_seq").fetchone()[0]
        read.set()
        return result
    original = events.read_after
    def observed(*args):
        result = original(*args)
        read.set()
        return result
    monkeypatch.setattr(events, "read_after", observed)

    async def scenario():
        stream = (events.stream_project(latest, after=0, interval=0.02, heartbeat=60,
                                        max_seconds=0, database=db) if project else
                  events.stream(type("Store", (), {"db": db})(), after=0, poll=0.02,
                                heartbeat=60, max_seconds=0))
        assert await anext(stream) == ": connected\n\n"
        next_chunk = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(read.wait, 1)
        with db.tx() as conn:
            if project:
                conn.execute("UPDATE project_seq SET seq=1")
            else:
                conn.execute("INSERT INTO run_events VALUES (1, 'run', 'now', 'ready', '{}')")
        assert "event: project" in await asyncio.wait_for(next_chunk, 2) if project else \
            "event: ready" in await asyncio.wait_for(next_chunk, 2)
        events.request_shutdown()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 1)
        assert not db._subscribers
    asyncio.run(scenario())
