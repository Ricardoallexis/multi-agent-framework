# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI2-T04: the unified subagents panel. Synthetic Claude, Codex and Copilot stores and a framework run (never the
user's real folders): the children list per source, the status rules with a fixed clock, that no message content
leaves the list, the route (token, filter, errors), the lightweight "children" stream, and static checks of the new
client code. No browser: the JavaScript is read as text, and its pure helpers run under Node when it is installed."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui import STATIC_DIR, events
from multiagent.gui.security import CSP
from multiagent.gui.token import ensure_token
from multiagent.services.errors import ServiceError
from multiagent.sessions.children import (
    ChildStatus,
    command_text,
    derive_status,
    group_of,
    is_test_call,
)
from session_fixtures import (
    CLAUDE_ID,
    CODEX_ID,
    COPILOT_ID,
    add_framework_run,
    append,
    jsonl,
    make_services,
    make_stores,
    write_claude,
    write_codex,
    write_copilot,
)
from test_gui_sessions_view import FORBIDDEN

JS = STATIC_DIR / "js"
NEW_JS = ["subagents-text.js", "subagents-panel.js", "subagents-mount.js"]
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
SECRET = "SECRETO-QUE-NO-DEBE-SALIR"
CODEX_KID_A = "44444444-4444-4444-8444-44444444444a"
CODEX_KID_B = "44444444-4444-4444-8444-44444444444b"
CODEX_KID_C = "44444444-4444-4444-8444-44444444444c"


def ago(minutes: float, now: datetime = NOW) -> str:
    return (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- synthetic stores --------------------------------------------------------------------------------------

def assistant(when: str, content: list, mid: str, *, model: str = "claude-sonnet-5-5", usage: dict | None = None,
              stop: str | None = None) -> dict:
    message = {"id": mid, "role": "assistant", "model": model, "content": content, "stop_reason": stop}
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "timestamp": when, "message": message}


def tool_call(call_id: str, command: str) -> dict:
    return {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}}


