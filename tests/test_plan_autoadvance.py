# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARR3-T03: the plan of an approved proposal advances by itself when one of its runs finishes.

Real services in Mock (no model is called). Plans of two tasks, the second depending on the first, approved by a double
(``user:ana`` through the service; nobody presses "Approve" in the GUI).
"""
from __future__ import annotations

import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from multiagent.config import Settings
from multiagent.contracts import SocialPostRequest
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.plan_autoadvance import ACTOR, ADVANCED_EVENT, FAILED_EVENT, notify_task_accepted
from multiagent.tasks.commands import CommandResult

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"


@pytest.fixture
def env(tmp_path):
    general = tmp_path / "general"
    shutil.copytree(GENERAL_BUNDLE, general)
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        execution_backend="mock", mock_mode=True, gemini_api_key="",
                        openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"general": general})
    yield SimpleNamespace(services=services)
    services.runs.close()


def approved(services, group="launch", *, bundle=None, requires="submitted"):
    """Two tasks, ``second`` after ``first``; with a bundle they run in its store (and its worker), else in the system's."""
    workflow = "ask_once" if bundle else "social_post"
    inputs = {"task": "Say hi"} if bundle else {"topic": "Launch"}
    prefix = "" if group == "launch" else group + "-"       # task ids are unique across groups
    first = {"id": prefix + "first", "title": "First", "workflow": workflow, "depends_on": [], "inputs": inputs}
    second = {"id": prefix + "second", "title": "Second", "workflow": workflow, "depends_on": [prefix + "first"],
              "inputs": inputs}
    if bundle:
        first["bundle"] = second["bundle"] = bundle
    # A dependency needs the first task "accepted" by default (a person's step); these plans ask for "submitted", which
    # is what the run's end gives, so the dependent can start from the run ending alone.
    if requires:
        services.tasks.create_group(group, "Launch product", actor="user:ana", authorized=True, default_requires=requires)
    document = services.planning.propose("manual", {"group_id": group, "goal": "Launch product",
                                                    "tasks": [first, second]})
    saved = services.plan_proposals.save(document, actor="user:ana")
    services.plan_proposals.approve(saved["id"], actor="user:ana", channel="python", expected_revision=1)
    return saved["id"]


def task_views(services, proposal_id):
    plan = services.plan_proposals.get(proposal_id)["proposal"]["plan"]
    return {task["id"]: task for task in services.plans.status(plan)["tasks"]}


def plan_events(services, group="launch"):
    return [event for event in services.tasks.events(group_id=group) if event["type"] in (ADVANCED_EVENT, FAILED_EVENT)]


def run_of(services, proposal_id, task_id):
    run = task_views(services, proposal_id)[task_id]["run"]
    return run["id"] if run else None


def bundle_worker(services):
    return services.runs._bundle_runtime("general").worker


def drive_to_the_review(services, proposal_id, task_id):
    """Run the task in the system worker until it waits for the person (supervised mode)."""
    run_id = run_of(services, proposal_id, task_id)
    for _ in range(5):
        if services.runs.get(run_id)["status"] == "waiting_human":
            return run_id
        assert services.system.worker.process_once()
    return run_id


def test_worker_finish_starts_dependent_task(env):
    services = env.services
    proposal_id = approved(services, bundle="general")
    assert run_of(services, proposal_id, "second") is None
    assert bundle_worker(services).process_once()           # the first run ends in the worker; nobody calls advance
    first_run = run_of(services, proposal_id, "first")
    assert services.runs.get(first_run)["status"] == "completed"
    assert run_of(services, proposal_id, "second") is not None
    events = plan_events(services)
    assert [event["type"] for event in events] == [ADVANCED_EVENT]
    event = events[0]
    assert event["actor"] == ACTOR == "system:worker" and event["task_id"] == "first"
    assert event["payload"]["proposal_id"] == proposal_id and event["payload"]["run_id"] == first_run
    assert "second" in {item["task_id"] for item in event["payload"]["advanced"]}


