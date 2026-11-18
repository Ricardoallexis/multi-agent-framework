# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Synthetic session stores for the GUI-T04 tests: built in ``tmp_path``, never the user's real folders."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from multiagent.config import Settings
from multiagent.contracts import SocialPostRequest
from multiagent.services import ApplicationServices

CLAUDE_ID = "11111111-1111-4111-8111-111111111111"
CLAUDE_OTHER = "11111111-1111-4111-8111-bbbbbbbbbbbb"
CODEX_ID = "22222222-2222-4222-8222-222222222222"
COPILOT_ID = "33333333-3333-4333-8333-333333333333"
AGY_ID = "44444444-4444-4444-8444-444444444444"
AGY_CHILD = "44444444-4444-4444-8444-555555555555"


@dataclass
class Stores:
    tmp: Path
    roots: Path      # the allowed folder (gui_session_roots)
    work: Path       # a project folder inside it
    other: Path      # a folder outside it
    claude: Path
    codex: Path
    copilot: Path
    desktop: Path
    antigravity: Path  # not created: a test that needs an agy store writes one with write_antigravity


def make_stores(tmp: Path) -> Stores:
    stores = Stores(tmp=tmp, roots=tmp / "work", work=tmp / "work" / "proj", other=tmp / "elsewhere",
                    claude=tmp / "claude", codex=tmp / "codex", copilot=tmp / "copilot", desktop=tmp / "desktop",
                    antigravity=tmp / "antigravity")
    for folder in (stores.work, stores.other):
        folder.mkdir(parents=True, exist_ok=True)
    return stores


def settings_for(stores: Stores, local: Path | None = None, **overrides) -> Settings:
    """Settings that point every store at ``tmp`` (so nothing real is ever read) and allow ``stores.roots``."""
    options = dict(
        _env_file=None, local_dir=local or stores.tmp / "local", worker_enabled=False, mock_mode=True,
        gemini_api_key="", openai_api_key="", openai_enabled=False,
        gui_token_file=stores.tmp / "gui-token", gui_heartbeat_seconds=0.05, gui_poll_seconds=0.02,
        gui_stream_max_seconds=0.4, gui_session_roots=[str(stores.roots)],
        gui_claude_home=stores.claude, gui_codex_home=stores.codex, gui_copilot_home=stores.copilot,
        gui_antigravity_home=stores.antigravity, gui_claude_desktop_dir=stores.desktop,
    )
    options.update(overrides)
    return Settings(**options)


def make_services(stores: Stores, **overrides) -> ApplicationServices:
    return ApplicationServices.create(settings_for(stores, **overrides), dry_run=True)


