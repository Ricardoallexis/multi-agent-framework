# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""USO-T02 readers of the apps' usage sources: synthetic stores in ``tmp_path`` only, never the real app folders."""
from __future__ import annotations

import builtins
import json
import os
import sqlite3
from datetime import timedelta

import pytest

from multiagent.sessions import usage_sources as src
from usage_fixtures import (
    CODEX_MAIN,
    CODEX_SPAWNED,
    NOW,
    SECRET,
    at,
    codex_count,
    codex_totals,
    epoch,
    statusline_snapshot,
    write_claude,
    write_codex,
    write_copilot,
    write_jsonl,
)

WEEK_AGO = NOW - timedelta(days=7)


def by(events, **fields):
    """The events whose attributes equal ``fields``."""
    return [e for e in events if all(getattr(e, k) == v for k, v in fields.items())]


# --- Claude tokens --------------------------------------------------------------------------------------------

def test_claude_tokens_dedupe_messages_by_id_and_keep_the_final_usage(tmp_path):
    write_claude(tmp_path)
    events, warnings = src.read_claude_tokens(tmp_path, WEEK_AGO, NOW)

    assert warnings == []
    first = by(events, model="claude-test-big")
    assert len(first) == 1                                  # written twice, counted once
    assert (first[0].input, first[0].output, first[0].cache_read, first[0].cache_write) == (10, 5, 100, 20)
    assert first[0].total == 135 and first[0].role == "primary" and first[0].app == "claude"
    # m2 is in two files (a resumed session repeats the history) and counted once; no cache means None, not 0.
    small = by(events, model="claude-test-small", role="primary")
    assert len(small) == 1 and (small[0].input, small[0].output, small[0].cache_read, small[0].total) == (7, 3, None, 10)


def test_claude_subagent_files_and_sidechain_messages_are_delegated(tmp_path):
    write_claude(tmp_path)
    events, _ = src.read_claude_tokens(tmp_path, WEEK_AGO, NOW)

    delegated = by(events, role="delegated")
    assert [(e.model, e.input, e.output) for e in delegated] == [("claude-test-small", 4, 2)]
    assert len(events) == 3                                 # m1, m2, m4; the synthetic error message is no usage


def test_claude_window_is_half_open_and_excludes_older_messages(tmp_path):
    write_claude(tmp_path)

    inside, _ = src.read_claude_tokens(tmp_path, at("2026-10-11T10:00:00Z"), at("2026-10-11T10:05:00Z"))
    assert [e.model for e in inside] == ["claude-test-big"]          # since is included, until is not
    everything, _ = src.read_claude_tokens(tmp_path, at("2026-10-01T00:00:00Z"), NOW)
    assert sum(e.input for e in everything) == 10 + 7 + 1000 + 4    # the October 4 message is in the wider window
    assert src.read_claude_tokens(tmp_path, at("2026-10-12T00:00:00Z"), at("2026-10-13T00:00:00Z"))[0] == []


def test_claude_files_older_than_the_window_are_not_even_read(tmp_path, monkeypatch):
    write_claude(tmp_path)
    opened = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])

    src.read_claude_tokens(tmp_path, at("2026-10-11T11:40:00Z"), NOW)

    assert opened == []          # every transcript was last written before 11:40


def test_claude_tokens_missing_folder_is_a_warning_not_an_error(tmp_path):
    events, warnings = src.read_claude_tokens(tmp_path / "nope", WEEK_AGO, NOW)
    assert events == [] and warnings and warnings[0].startswith("source_unavailable")


def test_claude_broken_and_oversized_lines_are_skipped(tmp_path):
    projects = tmp_path / "projects" / "p"
    good = {"type": "assistant", "timestamp": "2026-10-11T10:00:00Z", "message": {
        "id": "ok", "model": "claude-test-big", "usage": {"input_tokens": 3, "output_tokens": 4}}}
    write_jsonl(projects / "s.jsonl", ["{not json", "[1, 2]", json.dumps(good), json.dumps(good)[:40]],
                mtime="2026-10-11T10:00:01Z")
    events, _ = src.read_claude_tokens(tmp_path, WEEK_AGO, NOW)
    assert [(e.input, e.output) for e in events] == [(3, 4)]


# --- Claude plan sources ---------------------------------------------------------------------------------------

