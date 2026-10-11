# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI-T10: one test per finding of the independent security review (synthetic data in ``tmp_path`` only)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui import events
from multiagent.gui.__main__ import main as gui_main
from multiagent.gui.__main__ import run as gui_run
from multiagent.gui.token import TokenLocationError, check_token_location, ensure_token
from multiagent.services import ServiceError
from multiagent.sessions import _common
from multiagent.sessions._common import iter_records, within_root
from multiagent.sessions.config import SessionsConfig
from multiagent.sessions.copilot import CopilotReader
from session_fixtures import (
    CLAUDE_ID,
    CLAUDE_OTHER,
    COPILOT_ID,
    add_framework_run,
    claude_user,
    jsonl,
    make_services,
    make_stores,
    write_claude,
    write_copilot,
)

SID = f"claude:{CLAUDE_ID}"


@pytest.fixture(autouse=True)
def _restore_mock_mode(monkeypatch):
    """The launcher writes MOCK_MODE into ``os.environ``; register it so every test leaves it as it found it."""
    monkeypatch.setenv("MOCK_MODE", "false")


@pytest.fixture
def world(tmp_path):
    stores = make_stores(tmp_path)
    services = make_services(stores)
    write_claude(stores)
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield type("World", (), dict(stores=stores, services=services, client=client,
                                     auth={"Authorization": f"Bearer {token}"}))
    services.runs.close()


# -------- 1. a deeply nested JSON line is a broken line, not a failed source --------
def test_deeply_nested_json_line_counts_as_broken_and_the_listing_survives(world):
    deep = "[" * 200_000 + "]" * 200_000  # under the 1 MB line limit, over the parser's recursion limit
    path = world.stores.claude / "projects" / "proj" / f"{CLAUDE_ID}.jsonl"
    jsonl(path, [claude_user("before", "2026-10-01T10:00:00Z", str(world.stores.work)), deep,
                 claude_user("after", "2026-10-01T10:00:05Z")])
    assert [status for _, _, status in iter_records(path)] == ["ok", "broken", "ok"]
    listed = world.services.sessions.list_sessions()
    summary = next(s for s in listed["sessions"] if s["id"] == SID)
    assert "broken_json_lines: 1" in summary["warnings"]
    page = world.services.sessions.read_items(SID)
    assert "broken_json_lines: 1" in page["warnings"] and [i["text"] for i in page["items"]] == ["before", "after"]


# -------- 2. a relative cwd never matches the allowed folders --------
def test_a_relative_cwd_never_passes_the_allowed_folder_filter(world, monkeypatch):
    roots = (str(world.stores.roots),)
    monkeypatch.chdir(world.stores.work)  # a relative path would resolve inside the allowed folder
    for relative in (".", "proj", "..", "sub/dir", "C:proj"):
        assert within_root(relative, roots) is False
    assert within_root(str(world.stores.work), roots) is True
    write_claude(world.stores, cwd=Path("proj"))  # the session says its folder is the relative "proj"
    assert world.services.sessions.list_sessions()["sessions"] == []
    with pytest.raises(ServiceError) as err:
        world.services.sessions.get_session(SID)
    assert err.value.status == 404


# -------- 3. Last-Event-ID on the base stream is validated like ``after`` --------
@pytest.mark.parametrize("header", ["9" * 40, "99999999999999999999", "-1", "1.5", "abc", " ", "0x10"])
def test_a_bad_last_event_id_on_the_base_stream_is_ignored_not_a_500(world, header):
    run_id = add_framework_run(world.services, events=2)
    response = world.client.get("/gui/api/stream", headers={**world.auth, "Last-Event-ID": header})
    assert response.status_code == 200
    assert response.text.count("event: step_completed") == 2 and run_id in response.text  # replayed from 0
    ok = world.client.get("/gui/api/stream", headers={**world.auth, "Last-Event-ID": "1"})
    assert ok.status_code == 200 and ok.text.count("event: step_completed") < 3
    bad_after = world.client.get("/gui/api/stream?after=" + "9" * 40, headers=world.auth)
    assert bad_after.status_code == 422  # `after` is rejected by the same pattern


# -------- 4. workspace.yaml of Copilot is read up to 1 MB --------
def test_an_oversized_copilot_workspace_yaml_is_skipped_with_a_warning(tmp_path):
    stores = make_stores(tmp_path)
    write_copilot(stores)
    workspace = stores.copilot / "session-state" / COPILOT_ID / "workspace.yaml"
    config = SessionsConfig.defaults(allowed_roots=(str(stores.roots),), sources=("copilot",),
                                     copilot_home=stores.copilot)
    reader = CopilotReader(config, _common.MetaIndex())
    normal = reader.list_summaries()[0][0]
    assert normal.cwd == str(stores.work) and normal.title == "Copilot ws"
    workspace.write_text(f"name: big\ncwd: {stores.work}\n" + "# padding\n" * 120_000, encoding="utf-8")
    assert workspace.stat().st_size > 1024 * 1024
    big = reader.list_summaries()[0][0]
    assert big.cwd is None and big.title != "big"  # nothing was read from it
    assert "workspace_yaml_skipped: file is larger than 1 MB" in big.warnings


