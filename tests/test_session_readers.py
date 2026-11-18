# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI-T03 session readers: synthetic fixtures built in tmp_path only (never the real user folders)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from multiagent.sessions import (
    InvalidCursor,
    InvalidSessionId,
    SessionFilters,
    SessionNotFound,
    SessionRegistry,
    SessionsConfig,
    decode_cursor,
)
from multiagent.sessions import _common

CLAUDE_ID = "11111111-1111-4111-8111-111111111111"
CLAUDE_OLD_ID = "11111111-1111-4111-8111-aaaaaaaaaaaa"
CODEX_ID = "22222222-2222-4222-8222-222222222222"
CODEX_CHILD = "22222222-2222-4222-8222-333333333333"
COPILOT_ID = "33333333-3333-4333-8333-333333333333"
COPILOT_NOEV = "33333333-3333-4333-8333-444444444444"


def jl(path: Path, records: list, raw: list[bytes] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        for rec in records:
            fh.write((rec if isinstance(rec, str) else json.dumps(rec)).encode() + b"\n")
        for chunk in raw or []:
            fh.write(chunk)
    return path


@pytest.fixture()
def env(tmp_path):
    work = tmp_path / "work" / "proj"
    work.mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    cfg = SessionsConfig(
        allowed_roots=(str(tmp_path / "work"),),
        claude_home=tmp_path / "claude", codex_home=tmp_path / "codex", copilot_home=tmp_path / "copilot",
        claude_desktop_dir=tmp_path / "desktop")
    return {"tmp": tmp_path, "work": work, "other": other, "cfg": cfg}


def reg(env) -> SessionRegistry:
    return SessionRegistry(env["cfg"])


# ---------------- Claude ----------------
def claude_file(env, sid=CLAUDE_ID, cwd=None, extra=None) -> Path:
    cwd = str(cwd or env["work"])
    recs = [
        {"type": "file-history-snapshot", "snapshot": {}},
        {"type": "user", "uuid": "u1", "sessionId": sid, "cwd": cwd, "entrypoint": "sdk-cli", "version": "2.1.0",
         "timestamp": "2026-10-01T10:00:00.000Z", "message": {"role": "user", "content": "hola <b>mundo</b> <script>x()</script>"}},
        {"type": "assistant", "uuid": "a1", "sessionId": sid, "timestamp": "2026-10-01T10:00:05.000Z",
         "message": {"id": "m1", "role": "assistant", "model": "claude-opus-x", "content": [
             {"type": "thinking", "thinking": "secreto interno", "signature": "s"},
             {"type": "text", "text": "respuesta"},
             {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "a.py", "new": "x" * 5000}}],
             "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2}}},
        {"type": "assistant", "uuid": "a1b", "sessionId": sid, "timestamp": "2026-10-01T10:00:06.000Z",
         "message": {"id": "m1", "role": "assistant", "model": "claude-opus-x", "content": [{"type": "text", "text": "segunda parte"}],
                     "usage": {"input_tokens": 10, "output_tokens": 7, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2}}},
        {"type": "user", "uuid": "u2", "sessionId": sid, "timestamp": "2026-10-01T10:00:07.000Z",
         "toolUseResult": {"agentId": "abc123"},
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        {"type": "custom-title", "customTitle": "Titulo CLI"},
    ]
    recs += extra or []
    return jl(env["cfg"].claude_home / "projects" / "proj" / f"{sid}.jsonl", recs)


def test_claude_happy_path(env):
    claude_file(env)
    r = reg(env)
    page = r.list_sessions()
    assert [s.id for s in page.sessions] == [f"claude:{CLAUDE_ID}"]
    s = page.sessions[0]
    assert s.title == "Titulo CLI" and s.surface.value == "cli" and s.models == ["claude-opus-x"]
    assert s.created_at == "2026-10-01T10:00:00Z" and s.updated_at.endswith("Z")
    # tokens deduplicated by message.id (last record of m1 wins)
    assert s.tokens.to_dict() == {"input": 10, "output": 7, "cache_read": 3, "cache_write": 2}
    assert s.open_in_app.to_dict() == {"kind": "command", "value": ["claude", "--desktop", "--resume", CLAUDE_ID],
                                       "needs_confirmation": False}
    items = r.read_items(s.id).items
    kinds = [i.kind.value for i in items]
    assert kinds == ["user_message", "reasoning_omitted", "assistant_message", "tool_call", "assistant_message",
                     "tool_result", "subagent"]
    assert items[3].tool["name"] == "Edit" and items[3].files == ["a.py"] and len(items[3].tool["summary"]) <= 500
    assert items[6].child_id == f"claude:{CLAUDE_ID}:agent-abc123"
    assert items[0].timestamp == "2026-10-01T10:00:00Z"


def test_claude_tokens_null_when_unknown(env):
    jl(env["cfg"].claude_home / "projects" / "p" / f"{CLAUDE_ID}.jsonl", [
        {"type": "user", "cwd": str(env["work"]), "timestamp": "2026-10-01T10:00:00Z",
         "message": {"role": "user", "content": "x"}}])
    s = reg(env).list_sessions().sessions[0]
    assert s.tokens is None and s.title is None and s.models == []


def test_reasoning_omitted_has_no_text(env):
    claude_file(env)
    items = reg(env).read_items(f"claude:{CLAUDE_ID}").items
    r = items[1]
    assert r.kind.value == "reasoning_omitted" and r.text is None
    assert "secreto" not in json.dumps([i.to_dict() for i in items])


def test_html_text_returned_literally(env):
    claude_file(env)
    first = reg(env).read_items(f"claude:{CLAUDE_ID}").items[0]
    assert first.text == "hola <b>mundo</b> <script>x()</script>"


def test_text_truncated_at_20000(env):
    claude_file(env, extra=[{"type": "assistant", "timestamp": "2026-10-01T10:01:00Z",
                              "message": {"id": "big", "content": [{"type": "text", "text": "z" * 30000}]}}])
    items = reg(env).read_items(f"claude:{CLAUDE_ID}").items
    big = items[-1]
    assert len(big.text) == 20000 and big.truncated is True and items[0].truncated is False


def test_unknown_type_and_broken_and_oversized(env):
    path = claude_file(env, extra=[{"type": "brand-new-thing", "x": 1}])
    with open(path, "ab") as fh:
        fh.write(b"{not json\n")
        fh.write(b'{"type":"assistant","message":{"id":"o","content":[{"type":"text","text":"' + b"y" * (1_100_000) + b'"}]}}\n')
        fh.write(json.dumps({"type": "user", "timestamp": "2026-10-01T10:02:00Z",
                             "message": {"role": "user", "content": "despues"}}).encode() + b"\n")
    r = reg(env)
    page = r.read_items(f"claude:{CLAUDE_ID}", limit=100)
    assert page.items[-1].text == "despues"
    unk = [i for i in page.items if i.kind.value == "unknown"]
    assert len(unk) == 1 and unk[0].text is None
    joined = " ".join(page.warnings)
    assert "unknown_record_types: 1" in joined and "broken_json_lines: 1" in joined and "oversized_lines_skipped: 1" in joined
    assert page.done is True
    # warnings also summarized on the session
    assert any("oversized" in w for w in r.get_session(f"claude:{CLAUDE_ID}").warnings)


def test_truncated_between_reads_resets_cursor(env):
    path = claude_file(env)
    r = reg(env)
    page = r.read_items(f"claude:{CLAUDE_ID}")
    jl(path, [{"type": "user", "timestamp": "2026-10-01T11:00:00Z", "cwd": str(env["work"]),
               "message": {"role": "user", "content": "nuevo"}}])
    again = r.read_items(f"claude:{CLAUDE_ID}", after=page.next_cursor)
    assert any("cursor_reset" in w for w in again.warnings)
    assert [i.text for i in again.items] == ["nuevo"]


def test_partial_last_line_not_consumed(env):
    path = claude_file(env)
    size = path.stat().st_size
    with open(path, "ab") as fh:
        fh.write(b'{"type":"user","message":{"role":"user","content":"incompl')
    page = reg(env).read_items(f"claude:{CLAUDE_ID}")
    assert page.next_cursor == f"0:{size}" and page.done is False and page.warnings == []


def test_pagination_with_cursors(env):
    claude_file(env)
    r = reg(env)
    sid = f"claude:{CLAUDE_ID}"
    seen, after = [], None
    for _ in range(20):
        page = r.read_items(sid, after=after, limit=2)
        seen += [i.kind.value for i in page.items]
        after = page.next_cursor
        if page.done:
            break
    assert seen == [i.kind.value for i in r.read_items(sid, limit=100).items]
    assert r.read_items(sid, after=after).items == []
    assert decode_cursor(after) [0] == 0
    for bad in ("abc", "1", "0:-1", "5:3", "0:1:2"):
        with pytest.raises(InvalidCursor):
            r.read_items(sid, after=bad)


def test_session_outside_allowed_roots_hidden(env):
    other_id = "11111111-1111-4111-8111-bbbbbbbbbbbb"
    claude_file(env)
    claude_file(env, sid=other_id, cwd=env["other"])
    r = reg(env)
    assert [s.native_id for s in r.list_sessions().sessions] == [CLAUDE_ID]
    with pytest.raises(SessionNotFound):
        r.get_session(f"claude:{other_id}")
    with pytest.raises(SessionNotFound):
        r.read_items(f"claude:{other_id}")


def test_no_allowed_roots_lists_nothing(env):
    claude_file(env)
    cfg = SessionsConfig(allowed_roots=(), claude_home=env["cfg"].claude_home, codex_home=env["cfg"].codex_home,
                         copilot_home=env["cfg"].copilot_home)
    page = SessionRegistry(cfg).list_sessions()
    assert page.sessions == [] and page.warnings and "no_allowed_roots" in page.warnings[0]


def test_prefix_sibling_folder_is_not_inside_root(env):
    sib = env["tmp"] / "work-evil"
    sib.mkdir()
    claude_file(env, cwd=sib)
    assert reg(env).list_sessions().sessions == []


def test_claude_desktop_link_by_prior_session_id(env):
    claude_file(env)
    d = env["cfg"].claude_desktop_dir / "acct" / "ws"
    d.mkdir(parents=True)
    (d / "local_abc.json").write_text(json.dumps({
        "cliSessionId": "11111111-1111-4111-8111-cccccccccccc", "priorCliSessionIds": [CLAUDE_ID],
        "title": "Titulo Desktop", "isArchived": True, "model": "claude-desk-model"}), encoding="utf-8")
    s = reg(env).get_session(f"claude:{CLAUDE_ID}")
    assert s.title == "Titulo Desktop" and s.surface.value == "desktop" and s.status.value == "archived"
    assert s.models == ["claude-opus-x", "claude-desk-model"]


def test_claude_subagent_linking(env):
    main = claude_file(env)
    sub = main.parent / CLAUDE_ID / "subagents" / "agent-abc123.jsonl"
    jl(sub, [{"type": "user", "sessionId": CLAUDE_ID, "isSidechain": True, "timestamp": "2026-10-01T10:00:08Z",
              "message": {"role": "user", "content": "tarea hija"}}])
    r = reg(env)
    by_id = {s.id: s for s in r.list_sessions().sessions}
    parent, child = by_id[f"claude:{CLAUDE_ID}"], by_id[f"claude:{CLAUDE_ID}:agent-abc123"]
    assert parent.child_count == 1 and child.parent_id == parent.id and child.open_in_app is None
    assert child.cwd == parent.cwd  # inherited when the subagent file has none
    assert r.read_items(child.id).items[0].text == "tarea hija"
    # the child_id in the parent's items is a resolvable session id
    link = [i for i in r.read_items(parent.id).items if i.kind.value == "subagent"][0]
    assert link.child_id == child.id and r.get_session(link.child_id).id == child.id


def test_invalid_ids(env):
    claude_file(env)
    r = reg(env)
    for bad in ("", "nocolon", "evil:x", "claude:", "frameworkX:1"):
        with pytest.raises(InvalidSessionId):
            r.get_session(bad)
    for bad in ("claude:../../etc/passwd", "claude:not-a-uuid", f"claude:{CLAUDE_ID}:agent-../x", f"claude:{CLAUDE_ID}:zzz"):
        with pytest.raises(SessionNotFound):
            r.get_session(bad)


# ---------------- Codex ----------------
def make_codex(env, lock=False):
    home = env["cfg"].codex_home
    rollout = home / "sessions" / "2026" / "10" / "01" / f"rollout-2026-10-01T10-00-00-{CODEX_ID}.jsonl"
    jl(rollout, [
        {"timestamp": "2026-10-01T10:00:00Z", "type": "session_meta", "payload": {"id": CODEX_ID, "cwd": str(env["work"]),
                                                                              "originator": "codex_cli_rs", "cli_version": "0.99"}},
        {"timestamp": "2026-10-01T10:00:01Z", "type": "turn_context", "payload": {"model": "gpt-test"}},
        {"timestamp": "2026-10-01T10:00:02Z", "type": "response_item", "payload": {"type": "message", "role": "user",
                                                                              "content": [{"type": "input_text", "text": "<i>hola</i>"}]}},
        {"timestamp": "2026-10-01T10:00:03Z", "type": "response_item", "payload": {"type": "reasoning", "summary": [], "encrypted_content": "zzz"}},
        {"timestamp": "2026-10-01T10:00:04Z", "type": "response_item", "payload": {"type": "function_call", "name": "shell",
                                                                              "arguments": "{\"command\": [\"ls\"]}", "call_id": "c1"}},
        {"timestamp": "2026-10-01T10:00:05Z", "type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "listado"}},
        {"timestamp": "2026-10-01T10:00:06Z", "type": "response_item", "payload": {"type": "mystery_item"}},
        {"timestamp": "2026-10-01T10:00:07Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20, "total_tokens": 120}}}},
        {"timestamp": "2026-10-01T10:00:08Z", "type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                                              "content": [{"type": "output_text", "text": "listo"}]}},
        {"timestamp": "2026-10-01T10:00:09Z", "type": "compacted", "payload": {"message": "resumen"}},
        {"timestamp": "2026-10-01T10:00:10Z", "type": "new_top_level", "payload": {}},
    ])
    jl(home / "sessions" / "2026" / "10" / "01" / f"rollout-2026-10-01T11-00-00-{CODEX_CHILD}.jsonl", [
        {"timestamp": "2026-10-01T11:00:00Z", "type": "session_meta", "payload": {"cwd": str(env["work"])}}])
    db = home / "state_5.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE threads (id TEXT, title TEXT, cwd TEXT, archived INTEGER, created_at INTEGER, updated_at INTEGER, tokens_used INTEGER, source TEXT)")
    conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT)")
    conn.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)", (CODEX_ID, "Hilo Codex", str(env["work"]), 0, 1790000000, 1790000100000, 999, "cli"))
    conn.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)", (CODEX_CHILD, "Hijo", str(env["work"]), 1, 1790000200, 1790000300, None, "exec"))
    conn.execute("INSERT INTO thread_spawn_edges VALUES (?,?)", (CODEX_ID, CODEX_CHILD))
    conn.commit()
    if lock:
        return conn  # caller keeps it open with an exclusive lock
    conn.close()
    return None


def test_codex_happy_path(env):
    make_codex(env)
    r = reg(env)
    by_id = {s.id: s for s in r.list_sessions(SessionFilters(source="codex")).sessions}
    s, child = by_id[f"codex:{CODEX_ID}"], by_id[f"codex:{CODEX_CHILD}"]
    assert s.title == "Hilo Codex" and s.surface.value == "cli" and s.models == ["gpt-test"]
    assert s.created_at == "2026-10-14T16:53:20Z" or s.created_at.endswith("Z")  # epoch seconds normalized
    assert s.updated_at.endswith("Z") and s.updated_at.startswith("2026-")  # epoch ms normalized
    assert s.tokens.to_dict()["input"] == 100 and s.tokens.cache_read == 40 and s.tokens.cache_write is None
    assert s.open_in_app.to_dict() == {"kind": "url", "value": f"codex://threads/{CODEX_ID}", "needs_confirmation": False}
    assert s.child_count == 1 and child.parent_id == s.id and child.status.value == "archived"
    assert child.tokens is None
    items = r.read_items(s.id, limit=100)
    assert [i.kind.value for i in items.items] == ["user_message", "reasoning_omitted", "tool_call", "tool_result",
                                                   "unknown", "usage", "assistant_message", "compaction", "unknown"]
    assert items.items[0].text == "<i>hola</i>" and items.items[1].text is None
    assert items.items[2].tool["name"] == "shell" and items.items[2].model == "gpt-test"
    assert any("unknown_record_types: 2" in w for w in items.warnings)


def test_codex_without_index_uses_session_meta(env):
    make_codex(env)
    (env["cfg"].codex_home / "state_5.sqlite").unlink()
    s = reg(env).get_session(f"codex:{CODEX_ID}")
    assert s.cwd == str(env["work"]) and s.surface.value == "cli" and s.title is None


def test_codex_locked_sqlite_marks_source_unavailable(env):
    conn = make_codex(env, lock=True)
    conn.execute("BEGIN EXCLUSIVE")
    try:
        r = reg(env)
        page = r.list_sessions(SessionFilters(source="codex"))
        assert any("source_unavailable" in w for w in page.warnings)
        s = r.get_session(f"codex:{CODEX_ID}")  # falls back to the rollout file
        assert any("source_unavailable" in w for w in s.warnings) and s.title is None
        assert len(r.read_items(s.id).items) > 0
    finally:
        conn.rollback()
        conn.close()


def test_codex_connect_failure_monkeypatched(env, monkeypatch):
    make_codex(env)

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(_common.sqlite3, "connect", boom)
    page = reg(env).list_sessions(SessionFilters(source="codex"))
    assert any("source_unavailable" in w for w in page.warnings)


def test_codex_db_opened_read_only(env):
    make_codex(env)
    conn = _common.open_ro(env["cfg"].codex_home / "state_5.sqlite")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM threads")
    conn.close()


# ---------------- Copilot ----------------
def make_copilot(env):
    home = env["cfg"].copilot_home
    d = home / "session-state" / COPILOT_ID
    d.mkdir(parents=True)
    (d / "workspace.yaml").write_text(
        f"id: {COPILOT_ID}\nname: 'Nombre ws'\ncwd: {env['work']}\ncreated_at: 2026-10-01T09:00:00.000Z\n"
        "updated_at: 2026-10-01T09:30:00.000Z\nnested:\n  child: 1\n", encoding="utf-8")
    jl(d / "events.jsonl", [
        {"type": "session.start", "timestamp": "2026-10-01T09:00:00Z", "data": {"copilotVersion": "1.2.3", "selectedModel": "gpt-cop"}},
        {"type": "user.message", "timestamp": "2026-10-01T09:00:01Z", "data": {"content": "<h1>hola</h1>"}},
        {"type": "assistant.reasoning", "timestamp": "2026-10-01T09:00:02Z", "data": {"content": "pensando"}},
        {"type": "tool.execution_start", "timestamp": "2026-10-01T09:00:03Z", "data": {"toolName": "view", "toolCallId": "k1", "arguments": {"path": "x.py"}}},
        {"type": "tool.execution_complete", "timestamp": "2026-10-01T09:00:04Z", "data": {"toolCallId": "k1", "result": {"content": "contenido"}}},
        {"type": "subagent.started", "timestamp": "2026-10-01T09:00:05Z", "data": {"toolCallId": "sub1", "agentName": "explore"}},
        {"type": "assistant.message", "timestamp": "2026-10-01T09:00:06Z", "data": {"content": "listo"}},
        {"type": "session.compaction_complete", "timestamp": "2026-10-01T09:00:07Z", "data": {"summaryContent": "resumen"}},
        {"type": "weird.event", "timestamp": "2026-10-01T09:00:08Z", "data": {}},
    ])
    d2 = home / "session-state" / COPILOT_NOEV
    d2.mkdir(parents=True)
    (d2 / "workspace.yaml").write_text(f"id: {COPILOT_NOEV}\ncwd: {env['work']}\n", encoding="utf-8")
    conn = sqlite3.connect(home / "data.db")
    conn.execute("CREATE TABLE sessions (id TEXT, title TEXT, session_type TEXT)")
    conn.execute("INSERT INTO sessions VALUES (?,?,?)", (COPILOT_ID, "Titulo DB", "cli_session"))
    conn.execute("INSERT INTO sessions VALUES (?,?,?)", (COPILOT_NOEV, "Sin eventos", "desktop_session"))
    conn.commit()
    conn.close()
    conn = sqlite3.connect(home / "session-store.db")
    conn.execute("CREATE TABLE assistant_usage_events (session_id TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER)")
    conn.executemany("INSERT INTO assistant_usage_events VALUES (?,?,?,?,?)", [(COPILOT_ID, 5, 2, 1, 0), (COPILOT_ID, 7, 3, 0, 0)])
    conn.commit()
    conn.close()


def test_copilot_happy_path(env):
    make_copilot(env)
    r = reg(env)
    by_id = {s.id: s for s in r.list_sessions(SessionFilters(source="copilot")).sessions}
    s, noev = by_id[f"copilot:{COPILOT_ID}"], by_id[f"copilot:{COPILOT_NOEV}"]
    assert s.title == "Titulo DB" and s.surface.value == "cli" and s.models == ["gpt-cop"]
    assert s.cwd == str(env["work"]) and s.created_at == "2026-10-01T09:00:00Z"
    assert s.tokens.to_dict() == {"input": 12, "output": 5, "cache_read": 1, "cache_write": 0}
    assert s.child_count == 1
    assert s.open_in_app.to_dict() == {"kind": "url", "value": f"ghapp://sessions/{COPILOT_ID}", "needs_confirmation": True}
    assert noev.surface.value == "desktop" and "no_local_history" in noev.warnings and noev.tokens is None
    assert r.read_items(noev.id).items == []
    page = r.read_items(s.id, limit=100)
    assert [i.kind.value for i in page.items] == ["user_message", "reasoning_omitted", "tool_call", "tool_result",
                                                  "subagent", "assistant_message", "compaction", "unknown"]
    assert page.items[0].text == "<h1>hola</h1>" and page.items[1].text is None
    assert page.items[2].files == ["x.py"] and page.items[4].child_id == "sub1"
    assert any("unknown_record_types: 1" in w for w in page.warnings)


def test_copilot_locked_database_warns(env, monkeypatch):
    make_copilot(env)
    conn = sqlite3.connect(env["cfg"].copilot_home / "data.db")
    conn.execute("BEGIN EXCLUSIVE")
    try:
        s = reg(env).get_session(f"copilot:{COPILOT_ID}")
        assert s.title == "Nombre ws" and any("source_unavailable" in w for w in s.warnings)
    finally:
        conn.rollback()
        conn.close()


def test_invalid_uuid_gives_no_open_in_app_and_not_found(env):
    make_copilot(env)
    r = reg(env)
    for src in ("copilot", "codex"):
        with pytest.raises(SessionNotFound):
            r.get_session(f"{src}:not-a-uuid")
    bad = "zzzzzzzz-1111-4111-8111-111111111111"
    (env["cfg"].copilot_home / "session-state" / bad).mkdir()
    assert all(s.native_id != bad for s in r.list_sessions().sessions)


def test_filters_and_sources_disabled(env):
    claude_file(env)
    make_codex(env)
    make_copilot(env)
    r = reg(env)
    assert {s.source.value for s in r.list_sessions().sessions} == {"claude", "codex", "copilot"}
    assert {s.source.value for s in r.list_sessions(SessionFilters(surface="desktop")).sessions} == {"copilot"}
    assert [s.source.value for s in r.list_sessions(SessionFilters(text="titulo cli")).sessions] == ["claude"]
    assert r.list_sessions(SessionFilters(cwd=str(env["other"]))).sessions == []
    assert r.list_sessions(SessionFilters(since="2999-01-01T00:00:00Z")).sessions == []
    only = SessionsConfig(allowed_roots=env["cfg"].allowed_roots, sources=("codex",), claude_home=env["cfg"].claude_home,
                          codex_home=env["cfg"].codex_home, copilot_home=env["cfg"].copilot_home)
    assert {s.source.value for s in SessionRegistry(only).list_sessions().sessions} == {"codex"}
    # list pagination
    p1 = r.list_sessions(SessionFilters(limit=2))
    p2 = r.list_sessions(SessionFilters(limit=2, after=p1.next_cursor))
    assert len(p1.sessions) == 2 and p1.next_cursor == "0:2" and len(p2.sessions) == 2 and p2.next_cursor == "0:4" and len(r.list_sessions(SessionFilters(limit=2, after="0:4")).sessions) == 1
    assert not {s.id for s in p1.sessions} & {s.id for s in p2.sessions}


def test_missing_stores_are_reported_not_fatal(env):
    page = reg(env).list_sessions()
    assert page.sessions == [] and sum("source_unavailable" in w for w in page.warnings) == 4


def test_timestamp_normalization():
    n = _common.normalize_ts
    assert n(1790000000) == n(1790000000000) == n("1790000000") == "2026-09-21T14:13:20Z"
    assert n("2026-10-01T12:00:00+02:00") == "2026-10-01T10:00:00Z"
    assert n("2026-10-01T10:00:00") == "2026-10-01T10:00:00Z" and n("garbage") is None and n(None) is None

def test_session_activity_is_allowlisted_and_reports_claude_test_tool(env):
    sid = "11111111-1111-4111-8111-111111111112"
    path = claude_file(env, sid=sid, extra=[{
        "type": "assistant", "timestamp": "2026-10-14T12:30:00Z",
        "message": {"content": [{"type": "tool_use", "id": "tool-test", "name": "Bash",
                                "input": {"command": "pytest tests/test_synthetic.py"}}]},
    }])
    registry = reg(env)

    metadata = registry.activity(f"claude:{sid}")

    assert metadata["open_tests"] is True
    assert metadata["source"] == "claude_transcript"
    assert metadata["last_output_at"] >= "2026-10-14T12:30:00Z"
    assert str(path) not in repr(metadata) and "pytest tests" not in repr(metadata)
    hidden = "11111111-1111-4111-8111-bbbbbbbbbbbb"
    claude_file(env, sid=hidden, cwd=env["other"])
    with pytest.raises(SessionNotFound):
        registry.activity(f"claude:{hidden}")
def test_codex_activity_reports_rollout_mtime_and_open_tests(env):
    make_codex(env)
    rollout = env["cfg"].codex_home / "sessions" / "2026" / "10" / "01" / f"rollout-2026-10-01T10-00-00-{CODEX_ID}.jsonl"
    with rollout.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"timestamp": "2026-10-14T12:00:00Z", "type": "response_item",
                                 "payload": {"type": "function_call", "call_id": "test-call",
                                             "arguments": "{\"command\":\"pytest tests/test_synthetic.py\"}"}}) + "\n")
    metadata = reg(env).activity(f"codex:{CODEX_ID}")
    assert metadata["open_tests"] is True
    assert metadata["source"] == "codex_rollout_mtime"
    assert metadata["last_output_at"] is not None
    assert str(rollout) not in repr(metadata) and "pytest tests" not in repr(metadata)


def test_copilot_activity_reports_root_test_calls_without_subagent_text(env):
    make_copilot(env)
    events = env["cfg"].copilot_home / "session-state" / COPILOT_ID / "events.jsonl"
    with events.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "tool.execution_start", "timestamp": "2026-10-14T12:00:00Z",
                                 "data": {"toolName": "shell", "toolCallId": "test-call",
                                          "arguments": {"command": "pytest tests/test_synthetic.py"}}}) + "\n")
    metadata = reg(env).activity(f"copilot:{COPILOT_ID}")
    assert metadata["open_tests"] is True
    assert metadata["source"] == "copilot_events_mtime"
    assert metadata["last_output_at"] is not None
    assert str(events) not in repr(metadata) and "pytest tests" not in repr(metadata)