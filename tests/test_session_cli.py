# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI-T04 CLI: `multiagent sessions list|show|items` and `multiagent gui`, over synthetic stores."""
from __future__ import annotations

import contextlib
import io
import json
from types import SimpleNamespace

import pytest

from multiagent import cli
from multiagent.gui import __main__ as gui_main
from multiagent.services import SessionServices
from session_fixtures import (
    CLAUDE_ID,
    CODEX_ID,
    COPILOT_ID,
    add_framework_run,
    make_services,
    make_stores,
    settings_for,
    write_claude,
    write_codex,
    write_copilot,
)

SID = f"claude:{CLAUDE_ID}"


def invoke(*argv: str) -> tuple[str, str, int]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return out.getvalue(), err.getvalue(), code


@pytest.fixture
def world(tmp_path, monkeypatch):
    stores = make_stores(tmp_path)
    services = make_services(stores)
    write_claude(stores)
    write_codex(stores)
    write_copilot(stores)
    run_id = add_framework_run(services, model="m1")
    monkeypatch.setattr(cli, "_session_services", lambda: services.sessions)

    def no_http(*args, **kwargs):  # session data is private: the CLI must never go through the HTTP API
        raise AssertionError("the sessions commands must not call the HTTP API")

    monkeypatch.setattr(cli, "api_client", no_http)
    yield SimpleNamespace(services=services, run_id=run_id, stores=stores)
    services.runs.close()


def run_json(*argv: str):
    out, err, code = invoke(*argv)
    assert code == 0, err
    return json.loads(out)


def test_list_prints_the_service_page_as_json(world):
    page = run_json("sessions", "list")
    assert page == world.services.sessions.list_sessions()
    assert {s["source"] for s in page["sessions"]} == {"claude", "codex", "copilot", "framework"}


def test_list_filters_and_pagination_flags(world):
    assert [s["id"] for s in run_json("sessions", "list", "--source", "codex")["sessions"]] == [f"codex:{CODEX_ID}"]
    assert [s["id"] for s in run_json("sessions", "list", "--text", "copilot")["sessions"]] == [f"copilot:{COPILOT_ID}"]
    assert [s["id"] for s in run_json("sessions", "list", "--surface", "framework")["sessions"]] == [f"framework:{world.run_id}"]
    assert run_json("sessions", "list", "--cwd", str(world.stores.other))["sessions"] == []
    assert [s["id"] for s in run_json("sessions", "list", "--since", "2026-10-03", "--until", "2026-10-04")["sessions"]] == [f"copilot:{COPILOT_ID}"]
    first = run_json("sessions", "list", "--limit", "3")
    assert len(first["sessions"]) == 3 and first["next_cursor"] == "0:3"
    second = run_json("sessions", "list", "--limit", "3", "--after", first["next_cursor"])
    assert len(second["sessions"]) == 1 and second["next_cursor"] is None


def test_show_and_items(world):
    assert run_json("sessions", "show", SID) == world.services.sessions.get_session(SID)
    items = run_json("sessions", "items", SID)
    assert items == world.services.sessions.read_items(SID)
    assert items["items"][0]["text"] == "hola <b>mundo</b> <script>x()</script>"
    page = run_json("sessions", "items", SID, "--limit", "1")
    rest = run_json("sessions", "items", SID, "--after", page["next_cursor"])
    assert [i["kind"] for i in page["items"] + rest["items"]] == [i["kind"] for i in items["items"]]
    framework = run_json("sessions", "items", f"framework:{world.run_id}")
    assert framework["items"][0]["text"].startswith("workflow_started")


def test_jsonl_prints_one_line_per_session_or_item(world):
    out, _, code = invoke("sessions", "list", "--jsonl")
    lines = out.strip().splitlines()
    assert code == 0 and [json.loads(line) for line in lines] == world.services.sessions.list_sessions()["sessions"]
    out, _, _ = invoke("sessions", "items", SID, "--jsonl")
    assert [json.loads(line) for line in out.strip().splitlines()] == world.services.sessions.read_items(SID)["items"]


def test_empty_roots_say_how_to_configure(world):
    world.services.settings.gui_session_roots = []
    page = run_json("sessions", "list")
    assert page["sessions"] == [] and "gui_session_roots" in page["hint"]
    out, err, code = invoke("sessions", "list", "--jsonl")
    assert code == 0 and out == "" and "gui_session_roots" in err  # data on stdout, advice on stderr


@pytest.mark.parametrize("argv,code", [
    (("show", "not-an-id"), "invalid_session_id"), (("items", "claude:xyz"), "invalid_session_id"),
    (("items", SID, "--after", "abc"), "invalid_cursor"), (("list", "--after", "5"), "invalid_cursor"),
    (("items", SID, "--limit", "501"), "invalid_request"), (("list", "--limit", "0"), "invalid_request"),
    (("list", "--source", "nope"), "invalid_request"), (("list", "--since", "soon"), "invalid_request"),
    (("show", f"claude:{CLAUDE_ID[:-1]}9"), "not_found"), (("show", "framework:" + "0" * 32), "not_found"),
])
def test_errors_exit_1_with_the_service_code(world, argv, code):
    out, err, status = invoke("sessions", *argv)
    assert status == 1 and out == "" and err.startswith(f"ERROR: {code}: ")


def test_sessions_requires_a_subcommand(world):
    _, err, status = invoke("sessions")
    assert status == 2 and "usage" in err


def test_the_default_service_is_built_in_process(tmp_path, monkeypatch):
    stores = make_stores(tmp_path)
    write_claude(stores)
    monkeypatch.setattr(cli, "get_settings", lambda: settings_for(stores))
    service = cli._session_services()
    assert isinstance(service, SessionServices)
    assert [s["id"] for s in service.list_sessions(source="claude")["sessions"]] == [SID]


# -------- multiagent gui --------
def test_gui_subcommand_runs_the_same_server_as_the_module(monkeypatch):
    calls = []
    monkeypatch.setattr(gui_main, "run", lambda **kwargs: calls.append(kwargs))
    assert invoke("gui", "--port", "9123", "--real", "--no-browser")[2] == 0
    assert invoke("gui")[2] == 0
    gui_main.main(["--port", "9124", "--no-browser"])  # `python -m multiagent.gui` keeps working
    gui_main.main([])
    assert calls == [
        {"port": 9123, "real": True, "no_browser": True},
        {"port": 0, "real": False, "no_browser": False},
        {"port": 9124, "real": False, "no_browser": True},
        {"port": 0, "real": False, "no_browser": False},
    ]


def test_gui_options_are_defined_once(capsys):
    for entry in (lambda: cli.main(["gui", "--help"]), lambda: gui_main.main(["--help"])):
        with pytest.raises(SystemExit) as caught:
            entry()
        assert caught.value.code == 0
        text = capsys.readouterr().out
        assert "--port" in text and "--real" in text and "--no-browser" in text
    assert gui_main.__name__ == "multiagent.gui.__main__" and callable(gui_main.main)