def tool_result(when: str, call_id: str) -> dict:
    return {"type": "user", "timestamp": when, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": f"salida {SECRET}"}]}}


def prompt(when: str) -> dict:
    return {"type": "user", "timestamp": when, "message": {"role": "user", "content": f"haz esto {SECRET}"}}


def write_agent(stores, agent: str, records: list, meta: dict | None = None, session: str = CLAUDE_ID):
    path = stores.claude / "projects" / "proj" / session / "subagents" / f"agent-{agent}.jsonl"
    jsonl(path, records)
    if meta is not None:
        path.with_suffix(".meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def claude_children(stores) -> dict[str, list]:
    """One subagent per rule of the status table, all ending relative to ``NOW``."""
    write_claude(stores)
    short = {"input_tokens": 10, "output_tokens": 5}
    return {
        "running": [prompt(ago(9)), assistant(ago(2), [tool_call("t1", "ls")], "m1", usage=short)],
        "testing": [prompt(ago(9)), assistant(ago(3), [tool_call("t1", f"python -m pytest -q {SECRET}")], "m1")],
        "tested": [prompt(ago(9)), assistant(ago(4), [tool_call("t1", "pytest -q")], "m1"), tool_result(ago(3), "t1")],
        "idle": [prompt(ago(40)), assistant(ago(30), [tool_call("t1", "ls")], "m1")],
        "stopped": [prompt(ago(100)), assistant(ago(90), [tool_call("t1", "ls")], "m1")],
        "done": [prompt(ago(9)), assistant(ago(1), [{"type": "text", "text": f"listo {SECRET}"}], "m2",
                                           usage={"input_tokens": 7, "output_tokens": 3}, stop="end_turn")],
        "quiet": [prompt(ago(9)), assistant(ago(5), [{"type": "text", "text": "respuesta final"}], "m1")],
        "midturn": [prompt(ago(9)), assistant(ago(0.5), [{"type": "text", "text": "pienso"}], "m1")],
    }


@pytest.fixture
def stores(tmp_path):
    return make_stores(tmp_path)


@pytest.fixture
def services(stores):
    svc = make_services(stores)
    yield svc
    svc.runs.close()


def children(services, session_id: str, **kwargs) -> dict:
    return services.sessions.list_children(session_id, now=kwargs.pop("now", NOW), **kwargs)


def by_id(answer: dict) -> dict:
    return {child["id"].rsplit(":", 1)[-1].rsplit("#", 1)[-1]: child for child in answer["children"]}


# --- the status rules ------------------------------------------------------------------------------------

@pytest.mark.parametrize("age, expected", [
    (0, "running"), (14 * 60 + 59, "running"), (15 * 60, "idle"), (59 * 60, "idle"), (60 * 60, "idle"),
    (60 * 60 + 1, "stopped"), (5 * 3600, "stopped"),
])
def test_status_by_time_since_the_last_record(age, expected):
    last = (NOW - timedelta(seconds=age)).isoformat()
    assert derive_status(now=NOW, last_activity=last).value == expected


def test_status_testing_done_failed_unknown_and_future_times():
    recent = ago(1)
    assert derive_status(now=NOW, last_activity=recent, testing=True) is ChildStatus.TESTING
    assert derive_status(now=NOW, last_activity=ago(30), testing=True) is ChildStatus.IDLE  # a stale test call is not "testing"
    assert derive_status(now=NOW, last_activity=ago(500), finished=True) is ChildStatus.DONE
    assert derive_status(now=NOW, last_activity=recent, finished=True, failed=True) is ChildStatus.STOPPED
    assert derive_status(now=NOW, last_activity=None) is ChildStatus.UNKNOWN
    assert derive_status(now=NOW, last_activity="not a time") is ChildStatus.UNKNOWN
    assert derive_status(now=NOW, last_activity=(NOW + timedelta(minutes=5)).isoformat()) is ChildStatus.RUNNING


def test_active_and_done_groups_match_the_tabs():
    assert {s: group_of(s) for s in ("running", "testing", "idle", "done", "stopped", "unknown")} == {
        "running": "active", "testing": "active", "idle": "active",
        "done": "done", "stopped": "done", "unknown": "done"}


@pytest.mark.parametrize("args, expected", [
    ({"command": "python -m pytest -q"}, True), ({"command": "powershell .\\probar.ps1 -Rapido"}, True),
    ({"command": "integrar-sin-ventanas.ps1 -Solo"}, True), ({"cmd": "pytest"}, True),
    ('{"command": ["bash", "-lc", "pytest tests"]}', True), ({"action": {"command": ["pytest"]}}, True),
    ({"command": "ls -la"}, False), ({"command": "echo contestar"}, False),
    ({"new_string": "def test_pytest(): pass"}, False),     # file content is never searched
    ("*** Update File: x\n pytest", False), (None, False), (["pytest"], False), (7, False),
])
def test_test_calls_are_recognised_by_their_command_only(args, expected):
    assert is_test_call(args) is expected


def test_command_text_is_bounded():
    assert len(command_text({"command": "x" * 10_000})) == 2000


# --- Claude ------------------------------------------------------------------------------------------------

def test_claude_children_status_per_rule_with_a_fixed_clock(stores, services):
    for agent, records in claude_children(stores).items():
        write_agent(stores, agent, records)
    answer = children(services, f"claude:{CLAUDE_ID}")
    found = {name.removeprefix("agent-"): child["status"] for name, child in by_id(answer).items()}
    assert found == {"running": "running", "testing": "testing", "tested": "running", "idle": "idle",
                     "stopped": "stopped", "done": "done", "quiet": "done", "midturn": "running"}
    assert answer["counts"] == {"active": 5, "done": 3, "total": 8}
    assert answer["session_id"] == f"claude:{CLAUDE_ID}" and answer["warnings"] == []


def test_claude_child_fields_label_model_times_tokens_and_link(stores, services):
    write_claude(stores)
    records = [prompt(ago(9)),
               assistant(ago(8), [tool_call("t1", "ls")], "m1", usage={"input_tokens": 100, "output_tokens": 20,
                                                                      "cache_read_input_tokens": 5}),
               assistant(ago(2), [{"type": "text", "text": "x"}], "m1", usage={"input_tokens": 100, "output_tokens": 20,
                                                                              "cache_read_input_tokens": 5}),  # same id: counted once
               assistant(ago(1), [tool_call("t2", "ls")], "m2", usage={"input_tokens": 1, "output_tokens": 2})]
    write_agent(stores, "abc", records, meta={"agentType": "Explore", "description": "Revisar el lector", "model": "sonnet"})
    child = by_id(children(services, f"claude:{CLAUDE_ID}"))["agent-abc"]
    assert child["id"] == child["link_id"] == f"claude:{CLAUDE_ID}:agent-abc"
    assert (child["source"], child["kind"], child["name"], child["label"]) == ("claude", "subagent", "Explore", "Revisar el lector")
    assert child["model"] == "claude-sonnet-5-5" and child["level"] is None
    assert child["started_at"] == ago(9) and child["last_activity_at"] == ago(1)
    assert child["tokens"] == {"input": 101, "output": 22, "cache_read": 5, "cache_write": None}
    assert child["status"] == "running"


def test_claude_unknown_tokens_stay_null_and_the_meta_model_is_the_fallback(stores, services):
    write_claude(stores)
    write_agent(stores, "x1", [prompt(ago(2)), {"type": "assistant", "timestamp": ago(1), "message": {
        "role": "assistant", "content": [{"type": "text", "text": "hola"}]}}], meta={"model": "haiku"})
    child = by_id(children(services, f"claude:{CLAUDE_ID}"))["agent-x1"]
    assert child["tokens"] is None and child["model"] == "haiku"
    assert child["name"] is None and child["label"] is None


def test_claude_level_comes_from_the_catalog_when_the_model_is_mapped(stores, services):
    mapped = services.system.catalog.models["local_default"]
    assert mapped.level == "T1"
    write_claude(stores)
    write_agent(stores, "m1", [prompt(ago(2)), assistant(ago(1), [tool_call("t", "ls")], "a", model=mapped.model.upper())])
    write_agent(stores, "m2", [prompt(ago(2)), assistant(ago(1), [tool_call("t", "ls")], "b", model="claude-unmapped-9")])
    found = by_id(children(services, f"claude:{CLAUDE_ID}"))
    assert found["agent-m1"]["level"] == "T1" and found["agent-m2"]["level"] is None


def test_claude_without_subagents_or_for_a_subagent_is_an_empty_list(stores, services):
    write_claude(stores)
    assert children(services, f"claude:{CLAUDE_ID}")["children"] == []
    write_agent(stores, "q", [prompt(ago(1))])
    answer = children(services, f"claude:{CLAUDE_ID}:agent-q")
    assert answer["children"] == [] and answer["counts"] == {"active": 0, "done": 0, "total": 0}


def test_claude_tolerates_broken_lines_oversized_meta_and_odd_shapes(stores, services):
    write_claude(stores)
    path = write_agent(stores, "odd", [
        "{not json", prompt(ago(3)), {"type": "mystery", "timestamp": ago(2)},
        {"type": "assistant", "timestamp": ago(2), "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": ["unhashable"], "name": "Bash", "input": {"command": "pytest"}},
            "not a block", {"type": "tool_use", "id": "ok", "name": "Bash", "input": "not a dict"}]}},
        {"type": "user", "timestamp": ago(1), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": ["unhashable"]}]}},
    ])
    path.with_suffix(".meta.json").write_text("x" * 100_000, encoding="utf-8")
    child = by_id(children(services, f"claude:{CLAUDE_ID}"))["agent-odd"]
    assert child["status"] == "running" and child["name"] is None and child["label"] is None


def test_claude_labels_are_bounded_single_line_text(stores, services):
    write_claude(stores)
    write_agent(stores, "lab", [prompt(ago(1))], meta={"agentType": 7, "description": "uno\ndos  " + "z" * 500})
    child = by_id(children(services, f"claude:{CLAUDE_ID}"))["agent-lab"]
    assert child["name"] is None and "\n" not in child["label"] and len(child["label"]) == 120


