# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PAR-T03: the queue, the executor apps and agy follow the saved concurrency mode.

No real worker app, no real agy and no network: the worker runs a blocking engine, the apps are the generated fake
programs of the neighbouring tests, and the quota the calculator reads is a stub. Without a saved mode nothing here
changes, so each behaviour is shown with the mode on and once with it off.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from multiagent.config import Settings
from multiagent.contracts import RunStatus
from multiagent.executors import antigravity as agy
from multiagent.executors.antigravity import AntigravityAdapter, NOTICE_ID
from multiagent.executors.contract import RunStatus as ExecutorRunStatus
from multiagent.executors.selection import MODE_LEVEL_NOTICE, mode_level_notice, select_for_executor, select_for_resume
from multiagent.routing.assignment import AssignmentRequest
from multiagent.services import availability
from multiagent.services.availability import AvailabilityPolicy
from multiagent.services.concurrency import ConcurrencyServices
from multiagent.services.gui_settings import GuiSettingsServices
from multiagent.worker import LocalWorker, concurrency_limit_source

from test_executor_services import claude_out, event_types, never_a_real_cli, refused, world  # noqa: F401  (fixtures)
from test_executor_antigravity import (  # noqa: F401  (fixtures and helpers)
    MODEL, agy_world, fake, good_run, make_adapter, make_request, never_the_real_agy, service_run, usage_text, workdir,
)
from test_worker_concurrency import BlockingEngine, queued, stores


# --------------------------------------------------------------------------- the worker and the queue

def policy(limit, *, per_project=None, reason="test reason", code="quota_window"):
    return lambda: {"limit": limit, "per_project": per_project, "reason": reason, "code": code}


def wait_for(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert condition(), "the condition did not hold in time"


def test_without_a_policy_the_worker_keeps_its_static_limits(tmp_path):
    store, _ = stores(tmp_path)
    for _ in range(3):
        queued(store, project="one")
    worker = LocalWorker(store, BlockingEngine(store), concurrency=1, limit_source=lambda: None)
    assert worker._caps(worker._policy()) == (1, None, None)
    assert worker.process_once() is True                       # one run, as always (the engine here ends at once)


def test_a_limit_of_zero_waits_for_a_turn_and_nothing_fails(tmp_path):
    store, _ = stores(tmp_path)
    first, second = queued(store), queued(store)
    reason = "codex (five_hour): the 10% margin is reached, no new step starts"
    state = {"limit": 0}
    engine = BlockingEngine(store)
    engine.release.set()
    worker = LocalWorker(store, engine, limit_source=lambda: {"limit": state["limit"], "reason": reason,
                                                               "code": "quota_margin_reached", "per_project": 1})
    assert worker.process_once() is False and worker.process_once() is False
    assert [store.get_run(run)["status"] for run in (first, second)] == [RunStatus.QUEUED.value] * 2
    assert store.get_run(first)["error"] in (None, "") and engine.calls == []
    shown = worker.queue_state()
    assert shown["waiting"] is True and shown["limit"] == 0 and shown["reason"] == reason
    assert shown["code"] == "quota_margin_reached"
    state["limit"] = 1                                          # the turn comes: the run starts, it was never failed
    assert worker.process_once() is True
    assert store.get_run(first)["status"] == RunStatus.COMPLETED.value


def test_the_policy_caps_the_total_and_one_run_per_project(tmp_path):
    store, _ = stores(tmp_path)
    ids = [queued(store, project="alpha"), queued(store, project="alpha"), queued(store, project="beta")]
    engine = BlockingEngine(store, expected=2)
    worker = LocalWorker(store, engine, poll_seconds=0.06, concurrency=1, limit_source=policy(2, per_project=1))
    worker.start()
    try:
        assert engine.started.wait(10)
        time.sleep(0.3)
        assert len(engine.calls) == 2                           # two at once although the static concurrency is 1
        assert {store.get_run(run)["project_id"] for run in engine.calls} == {
            store.get_run(ids[0])["project_id"], store.get_run(ids[2])["project_id"]}
        assert store.get_run(ids[1])["status"] == RunStatus.QUEUED.value      # same project as the first: it waits
    finally:
        engine.release.set()
        worker.stop()
    assert store.run_counts()["open"] == 1 or store.get_run(ids[1])["status"] in {
        RunStatus.COMPLETED.value, RunStatus.QUEUED.value}


def test_lowering_the_limit_never_cuts_a_running_run(tmp_path):
    store, _ = stores(tmp_path)
    running = [queued(store, project="alpha"), queued(store, project="bravo")]
    later = queued(store, project="charlie")
    engine = BlockingEngine(store, expected=2)
    state = {"limit": 2}
    worker = LocalWorker(store, engine, poll_seconds=0.06,
                         limit_source=lambda: {"limit": state["limit"], "per_project": 1, "reason": "r", "code": "c"})
    worker.start()
    try:
        assert engine.started.wait(10)
        state["limit"] = 0                                      # the cap drops while both run
        time.sleep(0.4)
        assert store.get_run(later)["status"] == RunStatus.QUEUED.value
        assert all(store.get_run(run)["status"] == RunStatus.RUNNING.value for run in running)
        engine.release.set()                                    # they finish by themselves, never cancelled
        wait_for(lambda: all(store.get_run(run)["status"] == RunStatus.COMPLETED.value for run in running))
        time.sleep(0.3)
        assert store.get_run(later)["status"] == RunStatus.QUEUED.value      # still waiting for its turn
    finally:
        engine.release.set()
        worker.stop()


def test_a_claim_with_a_total_of_zero_takes_nothing(tmp_path):
    store, _ = stores(tmp_path)
    queued(store)
    assert store.claim_queued_run("w", lease_seconds=30, max_active_total=0) is None
    assert store.run_counts() == {"queued": 1, "open": 1}
    assert store.claim_queued_run("w", lease_seconds=30, max_active_total=1)["status"] == RunStatus.QUEUED.value


def test_the_static_caps_still_win_when_they_are_lower(tmp_path):
    store, _ = stores(tmp_path)
    worker = LocalWorker(store, BlockingEngine(store), max_active_total=2, max_active_per_project=3,
                         limit_source=policy(8, per_project=1))
    assert worker._caps(worker._policy()) == (8, 2, 1)
    worker = LocalWorker(store, BlockingEngine(store), max_active_per_project=1,
                         limit_source=policy(8, per_project=4))
    assert worker._caps(worker._policy()) == (8, 8, 1)


def test_a_policy_that_breaks_falls_back_to_the_static_limits(tmp_path):
    store, _ = stores(tmp_path)

    def broken():
        raise RuntimeError("usage files unreadable")

    worker = LocalWorker(store, BlockingEngine(store), concurrency=3, limit_source=broken)
    assert worker._caps(worker._policy()) == (3, None, None)
    assert LocalWorker(store, BlockingEngine(store), limit_source=lambda: {"limit": -1})._policy() is None


def test_the_end_of_a_batch_ends_a_temporary_mode_that_lasts_tasks(tmp_path):
    store, _ = stores(tmp_path)
    gui = GuiSettingsServices(SimpleNamespace(settings=SimpleNamespace(local_dir=tmp_path / "gui"),
                                              usage=SimpleNamespace(plans=lambda: {"apps": []})))
    gui.set_concurrency({"mode": "maximum", "duration": "tasks"})
    first, second = queued(store), queued(store)
    engine = BlockingEngine(store)
    engine.release.set()
    worker = LocalWorker(store, engine, on_batch_complete=gui.complete_temporary_tasks)
    assert worker.process_once() is True
    assert gui.concurrency()["mode"] == "maximum"               # a run is still queued: the batch goes on
    assert worker.process_once() is True
    assert gui.concurrency()["mode"] == "normal" and gui.concurrency()["duration"] is None
    assert store.get_run(first)["status"] == store.get_run(second)["status"] == RunStatus.COMPLETED.value
    gui.set_concurrency({"mode": "performance", "duration": "two_hours"})
    queued(store)
    assert worker.process_once() is True
    assert gui.concurrency()["mode"] == "performance"           # only "tasks" ends with the batch


def test_a_failing_batch_hook_never_breaks_the_worker(tmp_path):
    store, _ = stores(tmp_path)
    queued(store)
    engine = BlockingEngine(store)
    engine.release.set()

    def boom():
        raise RuntimeError("settings file locked")

    worker = LocalWorker(store, engine, on_batch_complete=boom)
    assert worker.process_once() is True and worker.process_once() is False


def _gui_services(tmp_path, plans=lambda: {"apps": []}, **fields):
    services = SimpleNamespace(settings=Settings(_env_file=None, local_dir=tmp_path, **fields),
                               usage=SimpleNamespace(plans=plans))
    services.gui_settings = GuiSettingsServices(services)
    services.concurrency = ConcurrencyServices(services)
    return services


def quota(app="claude", used=50, minutes=120):
    reset = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return {"apps": [{"app": app, "windows": [{"window": "five_hour", "used_percent": used,
                                                 "resets_at": reset.isoformat()}]}]}


def test_the_limit_source_is_silent_until_a_mode_is_saved_and_then_follows_it(tmp_path):
    services = _gui_services(tmp_path, plans=lambda: quota(used=95))
    clock = {"now": 0.0}
    source = concurrency_limit_source(services, ttl_seconds=5, monotonic=lambda: clock["now"])
    assert source() is None                                     # no mode saved: the static limits apply
    services.gui_settings.set_concurrency({"mode": "normal"})
    assert source() is None                                     # cached for the ttl
    clock["now"] = 6.0
    value = source()
    assert value["limit"] == 0 and value["code"] == "quota_margin_reached" and value["per_project"] == 1
    assert "margin is reached" in value["reason"]
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 3})
    clock["now"] = 20.0
    assert source()["limit"] == min(3, max(1, __import__("os").cpu_count() or 1))


