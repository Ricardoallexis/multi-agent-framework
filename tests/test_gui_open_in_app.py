# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI-T06 "Abrir en la app": the Claude launch endpoint and the client code that opens Codex and Copilot links.

Nothing real is ever started: ``subprocess.Popen`` is replaced for every test (a stray launch fails loudly) and
``shutil.which`` is faked. The JavaScript is read as text, like in the GUI-T05 tests (no browser, no Node)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR, open_in_app
from multiagent.gui.token import ensure_token
from session_fixtures import (
    CLAUDE_ID,
    CLAUDE_OTHER,
    CODEX_ID,
    COPILOT_ID,
    add_framework_run,
    jsonl,
    make_services,
    make_stores,
    write_claude,
    write_codex,
    write_copilot,
)
from test_gui_sessions_view import FORBIDDEN

SID = f"claude:{CLAUDE_ID}"
FAKE_CLAUDE = str(Path("C:/fake-bin/claude.exe") if os.name == "nt" else Path("/fake-bin/claude"))
JS = STATIC_DIR / "js"


def open_path(session_id: str) -> str:
    return f"/gui/api/sessions/{session_id}/open"


@pytest.fixture(autouse=True)
def no_real_launch(monkeypatch):
    """Whatever a test does, the real ``Popen`` is never reached; tests that need a launch install ``launcher``."""
    def refuse(*args, **kwargs):
        raise AssertionError(f"a real process launch was attempted: {args!r}")
    monkeypatch.setattr(open_in_app.subprocess, "Popen", refuse)
    monkeypatch.setattr(open_in_app, "_last_launch", None)  # the rate limit is per process: start every test fresh


