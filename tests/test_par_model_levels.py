# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Mode-specific model levels, provider units and confirmed local classification."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from types import SimpleNamespace

from multiagent.adapters.ollama import OllamaAdapter
from multiagent.catalog import ModelSpec, confirmed_level
from multiagent.model_router import ModelRouter
from multiagent.routing_policy import RoutingRequest
from multiagent.services.concurrency import economic_measure, effective_profile
from multiagent.services.gui_settings import GuiSettingsServices, load_file

NOW = datetime(2026, 11, 25, tzinfo=timezone.utc)


def model(name, level, provider="ollama", tier="local"):
    return ModelSpec(id=name, provider=provider, model=name, tier=tier, structured_output=True, web=False,
                     level=level, classification="classified" if level else "unclassified",
                     permissions=frozenset(), permissions_required=frozenset(),
                     allowed_data_classes=frozenset({"non_sensitive"}))


def test_effective_levels_and_t4_opt_in():
    profile = {"enabled": True, "mode": "supervised", "candidate_model_ids": ["t1", "t3", "t4"],
               "levels": {"work": {"min": "T1"}}}
    eco = effective_profile(profile, "eco")
    maximum = effective_profile(profile, "maximum")
    assert eco["levels"]["work"] == {"min": "T1", "max": "T2"}
    assert eco["levels"]["planning"]["min"] == "T3"
    assert maximum["levels"]["work"] == {"min": "T3", "max": "T3"}
    assert effective_profile(profile, "maximum", allow_t4=True)["levels"]["work"]["max"] == "T4"
    assert profile["levels"] == {"work": {"min": "T1"}}
    own = effective_profile({**profile, "levels": {"planning": {"min": "T2"}}}, "eco")
    assert own["levels"]["planning"]["min"] == "T2"  # Eco keeps the profile's own floor


def test_provider_units_do_not_convert():
    rate = {"amount": 1, "denominator": 1000000, "unit": "token", "currency": "USD",
            "source": "test rate", "valid_from": "2025-01-01T00:00:00Z",
            "valid_until": "2035-01-01T00:00:00Z", "provenance": "catalog_declared"}
    api = ModelSpec(id="api", provider="gemini", model="api", tier="cloud", structured_output=True,
                    web=False, level="T2", classification="classified", pricing={"input": rate, "output": rate})
    assert economic_measure(api, now=NOW) == ("usd_per_token", 2 / 1000000)
    assert economic_measure(model("local", "T3"), now=NOW) == ("local_no_charge", 0.0)
    assert economic_measure(model("codex", "T2", "codex_cli", "cloud"), now=NOW) == ("quota_percent_per_step", 2.0)
    assert economic_measure(model("copilot", "T2", "copilot_cli", "cloud"), now=NOW,
                            observations={"copilot": {"premium_interaction": 0.5}}) == ("premium_interaction", 0.5)
    assert economic_measure(model("copilot", "T2", "copilot_cli", "cloud"), now=NOW,
                            observations={"copilot": {"ai_credit": 0.25}}) == ("ai_credit", 0.25)
    assert economic_measure(model("agy", "T2", "agy", "cloud"), now=NOW,
                            observations={"agy": {"bag_free_percent": 70}}) == ("bag_free_percent", -70.0)


def test_ollama_tag_details_are_exposed_without_generation():
    class Client:
        def get(self, path):
            assert path == "/api/tags"
            return SimpleNamespace(json=lambda: {"models": [{"name": "large:q4", "details": {
                "parameter_size": "70B", "quantization_level": "Q4_K_M", "family": "qwen3"}}]})
    adapter = OllamaAdapter(SimpleNamespace(), client=Client())
    assert adapter.installed_model_details() == [{"model": "large:q4", "parameter_size": "70B",
                                                  "quantization_level": "Q4_K_M", "family": "qwen3"}]


def test_only_confirmed_levels_are_accepted():
    assert confirmed_level({"level": "T3", "source": "official", "confirmed_at": "2026-11-25",
                            "confirmed_by": "user:gui"}) == "T3"
    assert confirmed_level({"level": "T3", "source": "official"}) is None


def test_confirmation_is_saved_and_audited(tmp_path):
    events = []

    class Store:
        @contextmanager
        def transaction(self):
            yield object()

        def append_event(self, conn, event_type, **kwargs):
            events.append((event_type, kwargs))

    services = SimpleNamespace(settings=SimpleNamespace(local_dir=tmp_path),
                               system=SimpleNamespace(catalog=SimpleNamespace(model=lambda model_id: model_id)),
                               tasks=SimpleNamespace(store=Store()))
    settings = GuiSettingsServices(services)
    record = settings.confirm_model_level("large:q4", "T3", "official model card")
    assert load_file(tmp_path)["model_levels"]["large:q4"]["level"] == "T3"
    assert events[0][0] == "model.level_confirmed" and events[0][1]["ref"] == "large:q4"
    assert record["confirmed_by"] == "user:gui"