def test_one_per_project_unless_the_environment_says_otherwise(tmp_path):
    services = _gui_services(tmp_path, worker_max_active_per_project=2)
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 2})
    assert concurrency_limit_source(services)()["per_project"] == 2


# --------------------------------------------------------------------------- the level of the mode in the executors

def test_the_mode_level_notice_is_pure_and_follows_the_mode_bounds():
    assert mode_level_notice("eco", "T3") == f"{MODE_LEVEL_NOTICE}:eco"           # work stays at T2 or below in Eco
    assert mode_level_notice("eco", "T2") is None and mode_level_notice("eco", "T1") is None
    assert mode_level_notice("eco", "T2", step_kind="review") == f"{MODE_LEVEL_NOTICE}:eco"    # review stays at T3 or above
    assert mode_level_notice("eco", "T3", step_kind="planning") is None
    assert mode_level_notice("maximum", "T2") == f"{MODE_LEVEL_NOTICE}:maximum"
    assert mode_level_notice("maximum", "T3") is None
    assert mode_level_notice("maximum", "T4") == f"{MODE_LEVEL_NOTICE}:maximum"       # T4 needs the opt-in
    assert mode_level_notice("maximum", "T4", allow_t4=True) is None
    assert mode_level_notice("normal", "T1") is None and mode_level_notice("eco", None) is None


def test_only_the_persons_model_carries_the_notice_and_it_is_still_the_one_sent():
    descriptor = SimpleNamespace(model_support=SimpleNamespace(status=None, values=None),
                                 effort_support=SimpleNamespace(status=None, values=None))
    named = select_for_executor("claude_cli", descriptor, None, requested_model="named",
                                user_model_notices=(f"{MODE_LEVEL_NOTICE}:eco",))
    assert named.model == "named" and named.model_source == "user" and f"{MODE_LEVEL_NOTICE}:eco" in named.notes
    unnamed = select_for_executor("claude_cli", descriptor, None, user_model_notices=(f"{MODE_LEVEL_NOTICE}:eco",))
    assert unnamed.model is None and unnamed.notes == ()


def test_a_resume_keeps_the_model_of_the_session_whatever_the_mode():
    resumed = select_for_resume("claude_cli", model=None, effort=None, recorded_model="recorded-model",
                                recorded_effort=None)
    assert resumed.model == "recorded-model" and resumed.model_source == "session"


