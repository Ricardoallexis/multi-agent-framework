"""ECO-T06: bounded escalation and the economic modes.

A step of an economic routing profile that exhausts its retries on the quality of its output
(StructuredOutputError or a failed semantic validation) escalates once to the next model level, inside
the profile's ceiling and budgets, with the profile's normal eligibility. The tests drive the real
engine with scripted fake providers (no network) and cover each mode, the ceiling, a missing candidate,
sensitive data, exhausted budgets, provider errors, the run event and routing evidence, and the supervised
pause with its approve and reject paths.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest
import yaml

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.bootstrap import build_system
from multiagent.catalog import ModelSpec
from multiagent.config import Settings
from multiagent.contracts import ContentOutput, RunStatus, SocialPostRequest
from multiagent.errors import (
    AuthenticationError, InvalidStateTransition, ProviderUnavailable, QuotaExceeded, RateLimitExceeded,
    StructuredOutputError,
)
from multiagent.model_router import ModelRouter
from multiagent.routing_policy import RoutingEvidence, RoutingRequest

# --- scripted providers ---------------------------------------------------------------------------------


def good() -> ContentOutput:
    return ContentOutput(title="T", hook="A hook long enough for contract validation",
                         body="A body long enough to validate the structured contract correctly.",
                         cta="CTA", hashtags=["#ok"], source_urls_used=[])


def weak() -> ContentOutput:
    """Valid for the contract, rejected by the step's own validation (a social post needs hashtags)."""
    return good().model_copy(update={"hashtags": []})


class Scripted(LLMAdapter):
    """A provider whose answer per model is read from a script: ``ok``, ``bad`` (unparsable), ``semantic``
    (parses, fails validation) or an exception instance. Calling a model with an empty script is a test error."""

    def __init__(self, provider: str, scripts: dict[str, list]):
        self.provider, self.scripts, self.calls = provider, {key: list(value) for key, value in scripts.items()}, []

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
        self.calls.append(spec.model)
        queue = self.scripts.get(spec.model)
        assert queue, f"unexpected call to {spec.model}"
        action = queue.pop(0)
        if isinstance(action, Exception):
            raise action
        if action == "bad":
            raise StructuredOutputError("The model returned text that is not the requested JSON")
        parsed = weak() if action == "semantic" else good()
        return LLMResponse(parsed, parsed.model_dump_json(), self.provider, spec.model, tokens_in=1000, tokens_out=500)

    def health(self):
        return {"provider": self.provider, "available": True}


def side(amount, **overrides):
    values = {"amount": amount, "denominator": 1_000_000, "unit": "token", "currency": "USD",
              "source": "unit-test rate card", "provenance": "catalog_declared",
              "valid_from": "2025-01-01T00:00:00Z", "valid_until": "2035-01-01T00:00:00Z"}
    values.update(overrides)
    return values


def price(amount):
    """Input at ``amount`` per million and output at twice that."""
    return {"input": side(amount), "output": side(amount * 2)}


NO_CHARGE = {"input": side(0, provenance="local_no_charge", source="local, no charge"),
             "output": side(0, provenance="local_no_charge", source="local, no charge")}


def spec(model_id, level, *, cloud=False, amount=None, classes=("non_sensitive",), permissions=(), pricing=None,
         provider=None):
    permissions = sorted({*permissions, *(["provider:cloud"] if cloud else [])})
    return ModelSpec(
        id=model_id, provider=provider or ("net" if cloud else "lab"), model=model_id, tier="cloud" if cloud else "local",
        structured_output=True, web=False, capabilities=frozenset({"text_generation"}), roles=frozenset({"creator"}),
        permissions=frozenset(permissions), permissions_required=frozenset({"provider:cloud"} if cloud else ()),
        allowed_data_classes=frozenset(classes), level=level, classification="classified",
        pricing=pricing if pricing is not None else (price(amount) if amount is not None else None),
    )


def profile(mode="automatic", *, escalation=True, **overrides):
    values = {
        "version": 1, "enabled": True, "mode": mode,
        "levels": {"orchestration": {"min": "T3"}, "planning": {"min": "T3"}, "review": {"min": "T3"},
                   "work": {"max": "T1"}},
        "economic_selection": True, "required_capabilities": ["structured_output"], "required_roles": [],
        "required_permissions": [], "allowed_permissions": [], "allowed_data_classes": ["non_sensitive"],
        "execution_modes": ["auto", "local", "cloud"], "require_health": False, "max_retries_per_candidate": 0,
    }
    if escalation:
        values["escalation"] = {"max_escalations_per_step": 1, "ceiling": "T3"} if escalation is True else escalation
    values.update(overrides)
    return values


