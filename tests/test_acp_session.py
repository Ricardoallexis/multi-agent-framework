# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Interactive ACP API with a synthetic transport; no executable or network."""
from __future__ import annotations

import time
from threading import Event

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.executors.transport import AgentEvent, PermissionAsk
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices


class FakeSession:
    def __init__(self, sink):
        self.sink = sink
        self.cancelled = False
        self.released = Event()

    def prompt(self, text):
        if text == "block":
            self.released.wait(2)
            self.sink.on_event(AgentEvent("end_turn", data={"stop_reason": "cancelled"}))
            return
        self.sink.on_event(AgentEvent("message", data={"text": text}))
        self.sink.on_event(AgentEvent("end_turn", data={"stop_reason": "end_turn"}))

    def cancel(self):
        self.cancelled = True
        self.released.set()
        return True

    def close(self):
        return None


class FakeTransport:
    def __init__(self, executable):
        assert executable == "synthetic-acp"

    def open_session(self, request, sink):
        assert request.workdir.is_absolute()
        return FakeSession(sink)


def test_acp_consent_session_routes_and_replay(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "token")
    services = ApplicationServices.create(settings, dry_run=True)
    folder = tmp_path / "area"
    folder.mkdir()
    services.register_bundle("office-area", folder)
    services.offices.office_of = lambda bundle: ("office", "area") if bundle == "office-area" else None
    services.offices.area_class = lambda bundle: "non_sensitive"
    connections = services.connections
    connections._acp_resolver = lambda cwd: "synthetic-acp"
    connections._acp_probe_result = {"status": "compatible", "protocol_version": 1}
    connections._acp_probe_path = "synthetic-acp"
    asked = services.executor_activations.request("codex_acp")
    assert "Codex ACP" in asked["note"]
    services.user_approvals.decide(asked["approval_id"], "approve")  # synthetic person decision
    assert services.executor_activations.activate("codex_acp", asked["approval_id"])["state"] == "active"
    assert "codex_cli" not in services.executor_activations._read()
    row = connections.check("codex_acp")
    assert (row["installed"], row["compatible"], row["authenticated"], row["activated"]) == (True, True, None, True)
    assert row["selected_transport"] == "cli"
    services.runs.acp.transport_factory = FakeTransport
    token = ensure_token(settings.gui_token_file)
    headers = {"Authorization": f"Bearer {token}"}
    app = create_app(services=services)
    try:
        with TestClient(app, base_url="http://localhost") as client:
            consent = client.get("/gui/api/connections/codex_acp/consent?bundle=office-area", headers=headers)
            assert consent.status_code == 200
            assert consent.json()["configured"] is False
            started = client.post("/gui/api/threads/acp", headers=headers,
                                  json={"bundle": "office-area", "text": "<untrusted>"})
            assert started.status_code == 200, started.text
            identifier = started.json()["id"]
            assert connections.check("codex_acp")["authenticated"] is True
            assert started.json()["consent_configured"] is False
            sink = services.runs.acp._get(identifier)["sink"]
            assert sink.on_permission(PermissionAsk("read", None, ("allow_once",))) == "reject_once"
            for _ in range(100):
                page = client.get(f"/gui/api/threads/acp/{identifier}", headers=headers).json()
                if not page["active"]:
                    break
                time.sleep(0.01)
            assert [event["kind"] for event in page["events"]] == ["prompt", "message", "end_turn"]
            assert page["events"][0]["data"]["text"] == "<untrusted>"
            assert client.get(f"/gui/api/threads/acp/{identifier}?after=1", headers=headers).json()["events"][0]["cursor"] == 2
            stream = client.get(f"/gui/api/threads/acp/{identifier}/stream", headers=headers)
            assert "id: 1\nevent: item" in stream.text
            assert "id: 2\nevent: item" in stream.text
            assert "id: 3\nevent: item" in stream.text
            changed = client.put("/gui/api/connections/codex_acp/consent", headers=headers,
                                 json={"bundle": "office-area", "allow_write": True})
            assert changed.status_code == 200, changed.text
            assert changed.json()["allow_write"] is True
            new_session = client.post("/gui/api/threads/acp", headers=headers,
                                      json={"bundle": "office-area", "text": "new"}).json()
            configured_sink = services.runs.acp._get(new_session["id"])["sink"]
            assert new_session["consent_configured"] is True
            assert configured_sink.on_permission(PermissionAsk("read", str(folder / "file.txt"),
                                                             ("allow_once",), (str(folder / "file.txt"),))) == "allow_once"
            assert configured_sink.on_permission(PermissionAsk("write", str(folder / "file.txt"),
                                                             ("allow_once",), (str(folder / "file.txt"),))) == "allow_once"
            assert configured_sink.on_permission(PermissionAsk("write", str(tmp_path / "outside.txt"),
                                                             ("allow_once",), (str(tmp_path / "outside.txt"),))) == "reject_once"
            assert configured_sink.on_permission(PermissionAsk("execute", None, ("allow_once",))) == "reject_once"
            old = client.get(f"/gui/api/threads/acp/{identifier}", headers=headers)
            assert old.status_code == 200 and old.json()["saved"] is True
            for _ in range(100):
                if not client.get(f"/gui/api/threads/acp/{new_session['id']}", headers=headers).json()["active"]:
                    break
                time.sleep(0.01)
            next_turn = client.post(f"/gui/api/threads/acp/{new_session['id']}/prompts", headers=headers,
                                    json={"text": "block"})
            assert next_turn.status_code == 200
            cancelled = client.post(f"/gui/api/threads/acp/{new_session['id']}/cancel", headers=headers, json={})
            assert cancelled.status_code == 200 and cancelled.json()["cancelled"] is True
            for _ in range(100):
                page = client.get(f"/gui/api/threads/acp/{new_session['id']}", headers=headers).json()
                if not page["active"]:
                    break
                time.sleep(0.01)
            assert page["events"][-1]["data"]["stop_reason"] == "cancelled"
            assert sink.on_permission(PermissionAsk("write", None, ("allow_once",))) == "reject_once"
    finally:
        services.runs.close()


