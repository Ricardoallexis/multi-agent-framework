# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EJE-T01: «Activar» on the server. A fake detector stands in for the CLIs; a double of the one-use code decides.

No test starts, detects, or turns on a real CLI, and nobody clicks «Activar»: the approval is decided through the
challenge and decision routes with the test's own session, or straight through ``decide`` for the service tests.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.security import APPROVAL_COOKIE, APPROVAL_COOKIE_PATH
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services import executor_activations as ea
from test_executor_services import make_world, never_a_real_cli, refused  # noqa: F401  (the fixture is autouse)

AGY_NOTICE = "agy-third-party-notice/1"


class FakeDetector:
    """The shape of ``setup.detect.detect`` for the four apps, from a mutable table. It never runs anything."""

    def __init__(self):
        self.calls = 0
        self.apps = {"codex": self.fact("codex", "1.4.2", account=None),
                     "claude": self.fact("claude", "2.1.0", account="person@example.test"),
                     "copilot": self.fact("copilot", "0.0.5", account=None, session="unknown"),
                     "agy": self.fact("agy", "1.0.3", account=None, session="unknown")}

    @staticmethod
    def fact(app, version, *, account=None, session="signed_in", path=None):
        fact = {"app": app, "detection": "detected", "version": version,
                "path": path or f"C:\\fake\\bin\\{app}.exe", "session": {"state": session}}
        if account is not None:
            fact["account"] = account
        return fact

    def set(self, app, **changes):
        fact = dict(self.apps[app])
        for key, value in changes.items():
            if key == "session":
                fact["session"] = {"state": value}
            elif value is None:
                fact.pop(key, None)
            else:
                fact[key] = value
        self.apps[app] = fact

    def __call__(self, **kwargs):
        self.calls += 1
        return {"contexts": {"packaged_process": {"state": "measured", "apps": list(self.apps.values())}}}


def make_settings(tmp_path):
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False,
                    gui_token_file=tmp_path / "gui-token")


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    services = ApplicationServices.create(settings, dry_run=True)
    detector = FakeDetector()
    services.executor_activations.detector = detector
    token = ensure_token(settings.gui_token_file)
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    with TestClient(app, base_url="http://localhost") as client:
        yield SimpleNamespace(services=services, detector=detector, client=client, app=app, settings=settings,
                              headers={"Authorization": f"Bearer {token}"}, activations=services.executor_activations)
    services.runs.close()


def post(env, path, body=None):
    return env.client.post("/gui/api" + path, headers=env.headers, json=body if body is not None else {})


def decide_with_code(env, approval_id):
    """The double of the person: the browser session and the 120 s code of the approvals, as the GUI would use them."""
    if env.client.cookies.get(APPROVAL_COOKIE) is None:
        assert post(env, "/approvals/session", {"code": "synthetic-start-code"}).status_code == 200
    code = post(env, f"/approvals/{approval_id}/challenge").json()["code"]
    done = post(env, f"/approvals/{approval_id}/decision", {"decision": "approve", "code": code})
    assert done.status_code == 200, done.text
    return done.json()


def approved(env, executor_id):
    """A request decided by the double, straight through the service (no HTTP)."""
    asked = env.activations.request(executor_id)
    env.services.user_approvals.decide(asked["approval_id"], "approve")
    return asked


def activate(env, executor_id):
    return env.activations.activate(executor_id, approved(env, executor_id)["approval_id"])


def team_events(services, *types):
    return [event for event in services.tasks.events() if event["type"] in types]


# --------------------------------------------------------------------------- the one-use code