def test_no_message_content_leaves_the_list(stores, services):
    for agent, records in claude_children(stores).items():
        write_agent(stores, agent, records, meta={"agentType": "General", "description": "Tarea corta"})
    dumped = json.dumps(children(services, f"claude:{CLAUDE_ID}"))
    for hidden in (SECRET, "haz esto", "salida", "listo", "respuesta final", "pytest -q", "command"):
        assert hidden not in dumped, hidden
    keys = {"id", "source", "kind", "name", "label", "model", "level", "status", "note", "started_at",
            "last_activity_at", "tokens", "link_id"}
    assert all(set(child) == keys for child in json.loads(dumped)["children"])


# --- Codex -------------------------------------------------------------------------------------------------

def make_codex_family(stores, *, with_status: bool = True) -> None:
    """A parent thread with three child threads: one working, one finished (task_complete), one closed by the parent."""
    write_codex(stores)
    home = stores.codex
    day = home / "sessions" / "2026" / "10" / "02"
    meta = lambda cwd=stores.work: {"timestamp": ago(30), "type": "session_meta", "payload": {"cwd": str(cwd)}}  # noqa: E731
    jsonl(day / f"rollout-2026-10-02T11-00-00-{CODEX_KID_A}.jsonl", [
        meta(), {"timestamp": ago(20), "type": "turn_context", "payload": {"model": "gpt-6-luna"}},
        {"timestamp": ago(10), "type": "response_item", "payload": {"type": "function_call", "name": "shell",
         "arguments": json.dumps({"command": ["bash", "-lc", f"pytest {SECRET}"]}), "call_id": "c1"}},
        {"timestamp": ago(4), "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 50, "total_tokens": 1050}}}}])
    jsonl(day / f"rollout-2026-10-02T11-01-00-{CODEX_KID_B}.jsonl", [
        meta(), {"timestamp": ago(6), "type": "event_msg", "payload": {"type": "task_started"}},
        {"timestamp": ago(5), "type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": SECRET}]}},
        {"timestamp": ago(4), "type": "event_msg", "payload": {"type": "task_complete"}}])
    jsonl(day / f"rollout-2026-10-02T11-02-00-{CODEX_KID_C}.jsonl", [meta(), {"timestamp": ago(200), "type": "turn_context", "payload": {}}])
    conn = sqlite3.connect(home / "state_5.sqlite")
    conn.execute("DROP TABLE IF EXISTS threads")
    conn.execute("CREATE TABLE threads (id TEXT, title TEXT, cwd TEXT, archived INTEGER, created_at INTEGER, updated_at INTEGER,"
                 " tokens_used INTEGER, source TEXT, model TEXT, agent_nickname TEXT, agent_role TEXT)")
    conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT" + (", status TEXT)" if with_status else ")"))
    epoch = lambda minutes: int((NOW - timedelta(minutes=minutes)).timestamp())  # noqa: E731
    conn.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)", (CODEX_ID, "Parent", str(stores.work), 0, epoch(60), epoch(1), 5, "cli", "gpt-6-luna", None, None))
    conn.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)", (CODEX_KID_A, "Revisar pruebas", str(stores.work), 0, epoch(25), epoch(3), 9, "subagent", None, "Hubble", "explorer"))
    conn.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)", (CODEX_KID_B, "Escribir parche", str(stores.work), 0, epoch(25), epoch(4), 77, "subagent", "gpt-6-sol", None, "worker"))
    conn.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)", (CODEX_KID_C, "Hilo viejo", str(stores.work), 0, epoch(300), epoch(200), None, "subagent", None, None, None))
    for kid, state in ((CODEX_KID_A, "open"), (CODEX_KID_B, "open"), (CODEX_KID_C, "closed")):
        conn.execute("INSERT INTO thread_spawn_edges VALUES (?,?" + (",?)" if with_status else ")"), (CODEX_ID, kid, state)[: 3 if with_status else 2])
    conn.commit()
    conn.close()


def test_codex_children_from_spawn_edges_with_their_rollouts(stores, services):
    make_codex_family(stores)
    answer = children(services, f"codex:{CODEX_ID}")
    found = by_id(answer)
    assert set(found) == {CODEX_KID_A, CODEX_KID_B, CODEX_KID_C}
    a, b, c = found[CODEX_KID_A], found[CODEX_KID_B], found[CODEX_KID_C]
    assert (a["status"], a["name"], a["label"], a["model"]) == ("testing", "Hubble · explorer", None, "gpt-6-luna")
    assert a["tokens"] == {"input": 1000, "output": 50, "cache_read": 400, "cache_write": None, "total": 1050}
    assert a["link_id"] == f"codex:{CODEX_KID_A}" and a["kind"] == "thread" and a["source"] == "codex"
    assert (b["status"], b["name"], b["label"], b["model"]) == ("done", "worker", None, "gpt-6-sol")
    assert (c["name"], c["label"]) == (None, "Hilo viejo")   # the title is only a last resort
    assert b["tokens"] == {"input": None, "output": None, "cache_read": None, "cache_write": None, "total": 77}
    assert c["status"] == "done" and c["tokens"] is None  # closed by its parent (edge status), no usage anywhere
    assert [x["id"] for x in answer["children"]] == [f"codex:{CODEX_KID_A}", f"codex:{CODEX_KID_B}", f"codex:{CODEX_KID_C}"]
    assert answer["counts"] == {"active": 1, "done": 2, "total": 3}
    assert SECRET not in json.dumps(answer)


def test_codex_without_the_edge_status_column_uses_the_rollout_and_the_clock(stores, services):
    make_codex_family(stores, with_status=False)
    found = by_id(children(services, f"codex:{CODEX_ID}"))
    assert found[CODEX_KID_C]["status"] == "stopped"      # 200 minutes quiet, nobody closed it
    assert found[CODEX_KID_B]["status"] == "done" and found[CODEX_KID_A]["status"] == "testing"


def test_codex_thread_without_children_or_unknown_thread(stores, services):
    write_codex(stores)
    assert children(services, f"codex:{CODEX_ID}")["children"] == []
    with pytest.raises(ServiceError) as raised:
        children(services, "codex:99999999-9999-4999-8999-999999999999")
    assert raised.value.status == 404