def test_saved_history_survives_new_session_service_and_routes_delete_it(env):
    from multiagent.services.runs import AcpInteractiveSessions

    services, _, client, headers = env
    identifier = client.post("/gui/api/threads/acp", headers=headers,
                             json={"bundle": "office-area", "text": "hello"}).json()["id"]
    for _ in range(100):
        if not services.runs.acp.page(identifier)["active"]:
            break
        time.sleep(0.01)
    assert services.runs.acp.page(identifier)["events"][0]["kind"] == "prompt"
    services.runs.acp.close_all()
    services.runs.acp = AcpInteractiveSessions(services)
    saved = client.get(f"/gui/api/threads/acp/{identifier}", headers=headers)
    assert saved.status_code == 200 and saved.json()["saved"] is True
    assert [item["kind"] for item in saved.json()["events"]] == ["prompt", "message", "end_turn"]
    rows = client.get("/gui/api/threads/acp", headers=headers).json()["conversations"]
    assert rows[0]["id"] == identifier and rows[0]["active"] is False
    assert "id: 1\nevent: item" in client.get(f"/gui/api/threads/acp/{identifier}/stream?after=3",
                                               headers=headers).text
    assert client.post(f"/gui/api/threads/acp/{identifier}/delete", headers=headers, json={}).json()["deleted"] is True
    assert client.get(f"/gui/api/threads/acp/{identifier}", headers=headers).status_code == 404
    assert client.get("/gui/api/threads/acp", headers=headers).json()["conversations"] == []


def test_sensitive_area_requires_explicit_history_switch(env):
    services, _, client, headers = env
    services.offices.area_class = lambda bundle: "sensitive"
    consent = client.get("/gui/api/connections/codex_acp/consent?bundle=office-area", headers=headers).json()
    assert consent["keep_history"] is False
    first = client.post("/gui/api/threads/acp", headers=headers,
                        json={"bundle": "office-area", "text": "private"}).json()["id"]
    for _ in range(100):
        if not services.runs.acp.page(first)["active"]:
            break
        time.sleep(0.01)
    assert services.runs.acp.history.page(first) is None
    changed = client.put("/gui/api/connections/codex_acp/consent", headers=headers,
                         json={"bundle": "office-area", "allow_write": False, "keep_history": True})
    assert changed.status_code == 200 and changed.json()["keep_history"] is True
    second = client.post("/gui/api/threads/acp", headers=headers,
                         json={"bundle": "office-area", "text": "private"}).json()["id"]
    for _ in range(100):
        if not services.runs.acp.page(second)["active"]:
            break
        time.sleep(0.01)
    assert services.runs.acp.history.page(second)["saved"] is True
    assert client.post("/gui/api/threads/acp/delete-all", headers=headers, json={}).json()["deleted"] == 1
    assert services.runs.acp.history.page(second) is None


@pytest.mark.parametrize("classification", ["sensitive", "restricted", "undeclared", "missing_office", "read_error"])
def test_history_defaults_off_when_area_class_is_not_known_normal(env, classification):
    services, _, client, headers = env
    if classification == "read_error":
        def area_class(bundle):
            raise OSError("synthetic classification read error")
    else:
        area_class = lambda bundle: (None if classification in ("undeclared", "missing_office")
                                     else classification)
    services.offices.area_class = area_class
    response = client.get("/gui/api/connections/codex_acp/consent?bundle=office-area", headers=headers)
    assert response.status_code == 200 and response.json()["keep_history"] is False
    identifier = start(client, headers)
    settled(client, headers, identifier)
    assert services.runs.acp.history.page(identifier) is None