def models(**extra):
    base = {"t1": spec("t1", "T1", pricing=NO_CHARGE), "t2": spec("t2", "T2", amount=100), "t3": spec("t3", "T3", amount=300)}
    base.update(extra)
    return base


def make(tmp_path: Path, *, scripts, eco=None, catalog_models=None, checkpoint=True, step=None, max_llm_calls=None,
         net_scripts=None):
    """A system with a one-step assignment of ``create`` to the profile ``eco`` and scripted providers."""
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                        runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets",
                        worker_enabled=False, gemini_api_key="",
                        **({"max_llm_calls": max_llm_calls} if max_llm_calls else {}))
    lab, net = Scripted("lab", scripts), Scripted("net", net_scripts or {})
    system = build_system(settings, dry_run=False, adapter_overrides={"lab": lab, "net": net})
    directory = tmp_path / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    document = yaml.safe_load((Path(system.engine.workflows.workflows_dir) / "social_post.yaml").read_text(encoding="utf-8"))
    document["steps"][1].update({"checkpoint_after": checkpoint, **(step or {})})
    (directory / "social_post.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    system.engine.workflows.workflows_dir = directory
    system.catalog.models.update(catalog_models if catalog_models is not None else models())
    system.catalog.routing_profiles["eco"] = with_candidates(system, eco if eco is not None else profile())
    system.catalog.routing_assignments["social_post"] = {"create": "eco"}
    system.lab, system.net = lab, net
    return system


def with_candidates(system, declared):
    """Limit the profile to the test models: the bundled catalog models would otherwise take part."""
    if "candidate_model_ids" not in declared:
        declared = {**declared, "candidate_model_ids": [key for key, value in system.catalog.models.items()
                                                         if value.provider in ("lab", "net")]}
    return declared


def start(system, **request):
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="eco-t06", objective="Create an educational post", topic="KNX", **request))
    system.worker.process_once()
    return run["id"]


def state(system, run_id):
    return system.run_service.get(run_id)


def events(run, name):
    return [event["payload"] for event in run["events"] if event["event_type"] == name]


def names(run):
    return [event["event_type"] for event in run["events"]]


# --- modes ------------------------------------------------------------------------------------------------


def test_automatic_mode_escalates_one_level_after_the_retries_are_spent(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad", "bad"], "t2": ["ok"]}, eco=profile(max_retries_per_candidate=1))
    run = state(system, start(system))
    assert system.lab.calls == ["t1", "t1", "t2"]                    # both tries of t1, then one call to the next level
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"
    assert run["llm_calls"] == 3
    (escalated,) = events(run, "model_escalated")
    assert (escalated["from_level"], escalated["from_model"], escalated["to_level"], escalated["to_model"]) == (
        "T1", "t1", "T2", "t2")
    assert run["artifacts"][0]["step_id"] == "create"
    assert events(run, "model_selected")[0]["model"] == "t2"
    assert events(run, "model_selected")[0]["escalation"]["to_level"] == "T2"
    assert names(run).index("model_escalated") < names(run).index("step_completed")


def test_a_semantic_validation_failure_escalates_too_and_the_reason_says_so(tmp_path):
    system = make(tmp_path, scripts={"t1": ["semantic"], "t2": ["ok"]})
    (escalated,) = events(state(system, start(system)), "model_escalated")
    assert escalated["reason"] == "semantic_validation_failed"
    assert escalated["error_type"] == "StructuredOutputError" and "hashtags" in escalated["error"]
    assert system.lab.calls == ["t1", "t2"]


def test_an_unparsable_answer_escalates_with_its_own_reason(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]})
    (escalated,) = events(state(system, start(system)), "model_escalated")
    assert escalated["reason"] == "structured_output_failed"


def test_supervised_mode_escalates_below_t3_without_pausing(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]}, eco=profile("supervised"))
    run = state(system, start(system))
    assert run["waiting_reason"] == "review" and events(run, "model_escalated") and not events(run, "escalation_requested")


def test_automatic_mode_reaches_t3_without_pausing(tmp_path):
    system = make(tmp_path, scripts={"t2": ["bad"], "t3": ["ok"]}, eco=profile(levels=_only("T2")))
    run = state(system, start(system))
    assert system.lab.calls == ["t2", "t3"]
    assert run["waiting_reason"] == "review"
    (escalated,) = events(run, "model_escalated")
    assert (escalated["from_level"], escalated["to_level"], escalated["mode"]) == ("T2", "T3", "automatic")
    assert not events(run, "escalation_requested")