def finish_by_approval(services, run_id):
    for _ in range(5):                                       # approving ends the run outside the worker
        if services.runs.get(run_id)["status"] != "waiting_human":
            break
        services.runs.approve(run_id)
    assert services.runs.get(run_id)["status"] == "completed"


def test_plan_advances_after_human_approval_of_last_step(env):
    """The real path: the group as ``approve`` creates it (``accepted``), the person approves the last step and then
    accepts the first task; the second one starts without "Advance the plan"."""
    services = env.services
    proposal_id = approved(services, requires=None)
    run_id = drive_to_the_review(services, proposal_id, "first")
    assert services.runs.get(run_id)["status"] == "waiting_human"
    assert run_of(services, proposal_id, "second") is None
    finish_by_approval(services, run_id)
    assert task_views(services, proposal_id)["first"]["state"] == "submitted"    # the run's end submitted it
    assert run_of(services, proposal_id, "second") is None                       # it needs the person's acceptance
    services.tasks.transition("first", "accept", actor="user:ana")
    assert run_of(services, proposal_id, "second") is not None
    events = plan_events(services)
    assert [event["type"] for event in events] == [ADVANCED_EVENT, ADVANCED_EVENT]
    assert "run_id" in events[0]["payload"] and "run_id" not in events[1]["payload"]
    assert events[1]["actor"] == ACTOR and events[1]["task_id"] == "first"


