"""ECO-T05: the economic routing profile (mode, levels, effort, escalation) and its selection.

Covers the profile schema and its validation at catalog load, selection per step kind with level
bounds and price evidence, the exclusion of unclassified or unpriced models, privacy rules that stay
as they are, single mode, the profile effort that fills a step's missing effort, the decision record,
and that a disabled profile (the bundled one) changes nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from multiagent.bootstrap import build_system
from multiagent.catalog import Catalog, CatalogError, validate_pricing
from multiagent.config import Settings
from multiagent.contracts import RunStatus, SocialPostRequest
from multiagent.model_router import ModelRouter
from multiagent.routing_policy import RoutingEvidence, RoutingRequest

ROOT = Path(__file__).resolve().parents[1]


# --- fixtures and helpers ---------------------------------------------------------------------

def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local")


def side(amount, **overrides):
    values = {
        "amount": amount, "denominator": 1_000_000, "unit": "token", "currency": "USD",
        "source": "unit-test rate card", "provenance": "catalog_declared",
        "valid_from": "2025-01-01T00:00:00Z", "valid_until": "2035-01-01T00:00:00Z",
    }
    values.update(overrides)
    return values


def price(amount, **overrides):
    """Input at ``amount`` and output at twice that, so a cheaper model is cheaper on both sides."""
    return {"input": side(amount, **overrides), "output": side(amount * 2, **overrides)}


def model(level=None, amount=None, *, classified=True, tier="local", classes=("non_sensitive",),
          permissions=(), **overrides):
    values = {
        "provider": "ollama" if tier == "local" else "gemini", "model": "m", "tier": tier,
        "structured_output": True, "web": False, "capabilities": ["text_generation"],
        "roles": ["assistant"], "permissions": list(permissions), "permissions_required": [],
        "allowed_data_classes": list(classes),
    }
    if tier == "cloud":
        values["permissions"] = sorted({"provider:cloud", *permissions})
        values["permissions_required"] = ["provider:cloud"]
    if level is not None:
        values["level"] = level
        if classified:
            values["classification"] = "classified"
    if amount is not None:
        values["pricing"] = price(amount)
    values.update(overrides)
    return values


def economic(**overrides):
    values = {
        "version": 1, "enabled": True, "mode": "supervised",
        "levels": {"orchestration": {"min": "T3"}, "planning": {"min": "T3"}, "review": {"min": "T3"},
                   "work": {"max": "T2"}},
        "economic_selection": True,
        "required_capabilities": ["structured_output"], "required_roles": [], "required_permissions": [],
        "allowed_permissions": [], "allowed_data_classes": ["non_sensitive"],
        "execution_modes": ["auto", "local", "cloud"], "require_health": False,
        "max_retries_per_candidate": 0,
    }
    values.update(overrides)
    return values


def standard_models():
    return {
        "t1": model("T1", 0, pricing={"input": side(0, provenance="local_no_charge", source="local, no charge"),
                                      "output": side(0, provenance="local_no_charge", source="local, no charge")}),
        "t2": model("T2", 0.2),
        "t2_dear": model("T2", 0.4),
        "t3": model("T3", 0.6),
        "t3_unclassified_cheap": model("T3", 0.1, classified=False),
        "t3_unpriced": model("T3"),
        "t4": model("T4", 3.0),
    }


def load(tmp_path: Path, models=None, profile=None, *, profiles=None, assignments=None) -> Catalog:
    folder = tmp_path / "catalog"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    declared = profiles if profiles is not None else {"eco": profile if profile is not None else economic()}
    document = {
        "models": models if models is not None else standard_models(),
        "routing_profiles": {"profiles": declared, "assignments": assignments or {}},
    }
    (folder / "models.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return Catalog(settings(tmp_path), catalog_dir=folder)


def request(kind="work", **overrides):
    values = {
        "profile_id": "eco", "workflow_id": "wf", "step_id": "s", "step_kind": kind,
        "required_capabilities": frozenset({"structured_output"}), "required_permissions": frozenset(),
        "effective_permissions": frozenset(), "authorized_data_classes": frozenset({"non_sensitive"}),
        "authorization_provenance": "unit-test", "data_class": "non_sensitive", "execution_mode": "auto",
        "output_schema": "Payload",
    }
    values.update(overrides)
    return RoutingRequest(**values)


def select(catalog: Catalog, kind="work", **overrides):
    return ModelRouter(catalog, adapters={}).select(request(kind, **overrides), RoutingEvidence(scope="run-1"))


def rejected(decision):
    return {candidate.model_id: candidate.reason_codes for candidate in decision.candidates if not candidate.eligible}


# --- schema and validation --------------------------------------------------------------------

def test_profile_fields_are_validated_stored_and_normalized(tmp_path):
    profile = economic(effort={"work": "low", "review": "medium"},
                       escalation={"max_escalations_per_step": 1, "ceiling": "T3"})
    stored = load(tmp_path, profile=profile).routing_profiles["eco"]
    assert stored["mode"] == "supervised"
    assert stored["levels"] == {"orchestration": {"min": "T3"}, "planning": {"min": "T3"},
                                "review": {"min": "T3"}, "work": {"max": "T2"}}
    assert stored["effort"] == {"work": "low", "review": "medium"}
    assert stored["escalation"] == {"max_escalations_per_step": 1, "ceiling": "T3"}


def test_mode_defaults_to_supervised_and_escalation_defaults_come_from_the_contract(tmp_path):
    profile = economic()
    del profile["mode"]
    profile["escalation"] = {}
    stored = load(tmp_path, profile=profile).routing_profiles["eco"]
    assert stored["mode"] == "supervised"
    assert stored["escalation"] == {"max_escalations_per_step": 1, "ceiling": "T3"}


def test_a_profile_without_economic_fields_is_left_untouched(tmp_path):
    legacy = {"version": 1, "enabled": False, "candidate_model_ids": ["t1"], "max_candidates": 1}
    catalog = load(tmp_path, profiles={"legacy": legacy})
    assert catalog.routing_profiles["legacy"] == legacy
    assert "mode" not in catalog.routing_profiles["legacy"]


def test_both_bounds_and_every_mode_are_accepted(tmp_path):
    levels = {"work": {"min": "T1", "max": "T2"}}
    load(tmp_path, profile=economic(mode="automatic", levels=levels))
    load(tmp_path, profile=economic(mode="single", model_id="t2"))


@pytest.mark.parametrize("change, message", [
    ({"mode": "manual"}, r"Routing profile 'eco': mode must be one of supervised, automatic, single"),
    ({"mode": "single"}, r"mode 'single' requires model_id"),
    ({"mode": "single", "model_id": "nope"}, r"model_id 'nope' is not a model in the catalog"),
    ({"mode": "single", "model_id": "t2", "candidate_model_ids": ["t1"]}, r"model_id 't2' is not in candidate_model_ids"),
    ({"model_id": ""}, r"model_id must be a non-empty"),
    ({"levels": {}}, r"levels must be a mapping"),
    ({"levels": {"coding": {"min": "T1"}}}, r"unknown step kind 'coding'"),
    ({"levels": {"work": {"min": "T5"}}}, r"levels.work.min must be one of T1, T2, T3, T4"),
    ({"levels": {"work": {"top": "T2"}}}, r"levels.work has unknown keys"),
    ({"levels": {"work": {}}}, r"levels.work must be a mapping"),
    ({"levels": {"work": {"min": "T3", "max": "T1"}}}, r"min T3 is above max T1"),
    ({"effort": {"work": "extreme"}}, r"effort.work must be one of none, low, medium, high, max"),
    ({"effort": {"coding": "low"}}, r"effort has unknown step kind 'coding'"),
    ({"escalation": {"ceiling": "T4"}}, r"ceiling must be one of T1, T2, T3 \(T4 is never automatic\)"),
    ({"escalation": {"max_escalations_per_step": 2}}, r"max_escalations_per_step must be 0 or 1"),
    ({"escalation": {"retries": 1}}, r"escalation has unknown keys"),
    ({"candidate_model_ids": []}, r"candidate_model_ids must be a non-empty list"),
])
def test_invalid_economic_declarations_are_rejected_at_load_with_the_profile_id(tmp_path, change, message):
    with pytest.raises(CatalogError, match=message):
        load(tmp_path, profile=economic(**change))


def test_invalid_profile_set_after_load_blocks_at_selection_with_the_error(tmp_path):
    catalog = load(tmp_path)
    catalog.routing_profiles["eco"]["levels"]["work"] = {"max": "T9"}
    decision = select(catalog)
    assert decision.status == "blocked" and decision.reason_codes == ("economic_profile_invalid",)
    assert "levels.work.max" in decision.provenance["profile_error"]


def test_escalation_is_only_stored_and_never_changes_selection(tmp_path):
    plain = select(load(tmp_path / "a", profile=economic()), "orchestration")
    declared = select(load(tmp_path / "b", profile=economic(
        escalation={"max_escalations_per_step": 0, "ceiling": "T1"})), "orchestration")
    assert plain.selected_model == declared.selected_model == "t3"
    assert "escalation" not in plain.to_dict() and "escalation" not in declared.to_dict()


# --- selection per step kind ------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["orchestration", "planning", "review"])
def test_orchestration_planning_and_review_take_the_cheapest_at_or_above_the_minimum(tmp_path, kind):
    decision = select(load(tmp_path), kind)
    assert decision.status == "selected"
    assert decision.selected_model == "t3"            # cheaper than t4; t2 and t1 are below T3
    assert decision.selected_level == "T3" and decision.step_kind == kind
    assert decision.fallback_models == ("t4",)
    assert rejected(decision)["t2"] == ("level_below_minimum",)
    assert rejected(decision)["t1"] == ("level_below_minimum",)


def test_a_cheaper_model_above_the_minimum_wins_even_when_it_is_t4(tmp_path):
    models = standard_models()
    models["t4"] = model("T4", 0.3)
    decision = select(load(tmp_path, models), "planning")
    assert decision.selected_model == "t4" and decision.selected_level == "T4"


def test_work_takes_the_cheapest_at_or_below_the_maximum(tmp_path):
    decision = select(load(tmp_path), "work")
    assert decision.selected_model == "t1" and decision.selected_level == "T1"
    assert decision.fallback_models == ("t2",)
    assert rejected(decision)["t3"] == ("level_above_maximum",)
    assert rejected(decision)["t4"] == ("level_above_maximum",)


def test_work_never_picks_a_model_above_the_maximum_even_when_it_is_the_only_priced_one(tmp_path):
    models = {"t2_unpriced": model("T2"), "t3": model("T3", 0.01)}
    decision = select(load(tmp_path, models), "work")
    assert decision.status == "blocked"
    assert "no_eligible_candidate" in decision.reason_codes
    assert rejected(decision)["t3"] == ("level_above_maximum",)


def test_the_default_step_kind_is_work(tmp_path):
    catalog = load(tmp_path)
    plain = ModelRouter(catalog, adapters={}).select(RoutingRequest(
        profile_id="eco", required_capabilities=frozenset({"structured_output"}), data_class="non_sensitive",
        authorized_data_classes=frozenset({"non_sensitive"}), effective_permissions=frozenset()))
    assert plain.step_kind == "work" and plain.selected_model == "t1"


def test_ranking_uses_token_estimates_when_both_are_given(tmp_path):
    models = {"cheap_in": model("T3", 0.1, pricing={"input": side(0.1), "output": side(5.0)}),
              "cheap_out": model("T3", 0.1, pricing={"input": side(0.5), "output": side(0.2)})}
    heavy_input = select(load(tmp_path, models), "review", estimated_input_tokens=10_000, estimated_output_tokens=10)
    heavy_output = select(load(tmp_path / "x", models), "review", estimated_input_tokens=10, estimated_output_tokens=10_000)
    assert heavy_input.selected_model == "cheap_in"
    assert heavy_output.selected_model == "cheap_out"
    assert heavy_input.price_evidence["ranking"] == "estimated_tokens"


def test_without_common_quantities_or_dominance_the_choice_is_blocked_not_guessed(tmp_path):
    models = {"cheap_in": model("T3", pricing={"input": side(0.1), "output": side(5.0)}),
              "cheap_out": model("T3", pricing={"input": side(0.5), "output": side(0.2)})}
    decision = select(load(tmp_path, models), "review")
    assert decision.status == "blocked"
    assert decision.reason_codes == ("common_token_quantities_or_price_dominance_missing",)


def test_economic_selection_off_keeps_the_configured_order_for_work_only(tmp_path):
    models = {"first_dear": model("T2", 0.4), "second_cheap": model("T2", 0.1), "t3": model("T3", 0.6),
              "t3_cheaper": model("T3", 0.5)}
    catalog = load(tmp_path, models, economic(economic_selection=False))
    work = select(catalog, "work")
    orchestration = select(catalog, "orchestration")
    assert work.selected_model == "first_dear" and work.reason_codes == ("first_eligible_configured_candidate_after_planning_filters",)
    assert orchestration.selected_model == "t3_cheaper"


def test_candidate_allowlist_limits_the_pool_and_defaults_to_the_whole_catalog(tmp_path):
    allowlisted = load(tmp_path, profile=economic(candidate_model_ids=["t2", "t2_dear"]))
    decision = select(allowlisted, "work")
    assert decision.selected_model == "t2"
    assert {candidate.model_id for candidate in decision.candidates} == {"t2", "t2_dear"}
    assert {candidate.model_id for candidate in select(load(tmp_path / "all"), "work").candidates} == set(standard_models())


def test_a_step_kind_without_declared_levels_is_blocked(tmp_path):
    catalog = load(tmp_path, profile=economic(levels={"work": {"max": "T2"}}))
    decision = select(catalog, "review")
    assert decision.status == "blocked" and decision.reason_codes == ("step_kind_levels_missing",)
    assert select(catalog, "work").status == "selected"


# --- unclassified and unpriced models ---------------------------------------------------------

def test_unclassified_models_never_take_part_even_when_cheaper_and_priced(tmp_path):
    decision = select(load(tmp_path), "orchestration")
    assert decision.selected_model == "t3"            # t3_unclassified_cheap costs less but is not classified
    assert rejected(decision)["t3_unclassified_cheap"] == ("model_unclassified",)


def test_a_level_without_classification_is_unclassified(tmp_path):
    models = {"only": model("T3", 0.1, classified=False)}
    decision = select(load(tmp_path, models), "review")
    assert decision.status == "blocked"
    assert decision.reason_codes == ("no_eligible_candidate", "model_unclassified")


def test_models_without_a_level_are_unclassified(tmp_path):
    decision = select(load(tmp_path, {"plain": model(None, 0.1)}), "work")
    assert decision.status == "blocked" and rejected(decision)["plain"] == ("model_unclassified",)


def test_a_model_without_current_price_evidence_is_excluded_not_treated_as_free(tmp_path):
    decision = select(load(tmp_path), "orchestration")
    assert rejected(decision)["t3_unpriced"] == ("price_evidence_missing_or_expired",)


def test_no_pricing_at_all_blocks_with_a_clear_reason_and_never_falls_back(tmp_path):
    models = {"a": model("T3"), "b": model("T4")}
    decision = select(load(tmp_path, models), "review")
    assert decision.status == "blocked" and decision.manual_required is True
    assert decision.selected_model is None and decision.fallback_models == ()
    assert decision.reason_codes == ("no_eligible_candidate", "price_evidence_missing_or_expired")
    assert rejected(decision) == {"a": ("price_evidence_missing_or_expired",), "b": ("price_evidence_missing_or_expired",)}


def test_expired_or_future_prices_count_as_no_price(tmp_path):
    expired = price(0.2, valid_from="2020-01-01T00:00:00Z", valid_until="2021-01-01T00:00:00Z")
    future = price(0.2, valid_from="2034-01-01T00:00:00Z", valid_until="2035-01-01T00:00:00Z")
    models = {"expired": model("T3", pricing=expired), "future": model("T3", pricing=future)}
    decision = select(load(tmp_path, models), "review")
    assert decision.status == "blocked"
    assert set(rejected(decision)) == {"expired", "future"}


def test_no_candidate_within_the_level_bounds_is_blocked_with_the_reasons_seen(tmp_path):
    models = {"t1": standard_models()["t1"], "t2": standard_models()["t2"]}
    decision = select(load(tmp_path, models), "orchestration")
    assert decision.status == "blocked"
    assert decision.reason_codes == ("no_eligible_candidate", "level_below_minimum")


def test_a_blocked_economic_profile_is_never_replaced_by_the_legacy_selection(tmp_path):
    # The same pool under a plain profile (no levels) selects something; the economic one must not.
    models = {"only": model("T1", 0.1)}
    profiles = {"eco": economic(levels={"orchestration": {"min": "T3"}}),
                "plain": {"version": 1, "enabled": True, "candidate_model_ids": ["only"], "max_candidates": 1,
                          "required_capabilities": ["structured_output"], "allowed_permissions": [],
                          "allowed_data_classes": ["non_sensitive"], "execution_modes": ["auto"]}}
    catalog = load(tmp_path, models, profiles=profiles)
    assert select(catalog, "orchestration").status == "blocked"
    assert select(catalog, "orchestration", profile_id="plain").status == "selected"


# --- privacy, permissions and the rest of the existing rules ----------------------------------

def sensitive_profile(**overrides):
    values = {"allowed_data_classes": ["non_sensitive", "sensitive"],
              "allowed_permissions": ["data:sensitive", "provider:cloud"]}
    values.update(overrides)
    return economic(**values)


def sensitive_request(**overrides):
    values = {"data_class": "sensitive", "authorized_data_classes": frozenset({"sensitive", "non_sensitive"}),
              "effective_permissions": frozenset({"data:sensitive", "provider:cloud"})}
    values.update(overrides)
    return values


def test_sensitive_data_still_goes_only_to_a_local_model_that_allows_it(tmp_path):
    models = {
        "cloud_cheap": model("T3", 0.05, tier="cloud", classes=("non_sensitive",)),
        "local_sensitive": model("T3", 0.9, classes=("non_sensitive", "sensitive"), permissions=("data:sensitive",)),
    }
    catalog = load(tmp_path, models, sensitive_profile())
    decision = select(catalog, "review", **sensitive_request())
    assert decision.status == "selected" and decision.selected_model == "local_sensitive"
    assert "candidate_data_class_unknown_or_disallowed" in rejected(decision)["cloud_cheap"]
    # The cheaper cloud model is still chosen for non-sensitive data of the same step kind.
    open_data = select(catalog, "review", effective_permissions=frozenset({"provider:cloud"}))
    assert open_data.selected_model == "cloud_cheap"


def test_sensitive_data_without_a_suitable_local_model_is_blocked(tmp_path):
    models = {"cloud_cheap": model("T3", 0.05, tier="cloud", classes=("non_sensitive",))}
    decision = select(load(tmp_path, models, sensitive_profile()), "review", **sensitive_request())
    assert decision.status == "blocked"
    assert "candidate_data_class_unknown_or_disallowed" in rejected(decision)["cloud_cheap"]


def test_sensitive_data_that_was_not_authorized_is_blocked_before_any_economics(tmp_path):
    models = {"local": model("T3", 0.9, classes=("non_sensitive", "sensitive"), permissions=("data:sensitive",))}
    decision = select(load(tmp_path, models, sensitive_profile()), "review",
                      **sensitive_request(authorized_data_classes=frozenset({"non_sensitive"})))
    assert decision.status == "blocked" and decision.reason_codes == ("sensitive_class_not_authorized",)


def test_cloud_models_still_need_grants_and_the_profile_permission_ceiling(tmp_path):
    models = {"cloud": model("T3", 0.05, tier="cloud")}
    no_grant = select(load(tmp_path / "a", models), "review", effective_permissions=frozenset())
    ceiling_empty = select(load(tmp_path / "b", models), "review", effective_permissions=frozenset({"provider:cloud"}))
    allowed = select(load(tmp_path / "c", models, economic(allowed_permissions=["provider:cloud"])), "review",
                     effective_permissions=frozenset({"provider:cloud"}))
    assert "effective_permission_not_granted" in rejected(no_grant)["cloud"]
    assert "permission_exceeds_profile_ceiling" in rejected(ceiling_empty)["cloud"]
    assert allowed.status == "selected" and allowed.selected_model == "cloud"


def test_required_capability_and_role_still_filter_candidates(tmp_path):
    models = {"a": model("T3", 0.1), "b": model("T3", 0.9, roles=["reviewer"])}
    decision = select(load(tmp_path, models), "review", required_role="reviewer")
    assert decision.selected_model == "b"
    assert "required_role_missing_or_unknown" in rejected(decision)["a"]


# --- single mode ------------------------------------------------------------------------------

def test_single_mode_uses_only_its_model_whatever_the_kind_level_or_price(tmp_path):
    # unclassified, unpriced and at a level the bounds would reject: single mode does not look at any of it
    models = {**standard_models(), "pinned": model("T4", classified=False)}
    catalog = load(tmp_path, models, economic(mode="single", model_id="pinned"))
    for kind in ("orchestration", "planning", "review", "work"):
        decision = select(catalog, kind)
        assert decision.status == "selected" and decision.selected_model == "pinned"
        assert decision.fallback_models == ()
        assert decision.profile_mode == "single" and decision.step_kind == kind
        assert decision.reason_codes == ("single_model_profile",)
        assert [candidate.model_id for candidate in decision.candidates] == ["pinned"]
    assert decision.price_evidence is None


def test_single_mode_records_the_price_it_has_but_does_not_require_or_rank_it(tmp_path):
    catalog = load(tmp_path, profile=economic(mode="single", model_id="t4"))
    decision = select(catalog, "work", estimated_input_tokens=1000, estimated_output_tokens=100)
    assert decision.selected_model == "t4"
    assert decision.price_evidence["ranking"] == "not_ranked"
    assert decision.price_evidence["input"]["source"] == "unit-test rate card"


def test_single_mode_keeps_permissions_privacy_and_the_unknown_model_check(tmp_path):
    models = {"cloud": model("T3", 0.1, tier="cloud")}
    catalog = load(tmp_path, models, economic(mode="single", model_id="cloud"))
    assert select(catalog, "review", effective_permissions=frozenset()).status == "blocked"
    assert select(catalog, "review", effective_permissions=frozenset()).reason_codes == ("no_eligible_candidate",)
    catalog.models.pop("cloud")
    gone = select(catalog, "review")
    assert gone.status == "blocked" and rejected(gone) == {"cloud": ("unknown_model",)}


def test_single_mode_ignores_levels_and_escalation_but_keeps_them_stored(tmp_path):
    catalog = load(tmp_path, profile=economic(mode="single", model_id="t2",
                                              escalation={"max_escalations_per_step": 1, "ceiling": "T3"}))
    stored = catalog.routing_profiles["eco"]
    assert stored["levels"]["work"] == {"max": "T2"} and stored["escalation"]["ceiling"] == "T3"
    assert select(catalog, "orchestration").selected_model == "t2"     # T2 is below the orchestration minimum


# --- effort -----------------------------------------------------------------------------------

def test_profile_effort_fills_the_effort_only_when_the_step_has_none(tmp_path):
    catalog = load(tmp_path, profile=economic(effort={"work": "low", "review": "medium"}))
    filled = select(catalog, "work")
    own = select(catalog, "work", effort="high")
    none_for_kind = select(catalog, "orchestration")
    assert (filled.effort, filled.effort_source) == ("low", "profile")
    assert (own.effort, own.effort_source) == ("high", "step")
    assert (none_for_kind.effort, none_for_kind.effort_source) == (None, None)
    assert select(catalog, "review").effort == "medium"


def test_a_step_effort_of_none_level_is_kept_over_the_profile(tmp_path):
    catalog = load(tmp_path, profile=economic(effort={"work": "high"}))
    decision = select(catalog, "work", effort="none")
    assert (decision.effort, decision.effort_source) == ("none", "step")


def test_effort_is_reported_even_when_the_selection_is_blocked(tmp_path):
    catalog = load(tmp_path, {"t1": standard_models()["t1"]}, economic(effort={"review": "medium"}))
    blocked = select(catalog, "review")
    assert blocked.status == "blocked" and blocked.effort == "medium" and blocked.effort_source == "profile"


def _engine(tmp_path, *, profile, step_changes=None):
    system = build_system(Settings(
        data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db", runs_dir=tmp_path / "data" / "runs",
        assets_dir=tmp_path / "data" / "assets", worker_enabled=False, gemini_api_key=""), dry_run=True)
    directory = tmp_path / "custom_workflows"
    directory.mkdir()
    document = yaml.safe_load((Path(system.engine.workflows.workflows_dir) / "social_post.yaml").read_text(encoding="utf-8"))
    document["steps"][1].update(step_changes or {})
    (directory / "social_post.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    system.engine.workflows.workflows_dir = directory
    system.catalog.routing_assignments["social_post"] = {"create": "eco"}
    system.catalog.routing_profiles["eco"] = profile
    return system


def _run(system):
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="eco-t05", objective="Create an educational post", topic="KNX"))
    system.worker.process_once()
    return system.run_service.get(run["id"])


def _events(state, name):
    return [event["payload"] for event in state["events"] if event["event_type"] == name]


def test_engine_applies_the_profile_effort_and_records_the_decision(tmp_path):
    profile = economic(levels={"work": {"max": "T2"}}, effort={"work": "low"}, candidate_model_ids=["local_default"])
    state = _run(_engine(tmp_path, profile=profile))
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    decision = _events(state, "routing_decision")[0]
    assert decision["status"] == "selected" and decision["selected_model"] == "local_default"
    assert (decision["step_kind"], decision["selected_level"], decision["effort"], decision["effort_source"]) == (
        "work", "T1", "low", "profile")
    assert decision["price_evidence"]["input"]["provenance"] == "local_no_charge"
    assert decision["price_evidence"]["current"] is True
    assert _events(state, "step_started")[0]["effort"] == "low"
    attempt = _events(state, "model_attempt")[-1]
    assert attempt["effort_requested"] == "low" and attempt["effort_applied"] is True


def test_engine_keeps_the_step_effort_over_the_profile_one(tmp_path):
    profile = economic(levels={"work": {"max": "T2"}}, effort={"work": "low"}, candidate_model_ids=["local_default"])
    state = _run(_engine(tmp_path, profile=profile, step_changes={"effort": "high"}))
    assert _events(state, "routing_decision")[0]["effort_source"] == "step"
    assert _events(state, "step_started")[0]["effort"] == "high"
    assert _events(state, "model_attempt")[-1]["effort_requested"] == "high"


def test_engine_stops_with_the_reason_when_the_economic_profile_has_no_candidate(tmp_path):
    profile = economic(levels={"work": {"min": "T3"}}, candidate_model_ids=["local_default"])
    state = _run(_engine(tmp_path, profile=profile))
    assert state["status"] == RunStatus.FAILED.value
    assert "level_below_minimum" in state["error"] and "no_eligible_candidate" in state["error"]
    assert not _events(state, "model_attempt")


# --- decision record --------------------------------------------------------------------------

def test_decision_records_kind_level_price_evidence_and_rejected_candidates(tmp_path):
    decision = select(load(tmp_path), "orchestration", estimated_input_tokens=1000, estimated_output_tokens=200)
    data = decision.to_dict()
    json.dumps(data)                                              # the whole record is serializable
    assert (data["step_kind"], data["profile_mode"], data["selected_level"]) == ("orchestration", "supervised", "T3")
    evidence = data["price_evidence"]
    assert evidence["model_id"] == "t3" and evidence["ranking"] == "estimated_tokens" and evidence["current"] is True
    assert evidence["input"]["source"] == "unit-test rate card"
    assert evidence["input"]["valid_from"] == "2025-01-01T00:00:00Z"
    assert evidence["input"]["valid_until"] == "2035-01-01T00:00:00Z"
    assert evidence["input"]["provenance"] == "catalog_declared"
    assert evidence["estimated_cost"] == pytest.approx(0.6 * 1000 / 1e6 + 1.2 * 200 / 1e6)
    assert evidence["estimated_cost_currency"] == "USD"
    by_id = {candidate["model_id"]: candidate for candidate in data["candidates"]}
    assert by_id["t2"]["eligible"] is False and by_id["t2"]["reason_codes"] == ["level_below_minimum"]
    assert by_id["t2"]["level"] == "T2" and by_id["t2"]["classification"] == "classified"
    assert by_id["t3_unclassified_cheap"]["classification"] == "unclassified"
    assert by_id["t3"]["eligible"] is True and by_id["t3"]["level"] == "T3"


def test_unknown_cost_is_null_not_zero_when_tokens_are_not_estimated(tmp_path):
    decision = select(load(tmp_path), "orchestration")
    assert decision.price_evidence["estimated_cost"] is None and decision.price_evidence["estimated_cost_currency"] is None
    assert decision.price_evidence["ranking"] == "dominant_rates"


def test_a_single_eligible_candidate_needs_no_comparison(tmp_path):
    decision = select(load(tmp_path, {"only": model("T3", 0.2)}), "review")
    assert decision.selected_model == "only" and decision.price_evidence["ranking"] == "single_candidate"


# --- price units ------------------------------------------------------------------------------

def test_token_and_tokens_are_the_same_unit_when_comparing_prices(tmp_path):
    models = {"singular": model("T3", pricing={"input": side(0.8, unit="token"), "output": side(1.6, unit="token")}),
              "plural": model("T3", pricing={"input": side(0.3, unit="tokens"), "output": side(0.6, unit="tokens")})}
    decision = select(load(tmp_path, models), "review")
    assert decision.status == "selected" and decision.selected_model == "plural"
    reverse = select(load(tmp_path / "r", {"plural": models["plural"], "singular": models["singular"]}), "review",
                     estimated_input_tokens=100, estimated_output_tokens=100)
    assert reverse.selected_model == "plural"


def test_the_unit_comparison_ignores_case_and_surrounding_space(tmp_path):
    models = {"a": model("T3", pricing={"input": side(0.8, unit=" Token "), "output": side(1.6, unit=" Token ")}),
              "b": model("T3", pricing={"input": side(0.3, unit="TOKENS"), "output": side(0.6, unit="TOKENS")})}
    assert select(load(tmp_path, models), "review").selected_model == "b"


def test_input_and_output_prices_may_mix_token_and_tokens(tmp_path):
    pricing = {"input": side(0.3, unit="token"), "output": side(0.6, unit="tokens")}
    assert validate_pricing("m", pricing)["output"]["unit"] == "tokens"
    assert select(load(tmp_path, {"mixed": model("T3", pricing=pricing)}), "review").selected_model == "mixed"


def test_genuinely_different_units_or_currencies_stay_incomparable(tmp_path):
    different_unit = {"a": model("T3", 0.3), "b": model("T3", pricing={"input": side(0.3, unit="character"),
                                                                      "output": side(0.6, unit="character")})}
    different_currency = {"a": model("T3", 0.3), "b": model("T3", pricing={"input": side(0.3, currency="EUR"),
                                                                          "output": side(0.6, currency="EUR")})}
    assert select(load(tmp_path / "u", different_unit), "review").reason_codes == ("price_units_or_currency_incomparable",)
    assert select(load(tmp_path / "c", different_currency), "review").reason_codes == ("price_units_or_currency_incomparable",)


# --- disabled profile and the bundled one -----------------------------------------------------

def test_a_disabled_profile_is_blocked_without_looking_at_any_candidate(tmp_path):
    decision = select(load(tmp_path, profile=economic(enabled=False)), "work")
    assert decision.status == "blocked" and decision.reason_codes == ("profile_disabled",)
    assert decision.candidates == () and decision.selected_model is None


def test_bundled_catalog_has_a_disabled_unassigned_economic_profile_with_the_contract_values():
    catalog = Catalog(Settings(_env_file=None))
    profile = catalog.routing_profiles["economic"]
    assert profile["enabled"] is False and profile["mode"] == "supervised"
    assert profile["levels"] == {"orchestration": {"min": "T3"}, "planning": {"min": "T3"},
                                 "review": {"min": "T3"}, "work": {"max": "T2"}}
    assert profile["effort"] == {"work": "low", "review": "medium"}
    assert profile["economic_selection"] is True
    assert profile["escalation"] == {"max_escalations_per_step": 1, "ceiling": "T3"}
    assert "economic" not in {value for steps in catalog.routing_assignments.values() for value in steps.values()}
    assert catalog.routing_assignments == {}
    assert "mode" not in catalog.routing_profiles["local_non_sensitive_supervised"]


def test_bundled_economic_profile_is_inert_until_enabled_and_then_uses_only_classified_models():
    catalog = Catalog(Settings(_env_file=None))
    router = ModelRouter(catalog, adapters={})
    base = dict(profile_id="economic", required_capabilities=frozenset({"structured_output"}),
                data_class="non_sensitive", authorized_data_classes=frozenset({"non_sensitive"}),
                effective_permissions=frozenset(), authorization_provenance="unit-test")
    assert router.select(RoutingRequest(step_kind="work", **base)).reason_codes == ("profile_disabled",)

    catalog.routing_profiles["economic"]["enabled"] = True
    work = router.select(RoutingRequest(step_kind="work", **base))
    assert work.status == "selected" and work.selected_model in {"local_default", "local_reasoning"}
    assert work.selected_level == "T1" and work.effort == "low"
    assert {name for name, codes in rejected(work).items() if "model_unclassified" in codes} == {
        "cloud_fast", "cloud_advanced", "gemini_grounded", "gemini_fast", "openai_optional"}
    # Nothing in the bundled catalog is T3 yet, so the orchestration kinds block instead of using a weaker model.
    review = router.select(RoutingRequest(step_kind="review", **base))
    assert review.status == "blocked" and "level_below_minimum" in review.reason_codes
    assert "model_unclassified" in review.reason_codes


def test_removing_the_assignment_restores_the_legacy_route(tmp_path):
    # With the economic profile defined but no assignment, a run keeps its static binding (no profile decision).
    system = _engine(tmp_path, profile=economic(levels={"work": {"max": "T2"}}), step_changes={"effort": "low"})
    system.catalog.routing_assignments.clear()
    state = _run(system)
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    decision = _events(state, "routing_decision")[0]
    assert "profile_id" not in decision or decision.get("profile_id") is None
    assert decision["preferred_model"] == "local_default"


def test_legacy_profile_selection_is_unchanged_by_the_new_fields(tmp_path):
    models = {"cheap": model("T1", 0.1), "dear": model("T1", 0.9)}
    legacy = {"version": 1, "enabled": True, "candidate_model_ids": ["dear", "cheap"], "max_candidates": 2,
              "required_capabilities": ["structured_output"], "allowed_permissions": [],
              "allowed_data_classes": ["non_sensitive"], "execution_modes": ["auto"], "economic_selection": True}
    decision = select(load(tmp_path, models, profiles={"eco": legacy}), "orchestration")
    assert decision.selected_model == "cheap"                     # step kind and levels play no part without `levels`
    assert decision.profile_mode is None and decision.reason_codes == (
        "lowest_estimated_cost_for_common_token_quantities_or_dominant_rates",)