def jsonl(path: Path, records: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        for record in records:
            handle.write((record if isinstance(record, str) else json.dumps(record)).encode() + b"\n")
    return path


def append(path: Path, records: list) -> None:
    with open(path, "ab") as handle:
        for record in records:
            handle.write((record if isinstance(record, str) else json.dumps(record)).encode() + b"\n")


def claude_user(text: str, when: str, cwd: str | None = None) -> dict:
    record = {"type": "user", "timestamp": when, "message": {"role": "user", "content": text}}
    if cwd:
        record.update(cwd=cwd, entrypoint="sdk-cli", sessionId=CLAUDE_ID)
    return record


def write_claude(stores: Stores, session_id: str = CLAUDE_ID, cwd: Path | None = None, title: str = "Claude work") -> Path:
    where = str(cwd or stores.work)
    return jsonl(stores.claude / "projects" / "proj" / f"{session_id}.jsonl", [
        claude_user("hola <b>mundo</b> <script>x()</script>", "2026-10-01T10:00:00.000Z", where),
        {"type": "assistant", "timestamp": "2026-10-01T10:00:05.000Z", "message": {
            "id": "m1", "role": "assistant", "model": "claude-test", "content": [
                {"type": "thinking", "thinking": "private reasoning", "signature": "s"},
                {"type": "text", "text": "respuesta"}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}},
        {"type": "custom-title", "customTitle": title},
    ])


def write_codex(stores: Stores, thread_id: str = CODEX_ID, cwd: Path | None = None) -> Path:
    home = stores.codex / "sessions" / "2026" / "10" / "02"
    return jsonl(home / f"rollout-2026-10-02T10-00-00-{thread_id}.jsonl", [
        {"timestamp": "2026-10-02T10:00:00Z", "type": "session_meta",
         "payload": {"id": thread_id, "cwd": str(cwd or stores.work), "originator": "codex_cli_rs", "cli_version": "0.99"}},
        {"timestamp": "2026-10-02T10:00:01Z", "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "codex prompt"}]}},
        {"timestamp": "2026-10-02T10:00:02Z", "type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "codex answer"}]}},
    ])


def write_copilot(stores: Stores, session_id: str = COPILOT_ID, cwd: Path | None = None) -> Path:
    folder = stores.copilot / "session-state" / session_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "workspace.yaml").write_text(
        f"id: {session_id}\nname: 'Copilot ws'\ncwd: {cwd or stores.work}\n"
        "created_at: 2026-10-03T09:00:00.000Z\nupdated_at: 2026-10-03T09:30:00.000Z\n", encoding="utf-8")
    return jsonl(folder / "events.jsonl", [
        {"type": "session.start", "timestamp": "2026-10-03T09:00:00Z", "data": {"copilotVersion": "1.2.3", "selectedModel": "gpt-cop"}},
        {"type": "user.message", "timestamp": "2026-10-03T09:00:01Z", "data": {"content": "copilot prompt"}},
        {"type": "assistant.message", "timestamp": "2026-10-03T09:00:02Z", "data": {"content": "copilot answer"}},
    ])


def _pb_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def pb_field(number: int, value) -> bytes:
    """One protobuf field: an int is a varint, bytes are length-delimited (a synthetic encoder for the tests)."""
    if isinstance(value, int):
        return _pb_varint(number << 3) + _pb_varint(value)
    return _pb_varint(number << 3 | 2) + _pb_varint(len(value)) + value


def agy_gen_blob(input_tokens: int, output_tokens: int, cache: int = 0, thinking: int = 0) -> bytes:
    """A ``gen_metadata.data`` blob: field 1 -> message with a text field, a fixed32 and field 4 -> the usage varints."""
    usage = pb_field(2, input_tokens) + pb_field(3, output_tokens) + pb_field(7, b"GEN-SECRET")
    if cache:
        usage += pb_field(5, cache)
    if thinking:
        usage += pb_field(9, thinking)
    inner = pb_field(1, b"GEN-SECRET") + bytes([0x35, 1, 2, 3, 4]) + pb_field(4, usage)
    return pb_field(2, b"GEN-SECRET") + pb_field(1, inner)


def write_antigravity(home: Path, rows: list[dict] | None = None, *, files: bool = True) -> Path:
    """A synthetic agy folder: ``conversation_summaries.db`` with the real column names, one ``conversations/<id>.db``
    per row and a ``brain/<id>`` folder. The text in the rows is invented; nothing here comes from a real store."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "conversations").mkdir(exist_ok=True)
    rows = rows if rows is not None else [agy_row(AGY_ID, "agy synthetic title", "file:///d:/proj/app")]
    conn = sqlite3.connect(home / "conversation_summaries.db")
    conn.execute(
        "CREATE TABLE conversation_summaries (conversation_id text PRIMARY KEY, title text NOT NULL DEFAULT '', "
        "preview text NOT NULL DEFAULT '', step_count integer NOT NULL DEFAULT 0, last_modified_time datetime NOT NULL, "
        "workspace_uris text NOT NULL, status text NOT NULL DEFAULT '', source text NOT NULL DEFAULT '', "
        "agent_name text NOT NULL DEFAULT '', parent_conversation_id text NOT NULL DEFAULT '', "
        "nesting_depth integer NOT NULL DEFAULT 0, killed numeric NOT NULL DEFAULT 0, "
        "not_fully_idle numeric NOT NULL DEFAULT 0, raw_summary blob)")
    for row in rows:
        conn.execute(
            "INSERT INTO conversation_summaries (conversation_id, title, preview, step_count, last_modified_time, "
            "workspace_uris, status, agent_name, parent_conversation_id, killed, not_fully_idle, raw_summary) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["title"], "PREVIEW-SECRET", 3, row["modified"], json.dumps([row["workspace"]]),
             row["status"], row.get("agent", ""), row.get("parent", ""), row.get("killed", 0), row.get("busy", 0),
             b"RAW-SECRET"))
        if files:
            inner = sqlite3.connect(home / "conversations" / f"{row['id']}.db")
            inner.execute("CREATE TABLE steps (idx integer PRIMARY KEY, step_payload blob)")
            inner.execute("INSERT INTO steps VALUES (0, ?)", (b"STEP-SECRET",))
            if "gens" in row:  # blobs (bytes) of the generations; omit the key for a conversation without the table
                inner.execute("CREATE TABLE gen_metadata (idx integer PRIMARY KEY, data blob, size integer NOT NULL DEFAULT 0)")
                for i, blob in enumerate(row["gens"]):
                    inner.execute("INSERT INTO gen_metadata VALUES (?, ?, ?)", (i, blob, len(blob)))
            inner.commit()
            inner.close()
        (home / "brain" / row["id"]).mkdir(parents=True, exist_ok=True)
    conn.commit()
    conn.close()
    return home


def agy_row(cid: str, title: str, workspace: str, *, modified: str = "2026-11-17 22:11:05.1234567+01:00",
            status: str = "CASCADE_RUN_STATUS_IDLE", **extra) -> dict:
    return {"id": cid, "title": title, "workspace": workspace, "modified": modified, "status": status, **extra}


def add_framework_run(services: ApplicationServices, name: str = "demo", *, events: int = 2, model: str = "") -> str:
    """A run in the system store with ``events`` extra events and, when ``model`` is given, usage records."""
    run = services.runs.create(SocialPostRequest(project_name=name, objective="Create an educational post", topic="KNX"))
    for number in range(events):
        services.system.db.log_event(run["id"], "step_completed", {"step_id": f"s{number}", "model": model or None})
    if model:
        for tokens_in, tokens_out in ((10, 4), (7, None)):
            services.system.store.record_telemetry(
                run_id=run["id"], step_id="s0", agent_id="a", attempt=1, provider="ollama", model=model,
                model_digest="", prompt_id="p", prompt_version=1, prompt_sha256="", brand_version=None,
                tokens_in=tokens_in, tokens_out=tokens_out, num_ctx=None, latency_ms=1, duration_ms=1,
                estimated_cost_usd=None, success=1, error_type="")
    return run["id"]