def test_claude_limit_hit_gives_the_reset_instant_and_no_percent(tmp_path):
    write_claude(tmp_path)
    hits, warnings = src.read_claude_limit_hits(tmp_path, NOW - timedelta(days=8))

    assert warnings == [] and len(hits) == 1
    hit = hits[0]
    assert (hit.app, hit.window, hit.detail) == ("claude", "five_hour", "five_hour")
    assert hit.resets_at == at("2026-10-11T14:30:00Z") and hit.reset_source == "app_epoch"
    assert hit.at == at("2026-10-11T11:00:00Z")


def test_claude_limit_hit_maps_the_rate_limit_types_and_keeps_the_latest_per_type(tmp_path):
    def hit(when, rate_type, resets, status="rejected"):
        return {"type": "assistant", "timestamp": when, "message": {"id": when + rate_type, "model": "<synthetic>"},
                "apiErrorParams": {"rate_limit_info": {"status": status, "rateLimitType": rate_type,
                                                       "resetsAt": epoch(resets)}}}
    write_jsonl(tmp_path / "projects" / "p" / "s.jsonl", [
        hit("2026-10-10T10:00:00Z", "seven_day", "2026-10-15T00:00:00Z"),
        hit("2026-10-11T10:00:00Z", "seven_day", "2026-10-16T00:00:00Z"),
        hit("2026-10-11T10:30:00Z", "seven_day_opus", "2026-10-17T00:00:00Z"),
        hit("2026-10-11T10:40:00Z", "overage", "2026-11-01T00:00:00Z"),
        hit("2026-10-11T10:50:00Z", "five_hour", "2026-10-11T15:00:00Z", status="allowed"),   # not a rejection
        hit("2026-10-01T10:00:00Z", "mystery", "2026-10-02T00:00:00Z"),                       # older than the lookback
    ], mtime="2026-10-11T10:51:00Z")

    hits, _ = src.read_claude_limit_hits(tmp_path, NOW - timedelta(days=8))

    found = {(h.window, h.detail): h.resets_at for h in hits}
    assert found == {("weekly", "seven_day"): at("2026-10-16T00:00:00Z"),
                     ("model_weekly", "seven_day_opus"): at("2026-10-17T00:00:00Z"),
                     ("spend", "overage"): at("2026-11-01T00:00:00Z")}


def test_claude_statusline_snapshot_gives_percent_and_reset(tmp_path):
    snapshot = statusline_snapshot(tmp_path / "snap" / "status.json")
    observed, warnings = src.read_claude_statusline(snapshot)

    assert warnings == [] and observed is not None and observed.app == "claude"
    assert observed.observed_at == at("2026-10-11T11:55:00Z")
    windows = {w.window: w for w in observed.windows}
    assert windows["five_hour"].used_percent == 23.5 and windows["five_hour"].resets_at == at("2026-10-11T15:00:00Z")
    assert windows["weekly"].used_percent == 41.2 and windows["weekly"].window_minutes == 10080


@pytest.mark.parametrize("content, warning", [
    (None, "statusline_snapshot_missing"),
    ("not json", "statusline_snapshot_unreadable"),
    ('{"model": {}}', "statusline_snapshot_without_rate_limits"),
    ('{"rate_limits": {"five_hour": {"used_percentage": "high"}}}', "statusline_snapshot_without_rate_limits"),
    ('["x"]', "statusline_snapshot_without_rate_limits"),
])
def test_claude_statusline_snapshot_problems_are_warnings(tmp_path, content, warning):
    path = tmp_path / "status.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    observed, warnings = src.read_claude_statusline(path)
    assert observed is None and warnings == [warning]