@pytest.mark.parametrize("classification", [None, "sensitive"])
def test_explicit_history_true_overrides_area_class(env, classification):
    services, _, client, headers = env
    services.offices.area_class = lambda bundle: classification
    response = client.put("/gui/api/connections/codex_acp/consent", headers=headers,
                          json={"bundle": "office-area", "allow_write": False, "keep_history": True})
    assert response.status_code == 200 and response.json()["keep_history"] is True
    identifier = start(client, headers)
    settled(client, headers, identifier)
    assert services.runs.acp.history.page(identifier)["saved"] is True


def test_put_consent_without_history_field_preserves_explicit_value(env):
    services, _, client, headers = env
    services.offices.area_class = lambda bundle: "sensitive"
    first = client.put("/gui/api/connections/codex_acp/consent", headers=headers,
                       json={"bundle": "office-area", "allow_write": False, "keep_history": True})
    assert first.status_code == 200
    second = client.put("/gui/api/connections/codex_acp/consent", headers=headers,
                        json={"bundle": "office-area", "allow_write": True})
    assert second.status_code == 200 and second.json()["keep_history"] is True
    assert services.gui_settings.acp_consent("office/area")["keep_history"] is True


def test_deleting_a_live_conversation_closes_it(env):
    services, _, client, headers = env
    identifier = client.post("/gui/api/threads/acp", headers=headers,
                             json={"bundle": "office-area", "text": "hello"}).json()["id"]
    item = services.runs.acp._get(identifier)
    closed = []
    item["session"].close = lambda: closed.append(True)
    response = client.post(f"/gui/api/threads/acp/{identifier}/delete", headers=headers, json={})
    assert response.status_code == 200 and closed == [True]
    assert services.runs.acp.history.page(identifier) is None
    assert client.get(f"/gui/api/threads/acp/{identifier}", headers=headers).status_code == 404


def test_acp_rejects_unverified_area_and_has_no_cli_fallback(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings, dry_run=True)
    folder = tmp_path / "area"
    folder.mkdir()
    services.register_bundle("office-area", folder)
    services.offices.office_of = lambda bundle: None
    services.runs.acp.transport_factory = FakeTransport
    try:
        try:
            services.runs.acp.start("office-area", "hello")
        except Exception as error:
            assert error.details["reason"] == "acp_area_required"
        else:
            assert False, "an unverified area must not launch ACP"
    finally:
        services.runs.close()


# ---------------------------------------------------------------------------------------------------------------
# Round 2: one test per guarantee.
# ---------------------------------------------------------------------------------------------------------------
import asyncio
import subprocess
from pathlib import Path

from multiagent.executors.acp import AcpConsent
from multiagent.executors.transport import CliTransport
from multiagent.gui import events
from multiagent.services.errors import NOT_FOUND, ServiceError
from multiagent.services.executors import ExecutorServices
from multiagent.services.runs import _AcpSink


def build(tmp_path, *, installed=True, probed=True, activated=True, area=True):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "token", antigravity_kill_switch=tmp_path / "agy-off")
    services = ApplicationServices.create(settings, dry_run=True)
    folder = tmp_path / "area"
    folder.mkdir()
    services.register_bundle("office-area", folder)
    services.offices.office_of = lambda bundle: ("office", "area") if area and bundle == "office-area" else None
    services.offices.area_class = lambda bundle: "non_sensitive"
    connections = services.connections
    connections._acp_resolver = lambda cwd: "synthetic-acp" if installed else None
    if probed:
        connections._acp_probe_result = {"status": "compatible", "protocol_version": 1}
        connections._acp_probe_path = "synthetic-acp"
    if activated:
        asked = services.executor_activations.request("codex_acp")
        services.user_approvals.decide(asked["approval_id"], "approve")  # synthetic person decision
        services.executor_activations.activate("codex_acp", asked["approval_id"])
    services.runs.acp.transport_factory = FakeTransport
    return services, folder


@pytest.fixture
def env(tmp_path):
    services, folder = build(tmp_path)
    headers = {"Authorization": f"Bearer {ensure_token(services.settings.gui_token_file)}"}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield services, folder, client, headers
    services.runs.close()


@pytest.fixture
def no_cli(monkeypatch):
    """A double that fails if the CLI path is touched: neither CliTransport nor run_task may be called."""
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("the CLI path must not be used after an ACP error")

    monkeypatch.setattr(CliTransport, "open_session", forbidden)
    monkeypatch.setattr(CliTransport, "__init__", forbidden)
    monkeypatch.setattr(ExecutorServices, "run_task", forbidden)
    return calls