def test_activate_needs_one_time_code(env):
    asked = post(env, "/executors/claude_cli/activation")
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["note_id"] and body["text"] and "subscription" in body["text"] and body["sha256"]
    approval_id = body["approval_id"]
    for attempt in (approval_id, "no-such-approval", "x"):          # not decided, or not an approval at all
        refused_call = post(env, "/executors/claude_cli/activate", {"approval_id": attempt})
        assert refused_call.status_code == 409
        detail = refused_call.json()["detail"] if "detail" in refused_call.json() else refused_call.json()
        assert json.dumps(detail).count("person_required") >= 1
    assert env.activations.status()[1]["state"] == "ready"
    decide_with_code(env, approval_id)
    other = post(env, "/executors/codex_cli/activate", {"approval_id": approval_id})   # approved for another executor
    assert other.status_code == 409 and "person_required" in other.text
    done = post(env, "/executors/claude_cli/activate", {"approval_id": approval_id})
    assert done.status_code == 200, done.text
    assert done.json()["state"] == "active"
    again = post(env, "/executors/claude_cli/activate", {"approval_id": approval_id})     # one use
    assert again.status_code == 409 and "person_required" in again.text
    saved = json.loads((env.settings.local_dir / ea.ACTIVATIONS_FILE).read_text(encoding="utf-8"))
    assert saved["schema"] == ea.ACTIVATIONS_SCHEMA
    entry = saved["executors"]["claude_cli"]
    assert entry["by"] == "user:gui" and entry["channel"] == "gui" and entry["paused"] is None
    assert entry["fingerprint"]["major"] == "2" and entry["fingerprint"]["account"] == "person@example.test"
    events = team_events(env.services, "executor.activated")
    assert [event["payload"] for event in events] == [
        {"executor_id": "claude_cli", "major": "2", "account": "person@example.test", "channel": "gui",
         "reason": "by_person"}]


def test_activate_with_unknown_executor_is_404_and_pending_is_hidden_from_unknown(env):
    assert post(env, "/executors/nope/activation").status_code == 404
    assert post(env, "/executors/nope/activate", {"approval_id": "x"}).status_code == 404
    assert post(env, "/executors/nope/deactivate").status_code == 404


def test_request_refuses_without_install_or_session(env):
    env.detector.apps["codex"] = {"app": "codex", "detection": "not_detected", "version": None}
    env.detector.set("claude", session="signed_out")
    rows = {row["id"]: row["state"] for row in env.activations.status()}
    assert rows["codex_cli"] == "not_installed" and rows["claude_cli"] == "no_session"
    assert rows["copilot_cli"] == "ready" and rows["antigravity_cli"] == "ready"
    refused(lambda: env.activations.request("codex_cli"), reason="not_installed")
    refused(lambda: env.activations.request("claude_cli"), reason="no_session")
    assert post(env, "/executors/codex_cli/activation").status_code == 409


def test_no_api_v1_cli_or_mcp_route(env):
    paths = [getattr(route, "path", "") for route in env.app.routes]
    assert any(path.startswith("/gui/api/executors/") and "activate" in path for path in paths)   # the GUI has them
    assert not [path for path in paths if path.startswith("/api/v1") and "activat" in path.lower()]
    assert not [path for path in paths if not path.startswith("/gui/") and "executors/stop-all" in path]
    names: list[str] = []

    def walk(parser):
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                for name, sub in choices.items():
                    names.append(name)
                    walk(sub)

    walk(cli.build_parser())
    assert names and not [name for name in names
                          if ("activat" in name and name != "brand-activate") or "stop-all" in name]   # brand-activate: brand docs
    mcp = pytest.importorskip("mcp", reason="the optional extra 'mcp' is not installed")      # noqa: F841
    import asyncio
    from mcp import Client
    from multiagent.interop import mcp_server

    async def tools(server):
        async with Client(server) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    for options in ({}, {"allow_create_runs": True}):
        listed = asyncio.run(tools(mcp_server.build_server(env.services, **options)))
        assert not [name for name in listed if "activat" in name or "executor" in name]


def test_activation_hidden_from_lists(env):
    asked = post(env, "/executors/claude_cli/activation").json()
    assert env.services.user_approvals.status(asked["approval_id"])["state"] == "pending"     # it exists...
    assert env.services.user_approvals.pending() == []
    assert post(env, "/approvals/session", {"code": "synthetic-start-code"}).status_code == 200
    assert env.client.get("/gui/api/approvals", headers=env.headers).json()["items"] == []
    assert env.client.get("/gui/api/pending", headers=env.headers).json()["items"] == []
    # ...and a visible kind is still listed next to it
    env.services.user_approvals.request(kind="osv_query", destination="api.osv.dev", location={"bundle": "b"},
                                        sha256="a" * 64, masked_sample="r*")
    assert [item["kind"] for item in env.client.get("/gui/api/approvals", headers=env.headers).json()["items"]] == [
        "osv_query"]
    assert [item["reason"] for item in env.client.get("/gui/api/pending", headers=env.headers).json()["items"]] == [
        "osv_query"]


