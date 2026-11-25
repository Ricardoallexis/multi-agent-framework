# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""LIM-T01: a correction the person asks for is not cut by the automatic call and time limits, and the limit state
of a run says only what the evidence shows."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from types import SimpleNamespace

from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import PipelineMode, RunStatus, SocialPostRequest
from multiagent.gui.continuity_api import RUN_LIMIT_VERSION, run_limit_state

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- the engine

def settings_for(tmp_path: Path, **extra) -> Settings:
    return Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                    runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets",
                    worker_enabled=False, gemini_api_key="", **extra)


@pytest.fixture(params=["native", "mock"])
def backend(request):
    return request.param


def first_review(tmp_path, backend="native", **extra):
    system = build_system(settings_for(tmp_path, execution_backend=backend, **extra), dry_run=True)
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="lim-t01", objective="Create an educational post", topic="KNX"))
    system.worker.process_once()
    return system, run["id"]


def events(system, run_id, name):
    return [e for e in system.run_service.get(run_id)["events"] if e["event_type"] == name]


def test_a_correction_after_the_call_limit_is_not_rejected(tmp_path, backend):
    system, run_id = first_review(tmp_path, backend)
    with system.db.connect() as conn:                      # the automatic limit is already spent
        conn.execute("UPDATE runs SET max_llm_calls=llm_calls WHERE id=?", (run_id,))
        conn.commit()
    system.run_service.changes(run_id, "Make it shorter")
    system.worker.process_once()
    run = system.run_service.get(run_id)
    assert run["status"] == RunStatus.WAITING_HUMAN.value
    assert run["budget_exhausted"] == 0 and run["waiting_reason"] == "review"
    assert len(events(system, run_id, "correction_allowance")) == 1
    assert run["max_llm_calls"] >= run["llm_calls"] + 1


def test_a_correction_after_the_time_limit_is_not_cut(tmp_path, backend):
    system, run_id = first_review(tmp_path, backend)
    system.store.update_run(run_id, active_seconds=10_000)
    system.run_service.changes(run_id, "Make it shorter")
    system.worker.process_once()
    run = system.run_service.get(run_id)
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["budget_exhausted"] == 0
    assert run["max_run_seconds"] > 10_000


def test_the_allowance_is_granted_once_per_correction(tmp_path, backend):
    system, run_id = first_review(tmp_path, backend)
    system.run_service.changes(run_id, "One")
    system.worker.process_once()
    assert len(events(system, run_id, "correction_allowance")) == 1
    system.run_service.changes(run_id, "Two")
    system.worker.process_once()
    assert len(events(system, run_id, "correction_allowance")) == 2


def test_the_allowance_is_bounded_by_the_configured_limits(tmp_path, backend):
    system, run_id = first_review(tmp_path, backend, max_llm_calls=3, max_run_seconds=60)
    before = system.run_service.get(run_id)
    system.run_service.changes(run_id, "Shorter")
    system.worker.process_once()
    grant = events(system, run_id, "correction_allowance")[0]["payload"]
    assert grant["max_llm_calls"] == max(before["max_llm_calls"], before["llm_calls"] + 3)
    assert grant["max_run_seconds"] < int(before["active_seconds"]) + 1 + 60 + 1      # a limit, not "no limit"


def test_more_corrections_than_max_iterations_pass(tmp_path, backend):
    system, run_id = first_review(tmp_path, backend)
    for number in range(5):                                # the workflow allows 2 automatic revisions
        system.run_service.changes(run_id, f"Change {number}")
        system.worker.process_once()
        run = system.run_service.get(run_id)
        assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"
        assert run["budget_exhausted"] == 0
    assert run["waiting_attempt"] == 6


def test_an_attempt_nobody_asked_for_stays_bounded():
    from multiagent.call_guards import person_correction_pending

    asked = [{"event_type": "human_revision", "payload": {"step_id": "create", "attempt": 3}}]
    assert person_correction_pending(asked, "create", 4) is True
    assert person_correction_pending(asked, "create", 5) is False      # an automatic loop after it
    assert person_correction_pending(asked, "other", 4) is False
    assert person_correction_pending([], "create", 4) is False


