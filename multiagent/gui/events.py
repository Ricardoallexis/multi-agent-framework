# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Run events as one multiplexed SSE stream.

Cursor: ``run_events.id`` is an AUTOINCREMENT key of the framework's own event
table, so it is already an ordered, store-wide sequence across every run. The
stream reuses it as the SSE ``id:`` (no second event store, no composite
cursor). Limitation: each bundle keeps a separate database with its own ids,
so this base stream covers the system store only; bundle runs need a per-store
cursor in a later task.

Reconnect: the client sends the last ``id`` as ``?after=`` or ``Last-Event-ID``
(the larger wins); the server replays everything after it, then follows live.

One session (GUI-T04): ``?session=<id>`` narrows the stream to one session. A framework run
(``framework:<run_id>``) keeps the ``run_events`` ids above, filtered by run. A file session
(Claude, Codex, Copilot) sends its new items as the file grows: ``id:`` is the reader's cursor
(``"0:<offset>"``), ``event: item`` carries the normalized item, and ``event: notice`` the reader's
warnings (once each), so ``Last-Event-ID`` resumes exactly where the client stopped. A session that
disappears ends the stream with ``event: error``.

Children (GUI2-T04): ``?session=<id>&only=children`` is a lighter stream for the subagents panel. It sends no
items; it sends ``event: children`` (no ``id:``) with ``{"version", "counts"}`` when it connects and again each
time a child appears, disappears or changes status, and the panel then fetches
``/gui/api/sessions/<id>/children``. Polling that route every 10 seconds is the fallback when the stream is down.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from typing import Any, AsyncIterator

import anyio

from .runs import _clean  # the same cleaning as the run view: no *_path keys, bounded text

BATCH = 500
_stopping = threading.Event()  # set when the server starts to stop; every stream then ends at once
_SLICE = 0.1  # seconds between two checks of that flag while a stream waits


def request_shutdown() -> None:
    """Ask the open streams to end, so that a stopping server does not wait for them."""
    _stopping.set()


def is_shutting_down() -> bool:
    return _stopping.is_set()


def reset_shutdown() -> None:
    """Clear the flag at startup (and in tests)."""
    _stopping.clear()


async def _pause(seconds: float, wake: asyncio.Event | None = None) -> None:
    """Sleep up to ``seconds``, returning at once when the flag is set.

    Not ``threading.Event.wait`` in a worker thread: each open stream would hold one of AnyIO's threads
    for its whole poll interval. The flag is checked every ``_SLICE`` seconds instead.
    """
    deadline = time.monotonic() + seconds
    while not _stopping.is_set():
        left = deadline - time.monotonic()
        if left <= 0:
            return
        delay = min(left, _SLICE)
        if wake is None:
            await asyncio.sleep(delay)
        else:
            try:
                await asyncio.wait_for(wake.wait(), delay)
                return
            except asyncio.TimeoutError:
                pass


def read_after(store, after: int, limit: int = BATCH, run_id: str | None = None) -> list[dict[str, Any]]:
    where, params = ("id>? AND run_id=?", (after, run_id)) if run_id else ("id>?", (after,))
    with store.db.connect() as conn:
        rows = conn.execute(
            f"SELECT id,run_id,ts,event_type,payload_json FROM run_events WHERE {where} ORDER BY id LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [{"id": r["id"], "run_id": r["run_id"], "ts": r["ts"], "type": r["event_type"],
             "payload": _clean(json.loads(r["payload_json"]))} for r in rows]