@pytest.fixture
def launcher(monkeypatch):
    """A fake Windows with ``claude`` on PATH; records every launch."""
    calls: list[SimpleNamespace] = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append(SimpleNamespace(args=args, kwargs=kwargs))

    monkeypatch.setattr(open_in_app.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(open_in_app, "launch_supported", lambda: True)
    monkeypatch.setattr(open_in_app.shutil, "which", lambda name, *a, **k: FAKE_CLAUDE if name == "claude" else None)
    return calls


def build(tmp_path, **settings):
    stores = make_stores(tmp_path)
    services = make_services(stores, **settings)
    write_claude(stores)
    write_codex(stores)
    write_copilot(stores)
    return stores, services, ensure_token(services.settings.gui_token_file)


@pytest.fixture
def world(tmp_path):
    stores, services, token = build(tmp_path)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, stores=stores, services=services, token=token,
                              auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


def post(world, session_id=SID, **kwargs):
    return world.client.post(open_path(session_id), headers=world.auth, json=kwargs.pop("json", {}), **kwargs)


def fake_clock(monkeypatch) -> list[float]:
    """A manual monotonic clock for the launch module only (the real ``time`` module is left alone)."""
    clock = [1000.0]
    monkeypatch.setattr(open_in_app, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


def tamper(monkeypatch, world, **changes):
    """Make the sessions service answer a modified summary of the Claude session (a defense-in-depth probe)."""
    real = world.services.sessions.get_session

    def fake(session_id):
        summary = real(session_id)
        for name, value in changes.items():
            if name == "open_in_app" and isinstance(value, dict) and isinstance(summary.get("open_in_app"), dict):
                value = {**summary["open_in_app"], **value}
            summary[name] = value
        return summary

    monkeypatch.setattr(world.services.sessions, "get_session", fake)


# -------- the endpoint: token, JSON, ids, sources --------
def test_open_needs_the_token_and_a_json_body(world, launcher):
    path = open_path(SID)
    assert world.client.post(path, json={}).status_code == 401
    assert world.client.post(path, json={}, headers={"Authorization": "Bearer wrong-token-value"}).status_code == 401
    assert world.client.post(path, json={}, headers={"Authorization": f"Basic {world.token}"}).status_code == 401
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
        response = world.client.post(path, content="{}", headers={**world.auth, "Content-Type": content_type})
        assert response.status_code == 415, content_type
    assert world.client.get(path, headers=world.auth).status_code == 405  # reading never launches
    assert launcher == []


def test_unknown_and_malformed_ids(world, launcher):
    response = post(world, f"claude:{CLAUDE_OTHER}")
    assert (response.status_code, response.json()["detail"]["code"]) == (404, "not_found")
    for bad in ("claude:not-a-uuid", "nothing", "claude:--desktop", f"claude:{CLAUDE_ID}:agent-@@"):
        response = post(world, bad)
        assert (response.status_code, response.json()["detail"]["code"]) == (400, "invalid_session_id"), bad
    assert launcher == []


def test_only_claude_sessions_are_launched(world, launcher):
    run_id = add_framework_run(world.services)
    for session_id in (f"codex:{CODEX_ID}", f"copilot:{COPILOT_ID}", f"framework:{run_id}"):
        response = post(world, session_id)
        assert (response.status_code, response.json()["detail"]["code"]) == (400, "unsupported_source"), session_id
    assert launcher == []


def test_a_claude_subagent_cannot_be_opened_on_its_own(world, launcher):
    main = world.stores.claude / "projects" / "proj" / f"{CLAUDE_ID}.jsonl"
    jsonl(main.parent / CLAUDE_ID / "subagents" / "agent-abc123.jsonl", [
        {"type": "user", "timestamp": "2026-10-01T10:00:08Z", "message": {"role": "user", "content": "child task"}}])
    response = post(world, f"{SID}:agent-abc123")
    assert (response.status_code, response.json()["detail"]["code"]) == (400, "invalid_open_target")
    assert launcher == []


# -------- the launch itself --------
def test_launch_uses_an_exact_argument_list_in_a_new_console(world, launcher):
    response = post(world)
    assert response.status_code == 200
    assert response.json() == {"launched": True, "session_id": SID, "cwd": "home",
                               "command": ["claude", "--desktop", "--resume", CLAUDE_ID]}
    assert "fake-bin" not in response.text  # the answer never shows where the program lives
    (call,) = launcher
    assert call.args == [FAKE_CLAUDE, "--desktop", "--resume", CLAUDE_ID]
    assert isinstance(call.args, list) and all(isinstance(part, str) for part in call.args)
    assert call.kwargs["creationflags"] == open_in_app.CREATE_NEW_CONSOLE == 0x00000010
    assert not call.kwargs.get("shell", False)
    assert call.kwargs["cwd"] == str(Path.home()) != str(world.stores.work)  # never the transcript's folder
    for stream in ("stdin", "stdout", "stderr"):
        assert stream not in call.kwargs  # the new console keeps its own handles


def opened_events(world) -> list[dict]:
    return [e for e in world.services.tasks.events() if e["type"] == "session.opened_in_app"]


def test_a_launch_is_recorded_so_the_framework_does_not_resume_that_session(world, launcher):
    """ORQ-T51 (contract ORQ-6 section 6): a session the app just opened is not resumed for the next 12 hours."""
    assert opened_events(world) == []
    assert post(world).status_code == 200
    (event,) = opened_events(world)
    assert event["ref"] == CLAUDE_ID.lower() and event["actor"] == "gui" and event["payload"]["source"] == "claude"
    assert "opened_at" in event["payload"]


def test_a_launch_that_did_not_happen_records_nothing_and_a_broken_record_never_breaks_the_open(world, launcher, monkeypatch):
    monkeypatch.setattr(open_in_app.shutil, "which", lambda *a, **k: None)
    assert post(world).status_code == 409 and opened_events(world) == []
    monkeypatch.setattr(open_in_app.shutil, "which", lambda name, *a, **k: FAKE_CLAUDE if name == "claude" else None)

    def broken(*args, **kwargs):
        raise RuntimeError("the event log is unavailable")

    monkeypatch.setattr(world.services.executors, "record_app_open", broken)
    response = post(world)
    assert response.status_code == 200 and response.json()["launched"] is True and len(launcher) == 1


def test_environment_is_a_copy_without_claude_or_anthropic_variables(world, launcher, monkeypatch):
    for name in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "anthropic_lower"):
        monkeypatch.setenv(name, "secret-value")
    monkeypatch.setenv("KEEP_ME_TEST", "yes")
    assert post(world).status_code == 200
    (call,) = launcher
    env = call.kwargs["env"]
    assert env is not os.environ and isinstance(env, dict)
    assert [name for name in env if name.upper().startswith(("CLAUDE", "ANTHROPIC"))] == []
    assert "secret-value" not in env.values()
    assert env["KEEP_ME_TEST"] == "yes" and any(name.upper() == "PATH" for name in env)
    assert os.environ["CLAUDECODE"] == "secret-value"  # the viewer's own environment is untouched


def test_nothing_in_the_request_changes_what_is_launched(world, launcher):
    hostile = {"native_id": "11111111-1111-4111-8111-aaaaaaaaaaaa", "uuid": "--evil", "command": ["calc.exe"],
               "cwd": "C:/Windows", "session_id": f"claude:{CLAUDE_OTHER}", "args": ["&", "calc"]}
    response = world.client.post(open_path(SID) + "?uuid=--evil&cwd=C:/Windows", headers=world.auth, json=hostile)
    assert response.status_code == 200
    (call,) = launcher
    assert call.args == [FAKE_CLAUDE, "--desktop", "--resume", CLAUDE_ID] and call.kwargs["cwd"] == str(Path.home())


@pytest.mark.parametrize("changes", [
    {"native_id": "--evil"},
    {"native_id": CLAUDE_ID + "\n", "id": f"claude:{CLAUDE_ID}\n"},
    {"native_id": "11111111-1111-4111-8111-11111111111g", "id": "claude:11111111-1111-4111-8111-11111111111g"},
    {"native_id": f"{CLAUDE_ID} --dangerously-skip-permissions"},
    {"id": f"claude:{CLAUDE_OTHER}"},  # the summary's id and native id disagree
    {"open_in_app": {"value": ["claude", "--desktop", "--resume", "--evil"]}},
    {"open_in_app": {"value": ["claude", "--desktop", "--resume", CLAUDE_ID, "--extra"]}},
    {"open_in_app": {"value": ["calc.exe", "--desktop", "--resume", CLAUDE_ID]}},
    {"open_in_app": {"value": f"claude --desktop --resume {CLAUDE_ID}"}},
    {"open_in_app": {"value": ["claude", "--desktop", "--resume", CLAUDE_OTHER]}},
    {"open_in_app": {"kind": "url", "value": f"codex://threads/{CLAUDE_ID}"}},
    {"open_in_app": None},
])
def test_an_invalid_uuid_or_target_never_launches(world, launcher, monkeypatch, changes):
    tamper(monkeypatch, world, **changes)
    response = post(world)
    assert (response.status_code, response.json()["detail"]["code"]) == (400, "invalid_open_target")
    assert launcher == []


@pytest.mark.parametrize("cwd", [
    "session", None, "", "relative/folder", "Z:/definitely/not/here", str(Path(__file__)), str(Path(__file__).parent),
])
def test_working_folder_is_always_the_users_home(world, launcher, monkeypatch, cwd):
    """The folder in the transcript is never the process's cwd: a planted node.exe/.cmd/.bat there would run."""
    if cwd != "session":  # "session": the real transcript folder (an existing absolute folder)
        tamper(monkeypatch, world, cwd=cwd)
    assert post(world).json()["cwd"] == "home"
    (call,) = launcher
    assert call.kwargs["cwd"] == str(Path.home())


def test_environment_stops_searching_the_current_folder(world, launcher, monkeypatch):
    monkeypatch.setenv("NODEFAULTCURRENTDIRECTORYINEXEPATH", "0")  # a hostile value in another letter case is replaced
    assert post(world).status_code == 200
    (call,) = launcher
    env = call.kwargs["env"]
    assert env["NoDefaultCurrentDirectoryInExePath"] == "1"
    assert [name for name in env if name.upper() == "NODEFAULTCURRENTDIRECTORYINEXEPATH"] == ["NoDefaultCurrentDirectoryInExePath"]
    assert os.environ["NODEFAULTCURRENTDIRECTORYINEXEPATH"] == "0"  # the viewer's own environment is untouched


def test_a_claude_found_in_the_servers_current_folder_is_rejected(world, launcher, monkeypatch):
    planted = str(Path(os.getcwd()) / "claude.exe")
    monkeypatch.setattr(open_in_app.shutil, "which", lambda name, *a, **k: planted)
    response = post(world)
    assert (response.status_code, response.json()["detail"]["code"]) == (409, "claude_not_found")
    assert launcher == []
    monkeypatch.setattr(open_in_app.shutil, "which", lambda name, *a, **k: "claude.exe")  # relative: the current folder
    assert post(world).json()["detail"]["code"] == "claude_not_found" and launcher == []
    monkeypatch.setattr(open_in_app.shutil, "which", lambda name, *a, **k: FAKE_CLAUDE)  # a PATH folder is accepted
    assert post(world).status_code == 200 and len(launcher) == 1


def test_claude_is_looked_up_only_in_absolute_path_entries_other_than_the_current_folder(world, launcher, monkeypatch):
    here, elsewhere = os.getcwd(), str(Path(FAKE_CLAUDE).parent)
    seen: list[str] = []

    def which(name, path=None, **kwargs):
        seen.append(path)
        return FAKE_CLAUDE

    monkeypatch.setattr(open_in_app.shutil, "which", which)
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "relative/bin", here, here + os.sep, elsewhere]))
    assert post(world).status_code == 200
    assert seen == [elsewhere]


