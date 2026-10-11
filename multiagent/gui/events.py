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
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

import anyio

BATCH = 500


def read_after(store, after: int, limit: int = BATCH) -> list[dict[str, Any]]:
    with store.db.connect() as conn:
        rows = conn.execute(
            "SELECT id,run_id,ts,event_type,payload_json FROM run_events WHERE id>? ORDER BY id LIMIT ?",
            (after, limit),
        ).fetchall()
    return [{"id": r["id"], "run_id": r["run_id"], "ts": r["ts"], "type": r["event_type"],
             "payload": json.loads(r["payload_json"])} for r in rows]


def format_event(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {data}\n\n"


async def stream(store, *, after: int, heartbeat: float, poll: float, max_seconds: float) -> AsyncIterator[str]:
    started = last_beat = time.monotonic()
    yield ": connected\n\n"
    cursor = after
    while True:
        events = await anyio.to_thread.run_sync(read_after, store, cursor)
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