# --- Copilot -----------------------------------------------------------------------------------------------

def test_copilot_subagent_events_become_children_without_a_conversation_link(stores, services):
    write_copilot(stores)
    path = stores.copilot / "session-state" / COPILOT_ID / "events.jsonl"
    append(path, [
        {"type": "subagent.started", "timestamp": ago(50), "data": {"toolCallId": "call_done", "agentName": "explore",
                                                                  "agentDisplayName": "Explorador"}},
        {"type": "subagent.completed", "timestamp": ago(45), "data": {"toolCallId": "call_done", "model": "gpt-6-luna",
                                                                    "totalTokens": 4321}},
        {"type": "subagent.started", "timestamp": ago(20), "data": {"toolCallId": "call_bad", "agentName": "task"}},
        {"type": "subagent.failed", "timestamp": ago(18), "data": {"toolCallId": "call_bad", "error": SECRET}},
        {"type": "subagent.started", "timestamp": ago(8), "data": {"toolCallId": "call_live", "agentName": "task",
                                                                 "agentDescription": "Probar el parche"}},
        {"type": "tool.execution_start", "timestamp": ago(3), "data": {"toolCallId": "tc1", "parentToolCallId": "call_live",
                                                                     "toolName": "bash", "arguments": {"command": f"pytest {SECRET}"}}},
        {"type": "subagent.started", "timestamp": ago(30), "data": {"toolCallId": "call_idle", "agentName": "task"}},
        {"type": "user.message", "timestamp": ago(1), "data": {"content": SECRET}},
    ])
    answer = children(services, f"copilot:{COPILOT_ID}")
    found = by_id(answer)
    assert {k: v["status"] for k, v in found.items()} == {"call_done": "done", "call_bad": "stopped",
                                                          "call_live": "testing", "call_idle": "idle"}
    done = found["call_done"]
    assert (done["name"], done["label"], done["model"], done["link_id"]) == ("explore", "Explorador", "gpt-6-luna", None)
    assert done["tokens"] == {"input": None, "output": None, "cache_read": None, "cache_write": None, "total": 4321}
    assert done["id"] == f"copilot:{COPILOT_ID}#call_done" and done["kind"] == "subagent"
    assert found["call_live"]["label"] == "Probar el parche" and found["call_live"]["tokens"] is None
    assert found["call_live"]["last_activity_at"] == ago(3) and found["call_live"]["started_at"] == ago(8)
    assert SECRET not in json.dumps(answer)


def test_copilot_session_without_history_or_events(stores, services):
    write_copilot(stores)
    assert children(services, f"copilot:{COPILOT_ID}")["children"] == []
    (stores.copilot / "session-state" / COPILOT_ID / "events.jsonl").unlink()
    answer = children(services, f"copilot:{COPILOT_ID}")
    assert answer["children"] == [] and answer["warnings"] == ["no_local_history"]


# --- framework runs ----------------------------------------------------------------------------------------

def set_event_times(services, run_id: str, minutes_ago: dict[tuple[str, str], float]) -> None:
    """Pin the timestamps of step events: ``{(event type, step id): minutes before NOW}``."""
    with services.system.store.db.connect() as conn:
        for row in conn.execute("SELECT id,event_type,payload_json FROM run_events WHERE run_id=?", (run_id,)).fetchall():
            step = json.loads(row["payload_json"]).get("step_id")
            if (row["event_type"], step) in minutes_ago:
                conn.execute("UPDATE run_events SET ts=? WHERE id=?", (ago(minutes_ago[(row["event_type"], step)]), row["id"]))
        conn.commit()


def set_run_status(services, run_id: str, status: str, waiting_step: str = "") -> None:
    with services.system.store.db.connect() as conn:
        conn.execute("UPDATE runs SET status=?, waiting_step=? WHERE id=?", (status, waiting_step, run_id))
        conn.commit()


def framework_run(services, model: str = "m1") -> str:
    run_id = add_framework_run(services, "alfa", events=0, model=model)
    log = services.system.db.log_event
    log(run_id, "step_started", {"step_id": "s0", "attempt": 1})
    log(run_id, "model_selected", {"step_id": "s0", "model": "ignored-by-telemetry"})
    log(run_id, "step_completed", {"step_id": "s0", "attempt": 1})
    log(run_id, "step_skipped", {"step_id": "skipme", "condition": "requires_web"})
    log(run_id, "step_started", {"step_id": "s1", "attempt": 1})
    log(run_id, "human_step_requested", {"step_id": "s2", "attempt": 1})
    log(run_id, "human_step_submitted", {"step_id": "s2", "provider": "x", "model": "gpt-hand"})
    log(run_id, "run_started", {})
    set_event_times(services, run_id, {("step_started", "s0"): 12, ("step_completed", "s0"): 10, ("step_started", "s1"): 5,
                                       ("human_step_requested", "s2"): 40, ("human_step_submitted", "s2"): 39})
    return run_id


def test_framework_steps_are_children_with_models_tokens_and_status(services):
    model = services.system.catalog.models["local_default"].model
    run_id = framework_run(services, model=model)
    set_run_status(services, run_id, "running")
    answer = children(services, f"framework:{run_id}")
    found = by_id(answer)
    assert set(found) == {"s0", "s1", "s2"}   # the skipped step never ran
    s0, s1, s2 = found["s0"], found["s1"], found["s2"]
    assert (s0["status"], s0["model"], s0["level"], s0["kind"], s0["source"]) == ("done", model, "T1", "step", "framework")
    assert s0["tokens"] == {"input": 17, "output": 4, "cache_read": None, "cache_write": None}
    assert s0["started_at"] == ago(12) and s0["last_activity_at"] == ago(10)
    assert (s1["status"], s1["model"], s1["tokens"], s1["level"]) == ("running", None, None, None)
    assert (s2["status"], s2["model"]) == ("idle", "gpt-hand")   # 40 minutes quiet while the run is running
    assert all(child["link_id"] is None and child["name"] == key for key, child in found.items())
    assert answer["counts"] == {"active": 2, "done": 1, "total": 3}


