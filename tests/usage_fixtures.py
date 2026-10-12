# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Synthetic app stores and telemetry for the USO-T02 tests: built in ``tmp_path``, never the user's real folders.

Every number, model name and id here is invented. ``NOW`` is the fixed clock of the tests.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from multiagent.observability import estimate_call_cost

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
SECRET = "SYNTHETIC-SECRET-DO-NOT-READ"

CLAUDE_SESSION = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
CLAUDE_RESUMED = "aaaaaaaa-2222-4222-8222-aaaaaaaaaaaa"
CODEX_MAIN = "cccccccc-1111-4111-8111-cccccccccccc"
CODEX_SPAWNED = "cccccccc-2222-4222-8222-cccccccccccc"
CODEX_EDGE_CHILD = "cccccccc-3333-4333-8333-cccccccccccc"
COPILOT_SESSION = "dddddddd-1111-4111-8111-dddddddddddd"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def epoch(text: str) -> int:
    return int(at(text).timestamp())


def write_jsonl(path: Path, records: list, *, mtime: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        for record in records:
            handle.write((record if isinstance(record, str) else json.dumps(record)).encode() + b"\n")
    if mtime is not None:
        stamp = at(mtime).timestamp()
        os.utime(path, (stamp, stamp))
    return path


# --- Claude ----------------------------------------------------------------------------------------------

def claude_message(message_id: str, when: str, model: str, usage: dict, **extra) -> dict:
    return {"type": "assistant", "timestamp": when, **extra,
            "message": {"id": message_id, "role": "assistant", "model": model, "usage": usage,
                        "content": [{"type": "text", "text": "private answer"}]}}


def write_claude(home: Path, *, hit_resets: str = "2026-10-11T14:30:00Z") -> Path:
    projects = home / "projects" / "proj"
    big = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20}
    write_jsonl(projects / f"{CLAUDE_SESSION}.jsonl", [
        {"type": "user", "timestamp": "2026-10-11T09:59:00Z", "message": {"role": "user", "content": "private prompt"}},
        # The same message is written once per content block: the last record carries the final usage.
        claude_message("m1", "2026-10-11T10:00:00Z", "claude-test-big", {**big, "output_tokens": 1}),
        claude_message("m1", "2026-10-11T10:00:01Z", "claude-test-big", big),
        claude_message("m2", "2026-10-11T10:05:00Z", "claude-test-small", {"input_tokens": 7, "output_tokens": 3}),
        claude_message("m3", "2026-10-04T10:00:00Z", "claude-test-small", {"input_tokens": 1000, "output_tokens": 1000}),
        {"type": "assistant", "timestamp": "2026-10-11T11:00:00Z", "isApiErrorMessage": True, "error": "rate_limit",
         "apiErrorStatus": 429, "message": {"id": "synthetic-1", "model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0},
                                            "content": [{"type": "text", "text": "You've hit your session limit"}]},
         "quotaLimits": {"status": "rejected", "resetsAt": epoch(hit_resets), "rateLimitType": "five_hour",
                         "overageStatus": "rejected", "isUsingOverage": False}},
    ], mtime="2026-10-11T11:00:01Z")
    # A resumed session repeats the history of the one it continues: the same message ids, not new tokens.
    write_jsonl(projects / f"{CLAUDE_RESUMED}.jsonl", [
        claude_message("m2", "2026-10-11T10:05:00Z", "claude-test-small", {"input_tokens": 7, "output_tokens": 3}),
    ], mtime="2026-10-11T11:30:00Z")
    write_jsonl(projects / CLAUDE_SESSION / "subagents" / "agent-abc123.jsonl", [
        claude_message("m4", "2026-10-11T10:10:00Z", "claude-test-small", {"input_tokens": 4, "output_tokens": 2},
                       isSidechain=True),
    ], mtime="2026-10-11T10:11:00Z")
    # Credential-like files that must never be opened.
    (home / ".credentials.json").write_text(json.dumps({"token": SECRET}), encoding="utf-8")
    return home


# --- Codex -----------------------------------------------------------------------------------------------

def codex_totals(input_tokens: int, cached: int, output: int, reasoning: int) -> dict:
    return {"input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": output,
            "reasoning_output_tokens": reasoning, "total_tokens": input_tokens + output}


def codex_count(when: str, totals: dict, *, used: float | None = None, weekly: float = 10.0,
                resets: str = "2026-10-11T14:00:00Z", weekly_resets: str = "2026-10-16T00:00:00Z") -> dict:
    limits = None
    if used is not None:
        limits = {"limit_id": "codex", "limit_name": None, "plan_type": "plus",
                  "primary": {"used_percent": used, "window_minutes": 300, "resets_at": epoch(resets)},
                  "secondary": {"used_percent": weekly, "window_minutes": 10080, "resets_at": epoch(weekly_resets)},
                  "credits": {"has_credits": False, "unlimited": False, "balance": "0"}}
    return {"timestamp": when, "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": totals, "last_token_usage": {}}, "rate_limits": limits}}


