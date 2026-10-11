# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Synthetic session stores for the GUI-T04 tests: built in ``tmp_path``, never the user's real folders."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from multiagent.config import Settings
from multiagent.contracts import SocialPostRequest
from multiagent.services import ApplicationServices

CLAUDE_ID = "11111111-1111-4111-8111-111111111111"
CLAUDE_OTHER = "11111111-1111-4111-8111-bbbbbbbbbbbb"
CODEX_ID = "22222222-2222-4222-8222-222222222222"
COPILOT_ID = "33333333-3333-4333-8333-333333333333"


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


def make_stores(tmp: Path) -> Stores:
    stores = Stores(tmp=tmp, roots=tmp / "work", work=tmp / "work" / "proj", other=tmp / "elsewhere",
                    claude=tmp / "claude", codex=tmp / "codex", copilot=tmp / "copilot", desktop=tmp / "desktop")
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
        gui_claude_desktop_dir=stores.desktop,
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
