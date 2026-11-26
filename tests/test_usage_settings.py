# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI-owned usage settings and their server-side consumers."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.budget_cut import BudgetGuard, BudgetPolicy, policy_from_settings
from multiagent.call_guards import PROCEED, CallContext, Pause
from multiagent.catalog import ModelSpec
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices
from multiagent.services.errors import ServiceError
from multiagent.services.gui_settings import GUI_SETTINGS_FILE
from usage_fixtures import NOW, add_telemetry


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token",
                        usage_budgets=[{"id": "environment", "scope": "month", "amount": 50, "mode": "cut"}],
                        continuity_mode="manual")
    services = ApplicationServices.create(settings, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(services=services, client=client, auth={"Authorization": f"Bearer {token}"},
                              file=settings.local_dir / GUI_SETTINGS_FILE)
    services.runs.close()


def test_budget_route_persists_and_overrides_environment(env):
    api = "/gui/api/settings/budget"
    assert env.client.get(api).status_code == 401
    initial = env.client.get(api, headers=env.auth).json()
    assert initial["source"] == "environment" and initial["budgets"][0]["id"] == "environment"
    assert not env.file.exists()
    body = {"providers": {"openai": {"amount": 20.5, "mode": "warn"},
                          "gemini": {"amount": 0, "mode": "cut"},
                          "ollama": {"amount": None, "mode": "warn"}}}
    response = env.client.put(api, json=body, headers=env.auth)
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "gui"
    assert {row["provider"] for row in response.json()["budgets"]} == {"openai", "gemini"}
    assert env.client.get(api, headers=env.auth).json()["providers"] == body["providers"]
    assert json.loads(env.file.read_text(encoding="utf-8"))["updated_by"] == "user:gui@gui"
    budgets = env.services.usage.budgets()
    assert budgets["source"] == "gui" and {row["provider"] for row in budgets["status"]} == {"openai", "gemini"}
    assert next(row for row in budgets["status"] if row["provider"] == "gemini")["state"] == "limit_reached"
    assert policy_from_settings(env.services.settings, env.services.usage).cut is True
    row = next(row for row in budgets["status"] if row["provider"] == "gemini")
    assert BudgetGuard._applies(row, SimpleNamespace(spec=SimpleNamespace(provider="gemini")), {})
    assert not BudgetGuard._applies(row, SimpleNamespace(spec=SimpleNamespace(provider="openai")), {})


def test_gui_can_remove_environment_cut_without_creating_a_zero_budget(env):
    answer = env.client.put("/gui/api/settings/budget", json={"providers": {}}, headers=env.auth)
    assert answer.status_code == 200 and answer.json() == {"source": "gui", "providers": {}, "known_providers": ["gemini", "ollama", "openai"], "budgets": []}
    assert env.services.usage.budgets()["status"] == []
    assert policy_from_settings(env.services.settings, env.services.usage) is None


@pytest.mark.parametrize("body", [
    {"providers": {"openai": {"amount": -1, "mode": "cut"}}},
    {"providers": {"openai": {"amount": True, "mode": "warn"}}},
    {"providers": {"openai": {"amount": "20", "mode": "warn"}}},
    {"providers": {"openai": {"amount": 10**100, "mode": "warn"}}},
    {"providers": {"openai": {"amount": 1, "mode": "auto"}}},
    {"providers": {"openai": {"amount": 1, "mode": "cut", "overage": True}}},
    {"providers": {}, "max_handoffs": 3},
])
def test_budget_rejects_invalid_values(env, body):
    answer = env.client.put("/gui/api/settings/budget", json=body, headers=env.auth)
    assert answer.status_code == 422
    assert not env.file.exists()


def test_continuity_route_resolves_saved_policy_and_keeps_hard_limits(env):
    api = "/gui/api/settings/continuity"
    initial = env.client.get(api, headers=env.auth).json()
    assert initial["mode"] == "manual" and initial["source"] == "environment"
    assert initial["automatic"] is False and initial["automatic_since"] is None and not env.file.exists()   # USO-T08: off, nothing written
    body = {"mode": "semi_auto", "wait_threshold_hours": 24,
            "candidates": {"claude_cli": "never", "codex_cli": "ask", "copilot_cli": "never"}}
    response = env.client.put(api, json=body, headers=env.auth)
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "gui" and response.json()["automatic"] is False       # saving the three keys does not turn it on
    assert response.json()["automatic_since"] is None and response.json()["mode"] == "semi_auto"
    assert env.services.executors.continuity_policy()["mode"] == "semi_auto"
    assert env.services.executors.continuity_policy()["wait_threshold_hours"] == 24
    assert env.services.executors.continuity_policy()["candidates"] == body["candidates"]
    assert env.services.executors._policy_of({"mode": "manual"}).mode == "manual"
    assert env.services.executors.continuity_policy()["max_handoffs"] == 1


@pytest.mark.parametrize("extra", [
    {"automatic": "yes"}, {"automatic_since": "2026-01-01T00:00:00Z"}, {"max_handoffs": 3}, {"overage": True},
    {"mode": "auto"}, {"wait_threshold_hours": 0},
    {"candidates": {"paid_api": "no_ask"}},
])
def test_continuity_rejects_auto_as_a_mode_and_fixed_rule_changes(env, extra):
    # USO-T08: ``automatic`` is now a switch (a boolean the server stamps); ``auto`` is still never a mode a body may name
    body = {"mode": "manual", "wait_threshold_hours": 5, "candidates": {"claude_cli": "ask", "codex_cli": "ask", "copilot_cli": "ask"}}
    body.update(extra)
    assert env.client.put("/gui/api/settings/continuity", json=body, headers=env.auth).status_code == 422
    assert not env.file.exists()


def test_other_actor_cannot_write_even_through_service(env):
    with pytest.raises(ServiceError) as error:
        env.services.gui_settings.set_usage("continuity", {"mode": "manual", "wait_threshold_hours": 5,
                                                               "candidates": {}}, actor="agent:planner")
    assert error.value.status == 422 and not env.file.exists()


def _put(env, route, body, **kwargs):
    return env.client.put(f"/gui/api/settings/{route}", json=body, headers=kwargs.pop("headers", env.auth), **kwargs)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity"])
def test_budget_rejects_non_finite_amounts_on_the_wire(env, amount):
    raw = '{"providers": {"openai": {"amount": %s, "mode": "warn"}}}' % amount
    answer = env.client.put("/gui/api/settings/budget", content=raw,
                            headers={**env.auth, "Content-Type": "application/json"})
    assert answer.status_code == 422 and not env.file.exists()


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), float("-inf")])
def test_budget_rejects_non_finite_amounts_in_the_service(env, amount):
    with pytest.raises(ServiceError) as error:
        env.services.gui_settings.set_usage("budget", {"providers": {"openai": {"amount": amount, "mode": "warn"}}})
    assert error.value.status == 422 and not env.file.exists()


