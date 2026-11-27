# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EXA-T05: «Search for apps on this computer» is a consent the person gives once.

Without ``accepted`` no app is measured, from any place that reads the connections or the activations. A counting double
stands in for the detector: no CLI, no ``npm``, no network and no real install is ever run, and nobody presses «Activate».
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.gui_settings import GUI_SETTINGS_FILE
from multiagent.setup import guided


class CountingDetector:
    """The shape of ``setup.detect.detect`` for the four apps. It counts its calls and never runs anything."""

    def __init__(self):
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        apps = [{"app": app, "detection": "detected", "version": "1.2.3", "path": f"C:\\fake\\bin\\{app}.exe",
                 "session": {"state": "signed_in"}} for app in ("claude", "codex", "copilot", "agy")]
        return {"contexts": {"packaged_process": {"state": "measured", "apps": apps}}}


@pytest.fixture
def world(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, dry_run=True, adapter_overrides={"ollama": FakeAdapter()})
    detector = CountingDetector()
    services.connections.detector = detector
    services.connections.require_discovery_consent = True       # the guard itself is under test: any detector is guarded
    services.usage.plans = lambda: {"apps": []}                  # the real one would read the home and could start agy
    token = ensure_token(settings.gui_token_file)
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    with TestClient(app, base_url="http://localhost") as client:
        yield SimpleNamespace(services=services, detector=detector, client=client, settings=settings,
                              headers={"Authorization": f"Bearer {token}"}, app=app)
    services.runs.close()


def test_the_production_services_leave_the_discovery_guard_on(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, dry_run=True, adapter_overrides={"ollama": FakeAdapter()})
    try:
        assert services.connections.require_discovery_consent is None
        assert services.connections.discovery_guarded() is True
    finally:
        services.runs.close()


def rows(world):
    return {row["id"]: row for row in world.services.connections.list()["connections"]}


def answer(world, value):
    return world.client.put("/gui/api/connections/discovery", headers=world.headers, json={"answer": value})


# --- nothing is measured without «accepted» ---------------------------------------------------------------------------------

def test_without_the_setting_nothing_calls_the_detector_from_any_reader(world):
    services = world.services
    listed = rows(world)
    services.connections.check("codex_cli")
    services.executor_activations.status()
    services.first_run.status()
    services.connections.list()
    assert world.detector.calls == 0
    for identifier in ("claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"):
        assert listed[identifier]["reason"] == "consent_required" and listed[identifier]["installed"] is None
        assert listed[identifier]["available"] is False and listed[identifier]["version"] is None
    with pytest.raises(ServiceError) as caught:
        services.executor_activations.request("claude_cli")
    assert caught.value.details["reason"] == "consent_required"
    with pytest.raises(ServiceError):
        services.executor_activations.fingerprint("codex_cli")
    assert world.detector.calls == 0


def test_the_activation_status_says_nothing_was_measured_without_inventing_a_state(world):
    states = {row["id"]: row for row in world.services.executor_activations.status()}
    for executor_id in ("claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"):
        assert states[executor_id]["consent_required"] is True and states[executor_id]["session"] == "unknown"
    assert "consent_required" not in states["codex_acp"]
    assert world.detector.calls == 0


def test_declined_does_not_measure_either(world):
    assert answer(world, "declined").status_code == 200
    listed = rows(world)
    world.services.executor_activations.status()
    assert world.detector.calls == 0
    assert listed["claude_cli"]["reason"] == "consent_required"
    assert world.services.connections.discovery()["answer"] == "declined"


def test_the_first_run_and_the_other_readers_of_the_rows_measure_nothing(world):
    status = world.services.first_run.status()
    assert status["discovery"] is None
    world.client.get("/gui/api/start", headers=world.headers)
    world.client.get("/gui/api/composer/options", headers=world.headers)
    world.client.get("/api/v1/connections")
    world.client.post("/api/v1/connections/claude_cli/check")
    assert world.detector.calls == 0


# --- accepted measures once ------------------------------------------------------------------------------------------------

def test_accepted_measures_once_and_keeps_its_hour_and_actor(world):
    answered = answer(world, "accepted")
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert world.detector.calls == 1
    by_id = {row["id"]: row for row in body["connections"]}
    assert by_id["claude_cli"]["installed"] is True and by_id["claude_cli"]["version"] == "1.2.3"
    assert by_id["claude_cli"]["session"] == "signed_in" and by_id["claude_cli"]["reason"] != "consent_required"
    saved = json.loads((world.settings.local_dir / GUI_SETTINGS_FILE).read_text(encoding="utf-8"))["app_discovery"]
    assert saved["answer"] == "accepted" and saved["by"] == "user:gui@gui" and saved["at"].endswith("+00:00")
    world.services.connections.list()
    world.services.connections.list()
    assert world.detector.calls == 1                                  # «Refresh» of the cache does not measure again