def test_single_mode_never_escalates(tmp_path):
    eco = profile("single", model_id="t1")
    del eco["levels"]
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]}, eco=eco)
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and "not the requested JSON" in run["error"]
    assert system.lab.calls == ["t1"]
    assert not {"model_escalated", "escalation_requested", "escalation_blocked"} & set(names(run))


def test_profiles_without_escalation_or_without_economic_fields_keep_failing_as_before(tmp_path):
    legacy = {"version": 1, "enabled": True, "candidate_model_ids": ["t1"], "max_candidates": 1,
              "required_capabilities": ["structured_output"], "allowed_permissions": [],
              "allowed_data_classes": ["non_sensitive"], "execution_modes": ["auto"], "economic_selection": False}
    for index, eco in enumerate([profile(escalation=False), legacy,
                                 profile(escalation={"max_escalations_per_step": 0, "ceiling": "T3"})]):
        system = make(tmp_path / str(index), scripts={"t1": ["bad"], "t2": ["ok"]}, eco=eco)
        run = state(system, start(system))
        assert run["status"] == RunStatus.FAILED.value and "not the requested JSON" in run["error"]
        assert system.lab.calls == ["t1"]
        assert not {"model_escalated", "escalation_requested", "escalation_blocked"} & set(names(run))


def test_a_profile_that_escalates_keeps_its_fallback_inside_the_selected_level(tmp_path):
    # With `work: max T2` the T2 model is eligible, but it is not the fallback of a T1 model: moving up is escalation.
    eco = profile(levels={"work": {"max": "T2"}, "orchestration": {"min": "T3"}})
    system = make(tmp_path, scripts={"t1": [ProviderUnavailable("down")], "t1b": ["ok"], "t2": []},
                  eco=eco, catalog_models=models(t1b=spec("t1b", "T1", amount=1)))
    run = state(system, start(system))
    decision = events(run, "routing_decision")[0]
    assert decision["selected_model"] == "t1" and decision["fallback_models"] == ["t1b"]
    assert system.lab.calls == ["t1", "t1b"] and not events(run, "model_escalated")
    assert run["waiting_reason"] == "review"


# --- what does not escalate -------------------------------------------------------------------------------


@pytest.mark.parametrize("error", [ProviderUnavailable("network down"), AuthenticationError("bad key"),
                                   QuotaExceeded("quota"), RateLimitExceeded("slow down")])
def test_provider_errors_never_escalate(tmp_path, error):
    system = make(tmp_path, scripts={"t1": [error], "t2": ["ok"]})
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and str(error) in run["error"]
    assert system.lab.calls == ["t1"]
    assert not {"model_escalated", "escalation_requested", "escalation_blocked"} & set(names(run))


def test_a_provider_error_on_the_fallback_after_a_quality_failure_does_not_escalate_either(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad"], "t1b": [QuotaExceeded("quota")], "t2": ["ok"]},
                  catalog_models=models(t1b=spec("t1b", "T1", amount=1)))
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and "quota" in run["error"]
    assert system.lab.calls == ["t1", "t1b"]
    assert not events(run, "model_escalated")


def test_a_human_revision_does_not_escalate(tmp_path):
    system = make(tmp_path, scripts={"t1": ["ok", "ok"], "t2": []})
    run_id = start(system)
    system.run_service.changes(run_id, "Make it shorter")
    system.worker.process_once()
    run = state(system, run_id)
    assert system.lab.calls == ["t1", "t1"] and not events(run, "model_escalated")
    decisions = events(run, "routing_decision")
    assert [d["selected_level"] for d in decisions] == ["T1", "T1"] and all("escalation" not in d for d in decisions)


def test_the_gate_services_have_no_escalation_hook():
    from multiagent.db import gate_store
    from multiagent.services import gates

    for module in (gates, gate_store):
        assert "escalat" not in inspect.getsource(module).lower()


# --- limit, ceiling and candidates ------------------------------------------------------------------------


def test_at_most_one_escalation_per_step(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["bad"], "t3": ["ok"]})
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value
    assert system.lab.calls == ["t1", "t2"]                                  # t3 is never tried
    assert len(events(run, "model_escalated")) == 1
    (blocked,) = events(run, "escalation_blocked")
    assert blocked["code"] == "max_escalations_reached" and "Human intervention is required" in run["error"]