def write_codex(home: Path) -> Path:
    day = home / "sessions" / "2026" / "10" / "11"
    write_jsonl(day / f"rollout-2026-10-11T08-00-00-{CODEX_MAIN}.jsonl", [
        {"timestamp": "2026-10-11T08:00:00Z", "type": "session_meta", "payload": {"id": CODEX_MAIN, "source": "cli"}},
        {"timestamp": "2026-10-11T08:00:01Z", "type": "turn_context", "payload": {"model": "local_default"}},
        codex_count("2026-10-11T09:00:00Z", codex_totals(100, 40, 20, 5), used=42.5),
        codex_count("2026-10-11T09:00:30Z", codex_totals(100, 40, 20, 5), used=42.5),   # repeated: no new usage
        codex_count("2026-10-11T10:00:00Z", codex_totals(300, 100, 70, 15), used=55.0),
        {"timestamp": "2026-10-11T10:30:00Z", "type": "turn_context", "payload": {"model": "codex-test-other"}},
        codex_count("2026-10-11T11:00:00Z", codex_totals(350, 120, 90, 15), used=60.0, weekly=11.0),
        # Codex also writes premium-window events with no windows: they are not a plan observation.
        {"timestamp": "2026-10-11T11:30:00Z", "type": "event_msg", "payload": {
            "type": "token_count", "info": None,
            "rate_limits": {"limit_id": "premium", "primary": None, "secondary": None, "credits": None}}},
    ], mtime="2026-10-11T11:30:00Z")
    # A spawned thread: its last event is OLDER than the main thread's, but its file was written later.
    write_jsonl(day / f"rollout-2026-10-11T09-30-00-{CODEX_SPAWNED}.jsonl", [
        {"timestamp": "2026-10-11T09:30:00Z", "type": "session_meta",
         "payload": {"id": CODEX_SPAWNED, "source": {"subagent": {"thread_spawn": {"depth": 1}}}}},
        {"timestamp": "2026-10-11T09:30:01Z", "type": "turn_context", "payload": {"model": "codex-test-other"}},
        codex_count("2026-10-11T09:45:00Z", codex_totals(20, 0, 10, 0), used=50.0),
    ], mtime="2026-10-11T11:45:00Z")
    # A thread the index lists as spawned although its own header does not say so.
    write_jsonl(day / f"rollout-2026-10-11T10-00-00-{CODEX_EDGE_CHILD}.jsonl", [
        {"timestamp": "2026-10-11T10:00:00Z", "type": "session_meta", "payload": {"id": CODEX_EDGE_CHILD, "source": "exec"}},
        codex_count("2026-10-11T10:20:00Z", codex_totals(8, 0, 2, 0)),
    ], mtime="2026-10-11T10:21:00Z")
    conn = sqlite3.connect(home / "state_5.sqlite")
    conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
    conn.execute("INSERT INTO thread_spawn_edges VALUES (?,?,?)", (CODEX_MAIN, CODEX_EDGE_CHILD, "closed"))
    conn.commit()
    conn.close()
    (home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": SECRET, "tokens": {"access_token": SECRET}}),
                                    encoding="utf-8")
    return home


# --- Copilot ---------------------------------------------------------------------------------------------

def write_copilot(home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "session-store.db")
    conn.execute("CREATE TABLE assistant_usage_events (session_id TEXT, model TEXT, created_at TEXT, input_tokens INTEGER,"
                 " output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, total_nano_aiu INTEGER,"
                 " request_multiplier REAL)")
    rows = [
        (COPILOT_SESSION, "copilot-test", "2026-10-11T09:00:00.000Z", 1000, 200, 50, 10, 2_000_000_000, 1.0),
        (COPILOT_SESSION, "copilot-test", "2026-10-02T09:00:00.000Z", 500, 100, None, None, 3_000_000_000, 1.0),
        (COPILOT_SESSION, "copilot-test", "2026-09-30T23:00:00.000Z", 900, 90, None, None, 5_000_000_000, 0.33),
    ]
    conn.executemany("INSERT INTO assistant_usage_events VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    # The app's account database holds tokens: it must never be opened.
    conn = sqlite3.connect(home / "data.db")
    conn.execute("CREATE TABLE accounts (login TEXT, token TEXT)")
    conn.execute("INSERT INTO accounts VALUES ('someone', ?)", (SECRET,))
    conn.commit()
    conn.close()
    write_jsonl(home / "session-state" / COPILOT_SESSION / "events.jsonl", [
        {"type": "session.start", "timestamp": "2026-10-10T19:00:00Z", "data": {"copilotVersion": "1.0"}},
        {"type": "session.error", "timestamp": "2026-10-10T20:00:00Z", "data": {
            "errorType": "quota", "statusCode": 402, "errorCode": "quota_exceeded",
            "message": "You have exceeded your monthly quota (Request ID: 0000:SYNTHETIC)"}},
    ], mtime="2026-10-10T20:00:01Z")
    return home


def statusline_snapshot(path: Path, *, five: float = 23.5, week: float = 41.2,
                        five_resets: str = "2026-10-11T15:00:00Z", week_resets: str = "2026-10-17T00:00:00Z",
                        mtime: str = "2026-10-11T11:55:00Z") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": {"id": "claude-test-big"}, "rate_limits": {
        "five_hour": {"used_percentage": five, "resets_at": epoch(five_resets)},
        "seven_day": {"used_percentage": week, "resets_at": epoch(week_resets)}}}), encoding="utf-8")
    stamp = at(mtime).timestamp()
    os.utime(path, (stamp, stamp))
    return path


