# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Session index (GUI2-T10): bounded per-file cache, incremental JSONL reading, listing TTL and the counts route.

Everything runs on synthetic stores in ``tmp_path``; no real folder is read. Bytes read are counted through
the ``on_read`` probe of ``MetaIndex`` (never by timing), so the tests do not depend on the speed of the machine.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient
from session_fixtures import (
    CLAUDE_ID,
    append,
    claude_user,
    jsonl,
    make_services,
    make_stores,
    write_claude,
    write_codex,
    write_copilot,
)

from multiagent.api import create_app
from multiagent.gui.token import ensure_token
from multiagent.sessions import _common
from multiagent.sessions._common import ANCHOR_BYTES, HEAD_BYTES, JsonlFold, MetaIndex
from multiagent.sessions.claude import ClaudeReader
from multiagent.sessions.codex import _META_KEYS, CodexReader
from multiagent.sessions.config import SessionsConfig
from multiagent.sessions.copilot import CopilotReader
from multiagent.sessions.models import SessionFilters
from multiagent.sessions.registry import SessionRegistry

LONG_AGO = "2026-01-05T10:00:00Z"  # far from "now", so no session counts as active
MAX_VERIFY = 2 * (HEAD_BYTES + ANCHOR_BYTES)  # a refresh hashes the head and the bytes before the old end, twice


class Reads:
    """``on_read`` probe: bytes read per ``(file name, phase)``."""

    def __init__(self) -> None:
        self.bytes: dict[tuple[str, str], int] = defaultdict(int)

    def __call__(self, path, nbytes: int, phase: str) -> None:
        self.bytes[(path.name, phase)] += nbytes

    def of(self, name: str, phase: str = "records") -> int:
        return self.bytes.get((name, phase), 0)

    def reset(self) -> None:
        self.bytes.clear()


def config_for(stores, *sources: str) -> SessionsConfig:
    """All four stores inside ``tmp`` (the default Desktop folder would be the user's real one)."""
    return SessionsConfig(
        allowed_roots=(str(stores.roots),), sources=sources or ("claude", "codex", "copilot"),
        claude_home=stores.claude, codex_home=stores.codex, copilot_home=stores.copilot,
        claude_desktop_dir=stores.desktop)


def registry_for(stores, *sources: str, **kwargs) -> SessionRegistry:
    kwargs.setdefault("scan_ttl", 0)  # every call lists again, so only the per-file index is under test
    return SessionRegistry(config_for(stores, *sources), **kwargs)


def only_row(registry: SessionRegistry):
    rows, _ = registry.find_sessions()
    assert len(rows) == 1
    return rows[0]


def assistant_line(message_id: str, tokens: int, when: str) -> dict:
    return {"type": "assistant", "timestamp": when, "message": {
        "id": message_id, "role": "assistant", "model": "claude-test", "content": [{"type": "text", "text": "more"}],
        "usage": {"input_tokens": 1, "output_tokens": tokens}}}


def bulky_claude(stores, lines: int = 120):
    """A Claude transcript of ``3 + lines`` records (about 20 KB with the default)."""
    path = write_claude(stores)
    append(path, [assistant_line(f"b{number}", 1, LONG_AGO) for number in range(lines)])
    return path


def encoded_size(*records: dict) -> int:
    return sum(len(json.dumps(record).encode()) + 1 for record in records)


# --- the per-file index ------------------------------------------------------------------------------------


class _CountFold(JsonlFold):
    """The smallest fold: how many good records and how many bad lines."""

    def __init__(self) -> None:
        self.ok = self.bad = 0

    def copy(self):
        other = _CountFold()
        other.ok, other.bad = self.ok, self.bad
        return other

    def add(self, rec, status):
        if status == "ok":
            self.ok += 1
        else:
            self.bad += 1

    def result(self):
        return (self.ok, self.bad)


def test_metadata_cache_hits_invalidates_and_evicts(tmp_path):
    path = tmp_path / "summary.jsonl"
    path.write_text("one", encoding="utf-8")
    index = MetaIndex(max_entries=2)
    calls = []

    def build():
        calls.append(1)
        return path.read_text(encoding="utf-8")

    assert index.get("row", path, build) == "one"
    assert index.get("row", path, build) == "one" and len(calls) == 1
    path.write_text("changed", encoding="utf-8")
    assert index.get("row", path, build) == "changed" and len(calls) == 2
    other = tmp_path / "other"
    other.write_text("x", encoding="utf-8")
    index.get("row", other, lambda: "x")
    last = tmp_path / "last"
    last.write_text("y", encoding="utf-8")
    index.get("row", last, lambda: "y")
    assert len(index._data) <= 2
    # the least recently used entry went first: the changed file has to be built again
    assert index.get("row", path, build) == "changed" and len(calls) == 3