def test_orphan_activation_approval_is_reused_and_hidden(env):
    first = env.activations.request("claude_cli")
    second = env.activations.request("claude_cli")
    assert first["approval_id"] == second["approval_id"] and first["sha256"] == second["sha256"]
    assert env.activations.request("codex_cli")["approval_id"] != first["approval_id"]
    env.detector.set("claude", account="other@example.test")       # another fingerprint: a new approval
    assert env.activations.request("claude_cli")["approval_id"] != first["approval_id"]
    assert env.services.user_approvals.pending() == []


# --------------------------------------------------------------------------- the fingerprint

def test_fingerprint_uses_major_version_and_ignores_missing_account(env):
    activate(env, "claude_cli")
    env.detector.set("claude", version="2.4.3")                   # a minor and a patch: nothing
    env.activations.check_launch("claude_cli")
    env.detector.set("claude", account=None)                      # the CLI stops reporting the account: nothing
    env.activations.check_launch("claude_cli")
    assert env.activations.active_ids() == ["claude_cli"]
    env.detector.set("claude", version="3.0.0", account="person@example.test")
    err = refused(lambda: env.activations.check_launch("claude_cli"), reason="executor_paused_changed")
    assert err.details["changes"] == [{"field": "major", "from": "2", "to": "3"}]

    activate(env, "claude_cli")                                    # a new note and code, a new fingerprint
    env.detector.set("claude", account="someone.else@example.test")
    err = refused(lambda: env.activations.check_launch("claude_cli"), reason="executor_paused_changed")
    assert [change["field"] for change in err.details["changes"]] == ["account"]

    activate(env, "claude_cli")
    env.detector.set("claude", path="D:\\elsewhere\\claude.exe")
    err = refused(lambda: env.activations.check_launch("claude_cli"), reason="executor_paused_changed")
    assert [change["field"] for change in err.details["changes"]] == ["path"]


def test_fingerprint_has_path_major_account_and_sha(env):
    fingerprint = env.activations.fingerprint("claude_cli")
    assert set(fingerprint) == {"path", "major", "account", "sha256"}
    assert fingerprint["major"] == "2" and len(fingerprint["sha256"]) == 64
    assert env.activations.fingerprint("codex_cli")["account"] is None
    assert env.activations.fingerprint("claude_cli") == fingerprint        # stable
    env.detector.set("claude", version="2.9.9")
    assert env.activations.fingerprint("claude_cli")["sha256"] == fingerprint["sha256"]


def test_changed_between_note_and_click(env):
    asked = env.activations.request("claude_cli")
    env.services.user_approvals.decide(asked["approval_id"], "approve")
    env.detector.set("claude", version="3.1.0")
    refused(lambda: env.activations.activate("claude_cli", asked["approval_id"]), reason="executor_paused_changed")
    assert env.activations.active_ids() == []
    assert not (env.settings.local_dir / ea.ACTIVATIONS_FILE).exists()
    assert team_events(env.services, "executor.activated") == []
    # the approval was spent: it cannot be used again with the old note
    env.detector.set("claude", version="2.0.0")
    refused(lambda: env.activations.activate("claude_cli", asked["approval_id"]), reason="person_required")


def test_check_launch_pauses_on_change(tmp_path):
    world = make_world(tmp_path, tmp_path / "bin")
    services = world.services
    detector = FakeDetector()
    services.executor_activations.detector = detector
    asked = services.executor_activations.request("claude_cli")
    services.user_approvals.decide(asked["approval_id"], "approve")
    services.executor_activations.activate("claude_cli", asked["approval_id"])
    detector.set("claude", version="3.0.1")
    error = refused(lambda: services.executors.run_task("T1", "claude_cli"), reason="executor_paused_changed")
    assert error.details["changes"][0]["field"] == "major"
    deactivated = team_events(services, "executor.deactivated")
    assert [event["payload"]["reason"] for event in deactivated] == ["changed"]
    assert deactivated[0]["payload"]["executor_id"] == "claude_cli"
    row = services.executor_activations.status()[1]
    assert row["state"] == "paused" and row["paused"]["reason"] == "changed" and row["changes"]
    # paused: the next launch does not even look again, and nothing was started
    refused(lambda: services.executors.run_task("T1", "claude_cli"), reason="executor_paused_changed")
    assert services.tasks.get_task("T1")["state"] != "running"
    # an executor nobody activated here is left alone (an «enabled: true» edited by hand keeps working)
    services.executor_activations.check_launch("codex_cli")