def exhausted(system, run_id, *, error, quota=False):
    """Leave the run the way a cut leaves it: waiting on its budget with the artifact to show."""
    artifact = system.store.review_artifact(run_id)
    system.store.update_run(run_id, status="waiting_human", error=error, budget_exhausted=1,
                            waiting_reason="budget_exhausted", waiting_step=artifact["step_id"],
                            waiting_attempt=artifact["attempt"])
    if quota:
        system.store.db.log_event(run_id, "model_attempt", {"step_id": artifact["step_id"], "model": "cloud_fast",
                                                            "provider": "gemini", "error_type": "QuotaExceeded"})


@pytest.mark.parametrize("error", ["max_llm_calls reached", "max_run_seconds reached"])
def test_a_run_cut_by_its_own_limits_accepts_a_correction(tmp_path, backend, error):
    system, run_id = first_review(tmp_path, backend)
    exhausted(system, run_id, error=error)
    system.run_service.changes(run_id, "Make it shorter")
    system.worker.process_once()
    run = system.run_service.get(run_id)
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"
    assert run["budget_exhausted"] == 0 and len(events(system, run_id, "correction_allowance")) == 1


def test_a_run_cut_by_the_providers_quota_still_refuses_a_correction(tmp_path, backend):
    from multiagent.errors import BudgetExceeded

    system, run_id = first_review(tmp_path, backend)
    exhausted(system, run_id, error="max_llm_calls reached", quota=True)
    with pytest.raises(BudgetExceeded, match="only the existing artifact"):
        system.run_service.changes(run_id, "Make it shorter")
    assert system.run_service.get(run_id)["waiting_reason"] == "budget_exhausted"


def test_a_cut_that_is_not_a_run_limit_still_refuses_a_correction(tmp_path, backend):
    from multiagent.errors import BudgetExceeded

    system, run_id = first_review(tmp_path, backend)
    exhausted(system, run_id, error="Maximum revision count reached for step=create")
    with pytest.raises(BudgetExceeded):
        system.run_service.changes(run_id, "Make it shorter")


@pytest.mark.parametrize("error,quota,offered", [("max_llm_calls reached", False, True), ("max_run_seconds reached", False, True),
                                                 ("max_llm_calls reached", True, False),
                                                 ("Maximum revision count reached for step=create", False, False)])
def test_the_thread_offers_changes_only_when_the_run_limits_cut_the_run(tmp_path, error, quota, offered):
    from multiagent.gui.threads_api import _decision

    system, run_id = first_review(tmp_path)
    exhausted(system, run_id, error=error, quota=quota)
    decision = _decision(system.run_service.get(run_id), {})
    assert decision["type"] == "budget_exhausted"
    assert decision["actions"] == (["approve", "changes", "reject"] if offered else ["approve", "reject"])


def test_a_review_without_a_cut_keeps_its_three_actions(tmp_path):
    from multiagent.gui.threads_api import _decision

    system, run_id = first_review(tmp_path)
    assert _decision(system.run_service.get(run_id), {})["actions"] == ["approve", "changes", "reject"]


# --------------------------------------------------------------------------- review round 5 (H1-H5)

FIXTURE_WORKFLOWS = Path(__file__).parent / "fixtures" / "workflows"


def multi_step(tmp_path, backend, **extra):
    """The four-step ``g2_intermediate`` workflow (checkpoints after ``strategy`` and ``design``), stopped at ``strategy``."""
    from multiagent.workflows import WorkflowCatalog

    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, gemini_api_key="",
                        openai_api_key="", openai_enabled=False, execution_backend=backend, **extra)
    system = build_system(settings, dry_run=True)
    system.engine.workflows = WorkflowCatalog(settings, workflows_dir=FIXTURE_WORKFLOWS / "g2_intermediate")
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="lim-r5", objective="Explain durable execution", topic="Checkpoint", use_brand_context=False, pipeline_mode=PipelineMode.FULL))
    system.engine.process_run(run["id"])
    return system, run["id"]


