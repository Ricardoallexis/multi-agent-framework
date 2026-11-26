# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PAR-T05: the saved concurrency mode in the native engine (level bounds at step start, one place per model)."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from multiagent.call_guards import ModelTurnGate
from multiagent.catalog import ModelSpec
from multiagent.config import Settings
from multiagent.contracts import SocialPostRequest
from multiagent.model_router import ModelRouter
from multiagent.routing_policy import RoutingRequest
from multiagent.routing_preferences import model_limit, resolve_profile, saved_mode_options
from multiagent.services import ApplicationServices

PRICE = {"amount": 0, "denominator": 1000000, "unit": "token", "currency": "USD", "source": "synthetic local rate",
         "provenance": "local_no_charge", "valid_from": "2020-01-01T00:00:00Z", "valid_until": "2099-01-01T00:00:00Z"}


def _add_model(catalog, name, level, provider="ollama"):
    base = catalog.models["local_default"]
    catalog.models[name] = ModelSpec(**{**vars(base), "id": name, "provider": provider, "level": level,
                                        "classification": "classified", "pricing": {"input": PRICE, "output": PRICE},
                                        "allowed_data_classes": ["non_sensitive"], "permissions_required": [],
                                        "permissions": []})


def _profile(candidates, levels):
    return {"version": 1, "enabled": True, "mode": "supervised", "levels": levels, "candidate_model_ids": candidates,
            "required_capabilities": ["structured_output"], "required_roles": [], "required_permissions": [],
            "allowed_permissions": [], "allowed_data_classes": ["non_sensitive"], "execution_modes": ["auto", "local"],
            "require_health": False, "max_retries_per_candidate": 0}


@pytest.fixture
def services(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        openai_enabled=False, openai_api_key="", gemini_api_key="", gui_token_file=tmp_path / "token")
    result = ApplicationServices.create(settings)
    engine = result.system.engine
    engine.model_gate = ModelTurnGate(poll_seconds=0.02)       # a gate of its own: tests never share places
    yield result
    result.runs.close()


def _save(services, **concurrency):
    services.gui_settings.set_concurrency({"selection": "manual", **concurrency})


def _post(services, name):
    return services.system.run_service.create_social_post(SocialPostRequest(
        project_name=name, objective="Create a short educational post", topic="Synthetic topic"))


def _events(services, run_id, kind):
    return [event for event in services.system.store.get_run(run_id)["events"] if event["event_type"] == kind]


def _decision(services, run_id):
    return [event["payload"] for event in _events(services, run_id, "routing_decision")
            if event["payload"].get("step_id") == "create"][0]


def test_real_services_connect_the_engine_to_the_concurrency_service(services):
    assert services.system.engine.concurrency_source is services.concurrency
    assert saved_mode_options(services.concurrency) is None      # nothing saved: the engine does what it did


def test_an_installed_bundle_engine_is_connected_and_idle_without_a_saved_mode(tmp_path):
    from pathlib import Path
    bundle = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    real = ApplicationServices.create(settings, bundles={"team": bundle}, dry_run=True)
    try:
        runtime = real.runs._bundle_runtime("team")
        assert runtime.service.engine.concurrency_source is real.concurrency
        assert saved_mode_options(real.concurrency) is None
        from multiagent.run_request import RunRequest
        run = real.runs.create(RunRequest(workflow_id="spec_review", project_name="Synthetic review",
                                          inputs={"spec_text": "Users can reset a password by email."}), bundle="team")
        assert runtime.worker.process_once() is True
        events = runtime.store.get_run(run["id"])["events"]
        assert not [event for event in events if event["event_type"] == "concurrency_wait"]
    finally:
        real.runs.close()


def test_the_worker_engine_is_connected_and_builds_nothing_until_a_mode_is_saved(tmp_path):
    from multiagent.bootstrap import build_system
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    system = build_system(settings, dry_run=True)
    source = system.engine.concurrency_source
    assert source is not None and source._real is None
    assert saved_mode_options(source) is None and model_limit(source, system.catalog, "local_default") == (None, "")
    assert source._real is None                                    # nothing saved: no services were built
    ApplicationServices(system).gui_settings.set_concurrency({"mode": "eco", "selection": "manual"})
    system.engine.concurrency_source = source                      # the facade above wired its own; keep the worker's
    assert saved_mode_options(source)["concurrency_mode"] == "eco"
    assert source._real is not None