# --------------------------------------------------------------------------- the registry

def test_apply_to_config_and_reset_registry(env):
    registry = env.services.assignment.registry
    catalog_before = json.dumps(env.services.system.catalog.executor_routing, sort_keys=True, default=str)
    assert registry.get("claude_cli").enabled is False
    config = env.services.system.catalog.executor_routing
    assert env.activations.apply_to_config(config) == config                # nothing active: an equal copy
    activate(env, "claude_cli")
    applied = env.activations.apply_to_config(config)
    assert applied["executors"]["claude_cli"]["enabled"] is True
    assert applied["executors"]["codex_cli"]["enabled"] is False
    assert config["executors"]["claude_cli"]["enabled"] is False            # a copy: the input is untouched
    assert env.services.assignment.registry.get("claude_cli").enabled is True
    assert env.services.assignment.registry.get("codex_cli").enabled is False
    env.activations.deactivate("claude_cli")
    assert env.services.assignment.registry.get("claude_cli").enabled is False
    assert json.dumps(env.services.system.catalog.executor_routing, sort_keys=True, default=str) == catalog_before


def test_injected_registry_survives_reset(tmp_path):
    world = make_world(tmp_path, tmp_path / "bin")
    registry = world.services.assignment.registry
    world.services.assignment.reset_registry()
    assert world.services.assignment.registry is registry


# --------------------------------------------------------------------------- turning off

def test_deactivate_is_immediate_without_code(env):
    activate(env, "claude_cli")
    assert env.activations.active_ids() == ["claude_cli"]
    done = post(env, "/executors/claude_cli/deactivate")                  # the token alone
    assert done.status_code == 200 and done.json()["state"] == "ready"
    assert env.activations.active_ids() == []
    events = team_events(env.services, "executor.deactivated")
    assert [event["payload"]["reason"] for event in events] == ["by_person"]
    assert events[0]["payload"]["channel"] == "gui" and events[0]["payload"]["major"] == "2"
    assert post(env, "/executors/claude_cli/deactivate").status_code == 409     # nothing left to turn off
    assert env.client.post("/gui/api/executors/claude_cli/deactivate", json={}).status_code == 401


def test_stop_all_cancels_pauses_and_writes_kill_switch(env):
    activate(env, "claude_cli")
    activate(env, "antigravity_cli")
    cancelled = []
    background = SimpleNamespace(active=lambda: [{"task_id": "T1"}, {"task_id": "T2"}],
                                 cancel=lambda task_id: cancelled.append(task_id) or True)
    env.services.executors.background = background
    result = post(env, "/executors/stop-all").json()
    assert cancelled == ["T1", "T2"] and result["stopped_tasks"] == ["T1", "T2"]
    assert sorted(result["paused"]) == ["antigravity_cli", "claude_cli"]
    assert env.activations.active_ids() == []
    rows = {row["id"]: row for row in env.activations.status()}
    assert rows["claude_cli"]["state"] == "paused" and rows["claude_cli"]["paused"]["reason"] == "stopped_by_person"
    switch = env.settings.antigravity_kill_switch
    assert switch.read_text(encoding="utf-8").splitlines()[0] == ea.KILL_MARKER == "maf.kill-switch/1"
    assert rows["antigravity_cli"]["kill_switch_foreign"] is False
    reasons = [event["payload"]["reason"] for event in team_events(env.services, "executor.deactivated")]
    assert reasons == ["stopped_by_person", "stopped_by_person"]
    # the registry no longer has them on, and a launch says why
    assert env.services.assignment.registry.get("claude_cli").enabled is False
    refused(lambda: env.activations.check_launch("claude_cli"), reason="executor_stopped_by_person")
    # activating again is a new note and a new code, one by one
    env.services.executors.background = SimpleNamespace(active=lambda: [], cancel=lambda task_id: False)
    activate(env, "claude_cli")
    assert env.activations.active_ids() == ["claude_cli"]


