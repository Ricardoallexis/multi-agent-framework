"""Persistent run behavior required of each registered execution backend."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from multiagent.backends import CAPABILITY_EVENTS, REQUIRED_EVENTS
from multiagent.contracts import ExecutionMode, HumanStepSubmission, OUTPUT_SCHEMAS
from multiagent.errors import HumanSubmissionError
from multiagent.worker import LocalWorker

from backends.conformance import (  # tests/ is on sys.path, as for session_fixtures
    advance,
    backend_system,
    create_run,
    events,
    open_peer,
    steps,
    stop_workers,
    usage,
    wait_until,
)


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


def test_function_step_conformance(system):
    run = create_run(system, "function_steps")
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert final["llm_calls"] == 1
    assert len(usage(system, run["id"])) == 1
    assert final["artifacts"][1]["data"]["steps"]["first"] == final["artifacts"][0]["data"]
    assert events(final, "step_completed")[-1]["payload"]["function"] == "core.merge_outputs"


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


def test_budget_without_artifact_reports_exhaustion(system):
    # The contract (docs/BACKENDS.md, Events): with nothing to show the run fails; the budget_exhausted event is
    # only for a run that waits with an artifact, and the run's budget_exhausted flag says why it failed.
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
    assert not events(stopped, CAPABILITY_EVENTS["budgets"][0])


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
    assert callable(getattr(system.backend, "check_human_step", None))
    run = create_run(system, "human_step", step_modes={"human": ExecutionMode.HUMAN_GUIDED})
    paused = advance(system, run["id"])
    assert paused["status"] == "waiting_human"
    assert paused["waiting_reason"] == "execute_step"
    assert events(paused, CAPABILITY_EVENTS["human_steps"][0])
    before = (paused["current_step"], len(paused["artifacts"]))
    raw_before = list(system.settings.runs_dir.rglob("*_HUMAN_RAW*"))
    bad_check = system.backend.check_human_step(run["id"], "{}")
    assert bad_check["ok"] is False and bad_check["contract"] == "ContentOutput"
    assert bad_check["problems"] and any(problem["path"] != "$" for problem in bad_check["problems"])
    assert system.store.get_run(run["id"])["events"] == paused["events"]
    assert list(system.settings.runs_dir.rglob("*_HUMAN_RAW*")) == raw_before
    with pytest.raises(HumanSubmissionError):
        system.service.human_submit(run["id"], HumanStepSubmission(raw_response="not json"))
    unchanged = system.store.get_run(run["id"])
    assert (unchanged["current_step"], len(unchanged["artifacts"])) == before
    response = system.adapter.generate_structured(
        spec=None, prompt="", output_schema=OUTPUT_SCHEMAS["ContentOutput"],
    )
    assert system.backend.check_human_step(run["id"], response.raw_text) == {
        "ok": True, "contract": "ContentOutput", "problems": [],
    }
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


def test_two_workers_execute_queued_runs_once(system):
    run_ids = [create_run(system)["id"] for _ in range(4)]
    peer = open_peer(system)
    system.gate.hold_all()
    worker_a = LocalWorker(
        system.store, system.backend, poll_seconds=0.03, concurrency=1, shutdown_wait_seconds=10,
    )
    worker_b = LocalWorker(
        peer.store, peer.backend, poll_seconds=0.03, concurrency=1, shutdown_wait_seconds=10,
    )
    try:
        worker_a.start()
        worker_b.start()
        held = system.gate.wait_count(2)
        assert len(set(held)) == 2
        assert {system.store.get_run(rid)["worker_id"] for rid in held} == {
            worker_a.worker_id, worker_b.worker_id,
        }
        remaining = [rid for rid in run_ids if rid not in held]
        assert all(system.store.get_run(rid)["status"] == "queued" for rid in remaining)

        system.gate.release_all()
        wait_until(
            lambda: all(system.store.get_run(rid)["status"] == "completed" for rid in run_ids),
            message="Not all queued runs completed across two workers",
        )
    finally:
        stop_workers(system, worker_a, worker_b)

    for rid in run_ids:
        final = system.store.get_run(rid)
        assert final["status"] == "completed"
        assert len(events(final, "run_started")) == 1
        assert len(events(final, "workflow_completed")) == 1
        assert [(e["payload"]["step_id"], e["payload"]["attempt"]) for e in events(final, "step_started")] == [
            ("first", 1), ("second", 1),
        ]
        assert [(s["step_id"], s["status"]) for s in steps(system, rid)] == [
            ("first", "completed"), ("second", "completed"),
        ]


def test_expired_lease_recovered_while_live_run_survives(system):
    requires(system, "recovery")
    now_real = datetime.now(timezone.utc)
    t_start = now_real - timedelta(seconds=15)
    t_renew = now_real - timedelta(seconds=10)
    dead_hb = now_real - timedelta(seconds=65)
    lease_seconds = 60
    clock_a = [t_start]

    dead = create_run(system)
    system.store.update_run(
        dead["id"], status="running", worker_id="crashed-worker", heartbeat_at=dead_hb.isoformat(),
    )
    live = create_run(system)
    system.gate.hold(live["id"])

    worker_a = LocalWorker(
        system.store, system.backend, poll_seconds=0.03, concurrency=1,
        lease_seconds=lease_seconds, shutdown_wait_seconds=10,
        clock=lambda: clock_a[0].isoformat(),
    )
    peer = open_peer(system)
    worker_b = LocalWorker(
        peer.store, peer.backend, poll_seconds=0.03, concurrency=1,
        lease_seconds=lease_seconds, shutdown_wait_seconds=10,
        clock=lambda: now_real.isoformat(),
    )
    try:
        worker_a.start()
        system.gate.wait_entered(live["id"])
        clock_a[0] = t_renew
        wait_until(
            lambda: system.store.get_run(live["id"])["heartbeat_at"] == t_renew.isoformat(),
            message="Live worker did not renew lease",
        )
        assert system.store.get_run(dead["id"])["status"] == "running"

        worker_b.start()
        wait_until(
            lambda: peer.store.get_run(dead["id"])["status"] == "completed",
            message="Expired run was not recovered and completed by peer worker",
        )
        live_mid = peer.store.get_run(live["id"])
        assert live_mid["status"] == "running"
        assert live_mid["worker_id"] == worker_a.worker_id
        assert not events(live_mid, "run_interrupted")

        system.gate.release(live["id"])
        wait_until(
            lambda: system.store.get_run(live["id"])["status"] == "completed",
            message="Live run did not complete after release",
        )
    finally:
        stop_workers(system, worker_a, worker_b)

    dead_final = system.store.get_run(dead["id"])
    assert dead_final["status"] == "completed"
    assert len(events(dead_final, "run_interrupted")) == 1
    assert events(dead_final, "run_interrupted")[0]["payload"]["previous_worker_id"] == "crashed-worker"
    assert len(events(dead_final, "run_recovered")) == 1
    assert len(events(dead_final, "run_started")) == 1
    assert [(s["step_id"], s["status"]) for s in steps(system, dead["id"])] == [
        ("first", "completed"), ("second", "completed"),
    ]

    live_final = system.store.get_run(live["id"])
    assert live_final["status"] == "completed"
    assert not events(live_final, "run_interrupted")
    assert not events(live_final, "run_recovered")
    assert len(events(live_final, "run_started")) == 1
    assert [(s["step_id"], s["status"]) for s in steps(system, live["id"])] == [
        ("first", "completed"), ("second", "completed"),
    ]


def test_max_active_per_project_serializes_same_project_and_allows_other_project(system):
    alpha_1 = create_run(system, project_name="Alpha")
    alpha_2 = create_run(system, project_name="Alpha")
    beta = create_run(system, project_name="Beta")
    alpha_ids = {alpha_1["id"], alpha_2["id"]}
    system.gate.hold(alpha_1["id"], alpha_2["id"], beta["id"])

    peer = open_peer(system)
    worker_a = LocalWorker(
        system.store, system.backend, poll_seconds=0.03, concurrency=2,
        max_active_per_project=1, shutdown_wait_seconds=10,
    )
    worker_b = LocalWorker(
        peer.store, peer.backend, poll_seconds=0.03, concurrency=2,
        max_active_per_project=1, shutdown_wait_seconds=10,
    )
    try:
        worker_a.start()
        worker_b.start()
        system.gate.wait_entered(beta["id"])
        entered = system.gate.wait_count(2)
        active_alphas = [rid for rid in entered if rid in alpha_ids]
        assert len(active_alphas) == 1
        alpha_first = active_alphas[0]
        alpha_second = next(rid for rid in alpha_ids if rid != alpha_first)

        assert system.store.get_run(beta["id"])["status"] == "running"
        assert system.store.get_run(alpha_first)["status"] == "running"
        waiting = system.store.get_run(alpha_second)
        assert waiting["status"] == "queued"
        assert waiting["worker_id"] in ("", None)
        assert not events(waiting, "run_started")

        system.gate.release(alpha_first)
        wait_until(
            lambda: system.store.get_run(alpha_first)["status"] == "completed",
            message="First Alpha run did not complete",
        )
        system.gate.wait_entered(alpha_second)
        assert system.store.get_run(beta["id"])["status"] == "running"

        system.gate.release(beta["id"], alpha_second)
        wait_until(
            lambda: all(
                system.store.get_run(rid)["status"] == "completed"
                for rid in (alpha_1["id"], alpha_2["id"], beta["id"])
            ),
            message="Not all project runs completed",
        )
    finally:
        stop_workers(system, worker_a, worker_b)

    recorded = system.gate.recorded_events()
    idx_first_done = next(
        i for i, (rid, kind, _) in enumerate(recorded)
        if rid == alpha_first and kind == "workflow_completed"
    )
    idx_second_start = next(
        i for i, (rid, kind, _) in enumerate(recorded)
        if rid == alpha_second and kind == "run_started"
    )
    idx_beta_start = next(
        i for i, (rid, kind, _) in enumerate(recorded)
        if rid == beta["id"] and kind == "run_started"
    )
    assert idx_beta_start < idx_first_done < idx_second_start
    for rid in (alpha_1["id"], alpha_2["id"], beta["id"]):
        final = system.store.get_run(rid)
        assert final["status"] == "completed"
        assert len(events(final, "run_started")) == 1


def test_cancel_one_run_while_another_runs_concurrently(system):
    requires(system, "cancellation")
    to_cancel = create_run(system)
    to_complete = create_run(system)
    system.gate.hold(to_cancel["id"], to_complete["id"])

    worker = LocalWorker(
        system.store, system.backend, poll_seconds=0.03, concurrency=2, shutdown_wait_seconds=10,
    )
    try:
        worker.start()
        system.gate.wait_entered(to_cancel["id"])
        system.gate.wait_entered(to_complete["id"])
        assert system.store.get_run(to_cancel["id"])["status"] == "running"
        assert system.store.get_run(to_complete["id"])["status"] == "running"

        system.service.cancel(to_cancel["id"])
        system.gate.release(to_cancel["id"], to_complete["id"])
        wait_until(
            lambda: (
                system.store.get_run(to_cancel["id"])["status"] == "cancelled"
                and system.store.get_run(to_complete["id"])["status"] == "completed"
            ),
            message="Concurrent cancel and completion did not settle",
        )
    finally:
        stop_workers(system, worker)

    cancelled = system.store.get_run(to_cancel["id"])
    assert cancelled["status"] == "cancelled"
    assert len(events(cancelled, "workflow_cancelled")) == 1
    assert [e["payload"]["step_id"] for e in events(cancelled, "step_started")] == ["first"]
    assert not any(a["step_id"] == "second" for a in cancelled["artifacts"])

    completed = system.store.get_run(to_complete["id"])
    assert completed["status"] == "completed"
    assert len(events(completed, "workflow_completed")) == 1
    assert [(s["step_id"], s["status"]) for s in steps(system, to_complete["id"])] == [
        ("first", "completed"), ("second", "completed"),
    ]