def test_metadata_cache_is_bounded_by_default_and_keeps_one_entry_per_file(tmp_path):
    assert MetaIndex()._max_entries == 5000
    path = tmp_path / "f"
    index = MetaIndex(max_entries=10)
    for number in range(5):
        path.write_text("x" * (number + 1), encoding="utf-8")
        index.get("row", path, lambda number=number: number)
    assert len(index._data) == 1  # a changed file replaces its old entry; it does not pile up
    path.unlink()
    assert index.peek("row", path) is None  # a file that is gone has no current entry


def test_cache_build_does_not_hold_lock(tmp_path):
    path = tmp_path / "slow"
    path.write_text("x", encoding="utf-8")
    index = MetaIndex()
    entered, polled = threading.Event(), threading.Event()

    def slow():
        entered.set()
        time.sleep(0.1)
        return "done"

    worker = threading.Thread(target=lambda: index.get("row", path, slow))
    worker.start()
    assert entered.wait(1)
    with index._lock:
        polled.set()
    worker.join(1)
    assert polled.is_set() and not worker.is_alive()


def test_jsonl_scan_does_not_hold_the_lock_while_reading(tmp_path):
    path = jsonl(tmp_path / "t.jsonl", [{"type": "user"}] * 5)
    entered, polled = threading.Event(), threading.Event()

    def probe(_path, _nbytes, phase):
        if phase == "records":
            entered.set()
            time.sleep(0.05)

    index = MetaIndex(on_read=probe)
    worker = threading.Thread(target=lambda: index.scan_jsonl("claude", path, _CountFold))
    worker.start()
    assert entered.wait(1)
    with index._lock:
        polled.set()
    worker.join(3)
    assert polled.is_set() and not worker.is_alive()


def test_scan_jsonl_continues_after_an_append_and_not_after_a_rewrite(tmp_path):
    path = jsonl(tmp_path / "t.jsonl", [{"n": number} for number in range(40)])
    reads = Reads()
    index = MetaIndex(on_read=reads)
    assert index.scan_jsonl("k", path, _CountFold) == (40, 0)
    assert reads.of("t.jsonl") == path.stat().st_size
    reads.reset()
    assert index.scan_jsonl("k", path, _CountFold) == (40, 0) and not reads.bytes  # unchanged: nothing read
    append(path, [{"n": 41}, "not json"])
    assert index.scan_jsonl("k", path, _CountFold) == (41, 1)
    assert reads.of("t.jsonl") == len(b'{"n": 41}\n') + len(b"not json\n")
    reads.reset()
    jsonl(path, [{"n": number, "pad": "y" * 30} for number in range(10)])  # a different, larger file
    assert index.scan_jsonl("k", path, _CountFold) == (10, 0)
    assert reads.of("t.jsonl") == path.stat().st_size


def test_unreadable_file_is_reported_not_raised(tmp_path):
    assert MetaIndex().scan_jsonl("k", tmp_path / "gone.jsonl", _CountFold) == (0, 1)  # one "unavailable" line


# --- incremental reading is equal to a full read ---------------------------------------------------------------