def settled(client, headers, identifier):
    for _ in range(200):
        page = client.get(f"/gui/api/threads/acp/{identifier}", headers=headers).json()
        if not page["active"]:
            return page
        time.sleep(0.01)
    raise AssertionError("the turn did not finish")


def start(client, headers, text="hello"):
    response = client.post("/gui/api/threads/acp", headers=headers, json={"bundle": "office-area", "text": text})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def reason(response):
    return response.json()["detail"]["details"]["reason"]


# ---- point 1: installed is not probed ----
def test_activation_request_without_executable_is_not_installed(tmp_path):
    services, _ = build(tmp_path, installed=False, probed=False, activated=False)
    with pytest.raises(ServiceError) as caught:
        services.executor_activations.request("codex_acp")
    assert caught.value.status == 409 and caught.value.details["reason"] == "not_installed"
    row = {item["id"]: item for item in services.executor_activations.status()}["codex_acp"]
    assert row["state"] == "not_installed" and row["probe_required"] is False
    services.runs.close()


def test_activation_request_installed_but_unprobed_needs_a_probe(tmp_path):
    services, _ = build(tmp_path, probed=False, activated=False)
    with pytest.raises(ServiceError) as caught:
        services.executor_activations.request("codex_acp")
    assert caught.value.status == 409 and caught.value.details["reason"] == "acp_probe_required"
    row = {item["id"]: item for item in services.executor_activations.status()}["codex_acp"]
    assert row["state"] == "ready" and row["probe_required"] is True
    assert services.connections.check("codex_acp")["missing_step"] == "probe"
    services.runs.close()


def test_activation_request_after_a_compatible_probe_succeeds(tmp_path):
    services, _ = build(tmp_path, activated=False)
    asked = services.executor_activations.request("codex_acp")
    assert asked["approval_id"] and "Codex ACP" in asked["note"]
    row = {item["id"]: item for item in services.executor_activations.status()}["codex_acp"]
    assert row["state"] == "ready" and row["probe_required"] is False
    services.runs.close()


# ---- point 2: SSE reconnection ----
def test_stream_resumes_after_query_cursor(env):
    services, _, client, headers = env
    identifier = start(client, headers, "one")
    settled(client, headers, identifier)
    text = client.get(f"/gui/api/threads/acp/{identifier}/stream?after=1", headers=headers).text
    assert "id: 1\n" not in text and "id: 2\nevent: item" in text


def test_stream_resumes_after_last_event_id_header(env):
    services, _, client, headers = env
    identifier = start(client, headers, "one")
    settled(client, headers, identifier)
    text = client.get(f"/gui/api/threads/acp/{identifier}/stream",
                      headers={**headers, "Last-Event-ID": "1"}).text
    assert "id: 1\n" not in text and "id: 2\nevent: item" in text


def test_stream_uses_the_larger_of_cursor_and_last_event_id(env):
    services, _, client, headers = env
    identifier = start(client, headers, "one")
    settled(client, headers, identifier)
    text = client.get(f"/gui/api/threads/acp/{identifier}/stream?after=3",
                      headers={**headers, "Last-Event-ID": "1"}).text
    assert "event: item" not in text


def test_active_session_delivers_new_events_after_the_cursor():
    # While the session is still active, a follower sees only the events after its cursor, then the rest.
    pages = [
        {"events": [{"cursor": 2, "kind": "message", "tool_status": None, "data": {}}], "active": True},
        {"events": [{"cursor": 3, "kind": "end_turn", "tool_status": None, "data": {}}], "active": False},
    ]
    seen = []

    def read(cursor):
        seen.append(cursor)
        return pages.pop(0)

    async def collect():
        return [chunk async for chunk in events.stream_acp(read, after=1, heartbeat=60, poll=0.001, max_seconds=5)]

    chunks = asyncio.run(collect())
    assert seen == [1, 2]
    assert [chunk for chunk in chunks if chunk.startswith("id:")] == [
        'id: 2\nevent: item\ndata: {"cursor":2,"kind":"message","tool_status":null,"data":{}}\n\n',
        'id: 3\nevent: item\ndata: {"cursor":3,"kind":"end_turn","tool_status":null,"data":{}}\n\n']