def test_the_router_gets_the_mode_only_when_eco_or_maximum_is_saved(world):
    services = world.services
    seen = []
    services.system.engine.router.select = lambda request, **options: seen.append(options)
    request = AssignmentRequest(task_id="T1")
    profile = {"model_routing_profile_id": "economic"}
    assert services.assignment.mode_options() == {}
    services.assignment._routing_decision(request, profile, {"effective_permissions": None})
    assert seen == [{}]                                         # no mode saved: the router is called as before
    services.gui_settings.set_concurrency({"mode": "normal"})
    assert services.assignment.mode_options() == {}
    services.gui_settings.set_concurrency({"mode": "eco"})
    services.assignment._routing_decision(request, profile, {"effective_permissions": None})
    assert seen[-1]["concurrency_mode"] == "eco" and seen[-1]["allow_t4"] is False
    assert seen[-1]["confirmed_levels"] == {}


def test_the_persons_model_beats_the_mode_with_a_notice(world):
    services = world.services
    services.gui_settings.set_concurrency({"mode": "maximum", "duration": "tasks", "selection": "manual", "manual": 4})
    plan = services.executors._plan("T1", "claude_cli", "local_default", None, None, None)   # catalog level T1, Maximum wants T3+
    assert plan.model == "local_default" and plan.selection.model_source == "user"
    assert f"{MODE_LEVEL_NOTICE}:maximum" in plan.selection.notes
    eco = services.gui_settings.set_concurrency({"mode": "eco"})
    assert eco["mode"] == "eco"
    quiet = services.executors._plan("T1", "claude_cli", "local_default", None, None, None)  # T1 fits Eco's work ceiling
    assert quiet.model == "local_default" and MODE_LEVEL_NOTICE not in " ".join(quiet.selection.notes)
    loud = services.executors._plan("T1", "claude_cli", "local_default", None, None, None, "review")
    assert f"{MODE_LEVEL_NOTICE}:eco" in loud.selection.notes and loud.model == "local_default"


def test_without_a_mode_the_persons_model_has_no_notice(world):
    plan = world.services.executors._plan("T1", "claude_cli", "local_default", None, None, None)
    assert plan.model == "local_default" and MODE_LEVEL_NOTICE not in " ".join(plan.selection.notes)


def test_a_resume_under_a_mode_keeps_the_recorded_model(world):
    services = world.services
    world.fake.set("claude", stdout=claude_out("no envelope here"))
    first = services.executors.run_task("T1", "claude_cli", "claude-opus-4-1")
    assert first["outcome"] == "failed"
    services.gui_settings.set_concurrency({"mode": "eco", "selection": "manual", "manual": 4})
    world.fake.set("claude", stdout=claude_out())
    again = services.executors.resume_task("T1")
    assert again["outcome"] == "submitted" and again["model"] == "claude-opus-4-1"
    assert again["selection"]["model_source"] == "session"


# --------------------------------------------------------------------------- the turn of a launch

def slow(world, seconds=1.5):
    world.fake.set("claude", stdout=claude_out(), sleep=seconds)


def waiting_events(services, task_id):
    return [event for event in services.tasks.events(task_id=task_id) if event["type"] == "executor.waiting_turn"]


def test_without_a_mode_a_launch_never_waits_and_leaves_no_turn_event(world):
    out = world.services.executors.run_task("T1", "claude_cli")
    assert out["outcome"] == "submitted" and waiting_events(world.services, "T1") == []
    assert world.services.executors._turns.held() == 0


def test_a_margin_reached_waits_for_a_turn_without_failing_or_changing_anything(world):
    services = world.services
    services.usage.plans = lambda: quota("claude", used=95)
    services.gui_settings.set_concurrency({"mode": "normal"})
    before = services.tasks.get_task("T1")
    out = services.executors.run_task("T1", "claude_cli")
    assert out["outcome"] == "waiting_turn" and out["run_status"] == "queued" and out["notes"] == ["waiting_turn"]
    assert out["waiting"]["code"] == "quota_margin_reached" and out["waiting"]["limit"] == 0
    assert "margin is reached" in out["evidence"] and out["executor_id"] == "claude_cli"
    after = services.tasks.get_task("T1")
    assert (after["state"], after["status"], after["attempt"], after["revision"]) == (
        before["state"], before["status"], before["attempt"], before["revision"])
    assert world.fake.calls("claude") == []                     # nothing started, no other app or model was tried
    again = services.executors.run_task("T1", "claude_cli")
    assert again["outcome"] == "waiting_turn"
    (event,) = waiting_events(services, "T1")                   # one record for the same wait, never one per poll
    assert event["payload"]["limit_on"] == "app" and event["payload"]["code"] == "quota_margin_reached"
    assert "executor.attempt_finished" not in event_types(services, "T1")
    services.usage.plans = lambda: quota("claude", used=50)    # the quota comes back: the same call now runs
    done = services.executors.run_task("T1", "claude_cli")
    assert done["outcome"] == "submitted" and len(world.fake.calls("claude")) == 1


def test_a_full_app_makes_the_next_launch_wait_and_never_cuts_the_one_that_runs(world):
    services = world.services
    slow(world)
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 4, "per_app": {"claude": 1}})
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(services.executors.run_task, "T1", "claude_cli")
        wait_for(lambda: world.fake.calls("claude"))
        waiting = services.executors.run_task("T3", "claude_cli")
        assert waiting["outcome"] == "waiting_turn" and waiting["waiting"]["limit_on"] == "app"
        assert services.tasks.get_task("T3")["state"] == "open"
        assert waiting_events(services, "T3")[0]["payload"]["limit"] == 1
        assert running.result(30)["outcome"] == "submitted"     # the running step ended on its own
    assert services.executors._turns.held() == 0               # its place was given back
    assert services.executors.run_task("T3", "claude_cli")["outcome"] == "submitted"