def test_budget_rejects_an_unknown_provider_and_accepts_the_catalog_ones(env):
    for name in ("unknown_lab", "google", "anthropic"):
        answer = _put(env, "budget", {"providers": {name: {"amount": 1, "mode": "warn"}}})
        assert answer.status_code == 422
    assert not env.file.exists()
    with pytest.raises(ServiceError) as error:
        env.services.gui_settings.set_usage("budget", {"providers": {"unknown_lab": {"amount": 1, "mode": "warn"}}})
    assert "unknown_lab" in error.value.message
    known = {spec.provider for spec in env.services.system.catalog.models.values()}
    assert {"gemini", "openai", "ollama"} <= known
    body = {"providers": {name: {"amount": 1, "mode": "warn"} for name in sorted(known)}}
    assert _put(env, "budget", body).status_code == 200


def test_a_negative_zero_is_stored_as_zero(env):
    assert _put(env, "budget", {"providers": {"openai": {"amount": -0.0, "mode": "cut"}}}).status_code == 200
    saved = json.loads(env.file.read_text(encoding="utf-8"))["budget"]["providers"]["openai"]["amount"]
    assert saved == 0 and str(saved) == "0.0"


@pytest.mark.parametrize("route,body", [
    ("budget", {"providers": {}}),
    ("continuity", {"mode": "manual", "wait_threshold_hours": 5,
                    "candidates": {"claude_cli": "ask", "codex_cli": "ask", "copilot_cli": "ask"}}),
])
def test_usage_routes_need_the_token_and_a_json_body(env, route, body):
    api = f"/gui/api/settings/{route}"
    assert env.client.put(api, json=body).status_code == 401
    assert env.client.put(api, json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert env.client.put(api, content=json.dumps(body),
                          headers={**env.auth, "Content-Type": "text/plain"}).status_code == 415
    assert env.client.get(api).status_code == 401
    assert not env.file.exists()


def test_budget_write_by_another_actor_is_refused(env):
    for actor in ("agent:planner", "orchestrator", "user:cli"):
        with pytest.raises(ServiceError) as error:
            env.services.gui_settings.set_usage("budget", {"providers": {}}, actor=actor)
        assert error.value.status == 422
    assert not env.file.exists()


def test_continuity_candidates_must_be_exactly_the_three_apps(env):
    base = {"mode": "manual", "wait_threshold_hours": 5}
    full = {"claude_cli": "ask", "codex_cli": "no_ask", "copilot_cli": "never"}
    assert _put(env, "continuity", {**base, "candidates": {}}).status_code == 422
    assert _put(env, "continuity", {**base, "candidates": {"claude_cli": "ask"}}).status_code == 422
    assert _put(env, "continuity", {**base, "candidates": {**full, "antigravity_cli": "ask"}}).status_code == 422
    assert not env.file.exists()
    assert _put(env, "continuity", {**base, "candidates": full}).json()["candidates"] == full


def test_resolve_merges_saved_candidates_with_the_defaults(env, monkeypatch):
    from multiagent.services import gui_settings as module
    monkeypatch.setattr(module, "_written", lambda _dir: {"continuity": {
        "mode": "semi_auto", "wait_threshold_hours": 3, "candidates": {"codex_cli": "never"}}})
    resolved = env.services.gui_settings.resolve("continuity")
    assert resolved["candidates"] == {"claude_cli": "no_ask", "codex_cli": "never", "copilot_cli": "ask"}


def test_the_continuity_source_names_a_lone_max_handoffs_setting(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token", continuity_max_handoffs=2)
    services = ApplicationServices.create(settings, dry_run=True)
    try:
        assert services.gui_settings.resolve("continuity")["source"] == "environment"
    finally:
        services.runs.close()


def test_the_continuity_view_keeps_its_source_words_and_adds_the_origin(env):
    from multiagent.gui.continuity_api import policy_view
    assert policy_view(env.services)["source"] == "settings" and policy_view(env.services)["origin"] == "environment"
    _put(env, "continuity", {"mode": "manual", "wait_threshold_hours": 5,
                             "candidates": {"claude_cli": "ask", "codex_cli": "ask", "copilot_cli": "ask"}})
    view = policy_view(env.services)
    assert view["source"] == "settings" and view["origin"] == "gui"


def test_budgets_without_a_gui_budget_have_no_provider_key(env):
    status = env.services.usage.budgets()
    assert status["budgets"] and all("provider" not in budget for budget in status["budgets"])
    assert all("provider" not in row for row in status["status"])


def _gui_budget(env, providers):
    body = {"providers": {name: {"amount": amount, "mode": mode} for name, (amount, mode) in providers.items()}}
    assert _put(env, "budget", body).status_code == 200


def test_spend_is_added_up_per_provider(env):
    env.services.usage.clock = lambda: NOW
    store = env.services.system.store
    basis = json.dumps({"model_id": "m", "currency": "USD", "cost": 1})
    for provider, amount in (("openai", 3.0), ("openai", 1.5), ("gemini", 7.0), ("ollama", 9.0)):
        add_telemetry(store, run_id="run-a", when="2026-10-05T10:00:00Z", model="m", tokens_in=10, tokens_out=10,
                      cost=(amount, basis), provider=provider)
    _gui_budget(env, {"openai": (10, "warn"), "gemini": (5, "cut")})
    rows = {row["provider"]: row for row in env.services.usage.budgets()["status"]}
    assert set(rows) == {"openai", "gemini"}
    assert rows["openai"]["used"] == pytest.approx(4.5) and rows["openai"]["state"] == "ok"
    assert rows["gemini"]["used"] == pytest.approx(7.0) and rows["gemini"]["state"] == "limit_reached"


def _priced(provider):
    side = {"amount": 0.001, "denominator": 1, "unit": "token", "currency": "USD", "source": "synthetic",
            "provenance": "catalog_declared", "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": "2036-01-01T00:00:00Z"}
    return ModelSpec(id=f"{provider}-m", provider=provider, model="m", tier="cloud", structured_output=True, web=False,
                     pricing={"input": dict(side), "output": dict(side)}, max_output_tokens=100)


@pytest.mark.parametrize("amount", [0, 0.5])
def test_a_provider_budget_in_cut_mode_pauses_for_a_person_and_other_providers_go_on(env, amount):
    env.services.usage.clock = lambda: NOW
    _gui_budget(env, {"gemini": (amount, "cut")})
    guard = BudgetGuard(BudgetPolicy(reader=env.services.usage, cut=True), emit=lambda *_: None)
    run_id = env.services.system.store.create_run(RunRequest(workflow_id="w", project_name="proj"), 4, 300)["id"]
    state = {"id": run_id, "events": [], "llm_calls": 0, "max_llm_calls": 10}

    def context(provider):
        spec = _priced(provider)
        return CallContext(model_id=spec.id, spec=spec, run_id=run_id, step_id="s", attempt=1, ordinal=1,
                           estimated_input_tokens=1000)

    assert isinstance(guard.check(context("gemini"), state), Pause)   # a person must approve this call
    assert guard.check(context("openai"), state) is PROCEED