def test_gate_pass_advances_plan(env, monkeypatch):
    services = env.services
    services.tasks.create_group("launch", "Launch product", actor="user:ana", authorized=True)
    services.gates.set_group_gate("launch", ["tests"], actor="user:ana")
    proposal_id = approved(services, requires=None)
    finish_by_approval(services, drive_to_the_review(services, proposal_id, "first"))
    assert task_views(services, proposal_id)["first"]["state"] == "submitted" and run_of(services, proposal_id, "second") is None
    passed = CommandResult("test", "passed", 0, 0, "ok", "", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z")
    monkeypatch.setattr(services.gates, "_run_check", lambda *_args, **_kwargs: passed)
    assert services.gates.run_gate("first", actor="user:ana", cause="round 1")["outcome"] == "passed"
    assert run_of(services, proposal_id, "second") is not None
    assert [event["type"] for event in plan_events(services)] == [ADVANCED_EVENT, ADVANCED_EVENT]


def test_accept_inside_advance_does_not_reenter(env):
    services = env.services
    proposal_id = approved(services)
    calls = []
    real = services.plan_proposals.advance

    def advance_that_accepts(*args, **kwargs):
        calls.append(1)
        notify_task_accepted(services, "first")              # what an activity's submit would do inside the advance
        return real(*args, **kwargs)
    services.plan_proposals.advance = advance_that_accepts
    done = threading.Event()

    def work():
        services.plan_autoadvance.task_finished("first")
        done.set()
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    assert done.wait(15), "the hook re-entered and blocked on the group lock"
    thread.join(timeout=5)
    assert calls == [1]
    notify_task_accepted(services, "first")                  # and the flag is cleared afterwards
    assert calls == [1, 1]


@pytest.mark.parametrize("how", ["reject", "cancel"])
def test_reject_and_cancel_also_advance(env, how):
    services = env.services
    proposal_id = approved(services)
    run_id = drive_to_the_review(services, proposal_id, "first")
    result = services.runs.reject(run_id, "no") if how == "reject" else services.runs.cancel(run_id, force=True)
    assert result["status"] in {"rejected", "cancelled", "failed"}
    views = task_views(services, proposal_id)
    assert views["first"]["state"] == "failed"               # the failure is recorded, the dependent does not start
    assert views["second"]["run"] is None
    assert [event["type"] for event in plan_events(services)] == [ADVANCED_EVENT]


def test_idempotent(env):
    services = env.services
    proposal_id = approved(services, bundle="general")
    assert bundle_worker(services).process_once()
    first_run, second_run = run_of(services, proposal_id, "first"), run_of(services, proposal_id, "second")
    assert second_run
    assert services.plan_autoadvance.run_finished(first_run)["nothing_to_advance"] is True
    assert run_of(services, proposal_id, "second") == second_run
    assert len(plan_events(services)) == 1


def test_two_runs_finish_together(env):
    services = env.services
    left = approved(services, "left", bundle="general")
    right = approved(services, "right", bundle="general")
    assert bundle_worker(services).process_once() and bundle_worker(services).process_once()
    runs = [run_of(services, left, "left-first"), run_of(services, right, "right-first")]
    second_runs = [run_of(services, left, "left-second"), run_of(services, right, "right-second")]
    assert all(second_runs)
    start, errors = threading.Barrier(4), []

    def finish(run_id):
        try:
            start.wait(timeout=10)
            services.plan_autoadvance.run_finished(run_id)
        except Exception as exc:   # noqa: BLE001 - reported by the assertion below
            errors.append(exc)
    threads = [threading.Thread(target=finish, args=(run_id,), daemon=True) for run_id in runs * 2]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors and not any(thread.is_alive() for thread in threads)
    assert [run_of(services, left, "left-second"), run_of(services, right, "right-second")] == second_runs
    assert len(plan_events(services, "left")) == 1 and len(plan_events(services, "right")) == 1


def test_conflict_retries_once_then_records_failure(env):
    services = env.services
    proposal_id = approved(services, bundle="general")
    calls = []

    def conflict(*_args, **_kwargs):
        calls.append(1)
        raise ServiceError("invalid_state", "busy", status=409)
    services.plan_proposals.advance = conflict
    assert bundle_worker(services).process_once()
    assert len(calls) == 2                                   # one try and one retry
    first_run = run_of(services, proposal_id, "first")
    assert services.runs.get(first_run)["status"] == "completed"
    events = plan_events(services)
    assert [event["type"] for event in events] == [FAILED_EVENT]
    assert events[0]["payload"]["reason"] and events[0]["payload"]["run_id"] == first_run
    assert events[0]["actor"] == ACTOR and events[0]["task_id"] == "first"


def test_a_failing_hook_never_changes_the_run_or_the_answer(env):
    services = env.services
    proposal_id = approved(services)
    run_id = drive_to_the_review(services, proposal_id, "first")

    def boom(_run_id):
        raise RuntimeError("hook down")
    services.plan_autoadvance.run_finished = boom
    result = services.runs.approve(run_id)
    assert result["id"] == run_id and result["status"] != "failed"


def test_run_without_plan_is_ignored(env):
    services = env.services
    run = services.runs.create(SocialPostRequest(topic="Launch", objective="Announce", project_name="solo project"), bundle=None)
    assert services.system.worker.process_once()
    assert services.plan_autoadvance.run_finished(run["id"]) is None
    assert plan_events(services) == []


def test_system_worker_only_in_advance(env):
    services = env.services
    document = services.planning.propose("manual", {"group_id": "launch", "goal": "Launch product", "tasks": [
        {"id": "first", "title": "First", "workflow": "social_post", "depends_on": [], "inputs": {"topic": "Launch"}}]})
    saved = services.plan_proposals.save(document, actor="user:ana")
    with pytest.raises(ServiceError) as approve:
        services.plan_proposals.approve(saved["id"], actor="system:worker", channel="worker", expected_revision=1)
    assert approve.value.status == 422
    with pytest.raises(ServiceError) as reject:
        services.plan_proposals.reject(saved["id"], actor="system:worker", channel="worker", note="no")
    assert reject.value.status == 422
    with pytest.raises(ServiceError) as other:
        services.plan_proposals.advance(saved["id"], actor="system:worker2", channel="worker")
    assert other.value.status == 422
    with pytest.raises(ServiceError) as not_approved:        # accepted as an actor; the proposal is what stops it
        services.plan_proposals.advance(saved["id"], actor="system:worker", channel="worker")
    assert not_approved.value.details["reason"] == "not_approved"