def test_the_ceiling_stops_the_escalation(tmp_path):
    eco = profile(escalation={"max_escalations_per_step": 1, "ceiling": "T2"}, levels=_only("T2"))
    system = make(tmp_path, scripts={"t2": ["bad"], "t3": ["ok"]}, eco=eco)
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and system.lab.calls == ["t2"]
    (blocked,) = events(run, "escalation_blocked")
    assert blocked["code"] == "ceiling_reached" and blocked["ceiling"] == "T2" and blocked["from_level"] == "T2"
    assert "Human intervention is required" in run["error"] and not events(run, "model_escalated")


def test_t4_is_never_reached_automatically(tmp_path):
    eco = profile(levels=_only("T3"))
    system = make(tmp_path, scripts={"t3": ["bad"], "t4": ["ok"]}, catalog_models=models(t4=spec("t4", "T4", amount=900)),
                  eco=eco)
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and system.lab.calls == ["t3"]
    assert events(run, "escalation_blocked")[0]["code"] == "ceiling_reached"
    # The selector refuses the level on its own, whatever the engine asks for.
    decision = select(system, escalation={"to_level": "T4", "number": 1}, eco=profile())
    assert decision.status == "blocked" and decision.reason_codes == ("escalation_above_ceiling",)


def test_no_candidate_at_the_next_level_fails_asking_for_a_human_and_never_skips_a_level(tmp_path):
    only = {"t1": spec("t1", "T1", pricing=NO_CHARGE), "t3": spec("t3", "T3", amount=300)}       # nothing at T2
    system = make(tmp_path, scripts={"t1": ["bad"], "t3": ["ok"]}, catalog_models=only)
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and system.lab.calls == ["t1"]
    (blocked,) = events(run, "escalation_blocked")
    assert blocked["code"] == "no_eligible_candidate" and blocked["to_level"] == "T2"
    assert {"level_below_minimum", "level_above_maximum"} <= set(blocked["reason_codes"])   # t1 is too low, t3 too high
    assert "Human intervention is required" in run["error"] and "T2" in run["error"]
    escalation_decision = events(run, "routing_decision")[-1]
    assert escalation_decision["status"] == "blocked" and escalation_decision["escalation"]["to_level"] == "T2"


def test_escalation_only_considers_classified_models_with_current_price(tmp_path):
    spoiled = {"t1": spec("t1", "T1", pricing=NO_CHARGE),
               "t2_unpriced": spec("t2_unpriced", "T2"),
               "t2_expired": spec("t2_expired", "T2", pricing={"input": side(1, valid_until="2025-06-01T00:00:00Z"),
                                                                  "output": side(2, valid_until="2025-06-01T00:00:00Z")}),
               "t2_unclassified": dataclasses.replace(spec("t2_unclassified", "T2", amount=1), classification="unclassified")}
    system = make(tmp_path, scripts={"t1": ["bad"]}, catalog_models=spoiled)
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and system.lab.calls == ["t1"]
    reasons = events(run, "escalation_blocked")[0]["reason_codes"]
    assert {"model_unclassified", "price_evidence_missing_or_expired"} <= set(reasons)


# --- sensitive data ---------------------------------------------------------------------------------------


def _authorize_sensitive(system, run_id):
    """Play a trusted host: the run request carries the caller's grants (the models forbid the public ones)."""
    with system.db.connect() as conn:
        request = json.loads(conn.execute("SELECT request_json FROM runs WHERE id=?", (run_id,)).fetchone()["request_json"])
        request.update({"authorized_data_classes": ["sensitive", "non_sensitive"],
                        "effective_permissions": ["data:sensitive", "provider:cloud"]})
        conn.execute("UPDATE runs SET request_json=? WHERE id=?", (json.dumps(request), run_id))
        conn.commit()


def _sensitive_system(tmp_path, *, local_t2):
    sensitive = {"classes": ("sensitive", "non_sensitive"), "permissions": ("data:sensitive",)}
    extra = {"t1": spec("t1", "T1", pricing=NO_CHARGE, **sensitive),
             # The cloud T2 model is the cheapest T2, but it cannot take sensitive data.
             "t2_cloud": spec("t2_cloud", "T2", cloud=True, amount=1)}
    if local_t2:
        extra["t2_local"] = spec("t2_local", "T2", amount=50, **sensitive)
    eco = profile(allowed_data_classes=["sensitive", "non_sensitive"], allowed_permissions=["data:sensitive", "provider:cloud"])
    system = make(tmp_path, scripts={"t1": ["bad"], "t2_local": ["ok"]}, net_scripts={"t2_cloud": ["ok"]},
                  catalog_models=extra, eco=eco)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="eco-t06", objective="Create an educational post", topic="KNX", sensitive=True))
    _authorize_sensitive(system, run["id"])
    system.worker.process_once()
    return system, state(system, run["id"])