def test_another_app_is_not_held_by_a_full_one(world):
    services = world.services
    slow(world)
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 4, "per_app": {"claude": 1}})
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(services.executors.run_task, "T1", "claude_cli")
        wait_for(lambda: world.fake.calls("claude"))
        other = services.executors.run_task("T3", "codex_cli")
        assert other["outcome"] == "submitted" and not running.done()
        assert services.executors.run_task("T2", "claude_cli")["outcome"] == "waiting_turn"
        assert running.result(30)["outcome"] == "submitted"


def test_a_full_model_makes_the_next_launch_wait(world):
    services = world.services
    slow(world)
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 4, "per_model": {"model-a": 1}})
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(services.executors.run_task, "T1", "claude_cli", "model-a")
        wait_for(lambda: world.fake.calls("claude"))
        blocked = services.executors.run_task("T3", "claude_cli", "model-a")
        assert blocked["outcome"] == "waiting_turn" and blocked["waiting"]["limit_on"] == "model"
        free = services.executors.run_task("T2", "claude_cli", "model-b")           # another model has its own place
        assert free["outcome"] == "submitted"
        assert running.result(30)["outcome"] == "submitted"


def test_a_failure_before_the_program_gives_the_place_back(world):
    services = world.services
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 1})
    refused(lambda: services.executors.run_task("T4", "claude_cli"), reason="task_not_runnable")    # T4 is blocked
    assert services.executors._turns.held() == 0
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "submitted"


def test_a_policy_that_cannot_be_read_does_not_stop_a_launch(world):
    services = world.services
    services.gui_settings.set_concurrency({"mode": "normal"})

    def broken(**kwargs):
        raise RuntimeError("usage unreadable")

    services.concurrency.effective = broken
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "submitted"


# --------------------------------------------------------------------------- agy in slots

def run_sleeping(fake, seconds):
    fake.set(run={**service_run(), "sleep": seconds})