_T = "2026-01-05T10:00:%02dZ"
CLAUDE_RECORDS = [
    {"type": "user", "timestamp": _T % 0, "cwd": "/w", "entrypoint": "sdk-cli", "version": "2.0.1",
     "message": {"role": "user", "content": "hi"}},
    {"type": "assistant", "timestamp": _T % 1, "message": {
        "id": "m1", "role": "assistant", "model": "claude-a", "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 10, "output_tokens": 5}}},
    "this line is not json",
    {"type": "mystery", "timestamp": _T % 2},
    {"type": "assistant", "timestamp": _T % 3, "message": {
        "id": "m1", "role": "assistant", "model": "claude-a", "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 12, "output_tokens": 7, "cache_read_input_tokens": 3}}},
    {"type": "custom-title", "customTitle": "First title"},
    {"type": "assistant", "timestamp": _T % 4, "message": {
        "role": "assistant", "model": "claude-b", "content": [{"type": "text", "text": "x"}],
        "usage": {"input_tokens": 1}}},
    {"type": "custom-title", "customTitle": "Final title"},
    {"type": "agent-name", "agentName": "worker"},
]
CLAUDE_CHILD_RECORDS = [
    {"type": "user", "timestamp": _T % 0, "message": {"role": "user", "content": [{"type": "text", "text": "go"}]}},
    {"type": "assistant", "timestamp": _T % 1, "message": {
        "id": "a1", "model": "claude-a", "stop_reason": "tool_use", "usage": {"input_tokens": 3, "output_tokens": 2},
        "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q"}}]}},
    {"type": "user", "timestamp": _T % 2, "message": {
        "role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
    {"type": "assistant", "timestamp": _T % 3, "message": {
        "id": "a2", "model": "claude-b", "stop_reason": "tool_use", "usage": {"input_tokens": 4},
        "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pytest tests"}}]}},
    {"type": "assistant", "timestamp": _T % 4, "message": {
        "id": "a3", "model": "claude-b", "stop_reason": "end_turn", "usage": {"output_tokens": 9},
        "content": [{"type": "text", "text": "done"}]}},
]
CODEX_RECORDS = [
    {"timestamp": _T % 0, "type": "session_meta", "payload": {
        "id": "x", "cwd": "/w", "originator": "codex_cli_rs", "instructions": "base instructions " * 40}},
    {"timestamp": _T % 1, "type": "turn_context", "payload": {"model": "gpt-a"}},
    {"timestamp": _T % 2, "type": "event_msg", "payload": {"type": "task_started"}},
    {"timestamp": _T % 3, "type": "response_item", "payload": {
        "type": "function_call", "call_id": "c1", "name": "shell", "arguments": "{\"command\": \"pytest -q\"}"}},
    {"timestamp": _T % 4, "type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}}}},
    "broken json {",
    {"timestamp": _T % 5, "type": "mystery", "payload": {}},
    {"timestamp": _T % 6, "type": "response_item", "payload": {
        "type": "function_call_output", "call_id": "c1", "output": "ok"}},
    {"timestamp": _T % 7, "type": "turn_context", "payload": {"model": "gpt-b"}},
    {"timestamp": _T % 8, "type": "event_msg", "payload": {"type": "task_complete"}},
    {"timestamp": _T % 9, "type": "session_meta", "payload": {"cwd": "/other"}},
]
COPILOT_RECORDS = [
    {"type": "session.start", "timestamp": _T % 0, "data": {"copilotVersion": "1.0", "selectedModel": "gpt-c"}},
    {"type": "user.message", "timestamp": _T % 1, "data": {"content": "hi"}},
    {"type": "subagent.started", "timestamp": _T % 2, "data": {
        "toolCallId": "tc1", "agentName": "explorer", "agentDisplayName": "Explore", "model": "gpt-d"}},
    {"type": "tool.execution_start", "timestamp": _T % 3, "data": {
        "toolCallId": "x1", "parentToolCallId": "tc1", "arguments": {"command": "pytest"}}},
    {"type": "session.usage_record", "timestamp": _T % 4, "data": {"inputTokens": 4, "outputTokens": 1}},
    {"type": "weird.event", "timestamp": _T % 5, "data": {}},
    {"type": "tool.execution_complete", "timestamp": _T % 6, "data": {"toolCallId": "x1", "parentToolCallId": "tc1"}},
    {"type": "subagent.completed", "timestamp": _T % 7, "data": {"toolCallId": "tc1", "totalTokens": 99}},
    {"type": "session.usage_record", "timestamp": _T % 8, "data": {"inputTokens": 6}},
    {"type": "assistant.message", "timestamp": _T % 9, "data": {"model": "gpt-e", "content": "x"}},
    {"type": "subagent.started", "timestamp": _T % 10, "data": {"agentId": "ag2"}},
]
SCANS = [
    ("claude-session", ClaudeReader, "_scan", CLAUDE_RECORDS),
    ("claude-child", ClaudeReader, "_child_scan", CLAUDE_CHILD_RECORDS),
    ("codex-rollout", CodexReader, "_scan", CODEX_RECORDS),
    ("codex-child", CodexReader, "_child_scan", CODEX_RECORDS),
    ("copilot-events", CopilotReader, "_scan", COPILOT_RECORDS),
    ("copilot-children", CopilotReader, "_children_scan", COPILOT_RECORDS),
]


@pytest.mark.parametrize("reader_class, method, records", [s[1:] for s in SCANS], ids=[s[0] for s in SCANS])
def test_incremental_read_equals_a_full_read_at_every_step(tmp_path, reader_class, method, records):
    """Append line by line, and half a line at a time: the continued result is the one a new index computes."""
    config = config_for(make_stores(tmp_path))
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b"")
    reads = Reads()
    persistent = getattr(reader_class(config, MetaIndex(on_read=reads)), method)
    for record in records:
        raw = (record if isinstance(record, str) else json.dumps(record)).encode() + b"\n"
        cut = len(raw) // 2
        for chunk in (raw[:cut], raw[cut:]):  # the first half has no newline: a line still being written
            with open(path, "ab") as handle:
                handle.write(chunk)
            fresh = getattr(reader_class(config, MetaIndex()), method)
            assert persistent(path) == fresh(path)
    # one pass plus the half lines read twice; rescanning at each of the 2n steps would read ~n times the file
    assert reads.of("transcript.jsonl") <= 2 * path.stat().st_size