def test_router_without_mode_is_unchanged_and_unclassified_blocks_modes():
    spec = model("unknown", None)
    catalog = SimpleNamespace(routing_profiles={"test": {"enabled": True, "mode": "supervised",
        "candidate_model_ids": ["unknown"], "economic_selection": False, "required_capabilities": [],
        "allowed_permissions": [], "allowed_data_classes": ["non_sensitive"]}},
        models={"unknown": spec}, model=lambda name: spec)
    router = ModelRouter(catalog, {})
    request = RoutingRequest(profile_id="test", step_kind="work", execution_mode="auto",
                             data_class="non_sensitive", authorized_data_classes=frozenset({"non_sensitive"}),
                             effective_permissions=frozenset())
    assert router.select(request).status == "selected"
    for mode in ("eco", "maximum"):
        decision = router.select(request, concurrency_mode=mode)
        assert decision.status == "blocked" and "model_unclassified" in decision.reason_codes


def test_router_maximum_requires_t3_and_t4_opt_in():
    specs = {name: model(name, level) for name, level in (("t1", "T1"), ("t3", "T3"), ("t4", "T4"))}
    profile = {"enabled": True, "mode": "supervised", "candidate_model_ids": list(specs),
               "economic_selection": False, "required_capabilities": [], "allowed_permissions": [],
               "allowed_data_classes": ["non_sensitive"]}
    catalog = SimpleNamespace(routing_profiles={"test": profile}, models=specs, model=lambda name: specs[name])
    request = RoutingRequest(profile_id="test", step_kind="work", execution_mode="auto",
                             data_class="non_sensitive", authorized_data_classes=frozenset({"non_sensitive"}),
                             effective_permissions=frozenset())
    router = ModelRouter(catalog, {})
    # Without a mode the profile keeps today's two-candidate rule and ranking.
    two = {**profile, "candidate_model_ids": ["t1", "t3"]}
    catalog.routing_profiles["two"] = two
    plain = RoutingRequest(profile_id="two", step_kind="work", execution_mode="auto",
                           data_class="non_sensitive", authorized_data_classes=frozenset({"non_sensitive"}),
                           effective_permissions=frozenset())
    assert router.select(plain).selected_model == "t1"
    maximum = router.select(request, concurrency_mode="maximum")
    assert maximum.selected_model == "t3" and maximum.fallback_models == ()
    assert router.select(request, concurrency_mode="maximum", allow_t4=True).selected_model == "t4"


# --- Round 3 ---------------------------------------------------------------------------------------

import pytest


def _cloud(name, level, provider):
    from dataclasses import replace
    return replace(model(name, level, provider, "cloud"), permissions=frozenset({"provider:cloud"}),
                   permissions_required=frozenset({"provider:cloud"}))


def _router(specs, **profile_extra):
    profile = {"enabled": True, "mode": "supervised", "candidate_model_ids": list(specs),
               "economic_selection": False, "required_capabilities": [], "allowed_permissions": ["provider:cloud"],
               "allowed_data_classes": ["non_sensitive"], **profile_extra}
    catalog = SimpleNamespace(routing_profiles={"test": profile}, models=specs, model=lambda name: specs[name])
    request = RoutingRequest(profile_id="test", step_kind="work", execution_mode="auto",
                             data_class="non_sensitive", authorized_data_classes=frozenset({"non_sensitive"}),
                             effective_permissions=frozenset({"provider:cloud"}))
    return ModelRouter(catalog, {}), request


def test_maximum_does_not_drop_classified_cloud_models_without_a_price():
    specs = {"cloud3": _cloud("cloud3", "T3", "gemini"), "cloud4": _cloud("cloud4", "T4", "copilot_cli"),
             "cloud2": _cloud("cloud2", "T2", "agy")}
    router, request = _router(specs)
    decision = router.select(request, concurrency_mode="maximum")
    assert decision.status == "selected" and decision.selected_model == "cloud3"
    assert "economic_unit_unknown" not in str(decision.candidates)
    assert router.select(request, concurrency_mode="maximum", allow_t4=True).selected_model == "cloud4"
    eco = router.select(request, concurrency_mode="eco")
    assert eco.status == "blocked"  # Eco is where an unreadable unit leaves a model out