def test_two_agy_runs_in_different_folders_go_on_together_when_slots_are_on(fake, tmp_path):
    adapter = make_adapter(fake, tmp_path)
    adapter.slot_policy = lambda: True
    first, second = tmp_path / "task-one", tmp_path / "task-two"
    for folder in (first, second):
        folder.mkdir()
    run_sleeping(fake, 2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(adapter.launch, make_request(first))
        wait_for(lambda: adapter._lock.busy_for(first))
        assert adapter.launch_blocker() is None                  # no folder given: another folder may be free
        assert adapter.launch_blocker(second) is None and adapter.launch_blocker(first) == agy.RUN_IN_PROGRESS
        two = pool.submit(adapter.launch, make_request(second))
        wait_for(lambda: len(fake.runs()) == 2)                  # both programs were started, the first still running
        assert not one.done()
        assert one.result(30).status is ExecutorRunStatus.COMPLETED
        assert two.result(30).status is ExecutorRunStatus.COMPLETED
    assert not adapter._lock.busy_for(first) and not adapter._lock.busy_for(second)


def test_a_second_agy_run_in_the_same_folder_is_refused_even_with_slots(fake, tmp_path, workdir):
    adapter = make_adapter(fake, tmp_path)
    adapter.slot_policy = lambda: True
    run_sleeping(fake, 2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        one = pool.submit(adapter.launch, make_request(workdir))
        wait_for(lambda: adapter._lock.busy_for(workdir))
        with pytest.raises(agy.AntigravityAdapterError) as caught:
            adapter.launch(make_request(workdir))
        assert caught.value.code == agy.RUN_IN_PROGRESS
        assert one.result(30).status is ExecutorRunStatus.COMPLETED
    assert adapter.launch(make_request(workdir)).status is ExecutorRunStatus.COMPLETED


def test_without_slots_agy_keeps_its_one_lock_for_every_folder(fake, tmp_path):
    adapter = make_adapter(fake, tmp_path)
    first, second = tmp_path / "task-one", tmp_path / "task-two"
    for folder in (first, second):
        folder.mkdir()
    run_sleeping(fake, 2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        one = pool.submit(adapter.launch, make_request(first))
        wait_for(lambda: adapter._lock.path.exists() and fake.calls())
        assert adapter.launch_blocker(second) == agy.RUN_IN_PROGRESS
        with pytest.raises(agy.AntigravityAdapterError) as caught:
            adapter.launch(make_request(second))
        assert caught.value.code == agy.RUN_IN_PROGRESS
        one.result(30)


def test_the_quota_floors_still_stop_a_launch_in_a_slot(fake, tmp_path, workdir):
    adapter = make_adapter(fake, tmp_path)
    adapter.slot_policy = lambda: True
    fake.set(usage={"stdout": usage_text(gemini_week=12)})
    with pytest.raises(agy.AntigravityAdapterError) as caught:
        adapter.launch(make_request(workdir))
    assert caught.value.code == agy.QUOTA_BELOW_LAUNCH_FLOOR and fake.runs() == []
    assert not adapter._lock.busy_for(workdir)                   # the slot was given back
    fake.set(usage={"stdout": usage_text(gemini_5h=8)})
    paused = adapter.launch(make_request(workdir))
    assert paused.status is ExecutorRunStatus.QUOTA_EXHAUSTED and fake.runs() == []
    assert agy.LAUNCH_FLOOR_PERCENT == 15.0 and agy.PAUSE_FLOOR_PERCENT == 10.0


def test_a_policy_that_breaks_keeps_the_one_lock(fake, tmp_path, workdir):
    adapter = make_adapter(fake, tmp_path)

    def broken():
        raise RuntimeError("settings unreadable")

    adapter.slot_policy = broken
    assert adapter._lock.slot_mode() is False
    assert adapter.launch(make_request(workdir)).status is ExecutorRunStatus.COMPLETED


def test_the_quota_bag_of_a_model():
    assert agy.bag_of("gemini-4-argon-high") == "gemini" and agy.bag_of("claude-opus-5-5-high") == "claude_gpt"
    assert agy.bag_of("gpt-oss-120b-medium") == "claude_gpt" and agy.bag_of(None) is None and agy.bag_of("kimi-k3") is None


def test_the_services_turn_agy_slots_on_only_with_a_saved_mode(fake, tmp_path):
    services, adapter = agy_world(tmp_path, fake)
    own = [item for item in services.executors.build_adapters() if isinstance(item, AntigravityAdapter)][0]
    assert own._lock.slot_mode() is False
    services.gui_settings.set_concurrency({"mode": "normal"})
    assert own._lock.slot_mode() is True


def test_two_agy_tasks_of_one_project_take_turns_and_two_projects_run_together(fake, tmp_path):
    services, adapter = agy_world(tmp_path, fake)
    adapter.slot_policy = services.executors._mode_saved
    services.tasks.create_task("GEM", "T2", "Second", actor="user", objective="Summarize the other module.")
    services.tasks.create_group("OTH", "Other project", actor="user", authorized=True)
    services.tasks.create_task("OTH", "T3", "Elsewhere", actor="user", objective="Summarize a third module.")
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 4})
    run_sleeping(fake, 2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(services.executors.run_task, "T1", "antigravity_cli", model=MODEL)
        wait_for(lambda: len(fake.runs()) == 1)
        same = services.executors.run_task("T2", "antigravity_cli", model=MODEL)
        assert same["outcome"] == "waiting_turn" and same["waiting"]["limit_on"] in {"project", "folder"}
        assert services.tasks.get_task("T2")["state"] == "open" and len(fake.runs()) == 1
        other = pool.submit(services.executors.run_task, "T3", "antigravity_cli", model=MODEL)   # another project and folder
        wait_for(lambda: len(fake.runs()) == 2)                 # both programs are running at the same time
        assert not first.done() and not other.done()
        assert first.result(30)["outcome"] == "submitted" and other.result(30)["outcome"] == "submitted"
    assert services.executors.run_task("T2", "antigravity_cli", model=MODEL)["outcome"] == "submitted"


# --------------------------------------------------------------------------- the worker that runs alone

def test_a_worker_that_runs_alone_follows_the_saved_mode_and_has_none_before(tmp_path):
    from multiagent.bootstrap import build_system
    from multiagent.services import ApplicationServices

    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    system = build_system(settings)
    assert system.worker._policy() is None                       # no saved mode: the static limits, as before
    GuiSettingsServices(SimpleNamespace(settings=settings, usage=SimpleNamespace(plans=lambda: {"apps": []}))
                        ).set_concurrency({"selection": "manual", "manual": 2})
    value = system.worker._policy()
    assert value is not None and 1 <= value["limit"] <= 2 and value["per_project"] == 1
    services = ApplicationServices(system)                       # the services that host the GUI take the worker over
    assert system.worker.limit_source is not None and system.worker.on_batch_complete is not None
    assert services.runs._complete_temporary_tasks is not None


# =========================================================================== round 2

HOLDER = """
import sys
from pathlib import Path
from multiagent.executors.antigravity import _RunLock

lock = _RunLock(Path(sys.argv[1]))
handle = lock.acquire_slot(sys.argv[2])
print("READY" if handle is not None else "BUSY", flush=True)
sys.stdin.readline()
"""
ROOT = Path(__file__).resolve().parents[1]


def hold_in_process(lock_path, folder):
    """A real operating-system process that holds the agy lock of ``folder`` until it is killed. Returns ``(process, answer)``."""
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    process = subprocess.Popen([sys.executable, "-c", HOLDER, str(lock_path), str(folder)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True, env=env, cwd=str(ROOT))
    return process, process.stdout.readline().strip()


def kill(process):
    process.kill()
    process.wait(30)


def test_agy_folder_locks_hold_across_real_processes(fake, tmp_path):
    adapter = make_adapter(fake, tmp_path)
    adapter.slot_policy = lambda: True
    first, second = tmp_path / "task-one", tmp_path / "task-two"
    for folder in (first, second):
        folder.mkdir()
    one, answer = hold_in_process(adapter._lock.path, first)
    assert answer == "READY"
    other = None
    try:
        assert adapter.launch_blocker(first) == agy.RUN_IN_PROGRESS      # the same folder is taken by another process
        assert adapter.launch_blocker(second) is None                    # a different folder is free
        other, answer = hold_in_process(adapter._lock.path, second)      # and a second process takes it at the same time
        assert answer == "READY" and adapter.launch_blocker(second) == agy.RUN_IN_PROGRESS
        third, answer = hold_in_process(adapter._lock.path, first)       # the same folder again: refused, not shared
        assert answer == "BUSY"
        kill(third)
        with pytest.raises(agy.AntigravityAdapterError) as caught:
            adapter.launch(make_request(first))
        assert caught.value.code == agy.RUN_IN_PROGRESS
    finally:
        kill(one)
        if other is not None:
            kill(other)
    wait_for(lambda: adapter.launch_blocker(first) is None and adapter.launch_blocker(second) is None)   # a dead holder frees it
    assert adapter.launch(make_request(first)).status is ExecutorRunStatus.COMPLETED


def test_a_folder_held_by_another_process_makes_the_launch_wait_and_then_run(fake, tmp_path):
    services, adapter = agy_world(tmp_path, fake)
    adapter.slot_policy = services.executors._mode_saved
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 4})
    services.tasks.create_group("OTH", "Other project", actor="user", authorized=True)
    services.tasks.create_task("OTH", "T3", "Elsewhere", actor="user", objective="Summarize a third module.")
    fake.set(run=service_run())
    holder, answer = hold_in_process(adapter._lock.path, services.executors._workdir_key("T1", {}))
    assert answer == "READY"
    try:
        out = services.executors.run_task("T1", "antigravity_cli", model=MODEL)
        assert out["outcome"] == "waiting_turn" and out["waiting"]["code"] == "run_in_progress"
        assert out["waiting"]["limit_on"] == "folder"                     # a wait, never a 409 or a failed attempt
        assert services.tasks.get_task("T1")["state"] == "open" and fake.runs() == []
        assert services.executors.run_task("T3", "antigravity_cli", model=MODEL)["outcome"] == "submitted"
    finally:
        kill(holder)
    assert services.executors.run_task("T1", "antigravity_cli", model=MODEL)["outcome"] == "submitted"


def agy_plans(gemini_used, other_used, minutes=120):
    reset = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    return {"apps": [{"app": "antigravity", "windows": [
        {"window": "five_hour", "group": "Gemini", "used_percent": gemini_used, "resets_at": reset},
        {"window": "five_hour", "group": "Claude and GPT", "used_percent": other_used, "resets_at": reset}]}]}


def test_the_agy_bag_of_the_model_decides_the_wait(fake, tmp_path):
    services, adapter = agy_world(tmp_path, fake)
    services.gui_settings.set_concurrency({"mode": "normal"})
    services.usage.plans = lambda: agy_plans(gemini_used=95, other_used=20)
    task = services.tasks.get_task("T1")
    turn = services.executors._turn
    token, waiting = turn(task, "antigravity_cli", "gemini-4-argon-high", adapter, {}, reserve=False)
    assert token is None and waiting["limit_on"] == "bag" and waiting["code"] == "quota_margin_reached"
    token, waiting = turn(task, "antigravity_cli", "claude-sonnet-5-5", adapter, {}, reserve=False)
    assert waiting is None                                               # the other bag has its own quota
    services.usage.plans = lambda: agy_plans(gemini_used=20, other_used=95)
    assert turn(task, "antigravity_cli", "gemini-4-argon-high", adapter, {}, reserve=False)[1] is None
    blocked = turn(task, "antigravity_cli", "gpt-oss-120b", adapter, {}, reserve=False)[1]
    assert blocked["limit_on"] == "bag" and blocked["code"] == "quota_margin_reached"
    assert services.executors._turns.held() == 0                         # a probe holds nothing


def held_agy_task(fake, tmp_path):
    """The agy world with T1 paused by a usage limit whose reset time has passed."""
    services, adapter = agy_world(tmp_path, fake)
    fake.set(usage={"stdout": usage_text(gemini_5h=8)},
             run={"stdout": good_run(status="ERROR", error="RESOURCE_EXHAUSTED: quota reached"), "exit_code": 1})
    first = services.executors.run_task("T1", "antigravity_cli", model=MODEL, accept_quota_warning=True)
    assert first["outcome"] == "quota_exhausted"
    services.executors.clock = lambda: datetime(2027, 1, 1, tzinfo=timezone.utc)
    fake.set(usage={"stdout": usage_text()}, run=service_run())
    return services


def test_a_held_task_with_no_place_stays_held_and_resumes_by_itself_when_one_frees(fake, tmp_path):
    services = held_agy_task(fake, tmp_path)
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 1})
    token = services.executors._turns.reserve({"app": "antigravity"}, {"app": 1})        # a run elsewhere takes the only place
    assert services.tasks.get_task("T1")["state"] == "paused"
    before = len(fake.runs())

    manual = services.executors.resume_task("T1")                          # a person asks: the answer is a wait
    assert manual["outcome"] == "waiting_turn" and services.tasks.get_task("T1")["state"] == "paused"

    report = services.executors.resume_due(task_id="T1")                   # the sweep: not resumed, and the hold stays
    assert report["resumed"] == [] and report["skipped"] == []
    (waiting,) = report["waiting"]
    assert waiting["why"] == "waiting_turn" and waiting["waiting"]["reason"]
    assert services.tasks.get_task("T1")["state"] == "paused" and len(fake.runs()) == before

    services.executors._turns.release(token)                              # the place frees: the next sweep resumes it
    done = services.executors.resume_due(task_id="T1")
    assert [item["task_id"] for item in done["resumed"]] == ["T1"] and len(fake.runs()) == before + 1
    assert services.tasks.get_task("T1")["state"] == "submitted"