def test_incremental_read_keeps_oversized_lines_in_step_with_a_full_read(tmp_path):
    config = config_for(make_stores(tmp_path))
    path = jsonl(tmp_path / "t.jsonl", CLAUDE_RECORDS[:3])
    reader = ClaudeReader(config, MetaIndex())
    reader._scan(path)
    huge = b'{"type": "user", "pad": "' + b"x" * (_common.MAX_LINE_BYTES + 10) + b'"}\n'
    with open(path, "ab") as handle:
        handle.write(huge)
    append(path, [CLAUDE_RECORDS[4]])
    info = reader._scan(path)
    assert info == ClaudeReader(config, MetaIndex())._scan(path)
    assert "oversized_lines_skipped: 1" in info["warnings"]


def test_a_cached_result_is_never_changed_by_a_later_append(tmp_path):
    """Results are shared with other threads: continuing a scan works on a copy."""
    path = jsonl(tmp_path / "t.jsonl", CLAUDE_RECORDS[:2])
    reader = ClaudeReader(config_for(make_stores(tmp_path)), MetaIndex())
    before = reader._scan(path)
    snapshot = json.dumps(before, default=str, sort_keys=True)
    append(path, CLAUDE_RECORDS[2:])
    after = reader._scan(path)
    assert after is not before
    assert json.dumps(before, default=str, sort_keys=True) == snapshot
    assert after["usage"] != before["usage"]


def test_a_file_that_changes_while_it_is_read_is_not_continued(tmp_path):
    config = config_for(make_stores(tmp_path))
    path = jsonl(tmp_path / "t.jsonl", CLAUDE_RECORDS[:2])
    reads, fired = Reads(), []

    def probe(file, nbytes, phase):
        reads(file, nbytes, phase)
        if phase == "records" and not fired:
            fired.append(True)
            append(path, [assistant_line("during", 4, "2026-01-06T12:00:00Z")])

    reader = ClaudeReader(config, MetaIndex(on_read=probe))
    reader._scan(path)  # the write lands in the middle of this read
    reads.reset()
    info = reader._scan(path)
    assert reads.of("t.jsonl") == path.stat().st_size  # not trusted as a resume point: read whole
    assert info == ClaudeReader(config, MetaIndex())._scan(path)
    reads.reset()
    reader._scan(path)
    assert not reads.bytes  # and now it is a normal, cached entry
    append(path, [assistant_line("after", 1, "2026-01-06T12:01:00Z")])
    reads.reset()
    reader._scan(path)
    assert reads.of("t.jsonl") == encoded_size(assistant_line("after", 1, "2026-01-06T12:01:00Z"))


def test_codex_cache_keeps_only_the_session_meta_a_row_uses(tmp_path):
    path = jsonl(tmp_path / "rollout.jsonl", CODEX_RECORDS[:2])
    info = CodexReader(config_for(make_stores(tmp_path)), MetaIndex())._scan(path)
    assert set(info["meta"]) <= set(_META_KEYS) and info["meta"]["originator"] == "codex_cli_rs"
    assert "instructions" not in json.dumps(info)


# --- what a listing reads --------------------------------------------------------------------------------------


def test_append_reads_only_the_new_bytes_of_a_claude_transcript(tmp_path):
    stores = make_stores(tmp_path)
    path = bulky_claude(stores)
    reads = Reads()
    registry = registry_for(stores, "claude", on_read=reads)
    first = only_row(registry)
    assert reads.of(path.name) == path.stat().st_size and path.stat().st_size > 10_000
    reads.reset()
    only_row(registry)
    assert not reads.bytes  # unchanged since the last listing: not a single byte
    line = assistant_line("late", 7, "2026-01-06T11:00:00Z")
    append(path, [line])
    row = only_row(registry)
    assert reads.of(path.name) == encoded_size(line)  # the new line and nothing else
    assert reads.of(path.name, "verify") <= MAX_VERIFY
    assert row.updated_at == "2026-01-06T11:00:00Z"
    assert row.tokens.output == first.tokens.output + 7
    reads.reset()
    only_row(registry)
    assert not reads.bytes