def test_claude_statusline_snapshot_too_large_is_refused(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(" " * (src.MAX_STATUSLINE_BYTES + 1), encoding="utf-8")
    assert src.read_claude_statusline(path) == (None, ["statusline_snapshot_too_large"])


# --- Codex tokens ----------------------------------------------------------------------------------------------

def test_codex_tokens_are_the_growth_of_the_cumulative_counters_per_model(tmp_path):
    write_codex(tmp_path)
    events, warnings = src.read_codex_tokens(tmp_path, at("2026-10-11T00:00:00Z"), NOW)

    assert warnings == []
    main = sorted(by(events, role="primary"), key=lambda e: e.at)
    # 09:00 first counters; the repeated 09:00:30 event adds nothing; 10:00 and 11:00 are growth over the previous one.
    assert [(e.at.strftime("%H:%M"), e.model, e.total) for e in main] == [
        ("09:00", "local_default", 120), ("10:00", "local_default", 250), ("11:00", "codex-test-other", 70)]
    ten = main[1]
    assert (ten.input, ten.cache_read, ten.output, ten.reasoning) == (200, 60, 50, 10)
    assert sum(e.total for e in main) == 440                 # = the last cumulative total of the rollout


def test_codex_window_counts_only_the_growth_inside_it(tmp_path):
    write_codex(tmp_path)
    events, _ = src.read_codex_tokens(tmp_path, at("2026-10-11T09:30:00Z"), at("2026-10-11T10:30:00Z"))

    # The 10:00 growth is measured against the 09:00 counters, which are before the window.
    assert [(e.role, e.total) for e in sorted(events, key=lambda e: e.at)] == [
        ("delegated", 30), ("primary", 250), ("delegated", 10)]


def test_codex_spawned_threads_are_delegated_by_their_header_or_by_the_index(tmp_path):
    write_codex(tmp_path)
    events, _ = src.read_codex_tokens(tmp_path, at("2026-10-11T00:00:00Z"), NOW)

    delegated = by(events, role="delegated")
    assert sorted((e.model, e.total) for e in delegated if e.model) == [("codex-test-other", 30)]   # header says subagent
    assert sorted(e.total for e in delegated if e.model is None) == [10]                          # only state_5.sqlite says so
    assert sum(e.total for e in by(events, role="primary")) == 440


def test_codex_counter_that_goes_down_restarts_instead_of_going_negative(tmp_path):
    write_jsonl(tmp_path / "sessions" / "r" / f"rollout-x-{CODEX_MAIN}.jsonl", [
        codex_count("2026-10-11T09:00:00Z", codex_totals(100, 0, 50, 0)),
        codex_count("2026-10-11T10:00:00Z", codex_totals(30, 0, 10, 0)),      # restarted below the previous total
    ], mtime="2026-10-11T10:00:01Z")
    events, _ = src.read_codex_tokens(tmp_path, at("2026-10-11T00:00:00Z"), NOW)
    assert [e.total for e in sorted(events, key=lambda e: e.at)] == [150, 40]


def test_codex_missing_store_is_a_warning(tmp_path):
    assert src.read_codex_tokens(tmp_path / "nope", WEEK_AGO, NOW) == ([], ["source_unavailable: codex store not found"])
    assert src.read_codex_plan(tmp_path / "nope") == (None, ["source_unavailable: codex store not found"])


# --- Codex plan ------------------------------------------------------------------------------------------------

def test_codex_plan_is_the_newest_rate_limits_by_event_time_not_file_time(tmp_path):
    write_codex(tmp_path)            # the spawned rollout was written later but holds an older observation (50 %)
    assert os.stat(next((tmp_path / "sessions").rglob(f"*{CODEX_SPAWNED}.jsonl"))).st_mtime > os.stat(
        next((tmp_path / "sessions").rglob(f"*{CODEX_MAIN}.jsonl"))).st_mtime

    observed, warnings = src.read_codex_plan(tmp_path)

    assert warnings == [] and observed is not None and observed.app == "codex"
    assert observed.observed_at == at("2026-10-11T11:00:00Z") and observed.plan == "plus"
    windows = {w.window: w for w in observed.windows}
    assert windows["five_hour"].used_percent == 60.0 and windows["five_hour"].window_minutes == 300
    assert windows["five_hour"].resets_at == at("2026-10-11T14:00:00Z") and windows["five_hour"].reset_source == "app_epoch"
    assert windows["weekly"].used_percent == 11.0 and windows["weekly"].window_minutes == 10080
    assert observed.credits == {"has_credits": False, "unlimited": False, "balance": "0"}


def test_codex_plan_ignores_files_without_a_usable_window(tmp_path):
    write_jsonl(tmp_path / "sessions" / "r" / f"rollout-x-{CODEX_MAIN}.jsonl", [
        {"timestamp": "2026-10-11T09:00:00Z", "type": "session_meta", "payload": {}},
        {"timestamp": "2026-10-11T09:01:00Z", "type": "event_msg", "payload": {
            "type": "token_count", "info": None, "rate_limits": {"limit_id": "premium", "primary": None}}},
    ])
    assert src.read_codex_plan(tmp_path) == (None, ["no_rate_limit_events"])


def test_codex_plan_reads_only_the_end_of_a_long_rollout(tmp_path):
    padding = ["x" * 1000] * 2000                       # about 2 MB of lines before the observation
    write_jsonl(tmp_path / "sessions" / "r" / f"rollout-x-{CODEX_MAIN}.jsonl", [
        codex_count("2026-10-11T08:00:00Z", codex_totals(1, 0, 1, 0), used=5.0),
        *(json.dumps({"timestamp": "2026-10-11T08:30:00Z", "type": "response_item", "payload": {"text": p}})
          for p in padding),
        codex_count("2026-10-11T11:00:00Z", codex_totals(9, 0, 9, 0), used=77.0),
    ])
    observed, _ = src.read_codex_plan(tmp_path)
    assert observed is not None and observed.windows[0].used_percent == 77.0


# --- Copilot ---------------------------------------------------------------------------------------------------

def test_copilot_tokens_come_from_the_usage_table_inside_the_window(tmp_path):
    write_copilot(tmp_path)
    events, warnings = src.read_copilot_tokens(tmp_path, WEEK_AGO, NOW)

    assert warnings == [] and len(events) == 1
    event = events[0]
    assert (event.app, event.model, event.role) == ("copilot", "copilot-test", "unknown")   # the table cannot tell a subagent
    assert (event.input, event.output, event.cache_read, event.cache_write, event.total) == (1000, 200, 50, 10, 1200)
    assert event.nano_aiu == 2_000_000_000
    month, _ = src.read_copilot_tokens(tmp_path, src.month_start(NOW), src.next_month(NOW))
    assert sorted(e.nano_aiu for e in month) == [2_000_000_000, 3_000_000_000]   # September's call is not this month's
    assert [e.cache_read for e in month if e.at.day == 2] == [None]


def test_copilot_missing_database_or_table_is_a_warning_or_empty(tmp_path):
    assert src.read_copilot_tokens(tmp_path, WEEK_AGO, NOW)[1][0].startswith("source_unavailable")
    (tmp_path / "session-store.db").write_bytes(b"")
    conn = sqlite3.connect(tmp_path / "session-store.db")
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    assert src.read_copilot_tokens(tmp_path, WEEK_AGO, NOW) == ([], [])
    (tmp_path / "session-store.db").write_bytes(b"this is not a database at all, just text")
    events, warnings = src.read_copilot_tokens(tmp_path, WEEK_AGO, NOW)
    assert events == [] and warnings and warnings[0].startswith("source_unavailable")


def test_copilot_quota_error_is_found_without_its_message_or_request_id(tmp_path):
    write_copilot(tmp_path)
    hit, warnings = src.read_copilot_limit_hit(tmp_path, src.month_start(NOW))

    assert warnings == [] and hit is not None
    assert (hit.app, hit.window, hit.detail, hit.at) == ("copilot", "monthly", "quota_exceeded", at("2026-10-10T20:00:00Z"))
    assert hit.resets_at is None and hit.reset_source == "none"
    assert "SYNTHETIC" not in repr(hit)
    assert src.read_copilot_limit_hit(tmp_path, NOW)[0] is None             # nothing since noon today


def test_month_boundaries_are_utc_and_wrap_the_year():
    assert src.month_start(at("2026-10-31T23:59:59Z")) == at("2026-10-01T00:00:00Z")
    assert src.next_month(at("2026-10-31T23:59:59Z")) == at("2026-11-01T00:00:00Z")
    assert src.next_month(at("2026-12-15T00:00:00Z")) == at("2027-01-01T00:00:00Z")


# --- what is never read --------------------------------------------------------------------------------------------

def test_credential_files_and_account_databases_are_never_opened(tmp_path, monkeypatch):
    claude, codex, copilot = tmp_path / "c", tmp_path / "x", tmp_path / "p"
    write_claude(claude)
    write_codex(codex)
    write_copilot(copilot)
    touched: list[str] = []
    real_open, real_connect = builtins.open, sqlite3.connect
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (touched.append(str(f)), real_open(f, *a, **k))[1])
    monkeypatch.setattr(sqlite3, "connect", lambda f, *a, **k: (touched.append(str(f)), real_connect(f, *a, **k))[1])

    outputs = [
        src.read_claude_tokens(claude, WEEK_AGO, NOW), src.read_claude_limit_hits(claude, WEEK_AGO),
        src.read_codex_tokens(codex, WEEK_AGO, NOW), src.read_codex_plan(codex),
        src.read_copilot_tokens(copilot, WEEK_AGO, NOW), src.read_copilot_limit_hit(copilot, WEEK_AGO),
    ]

    assert touched, "the readers should have opened the synthetic stores"
    names = {os.path.basename(path.split("?")[0]) for path in touched}
    assert not names & {"auth.json", ".credentials.json", "data.db", ".claude.json"}, names
    assert SECRET not in repr(outputs)