def test_two_threads_build_one_application_services_for_the_worker_source(tmp_path, monkeypatch):
    from multiagent.bootstrap import build_system
    import multiagent.services as services_module
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    system = build_system(settings, dry_run=True)
    source = system.engine.concurrency_source
    real_class, built = services_module.ApplicationServices, []

    class Counting(real_class):
        def __init__(self, *args, **kwargs):
            built.append(1)
            time.sleep(0.2)                       # a slow build: the second thread arrives while the first builds
            super().__init__(*args, **kwargs)

    seed = real_class(system)                     # one facade only to save the mode; the counter starts after it
    seed.gui_settings.set_concurrency({"mode": "eco", "selection": "manual"})
    system.engine.concurrency_source = source
    monkeypatch.setattr(services_module, "ApplicationServices", Counting)
    found, start = [], threading.Barrier(2)

    def ask():
        start.wait(5)
        found.append(source.routing_options())

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert len(built) == 1 and len(found) == 2 and found[0] == found[1]
    seed.runs.close()
    if source._real is not None:
        source._real.runs.close()


# ---------- no saved mode: the engine does what it did ----------

def test_without_a_saved_mode_nothing_changes(services):
    engine = services.system.engine
    assert saved_mode_options(services.concurrency) is None
    assert model_limit(services.concurrency, engine.catalog, "local_default") == (None, "")
    run = _post(services, "plain")
    assert services.system.worker.process_once() is True
    assert services.system.run_service.get(run["id"])["status"] == "waiting_human"
    assert not _events(services, run["id"], "concurrency_wait")
    assert engine.concurrency_source is services.concurrency   # wired, and still idle without a saved mode


def test_normal_and_performance_leave_the_levels_alone(services):
    for mode in ("normal", "performance"):
        _save(services, mode=mode)
        assert saved_mode_options(services.concurrency) is None


# ---------- levels at step start ----------

def test_eco_keeps_planning_review_and_orchestration_at_t3(services):
    catalog = services.system.catalog
    profile = _profile(["t1", "t2", "t3"], {"work": {"min": "T1"}})
    for name, level in (("t1", "T1"), ("t2", "T2"), ("t3", "T3")):
        _add_model(catalog, name, level)
    catalog.routing_profiles["par_eco"] = profile
    _save(services, mode="eco")
    options = saved_mode_options(services.concurrency)
    assert options["concurrency_mode"] == "eco"
    router = ModelRouter(catalog, services.system.adapters, dry_run=True)
    for kind, expected in (("planning", "T3"), ("review", "T3"), ("orchestration", "T3")):
        _, composed = resolve_profile(catalog, "social_post", "create", kind, {"profile_id": "par_eco"}, options)
        assert composed["levels"][kind]["min"] == "T3"
        request = RoutingRequest(profile_id="par_eco", workflow_id="social_post", step_id="create", step_kind=kind,
                                 required_capabilities=frozenset({"structured_output"}), data_class="non_sensitive",
                                 execution_mode="auto")
        decision = router.select(request, profile_override=composed, **options)
        assert decision.status == "selected" and decision.selected_level == expected, (kind, decision)
    _, work = resolve_profile(catalog, "social_post", "create", "work", {"profile_id": "par_eco"}, options)
    assert work["levels"]["work"] == {"min": "T1", "max": "T2"}


def test_a_native_step_starts_with_the_eco_bounds_and_the_profile_stays_unchanged(services):
    catalog = services.system.catalog
    for name, level in (("t3", "T3"), ("t2", "T2")):
        _add_model(catalog, name, level)
    catalog.routing_profiles["par_eco"] = _profile(["t3", "t2"], {"work": {"min": "T2", "max": "T3"}})
    catalog.routing_assignments["social_post"] = {"create": "par_eco"}
    before = dict(catalog.routing_profiles["par_eco"]["levels"])
    services.system.engine.concurrency_source = services.concurrency
    _save(services, mode="eco")
    run = _post(services, "eco-step")
    assert services.system.worker.process_once() is True
    decision = _decision(services, run["id"])
    assert decision["status"] == "selected" and decision["selected_level"] == "T2"
    assert catalog.routing_profiles["par_eco"]["levels"] == before
    # the same flow without a saved mode keeps the first candidate, T3
    services.system.engine.concurrency_source = None
    other = _post(services, "no-mode")
    assert services.system.worker.process_once() is True
    assert _decision(services, other["id"])["selected_level"] == "T3"