@pytest.mark.parametrize("write, source, pad, record, check", [
    (write_codex, "codex",
     {"timestamp": LONG_AGO, "type": "response_item", "payload": {
         "type": "message", "role": "user", "content": [{"type": "input_text", "text": "pad " * 20}]}},
     {"timestamp": "2026-01-06T11:00:00Z", "type": "event_msg", "payload": {
         "type": "token_count", "info": {"total_token_usage": {"input_tokens": 9, "total_tokens": 10}}}},
     lambda row: row.tokens.total == 10),
    (write_copilot, "copilot",
     {"type": "user.message", "timestamp": LONG_AGO, "data": {"content": "pad " * 20}},
     {"type": "session.usage_record", "timestamp": "2026-01-06T11:00:00Z", "data": {"inputTokens": 9}},
     lambda row: row.tokens.input == 9),
], ids=["codex", "copilot"])
def test_append_reads_only_the_new_bytes_of_codex_and_copilot(tmp_path, write, source, pad, record, check):
    stores = make_stores(tmp_path)
    path = write(stores)
    append(path, [pad] * 80)
    reads = Reads()
    registry = registry_for(stores, source, on_read=reads)
    only_row(registry)
    assert reads.of(path.name) == path.stat().st_size
    reads.reset()
    only_row(registry)
    assert not reads.bytes
    append(path, [record])
    row = only_row(registry)
    assert reads.of(path.name) == encoded_size(record)
    assert reads.of(path.name, "verify") <= MAX_VERIFY
    assert check(row)


def test_a_line_still_being_written_is_read_again_from_its_start(tmp_path):
    stores = make_stores(tmp_path)
    path = bulky_claude(stores, lines=20)
    reads = Reads()
    registry = registry_for(stores, "claude", on_read=reads)
    only_row(registry)
    line = json.dumps(assistant_line("slow", 3, "2026-01-06T11:00:00Z")).encode() + b"\n"
    half = len(line) // 2
    with open(path, "ab") as handle:
        handle.write(line[:half])
    reads.reset()
    assert only_row(registry).updated_at != "2026-01-06T11:00:00Z"  # not a record yet
    assert reads.of(path.name) == half
    with open(path, "ab") as handle:
        handle.write(line[half:])
    reads.reset()
    assert only_row(registry).updated_at == "2026-01-06T11:00:00Z"
    assert reads.of(path.name) == len(line)  # the whole line, from where it started: nothing lost or doubled


def test_a_rewritten_file_is_read_whole(tmp_path):
    stores = make_stores(tmp_path)
    path = bulky_claude(stores, lines=30)
    reads = Reads()
    registry = registry_for(stores, "claude", on_read=reads)
    only_row(registry)
    old_size = path.stat().st_size
    # another, larger transcript under the same name: the file grew, but its first bytes are not the old ones
    jsonl(path, [claude_user("brand new", "2026-01-07T09:00:00Z", str(stores.work))]
          + [assistant_line(f"n{number}", 2, "2026-01-07T09:00:01Z") for number in range(60)])
    assert path.stat().st_size > old_size
    reads.reset()
    row = only_row(registry)
    assert reads.of(path.name) == path.stat().st_size
    assert row.created_at == "2026-01-07T09:00:00Z" and row.tokens.output == 120


def test_a_rewrite_that_keeps_the_head_but_not_the_bytes_before_the_old_end_is_read_whole(tmp_path):
    stores = make_stores(tmp_path)
    path = bulky_claude(stores, lines=60)
    reads = Reads()
    registry = registry_for(stores, "claude", on_read=reads)
    only_row(registry)
    old_size = path.stat().st_size
    kept = b"\n".join(path.read_bytes().split(b"\n")[:43]) + b"\n"  # the first 43 lines stay as they were
    assert len(kept) > HEAD_BYTES
    rewritten = [assistant_line(f"c{number}", 5, "2026-01-07T09:00:01Z") for number in range(60)]
    path.write_bytes(kept + b"".join(json.dumps(r).encode() + b"\n" for r in rewritten))
    assert path.stat().st_size > old_size
    reads.reset()
    row = only_row(registry)
    assert reads.of(path.name) == path.stat().st_size  # the head matched, the bytes before the old end did not
    assert row == only_row(registry_for(stores, "claude"))  # the same row a brand new index gives