def spend_calls(system, run_id):
    with system.db.connect() as conn:
        conn.execute("UPDATE runs SET max_llm_calls=llm_calls WHERE id=?", (run_id,))
        conn.commit()


def test_the_time_limit_really_rises_and_is_still_finite(tmp_path, backend):
    system, run_id = first_review(tmp_path, backend)
    assert system.run_service.get(run_id)["max_run_seconds"] == 300
    system.store.update_run(run_id, active_seconds=100)
    system.run_service.changes(run_id, "Shorter")
    system.worker.process_once()
    grant = events(system, run_id, "correction_allowance")[0]["payload"]
    assert grant["max_run_seconds"] == 401 and system.run_service.get(run_id)["max_run_seconds"] == 401


def test_the_cursor_goes_back_to_the_reviewed_step_after_a_cut(tmp_path, backend):
    system, run_id = multi_step(tmp_path, backend)
    assert system.run_service.get(run_id)["waiting_step"] == "strategy"
    system.run_service.approve(run_id)
    spend_calls(system, run_id)
    cut_run = system.engine.process_run(run_id)
    assert cut_run["waiting_reason"] == "budget_exhausted" and cut_run["error"] == "max_llm_calls reached"
    assert cut_run["waiting_step"] == "strategy" and cut_run["current_step"] > 1      # the cursor is on the step that could not run
    queued = system.run_service.changes(run_id, "Clarify the strategy")
    assert queued["current_step"] == 1                                                  # back on the reviewed step
    revised = system.engine.process_run(run_id)
    assert (revised["waiting_step"], revised["waiting_attempt"]) == ("strategy", 2)
    assert revised["waiting_reason"] == "review" and revised["budget_exhausted"] == 0


def test_a_spent_allowance_cuts_the_run_again_and_an_approval_grants_nothing(tmp_path, backend):
    system, run_id = multi_step(tmp_path, backend, max_llm_calls=1)      # one call of margin per correction
    system.run_service.approve(run_id)
    spend_calls(system, run_id)
    system.engine.process_run(run_id)                                    # cut at the step after strategy
    system.run_service.changes(run_id, "Clarify the strategy")
    revised = system.engine.process_run(run_id)                          # the correction spends its one call
    assert revised["llm_calls"] == revised["max_llm_calls"] and len(events(system, run_id, "correction_allowance")) == 1
    system.run_service.approve(run_id)                                   # no new revision: nothing is granted
    stopped = system.engine.process_run(run_id)
    assert stopped["waiting_reason"] == "budget_exhausted" and stopped["error"] == "max_llm_calls reached"
    assert len(events(system, run_id, "correction_allowance")) == 1


def test_resuming_without_a_new_revision_grants_nothing(tmp_path, backend):
    system, run_id = first_review(tmp_path, backend)
    system.run_service.changes(run_id, "Shorter")
    system.worker.process_once()
    for _ in range(2):                                    # a restart or another approval re-queues the same wait
        system.store.update_run(run_id, status="queued", waiting_reason="", waiting_step="", waiting_attempt=None)
        system.engine.process_run(run_id)
    assert len(events(system, run_id, "correction_allowance")) == 1


def test_an_attempt_nobody_asked_for_is_still_cut_by_max_iterations(tmp_path, backend):
    system, run_id = first_review(tmp_path, backend)
    for number in range(3):                                # attempts 2, 3 and 4: all asked for by the person
        system.run_service.changes(run_id, f"Change {number}")
        system.worker.process_once()
    with system.db.connect() as conn:                      # forget that a person asked: attempt 5 is automatic
        conn.execute("DELETE FROM run_events WHERE run_id=? AND event_type IN ('human_revision','correction_allowance')",
                     (run_id,))
        conn.commit()
    system.store.update_run(run_id, status="queued", waiting_reason="", waiting_step="", waiting_attempt=None,
                            budget_exhausted=0)
    stopped = system.engine.process_run(run_id)
    assert stopped["waiting_reason"] == "budget_exhausted" and stopped["error"].startswith("Maximum revision count")