def test_stream_restarts_once_when_live_session_becomes_saved():
    seen = []
    live = {"events": [{"cursor": 5, "kind": "message", "data": {"text": "live"}}],
            "active": True, "saved": False}
    saved = [{"cursor": index, "kind": "message", "data": {"text": str(index)}}
             for index in range(1, 4)]

    def read(cursor):
        seen.append(cursor)
        if len(seen) == 1:
            return live
        return {"events": saved[cursor:], "active": False, "saved": True}

    async def collect():
        return [chunk async for chunk in events.stream_acp(read, after=4, heartbeat=60,
                                                            poll=0.001, max_seconds=5)]

    chunks = asyncio.run(collect())
    assert seen == [4, 5, 0]
    assert [int(chunk.split("\n", 1)[0][4:]) for chunk in chunks if chunk.startswith("id:")] == [5, 1, 2, 3]


# ---- point 6: a stream outlives its session ----
def test_stream_ends_cleanly_with_a_notice_when_the_session_closes():
    calls = []

    def read(cursor):
        calls.append(cursor)
        if len(calls) == 1:
            return {"events": [], "active": True}
        raise ServiceError(NOT_FOUND, "ACP session not found", status=404)

    async def collect():
        return [chunk async for chunk in events.stream_acp(read, after=0, heartbeat=60, poll=0.001, max_seconds=5)]

    chunks = asyncio.run(collect())
    assert chunks[0] == ": connected\n\n"
    assert chunks[-1].startswith("event: error\n") and "id:" not in chunks[-1]
    assert '"code":"not_found"' in chunks[-1]
    assert "ACP session not found" in chunks[-1]


# ---- point 2: consent policy, one guarantee per test ----
def sink(tmp_path, *, allow_write):
    folder = tmp_path / "area"
    folder.mkdir(exist_ok=True)
    return _AcpSink(AcpConsent(scope_id="office/area", allow_write=allow_write, allow_always=False), folder.resolve()), folder.resolve()


def ask(tool, *targets):
    return PermissionAsk(tool, None, ("allow_once", "allow_always"), tuple(str(target) for target in targets))


def test_read_only_consent_allows_think(tmp_path):
    guard, _ = sink(tmp_path, allow_write=False)
    assert guard.on_permission(ask("think")) == "allow_once"


@pytest.mark.parametrize("tool", ["read", "search"])
def test_read_only_consent_allows_read_and_search_inside_the_folder(tmp_path, tool):
    guard, folder = sink(tmp_path, allow_write=False)
    assert guard.on_permission(ask(tool, folder / "file.txt")) == "allow_once"


@pytest.mark.parametrize("tool", ["edit", "write"])
def test_read_only_consent_denies_file_changes(tmp_path, tool):
    guard, folder = sink(tmp_path, allow_write=False)
    assert guard.on_permission(ask(tool, folder / "file.txt")) == "reject_once"


@pytest.mark.parametrize("tool", ["read", "search"])
def test_read_outside_the_folder_is_denied(tmp_path, tool):
    guard, _ = sink(tmp_path, allow_write=True)
    assert guard.on_permission(ask(tool, tmp_path / "outside.txt")) == "reject_once"


def test_read_without_targets_is_denied(tmp_path):
    guard, _ = sink(tmp_path, allow_write=True)
    assert guard.on_permission(ask("read")) == "reject_once"
    assert guard.on_permission(ask("search")) == "reject_once"


def test_allow_always_is_never_granted(tmp_path):
    guard, folder = sink(tmp_path, allow_write=True)
    for tool in ("think", "read", "search", "edit", "write", "execute"):
        assert guard.on_permission(ask(tool, folder / "a.txt")) in {"allow_once", "reject_once"}
    assert "allow_always" not in {guard.on_permission(ask("think")), guard.on_permission(ask("edit", folder / "a"))}


def test_relative_target_is_denied(tmp_path):
    guard, _ = sink(tmp_path, allow_write=True)
    assert guard.on_permission(ask("read", "file.txt")) == "reject_once"


# ---- point 4: edit versus write ----
@pytest.mark.parametrize("tool", ["edit", "write"])
def test_write_consent_allows_edit_and_write_inside_the_folder(tmp_path, tool):
    guard, folder = sink(tmp_path, allow_write=True)
    assert guard.on_permission(ask(tool, folder / "a.txt", folder / "b.txt")) == "allow_once"


@pytest.mark.parametrize("tool", ["edit", "write"])
def test_write_consent_denies_when_any_target_is_outside(tmp_path, tool):
    guard, folder = sink(tmp_path, allow_write=True)
    assert guard.on_permission(ask(tool, folder / "a.txt", tmp_path / "outside.txt")) == "reject_once"


@pytest.mark.parametrize("tool", ["edit", "write"])
def test_write_consent_denies_a_change_without_targets(tmp_path, tool):
    guard, _ = sink(tmp_path, allow_write=True)
    assert guard.on_permission(ask(tool)) == "reject_once"