def test_a_truncated_file_is_read_whole_and_an_empty_one_survives(tmp_path):
    stores = make_stores(tmp_path)
    path = bulky_claude(stores, lines=60)
    reads = Reads()
    registry = registry_for(stores, "claude", on_read=reads)
    full = only_row(registry)
    data = path.read_bytes()
    short = b"\n".join(data.split(b"\n")[:20]) + b"\n"
    path.write_bytes(short)  # shorter: the end of the conversation is gone
    reads.reset()
    row = only_row(registry)
    assert reads.of(path.name) == len(short)
    assert row.tokens.output < full.tokens.output
    assert row == only_row(registry_for(stores, "claude"))
    shorter = data[: len(short) // 2]  # cut in the middle of a line
    path.write_bytes(shorter)
    reads.reset()
    row = only_row(registry)
    assert reads.of(path.name) == len(shorter)
    assert row == only_row(registry_for(stores, "claude"))
    path.write_bytes(b"")  # nothing left: still a transcript, with no content and the times of the file
    summaries, _ = registry._readers["claude"].list_summaries()
    assert len(summaries) == 1 and summaries[0].tokens is None and summaries[0].models == []
    assert summaries[0].created_at is not None and summaries[0].cwd is None
    path.write_bytes(data)  # and it can come back as it was
    assert only_row(registry) == only_row(registry_for(stores, "claude"))


def test_a_touched_file_of_the_same_size_is_read_whole(tmp_path):
    stores = make_stores(tmp_path)
    path = bulky_claude(stores, lines=10)
    reads = Reads()
    registry = registry_for(stores, "claude", on_read=reads)
    only_row(registry)
    reads.reset()
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    only_row(registry)
    assert reads.of(path.name) == stat.st_size


def test_concurrent_listings_while_a_transcript_grows_stay_consistent(tmp_path):
    stores = make_stores(tmp_path)
    path = bulky_claude(stores, lines=10)
    registry = registry_for(stores, "claude")
    errors: list[BaseException] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            while not stop.is_set():
                assert only_row(registry).tokens is not None
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for number in range(40):
        append(path, [assistant_line(f"g{number}", 1, LONG_AGO)])
        time.sleep(0.002)
    stop.set()
    for thread in threads:
        thread.join(10)
    assert not errors
    assert only_row(registry) == only_row(registry_for(stores, "claude"))


def test_children_are_read_once_and_then_only_their_appended_bytes(tmp_path):
    stores = make_stores(tmp_path)
    main = write_claude(stores)
    sub = jsonl(main.parent / main.stem / "subagents" / "agent-aaa.jsonl", CLAUDE_CHILD_RECORDS[:3])
    meta = sub.with_suffix(".meta.json")
    meta.write_text(json.dumps({"agentType": "explorer", "description": "Looks around", "model": "claude-a"}),
                    encoding="utf-8")
    reads = Reads()
    registry = registry_for(stores, "claude", on_read=reads)
    children, _ = registry.list_children(f"claude:{CLAUDE_ID}")
    assert [c.name for c in children] == ["explorer"]
    assert registry._index.peek("claude-child-meta", meta) is not None  # the small meta file is cached too
    reads.reset()
    again, _ = registry.list_children(f"claude:{CLAUDE_ID}")
    assert [c.to_dict() for c in again] == [c.to_dict() for c in children]
    assert not reads.of(sub.name)  # the child's transcript is not read again
    append(sub, CLAUDE_CHILD_RECORDS[3:5])
    reads.reset()
    registry.list_children(f"claude:{CLAUDE_ID}")
    assert reads.of(sub.name) == encoded_size(*CLAUDE_CHILD_RECORDS[3:5])


def test_a_subagent_borrows_the_working_folder_by_reading_only_the_head_of_its_parent(tmp_path, monkeypatch):
    from multiagent.sessions import claude as claude_module

    stores = make_stores(tmp_path)
    main = write_claude(stores)
    append(main, [assistant_line(f"b{number}", 1, LONG_AGO) for number in range(200)])
    jsonl(main.parent / main.stem / "subagents" / "agent-bbb.jsonl", [
        {"type": "user", "timestamp": LONG_AGO, "message": {"role": "user", "content": "go"}}])
    seen: list[str] = []
    original = claude_module.iter_records

    def counting(path, *args, **kwargs):
        for item in original(path, *args, **kwargs):
            seen.append(path.name)
            yield item

    monkeypatch.setattr(claude_module, "iter_records", counting)
    registry = registry_for(stores, "claude")
    summary = registry.get_session(f"claude:{CLAUDE_ID}:agent-bbb")
    assert summary.cwd == str(stores.work)
    assert seen.count(main.name) == 1  # the parent was read up to its first record with a folder, then stopped
    # and the answer is the one the listing gives, which reads the parent whole
    rows, _ = registry.find_sessions()
    assert next(r for r in rows if r.id == summary.id).cwd == summary.cwd


# --- the listing cache --------------------------------------------------------------------------------------


def test_directory_cache_expires_with_injected_clock(tmp_path):
    stores = make_stores(tmp_path)
    write_claude(stores)
    now = [0.0]
    registry = registry_for(stores, "claude", scan_ttl=2.0, clock=lambda: now[0])
    reader = registry._readers["claude"]
    calls = []
    original = reader.list_summaries

    def counted():
        calls.append(1)
        return original()

    reader.list_summaries = counted
    registry.list_sessions()
    registry.list_sessions()
    assert len(calls) == 1
    now[0] = 1.9
    registry.list_sessions()
    assert len(calls) == 1
    now[0] = 2.1
    registry.list_sessions()
    assert len(calls) == 2
    registry.invalidate()
    registry.list_sessions()
    assert len(calls) == 3


def test_directory_cache_expires_and_removes_deleted_session(tmp_path):
    stores = make_stores(tmp_path)
    path = write_claude(stores)
    now = [0.0]
    registry = registry_for(stores, "claude", scan_ttl=2.0, clock=lambda: now[0])
    assert len(registry.list_sessions().sessions) == 1
    path.unlink()
    assert len(registry.list_sessions().sessions) == 1  # still inside the TTL
    now[0] = 2.1
    assert registry.list_sessions().sessions == []


def test_a_source_filter_lists_only_that_source(tmp_path):
    stores = make_stores(tmp_path)
    write_claude(stores)
    write_codex(stores)
    registry = registry_for(stores, scan_ttl=60)

    def boom():
        raise AssertionError("the codex store must not be listed for source=claude")

    registry._readers["codex"].list_summaries = boom
    rows, _ = registry.find_sessions(SessionFilters(source="claude"))
    assert [r.source.value for r in rows] == ["claude"]


def test_cache_rows_are_the_rows_of_an_uncached_registry(tmp_path):
    stores = make_stores(tmp_path)
    write_claude(stores)
    write_codex(stores)
    write_copilot(stores)
    cached = registry_for(stores, scan_ttl=60)
    cached.find_sessions()
    warm, _ = cached.find_sessions()
    plain, _ = registry_for(stores).find_sessions()
    assert [r.to_dict() for r in warm] == [r.to_dict() for r in plain] and len(warm) == 3
    assert [r.id for r in cached.list_sessions().sessions] == [r.id for r in warm]  # paging is the same list


def test_a_slow_listing_does_not_hold_the_registry_lock(tmp_path):
    stores = make_stores(tmp_path)
    write_claude(stores)
    registry = registry_for(stores, "claude")
    entered, polled = threading.Event(), threading.Event()
    original = registry._readers["claude"].list_summaries

    def slow():
        entered.set()
        time.sleep(0.2)
        return original()

    registry._readers["claude"].list_summaries = slow
    worker = threading.Thread(target=registry.find_sessions)
    worker.start()
    assert entered.wait(1)
    with registry._scan_lock:
        polled.set()
    worker.join(3)
    assert polled.is_set() and not worker.is_alive()


def test_a_slow_listing_does_not_block_the_live_poll(tmp_path):
    """The stream polls ``poll_items``; a long listing in another thread must not make it wait."""
    stores = make_stores(tmp_path)
    services = make_services(stores)
    write_claude(stores)
    registry = services.sessions._get_registry()
    entered = threading.Event()
    original = registry._readers["claude"].list_summaries

    def slow():
        entered.set()
        time.sleep(0.8)
        return original()

    registry._readers["claude"].list_summaries = slow
    worker = threading.Thread(target=services.sessions.list_sessions)
    worker.start()
    assert entered.wait(2)
    started = time.perf_counter()
    page = services.sessions.poll_items(f"claude:{CLAUDE_ID}", None)
    summary = services.sessions.get_session(f"claude:{CLAUDE_ID}")
    waited = time.perf_counter() - started
    worker.join(5)
    assert page["items"] and summary["id"] == f"claude:{CLAUDE_ID}"
    assert waited < 0.5  # the listing needs 0.8 s; the poll did not queue behind it
    services.runs.close()


# --- counts -----------------------------------------------------------------------------------------------------


def test_counts_route_requires_token_and_counts_each_reader(tmp_path):
    stores = make_stores(tmp_path)
    services = make_services(stores)
    write_claude(stores)
    write_codex(stores)
    write_copilot(stores)
    token = ensure_token(services.settings.gui_token_file)
    auth = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        assert client.get("/gui/api/sessions/counts").status_code == 401
        assert client.get("/gui/api/sessions/counts", headers={"Authorization": "Bearer wrong"}).status_code in (401, 403)
        response = client.get("/gui/api/sessions/counts", headers=auth)
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"counts", "hint"} and payload["hint"] is None
        assert set(payload["counts"]) == {"claude", "codex", "copilot", "framework"}
        for source in ("claude", "codex", "copilot"):
            assert payload["counts"][source]["total"] == 1
            assert set(payload["counts"][source]) == {"total", "active", "idle", "archived", "unknown"}
        assert payload["counts"]["framework"]["total"] == 0
        assert payload == services.sessions.counts()  # HTTP and the Python facade answer the same
    services.runs.close()