def test_the_answer_survives_a_restart_and_is_measured_again_without_asking(world):
    answer(world, "accepted")
    fresh = ApplicationServices.create(world.settings, dry_run=True, adapter_overrides={"ollama": FakeAdapter()})
    try:
        detector = CountingDetector()
        fresh.connections.detector = detector
        fresh.connections.require_discovery_consent = True
        fresh.usage.plans = lambda: {"apps": []}
        assert fresh.connections.discovery()["answer"] == "accepted"       # the consent is saved, the result is not
        listed = {row["id"]: row for row in fresh.connections.list()["connections"]}
        assert detector.calls == 1 and listed["codex_cli"]["installed"] is True
    finally:
        fresh.runs.close()


def test_check_again_measures_again_without_asking(world):
    answer(world, "accepted")
    world.client.post("/gui/api/connections/codex_cli/check", headers=world.headers, json={})
    assert world.detector.calls == 2


def test_withdrawing_stops_the_measuring(world):
    answer(world, "accepted")
    before = world.detector.calls
    withdrawn = answer(world, "declined").json()
    world.services.connections.check("claude_cli")
    world.services.executor_activations.status()
    world.services.connections.list()
    assert world.detector.calls == before
    assert {row["id"]: row for row in withdrawn["connections"]}["claude_cli"]["reason"] == "consent_required"
    assert withdrawn["discovery"]["answer"] == "declined"


# --- only the person writes it --------------------------------------------------------------------------------------------

def test_an_actor_that_is_not_the_gui_person_cannot_write_the_answer(world):
    settings = world.services.gui_settings
    for actor, channel in (("agent:helper", "gui"), ("user:gui", "cli"), ("user:ana", "gui"), ("system:executor-activations", "gui")):
        with pytest.raises(ServiceError) as caught:
            settings.set_app_discovery("accepted", actor=actor, channel=channel)
        assert caught.value.status == 422
    with pytest.raises(ServiceError):
        world.services.connections.set_discovery("accepted", actor="agent:helper")
    with pytest.raises(ServiceError):
        settings.set_value("app_discovery", "accepted")                # not a key of the generic setter
    assert settings.app_discovery() is None and not (world.settings.local_dir / GUI_SETTINGS_FILE).exists()


def test_no_agent_route_and_no_invalid_answer_reaches_the_setting(world):
    assert answer(world, "maybe").status_code == 422
    assert world.client.put("/gui/api/connections/discovery", json={"answer": "accepted"}).status_code in (401, 403)
    for route in ("/api/v1/connections/discovery", "/api/v1/settings/app_discovery", "/api/v1/gui/connections/discovery"):
        assert world.client.put(route, json={"answer": "accepted"}).status_code in (401, 403, 404, 405)
    assert world.client.post("/gui/api/settings/mode", headers=world.headers, json={"mode": "app_discovery"}).status_code == 422
    assert world.services.gui_settings.app_discovery() is None
    assert world.detector.calls == 0


def test_the_other_answers_of_the_file_are_kept_when_the_answer_is_written(world):
    world.services.gui_settings.set_mode("advanced")
    answer(world, "accepted")
    content = json.loads((world.settings.local_dir / GUI_SETTINGS_FILE).read_text(encoding="utf-8"))
    assert content["mode"] == "advanced" and content["app_discovery"]["answer"] == "accepted"
    world.services.gui_settings.set_mode("simple")
    assert world.services.gui_settings.app_discovery()["answer"] == "accepted"


# --- a saved activation does not depend on the measuring -------------------------------------------------------------------

def test_a_saved_activation_stays_active_and_available_without_the_consent(world):
    activations = world.services.executor_activations
    fingerprint = {"path": "c:\\fake\\bin\\claude.exe", "major": "1", "account": None, "sha256": "synthetic"}
    activations._write({"claude_cli": {"fingerprint": fingerprint, "by": "user:gui", "at": "2026-11-20T10:00:00+00:00",
                                       "channel": "gui", "notice": None, "paused": None}})
    assert activations.active_ids() == ["claude_cli"]
    activations.check_launch("claude_cli")                              # no measuring, so no change is seen and nothing pauses
    states = {row["id"]: row for row in activations.status()}
    assert states["claude_cli"]["state"] == "active" and "consent_required" not in states["claude_cli"]
    row = rows(world)["claude_cli"]
    assert row["reason"] is None and row["available"] is True and row["enabled"] is True
    assert row["discovery"] == "consent_required" and row["installed"] is None
    assert rows(world)["codex_cli"]["reason"] == "consent_required"
    assert world.detector.calls == 0
    assert activations.active_ids() == ["claude_cli"]


