# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EXA-T06: the person's own ACP agents, on the server. A fake ACP peer stands in for the program; nothing real runs.

The fake peer is a small Python script launched by ``sys.executable`` with two literal arguments. It records its own
command line and every line it receives, so a test can prove that the task text only travels over the standard input.
No network, no ``npx``, no ``codex-acp``, and nobody clicks «Activar»: the approval is decided with the service double.
"""
from __future__ import annotations

import json
import os
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.executors import acp, acp_probe, process
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices, ServiceError

FAKE = r'''
import json, os, sys
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "peer.jsonl")
version = sys.argv[2] if len(sys.argv) > 2 else "1.0"
def note(value):
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")
def send(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)
note({"argv": sys.argv})
while True:
    line = sys.stdin.readline()
    if not line:
        break
    req = json.loads(line)
    note({"received": req})
    method = req.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": req["id"], "result": {"protocolVersion": 1, "agentCapabilities": {},
              "agentInfo": {"name": "fake", "version": version}}})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": req["id"], "result": {"sessionId": "fake-session"}})
    elif method == "session/prompt":
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "fake-session",
              "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}}}})
        send({"jsonrpc": "2.0", "id": req["id"], "result": {"stopReason": "end_turn"}})
'''
TASK_TEXT = "SYNTHETIC-TASK-TEXT-7731"


def make_settings(tmp_path):
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False,
                    gui_token_file=tmp_path / "gui-token")


@pytest.fixture
def world(tmp_path):
    settings = make_settings(tmp_path)
    services = ApplicationServices.create(settings, dry_run=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "fake_agent.py"
    script.write_text(FAKE, encoding="utf-8")
    area = tmp_path / "area"
    area.mkdir()
    services.register_bundle("office-area", area)
    services.offices.office_of = lambda bundle: ("office", "area") if bundle == "office-area" else None
    services.offices.area_class = lambda bundle: "non_sensitive"
    token = ensure_token(settings.gui_token_file)
    app = create_app(services=services)
    with TestClient(app, base_url="http://localhost") as client:
        yield SimpleNamespace(services=services, client=client, app=app, settings=settings, area=area, tools=tools,
                              script=script, log=tools / "peer.jsonl", tmp=tmp_path,
                              headers={"Authorization": f"Bearer {token}"}, agents=services.acp_agents)
    services.runs.close()


def detail(response):
    return response.json()["detail"]


def declare(world, **changes):
    body = {"name": "My agent", "executable": sys.executable, "args": [str(world.script), "--acp"]}
    body.update(changes)
    return body


def api(world, method, path, body=None, headers=True):
    return world.client.request(method, "/gui/api" + path, headers=world.headers if headers else {},
                                **({} if body is None else {"json": body}))


def add(world, **changes):
    done = api(world, "POST", "/acp-agents", declare(world, **changes))
    assert done.status_code == 200, done.text
    return done.json()


def peer_log(world):
    if not world.log.exists():
        return []
    return [json.loads(line) for line in world.log.read_text(encoding="utf-8").splitlines()]


def launches(world):
    return [item["argv"] for item in peer_log(world) if "argv" in item]


def probe(world, agent_id):
    done = api(world, "POST", f"/acp-agents/{agent_id}/probe", {})
    assert done.status_code == 200, done.text
    return done.json()


def activate(world, agent_id):
    asked = world.services.executor_activations.request(agent_id)
    world.services.user_approvals.decide(asked["approval_id"], "approve")      # the person's decision, as a double
    return world.services.executor_activations.activate(agent_id, asked["approval_id"])


def ready(world, **changes):
    """An agent that is added, probed and activated."""
    agent = add(world, **changes)
    assert probe(world, agent["id"])["probed"] is True
    assert activate(world, agent["id"])["state"] == "active"
    return agent


def wait_turn(world, identifier):
    for _ in range(300):
        page = world.client.get(f"/gui/api/threads/acp/{identifier}", headers=world.headers).json()
        if not page["active"]:
            return page
        time.sleep(0.02)
    raise AssertionError("the turn did not end")


# --------------------------------------------------------------------------- the declaration

def test_valid_add_starts_disabled_and_is_written_atomically(world):
    agent = add(world)
    assert agent["id"] == "my-agent"
    assert (agent["state"], agent["probed"], agent["probe_required"], agent["scope"]) == (
        "disabled", False, True, "acp_conversations")
    assert agent["args"] == [str(world.script), "--acp"] and agent["created_by"] == "user:gui"
    saved = json.loads((world.settings.local_dir / "acp-agents.json").read_text(encoding="utf-8"))
    assert saved["schema"] == "maf.acp-agents/1" and set(saved["agents"]) == {"my-agent"}
    assert saved["agents"]["my-agent"]["created_by"] == "user:gui" and saved["agents"]["my-agent"]["created_at"]
    assert not list(world.settings.local_dir.glob("*.tmp"))
    assert api(world, "GET", "/acp-agents").json()["agents"][0]["state"] == "disabled"
    assert world.services.executor_activations._read() == {}          # added is not activated
    assert launches(world) == []                                       # and nothing was launched
    events = [event for event in world.services.tasks.events() if event["type"] == "acp_agent.added"]
    assert events and events[0]["actor"] == "user:gui"


@pytest.mark.parametrize("changes,reason", [
    ({"executable": "relative/agent"}, "executable_not_absolute"),
    ({"executable": "agent.exe"}, "executable_not_absolute"),
    ({"executable": ""}, "executable_not_absolute"),
    ({"executable": os.path.join(os.path.dirname(sys.executable), "py*")}, "executable_has_wildcard"),
    ({"executable": sys.executable + " && calc"}, "executable_has_shell_characters"),
    ({"executable": sys.executable + "|more"}, "executable_has_shell_characters"),
    ({"executable": os.path.join(os.path.dirname(sys.executable), "no-such-agent-xyz")}, "executable_not_found"),
    ({"args": ["{task}"]}, "argument_has_placeholder"),
    ({"args": ["--prompt=%TASK%"]}, "argument_has_placeholder"),
    ({"args": ["$TASK"]}, "argument_has_placeholder"),
    ({"args": ["<task>"]}, "argument_has_placeholder"),
    ({"args": ["`task`"]}, "argument_has_placeholder"),
    ({"args": ["a;b"]}, "argument_has_shell_syntax"),
    ({"args": ["a | b"]}, "argument_has_shell_syntax"),
    ({"args": [""]}, "argument_invalid"),
    ({"args": [" padded"]}, "argument_invalid"),
    ({"args": ["x"] * 17}, "too_many_arguments"),
    ({"name": "   "}, "name_invalid"),
    ({"name": "x" * 61}, "name_invalid"),
    ({"id": "A"}, "invalid_agent_id"),
    ({"id": "1abc"}, "invalid_agent_id"),
    ({"id": "has_underscore"}, "invalid_agent_id"),
    ({"id": "codex_acp"}, "invalid_agent_id"),
    ({"id": "codex-acp"}, "agent_id_reserved"),
    ({"id": "claude"}, "agent_id_reserved"),
    ({"id": "acp"}, "agent_id_reserved"),
    ({"id": "gemini"}, "agent_id_reserved"),
])
def test_every_rejection_has_its_reason_and_writes_nothing(world, changes, reason):
    refused = api(world, "POST", "/acp-agents", declare(world, **changes))
    assert refused.status_code == 422, refused.text
    assert detail(refused)["code"] == "invalid_request" and detail(refused)["details"]["reason"] == reason
    assert not (world.settings.local_dir / "acp-agents.json").exists()


def test_executable_in_a_writable_folder_or_a_shell_script_is_refused(world):
    inside = world.area / "agent.exe"
    inside.write_bytes(b"x")
    inside.chmod(0o755)
    local = world.settings.local_dir
    local.mkdir(parents=True, exist_ok=True)
    kept = local / "agent.exe"
    kept.write_bytes(b"x")
    kept.chmod(0o755)
    shim = world.tools / "agent.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    shim.chmod(0o755)
    for path, reason in ((inside, "executable_in_writable_folder"), (kept, "executable_in_writable_folder"),
                         (shim, "executable_is_shell_script")):
        refused = api(world, "POST", "/acp-agents", declare(world, executable=str(path)))
        assert refused.status_code == 422 and detail(refused)["details"]["reason"] == reason, path


def test_duplicate_id_and_the_limit_are_conflicts(world):
    add(world)
    again = api(world, "POST", "/acp-agents", declare(world))
    assert again.status_code == 409 and detail(again)["details"]["reason"] == "agent_exists"
    for number in range(15):
        add(world, name=f"Agent {number:02d}")
    full = api(world, "POST", "/acp-agents", declare(world, name="One too many"))
    assert full.status_code == 409 and detail(full)["details"]["reason"] == "too_many_agents"


def test_only_the_person_adds_edits_probes_or_removes(world):
    add(world)
    for actor in ("agent:claude", "system:x", "user:cli", ""):
        for call in (lambda a=actor: world.agents.add("Other", sys.executable, [], actor=a),
                     lambda a=actor: world.agents.edit("my-agent", name="Renamed", actor=a),
                     lambda a=actor: world.agents.remove("my-agent", actor=a),
                     lambda a=actor: world.services.connections.probe_acp_agent("my-agent", actor=a)):
            with pytest.raises(ServiceError) as caught:
                call()
            assert caught.value.details["reason"] == "person_required"
    assert [row["id"] for row in world.agents.list()["agents"]] == ["my-agent"]
    assert world.agents.list()["agents"][0]["name"] == "My agent"
    # the browser token is the only way in: no token, no route; no /api/v1 route, no CLI command, no MCP tool
    assert api(world, "GET", "/acp-agents", headers=False).status_code in (401, 403)
    assert api(world, "POST", "/acp-agents", declare(world, name="Nope"), headers=False).status_code in (401, 403)
    paths = [getattr(route, "path", "") for route in world.app.routes]
    assert not [path for path in paths if path.startswith("/api/v1") and "acp-agent" in path]
    from multiagent import cli

    names: list[str] = []

    def walk(parser):
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                for name, sub in choices.items():
                    names.append(name)
                    walk(sub)

    walk(cli.build_parser())
    assert names and not [name for name in names if "acp-agent" in name or "acp_agent" in name]
    mcp = pytest.importorskip("mcp", reason="the optional extra 'mcp' is not installed")      # noqa: F841
    import asyncio
    from mcp import Client
    from multiagent.interop import mcp_server

    async def tools(server):
        async with Client(server) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    for options in ({}, {"allow_create_runs": True}):
        listed = asyncio.run(tools(mcp_server.build_server(world.services, **options)))
        assert not [name for name in listed if "acp" in name]


# --------------------------------------------------------------------------- probe, activation, conversation

def test_probe_runs_initialize_only_with_the_fixed_arguments(world):
    agent = add(world)
    assert launches(world) == []                       # adding never launches
    row = probe(world, agent["id"])
    assert (row["probed"], row["probe_required"], row["state"]) == (True, False, "disabled")
    assert row["probe"]["status"] == "compatible" and row["probe"]["agent_info"]["version"] == "1.0"
    assert launches(world) == [[str(world.script), "--acp"]]
    received = [item["received"]["method"] for item in peer_log(world) if "received" in item]
    assert received == ["initialize"]                  # no session, no request
    assert api(world, "POST", "/acp-agents/nobody/probe", {}).status_code == 404


def test_activation_needs_a_probe_and_the_note_names_the_agent(world):
    agent = add(world)
    with pytest.raises(ServiceError) as caught:
        world.services.executor_activations.request(agent["id"])
    assert caught.value.details["reason"] == "acp_probe_required"
    probe(world, agent["id"])
    asked = world.client.post(f"/gui/api/executors/{agent['id']}/activation", headers=world.headers, json={})
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert "My agent" in body["note"] and "protocol, not isolation" in body["note"] and "GUI only" in body["note"]
    assert body["fingerprint"]["args"] == [str(world.script), "--acp"] and body["fingerprint"]["version"] == "1.0"
    # only the person's approved, unspent approval turns it on
    with pytest.raises(ServiceError) as refused:
        world.services.executor_activations.activate(agent["id"], body["approval_id"])
    assert refused.value.details["reason"] == "person_required"
    assert api(world, "GET", "/acp-agents").json()["agents"][0]["state"] == "disabled"
    assert world.client.post("/gui/api/executors/nobody/activation", headers=world.headers, json={}).status_code == 404


def test_conversation_needs_a_probe_and_an_activation_in_today_order(world):
    agent = add(world)
    with pytest.raises(ServiceError) as caught:
        world.services.runs.acp.start("office-area", "hello", agent["id"])
    assert (caught.value.details["reason"], launches(world)) == ("acp_unavailable", [])        # no probe
    probe(world, agent["id"])
    with pytest.raises(ServiceError) as caught:
        world.services.runs.acp.start("office-area", "hello", agent["id"])
    assert (caught.value.details["reason"], launches(world)) == ("acp_activation_required", launches(world))
    assert len(launches(world)) == 1                                                            # only the probe ran
    with pytest.raises(ServiceError) as caught:
        world.services.runs.acp.start("office-area", "hello", "nobody")
    assert caught.value.status == 404 and caught.value.details["reason"] == "acp_agent_not_found"
    refused = api(world, "POST", "/threads/acp", {"bundle": "office-area", "text": "hello", "agent": agent["id"]})
    assert refused.status_code == 409 and detail(refused)["details"]["reason"] == "acp_activation_required"


def test_task_text_never_reaches_the_command_line(world):
    agent = ready(world)
    started = api(world, "POST", "/threads/acp", {"bundle": "office-area", "text": TASK_TEXT, "agent": agent["id"]})
    assert started.status_code == 200, started.text
    assert started.json()["agent"] == {"id": "my-agent", "name": "My agent"}
    page = wait_turn(world, started.json()["id"])
    assert [event["kind"] for event in page["events"]] == ["prompt", "message", "end_turn"]
    argvs = launches(world)
    assert argvs == [[str(world.script), "--acp"]] * 2           # the probe and the conversation, the same literal arguments
    assert not [argument for argv in argvs for argument in argv if TASK_TEXT in argument]
    received = [item["received"] for item in peer_log(world) if "received" in item]
    prompt = next(item for item in received if item["method"] == "session/prompt")
    assert TASK_TEXT in json.dumps(prompt)                        # it travels over the standard input
    capabilities = next(item for item in received if item["method"] == "initialize" and
                        "fs" in item["params"]["clientCapabilities"])["params"]["clientCapabilities"]
    assert "terminal" not in capabilities and capabilities["fs"] == {"readTextFile": False, "writeTextFile": False}
    sink = world.services.runs.acp._get(started.json()["id"])["sink"]
    from multiagent.executors.transport import PermissionAsk
    assert sink.on_permission(PermissionAsk("read", None, ("allow_once",))) == "reject_once"    # no consent: all denied


def test_the_launch_uses_the_clean_environment_and_the_declared_program(world, monkeypatch):
    agent = ready(world)
    seen: dict = {}
    real = process.start

    def spy(argv, **kwargs):
        seen.update(argv=list(argv), env=dict(kwargs.get("env_extra") or {}))
        return real(argv, **kwargs)

    monkeypatch.setattr(acp.process, "start", spy)
    os.environ["SYNTHETIC_SECRET_NAME"] = "value"
    try:
        started = world.services.runs.acp.start("office-area", TASK_TEXT, agent["id"])
    finally:
        del os.environ["SYNTHETIC_SECRET_NAME"]
    wait_turn(world, started["id"])
    assert seen["argv"] == [os.path.abspath(sys.executable), str(world.script), "--acp"]
    assert seen["env"].get("SYNTHETIC_SECRET_NAME") == ""         # inherited names are cleared, as for Codex ACP
    assert not [item for item in seen["argv"] if TASK_TEXT in item]


# --------------------------------------------------------------------------- editing and removing

def test_editing_an_argument_pauses_the_activation_and_clears_the_probe(world):
    agent = ready(world)
    live = world.services.runs.acp.start("office-area", "first", agent["id"])
    wait_turn(world, live["id"])
    renamed = api(world, "PUT", f"/acp-agents/{agent['id']}", {"name": "Renamed"}).json()
    assert (renamed["name"], renamed["state"], renamed["probed"]) == ("Renamed", "active", True)   # a name changes nothing
    edited = api(world, "PUT", f"/acp-agents/{agent['id']}", {"args": [str(world.script), "--acp", "2.0"]})
    assert edited.status_code == 200, edited.text
    row = edited.json()
    assert (row["state"], row["probed"], row["probe_required"]) == ("paused", False, True)
    assert row["changes"] == [{"field": "args", "from": [str(world.script), "--acp"],
                               "to": [str(world.script), "--acp", "2.0"]}]
    assert world.services.runs.acp._sessions == {}                # its live conversation was closed
    with pytest.raises(ServiceError) as caught:
        world.services.runs.acp.start("office-area", "again", agent["id"])
    assert caught.value.details["reason"] == "acp_probe_required"
    probe(world, agent["id"])
    with pytest.raises(ServiceError) as caught:
        world.services.runs.acp.start("office-area", "again", agent["id"])
    assert caught.value.details["reason"] == "executor_paused_changed"
    assert caught.value.details["changes"][0]["field"] == "args"
    assert activate(world, agent["id"])["state"] == "active"      # the person reviewed it again
    again = world.services.runs.acp.start("office-area", "again", agent["id"])
    wait_turn(world, again["id"])
    assert launches(world)[-1] == [str(world.script), "--acp", "2.0"]


def test_editing_the_executable_also_starts_over(world, tmp_path):
    agent = ready(world)
    other = tmp_path / "other" / os.path.basename(sys.executable)
    other.parent.mkdir()
    other.write_bytes(open(sys.executable, "rb").read())
    other.chmod(0o755)
    row = api(world, "PUT", f"/acp-agents/{agent['id']}", {"executable": str(other)}).json()
    assert (row["state"], row["probed"]) == ("paused", False) and row["changes"][0]["field"] == "path"
    assert api(world, "PUT", "/acp-agents/nobody", {"name": "x"}).status_code == 404
    assert api(world, "PUT", f"/acp-agents/{agent['id']}", {"args": ["{task}"]}).status_code == 422


def test_what_the_fingerprint_compares_with_and_without_a_probe(world):
    agent = ready(world)
    activations = world.services.executor_activations
    fingerprint = activations.fingerprint(agent["id"])
    assert set(fingerprint) == {"path", "major", "account", "args", "version", "size", "mtime_ns", "sha256"}
    assert (fingerprint["major"], fingerprint["version"], fingerprint["args"]) == ("1", "1.0", [str(world.script), "--acp"])
    # a restart forgets the probe: the person probes again, and the activation is not paused by it
    world.services.connections._agent_probes.clear()
    assert [row["state"] for row in world.agents.list()["agents"]] == ["active"]
    activations.status()
    assert activations._read()[agent["id"]]["paused"] is None
    with pytest.raises(ServiceError) as caught:
        activations.check_launch(agent["id"])
    assert caught.value.details["reason"] == "acp_probe_required"
    # the declared executable and arguments are compared without a process; the announced version needs a probe
    world.services.connections._agent_probe = lambda executable, *, cwd, args=(): {
        "status": "compatible", "protocol_version": 1, "agent_info": {"name": "fake", "version": "2.0"}}
    probe(world, agent["id"])
    with pytest.raises(ServiceError) as caught:
        activations.check_launch(agent["id"])
    assert caught.value.details["reason"] == "executor_paused_changed"
    assert [item["field"] for item in caught.value.details["changes"]] == ["version"]
    assert world.agents.list()["agents"][0]["state"] == "paused"


def test_a_missing_executable_pauses_through_the_fingerprint(world, tmp_path):
    copy = tmp_path / "bin" / os.path.basename(sys.executable)
    copy.parent.mkdir()
    copy.write_bytes(open(sys.executable, "rb").read())
    copy.chmod(0o755)
    world.services.connections._agent_probe = lambda executable, *, cwd, args=(): {
        "status": "compatible", "protocol_version": 1, "agent_info": {"name": "fake", "version": "1.0"}}
    agent = ready(world, executable=str(copy))      # a copy that would not run alone: the probe is a double
    copy.unlink()
    world.services.executor_activations.status()      # a status refresh exposes the drift
    assert world.agents.list()["agents"][0]["state"] == "paused"
    with pytest.raises(ServiceError) as caught:
        world.services.runs.acp.start("office-area", "x", agent["id"])
    assert caught.value.details["reason"] == "acp_unavailable"      # the declaration is checked again before any launch


def test_removing_closes_its_conversations_deletes_its_activation_and_keeps_the_history(world):
    agent = ready(world)
    live = world.services.runs.acp.start("office-area", "first", agent["id"])
    wait_turn(world, live["id"])
    assert world.services.runs.acp._get(live["id"])["agent"] == "my-agent"
    deleted = []
    original = world.services.runs.acp.history.delete
    world.services.runs.acp.history.delete = lambda identifier: deleted.append(identifier) or original(identifier)
    gone = api(world, "POST", f"/acp-agents/{agent['id']}/remove", {})
    assert gone.status_code == 200 and gone.json() == {"id": "my-agent", "removed": True}
    assert world.services.runs.acp._sessions == {} and deleted == []     # closed, and no history was deleted
    assert world.services.executor_activations._read() == {}
    assert raw_activations(world) == {}
    assert api(world, "GET", "/acp-agents").json()["agents"] == []
    assert world.client.get(f"/gui/api/threads/acp/{live['id']}", headers=world.headers).status_code in (200, 404)
    assert api(world, "POST", f"/acp-agents/{agent['id']}/remove", {}).status_code == 404
    again = add(world)                                                    # the same id comes back disabled
    assert again["state"] == "disabled"


def raw_activations(world):
    path = world.settings.local_dir / "executors-activados.json"
    return json.loads(path.read_text(encoding="utf-8"))["executors"] if path.exists() else {}


def test_deactivating_closes_only_that_agents_conversations(world):
    first = ready(world)
    second = ready(world, name="Second agent")
    one = world.services.runs.acp.start("office-area", "one", first["id"])
    two = world.services.runs.acp.start("office-area", "two", second["id"])
    wait_turn(world, one["id"])
    wait_turn(world, two["id"])
    done = world.client.post(f"/gui/api/executors/{first['id']}/deactivate", headers=world.headers, json={})
    assert done.status_code == 200 and done.json()["state"] == "ready"
    assert set(world.services.runs.acp._sessions) == {two["id"]}


# --------------------------------------------------------------------------- Codex ACP and the workflows do not change

def test_codex_acp_launches_without_arguments_as_before(monkeypatch, tmp_path):
    seen = []

    class Stop(Exception):
        pass

    def spy(argv, **kwargs):
        seen.append(list(argv))
        raise process.ProcessLaunchError("launch_failed", "double")

    monkeypatch.setattr(acp.process, "start", spy)
    transport = acp.AcpTransport("codex-acp")
    assert transport.args == ()
    from multiagent.executors.contract import ExecutorLaunchRequest
    with pytest.raises(acp.AcpTransportError):
        transport.open_session(ExecutorLaunchRequest("T1", {}, tmp_path), SimpleNamespace(acp_consent=None))
    assert seen == [["codex-acp"]]
    runs = []
    outcome = SimpleNamespace(cancelled=False, stdout_truncated=False, stderr_truncated=False, timed_out=False,
                              stdout_tail="")
    acp_probe.probe_codex_acp("codex-acp", cwd=tmp_path, run=lambda argv, **kw: runs.append(list(argv)) or outcome)
    assert runs == [["codex-acp"]]


def test_own_agents_do_not_enter_workflows_or_the_codex_row(world):
    agent = ready(world)
    activations = world.services.executor_activations
    assert agent["id"] not in activations.active_ids()          # the old meaning: the house and codex_acp only
    assert agent["id"] in activations.active_agent_ids()
    config = {"executors": {"codex_cli": {"enabled": False}}}
    assert activations.apply_to_config(config) == {"executors": {"codex_cli": {"enabled": False}}}
    assert agent["id"] not in [item["id"] for item in activations.status()]
    assert agent["id"] not in [item["executor_id"] for item in world.services.executors.status()["executors"]]
    assert world.services.connections.check("codex_acp")["activated"] is False
    assert agent["id"] not in [row["id"] for row in world.services.connections.list()["connections"]]


# =========================================================================== round 2

def make_file(folder, name, content=b"x"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    path.chmod(0o755)
    return path


def reason_of(response):
    return detail(response)["details"]["reason"]


@pytest.mark.parametrize("setting", ["data_dir", "file_tools_root", "executor_workdir_root", "runs_dir", "assets_dir"])
def test_every_configurable_writable_folder_is_forbidden_to_the_executable(world, setting):
    folder = world.tmp / ("writable-" + setting)
    program = make_file(folder, "agent.exe")
    setattr(world.settings, setting, folder)
    refused = api(world, "POST", "/acp-agents", declare(world, executable=str(program)))
    assert refused.status_code == 422 and reason_of(refused) == "executable_in_writable_folder", setting
    # also as a script argument of a program interpreter
    refused = api(world, "POST", "/acp-agents", declare(world, args=[str(program), "--acp"]))
    assert refused.status_code == 422 and reason_of(refused) == "argument_in_writable_folder", setting


def test_cwd_and_the_framework_root_are_forbidden(world, monkeypatch):
    work = world.tmp / "cwd-folder"
    inside = make_file(work, "agent.exe")
    monkeypatch.chdir(work)
    monkeypatch.setattr(os, "access", lambda *args, **kwargs: True)     # the framework file below is not executable on every system
    framework = os.path.dirname(os.path.dirname(os.path.abspath(sys.modules["multiagent"].__file__)))
    for program in (inside, os.path.join(framework, "pyproject.toml")):
        refused = api(world, "POST", "/acp-agents", declare(world, executable=str(program)))
        assert refused.status_code == 422 and reason_of(refused) == "executable_in_writable_folder", program


@pytest.mark.parametrize("name", ["cmd.exe", "CMD.EXE", "PowerShell.exe", "pwsh", "wscript.exe", "cscript", "mshta.exe",
                                  "bash", "Bash.EXE", "sh", "zsh", "ksh", "fish", "csh", "tcsh", "dash", "wsl.exe",
                                  "busybox"])
def test_command_interpreters_are_refused_by_base_name(world, name):
    program = make_file(world.tools, name)
    refused = api(world, "POST", "/acp-agents", declare(world, executable=str(program), args=["-c", "x"]))
    assert refused.status_code == 422 and reason_of(refused) == "executable_is_shell", name


def test_an_interpreter_behind_a_link_is_refused_by_its_resolved_name(world):
    target = make_file(world.tools, "bash")
    link = world.tools / "harmless-agent"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    refused = api(world, "POST", "/acp-agents", declare(world, executable=str(link), args=[]))
    assert refused.status_code == 422 and reason_of(refused) == "executable_is_shell"


def test_program_interpreters_stay_allowed_with_a_script_outside_the_forbidden_folders(world):
    node = make_file(world.tools, "node.exe")
    script = make_file(world.tools / "agent-pkg", "index.js")
    added = api(world, "POST", "/acp-agents", declare(world, executable=str(node), args=[str(script), "--acp"]))
    assert added.status_code == 200 and added.json()["state"] == "disabled"
    inside = make_file(world.area, "index.js")                  # the script sits in an area: the agent could rewrite it
    for args, reason in (([str(inside)], "argument_in_writable_folder"),
                         ([f"--entry={inside}"], "argument_in_writable_folder"),
                         (["dist/index.js"], "argument_relative_path"),
                         (["--config=sub/dir.json"], "argument_relative_path"),
                         (["index.js"], "argument_relative_path")):
        refused = api(world, "POST", "/acp-agents", declare(world, name="Node two", executable=str(node), args=args))
        assert refused.status_code == 422 and reason_of(refused) == reason, args
    ok = api(world, "POST", "/acp-agents", declare(world, name="Flags", executable=str(node),
                                                   args=["--acp", "--flag=value", "--url=https://example.test/x"]))
    assert ok.status_code == 200


@pytest.mark.parametrize("name", ["agent.bat", "agent.ps1", "agent.vbs", "agent.sh", "agent.CMD"])
def test_shell_scripts_are_refused_even_when_they_exist(world, name):
    program = make_file(world.tools, name)
    refused = api(world, "POST", "/acp-agents", declare(world, executable=str(program)))
    assert refused.status_code == 422 and reason_of(refused) == "executable_is_shell_script"


@pytest.mark.parametrize("identifier", ["my agent", "my@agent", "../x", "a/b", "ünï", "x" * 33, "a", "-lead", "tab\tid"])
def test_ids_with_spaces_or_odd_characters_are_refused(world, identifier):
    refused = api(world, "POST", "/acp-agents", declare(world, id=identifier))
    assert refused.status_code == 422 and reason_of(refused) == "invalid_agent_id", identifier


def test_no_token_means_no_put_remove_or_probe(world):
    add(world)
    for method, path, body in (("PUT", "/acp-agents/my-agent", {"name": "x"}), ("POST", "/acp-agents/my-agent/remove", {}),
                               ("POST", "/acp-agents/my-agent/probe", {})):
        assert api(world, method, path, body, headers=False).status_code in (401, 403), path
    assert [row["name"] for row in world.agents.list()["agents"]] == ["My agent"]


# --------------------------------------------------------------------------- a file edited by hand

def write_file(world, agents, raw=None):
    world.settings.local_dir.mkdir(parents=True, exist_ok=True)
    path = world.settings.local_dir / "acp-agents.json"
    path.write_text(raw if raw is not None else json.dumps({"schema": "maf.acp-agents/1", "agents": agents}),
                    encoding="utf-8")
    return path


def entry(world, **changes):
    value = {"name": "By hand", "executable": sys.executable, "args": [str(world.script)],
             "created_at": "2026-01-01T00:00:00+00:00", "created_by": "user:gui"}
    value.update(changes)
    return value


def test_a_broken_file_reads_as_nothing_and_is_not_overwritten_until_the_next_add(world):
    path = write_file(world, None, raw="{ this is not json")
    assert api(world, "GET", "/acp-agents").json() == {"agents": []}
    assert world.services.executor_activations._agent_ids() == []
    assert path.read_text(encoding="utf-8") == "{ this is not json"
    add(world)
    assert set(json.loads(path.read_text(encoding="utf-8"))["agents"]) == {"my-agent"}


def test_a_reserved_or_malformed_id_in_the_file_is_not_listed_anywhere(world):
    write_file(world, {"claude": entry(world), "codex-acp": entry(world), "Bad Id": entry(world), "fine-one": entry(world)})
    assert [row["id"] for row in api(world, "GET", "/acp-agents").json()["agents"]] == ["fine-one"]
    assert world.agents.ids() == ["fine-one"] and world.services.executor_activations._agent_ids() == ["fine-one"]
    add(world)                                                      # a later write keeps what the person had in the file
    kept = json.loads((world.settings.local_dir / "acp-agents.json").read_text(encoding="utf-8"))["agents"]
    assert set(kept) >= {"claude", "fine-one", "my-agent"}


@pytest.mark.parametrize("changes,reason", [
    ({"executable": "relative/agent"}, "executable_not_absolute"),
    ({"executable": "C:/tools/agent.cmd"}, "executable_is_shell_script"),
    ({"executable": "FORBIDDEN"}, "executable_in_writable_folder"),
    ({"args": ["{task}"]}, "argument_has_placeholder"),
    ({"args": ["a;b"]}, "argument_has_shell_syntax"),
    ({"name": ""}, "name_invalid"),
])
def test_an_entry_the_rules_refuse_is_listed_invalid_and_cannot_be_used(world, changes, reason):
    if changes.get("executable") == "FORBIDDEN":
        changes = {"executable": str(make_file(world.area, "agent.exe"))}
    write_file(world, {"hand": entry(world, **changes)})
    row = api(world, "GET", "/acp-agents").json()["agents"][0]
    assert (row["id"], row["valid"], row["reason"], row["installed"]) == ("hand", False, reason, False)
    probed = api(world, "POST", "/acp-agents/hand/probe", {})
    assert probed.status_code == 409 and reason_of(probed) == reason
    asked = world.client.post("/gui/api/executors/hand/activation", headers=world.headers, json={})
    assert asked.status_code == 409 and reason_of(asked) == reason
    with pytest.raises(ServiceError) as caught:
        world.services.runs.acp.start("office-area", "hello", "hand")
    assert caught.value.details["reason"] == reason and launches(world) == []
    renamed = api(world, "PUT", "/acp-agents/hand", {"name": "Renamed"})      # the whole entry is validated, not one field
    if reason == "name_invalid":
        assert renamed.status_code == 200          # the edit fixes the field that was wrong
    else:
        assert renamed.status_code in (409, 422) and reason_of(renamed) == reason
    assert world.services.executor_activations.active_agent_ids() == []
    removed = api(world, "POST", "/acp-agents/hand/remove", {})                # the person can always remove it
    assert removed.status_code == 200


def test_entries_past_the_limit_are_listed_too_many_agents(world):
    write_file(world, {f"agent-{number:02d}": entry(world) for number in range(18)})
    rows = api(world, "GET", "/acp-agents").json()["agents"]
    assert [row["id"] for row in rows if row["valid"]] == [f"agent-{number:02d}" for number in range(16)]
    assert [(row["id"], row["reason"]) for row in rows if not row["valid"]] == [
        ("agent-16", "too_many_agents"), ("agent-17", "too_many_agents")]
    probed = api(world, "POST", "/acp-agents/agent-17/probe", {})
    assert probed.status_code == 409 and reason_of(probed) == "too_many_agents"


# --------------------------------------------------------------------------- the fingerprint covers the file

def copy_program(world, name="agent-copy.exe"):
    copy = make_file(world.tmp / "bin", name, open(sys.executable, "rb").read())
    world.services.connections._agent_probe = lambda executable, *, cwd, args=(): {
        "status": "compatible", "protocol_version": 1, "agent_info": {"name": "fake", "version": "1.0"}}
    return copy


def test_a_changed_modification_date_pauses_even_without_a_probe(world):
    copy = copy_program(world)
    agent = ready(world, name="Copy", executable=str(copy))
    world.services.connections._agent_probes.clear()                 # no probe in memory
    stat = copy.stat()
    os.utime(copy, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    world.services.executor_activations.status()
    row = world.agents.list()["agents"][0]
    assert row["state"] == "paused" and [item["field"] for item in row["changes"]] == ["file"]
    assert row["changes"][0]["from"]["mtime_ns"] == stat.st_mtime_ns and row["changes"][0]["to"]["size"] == stat.st_size
    with pytest.raises(ServiceError) as caught:
        world.services.runs.acp.start("office-area", "x", agent["id"])
    assert caught.value.details["reason"] in {"acp_probe_required", "executor_paused_changed"}


def test_a_changed_size_pauses_with_the_same_date(world):
    copy = copy_program(world)
    agent = ready(world, name="Copy", executable=str(copy))
    stat = copy.stat()
    with open(copy, "ab") as stream:
        stream.write(b"more")
    os.utime(copy, ns=(stat.st_atime_ns, stat.st_mtime_ns))          # only the size differs
    with pytest.raises(ServiceError) as caught:
        world.services.executor_activations.check_launch(agent["id"])
    assert caught.value.details["reason"] == "executor_paused_changed"
    assert [item["field"] for item in caught.value.details["changes"]] == ["file"]


def test_codex_acp_fingerprint_has_no_file_fields(world):
    world.services.connections._acp_resolver = lambda cwd: "synthetic-acp"
    world.services.connections._acp_probe_result = {"status": "compatible", "protocol_version": 1}
    world.services.connections._acp_probe_path = "synthetic-acp"
    fingerprint = world.services.executor_activations.fingerprint("codex_acp")
    assert set(fingerprint) == {"path", "major", "account", "sha256"}


def test_a_second_edit_of_a_paused_agent_updates_the_reported_change(world):
    agent = ready(world)
    first = [str(world.script), "--acp", "one"]
    second = [str(world.script), "--acp", "two"]
    api(world, "PUT", f"/acp-agents/{agent['id']}", {"args": first})
    row = api(world, "PUT", f"/acp-agents/{agent['id']}", {"args": second}).json()
    assert row["state"] == "paused" and row["paused"]["reason"] == "changed"
    assert row["changes"] == [{"field": "args", "from": first, "to": second}]
    events = [event for event in world.services.tasks.events() if event["type"] == "executor.deactivated"]
    assert len(events) == 2


# --------------------------------------------------------------------------- closing while a conversation opens

def test_close_agent_also_closes_a_conversation_that_is_still_opening(world):
    from threading import Event, Thread
    agent = ready(world)
    entered, release, closed = Event(), Event(), []

    class Slow:
        def __init__(self, executable, args=()):
            pass

        def open_session(self, request, sink):
            entered.set()
            release.wait(5)
            return SimpleNamespace(prompt=lambda text: None, cancel=lambda: True, close=lambda: closed.append(True))

    world.services.runs.acp.transport_factory = Slow
    outcome = {}

    def run():
        try:
            world.services.runs.acp.start("office-area", "hello", agent["id"])
        except ServiceError as exc:
            outcome["reason"] = exc.details["reason"]

    thread = Thread(target=run)
    thread.start()
    assert entered.wait(5)
    world.services.runs.acp.close_agent(agent["id"])
    release.set()
    thread.join(5)
    assert outcome == {"reason": "acp_unavailable"} and closed == [True]
    assert world.services.runs.acp._sessions == {} and world.services.runs.acp._opening == 0


def test_active_ids_keeps_its_old_meaning(world):
    agent = ready(world)
    activations = world.services.executor_activations
    assert activations.active_ids() == [] and activations.active_agent_ids() == [agent["id"]]


# --------------------------------------------------------------------------- the area consent is the same for every ACP agent

@pytest.mark.parametrize("allow_write", [False, True])
def test_area_consent_applies_to_an_own_agent_as_to_codex(world, allow_write):
    from multiagent.executors.transport import PermissionAsk
    agent = ready(world)
    saved = world.client.put("/gui/api/connections/codex_acp/consent", headers=world.headers,
                             json={"bundle": "office-area", "allow_write": allow_write})
    assert saved.status_code == 200, saved.text
    started = api(world, "POST", "/threads/acp", {"bundle": "office-area", "text": "hello", "agent": agent["id"]}).json()
    assert started["consent_configured"] is True
    wait_turn(world, started["id"])
    sink = world.services.runs.acp._get(started["id"])["sink"]
    inside, outside = str(world.area / "file.txt"), str(world.tmp / "outside.txt")

    def ask(kind, where):
        return sink.on_permission(PermissionAsk(kind, where, ("allow_once",), (where,) if where else ()))

    assert ask("read", inside) == "allow_once" and ask("read", outside) == "reject_once"
    assert ask("write", inside) == ("allow_once" if allow_write else "reject_once")
    assert ask("write", outside) == "reject_once" and ask("execute", None) == "reject_once"
    initialize = [item["received"] for item in peer_log(world)
                  if item.get("received", {}).get("method") == "initialize"][-1]
    assert initialize["params"]["clientCapabilities"]["fs"] == {"readTextFile": True, "writeTextFile": allow_write}
    assert "terminal" not in initialize["params"]["clientCapabilities"]
