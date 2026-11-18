"""Persistent run behavior required of each registered execution backend."""
from __future__ import annotations

import json

import pytest

from multiagent.backends import CAPABILITY_EVENTS, REQUIRED_EVENTS
from multiagent.contracts import ExecutionMode, HumanStepSubmission, OUTPUT_SCHEMAS
from multiagent.errors import HumanSubmissionError

from backends.conformance import advance, backend_system, create_run, events, steps, usage  # tests/ is on sys.path, as for session_fixtures


@pytest.fixture(params=["native", "mock"])
def system(request, tmp_path):
    # Keep the public parametrization by backend name; backend_system builds a fresh store for each case.
    return request.getfixturevalue("backend_system")


def requires(system, capability):
    if capability not in system.backend.capabilities:
        pytest.skip(f"{system.name} does not declare {capability}")


def event_types(run):
    return [event["event_type"] for event in run["events"]]


def test_events_and_step_artifacts(system):
    run = create_run(system)
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    kinds = event_types(final)
    for required in ("run_started", "step_started", "step_completed", "workflow_completed"):
        assert required in REQUIRED_EVENTS
        assert required in kinds
    assert kinds.index("run_started") < kinds.index("step_started")
    assert kinds[-1] == "workflow_completed"
    started = events(final, "step_started")
    completed = events(final, "step_completed")
    assert [(e["payload"]["step_id"], e["payload"]["attempt"]) for e in started] == [
        ("first", 1), ("second", 1),
    ]
    assert [(e["payload"]["step_id"], e["payload"]["attempt"]) for e in completed] == [
        ("first", 1), ("second", 1),
    ]
    for begun, done in zip(started, completed):
        assert begun["seq"] < done["seq"]
    assert completed[0]["seq"] < started[1]["seq"]
    assert [(a["step_id"], a["attempt"]) for a in final["artifacts"]] == [
        ("first", 1), ("second", 1),
    ]
    assert [(s["step_id"], s["attempt"], s["status"]) for s in steps(system, run["id"])] == [
        ("first", 1, "completed"), ("second", 1, "completed"),
    ]


def test_usage_for_each_model_call(system):
    requires(system, "usage")
    run = create_run(system)
    final = advance(system, run["id"])
    records = usage(system, run["id"])
    assert len(records) == final["llm_calls"] == 2
    assert [(row["run_id"], row["step_id"], row["attempt"]) for row in records] == [
        (run["id"], "first", 1), (run["id"], "second", 1),
    ]


@pytest.mark.parametrize("limit", ["calls", "time"])
def test_budget_stops_before_second_call(system, limit):
    requires(system, "budgets")
    run = create_run(system, "checkpoint")
    first = advance(system, run["id"])
    assert first["waiting_reason"] == "review"
    system.service.approve(run["id"])
    if limit == "calls":
        with system.store.db.connect() as conn:
            conn.execute("UPDATE runs SET max_llm_calls=1 WHERE id=?", (run["id"],))
            conn.commit()
    else:
        system.store.update_run(run["id"], active_seconds=first["max_run_seconds"] + 1)
    stopped = advance(system, run["id"])
    assert stopped["status"] == "waiting_human"
    assert stopped["waiting_reason"] == "budget_exhausted"
    assert stopped["budget_exhausted"] == 1
    assert stopped["llm_calls"] == 1
    assert [(a["step_id"], a["attempt"]) for a in stopped["artifacts"]] == [("first", 1)]
    assert not events(stopped, "step_started")[-1]["payload"]["step_id"] == "second"
    assert len(usage(system, run["id"])) == 1
    assert events(stopped, CAPABILITY_EVENTS["budgets"][0])


def test_budget_without_artifact_reports_exhaustion(system, request):
    requires(system, "budgets")
    run = create_run(system)
    system.store.update_run(run["id"], active_seconds=run["max_run_seconds"] + 1)
    stopped = advance(system, run["id"])
    assert stopped["status"] == "failed"
    assert stopped["budget_exhausted"] == 1
    assert stopped["llm_calls"] == 0
    assert not stopped["artifacts"]
    assert not usage(system, run["id"])
    assert events(stopped, "workflow_failed")
    if system.name == "native":
        request.node.add_marker(pytest.mark.xfail(
            strict=True,
            reason="Native engine logs workflow_failed but omits budget_exhausted without an artifact",
        ))
    assert events(stopped, CAPABILITY_EVENTS["budgets"][0])