@pytest.mark.parametrize("tool", ["delete", "move", "execute", "fetch", "switch_mode", "other", "unheard_of"])
def test_write_consent_always_rejects_other_kinds(tmp_path, tool):
    guard, folder = sink(tmp_path, allow_write=True)
    assert guard.on_permission(ask(tool, folder / "a.txt")) == "reject_once"
    assert guard.on_permission(ask(tool)) == "reject_once"


# ---- point 11: a target that cannot be resolved ----
def test_unresolvable_target_is_rejected_without_raising(tmp_path, monkeypatch):
    guard, folder = sink(tmp_path, allow_write=True)

    def broken(self, *args, **kwargs):
        raise OSError("cannot resolve")

    monkeypatch.setattr(Path, "resolve", broken)
    assert guard.on_permission(ask("read", folder / "a.txt")) == "reject_once"
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("loop")))
    assert guard.on_permission(ask("edit", folder / "a.txt")) == "reject_once"
    assert guard.on_permission(ask("think")) == "allow_once"


# ---- point 2: 409 errors and no CLI fallback ----
def test_session_without_activation_is_409_and_never_falls_back(tmp_path, no_cli):
    services, _ = build(tmp_path, activated=False)
    token = ensure_token(services.settings.gui_token_file)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post("/gui/api/threads/acp", headers=headers,
                               json={"bundle": "office-area", "text": "hi"})
    assert response.status_code == 409
    assert reason(response) == "acp_activation_required"
    assert no_cli == []
    services.runs.close()


def test_session_with_an_unprobed_adapter_is_409_acp_unavailable(tmp_path, no_cli):
    services, _ = build(tmp_path, probed=False, activated=False)
    with pytest.raises(ServiceError) as caught:
        services.runs.acp.start("office-area", "hi")
    assert caught.value.status == 409 and caught.value.details["reason"] == "acp_unavailable"
    assert no_cli == []
    services.runs.close()


def test_a_saved_activation_without_a_probe_is_acp_probe_required(tmp_path, no_cli):
    services, _ = build(tmp_path)
    services.connections._acp_probe_result = None       # a restart loses the in-memory evidence
    services.connections._acp_probe_path = None
    with pytest.raises(ServiceError) as caught:
        services.executor_activations.check_launch("codex_acp")
    assert caught.value.status == 409 and caught.value.details["reason"] == "acp_probe_required"
    assert no_cli == []
    services.runs.close()


def test_a_missing_adapter_is_409_acp_unavailable(tmp_path, no_cli):
    services, _ = build(tmp_path, installed=False, probed=False, activated=False)
    with pytest.raises(ServiceError) as caught:
        services.runs.acp.start("office-area", "hi")
    assert caught.value.status == 409 and caught.value.details["reason"] == "acp_unavailable"
    assert no_cli == []
    services.runs.close()


def test_a_transport_that_fails_to_open_is_409_acp_unavailable(tmp_path, no_cli):
    services, _ = build(tmp_path)

    class Broken:
        def __init__(self, executable):
            pass

        def open_session(self, request, sink):
            raise OSError("adapter vanished")

    services.runs.acp.transport_factory = Broken
    with pytest.raises(ServiceError) as caught:
        services.runs.acp.start("office-area", "hi")
    assert caught.value.status == 409 and caught.value.details["reason"] == "acp_unavailable"
    assert services.connections.check("codex_acp")["authenticated"] is None
    assert no_cli == []
    services.runs.close()


def test_unverified_area_is_409_and_never_falls_back(tmp_path, no_cli):
    services, _ = build(tmp_path, area=False)
    with pytest.raises(ServiceError) as caught:
        services.runs.acp.start("office-area", "hi")
    assert caught.value.details["reason"] == "acp_area_required"
    assert no_cli == []
    services.runs.close()


def test_a_second_prompt_during_an_active_turn_is_409(env, no_cli):
    services, _, client, headers = env
    identifier = start(client, headers, "block")
    again = client.post(f"/gui/api/threads/acp/{identifier}/prompts", headers=headers, json={"text": "more"})
    assert again.status_code == 409
    client.post(f"/gui/api/threads/acp/{identifier}/cancel", headers=headers, json={})
    settled(client, headers, identifier)
    assert no_cli == []


def test_a_turn_that_raises_is_an_acp_error_event_not_a_cli_run(env, no_cli):
    services, _, client, headers = env

    class Failing(FakeSession):
        def prompt(self, text):
            raise OSError("pipe closed")

    services.runs.acp.transport_factory = type("T", (), {
        "__init__": lambda self, executable: None, "open_session": lambda self, request, sink: Failing(sink)})
    identifier = start(client, headers)
    page = settled(client, headers, identifier)
    assert page["events"][-1]["kind"] == "error"
    assert no_cli == []


