# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Clean Ctrl+C (GUI-T14): open SSE streams end at once when the server stops, and the stop prints no traceback."""
from __future__ import annotations

import asyncio
import signal
import socket
import time
from pathlib import Path

import pytest
import uvicorn

from multiagent import cli
from multiagent.gui import __main__ as gui_main
from multiagent.gui import events

HEARTBEAT = 20.0  # long on purpose: a stream that waited out its heartbeat or poll would miss the 1 s bound
POLL = 10.0


@pytest.fixture(autouse=True)
def stop_flag_is_clear():
    events.reset_shutdown()
    yield
    events.reset_shutdown()


@pytest.fixture
def launch(tmp_path, monkeypatch):
    """A private home and local folder, so the real launchers run up to the point where uvicorn starts."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for name, value in (("APP_HOST", "127.0.0.1"), ("LOCAL_DIR", str(tmp_path / "local")), ("WORKER_ENABLED", "false"),
                        ("GUI_TOKEN_FILE", str(home / ".multiagent" / "gui-token")), ("MOCK_MODE", "true"),
                        ("GEMINI_API_KEY", ""), ("OPENAI_API_KEY", "")):
        monkeypatch.setenv(name, value)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def collect(agen, chunks: list[str]) -> None:
    async for chunk in agen:
        chunks.append(chunk)


def no_new_events(store, after, limit, run_id):
    return []


def nothing_new(cursor):
    return {"items": [], "next_cursor": cursor, "done": True, "warnings": []}


def unchanged_children() -> dict:
    return {"version": "v1", "counts": {"active": 0, "done": 0, "total": 0}}


def run_stream(kind: str):
    if kind == "run":
        return events.stream(object(), after=0, heartbeat=HEARTBEAT, poll=POLL, max_seconds=0)
    if kind == "children":  # the subagents panel: a long interval, the answer never changes
        return events.stream_children(unchanged_children, interval=POLL, heartbeat=HEARTBEAT, max_seconds=0)
    return events.stream_session(nothing_new, after="0:0", heartbeat=HEARTBEAT, poll=POLL, max_seconds=0)


@pytest.mark.parametrize("kind", ["run", "session"])
def test_an_open_stream_ends_within_a_second_of_a_shutdown(monkeypatch, kind):
    monkeypatch.setattr(events, "read_after", no_new_events)

    async def scenario():
        chunks: list[str] = []
        task = asyncio.ensure_future(collect(run_stream(kind), chunks))
        await asyncio.sleep(0.2)  # the stream is now waiting between two polls
        assert not task.done()
        stopped_at = time.monotonic()
        events.request_shutdown()
        await asyncio.wait_for(task, timeout=5)  # never hangs: the test fails instead
        return time.monotonic() - stopped_at, chunks

    elapsed, chunks = asyncio.run(scenario())
    assert elapsed < 1.0
    assert chunks == [": connected\n\n"]


def test_an_open_children_stream_ends_within_a_second_of_a_shutdown():
    async def scenario():
        chunks: list[str] = []
        task = asyncio.ensure_future(collect(run_stream("children"), chunks))
        await asyncio.sleep(0.2)  # the connect answer went out; the stream waits 10 s between two reads
        assert not task.done()
        stopped_at = time.monotonic()
        events.request_shutdown()
        await asyncio.wait_for(task, timeout=5)  # never hangs: the test fails instead
        return time.monotonic() - stopped_at, chunks

    elapsed, chunks = asyncio.run(scenario())
    assert elapsed < 1.0
    assert chunks[0] == ": connected\n\n" and chunks[1].startswith("event: children") and len(chunks) == 2


@pytest.mark.parametrize("kind", ["run", "session", "children"])
def test_a_stream_opened_during_a_shutdown_returns_at_once(monkeypatch, kind):
    monkeypatch.setattr(events, "read_after", no_new_events)
    events.request_shutdown()
    chunks: list[str] = []
    asyncio.run(asyncio.wait_for(collect(run_stream(kind), chunks), timeout=5))
    assert chunks == [": connected\n\n"]


def test_the_run_stream_still_sends_events_and_heartbeats_without_a_shutdown(monkeypatch):
    row = {"id": 7, "run_id": "run-1", "ts": "2026-10-11T10:00:00Z", "type": "run.finished", "payload": {}}
    batches = [[row]]
    monkeypatch.setattr(events, "read_after", lambda store, after, limit, run_id: batches.pop(0) if batches else [])
    chunks: list[str] = []
    stream = events.stream(object(), after=0, heartbeat=0.05, poll=0.01, max_seconds=0.3)
    asyncio.run(asyncio.wait_for(collect(stream, chunks), timeout=20))  # ends by max_seconds, as before
    assert chunks[0] == ": connected\n\n"
    assert events.format_event(row) in chunks
    assert ": heartbeat\n\n" in chunks


def test_the_session_stream_still_follows_and_beats_without_a_shutdown():
    item = {"cursor": "0:40", "kind": "user", "text": "hola"}

    def read(cursor):
        if cursor == "0:0":
            return {"items": [item], "next_cursor": "0:40", "done": True, "warnings": []}
        return nothing_new(cursor)

    chunks: list[str] = []
    stream = events.stream_session(read, after="0:0", heartbeat=0.05, poll=0.01, max_seconds=0.3)
    asyncio.run(asyncio.wait_for(collect(stream, chunks), timeout=20))
    assert events.format_item(item) in chunks
    assert ": heartbeat\n\n" in chunks


def test_ctrl_c_stops_the_gui_without_an_exception(launch, monkeypatch, capsys):
    def interrupted(self):  # uvicorn re-raises the Ctrl+C it captured, once its own shutdown is clean
        raise KeyboardInterrupt

    monkeypatch.setattr(uvicorn.Server, "run", interrupted)
    gui_main.run(port=free_port(), no_browser=True)  # must return normally
    assert capsys.readouterr().out.splitlines()[-1] == "GUI detenida."


def test_the_gui_server_gets_a_three_second_stop_limit_and_flags_its_streams(launch, monkeypatch, capsys):
    seen: dict = {}

    def ctrl_c_then_clean_stop(self):  # the signal arrives while serving; uvicorn then returns normally
        seen["config"] = self.config
        self.handle_exit(signal.SIGINT, None)
        seen["flagged"] = events.is_shutting_down()
        seen["should_exit"] = self.should_exit

    monkeypatch.setattr(uvicorn.Server, "run", ctrl_c_then_clean_stop)
    gui_main.run(port=free_port(), no_browser=True)
    assert seen["config"].timeout_graceful_shutdown == 3
    assert seen["config"].host == "127.0.0.1"
    assert seen["flagged"] is True and seen["should_exit"] is True
    assert capsys.readouterr().out.splitlines()[-1] == "GUI detenida."


def test_ctrl_c_stops_the_stage0_ui_without_an_exception(launch, monkeypatch, capsys):
    def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(uvicorn.Server, "run", interrupted)
    args = cli.build_parser().parse_args(["ui", "--no-browser", "--port", str(free_port())])
    cli.cmd_ui(args)  # must return normally
    assert capsys.readouterr().out.splitlines()[-1] == "UI detenida."