def test_counts_match_the_list_by_source_and_status(tmp_path):
    stores = make_stores(tmp_path)
    write_claude(stores)
    other = "11111111-1111-4111-8111-222222222222"
    write_claude(stores, session_id=other)
    desktop = stores.desktop / "acct" / "ws"
    desktop.mkdir(parents=True)
    (desktop / "local_x.json").write_text(
        json.dumps({"cliSessionId": other, "title": "Gone", "isArchived": True}), encoding="utf-8")
    write_codex(stores)
    write_copilot(stores)
    write_claude(stores, session_id="11111111-1111-4111-8111-333333333333", cwd=stores.other)  # outside the roots
    services = make_services(stores)
    answer = services.sessions.counts()["counts"]
    listed = services.sessions.list_sessions(limit=500)["sessions"]
    for source in ("claude", "codex", "copilot"):
        rows = [s for s in listed if s["source"] == source]
        assert answer[source]["total"] == len(rows)
        for status in ("active", "idle", "archived", "unknown"):
            assert answer[source][status] == sum(1 for s in rows if s["status"] == status)
    assert answer["claude"]["total"] == 2 and answer["claude"]["archived"] == 1
    services.runs.close()


def test_counts_without_roots_are_zero_with_a_hint(tmp_path):
    stores = make_stores(tmp_path)
    write_claude(stores)
    services = make_services(stores, gui_session_roots=[])
    answer = services.sessions.counts()
    assert answer["hint"] and all(row["total"] == 0 for row in answer["counts"].values())
    services.runs.close()