def test_sensitive_data_escalates_only_to_a_local_model(tmp_path):
    system, run = _sensitive_system(tmp_path, local_t2=True)
    (escalated,) = events(run, "model_escalated")
    assert escalated["to_model"] == "t2_local" and system.net.calls == []
    rejected = {c["model_id"]: c["reason_codes"] for c in events(run, "routing_decision")[-1]["candidates"] if not c["eligible"]}
    assert {"candidate_permission_unavailable_or_unknown", "candidate_data_class_unknown_or_disallowed"} <= set(rejected["t2_cloud"])


def test_sensitive_data_never_leaves_local_even_when_that_leaves_no_candidate(tmp_path):
    system, run = _sensitive_system(tmp_path, local_t2=False)
    assert run["status"] == RunStatus.FAILED.value and system.net.calls == [] and system.lab.calls == ["t1"]
    (blocked,) = events(run, "escalation_blocked")
    assert blocked["code"] == "no_eligible_candidate" and "Human intervention is required" in run["error"]


# --- budgets ----------------------------------------------------------------------------------------------


def test_max_llm_calls_exhausted_means_no_escalation(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]}, max_llm_calls=1)
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and system.lab.calls == ["t1"]
    (blocked,) = events(run, "escalation_blocked")
    assert blocked["code"] == "max_llm_calls_reached"
    assert not events(run, "model_escalated") and len(events(run, "routing_decision")) == 1


def test_the_profile_dispatch_budget_exhausted_means_no_escalation(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]}, eco=profile(max_provider_invocations=1))
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and system.lab.calls == ["t1"]
    assert events(run, "escalation_blocked")[0]["code"] == "max_provider_invocations_reached"


def test_the_dispatch_budget_with_room_lets_the_escalation_run(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]}, eco=profile(max_provider_invocations=2))
    run = state(system, start(system))
    assert system.lab.calls == ["t1", "t2"] and events(run, "model_escalated")
    assert [e["payload"]["attempted_model"] for e in run["events"] if e["event_type"] == "routing_dispatch_reserved"] == [
        "t1", "t2"]


def _queued_after_earlier_dispatch(system, *, corrected):
    """A run that already booked one dispatch of ``eco`` (optionally followed by a correction of the person) and is queued."""
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="eco-t06", objective="Create an educational post", topic="KNX"))
    system.store.db.log_event(run["id"], "routing_dispatch_reserved", {"profile_id": "eco", "attempted_model": "t1"})
    if corrected:
        system.store.db.log_event(run["id"], "human_revision", {"step_id": "create", "attempt": 1, "comment": "Fix it"})
    system.worker.process_once()
    return run["id"]


def test_the_dispatch_budget_counts_from_the_last_correction_when_escalating(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]}, eco=profile(max_provider_invocations=2))
    run = state(system, _queued_after_earlier_dispatch(system, corrected=True))
    assert system.lab.calls == ["t1", "t2"] and events(run, "model_escalated")
    assert not events(run, "escalation_blocked")


def test_the_dispatch_budget_without_a_correction_still_counts_every_dispatch_when_escalating(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]}, eco=profile(max_provider_invocations=2))
    run = state(system, _queued_after_earlier_dispatch(system, corrected=False))
    assert system.lab.calls == ["t1"]
    assert events(run, "escalation_blocked")[0]["code"] == "max_provider_invocations_reached"


def _with_output_estimate(system, monkeypatch, tokens=100):
    """The engine estimates input tokens only; a host that also estimates output makes cost budgets usable."""
    select_ = system.engine.router.select
    monkeypatch.setattr(system.engine.router, "select",
                        lambda request, evidence=None: select_(dataclasses.replace(request, estimated_output_tokens=tokens), evidence))


def test_a_planning_cost_budget_the_next_level_would_break_means_no_escalation(tmp_path, monkeypatch):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]},
                  eco=profile(planning_max_cost={"amount": 0.01, "currency": "USD"}))   # t1 is free; t2 costs ~0.06
    _with_output_estimate(system, monkeypatch)
    run = state(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and system.lab.calls == ["t1"]
    (blocked,) = events(run, "escalation_blocked")
    assert "cost_budget_exceeded" in blocked["reason_codes"]


def test_a_planning_cost_budget_with_room_lets_the_escalation_run(tmp_path, monkeypatch):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]},
                  eco=profile(planning_max_cost={"amount": 5, "currency": "USD"}))
    _with_output_estimate(system, monkeypatch)
    run = state(system, start(system))
    assert system.lab.calls == ["t1", "t2"]
    (escalated,) = events(run, "model_escalated")
    assert escalated["estimated_cost"]["currency"] == "USD" and escalated["estimated_cost"]["amount"] > 0