def test_maximum_never_goes_below_t3_and_t4_needs_the_box(services):
    catalog = services.system.catalog
    for name, level in (("t2", "T2"), ("t3", "T3"), ("t4", "T4")):
        _add_model(catalog, name, level)
    catalog.routing_profiles["par_max"] = _profile(["t2", "t4", "t3"], {"work": {"min": "T1"}})
    _save(services, mode="maximum")
    options = saved_mode_options(services.concurrency)
    _, composed = resolve_profile(catalog, "social_post", "create", "work", {"profile_id": "par_max"}, options)
    assert composed["levels"]["work"] == {"min": "T3", "max": "T3"}
    _save(services, mode="maximum", allow_t4=True)
    options = saved_mode_options(services.concurrency)
    assert options["allow_t4"] is True
    _, composed = resolve_profile(catalog, "social_post", "create", "work", {"profile_id": "par_max"}, options)
    assert composed["levels"]["work"]["max"] == "T4"


def test_maximum_runs_a_native_step_at_t3_and_at_t4_only_with_the_box(services):
    catalog = services.system.catalog
    for name, level in (("t2", "T2"), ("t4", "T4"), ("t3", "T3")):
        _add_model(catalog, name, level)
    catalog.routing_profiles["par_max"] = _profile(["t2", "t4", "t3"], {"work": {"min": "T1"}})
    catalog.routing_assignments["social_post"] = {"create": "par_max"}
    services.system.engine.concurrency_source = services.concurrency
    services.system.worker.limit_source = lambda: None
    for allow, level in ((False, "T3"), (True, "T4")):
        _save(services, mode="maximum", allow_t4=allow)
        run = _post(services, f"maximum-{level}")
        assert services.system.worker.process_once() is True
        decision = _decision(services, run["id"])
        assert decision["status"] == "selected" and decision["selected_level"] == level, decision


def test_an_agy_bag_follows_the_model_name_when_the_id_is_an_alias(services):
    catalog = services.system.catalog
    _add_model(catalog, "alias_one", "T3", provider="agy")
    catalog.models["alias_one"] = ModelSpec(**{**vars(catalog.models["alias_one"]), "model": "gemini-3-pro"})
    reset = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    plans = {"apps": [{"app": "antigravity", "windows": [
        {"window": "five_hour", "group": "Gemini Models", "used_percent": 99.0, "resets_at": reset},
        {"window": "five_hour", "group": "Claude and GPT models", "used_percent": 5.0, "resets_at": reset}]}]}
    services.usage = SimpleNamespace(plans=lambda: plans)
    _save(services, mode="eco", selection="auto")
    assert model_limit(services.concurrency, catalog, "alias_one")[0] == 0


def test_an_agy_model_is_limited_by_its_own_bag(services):
    catalog = services.system.catalog
    _add_model(catalog, "gemini-3-pro", "T3", provider="agy")
    _add_model(catalog, "claude-sonnet-x", "T3", provider="agy")
    now = datetime.now(timezone.utc)
    reset = (now + timedelta(hours=3)).isoformat()
    plans = {"apps": [{"app": "antigravity", "windows": [
        {"window": "five_hour", "group": "Gemini Models", "used_percent": 99.0, "resets_at": reset},
        {"window": "five_hour", "group": "Claude and GPT models", "used_percent": 5.0, "resets_at": reset}]}]}
    services.usage = SimpleNamespace(plans=lambda: plans)
    _save(services, mode="eco", selection="auto")
    assert model_limit(services.concurrency, catalog, "gemini-3-pro")[0] == 0
    assert model_limit(services.concurrency, catalog, "claude-sonnet-x")[0] > 0


# ---------- one place per model ----------

def test_the_gate_holds_the_second_flow_until_the_first_ends():
    gate = ModelTurnGate(poll_seconds=0.01)
    order, waits = [], []
    entered, release = threading.Event(), threading.Event()

    def first():
        with gate.hold("m", lambda: (1, "m limit 1")):
            order.append("first in")
            entered.set()
            release.wait(2)
            order.append("first out")

    def second():
        entered.wait(2)
        with gate.hold("m", lambda: (1, "m limit 1"), on_wait=waits.append):
            order.append("second in")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    time.sleep(0.15)
    assert order == ["first in"] and gate.held("m") == 1 and waits[0]["reason"] == "m limit 1" and len(waits) == 1
    release.set()
    for thread in threads:
        thread.join(3)
    assert order == ["first in", "first out", "second in"] and gate.held() == 0


def test_a_limit_that_goes_down_does_not_cut_a_step_that_runs():
    gate = ModelTurnGate(poll_seconds=0.01)
    limit = {"value": 2}
    with gate.hold("m", lambda: (limit["value"], "")):
        with gate.hold("m", lambda: (limit["value"], "")):
            limit["value"] = 1          # both keep running; only a third would wait
            assert gate.held("m") == 2