def test_resume_met_never_counts_a_wait_for_a_turn_as_resumed(fake, tmp_path, monkeypatch):
    services = held_agy_task(fake, tmp_path)
    executors = services.executors
    info = {"task_id": "T1", "hold_id": "h1", "state": "due"}
    hold = executors._view_hold(services.tasks.get_task("T1"))
    monkeypatch.setattr(executors, "_condition_rows", lambda task_id: ([(services.tasks.get_task("T1"), hold)], {}, {}))
    monkeypatch.setattr(executors, "_assess", lambda *a, **k: ("due", info))
    waiting_item = {"outcome": "waiting_turn", "waiting": {"reason": "full", "limit_on": "app"}}
    monkeypatch.setattr(executors, "_resume_one", lambda *a, **k: waiting_item)
    out = executors.resume_met(task_id="T1")
    assert out["resumed"] == [] and out["skipped"] == []
    assert out["waiting"][0]["why"] == "waiting_turn" and out["waiting"][0]["waiting"]["limit_on"] == "app"


def test_a_watcher_that_gets_a_wait_for_a_turn_keeps_waiting_and_resumes_when_it_frees(tmp_path):
    from datetime import datetime as dt
    from multiagent.services.watcher import INTERVAL_SECONDS, WatcherProcess

    now = [dt(2026, 1, 1, tzinfo=timezone.utc)]
    answers = [{"resumed": [], "waiting": [{"task_id": "T1", "why": "waiting_turn"}], "skipped": [], "expired": []},
               {"resumed": [{"task_id": "T1"}], "waiting": [], "skipped": [], "expired": []}]
    wait = {"task_id": "T1", "hold_id": "h1", "attempt": 1, "executor_id": "fake", "due": True, "state": "due"}
    service = SimpleNamespace(check_waits=lambda **kw: {"waits": [dict(wait)]}, resume_met=lambda **kw: answers.pop(0))
    watcher = WatcherProcess(tmp_path, service, integration_marker=tmp_path / "integration.lock",
                             integration_active=lambda: False, clock=lambda: now[0], alive=lambda pid: pid == 1, pid=1)
    watcher.claim("T1", "h1", 1)
    assert watcher.tick()["state"] == "waiting"                              # not resumed, not given up
    now[0] += timedelta(seconds=INTERVAL_SECONDS * 2)
    assert watcher.tick()["state"] == "resumed"