@pytest.mark.parametrize("attempt", [None, "3", 3.0, True, 3.5])
def test_a_revision_without_an_integer_attempt_covers_nothing(attempt):
    from multiagent.call_guards import person_correction_pending

    odd = [{"event_type": "human_revision", "payload": {"step_id": "create", "attempt": attempt}}]
    assert person_correction_pending(odd, "create", 4) is False


def test_only_the_last_of_two_revisions_counts():
    from multiagent.call_guards import person_correction_pending

    two = [{"event_type": "human_revision", "payload": {"step_id": "create", "attempt": 2}},
           {"event_type": "human_revision", "payload": {"step_id": "create", "attempt": 3}}]
    assert person_correction_pending(two, "create", 4) is True
    assert person_correction_pending(two, "create", 5) is False


def test_a_cut_flag_with_a_review_wait_still_offers_changes_when_the_run_limits_cut_it():
    from multiagent.gui.threads_api import _decision

    run = {"budget_exhausted": 1, "waiting_reason": "review", "error": "max_llm_calls reached", "events": []}
    assert _decision(run, {})["actions"] == ["approve", "changes", "reject"]
    quota = {**run, "events": [{"event_type": "model_attempt", "payload": {"error_type": "QuotaExceeded"}}]}
    assert _decision(quota, {})["actions"] == ["approve", "reject"]


def test_an_attempt_a_guard_stopped_before_the_call_is_not_the_providers_answer():
    from multiagent.call_guards import cut_by_run_limits

    def attempt(error_type, **extra):
        return {"event_type": "model_attempt", "payload": {"error_type": error_type, **extra}}

    preflight = attempt("BudgetExceeded", stage="preflight", provider_invoked=False)
    cut = {"error": "max_llm_calls reached"}
    assert cut_by_run_limits({**cut, "events": [attempt("QuotaExceeded", provider_invoked=True), preflight]}) is False
    assert cut_by_run_limits({**cut, "events": [preflight]}) is True
    assert cut_by_run_limits({**cut, "events": []}) is True


def test_the_profile_invocation_limit_counts_from_the_last_correction():
    from multiagent.call_guards import PROCEED, RunLimitsGuard, Stop

    guard = RunLimitsGuard("eco", {"max_provider_invocations": 1})
    reserved = {"event_type": "routing_dispatch_reserved", "payload": {"profile_id": "eco"}}
    revision = {"event_type": "human_revision", "payload": {"step_id": "create", "attempt": 1}}

    def check(*found):
        return guard.check(None, {"events": list(found), "llm_calls": 0, "max_llm_calls": 10})

    assert isinstance(check(reserved), Stop)                  # nobody corrected: counted as always
    assert check(reserved, revision) is PROCEED               # the correction has its own margin
    assert isinstance(check(reserved, revision, reserved), Stop)
    assert check() is PROCEED


def test_the_shared_dispatch_count_starts_after_the_last_correction():
    from multiagent.call_guards import profile_dispatches_since_correction

    reserved = {"event_type": "routing_dispatch_reserved", "payload": {"profile_id": "eco"}}
    other = {"event_type": "routing_dispatch_reserved", "payload": {"profile_id": "other"}}
    revision = {"event_type": "human_revision", "payload": {}}
    assert profile_dispatches_since_correction([reserved, other, reserved], "eco") == 2
    assert profile_dispatches_since_correction([reserved, revision, reserved, other, revision, reserved], "eco") == 1
    assert profile_dispatches_since_correction(None, "eco") == 0


# --------------------------------------------------------------------------- the limit state

def model(tier, level=None, pricing=None, provider="gemini"):
    return SimpleNamespace(tier=tier, level=level, pricing=pricing, provider=provider)


def price(amount):
    side = {"amount": amount, "denominator": 1_000_000, "unit": "token", "currency": "USD",
            "source": "docs", "provenance": "provider_docs",
            "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2027-01-01T00:00:00Z"}
    return {"input": side, "output": side}