def test_a_second_launch_within_two_seconds_is_a_429(world, launcher, monkeypatch):
    clock = fake_clock(monkeypatch)
    assert post(world).status_code == 200
    clock[0] += 1.9
    response = post(world)
    assert (response.status_code, response.json()["detail"]["code"]) == (429, "too_many_launches")
    assert len(launcher) == 1  # nothing was started the second time
    clock[0] += 0.2  # 2.1 s after the first launch
    assert post(world).status_code == 200 and len(launcher) == 2


def test_the_rate_limit_counts_launch_attempts_only(world, launcher, monkeypatch):
    fake_clock(monkeypatch)  # the time never moves: any request that did use the slot would make the next one a 429
    assert post(world, f"claude:{CLAUDE_OTHER}").status_code == 404  # unknown session
    assert post(world, f"codex:{CODEX_ID}").status_code == 400  # not a Claude session
    monkeypatch.setattr(open_in_app.shutil, "which", lambda *a, **k: None)
    assert post(world).status_code == 409  # claude not found
    monkeypatch.setattr(open_in_app.shutil, "which", lambda name, *a, **k: FAKE_CLAUDE)
    assert launcher == [] and post(world).status_code == 200 and len(launcher) == 1


def test_missing_claude_answers_409_and_starts_nothing(world, launcher, monkeypatch):
    monkeypatch.setattr(open_in_app.shutil, "which", lambda *a, **k: None)
    response = post(world)
    assert (response.status_code, response.json()["detail"]["code"]) == (409, "claude_not_found")
    assert "PATH" in response.json()["detail"]["message"]
    assert launcher == []


