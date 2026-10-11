# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI-T04 parity: the service (facade), the /gui/api HTTP routes and the CLI answer the same, errors included."""
from __future__ import annotations

import contextlib
import io
import json

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.gui.token import ensure_token
from multiagent.services import ServiceError
from session_fixtures import (
    CLAUDE_ID,
    CODEX_ID,
    COPILOT_ID,
    add_framework_run,
    make_services,
    make_stores,
    write_claude,
    write_codex,
    write_copilot,
)


@pytest.fixture
def world(tmp_path, monkeypatch):
    stores = make_stores(tmp_path)
    services = make_services(stores)
    write_claude(stores)
    write_codex(stores)
    write_copilot(stores)
    run_id = add_framework_run(services, model="m1", events=3)
    token = ensure_token(services.settings.gui_token_file)
    monkeypatch.setattr(cli, "_session_services", lambda: services.sessions)
    with TestClient(create_app(services=services), base_url="http://localhost") as http:
        yield services, http, {"Authorization": f"Bearer {token}"}, run_id
    services.runs.close()


def via_service(services, command, session_id, params):
    if command == "list":
        return services.sessions.list_sessions(**params)
    if command == "show":
        return services.sessions.get_session(session_id)
    return services.sessions.read_items(session_id, **params)


def via_http(http, headers, command, session_id, params):
    base = "/gui/api/sessions"
    path = {"list": base, "show": f"{base}/{session_id}", "items": f"{base}/{session_id}/items"}[command]
    return http.get(path, params=params, headers=headers)


def via_cli(command, session_id, params):
    argv = ["sessions", command] + ([session_id] if session_id else [])
    for key, value in params.items():
        argv += [f"--{key}", str(value)]
    out, err = io.StringIO(), io.StringIO()
    status = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(argv)
        except SystemExit as exc:
            status = exc.code
    return status, out.getvalue(), err.getvalue()


def three_ways(world, command, session_id=None, **params):
    services, http, headers, _ = world
    from_service = via_service(services, command, session_id, params)
    response = via_http(http, headers, command, session_id, params)
    status, out, _ = via_cli(command, session_id, params)
    assert response.status_code == 200 and status == 0
    return from_service, response.json(), json.loads(out)


def ids_of(world):
    run_id = world[3]
    return [f"claude:{CLAUDE_ID}", f"codex:{CODEX_ID}", f"copilot:{COPILOT_ID}", f"framework:{run_id}"]


@pytest.mark.parametrize("params", [
    {}, {"source": "codex"}, {"source": "framework"}, {"surface": "cli"}, {"text": "work"}, {"limit": 2},
    {"limit": 2, "after": "0:2"}, {"since": "2026-10-02", "until": "2026-10-03T23:59:59Z"}, {"limit": 1, "after": "0:99"},
])
def test_list_is_identical_everywhere(world, params):
    a, b, c = three_ways(world, "list", **params)
    assert a == b == c
    assert set(a) == {"sessions", "next_cursor", "warnings", "hint"}


def test_list_with_a_folder_filter_is_identical(world):
    folder = str(make_stores(world[0].settings.local_dir.parent).work)
    a, b, c = three_ways(world, "list", cwd=folder)
    assert a == b == c and len(a["sessions"]) == 3


def test_empty_roots_page_is_identical(world):
    world[0].settings.gui_session_roots = []
    a, b, c = three_ways(world, "list")
    assert a == b == c and a["sessions"] == [] and a["hint"]


def test_show_is_identical_for_every_source(world):
    for session_id in ids_of(world):
        a, b, c = three_ways(world, "show", session_id)
        assert a == b == c and a["id"] == session_id


@pytest.mark.parametrize("params", [{}, {"limit": 1}, {"limit": 500}])
def test_items_are_identical_for_every_source(world, params):
    for session_id in ids_of(world):
        a, b, c = three_ways(world, "items", session_id, **params)
        assert a == b == c and set(a) == {"items", "next_cursor", "done", "warnings"}


def test_items_after_a_cursor_are_identical(world):
    for session_id in ids_of(world):
        first = world[0].sessions.read_items(session_id, limit=1)
        a, b, c = three_ways(world, "items", session_id, after=first["next_cursor"])
        assert a == b == c


@pytest.mark.parametrize("command,session_id,params,code,status", [
    ("show", "bogus", {}, "invalid_session_id", 400),
    ("items", "claude:not-a-uuid", {}, "invalid_session_id", 400),
    ("items", f"claude:{CLAUDE_ID}", {"after": "abc"}, "invalid_cursor", 400),
    ("list", None, {"after": "abc"}, "invalid_cursor", 400),
    ("items", f"claude:{CLAUDE_ID}", {"limit": 501}, "invalid_request", 422),
    ("list", None, {"limit": 0}, "invalid_request", 422),
    ("list", None, {"source": "nope"}, "invalid_request", 422),
    ("show", "codex:" + CLAUDE_ID, {}, "not_found", 404),
    ("items", "framework:" + "0" * 32, {}, "not_found", 404),
])
def test_errors_are_identical_everywhere(world, command, session_id, params, code, status):
    services, http, headers, _ = world
    with pytest.raises(ServiceError) as caught:
        via_service(services, command, session_id, params)
    error = caught.value
    assert (error.code, error.status) == (code, status)

    response = via_http(http, headers, command, session_id, params)
    assert response.status_code == status and response.json() == {"detail": error.to_payload()}

    cli_status, out, err = via_cli(command, session_id, params)
    assert cli_status == 1 and out == "" and err == f"ERROR: {code}: {error.message}\n"