# --- the record -------------------------------------------------------------------------------------------


def test_the_event_and_the_routing_evidence_say_what_was_escalated(tmp_path):
    system = make(tmp_path, scripts={"t1": ["semantic"], "t2": ["ok"]},
                  catalog_models=models(t1=spec("t1", "T1", amount=1)))
    run = state(system, start(system))
    (escalated,) = events(run, "model_escalated")
    assert escalated["step_id"] == "create" and escalated["attempt"] == 1 and escalated["escalation"] == 1
    assert escalated["profile_id"] == "eco" and escalated["approved_by_human"] is False
    # 1000 input and 500 output tokens at 1 and 2 USD per million, spent on the failed call.
    so_far = escalated["estimated_cost_so_far"]
    assert so_far["run"]["estimated_cost_usd"] == pytest.approx(0.002) and so_far["run"]["cost_unknown_records"] == 0
    assert so_far["step"]["estimated_cost_usd"] == pytest.approx(0.002) and so_far["run"]["currency"] == "USD"
    assert escalated["price_evidence"]["model_id"] == "t2" and escalated["price_evidence"]["input"]["source"] == "unit-test rate card"

    first, second = events(run, "routing_decision")
    assert "escalation" not in first and first["selected_level"] == "T1"
    evidence = second["escalation"]
    assert (evidence["number"], evidence["attempt"], evidence["from_level"], evidence["from_model"],
            evidence["to_level"], evidence["reason"]) == (1, 1, "T1", "t1", "T2", "semantic_validation_failed")
    assert (evidence["ceiling"], evidence["max_escalations_per_step"]) == ("T3", 1)
    assert second["selected_model"] == "t2" and second["selected_level"] == "T2"
    assert "escalated_to_next_level" in second["reason_codes"]
    attempts = [e["payload"] for e in run["events"] if e["event_type"] == "model_attempt"]
    assert [(a["attempted_model"], a.get("escalation")) for a in attempts] == [("t1", None), ("t2", 1)]
    assert [a["model"] for a in events(run, "model_selected")[0]["attempts"]] == ["t1", "t2"]   # the whole step, not the last tries


def test_an_unknown_cost_is_kept_apart_not_counted_as_zero(tmp_path):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]})
    (escalated,) = events(state(system, start(system)), "model_escalated")
    so_far = escalated["estimated_cost_so_far"]["run"]
    assert so_far["estimated_cost_usd"] is None and so_far["cost_unknown_records"] == 1 and so_far["cost_complete"] is False


def test_the_router_sees_the_attempt_and_the_escalation_of_each_request(tmp_path, monkeypatch):
    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]})
    seen: list[RoutingRequest] = []
    select_ = system.engine.router.select
    monkeypatch.setattr(system.engine.router, "select", lambda request, evidence=None: (seen.append(request), select_(request, evidence))[1])
    start(system)
    assert [(r.attempt, r.step_kind) for r in seen] == [(1, "work"), (1, "work")]
    assert seen[0].escalation is None
    assert seen[1].escalation["to_level"] == "T2" and seen[1].escalation["from_model"] == "t1"
    assert seen[1].to_dict()["escalation"]["reason"] == "structured_output_failed"


# --- supervised pause -------------------------------------------------------------------------------------


def _paused(tmp_path, **kwargs):
    system = make(tmp_path, scripts={"t2": ["bad"], "t3": ["ok"]}, eco=profile("supervised", levels=_only("T2")), **kwargs)
    return system, start(system)


def test_supervised_mode_pauses_for_approval_before_escalating_to_t3(tmp_path):
    system, run_id = _paused(tmp_path)
    run = state(system, run_id)
    assert run["status"] == RunStatus.WAITING_HUMAN.value
    assert (run["waiting_reason"], run["waiting_step"], run["waiting_attempt"], run["current_step"]) == (
        "escalation_approval", "create", 1, 1)
    assert system.lab.calls == ["t2"]                                     # nothing ran at the stronger level
    (asked,) = events(run, "escalation_requested")
    assert (asked["from_level"], asked["from_model"], asked["to_level"], asked["to_model"]) == ("T2", "t2", "T3", "t3")
    assert asked["reason"] == "structured_output_failed" and "not the requested JSON" in asked["error"]
    assert asked["mode"] == "supervised" and asked["price_evidence"]["model_id"] == "t3"
    assert "estimated_cost_so_far" in asked and asked["approved_by_human"] is False
    assert not events(run, "model_escalated")
    assert run["llm_calls"] == 1 and not run["artifacts"]