def test_agy_activation_removes_only_marked_kill_switch(env):
    switch = env.settings.antigravity_kill_switch
    switch.parent.mkdir(parents=True, exist_ok=True)
    switch.write_text(ea.KILL_MARKER + "\nwritten by the framework\n", encoding="utf-8")
    activate(env, "antigravity_cli")
    assert not switch.exists()
    env.activations.deactivate("antigravity_cli")
    switch.write_text("stopped by hand\n", encoding="utf-8")                  # no marker: a person's own
    rows = {row["id"]: row for row in env.activations.status()}
    assert rows["antigravity_cli"]["kill_switch_foreign"] is True and rows["claude_cli"]["kill_switch_foreign"] is False
    row = activate(env, "antigravity_cli")
    assert switch.read_text(encoding="utf-8") == "stopped by hand\n"
    assert row["kill_switch_foreign"] is True
    env.services.executors.background = SimpleNamespace(active=lambda: [], cancel=lambda task_id: False)
    assert env.activations.stop_all()["kill_switch"] == "foreign"            # «Apagar todos» does not overwrite it either
    assert switch.read_text(encoding="utf-8") == "stopped by hand\n"


def test_agy_note_counts_as_notice_accepted(env):
    assert env.settings.antigravity_notice_accepted == ""
    adapter = env.services.assignment.registry.resolve("antigravity_cli")
    assert adapter.notice_accepted is False
    asked = env.activations.request("antigravity_cli")
    assert ea.NOTICE_IDS["antigravity_cli"] in asked["note"] and "AI credits" in asked["note"]
    assert "credits" not in env.activations.request("claude_cli")["note"]
    activate(env, "antigravity_cli")
    assert env.activations.notice_accepted("antigravity_cli") == AGY_NOTICE
    adapter = env.services.assignment.registry.resolve("antigravity_cli")
    assert adapter.notice_accepted is True
    assert env.services.assignment.registry.get("antigravity_cli").enabled is True
    entry = json.loads((env.settings.local_dir / ea.ACTIVATIONS_FILE).read_text(encoding="utf-8"))
    assert entry["executors"]["antigravity_cli"]["notice"] == AGY_NOTICE
    env.activations.deactivate("antigravity_cli")
    assert env.services.assignment.registry.resolve("antigravity_cli").notice_accepted is False


def test_antigravity_adapter_takes_the_saved_notice():
    from multiagent.executors.defaults import antigravity_adapter

    assert antigravity_adapter(None, notice_accepted=AGY_NOTICE).notice_accepted is True
    assert antigravity_adapter(None).notice_accepted is False
    assert antigravity_adapter(None, notice_accepted="something else").notice_accepted is False


def test_one_by_one(env):
    # one executor and one approval per call: there is no route and no service call that takes several
    paths = [getattr(route, "path", "") for route in env.app.routes]
    assert not [path for path in paths if "activate-all" in path or "activate_all" in path]
    assert post(env, "/executors/all/activation").status_code == 404
    assert post(env, "/executors/claude_cli/activate", {"approval_id": ["a", "b"]}).status_code == 422
    first = approved(env, "claude_cli")
    refused(lambda: env.activations.activate("codex_cli", first["approval_id"]), reason="person_required")
    assert env.activations.active_ids() == []
    with pytest.raises(TypeError):
        env.activations.activate(["claude_cli", "codex_cli"])           # type: ignore[call-arg]
    activate(env, "codex_cli")
    assert env.activations.active_ids() == ["codex_cli"]


def test_status_rows_have_the_documented_fields(env):
    rows = env.activations.status()
    assert [row["id"] for row in rows] == list(ea.EXECUTOR_IDS)
    assert all(set(row) == {"id", "state", "session", "activated_at", "activated_by", "paused", "changes",
                            "kill_switch_foreign"} for row in rows)
    assert {row["state"] for row in rows} <= set(ea.STATES)
    listed = env.client.get("/gui/api/executors/activations", headers=env.headers)
    assert listed.status_code == 200 and [row["id"] for row in listed.json()["executors"]] == list(ea.EXECUTOR_IDS)


def test_broken_file_reads_as_nothing_activated(env):
    folder = env.settings.local_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ea.ACTIVATIONS_FILE).write_text("{not json", encoding="utf-8")
    assert env.activations.active_ids() == []
    activate(env, "claude_cli")                                             # and the next change rewrites it whole
    assert env.activations.active_ids() == ["claude_cli"]
    assert not list(folder.glob("*.tmp"))