# ---- point 2: consent routes ----
def test_get_consent_for_an_unknown_bundle_is_404(env):
    _, _, client, headers = env
    response = client.get("/gui/api/connections/codex_acp/consent?bundle=missing", headers=headers)
    assert response.status_code == 404


def test_get_consent_without_a_verified_area_is_409(tmp_path):
    services, _ = build(tmp_path, area=False)
    headers = {"Authorization": f"Bearer {ensure_token(services.settings.gui_token_file)}"}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.get("/gui/api/connections/codex_acp/consent?bundle=office-area", headers=headers)
    assert response.status_code == 409 and reason(response) == "acp_area_required"
    services.runs.close()


def test_put_consent_rejects_a_scope_from_the_client(env):
    services, _, client, headers = env
    response = client.put("/gui/api/connections/codex_acp/consent", headers=headers,
                          json={"bundle": "office-area", "allow_write": True, "scope": "other/area"})
    assert response.status_code == 422
    assert services.gui_settings.acp_consent("other/area") is None
    assert services.gui_settings.acp_consent("office/area") is None


# ---- point 3: closing sessions ----
def test_deactivate_closes_every_interactive_session(env):
    services, _, client, headers = env
    identifier = start(client, headers)
    settled(client, headers, identifier)
    services.executor_activations.deactivate("codex_acp")
    assert client.post(f"/gui/api/threads/acp/{identifier}/prompts", headers=headers,
                       json={"text": "again"}).status_code == 404


def test_stop_all_closes_every_interactive_session(env):
    services, _, client, headers = env
    identifier = start(client, headers)
    settled(client, headers, identifier)
    services.executor_activations.stop_all()
    assert client.post(f"/gui/api/threads/acp/{identifier}/prompts", headers=headers,
                       json={"text": "again"}).status_code == 404


def test_deactivating_another_executor_leaves_sessions_open(env):
    services, _, client, headers = env
    identifier = start(client, headers)
    settled(client, headers, identifier)
    with pytest.raises(ServiceError):
        services.executor_activations.deactivate("codex_cli")   # never activated: not_active
    assert client.get(f"/gui/api/threads/acp/{identifier}", headers=headers).status_code == 200


# ---- point 5: install data and no process ----
def test_row_carries_the_install_data_and_nothing_runs_it(env, monkeypatch):
    services, _, client, headers = env

    def forbidden(*args, **kwargs):
        raise AssertionError("no process may be started to show the install data")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    row = client.post("/gui/api/connections/codex_acp/check", headers=headers, json={}).json()
    assert row["install"]["command"] == "npm install -g @agentclientprotocol/codex-acp@2.12.0"
    assert row["install"]["version"] == "2.12.0" and row["install"]["license"] == "Apache-2.0"
    assert row["install"]["package"] == "@agentclientprotocol/codex-acp"
    assert row["install"]["source"] == "https://www.npmjs.com/package/@agentclientprotocol/codex-acp"


# ---- point 8: the session limit comes before the process ----
def test_session_limit_is_checked_before_the_adapter_opens(tmp_path):
    services, _ = build(tmp_path)
    opened = []

    class Counting:
        def __init__(self, executable):
            pass

        def open_session(self, request, sink):
            opened.append(1)
            return FakeSession(sink)

    services.runs.acp.transport_factory = Counting
    acp = services.runs.acp
    for index in range(16):
        acp._sessions[f"s{index}"] = {"session": FakeSession(None), "sink": None, "bundle": "office-area",
                                      "scope": "office/area", "consent_configured": False, "active": False,
                                      "cancel_early": False}
    with pytest.raises(ServiceError) as caught:
        acp.start("office-area", "hi")
    assert caught.value.details["reason"] == "acp_session_limit"
    assert opened == [] and acp._opening == 0 and len(acp._sessions) == 16
    acp._sessions.clear()
    services.runs.close()


# ---- point 9: cancel before the turn reaches the adapter ----
def test_cancel_before_the_turn_enters_the_adapter_cancels_it_at_the_start(tmp_path):
    services, _ = build(tmp_path)
    acp = services.runs.acp
    identifier = acp.start("office-area", "first")["id"]
    item = acp._get(identifier)
    for _ in range(200):
        if not item["active"]:
            break
        time.sleep(0.01)
    prompts = []
    original = item["session"].prompt
    item["session"].prompt = lambda text: (prompts.append(text), original(text))
    item["session"].cancel = lambda: False            # the adapter has no active turn yet
    with acp._lock:                                   # holds the turn thread at its start; the lock is re-entrant
        acp.prompt(identifier, "second")
        assert acp.cancel(identifier)["cancelled"] is True
    for _ in range(200):
        if not item["active"]:
            break
        time.sleep(0.01)
    assert prompts == []
    assert item["sink"].events[-1] == {"cursor": len(item["sink"].events), "kind": "end_turn",
                                       "tool_status": None, "data": {"stop_reason": "cancelled"}}
    services.runs.close()