def test_a_handoff_to_a_delegate_with_no_place_commits_nothing(world):
    services = world.services
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 1, "per_app": {"codex": 1}})
    token = services.executors._turns.reserve({"app": "codex"}, {"app": 1})
    try:
        before = services.tasks.get_task("T1")
        _descriptor, adapter, declaration = services.executors._executor("codex_cli")
        probe = services.executors._turn(before, "codex_cli", None, adapter, declaration, reserve=False)
        assert probe[0] is None and probe[1]["limit_on"] == "app"          # what ``_handoff`` asks before it commits
        assert services.tasks.get_task("T1")["revision"] == before["revision"]
    finally:
        services.executors._turns.release(token)


def test_a_launch_the_gui_could_not_place_answers_waiting_and_leaves_no_open_task(tmp_path, monkeypatch):
    from test_gui_executor_launch import GUI_LAUNCH, GuiWorld, launch_body

    gui = GuiWorld(tmp_path, monkeypatch)
    try:
        gui.configure({"claude_cli"})
        gui.services.usage.plans = lambda: quota("claude", used=95)
        gui.services.gui_settings.set_concurrency({"mode": "normal"})
        response = gui.client.post(GUI_LAUNCH, headers=gui.auth, json=launch_body("claude_cli"))
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "waiting_turn" and detail["details"]["waiting"]["code"] == "quota_margin_reached"
        assert gui.fake_cli.calls() == []                                     # nothing started
        launched = [task for task in gui.services.tasks.list_tasks() if task["id"].startswith("gui-executor-")]
        assert [task["status"] for task in launched] == ["cancelled"]         # no task left open behind the answer
        gui.services.usage.plans = lambda: quota("claude", used=50)           # the place is there: the same launch runs
        assert gui.client.post(GUI_LAUNCH, headers=gui.auth, json=launch_body("claude_cli")).status_code == 200
    finally:
        gui.client.close()
        gui.services.runs.close()


@pytest.fixture
def background(world, monkeypatch):
    """The executor world with the availability policy on (small waits); what is left running is killed at the end."""
    monkeypatch.setattr(availability, "MIN_MAX_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(availability, "MIN_HEARTBEAT_SECONDS", 0.1)
    world.services.executors.availability = AvailabilityPolicy(enabled=True, max_wait_seconds=0.3, heartbeat_seconds=0.2)
    world.services.executors._session_activity = lambda adapter, session_id: None
    yield world
    world.services.executors.background.shutdown(10)


def test_a_background_attempt_keeps_its_place_until_it_ends(background):
    services = background.services
    background.fake.set("claude", sleep=1.0, stdout=claude_out())
    services.gui_settings.set_concurrency({"selection": "manual", "manual": 4, "per_app": {"claude": 1}})
    first = services.executors.run_task("T1", "claude_cli")
    assert first["outcome"] == "running"                                   # the caller was answered, the attempt goes on
    assert services.executors._turns.held() == 1                           # the background attempt took the place over
    blocked = services.executors.run_task("T3", "claude_cli")
    assert blocked["outcome"] == "waiting_turn" and blocked["waiting"]["limit_on"] == "app"
    assert services.executors.background.join(30)
    assert services.executors._turns.held() == 0                           # it gave the place back when it ended
    assert services.executors.run_task("T3", "claude_cli")["outcome"] in {"running", "submitted"}
    services.executors.background.join(30)
    assert services.executors._turns.held() == 0


def test_the_real_router_picks_the_level_of_the_saved_mode_for_a_plan_and_a_preview(tmp_path):
    from test_executor_routing import make_world as routing_world, verified

    world = routing_world(tmp_path, ["fake_cli"])
    services = world.services
    plan = lambda: services.executors._plan("T1", "fake_cli", None, None, "auto", verified())
    assert plan().model == "fake-small"                                       # no mode: the cheapest allowed for work (T1)
    services.gui_settings.set_concurrency({"mode": "maximum", "duration": "tasks", "selection": "manual", "manual": 2})
    chosen = plan()                                                           # Maximum: T3 or above for every step
    assert chosen.model == "fake-big" and chosen.selection.model_source == "profile"
    preview = services.assignment.preview("T1", {"profile_id": "auto"}, verified())["decision"]
    assert preview["status"] == "selected"
    assert services.assignment.mode_options()["concurrency_mode"] == "maximum"
    services.gui_settings.set_concurrency({"mode": "eco"})
    assert plan().model == "fake-small"                                       # Eco: work at T2 or below
    # The level the person confirmed for a model is the one the router uses.
    services.gui_settings.confirm_model_level("fake_small", "T3", "checked by hand")
    assert plan().model == "fake-mid"                                         # Eco no longer takes fake-small: it is T3 now


def test_the_mode_notice_uses_the_level_the_person_confirmed(tmp_path):
    from test_executor_routing import make_world as routing_world

    services = routing_world(tmp_path, ["fake_cli"]).services
    services.gui_settings.set_concurrency({"mode": "maximum", "duration": "tasks"})
    assert services.assignment.mode_notices("fake-small", "work") == (f"{MODE_LEVEL_NOTICE}:maximum",)   # catalog T1
    services.gui_settings.confirm_model_level("fake_small", "T3", "checked by hand")
    assert services.assignment.mode_notices("fake-small", "work") == ()        # confirmed T3 fits Maximum
    services.gui_settings.set_concurrency({"mode": "eco"})
    assert services.assignment.mode_notices("fake-small", "work") == (f"{MODE_LEVEL_NOTICE}:eco",)   # T3 is above Eco's work
    assert services.assignment.mode_notices("fake-small", "review") == ()      # review wants T3 or above: it has T3


def test_an_explicit_concurrency_flag_beats_a_saved_mode(tmp_path, monkeypatch):
    from multiagent import cli
    from multiagent.bootstrap import build_system

    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    GuiSettingsServices(SimpleNamespace(settings=settings, usage=SimpleNamespace(plans=lambda: {"apps": []}))
                        ).set_concurrency({"selection": "manual", "manual": 1})
    seen = {}

    def fake_build(received):
        system = build_system(received)
        seen["system"], seen["concurrency"] = system, received.worker_concurrency
        return system

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_system", fake_build)

    class Interrupted:
        def wait(self, timeout=None):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "threading", SimpleNamespace(Event=Interrupted))
    cli.cmd_worker(SimpleNamespace(concurrency=3))
    assert seen["concurrency"] == 3 and seen["system"].worker.limit_source is None      # the flag wins: static caps
    assert seen["system"].worker._policy() is None and seen["system"].worker.concurrency == 3
    cli.cmd_worker(SimpleNamespace(concurrency=None))
    assert seen["system"].worker.limit_source is not None                  # no flag: the saved mode rules