def test_two_flows_with_the_same_model_and_cap_one_run_one_after_the_other(services):
    engine = services.system.engine
    engine.concurrency_source = services.concurrency
    services.system.worker.limit_source = lambda: None             # not bounded by this machine's CPU count
    _save(services, mode="normal", per_model={name: 1 for name in engine.catalog.models})
    original, state = engine.router.execute, {"active": 0, "peak": 0}
    guard = threading.Lock()

    def slow_execute(*args, **kwargs):
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(0.25)
            return original(*args, **kwargs)
        finally:
            with guard:
                state["active"] -= 1

    engine.router.execute = slow_execute
    runs = [_post(services, "first"), _post(services, "second")]
    threads = [threading.Thread(target=services.system.worker.process_once) for _ in runs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert state["peak"] == 1
    assert [services.system.run_service.get(run["id"])["status"] for run in runs] == ["waiting_human"] * 2
    waited = [run for run in runs if _events(services, run["id"], "concurrency_wait")]
    assert len(waited) == 1
    event = _events(services, waited[0]["id"], "concurrency_wait")[0]["payload"]
    assert event["limit"] == 1 and event["running"] == 1 and event["step_id"] == "create"


def test_with_cap_two_both_flows_run_at_once(services):
    engine = services.system.engine
    engine.concurrency_source = services.concurrency
    services.system.worker.limit_source = lambda: None             # not bounded by this machine's CPU count
    _save(services, mode="normal", per_model={name: 2 for name in engine.catalog.models})
    original, state, guard = engine.router.execute, {"active": 0, "peak": 0}, threading.Lock()

    def slow_execute(*args, **kwargs):
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(0.4)
            return original(*args, **kwargs)
        finally:
            with guard:
                state["active"] -= 1

    engine.router.execute = slow_execute
    runs = [_post(services, "one"), _post(services, "two")]
    threads = [threading.Thread(target=services.system.worker.process_once) for _ in runs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert state["peak"] == 2
    assert not any(_events(services, run["id"], "concurrency_wait") for run in runs)


# ---------- the quota ends: wait, never a lower level or another model ----------

def _quota_services(services, used):
    plans = {"apps": [{"app": "codex", "windows": [{
        "window": "five_hour", "used_percent": used["value"],
        "resets_at": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()}]}]}
    services.usage = SimpleNamespace(plans=lambda: plans, **{})
    return plans


def test_a_quota_at_its_margin_waits_and_never_lowers_the_level(services):
    catalog = services.system.catalog
    _add_model(catalog, "codex_t3", "T3", provider="codex_cli")
    _add_model(catalog, "cheap_t1", "T1")
    catalog.routing_profiles["par_eco"] = _profile(["codex_t3", "cheap_t1"], {"work": {"min": "T3", "max": "T3"}})
    catalog.routing_assignments["social_post"] = {"create": "par_eco"}
    used = {"value": 99.0}
    plans = _quota_services(services, used)
    services.system.engine.concurrency_source = services.concurrency
    services.system.worker.limit_source = lambda: None             # the worker's own cap is PAR-T03's; here only the engine
    _save(services, mode="eco", selection="auto")
    assert model_limit(services.concurrency, catalog, "codex_t3")[0] == 0
    assert model_limit(services.concurrency, catalog, "cheap_t1") == (None, "")   # a local model has no quota

    run = _post(services, "quota")
    worker = threading.Thread(target=services.system.worker.process_once)
    worker.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not _events(services, run["id"], "concurrency_wait"):
        time.sleep(0.02)
    waiting = _events(services, run["id"], "concurrency_wait")
    assert waiting and waiting[0]["payload"]["limit"] == 0
    assert "margin" in waiting[0]["payload"]["reason"]
    assert worker.is_alive()                                       # waiting, not failed and not downgraded
    assert services.system.run_service.get(run["id"])["status"] != "failed"
    plans["apps"][0]["windows"][0]["used_percent"] = 10.0            # the window has room again
    worker.join(20)
    assert not worker.is_alive()
    assert services.system.run_service.get(run["id"])["status"] == "waiting_human"
    decision = _decision(services, run["id"])
    assert decision["selected_model"] == "codex_t3" and decision["selected_level"] == "T3"
    assert not [e for e in _events(services, run["id"], "model_attempt") if e["payload"].get("model") == "cheap_t1"]