def test_cancel_without_an_active_turn_reports_false(tmp_path):
    services, _ = build(tmp_path)
    acp = services.runs.acp
    identifier = acp.start("office-area", "first")["id"]
    item = acp._get(identifier)
    for _ in range(200):
        if not item["active"]:
            break
        time.sleep(0.01)
    item["session"].cancel = lambda: False
    assert acp.cancel(identifier)["cancelled"] is False
    assert item["cancel_early"] is False
    services.runs.close()


def test_an_activated_adapter_whose_executable_changed_asks_for_a_probe_and_is_not_paused(tmp_path, no_cli):
    services, _ = build(tmp_path)  # probed and activated for "synthetic-acp"
    services.connections._acp_resolver = lambda cwd: "another-acp"  # the resolved executable is another one
    activations = services.executor_activations
    row = {item["id"]: item for item in activations.status()}["codex_acp"]
    assert row["state"] == "active" and row["paused"] is None and row["probe_required"] is True
    with pytest.raises(ServiceError) as caught:
        activations.check_launch("codex_acp")
    assert caught.value.status == 409 and caught.value.details["reason"] == "acp_probe_required"
    # the saved activation was not paused as if the executable had disappeared
    assert activations._read()["codex_acp"]["paused"] is None
    # going back to the executable that was probed clears it
    services.connections._acp_resolver = lambda cwd: "synthetic-acp"
    activations.check_launch("codex_acp")
    assert {item["id"]: item for item in activations.status()}["codex_acp"]["probe_required"] is False
    services.runs.close()


def test_the_action_rows_carry_probe_required_like_status(tmp_path):
    services, _ = build(tmp_path)
    activations = services.executor_activations
    assert activations.deactivate("codex_acp")["probe_required"] is False
    services.connections._acp_resolver = lambda cwd: "another-acp"
    stale = activations._row("codex_acp")
    assert stale["probe_required"] is True
    assert stale["probe_required"] == {item["id"]: item for item in activations.status()}["codex_acp"]["probe_required"]
    assert "probe_required" not in activations._row("codex_cli")
    services.runs.close()


def post_start(services):
    headers = {"Authorization": f"Bearer {ensure_token(services.settings.gui_token_file)}"}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        return client.post("/gui/api/threads/acp", headers=headers, json={"bundle": "office-area", "text": "hi"})


def test_the_route_says_acp_unavailable_when_the_adapter_is_not_installed(tmp_path, no_cli):
    services, _ = build(tmp_path, installed=False, probed=False, activated=False)
    response = post_start(services)
    assert response.status_code == 409 and reason(response) == "acp_unavailable"
    assert no_cli == []
    services.runs.close()


def test_the_route_says_acp_probe_required_for_an_activation_without_a_probe(tmp_path, no_cli):
    services, _ = build(tmp_path)
    services.connections._acp_probe_result = None       # a restart loses the in-memory evidence
    services.connections._acp_probe_path = None
    response = post_start(services)
    assert response.status_code == 409 and reason(response) == "acp_probe_required"
    assert no_cli == []
    services.runs.close()


def test_the_route_says_acp_probe_required_for_another_resolved_executable(tmp_path, no_cli):
    services, _ = build(tmp_path)
    services.connections._acp_resolver = lambda cwd: "another-acp"
    response = post_start(services)
    assert response.status_code == 409 and reason(response) == "acp_probe_required"
    assert services.executor_activations._read()["codex_acp"]["paused"] is None
    services.runs.close()


def test_the_route_says_acp_activation_required_without_an_activation(tmp_path, no_cli):
    services, _ = build(tmp_path, activated=False)
    response = post_start(services)
    assert response.status_code == 409 and reason(response) == "acp_activation_required"
    assert no_cli == []
    services.runs.close()


def test_an_activated_adapter_that_is_uninstalled_after_a_restart_is_paused_not_active(tmp_path, no_cli):
    services, _ = build(tmp_path)
    services.connections._acp_probe_result = None       # restart: no evidence in memory
    services.connections._acp_probe_path = None
    services.connections._acp_resolver = lambda cwd: None    # and the executable is gone
    activations = services.executor_activations
    assert activations._acp_probe_required() is False
    row = {item["id"]: item for item in activations.status()}["codex_acp"]
    assert row["state"] != "active" and row["paused"] is not None and row["paused"]["reason"] == "changed"
    with pytest.raises(ServiceError) as caught:
        activations.check_launch("codex_acp")
    assert caught.value.details["reason"] == "executor_paused_changed"
    services.runs.close()