# --- load -------------------------------------------------------------------------------------------------


def test_large_index_is_cached_and_one_append_refreshes_only_its_row(tmp_path):
    stores = make_stores(tmp_path)
    services = make_services(stores)
    claude_dir = stores.claude / "projects" / "bulk"
    codex_dir = stores.codex / "sessions" / "2026" / "01" / "05"
    copilot_dir = stores.copilot / "session-state"
    claude_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)
    copilot_dir.mkdir(parents=True)
    ids = [str(uuid.UUID(int=n + 1)) for n in range(2000)]
    cwd = str(stores.work)
    for index, sid in enumerate(ids):
        if index % 3 == 0:
            jsonl(claude_dir / f"{sid}.jsonl", [claude_user("x", LONG_AGO, cwd), assistant_line("m", 1, LONG_AGO)])
        elif index % 3 == 1:
            jsonl(codex_dir / f"rollout-2026-01-05T10-00-00-{sid}.jsonl", [{
                "timestamp": LONG_AGO, "type": "session_meta",
                "payload": {"id": sid, "cwd": cwd, "originator": "codex_cli"},
            }])
        else:
            folder = copilot_dir / sid
            folder.mkdir()
            (folder / "workspace.yaml").write_text(
                f"id: {sid}\ncwd: {cwd}\ncreated_at: {LONG_AGO}\n", encoding="utf-8")
            jsonl(folder / "events.jsonl", [{
                "type": "session.start", "timestamp": LONG_AGO, "data": {"selectedModel": "test"},
            }])

    started = time.perf_counter()
    first = services.sessions.list_sessions(limit=500)
    first_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    second = services.sessions.list_sessions(limit=500)
    second_elapsed = time.perf_counter() - started
    assert second["sessions"] == first["sessions"] and len(first["sessions"]) == 500
    assert second_elapsed < 1.5 or second_elapsed * 5 <= first_elapsed
    counts = services.sessions.counts()["counts"]
    assert sum(counts[source]["total"] for source in ("claude", "codex", "copilot")) == 2000

    # The listing is stale after the TTL, but no transcript changed: the folders are walked and no file is read.
    registry = services.sessions._get_registry()
    reads = Reads()
    registry._index._on_read = reads
    registry.invalidate()
    again = services.sessions.list_sessions(limit=500)
    assert again["sessions"] == first["sessions"] and not reads.bytes

    # One transcript grows: only its row changes, and only the appended bytes are read for it.
    target = ids[0]
    path = claude_dir / f"{target}.jsonl"
    line = assistant_line("y", 3, "2026-01-05T10:01:00Z")
    append(path, [line])
    registry.invalidate()
    refreshed = services.sessions.list_sessions(limit=500)
    changed = [row for row in refreshed["sessions"] if row not in first["sessions"]]
    assert [row["id"] for row in changed] == [f"claude:{target}"]
    assert changed[0]["updated_at"] == "2026-01-05T10:01:00Z" and changed[0]["tokens"]["output"] == 4
    assert {key for key in reads.bytes} == {(path.name, "records"), (path.name, "verify")}
    assert reads.of(path.name) == encoded_size(line) and reads.of(path.name, "verify") <= MAX_VERIFY
    services.runs.close()