def test_maximum_known_measure_only_breaks_a_tie():
    specs = {"a": model("a", "T3", "ollama", "local"), "b": _cloud("b", "T3", "gemini"),
             "c": model("c", "T4", "ollama", "local")}
    router, request = _router(specs)
    assert router.select(request, concurrency_mode="maximum").selected_model == "a"  # T3 tie: local no charge first
    assert router.select(request, concurrency_mode="maximum", allow_t4=True).selected_model == "c"


def test_normal_and_performance_change_nothing():
    specs = {"t1": model("t1", "T1"), "t3": model("t3", "T3")}
    router, request = _router(specs)
    base = router.select(request)
    for mode in ("normal", "performance"):
        decision = router.select(request, concurrency_mode=mode, allow_t4=True,
                                 confirmed_levels={"t1": {"level": "T4"}})
        assert decision == base


def test_confirmed_levels_unblock_only_complete_confirmations():
    specs = {"u": model("u", None)}
    router, request = _router(specs)
    blocked = router.select(request, concurrency_mode="eco")
    assert blocked.status == "blocked" and "model_unclassified" in blocked.reason_codes
    partial = {"u": {"level": "T1", "source": "card"}}
    assert router.select(request, concurrency_mode="eco", confirmed_levels=partial).status == "blocked"
    full = {"u": {"level": "T1", "source": "card", "confirmed_at": "2026-11-25T00:00:00+00:00",
                  "confirmed_by": "user:gui"}}
    chosen = router.select(request, concurrency_mode="eco", confirmed_levels=full)
    assert chosen.status == "selected" and chosen.selected_model == "u" and chosen.selected_level == "T1"
    # A confirmation is not read outside Eco and Maximum.
    assert router.select(request, confirmed_levels=full).status == "selected"


def test_eco_ceiling_never_goes_below_the_profile_floor():
    notes = []
    profile = {"mode": "supervised", "levels": {"work": {"min": "T3"}}}
    effective = effective_profile(profile, "eco", notes=notes)
    assert effective["levels"]["work"] == {"min": "T3", "max": "T3"}
    assert notes == ["eco_ceiling_raised_to_profile_floor"]
    specs = {"t2": model("t2", "T2"), "t3": model("t3", "T3")}
    router, request = _router(specs, levels={"work": {"min": "T3"}})
    decision = router.select(request, concurrency_mode="eco")
    assert decision.status == "selected" and decision.selected_model == "t3"
    assert "eco_ceiling_raised_to_profile_floor" in decision.reason_codes
    assert decision.provenance["mode_adjustments"] == ["eco_ceiling_raised_to_profile_floor"]
    capped = effective_profile({"mode": "supervised", "levels": {"planning": {"max": "T2"}}}, "eco")
    assert capped["levels"]["planning"] == {"max": "T2", "min": "T2"}


def test_maximum_blocks_when_the_profile_needs_t4_and_it_is_not_allowed():
    specs = {"t3": model("t3", "T3"), "t4": model("t4", "T4")}
    router, request = _router(specs, levels={"work": {"min": "T4"}})
    blocked = router.select(request, concurrency_mode="maximum")
    assert blocked.status == "blocked" and blocked.reason_codes == ("t4_not_allowed",)
    assert router.select(request, concurrency_mode="maximum", allow_t4=True).selected_model == "t4"


def test_single_profile_ignores_mode_levels():
    specs = {"only": model("only", None)}
    profile = {"enabled": True, "mode": "single", "model_id": "only", "required_capabilities": [],
               "allowed_permissions": [], "allowed_data_classes": ["non_sensitive"]}
    catalog = SimpleNamespace(routing_profiles={"test": profile}, models=specs, model=lambda name: specs[name])
    request = RoutingRequest(profile_id="test", step_kind="work", execution_mode="auto",
                             data_class="non_sensitive", authorized_data_classes=frozenset({"non_sensitive"}),
                             effective_permissions=frozenset())
    router = ModelRouter(catalog, {})
    today = router.select(request)
    assert today.status == "selected"
    for mode in ("eco", "maximum"):
        assert router.select(request, concurrency_mode=mode).selected_model == today.selected_model
    assert effective_profile(profile, "eco") == profile


def test_ollama_tags_failures_give_an_empty_list():
    class Boom:
        def get(self, path):
            raise OSError("down")

    class BadJson:
        def get(self, path):
            return SimpleNamespace(json=lambda: (_ for _ in ()).throw(ValueError("not json")))

    class Shapes:
        def __init__(self, payload):
            self.payload = payload

        def get(self, path):
            return SimpleNamespace(json=lambda: self.payload)

    for client in (Boom(), BadJson(), Shapes([]), Shapes({"models": "x"}), Shapes(None)):
        adapter = OllamaAdapter.__new__(OllamaAdapter)
        adapter._client = client
        assert adapter.installed_model_details() == []