def test_workers_of_one_process_share_the_total_cap_and_the_batch_ends_in_every_database(tmp_path):
    from multiagent.worker import SharedSlots

    slots = SharedSlots()
    store_a, _ = stores(tmp_path / "a")
    store_b, _ = stores(tmp_path / "b")
    engine_a, engine_b = BlockingEngine(store_a, expected=2), BlockingEngine(store_b, expected=2)
    for _ in range(2):
        queued(store_a)
        queued(store_b)
    one = LocalWorker(store_a, engine_a, concurrency=2, limit_source=policy(2, per_project=2), shared_slots=slots)
    two = LocalWorker(store_b, engine_b, concurrency=2, limit_source=policy(2, per_project=2), shared_slots=slots)
    one.start()
    two.start()
    try:
        wait_for(lambda: slots.count == 2)
        time.sleep(0.3)
        assert slots.count == 2 and len(engine_a.calls) + len(engine_b.calls) == 2      # the cap of 2 is for both workers
    finally:
        engine_a.release.set()
        engine_b.release.set()
        one.stop()
        two.stop()
    assert slots.count == 0


def test_the_batch_hook_waits_for_every_database_of_the_process(tmp_path):
    from multiagent.services import ApplicationServices
    from multiagent.bootstrap import build_system

    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices(build_system(settings))
    services.gui_settings.set_concurrency({"mode": "maximum", "duration": "tasks"})
    other_store, _ = stores(tmp_path / "bundle")
    runtime = SimpleNamespace(store=other_store, worker=object())
    services.runs._bundle_runtimes["extra"] = runtime
    pending = queued(other_store)
    assert services.runs._complete_temporary_tasks() is False                # another database still has work
    assert services.gui_settings.concurrency()["mode"] == "maximum"
    other_store.update_run(pending, status=RunStatus.COMPLETED.value)
    assert services.runs._complete_temporary_tasks() is True
    assert services.gui_settings.concurrency()["mode"] == "normal"
    services.runs._bundle_runtimes.clear()


# =========================================================================== round 3

def test_a_model_launch_is_bound_to_its_own_app_and_never_to_another_apps_quota(world):
    services = world.services
    services.gui_settings.set_concurrency({"mode": "normal"})
    services.usage.plans = lambda: quota("codex", used=95)              # codex is at its margin, claude reports nothing
    task = services.tasks.get_task("T1")
    adapter = services.executors._executor("claude_cli")[1]
    token, waiting = services.executors._turn(task, "claude_cli", "model-a", adapter, {}, reserve=False)
    assert token is None and waiting is None                              # no data for claude: one place, not codex's 0
    services.usage.plans = lambda: quota("claude", used=95)
    waiting = services.executors._turn(task, "claude_cli", "model-a", adapter, {}, reserve=False)[1]
    assert waiting is not None and waiting["code"] == "quota_margin_reached"      # its own quota does bind it


def test_agy_in_auto_keeps_the_per_app_and_per_model_numbers_and_the_bags_stay_apart(fake, tmp_path):
    services, adapter = agy_world(tmp_path, fake)
    services.gui_settings.set_concurrency({"mode": "normal", "per_app": {"antigravity": 1},
                                           "per_model": {"claude-sonnet-5-5": 1}})
    services.usage.plans = lambda: agy_plans(gemini_used=20, other_used=20)
    task = services.tasks.get_task("T1")
    turn, turns = services.executors._turn, services.executors._turns
    probe = lambda model: turn(task, "antigravity_cli", model, adapter, {}, reserve=False)[1]
    assert probe("gemini-4-argon-high") is None
    held = turns.reserve({"app": "antigravity"}, {"app": 1})                # the app as a whole is full (per_app = 1)
    assert probe("gemini-4-argon-high")["limit_on"] == "app" and probe("claude-sonnet-5-5")["limit_on"] == "app"
    turns.release(held)
    held = turns.reserve({"model": "claude-sonnet-5-5"}, {"model": 1})      # one model is full (per_model = 1)
    assert probe("claude-sonnet-5-5")["limit_on"] == "model" and probe("gemini-4-argon-high") is None
    turns.release(held)
    services.usage.plans = lambda: agy_plans(gemini_used=95, other_used=20)  # Gemini at its margin, Claude/GPT free
    assert probe("gemini-4-argon-high")["limit_on"] in {"bag", "model"} and probe("claude-sonnet-5-5") is None


def test_one_models_weekly_row_does_not_stop_the_other_models_of_its_bag(fake, tmp_path):
    services, adapter = agy_world(tmp_path, fake)
    services.gui_settings.set_concurrency({"mode": "normal"})
    reset = (datetime.now(timezone.utc) + timedelta(minutes=120)).isoformat()
    plans = agy_plans(gemini_used=20, other_used=20)
    plans["apps"][0]["windows"].append({"window": "model_weekly", "group": "Claude Opus 4.5", "used_percent": 100,
                                        "resets_at": reset})
    services.usage.plans = lambda: plans
    task = services.tasks.get_task("T1")
    probe = lambda model: services.executors._turn(task, "antigravity_cli", model, adapter, {}, reserve=False)[1]
    assert probe("gpt-oss-120b") is None and probe("claude-sonnet-5-5") is None
    assert probe("claude-opus-4-5")["code"] == "quota_margin_reached"       # only the model that row belongs to


def test_a_claim_that_raises_never_keeps_the_shared_place(tmp_path):
    from multiagent.worker import SharedSlots

    store, _ = stores(tmp_path)
    slots = SharedSlots()
    worker = LocalWorker(store, BlockingEngine(store), limit_source=policy(2), shared_slots=slots)

    def broken(*args, **kwargs):
        raise RuntimeError("database gone")

    store.claim_queued_run = broken
    with pytest.raises(RuntimeError):
        worker.process_once()
    assert slots.count == 0