def test_approving_the_escalation_resumes_the_step_with_the_escalated_model(tmp_path):
    system, run_id = _paused(tmp_path)
    approved = system.run_service.approve(run_id)
    assert approved["status"] == RunStatus.QUEUED.value and approved["waiting_reason"] == ""
    assert events(approved, "escalation_approved")[0]["to_model"] == "t3"
    system.worker.process_once()
    run = state(system, run_id)
    assert system.lab.calls == ["t2", "t3"]                                # t2 is not run again
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"
    (escalated,) = events(run, "model_escalated")
    assert escalated["approved_by_human"] is True and escalated["to_model"] == "t3" and escalated["from_model"] == "t2"
    assert events(run, "model_selected")[0]["model"] == "t3"
    resumed = events(run, "routing_decision")[-1]
    assert resumed["escalation"]["approved_by_human"] is True and resumed["selected_level"] == "T3"
    assert events(run, "step_started")[-1]["escalation"]["to_level"] == "T3"
    assert [e["payload"].get("escalation") for e in run["events"] if e["event_type"] == "model_attempt"] == [None, 1]
    assert run["llm_calls"] == 2


def test_an_escalated_step_that_fails_again_after_approval_does_not_escalate_a_second_time(tmp_path):
    system = make(tmp_path, scripts={"t2": ["bad"], "t3": ["bad"]}, eco=profile("supervised", levels=_only("T2")))
    run_id = start(system)
    system.run_service.approve(run_id)
    system.worker.process_once()
    run = state(system, run_id)
    assert run["status"] == RunStatus.FAILED.value and system.lab.calls == ["t2", "t3"]
    assert events(run, "escalation_blocked")[0]["code"] == "max_escalations_reached" and len(events(run, "escalation_requested")) == 1


def test_rejecting_the_escalation_fails_the_step(tmp_path):
    system, run_id = _paused(tmp_path)
    rejected = system.run_service.reject(run_id, "Too expensive for this post")
    assert rejected["status"] == RunStatus.FAILED.value
    assert "rejected by a human" in rejected["error"] and "Too expensive for this post" in rejected["error"]
    assert (rejected["waiting_reason"], rejected["waiting_step"], rejected["waiting_attempt"]) == ("", "", None)
    assert events(rejected, "escalation_rejected")[0]["feedback"] == "Too expensive for this post"
    assert events(rejected, "workflow_failed")[-1]["type"] == "EscalationRejected"
    system.worker.process_once()
    assert system.lab.calls == ["t2"]                                      # the stronger model was never called
    assert state(system, run_id)["status"] == RunStatus.FAILED.value


def test_the_pending_escalation_takes_no_other_human_action_and_no_double_decision(tmp_path):
    system, run_id = _paused(tmp_path)
    with pytest.raises(InvalidStateTransition):
        system.run_service.changes(run_id, "Make it shorter")
    system.run_service.approve(run_id)
    with pytest.raises(InvalidStateTransition):
        system.run_service.approve(run_id)
    with pytest.raises(InvalidStateTransition):
        system.run_service.reject(run_id)


def test_a_cancelled_run_cannot_be_approved(tmp_path):
    system, run_id = _paused(tmp_path)
    system.run_service.cancel(run_id)
    with pytest.raises(InvalidStateTransition):
        system.run_service.approve(run_id)
    assert system.lab.calls == ["t2"]


def test_approval_does_not_skip_the_policy_checks_at_resume(tmp_path):
    system, run_id = _paused(tmp_path)
    system.run_service.approve(run_id)
    system.catalog.models["t3"] = dataclasses.replace(system.catalog.models["t3"], classification="unclassified")
    system.worker.process_once()
    run = state(system, run_id)
    assert run["status"] == RunStatus.FAILED.value and system.lab.calls == ["t2"]
    assert events(run, "escalation_blocked")[0]["code"] == "no_eligible_candidate"
    assert "Human intervention is required" in run["error"]


