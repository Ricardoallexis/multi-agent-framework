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
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any, AsyncIterator

import anyio

BATCH = 500


def read_after(store, after: int, limit: int = BATCH, run_id: str | None = None) -> list[dict[str, Any]]:
    where, params = ("id>? AND run_id=?", (after, run_id)) if run_id else ("id>?", (after,))
    with store.db.connect() as conn:
        rows = conn.execute(
            f"SELECT id,run_id,ts,event_type,payload_json FROM run_events WHERE {where} ORDER BY id LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [{"id": r["id"], "run_id": r["run_id"], "ts": r["ts"], "type": r["event_type"],
             "payload": json.loads(r["payload_json"])} for r in rows]


def format_event(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {data}\n\n"


async def stream(store, *, after: int, heartbeat: float, poll: float, max_seconds: float,
                 run_id: str | None = None) -> AsyncIterator[str]:
    started = last_beat = time.monotonic()
    yield ": connected\n\n"
    cursor = after
    while True:
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
        await asyncio.sleep(poll)


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
        try:
            page = await anyio.to_thread.run_sync(read, cursor)
        except Exception as exc:  # the session vanished or is no longer readable
            code = getattr(exc, "code", "session_unavailable")
            yield format_notice("error", {"code": code, "message": getattr(exc, "message", str(exc))})
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
        await asyncio.sleep(poll)