def test_cancel_queued_run(system):
    requires(system, "cancellation")
    run = create_run(system)
    cancelled = system.service.cancel(run["id"])
    assert cancelled["status"] == "cancelled"
    assert "workflow_cancelled" in REQUIRED_EVENTS
    assert len(events(cancelled, "workflow_cancelled")) == 1
    assert not system.worker.process_once()
    assert not usage(system, run["id"])


def test_cancel_between_steps(system):
    requires(system, "cancellation")
    run = create_run(system)
    def cancel_after_first(event_run_id, kind, payload, event_id):
        if event_run_id == run["id"] and kind == "step_completed" and payload["step_id"] == "first":
            system.service.cancel(run["id"])

    system.store.db.event_listeners.append(cancel_after_first)
    final = advance(system, run["id"])
    assert final["status"] == "cancelled"
    assert len(events(final, "workflow_cancelled")) == 1
    assert [e["payload"]["step_id"] for e in events(final, "step_started")] == ["first"]
    assert not any(a["step_id"] == "second" for a in final["artifacts"])
    assert len(usage(system, run["id"])) <= 1


def test_human_step_validation_and_resume(system):
    requires(system, "human_steps")
    run = create_run(system, "human_step", step_modes={"human": ExecutionMode.HUMAN_GUIDED})
    paused = advance(system, run["id"])
    assert paused["status"] == "waiting_human"
    assert paused["waiting_reason"] == "execute_step"
    assert events(paused, CAPABILITY_EVENTS["human_steps"][0])
    before = (paused["current_step"], len(paused["artifacts"]))
    with pytest.raises(HumanSubmissionError):
        system.service.human_submit(run["id"], HumanStepSubmission(raw_response="not json"))
    unchanged = system.store.get_run(run["id"])
    assert (unchanged["current_step"], len(unchanged["artifacts"])) == before
    response = system.adapter.generate_structured(
        spec=None, prompt="", output_schema=OUTPUT_SCHEMAS["ContentOutput"],
    )
    queued = system.service.human_submit(
        run["id"], HumanStepSubmission(raw_response=response.raw_text, provider="fixture_human"),
    )
    assert queued["status"] == "queued"
    assert events(queued, CAPABILITY_EVENTS["human_steps"][1])
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert [(a["step_id"], a["attempt"]) for a in final["artifacts"]] == [
        ("human", 1), ("second", 1),
    ]


def test_checkpoint_approval_and_rejection(system):
    requires(system, "review")
    run = create_run(system, "checkpoint")
    review = advance(system, run["id"])
    assert (review["status"], review["waiting_reason"], review["waiting_step"]) == (
        "waiting_human", "review", "first",
    )
    assert events(review, CAPABILITY_EVENTS["review"][0])
    approved = system.service.approve(run["id"])
    assert approved["status"] == "queued"
    assert advance(system, run["id"])["status"] == "completed"

    revised_run = create_run(system, "checkpoint")
    advance(system, revised_run["id"])
    comment = "Clarify the durable handoff"
    queued = system.service.changes(revised_run["id"], comment)
    assert queued["status"] == "queued"
    retry = advance(system, revised_run["id"])
    assert (retry["waiting_reason"], retry["waiting_step"], retry["waiting_attempt"]) == (
        "review", "first", 2,
    )
    assert [(a["step_id"], a["attempt"]) for a in retry["artifacts"]] == [
        ("first", 1), ("first", 2),
    ]
    recorded = [s for s in steps(system, revised_run["id"]) if s["step_id"] == "first"]
    assert [s["attempt"] for s in recorded] == [1, 2]
    assert comment in json.loads(recorded[1]["input_json"])["revision_feedback"]
    system.service.approve(revised_run["id"])
    assert advance(system, revised_run["id"])["status"] == "completed"

    rejected_run = create_run(system, "checkpoint")
    advance(system, rejected_run["id"])
    rejected = system.service.reject(rejected_run["id"], "Output is unsuitable")
    assert rejected["status"] == "rejected"
    assert rejected["revision_feedback"] == "Output is unsuitable"
    assert not system.worker.process_once()


def test_recover_running_run_from_cursor(system):
    requires(system, "recovery")
    run = create_run(system, "checkpoint")
    advance(system, run["id"])
    system.service.approve(run["id"])
    system.store.update_run(run["id"], status="running")
    assert system.store.recover_running_runs("replacement-worker") == [run["id"]]
    recovered = system.store.get_run(run["id"])
    assert (recovered["status"], recovered["current_step"]) == ("queued", 1)
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert [(a["step_id"], a["attempt"]) for a in final["artifacts"]] == [
        ("first", 1), ("second", 1),
    ]
    assert len([s for s in steps(system, run["id"]) if s["step_id"] == "first"]) == 1