@pytest.mark.parametrize("run_status, waiting, expected, note", [
    ("failed", "", "stopped", None), ("cancelled", "", "stopped", None), ("rejected", "", "stopped", None),
    ("waiting_human", "s1", "idle", "waiting_human"), ("waiting_human", "other", "running", None),
    ("completed", "", "done", None), ("queued", "", "running", None),
])
def test_framework_step_status_follows_the_run_state(services, run_status, waiting, expected, note):
    run_id = framework_run(services)
    set_run_status(services, run_id, run_status, waiting)
    s1 = by_id(children(services, f"framework:{run_id}"))["s1"]
    assert (s1["status"], s1["note"]) == (expected, note)


def test_framework_unknown_run_and_disabled_source(stores):
    svc = make_services(stores, gui_session_sources=["claude"])
    try:
        run_id = add_framework_run(svc)
        with pytest.raises(ServiceError) as raised:
            svc.sessions.list_children(f"framework:{run_id}")
        assert raised.value.status == 404
    finally:
        svc.runs.close()
    svc = make_services(stores)
    try:
        with pytest.raises(ServiceError) as raised:
            svc.sessions.list_children("framework:nope")
        assert raised.value.status == 404
    finally:
        svc.runs.close()


# --- the service answer ------------------------------------------------------------------------------------

def test_status_filter_counts_and_version(stores, services):
    for agent, records in claude_children(stores).items():
        write_agent(stores, agent, records)
    session = f"claude:{CLAUDE_ID}"
    every, active, done = (children(services, session, status=s) for s in (None, "active", "done"))
    assert {c["status"] for c in active["children"]} <= {"running", "testing", "idle"}
    assert {c["status"] for c in done["children"]} <= {"done", "stopped", "unknown"}
    assert len(active["children"]) + len(done["children"]) == len(every["children"]) == 8
    assert every["counts"] == active["counts"] == done["counts"] == {"active": 5, "done": 3, "total": 8}
    assert every["version"] == active["version"] == done["version"] and len(every["version"]) == 12
    assert children(services, session, status="")["counts"]["total"] == 8
    stamps = [c["last_activity_at"] for c in every["children"]]
    assert stamps == sorted(stamps, reverse=True)                       # most recent activity first
    later = children(services, session, now=NOW + timedelta(hours=2))   # everyone is quiet now
    assert later["version"] != every["version"] and later["counts"]["active"] == 0


def test_the_version_changes_only_when_a_child_appears_or_changes_status(stores, services):
    write_claude(stores)
    write_agent(stores, "one", [prompt(ago(2)), assistant(ago(1), [tool_call("t", "ls")], "m")])
    session = f"claude:{CLAUDE_ID}"
    first = children(services, session)["version"]
    append(write_agent(stores, "one", [prompt(ago(2)), assistant(ago(1), [tool_call("t", "ls")], "m")]),
           [assistant(ago(0.5), [tool_call("u", "ls")], "n")])
    assert children(services, session)["version"] == first              # more activity, same status
    write_agent(stores, "two", [prompt(ago(2)), assistant(ago(1), [tool_call("t", "ls")], "m")])
    assert children(services, session)["version"] != first              # a new child


def test_errors_are_the_services_usual_ones(stores, services):
    write_claude(stores)
    session = f"claude:{CLAUDE_ID}"
    for bad, status, code in (("nonsense", 400, "invalid_session_id"), ("claude:not-a-uuid", 400, "invalid_session_id"),
                              ("claude:11111111-1111-4111-8111-999999999999", 404, "not_found")):
        with pytest.raises(ServiceError) as raised:
            children(services, bad)
        assert (raised.value.status, raised.value.code) == (status, code)
    for bad in ("all", "ACTIVE", "running", 7):
        with pytest.raises(ServiceError) as raised:
            children(services, session, status=bad)
        assert (raised.value.status, raised.value.code) == (422, "invalid_request")


def test_a_session_outside_the_allowed_folders_has_no_children_list(stores, services):
    write_claude(stores, cwd=stores.other)
    write_agent(stores, "a", [prompt(ago(1))])
    with pytest.raises(ServiceError) as raised:
        children(services, f"claude:{CLAUDE_ID}")
    assert raised.value.status == 404


def test_a_source_that_is_switched_off_has_no_children_list(stores):
    svc = make_services(stores, gui_session_sources=["codex"])
    try:
        write_claude(stores)
        with pytest.raises(ServiceError) as raised:
            svc.sessions.list_children(f"claude:{CLAUDE_ID}")
        assert raised.value.status == 404
    finally:
        svc.runs.close()


def test_a_long_list_is_cut_to_the_most_recent_with_a_warning(stores, services, monkeypatch):
    from multiagent.sessions import children as children_module, registry

    monkeypatch.setattr(registry, "MAX_CHILDREN", 3)
    assert children_module.MAX_CHILDREN == 500
    write_claude(stores)
    for number in range(5):
        write_agent(stores, f"n{number}", [prompt(ago(20 - number)), assistant(ago(10 - number), [tool_call("t", "ls")], "m")])
    answer = children(services, f"claude:{CLAUDE_ID}")
    assert len(answer["children"]) == 3 and answer["warnings"][0].startswith("children_truncated")
    assert answer["children"][0]["id"].endswith("agent-n4")


# --- the route ---------------------------------------------------------------------------------------------

@pytest.fixture
def world(stores):
    services = make_services(stores)
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, stores=stores, auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


def live_children(stores) -> None:
    """Subagents relative to the real clock (the route cannot take a fixed one)."""
    write_claude(stores)
    real = datetime.now(UTC)
    write_agent(stores, "live", [prompt(ago(5, real)), assistant(ago(1, real), [tool_call("t", "ls")], "m")],
                meta={"agentType": "Explore", "description": "En marcha"})
    write_agent(stores, "gone", [prompt(ago(300, real)), assistant(ago(200, real), [tool_call("t", "ls")], "m")])