def test_the_approved_model_must_still_be_the_eligible_choice(tmp_path):
    system = make(tmp_path, scripts={"t2": ["bad"], "t3": ["ok"], "t3b": ["ok"]},
                  eco=profile("supervised", levels=_only("T2")), catalog_models=models(t3b=spec("t3b", "T3", amount=200)))
    run_id = start(system)
    assert events(state(system, run_id), "escalation_requested")[0]["to_model"] == "t3b"      # the cheaper T3
    system.run_service.approve(run_id)
    system.catalog.models["t3b"] = spec("t3b", "T3", amount=900)                              # now dearer than t3
    system.worker.process_once()
    run = state(system, run_id)
    assert run["status"] == RunStatus.FAILED.value and system.lab.calls == ["t2"]
    assert events(run, "escalation_blocked")[0]["code"] == "approved_model_no_longer_selected"


# --- the selector -----------------------------------------------------------------------------------------


def select(system, *, escalation, eco=None, **overrides):
    system.catalog.routing_profiles["eco"] = with_candidates(system, eco if eco is not None else profile())
    request = RoutingRequest(
        profile_id="eco", workflow_id="social_post", step_id="create", required_role="creator",
        required_capabilities=frozenset({"structured_output"}), effective_permissions=frozenset(),
        authorized_data_classes=frozenset({"non_sensitive"}), data_class="non_sensitive", execution_mode="auto",
        escalation=escalation, **overrides)
    return ModelRouter(system.catalog, adapters={}).select(request, RoutingEvidence(scope="run-1"))


@pytest.fixture
def bare(tmp_path):
    return make(tmp_path, scripts={})


def test_escalation_selects_exactly_the_requested_level(bare):
    bare.catalog.models.update({"t2_dear": spec("t2_dear", "T2", amount=400), "t3_cheap": spec("t3_cheap", "T3", amount=1)})
    decision = select(bare, escalation={"to_level": "T2", "number": 1, "from_level": "T1"})
    assert decision.status == "selected" and decision.selected_model == "t2" and decision.selected_level == "T2"
    assert decision.fallback_models == ("t2_dear",)                              # a peer at the same level, never a T3
    rejected = {c.model_id: c.reason_codes for c in decision.candidates if not c.eligible}
    assert rejected["t1"] == ("level_below_minimum",) and rejected["t3"] == ("level_above_maximum",)
    assert decision.escalation == {"to_level": "T2", "number": 1, "from_level": "T1", "ceiling": "T3",
                                   "max_escalations_per_step": 1}
    assert decision.to_dict()["escalation"]["ceiling"] == "T3"


def test_escalation_is_refused_by_profiles_that_do_not_allow_it(bare):
    ask = {"to_level": "T2", "number": 1}
    for eco in (profile(escalation=False), profile(escalation={"max_escalations_per_step": 0, "ceiling": "T3"}),
                profile("single", model_id="t1")):
        decision = select(bare, escalation=ask, eco=eco)
        assert decision.status == "blocked" and decision.reason_codes == ("escalation_not_enabled_for_profile",)
        assert decision.escalation["to_level"] == "T2"
    legacy = {"version": 1, "enabled": True, "candidate_model_ids": ["t1", "t2"], "max_candidates": 2,
              "required_capabilities": ["structured_output"], "allowed_permissions": [],
              "allowed_data_classes": ["non_sensitive"], "execution_modes": ["auto"]}
    assert select(bare, escalation=ask, eco=legacy).reason_codes == ("escalation_not_enabled_for_profile",)


def test_escalation_above_the_ceiling_is_refused(bare):
    eco = profile(escalation={"max_escalations_per_step": 1, "ceiling": "T2"})
    assert select(bare, escalation={"to_level": "T3", "number": 1}, eco=eco).reason_codes == ("escalation_above_ceiling",)
    assert select(bare, escalation={"to_level": "T2", "number": 1}, eco=eco).selected_model == "t2"


def test_the_ordinary_decision_is_unchanged_and_has_no_escalation_key(bare):
    decision = select(bare, escalation=None)
    assert decision.selected_model == "t1" and decision.escalation is None and "escalation" not in decision.to_dict()
    assert decision.fallback_models == ()                       # work is capped at T1 here: no peer
    assert "escalation" in RoutingRequest().to_dict() and RoutingRequest().escalation is None


@pytest.mark.parametrize("bad", [{}, {"to_level": "T9"}, {"to_level": None}, "T2"])
def test_a_malformed_escalation_request_is_rejected(bad):
    with pytest.raises(ValueError):
        RoutingRequest(escalation=bad)


def _only(level):
    """Work steps may start only at ``level`` (so the level above it is reached by escalation alone)."""
    return {"work": {"min": level, "max": level}, "orchestration": {"min": "T3"}, "planning": {"min": "T3"},
            "review": {"min": "T3"}}