# --- framework telemetry -----------------------------------------------------------------------------------

def price_side(amount: float, **overrides) -> dict:
    side = {"amount": amount, "denominator": 1_000_000, "unit": "token", "currency": "USD",
            "source": "unit-test rate card", "provenance": "catalog_declared",
            "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2027-01-01T00:00:00Z"}
    side.update(overrides)
    return side


def priced(tokens_in: int, tokens_out: int, *, model_id: str = "m-priced", currency: str = "USD",
           input_price: float = 2.0, output_price: float = 6.0, when: str = "2026-10-10T08:00:00Z"):
    """``(estimated_cost_usd, cost_basis JSON)`` of a call priced with synthetic evidence."""
    spec = SimpleNamespace(id=model_id, pricing={"input": price_side(input_price, currency=currency),
                                                 "output": price_side(output_price, currency=currency)})
    estimate = estimate_call_cost(spec, tokens_in=tokens_in, tokens_out=tokens_out, at=at(when))
    return estimate.estimated_cost_usd, json.dumps(estimate.basis, sort_keys=True)


def add_telemetry(store, *, run_id: str, when: str, model: str, tokens_in: int | None, tokens_out: int | None,
                  cost: tuple | None = None, provider: str = "gemini", step_id: str = "s0", success: int = 1) -> None:
    """One telemetry row with a chosen ``created_at`` (the store itself stamps the current time)."""
    usd, basis = cost if cost is not None else (None, None)
    stamp = at(when).isoformat()
    with store.db.connect() as conn:
        conn.execute(
            "INSERT INTO telemetry(run_id,step_id,agent_id,attempt,provider,model,model_digest,prompt_id,prompt_version,"
            "prompt_sha256,brand_version,tokens_in,tokens_out,num_ctx,latency_ms,duration_ms,estimated_cost_usd,"
            "cost_basis,success,error_type,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, step_id, "agent", 1, provider, model, "", "p", 1, "", None, tokens_in, tokens_out, None, 5, 5,
             usd, basis, success, "", stamp))
        conn.commit()


def fill_telemetry(store) -> None:
    """Runs on three days and two months, with priced, unpriced, other-currency, partial and human calls."""
    add_telemetry(store, run_id="run-a", when="2026-10-10T08:00:00Z", model="m-priced", tokens_in=1000, tokens_out=500,
                  cost=priced(1000, 500))
    add_telemetry(store, run_id="run-a", when="2026-10-10T08:01:00Z", model="m-priced", tokens_in=2000, tokens_out=100,
                  cost=priced(2000, 100), step_id="s1")
    add_telemetry(store, run_id="run-a", when="2026-10-10T08:02:00Z", model="", tokens_in=None, tokens_out=None,
                  provider="human", step_id="s2")
    add_telemetry(store, run_id="run-b", when="2026-10-11T09:00:00Z", model="qwen3:8b", tokens_in=100, tokens_out=50,
                  provider="ollama")
    add_telemetry(store, run_id="run-b", when="2026-10-11T09:01:00Z", model="m-partial", tokens_in=70, tokens_out=None,
                  step_id="s1")
    add_telemetry(store, run_id="run-c", when="2026-09-30T23:30:00Z", model="m-priced", tokens_in=300, tokens_out=300,
                  cost=priced(300, 300, when="2026-09-30T23:30:00Z"))
    add_telemetry(store, run_id="run-d", when="2026-10-11T10:00:00Z", model="m-euro", tokens_in=1000, tokens_out=1000,
                  cost=priced(1000, 1000, model_id="m-euro", currency="EUR", input_price=1.0, output_price=3.0))