def format_event(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {data}\n\n"


async def stream(store, *, after: int, heartbeat: float, poll: float, max_seconds: float,
                 run_id: str | None = None) -> AsyncIterator[str]:
    started = last_beat = time.monotonic()
    wake = asyncio.Event()
    loop = asyncio.get_running_loop()
    def notify() -> None:
        loop.call_soon_threadsafe(wake.set)
    try:
        cancel = store.db.subscribe(notify)
    except Exception:
        cancel = lambda: None
    try:
        yield ": connected\n\n"
        cursor = after
        while True:
            if is_shutting_down():
                return
            wake.clear()
            events = await anyio.to_thread.run_sync(read_after, store, cursor, BATCH, run_id)
            for event in events:
                cursor = event["id"]
                yield format_event(event)
            now = time.monotonic()
            if events:
                last_beat = now
                if len(events) == BATCH:
                    continue
            elif now - last_beat >= heartbeat:
                last_beat = now
                yield ": heartbeat\n\n"
            if max_seconds and now - started >= max_seconds:
                return
            await _pause(poll, wake)
    finally:
        cancel()


async def stream_acp(read: Callable[[int], dict[str, Any]], *, after: int, heartbeat: float,
                     poll: float, max_seconds: float) -> AsyncIterator[str]:
    """Replay a live or saved ACP session with ordered cursors."""
    started = last_beat = time.monotonic()
    cursor = after
    was_live = False
    yield ": connected\n\n"
    while not is_shutting_down():
        try:
            page = await anyio.to_thread.run_sync(read, cursor)
        except Exception as exc:  # the session was closed (consent change, deactivation, shutdown) while followed
            code, message = getattr(exc, "code", None), getattr(exc, "message", None)
            yield format_notice("error", {"code": code if isinstance(code, str) else "session_unavailable",
                                          "message": message if isinstance(message, str)
                                          else "The session can no longer be read"})
            return
        if page.get("saved") and was_live:
            cursor = 0
            page = await anyio.to_thread.run_sync(read, cursor)
            was_live = False
        elif not page.get("saved"):
            was_live = True
        for event in page["events"]:
            cursor = event["cursor"]
            yield format_item(event)
        if not page["active"]:
            return
        now = time.monotonic()
        if page["events"]:
            last_beat = now
        elif now - last_beat >= heartbeat:
            last_beat = now
            yield ": heartbeat\n\n"
        if max_seconds and now - started >= max_seconds:
            return
        await _pause(poll)


def format_item(item: dict[str, Any]) -> str:
    data = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    return f"id: {item['cursor']}\nevent: item\ndata: {data}\n\n"


def format_notice(event: str, payload: dict[str, Any]) -> str:
    """An event without ``id:``, so it never moves the client's ``Last-Event-ID``."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


async def stream_session(read: Callable[[str], dict[str, Any]], *, after: str, heartbeat: float, poll: float,
                         max_seconds: float) -> AsyncIterator[str]:
    """Follow one file session: ``read(cursor)`` returns the reader's page (``items``, ``next_cursor``,
    ``done``, ``warnings``); it is polled until the client leaves or ``max_seconds`` passes.

    Reading at the end of the file costs one seek, so polling is cheap; a cursor that no longer fits
    (the file was rewritten) makes the reader restart from the beginning and say so in its warnings.
    """
    started = last_beat = time.monotonic()
    yield ": connected\n\n"
    cursor = after
    seen: set[str] = set()
    while True:
        if is_shutting_down():
            return
        try:
            page = await anyio.to_thread.run_sync(read, cursor)
        except Exception as exc:  # the session vanished or is no longer readable
            # Only our own errors carry a message that is safe to show; str(exc) of an OSError holds a local path.
            code, message = getattr(exc, "code", None), getattr(exc, "message", None)
            yield format_notice("error", {"code": code if isinstance(code, str) else "session_unavailable",
                                          "message": message if isinstance(message, str)
                                          else "The session can no longer be read"})
            return
        fresh = [w for w in page.get("warnings", ()) if w not in seen]
        if fresh:
            seen.update(fresh)
            yield format_notice("notice", {"warnings": fresh})
        for item in page["items"]:
            yield format_item(item)
        moved = page["next_cursor"] != cursor
        cursor = page["next_cursor"]
        now = time.monotonic()
        if page["items"]:
            last_beat = now
        elif now - last_beat >= heartbeat:
            last_beat = now
            yield ": heartbeat\n\n"
        if max_seconds and now - started >= max_seconds:
            return
        if moved and not page["done"]:
            continue  # more is already in the file
        await _pause(poll)


async def stream_children(read: Callable[[], dict[str, Any]], *, interval: float, heartbeat: float,
                          max_seconds: float) -> AsyncIterator[str]:
    """Tell the client when the children of one session changed.

    ``read()`` returns the children answer (``version`` and ``counts`` are what is sent). It runs at most
    every ``interval`` seconds; nothing else is sent between changes except heartbeats. A shutdown ends it.
    """
    started = last_beat = time.monotonic()
    yield ": connected\n\n"
    version: object = None
    while True:
        if is_shutting_down():
            return
        try:
            answer = await anyio.to_thread.run_sync(read)
        except Exception as exc:  # the session vanished or is no longer readable
            code, message = getattr(exc, "code", None), getattr(exc, "message", None)
            yield format_notice("error", {"code": code if isinstance(code, str) else "session_unavailable",
                                          "message": message if isinstance(message, str)
                                          else "The session can no longer be read"})
            return
        now = time.monotonic()
        if answer["version"] != version:
            version = answer["version"]
            last_beat = now
            yield format_notice("children", {"version": version, "counts": answer["counts"]})
        elif now - last_beat >= heartbeat:
            last_beat = now
            yield ": heartbeat\n\n"
        if max_seconds and now - started >= max_seconds:
            return
        await _pause(interval)


async def stream_project(read: Callable[[], int | None], *, after: int, interval: float, heartbeat: float,
                         max_seconds: float, database: Any = None) -> AsyncIterator[str]:
    """Send only a project's newest team-event sequence when it moves beyond ``after``.

    The reader returns ``None`` when the project is no longer available; the stream then ends quietly.
    """
    started = last_beat = time.monotonic()
    cursor = after
    wake = asyncio.Event()
    loop = asyncio.get_running_loop()
    def notify() -> None:
        loop.call_soon_threadsafe(wake.set)
    try:
        cancel = database.subscribe(notify)
    except Exception:
        cancel = lambda: None
    try:
        yield ": connected\n\n"
        while True:
            if is_shutting_down():
                return
            wake.clear()
            seq = await anyio.to_thread.run_sync(read)
            if seq is None:
                return
            now = time.monotonic()
            if seq > cursor:
                cursor = seq
                last_beat = now
                yield format_notice("project", {"seq": seq})
            elif now - last_beat >= heartbeat:
                last_beat = now
                yield ": heartbeat\n\n"
            if max_seconds and now - started >= max_seconds:
                return
            await _pause(interval, wake)
    finally:
        cancel()