ROWS = [
    {"id": "cloud_fast", "kind": "model", "enabled": True, "available": False},
    {"id": "local_default", "kind": "model", "enabled": True, "available": True},
    {"id": "cloud_other", "kind": "model", "enabled": True, "available": True},
    {"id": "cloud_off", "kind": "model", "enabled": False, "available": True},
]
MODELS = {"cloud_fast": model("cloud"), "local_default": model("local", "T1", provider="ollama"),
          "cloud_other": model("cloud", "T2", price(2.0)), "cloud_off": model("cloud", "T2")}


def services(rows=ROWS, models=MODELS):
    return SimpleNamespace(connections=SimpleNamespace(list=lambda: {"connections": rows}),
                           usage=SimpleNamespace(plans=lambda: {"apps": []}),
                           system=SimpleNamespace(catalog=SimpleNamespace(models=models, model=lambda key: models[key])))


def attempt(error="QuotaExceeded", model_id="cloud_fast", provider="gemini", **extra):
    payload = {"model": model_id, "provider": provider, "error_type": error, **extra}
    return {"seq": 1, "ts": "2026-11-25T11:00:00+00:00", "event_type": "model_attempt", "payload": payload}


def run_of(*events, confirm_cloud=False):
    return {"id": "r1", "request": {"confirm_cloud": confirm_cloud}, "events": list(events)}


def test_a_known_limit_carries_source_reset_and_the_contract_fields():
    state = run_limit_state(services(), run_of(attempt(retry_at="2026-11-25T18:00:00+00:00"), confirm_cloud=True),
                            estimated_input_tokens=1000, now=NOW)
    assert state["version"] == RUN_LIMIT_VERSION and state["limit_state"] == "reached"
    assert state["source"] == "model_attempt" and state["model"] == "cloud_fast"
    assert state["observed_at"] == "2026-11-25T11:00:00+00:00"
    assert state["resets_at"] == "2026-11-25T18:00:00+00:00" and state["reset_confidence"] == "high"
    assert state["requires_decision"] is True and state["executes"] is False and state["enables"] is False


def test_a_limit_without_a_reset_keeps_the_reset_unknown():
    state = run_limit_state(services(), run_of(attempt()), now=NOW)
    assert state["limit_state"] == "reached" and state["resets_at"] is None and state["reset_confidence"] == "none"


def test_no_failure_or_another_failure_is_unknown_never_exhausted():
    for events in ([], [attempt(error="StructuredOutputError")]):
        state = run_limit_state(services(), run_of(*events), now=NOW)
        assert state["limit_state"] == "unknown" and state["candidates"] == [] and state["requires_decision"] is False


def test_a_local_model_has_no_provider_quota():
    state = run_limit_state(services(), run_of(attempt(model_id="local_default", provider="ollama")), now=NOW)
    assert state["limit_state"] == "not_applicable" and state["candidates"] == []


def test_a_disabled_candidate_is_not_offered_and_cloud_needs_the_persons_confirmation():
    plain = run_limit_state(services(), run_of(attempt()), now=NOW)
    assert [item["id"] for item in plain["candidates"]] == ["local_default"]
    confirmed = run_limit_state(services(), run_of(attempt(), confirm_cloud=True), now=NOW)
    assert [item["id"] for item in confirmed["candidates"]] == ["local_default", "cloud_other"]
    assert "cloud_off" not in {item["id"] for item in confirmed["candidates"]} and "cloud_fast" not in {
        item["id"] for item in confirmed["candidates"]}


def test_cost_and_context_are_null_when_they_cannot_be_known():
    state = run_limit_state(services(), run_of(attempt(), confirm_cloud=True), now=NOW)
    assert state["estimated_input_tokens"] is None
    assert all(item["estimated_cost"] is None for item in state["candidates"])
    sized = run_limit_state(services(), run_of(attempt(), confirm_cloud=True), estimated_input_tokens=500_000, now=NOW)
    by_id = {item["id"]: item for item in sized["candidates"]}
    assert by_id["cloud_other"]["estimated_cost"] == {"input_only": True, "amount": 1.0, "currency": "USD"}
    assert by_id["local_default"]["estimated_cost"] is None            # no catalog price: null, not zero