def test_route_needs_the_token(world):
    live_children(world.stores)
    url = f"/gui/api/sessions/claude:{CLAUDE_ID}/children"
    assert world.client.get(url).status_code == 401
    assert world.client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert world.client.get(url, headers=world.auth).status_code == 200


def test_route_lists_filters_and_counts(world):
    live_children(world.stores)
    url = f"/gui/api/sessions/claude:{CLAUDE_ID}/children"
    whole = world.client.get(url, headers=world.auth).json()
    assert set(whole) == {"session_id", "children", "counts", "version", "warnings"}
    assert whole["counts"] == {"active": 1, "done": 1, "total": 2}
    active = world.client.get(url, params={"status": "active"}, headers=world.auth).json()
    done = world.client.get(url, params={"status": "done"}, headers=world.auth).json()
    assert [c["status"] for c in active["children"]] == ["running"] and [c["status"] for c in done["children"]] == ["stopped"]
    assert active["children"][0]["label"] == "En marcha" and active["counts"] == whole["counts"]


def test_route_errors(world):
    live_children(world.stores)
    get = lambda path, **kw: world.client.get(f"/gui/api/sessions/{path}/children", headers=world.auth, **kw)  # noqa: E731
    assert get(f"claude:{CLAUDE_ID}", params={"status": "bogus"}).status_code == 422
    assert get("nonsense").status_code == 400
    assert get("claude:11111111-1111-4111-8111-999999999999").status_code == 404
    assert get("framework:nope").status_code == 404
    body = get(f"claude:{CLAUDE_ID}", params={"status": "bogus"}).json()
    assert body["detail"]["code"] == "invalid_request" and body["detail"]["details"]["field"] == "status"


def test_route_for_a_framework_run_and_codex_family(world):
    run_id = framework_run(world.services)
    answer = world.client.get(f"/gui/api/sessions/framework:{run_id}/children", headers=world.auth).json()
    assert {c["name"] for c in answer["children"]} == {"s0", "s1", "s2"}
    make_codex_family(world.stores)
    codex = world.client.get(f"/gui/api/sessions/codex:{CODEX_ID}/children", headers=world.auth).json()
    assert codex["counts"]["total"] == 3 and all(c["source"] == "codex" for c in codex["children"])


def test_route_does_not_leak_content_or_paths(world):
    for agent, records in claude_children(world.stores).items():
        write_agent(world.stores, agent, records, meta={"agentType": "General", "description": "Tarea"})
    raw = world.client.get(f"/gui/api/sessions/claude:{CLAUDE_ID}/children", headers=world.auth).text
    assert SECRET not in raw and str(world.stores.tmp) not in raw and "subagents" not in raw


# --- the lightweight "children" stream ---------------------------------------------------------------------

def run_stream(read, **timing) -> list[str]:
    async def collect() -> list[str]:
        return [chunk async for chunk in events.stream_children(read, **timing)]

    return asyncio.run(collect())


def scripted(answers: list[dict]):
    """A ``read`` that gives the answers in turn and then fails like a vanished session (which ends the stream)."""
    queue = iter(answers)

    def read() -> dict:
        try:
            return next(queue)
        except StopIteration:
            raise ServiceError("not_found", "Session not found", status=404) from None

    return read


def test_children_stream_sends_the_version_on_connect_and_on_every_change():
    one = {"version": "v1", "counts": {"active": 1, "done": 0, "total": 1}}
    two = {"version": "v2", "counts": {"active": 0, "done": 1, "total": 1}}
    chunks = run_stream(scripted([one, one, two, two]), interval=0.001, heartbeat=30, max_seconds=30)
    assert chunks[0] == ": connected\n\n"
    sent = [c for c in chunks if c.startswith("event: children")]
    assert len(sent) == 2                                                # connect (v1) and the change (v2); a repeat is silent
    assert all("\nid:" not in c and not c.startswith("id:") for c in sent)  # no id: the client's Last-Event-ID is untouched
    assert json.loads(sent[0].split("data: ", 1)[1]) == {"version": "v1", "counts": one["counts"]}
    assert json.loads(sent[1].split("data: ", 1)[1]) == {"version": "v2", "counts": two["counts"]}
    assert chunks[-1].startswith("event: error") and len(chunks) == 4    # then the scripted session "vanishes"


def test_children_stream_heartbeats_between_changes_and_reports_a_vanished_session():
    same = {"version": "v1", "counts": {}}
    # every poll comes later than the heartbeat period, so each silent poll is a heartbeat
    chunks = run_stream(scripted([same, same, same]), interval=0.02, heartbeat=0.005, max_seconds=30)
    assert chunks[0] == ": connected\n\n" and chunks[1].startswith("event: children")
    assert chunks[2:4] == [": heartbeat\n\n", ": heartbeat\n\n"]
    assert chunks[4].startswith("event: error") and "not_found" in chunks[4] and len(chunks) == 5
    assert run_stream(scripted([]), interval=0.01, heartbeat=1, max_seconds=30)[1].startswith("event: error")


def test_children_stream_route(world):
    live_children(world.stores)
    url = "/gui/api/stream"
    text = world.client.get(url, params={"session": f"claude:{CLAUDE_ID}", "only": "children"}, headers=world.auth).text
    assert text.startswith(": connected")
    block = next(b for b in text.split("\n\n") if b.startswith("event: children"))
    data = json.loads(block.split("data: ", 1)[1])
    whole = world.client.get(f"/gui/api/sessions/claude:{CLAUDE_ID}/children", headers=world.auth).json()
    assert data == {"version": whole["version"], "counts": whole["counts"]}
    # no items travel on this stream
    assert "event: item" not in text and SECRET not in text


def test_children_stream_route_validates_up_front(world):
    live_children(world.stores)
    stream = lambda **params: world.client.get("/gui/api/stream", params=params, headers=world.auth)  # noqa: E731
    assert world.client.get("/gui/api/stream", params={"session": f"claude:{CLAUDE_ID}", "only": "children"}).status_code == 401
    assert stream(only="children").status_code == 422
    assert stream(session=f"claude:{CLAUDE_ID}", only="items").status_code == 422
    assert stream(session="claude:11111111-1111-4111-8111-999999999999", only="children").status_code == 404
    assert stream(session="nonsense", only="children").status_code == 400
    # the existing streams are unchanged
    assert "event: item" in stream(session=f"claude:{CLAUDE_ID}").text