# -------- 5. errors do not carry local paths --------
def test_unreadable_session_file_is_a_clean_error_without_the_path(world, monkeypatch):
    path = world.stores.claude / "projects" / "proj" / f"{CLAUDE_ID}.jsonl"
    assert world.services.sessions.get_session(SID)["id"] == SID  # known (cached) before the file is locked
    locked_later = write_claude(world.stores, session_id=CLAUDE_OTHER)  # never scanned: its scan hits the lock
    real_open = open

    def deny(file, *args, **kwargs):
        if Path(file) in (path, locked_later):
            raise PermissionError(13, "Permission denied", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(_common, "open", deny, raising=False)
    page = world.services.sessions.read_items(SID)  # was a 500 with the path in the message
    assert page["items"] == [] and page["done"] is True
    assert page["warnings"] == ["session_file_unavailable"]
    listed = world.services.sessions.list_sessions()  # one locked file does not fail the whole source
    assert [s["id"] for s in listed["sessions"]] == [SID]
    over_http = world.client.get(f"/gui/api/sessions/{SID}/items", headers=world.auth)
    assert over_http.status_code == 200 and str(world.stores.tmp) not in over_http.text

    async def scenario():
        read = lambda cursor: world.services.sessions.poll_items(SID, cursor)  # noqa: E731
        return [chunk async for chunk in events.stream_session(read, after="0:0", heartbeat=0.05, poll=0.01, max_seconds=0.1)]

    streamed = "".join(asyncio.run(scenario()))
    assert "session_file_unavailable" in streamed and str(world.stores.tmp) not in streamed


def test_the_session_stream_never_serializes_an_unexpected_exception_text(tmp_path):
    secret = str(tmp_path / "secret-folder" / "session.jsonl")

    def read(cursor):
        raise PermissionError(13, "Permission denied", secret)

    async def scenario():
        return [chunk async for chunk in events.stream_session(read, after="0:0", heartbeat=0.05, poll=0.01, max_seconds=1)]

    chunks = asyncio.run(scenario())
    error = json.loads(chunks[-1].split("data: ", 1)[1])
    assert chunks[-1].startswith("event: error") and error["code"] == "session_unavailable"
    assert "secret-folder" not in "".join(chunks) and "Permission denied" not in "".join(chunks)


def test_framework_event_items_drop_path_keys_and_cut_long_text(world):
    run_id = add_framework_run(world.services, events=0)
    world.services.system.db.log_event(run_id, "artifact_saved", {
        "artifact_path": "C:\\Users\\someone\\secret\\out.md", "path": "/home/someone/secret",
        "nested": {"output_path": "/home/someone/secret2", "kept": "visible"}, "long": "x" * 5000})
    page = world.services.sessions.read_items(f"framework:{run_id}")
    text = next(i["text"] for i in page["items"] if i["text"].startswith("artifact_saved"))
    assert "secret" not in text and "_path" not in text and "visible" in text
    assert len(text) < 2500 and "x" * 2001 not in text


def test_the_base_stream_drops_path_keys_from_event_payloads(world):
    run_id = add_framework_run(world.services, events=0)
    world.services.system.db.log_event(run_id, "artifact_saved", {
        "artifact_path": "/home/someone/secret/out.md",
        "nested": {"output_path": "/home/someone/secret2", "kept": "visible"}, "long": "x" * 5000})
    for url in ("/gui/api/stream", f"/gui/api/stream?session=framework:{run_id}"):  # the base and the per-run stream
        body = world.client.get(url, headers=world.auth).text
        assert "event: artifact_saved" in body and "visible" in body
        assert "secret" not in body and "_path" not in body and "x" * 2001 not in body


# -------- 6. the token file must be under the user's home folder --------
def test_a_token_file_outside_the_home_folder_is_refused_unless_opted_in(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    check_token_location(home / ".multiagent" / "gui-token")  # inside: fine
    outside = tmp_path / "shared" / "gui-token"
    with pytest.raises(TokenLocationError, match="outside your home folder"):
        check_token_location(outside)
    check_token_location(outside, allow_outside_home=True)  # explicit opt-in
    # the launcher applies it before it creates anything
    monkeypatch.setenv("GUI_TOKEN_FILE", str(outside))
    monkeypatch.setenv("LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setenv("APP_HOST", "127.0.0.1")
    monkeypatch.delenv("GUI_TOKEN_OUTSIDE_HOME", raising=False)
    with pytest.raises(TokenLocationError):
        gui_run(no_browser=True)
    assert not outside.exists() and not (tmp_path / "local").exists()


# -------- 7. the launcher only starts on a loopback host --------
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "example.com", ""])
def test_the_launcher_aborts_when_the_host_is_not_loopback(tmp_path, monkeypatch, host):
    monkeypatch.setenv("APP_HOST", host)
    monkeypatch.setenv("LOCAL_DIR", str(tmp_path / "local"))
    with pytest.raises(RuntimeError, match="loopback"):
        gui_run(no_browser=True)
    assert not (tmp_path / "local").exists()
    with pytest.raises(SystemExit) as exit_info:  # `python -m multiagent.gui`: the message, no traceback
        gui_main(["--no-browser"])
    assert str(exit_info.value).startswith("ERROR: ") and "loopback" in str(exit_info.value)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_the_launcher_accepts_loopback_hosts(tmp_path, monkeypatch, host):
    import multiagent.cli
    import uvicorn

    started = []
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(uvicorn.Server, "run",
                        lambda self: started.append(dict(host=self.config.host, port=self.config.port,
                                                         log_level=self.config.log_level)))
    monkeypatch.setattr(multiagent.cli, "_check_port_free", lambda host, port: None)
    for name, value in (("APP_HOST", host), ("LOCAL_DIR", str(tmp_path / "local")), ("WORKER_ENABLED", "false"),
                        ("GUI_TOKEN_FILE", str(home / ".multiagent" / "gui-token")), ("MOCK_MODE", "true")):
        monkeypatch.setenv(name, value)
    gui_run(port=8123, no_browser=True)
    assert started == [dict(host=host, port=8123, log_level="warning")]