def test_launch_is_unsupported_outside_windows(world, launcher, monkeypatch):
    monkeypatch.setattr(open_in_app, "launch_supported", lambda: False)
    response = post(world)
    assert (response.status_code, response.json()["detail"]["code"]) == (409, "launch_unsupported")
    assert launcher == []
    assert world.client.get("/gui/api/open-in-app", headers=world.auth).json() == {"allow_launch": False}


def test_a_failed_start_is_a_409_that_does_not_leak_the_error(world, launcher, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("C:\\secret\\path\\claude.exe: access denied")
    monkeypatch.setattr(open_in_app.subprocess, "Popen", broken)
    response = post(world)
    assert (response.status_code, response.json()["detail"]["code"]) == (409, "launch_failed")
    assert "secret" not in response.text


def test_gui_allow_launch_false_disables_the_launch(tmp_path, monkeypatch, launcher):
    stores, services, token = build(tmp_path, gui_allow_launch=False)
    auth = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post(open_path(SID), headers=auth, json={})
        assert (response.status_code, response.json()["detail"]["code"]) == (403, "launch_disabled")
        assert client.get("/gui/api/open-in-app", headers=auth).json() == {"allow_launch": False}
        # the other checks still come first: an unknown session is a 404 and Codex a 400, launch disabled or not
        assert client.post(open_path(f"claude:{CLAUDE_OTHER}"), headers=auth, json={}).status_code == 404
        assert client.post(open_path(f"codex:{CODEX_ID}"), headers=auth, json={}).status_code == 400
    services.runs.close()
    assert launcher == []


def test_open_in_app_options_need_the_token_and_follow_the_setting(world, launcher):
    assert world.client.get("/gui/api/open-in-app").status_code == 401
    assert world.client.get("/gui/api/open-in-app", headers=world.auth).json() == {"allow_launch": True}
    world.services.settings.gui_allow_launch = False
    assert world.client.get("/gui/api/open-in-app", headers=world.auth).json() == {"allow_launch": False}


def test_the_setting_defaults_to_on_and_reads_the_environment(monkeypatch):
    monkeypatch.delenv("GUI_ALLOW_LAUNCH", raising=False)
    assert Settings(_env_file=None).gui_allow_launch is True
    monkeypatch.setenv("GUI_ALLOW_LAUNCH", "false")
    assert Settings(_env_file=None).gui_allow_launch is False


# -------- the client code (read as text) --------
def read(name: str) -> str:
    return ui_text((JS / name))


def js_regex(source: str, name: str) -> re.Pattern:
    """A strict JavaScript regex literal ``const NAME = /^...$/;`` as a Python pattern that must match all of it."""
    body = re.search(rf"^const {name} = /\^(.*)\$/;$", source, re.M)[1]
    return re.compile(body.replace("\\/", "/"))


def function_body(source: str, name: str) -> str:
    return re.search(rf"export function {name}\(.*?\n}}\n", source, re.S)[0]


def test_the_client_file_is_loaded_by_the_detail_and_clean():
    detail, client = read("session-detail.js"), read("open-in-app.js")
    assert 'import { OpenInApp } from "./open-in-app.js"' in detail
    assert re.search(r'<div id="open-in-app-slot" class="open-in-app-slot"><\$\{OpenInApp\} token=\$\{token\} summary=\$\{summary\} /></div>', detail)
    assert detail.count('id="open-in-app-slot"') == 1
    assert re.search(r"<\$\{Header\} token=\$\{token\} summary=\$\{summary\}", detail)
    for pattern, label in FORBIDDEN:
        assert not re.search(pattern, client), f"open-in-app.js: {label}"
    assert "__html" not in client
    for other in ("sessions.js", "session-items.js", "session-text.js", "route.js"):
        assert not re.search(r"codex:|ghapp:|open_in_app", read(other)), other  # links are built in one place only


def test_links_come_only_from_open_in_app_after_a_strict_pattern():
    client = read("open-in-app.js")
    assert re.findall(r"\bhref=\$\{[^}]*\}", client) == ["href=${url}"]  # the one navigation: the validated link
    assert "codex://" not in client and "ghapp://" not in client  # only escaped, inside the regex literals
    assert not re.search(r"""["'`]\s*(?:codex|ghapp):""", client)  # no URL is ever assembled from pieces
    for name in ("linkTarget", "commandTarget"):
        body = function_body(client, name)
        assert not re.search(r"\.(?:title|cwd|native_id|parent_id|warnings|reader)\b", body), name
    assert "summary.title" not in client
    link = function_body(client, "linkTarget")
    assert "open.value" in link and "app.pattern.exec(open.value)" in link and re.search(r"\?\s*open\.value\s*:\s*null", link)
    assert 'const url = linkTarget(summary);' in client and "<${LinkOpen} summary=${summary} url=${url} />" in client


def test_client_patterns_accept_real_api_values_and_reject_lookalikes():
    client = read("open-in-app.js")
    codex, copilot, uuid = (js_regex(client, name) for name in ("CODEX_URL", "COPILOT_URL", "UUID"))
    assert codex.fullmatch(f"codex://threads/{CODEX_ID}") and copilot.fullmatch(f"ghapp://sessions/{COPILOT_ID}")
    assert uuid.fullmatch(CLAUDE_ID) and uuid.fullmatch(CLAUDE_ID.upper())
    for bad in (f"ghapp://sessions/{CODEX_ID}", f"codex://threads/{CODEX_ID}/", f"codex://threads/{CODEX_ID}?x=1",
                f"codex://threads/{CODEX_ID}#x", f"codex://threads/{CODEX_ID}\n", f" codex://threads/{CODEX_ID}",
                f"CODEX://threads/{CODEX_ID}", f"codex://threads/../{CODEX_ID}", "codex://threads/1234",
                f"codex://threads/{CODEX_ID}x", f"codex://evil@threads/{CODEX_ID}", f"javascript:codex://threads/{CODEX_ID}",
                "javascript:alert(1)", f"https://example.test/codex://threads/{CODEX_ID}"):
        assert not codex.fullmatch(bad), bad
    for bad in (f"codex://threads/{COPILOT_ID}", f"ghapp://sessions/{COPILOT_ID}?x=1", f"ghapp://sessions/{COPILOT_ID}\n",
                f"ghapp://session/{COPILOT_ID}", f"ghapp://sessions/{{{COPILOT_ID}}}"):
        assert not copilot.fullmatch(bad), bad
    for bad in ("--evil", CLAUDE_ID + "\n", CLAUDE_ID[:-1], CLAUDE_ID + "0", CLAUDE_ID.replace("1", "g", 1)):
        assert not uuid.fullmatch(bad), bad
    assert "^" in client and re.search(r"^const (?:UUID|CODEX_URL|COPILOT_URL) = /\^", client, re.M)  # anchored at both ends


def test_client_patterns_accept_what_the_api_really_sends(world):
    client = read("open-in-app.js")
    codex, copilot, uuid = (js_regex(client, name) for name in ("CODEX_URL", "COPILOT_URL", "UUID"))
    got = {name: world.client.get(f"/gui/api/sessions/{name}:{native}", headers=world.auth).json()
           for name, native in (("claude", CLAUDE_ID), ("codex", CODEX_ID), ("copilot", COPILOT_ID))}
    assert codex.fullmatch(got["codex"]["open_in_app"]["value"]) and got["codex"]["open_in_app"]["kind"] == "url"
    assert copilot.fullmatch(got["copilot"]["open_in_app"]["value"]) and got["copilot"]["open_in_app"]["needs_confirmation"] is True
    command = got["claude"]["open_in_app"]
    assert command["kind"] == "command" and command["value"] == ["claude", "--desktop", "--resume", CLAUDE_ID]
    for name in ("claude", "codex", "copilot"):  # what the client compares the link or command with
        assert got[name]["id"] == f"{name}:{got[name]['native_id']}" and uuid.fullmatch(got[name]["native_id"])
    assert 'summary.id === summary.source + ":" + summary.native_id' in client


def test_claude_button_asks_first_then_posts_only_the_session_id():
    client = read("open-in-app.js")
    assert client.count("apiPost(") == 1
    assert 'apiPost("/gui/api/sessions/" + encodeURIComponent(summary.id) + "/open", token, {})' in client
    assert client.count("onClick=${launch}") == 1  # only the confirm button launches
    assert re.search(r'onClick=\$\{\(\) => \{[^}]*setStep\("confirm"\)', client)  # the first button only asks
    assert 'aria-label="Confirmar la apertura en Claude"' in client and "Cancelar" in client
    assert "confirmButton.current.focus()" in client  # keyboard users land on the question
    assert 'role="status"' in client and 'role="alert"' in client  # the result is announced
    assert "launchErrorText" in client and "claude_not_found" in client and "launch_disabled" in client


def test_command_to_copy_is_shown_when_launching_is_disabled():
    client = read("open-in-app.js")
    assert 'apiGet("/gui/api/open-in-app", token)' in client and "allow === false" in client
    assert "gui_allow_launch" in client and "navigator.clipboard.writeText(command)" in client
    assert "Copiar comando" in client and "selecciónalo y cópialo a mano" in client  # copy may be blocked: say so
    assert 'return "claude --desktop --resume " + uuid;' in client
    command = function_body(client, "commandTarget")
    for part in ('name !== "claude"', 'desktop !== "--desktop"', 'resume !== "--resume"', "extra.length", "UUID.test(uuid)"):
        assert part in command


def test_notes_for_each_app_are_shown():
    client = read("open-in-app.js")
    assert "pedirá confirmación antes de abrir la sesión" in client and "needs_confirmation" in client  # Copilot
    assert "solo después de cerrar y volver a abrir la app" in client and 'NOTE_REOPEN("Claude")' in client
    assert 'NOTE_REOPEN(app.label)' in client and 'summary.source === "copilot"' in client
    assert "bloquea las escrituras mientras la app tiene el hilo abierto" in client and "solo lee" in client  # Codex
    assert "Abrir en ${app.label}" in client and "Abrir en Claude" in client


def test_framework_runs_keep_their_link_and_get_no_app_button():
    detail, client = read("session-detail.js"), read("open-in-app.js")
    assert 'routeHref("runs", summary.native_id)' in detail
    assert re.search(r'if \(summary\.source === "framework"\) return null;', client)


def test_styles_for_the_new_controls_exist():
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    for selector in (".open-in-app-slot:empty", ".open-notes", ".open-confirm", "code.command"):
        assert selector in css, selector