def test_children_stream_route_for_a_framework_run(world):
    run_id = framework_run(world.services)
    text = world.client.get("/gui/api/stream", params={"session": f"framework:{run_id}", "only": "children"},
                            headers=world.auth).text
    block = next(b for b in text.split("\n\n") if b.startswith("event: children"))
    assert json.loads(block.split("data: ", 1)[1])["counts"]["total"] == 3


# --- the new client code (static) --------------------------------------------------------------------------

def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def used(text: str, name: str) -> set[str]:
    return set(re.findall(rf"\b{name}\.([a-z_]+)", text))


def test_new_files_exist_and_the_panel_lives_in_the_right_panel_slot_of_the_shell():
    for name in NEW_JS:
        assert (JS / name).is_file(), name
    assert (STATIC_DIR / "subagents-panel.css").is_file()
    # no import of its own and no column of its own: the «Subagentes» slot of the shell (GUI2-T02) mounts the panel
    assert [line for line in read("main.js").splitlines() if "subagents" in line] == []
    slots = read("panel-slots.js")
    assert 'import { SubagentsMount } from "./subagents-mount.js"' in slots and "<${SubagentsMount}" in slots
    assert 'id="subagents-slot"' in slots and 'data-slot="subagents"' in slots
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script[^>]*>", html) == ['<script type="module" src="/gui/static/js/main.js">']
    assert re.findall(r'<link rel="stylesheet" href="([^"]+)">', html) == [
        "/gui/static/app.css", "/gui/static/subagents-panel.css", "/gui/static/agent-tree.css",
        "/gui/static/panels.css"]   # agent-tree.css: GUI2-T08; panels.css: GUI2-T05


@pytest.mark.parametrize("name", NEW_JS)
def test_new_code_has_no_html_injection_eval_inline_handlers_or_remote_code(name):
    text = read(name)
    for pattern, label in FORBIDDEN:
        assert not re.search(pattern, text), f"{name}: {label}"
    assert "__html" not in text and "style=" not in text   # no inline styles either (CSP style-src 'self')


def test_the_new_code_depends_only_on_shared_helpers_never_on_the_session_views():
    allowed = {"api.js", "auth.js", "route.js", "stream.js", "text.js", "session-text.js", "help.js", *NEW_JS}
    for name in NEW_JS:
        imported = set(re.findall(r'from "\./([a-z-]+\.js)"', read(name)))
        assert imported <= allowed, (name, imported - allowed)
    assert "sessions.js" not in read("subagents-mount.js") and "session-detail.js" not in read("subagents-panel.js")


def test_panel_markup_is_accessible_and_keyboard_driven():
    panel, text = read("subagents-panel.js"), read("subagents-text.js")
    # the tabs are the shared Tabs component (GUI2-T11): the roles, aria attributes and keys live in ui/tabs.js
    tabs = (JS / "ui" / "tabs.js").read_text(encoding="utf-8")
    assert 'import { Tabs } from "./ui/tabs.js"' in panel and "<${Tabs}" in panel and 'helpFor("subagents.tabs")' in panel
    assert 'role="tablist"' in tabs and 'role="tab"' in tabs and 'role="tabpanel"' in tabs
    assert "aria-selected" in tabs and "aria-controls" in tabs and "tabindex" in tabs
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert f'"{key}"' in tabs
    assert 'aria-live="polite"' in panel and 'role="alert"' in panel and "aria-busy" in panel
    assert 'aria-hidden="true"' in panel                           # the avatar and the dot are decoration
    assert 'href=${href}' in panel and "aria-current" in panel     # items are real links; the open one is marked
    assert "<button" in panel and '"Ver " + hidden + " más"' in panel and "Ver menos" in panel and "PAGE_SIZE" in panel
    assert 'aria-expanded=${expanded[tab] ? "true" : "false"}' in panel   # the "Ver más" toggle says what it does
    assert re.search(r"PAGE_SIZE = 10;", text)


def test_status_is_never_colour_only_and_all_texts_are_spanish():
    text, panel = read("subagents-text.js"), read("subagents-panel.js")
    labels = (JS / "ui" / "labels.js").read_text(encoding="utf-8")
    # the words of the statuses are in the shared label map (GUI2-T11); the panel draws them with StatusChip, text next to the mark
    for label in ("Trabajando", "Ejecutando prueba", "Sin actividad", "Terminado", "Detenido", "Estado desconocido"):
        assert f'text: "{label}"' in labels, label
    for label in ("Activos", "Terminados", "Esperando a una persona"):
        assert label in text, label
    assert '<${StatusChip} status=${child.status} labels=${childLabels(child)} variant="inline" />' in panel
    chip = (JS / "ui" / "status-chip.js").read_text(encoding="utf-8")
    assert '<span class="status-text">${text}</span>' in chip and 'aria-hidden="true"' in chip   # the mark is decoration
    css = (STATIC_DIR / "subagents-panel.css").read_text(encoding="utf-8")
    sheet = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "min-height: 44px" in css and "prefers-reduced-motion" in sheet and ".mark.pulse" in sheet
    assert "subagents-dock" not in css and "has-subagents" not in css and "position: fixed" not in css   # one right panel only


def test_the_panel_opens_children_through_the_existing_route_scheme():
    panel, mount = read("subagents-panel.js"), read("subagents-mount.js")
    assert 'routeHref("sessions", child.link_id)' in panel and 'routeHref("runs", ' in panel
    assert 'only=children' in panel and "parseBlock" in panel and "setInterval(" in panel
    assert re.search(r"TICK_MS = (\d+)", panel) and int(re.search(r"TICK_MS = (\d+)", panel)[1]) >= 10000
    assert "export function SubagentsMount" in mount and "openId" in mount and "useListedSession" in mount
    assert "<${SubagentsPanel}" in mount and "currentId=${openId}" in mount
    assert "innerHTML" not in mount and "document.body" not in mount and "render(" not in mount   # no root or column of its own