def test_with_the_consent_a_change_still_pauses_a_saved_activation(world):
    answer(world, "accepted")
    activations = world.services.executor_activations
    fingerprint = {"path": "c:\\other\\claude.exe", "major": "1", "account": None, "sha256": "synthetic"}
    activations._write({"claude_cli": {"fingerprint": fingerprint, "by": "user:gui", "at": "2026-11-20T10:00:00+00:00",
                                       "channel": "gui", "notice": None, "paused": None}})
    with pytest.raises(ServiceError) as caught:
        activations.check_launch("claude_cli")
    assert caught.value.details["reason"] == "executor_paused_changed"


# --- the route, the presets and the install steps ---------------------------------------------------------------------------

def test_the_discovery_route_gives_the_fixed_presets_and_what_is_read(world):
    body = world.client.get("/gui/api/connections/discovery", headers=world.headers).json()
    assert body["answer"] is None and body["allowed"] is False and body["stored_in"] == "gui-settings.json"
    assert body["not_released"] == ["install", "sign_in", "activate", "credentials", "quota"]
    presets = {item["app"]: item for item in body["presets"]}
    for app in ("claude", "codex", "copilot"):
        plan = guided.step_plan(app, "install")
        assert presets[app]["install"] == {"command": plan["command"], "source": plan["source"],
                                           "source_date": plan["source_date"]}
    assert presets["agy"]["install"] is None and presets["agy"]["id"] == "antigravity_cli"
    assert body["reads"]["codex"] == [["--version"], ["login", "status"]] and body["reads"]["copilot"] == [["--version"]]
    assert world.detector.calls == 0


def test_agy_has_no_install_command():
    with pytest.raises(ValueError):
        guided.step_plan("agy", "install")
    assert "agy" not in guided.STEPS


@pytest.mark.parametrize("text, family", [
    ("'npm' is not recognized as an internal or external command", "npm_missing"),
    ("bash: npm: command not found", "npm_missing"),
    ("npm ERR! code EACCES\nnpm ERR! syscall mkdir", "permissions"),
    ("EPERM: operation not permitted, rename", "permissions"),
    ("npm ERR! code ENOTFOUND\nnpm ERR! getaddrinfo ENOTFOUND registry.npmjs.org", "network"),
    ("npm ERR! code SELF_SIGNED_CERT_IN_CHAIN", "network"),
    ("npm ERR! network proxy refused the connection", "network"),
    ("npm ERR! code E404\nnpm ERR! 404 Not Found - GET https://registry.npmjs.org/nope", "package_not_found"),
])
def test_the_four_npm_families_are_recognised(text, family):
    explained = guided.explain_npm_failure(text)
    assert explained["family"] == family and explained["message"] and explained["detail"] == text.strip()


def test_any_other_npm_text_is_a_technical_detail_and_is_not_interpreted():
    explained = guided.explain_npm_failure("npm ERR! something nobody planned for")
    assert explained == {"family": "technical_detail", "message": None, "detail": "npm ERR! something nobody planned for"}
    assert guided.explain_npm_failure(None)["family"] == "technical_detail"
    assert len(guided.explain_npm_failure("x" * 5000)["detail"]) == guided.NPM_DETAIL_LIMIT


def test_a_failed_npm_install_explains_itself_in_the_console(monkeypatch, capsys):
    monkeypatch.setattr(guided.launch_safety, "launch_environment", lambda: {"SAFE": "1"})
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=1, stderr="npm ERR! code EACCES")

    code = guided.console_step("codex", "install", wait=lambda _: None, run=run, find=lambda name: f"C:/fake/{name}.exe")
    printed = capsys.readouterr().out
    assert code == 1 and calls[0][0] == ["C:/fake/npm.exe", "install", "-g", "@openai/codex"]
    assert "not allowed to write" in printed and "Technical detail" in printed and "EACCES" in printed


# --- the console recheck is a search too -----------------------------------------------------------------------------------

def test_the_recheck_after_a_console_closes_runs_only_with_the_consent(world, monkeypatch):
    seen = []
    measured = []
    monkeypatch.setattr(guided, "detect", lambda **kwargs: measured.append(kwargs) or {"contexts": {}})
    world.services.setup_guide = lambda app, step, **kwargs: seen.append(kwargs) or {
        "plan": {"app": app, "step": step}, "console_id": "synthetic", "state": "open"}
    world.client.post("/gui/api/setup/console", headers=world.headers, json={"id": "codex_cli", "step": "install"})
    assert seen[0]["detector"](context="packaged_process") == {"skipped": "consent_required"} and measured == []
    answer(world, "accepted")
    seen[0]["detector"](context="packaged_process")
    assert len(measured) == 1