def test_fields_read_by_the_client_exist_in_the_api_answers(world):
    for agent, records in claude_children(world.stores).items():
        write_agent(world.stores, agent, records, meta={"agentType": "General", "description": "Tarea"})
    answer = world.client.get(f"/gui/api/sessions/claude:{CLAUDE_ID}/children", headers=world.auth).json()
    panel, text = read("subagents-panel.js"), read("subagents-text.js")
    child_keys = set(answer["children"][0])
    assert used(panel, "child") <= child_keys and used(text, "child") <= child_keys
    assert used(panel, "result") <= set(answer) and used(panel, "counts") <= set(answer["counts"])
    token_keys = {"input", "output", "cache_read", "cache_write", "total"}
    assert used(text, "tokens") <= token_keys
    assert "version" in used(panel, "event")     # the stream's "children" event carries {version, counts}
    assert {"children", "counts", "version", "warnings"} >= used(panel, "data") - {"loaded"}


def test_new_client_files_and_the_stylesheet_are_served_with_the_csp(world):
    for name in NEW_JS:
        response = world.client.get(f"/gui/static/js/{name}")
        assert response.status_code == 200 and "javascript" in response.headers["content-type"]
        assert response.headers["content-security-policy"] == CSP
    css = world.client.get("/gui/static/subagents-panel.css")
    assert css.status_code == 200 and ".sub-item" in css.text and css.headers["content-security-policy"] == CSP


# --- the pure helpers, run under Node when it is installed -------------------------------------------------

NODE_SCRIPT = """
import { compactTokens, statusInfo, groupOf, initials, childTitle, detailText, activityText, countsText, childrenWarning,
  childLabels } from %s;
const now = Date.parse("2026-10-11T12:00:00Z");
const out = {
  tokens: [null, {}, { input: 1200, output: 300 }, { total: 2500000 }, { input: 5 }, { total: 12 }].map(compactTokens),
  status: ["running", "testing", "idle", "done", "stopped", "unknown", "weird"].map((s) => statusInfo(s).label),
  tone: ["running", "testing", "idle", "done"].map((s) => statusInfo(s).tone),
  note: statusInfo("idle", "waiting_human").label,
  groups: ["running", "testing", "idle", "done", "stopped", "unknown"].map(groupOf),
  titles: [
    childTitle({ id: "claude:u:agent-abcdef123", kind: "subagent", name: "Explore", label: "Revisar el lector" }),
    childTitle({ id: "claude:u:agent-abcdef123", kind: "subagent", name: "Explore", label: null }),
    childTitle({ id: "claude:u:agent-abcdef123", kind: "subagent", name: null, label: null }),
    childTitle({ id: "codex:4444", kind: "thread" }), childTitle({ id: "framework:r#s1", kind: "step" })],
  initials: [initials({ id: "x", kind: "step", name: "research", label: null, source: "framework" }),
    initials({ id: "x", kind: "subagent", name: "Explore", label: "Revisar el lector", source: "claude" }),
    initials({ id: "x", kind: "subagent", name: "---", label: null, source: "codex" })],
  detail: [detailText({ name: "Explore", label: "Revisar", model: "claude-sonnet-5-5", level: "T2" }),
    detailText({ name: null, label: null, model: null, level: null }),
    detailText({ name: "Explore", label: "Revisar", model: "claude-sonnet-5-5", level: "T2" }, false)],
  labels: [childLabels({ status: "idle", note: null }), Object.keys(childLabels({ status: "idle", note: "waiting_human" })),
    childLabels({ status: "idle", note: "waiting_human" }).idle.text],
  activity: activityText({ last_activity_at: "2026-10-11T11:56:00Z", tokens: { input: 10, output: 5 } }, now),
  activityNone: activityText({ last_activity_at: null, tokens: null }, now),
  counts: [countsText({ active: 1, done: 0 }), countsText({ active: 2, done: 1 }), countsText(null)],
  warning: [childrenWarning("children_truncated: only the 500 most recent are listed"), childrenWarning("no_local_history")],
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_text_helpers_under_node(tmp_path):
    # Node needs a module extension; copy the modules the helper imports (they import each other) with their imports renamed.
    (tmp_path / "ui").mkdir()
    for name in ("subagents-text.js", "session-text.js", "ui/labels.js"):
        source = re.sub(r'(from "\./[^"]+)\.js"', lambda match: match[1] + '.mjs"', read(name))
        (tmp_path / name.replace(".js", ".mjs")).write_text(source, encoding="utf-8")
    script = tmp_path / "run.mjs"
    script.write_text(NODE_SCRIPT % json.dumps("./subagents-text.mjs"), encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out["tokens"] == ["tokens: desconocido", "tokens: desconocido", "1,5 k tokens", "2,5 M tokens", "5 tokens", "12 tokens"]
    assert out["status"] == ["Trabajando", "Ejecutando prueba", "Sin actividad", "Terminado", "Detenido",
                             "Estado desconocido", "weird"]
    assert out["tone"] == ["success", "warning", "muted", "muted"] and out["note"] == "Esperando a una persona"   # tones of ui/labels.js
    assert out["groups"] == ["active", "active", "active", "done", "done", "done"]
    assert out["titles"] == ["Revisar el lector", "Explore", "Subagente abcdef12", "Hilo 4444", "Paso s1"]
    assert out["initials"] == ["R", "RE", "Co"]
    assert out["detail"] == ["Explore · claude-sonnet-5-5 · nivel T2", "modelo sin indicar", "Explore · claude-sonnet-5-5"]
    assert out["labels"] == ["subagent", ["idle"], "Esperando a una persona"]
    assert out["activity"].startswith("actividad hace 4 minutos") and out["activity"].endswith("15 tokens")
    assert out["activityNone"] == "sin hora registrada · tokens: desconocido"
    assert out["counts"] == ["1 activo y 0 terminados", "2 activos y 1 terminado", "0 activos y 0 terminados"]
    assert out["warning"][0].startswith("Solo se muestran") and "historial" in out["warning"][1]
