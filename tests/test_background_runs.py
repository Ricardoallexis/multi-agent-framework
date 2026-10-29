# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""AVA-T02: background launch, heartbeat and end event of an executor attempt, with fake programs only.

No real Claude, Codex or Copilot is started: the harness of ``test_executor_services`` generates a script that the real
adapters run as ``python fake_cli.py <spec>.json``. The availability policy of AVA-T01 is built here with small limits (the
module constants that hold the real minimums are patched), so a test lasts seconds and not minutes.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.executors import process
from multiagent.services import ServiceError
from multiagent.services import availability
from multiagent.services import executors as executor_module
from multiagent.services.availability import AvailabilityPolicy, AvailabilityPolicyError
from multiagent.services.executors import BackgroundAttempts
from test_effect_gates import arm, untouched
from test_executor_services import (  # noqa: F401  (the fixtures are used by name)
    NOW, THREAD, attempt_events, claude_out, codex_out, event_types, never_a_real_cli, refused, world,
)

SLOW = 3.0       # seconds a "slow" fake program takes; the caller of a background run waits a fraction of that
LONG = 60.0      # a program that only ends if somebody kills it


@pytest.fixture
def policy(monkeypatch) -> AvailabilityPolicy:
    """An enabled policy with small limits (the real minimums are 1 s and 5 s)."""
    monkeypatch.setattr(availability, "MIN_MAX_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(availability, "MIN_HEARTBEAT_SECONDS", 0.1)
    return AvailabilityPolicy(enabled=True, max_wait_seconds=0.3, heartbeat_seconds=0.2)


@pytest.fixture
def patient(policy) -> AvailabilityPolicy:
    """The same policy with a caller that waits long enough for a fast fake program: for attempts that must end in the wait."""
    return AvailabilityPolicy(enabled=True, max_wait_seconds=30, heartbeat_seconds=0.2)


@pytest.fixture
def background(world, policy):
    """The world of the executor tests with the policy on; whatever is left running is killed at the end."""
    world.services.executors.availability = policy
    world.services.executors._session_activity = lambda adapter, session_id: None   # no local session files are read
    yield world
    world.services.executors.background.shutdown(10)


def background_policy_short(patient: AvailabilityPolicy) -> AvailabilityPolicy:
    """The patient policy with a short wait (a 1 second program is then still running when the caller is answered)."""
    return replace(patient, max_wait_seconds=0.3)


def events_of(services, task_id: str, event_type: str) -> list[dict]:
    return [event for event in services.tasks.events(task_id=task_id) if event["type"] == event_type]


def finished_wait(services, timeout: float = 30.0) -> None:
    assert services.executors.background.join(timeout), "a background attempt did not end in time"


# --------------------------------------------------------------------------- the policy decides

def test_with_the_policy_off_a_run_waits_for_the_whole_attempt_as_before(world):
    services = world.services
    assert services.executors.availability is None
    out = services.executors.run_task("T1", "claude_cli")
    forced = services.executors.run_task("T3", "claude_cli", wait=False)          # no effect while the policy is off
    assert out["outcome"] == "submitted" and forced["outcome"] == "submitted"
    assert services.executors.background.active() == []
    assert "executor.heartbeat" not in event_types(services, "T1") + event_types(services, "T3")


def real_settings(like) -> Settings:
    """A real ``Settings`` (it reads the environment) for the same folder as the world's."""
    return Settings(_env_file=None, local_dir=like.local_dir, worker_enabled=False, mock_mode=True, gemini_api_key="",
                    openai_api_key="", openai_enabled=False)


def test_the_real_settings_turn_the_policy_on_from_the_environment(world, monkeypatch):
    services = world.services
    assert services.executors.availability is None
    assert availability.policy_from_settings(services.settings) == availability.DISABLED     # off by default
    monkeypatch.setenv("AVAILABILITY_ENABLED", "true")
    monkeypatch.setenv("AVAILABILITY_MAX_WAIT_SECONDS", "1")
    monkeypatch.setenv("AVAILABILITY_HEARTBEAT_SECONDS", "5")
    monkeypatch.setenv("AVAILABILITY_PRIORITIES", "cancel;note;adjust;interrupt")
    monkeypatch.setenv("AVAILABILITY_ORCHESTRATOR_THRESHOLD", "0.55")
    settings = real_settings(services.settings)
    assert availability.policy_from_settings(settings) == AvailabilityPolicy(
        enabled=True, max_wait_seconds=1, heartbeat_seconds=5, priorities=("cancel", "note", "adjust", "interrupt"),
        orchestrator_threshold=0.55)
    services.system.settings = settings                                             # no policy set: the settings decide
    world.fake.set("claude", sleep=SLOW, stdout=claude_out())
    try:
        out = services.executors.run_task("T1", "claude_cli")
        assert out["outcome"] == "running" and out["background"] == {"max_wait_seconds": 1.0, "heartbeat_seconds": 5.0}
    finally:
        services.executors.background.shutdown(10)
    assert services.tasks.get_task("T1")["state"] == "paused"


def _number_of(value):
    try:
        return float(value)
    except ValueError:
        return value


def test_the_settings_defaults_are_the_off_policy_and_their_limits_are_the_policys(monkeypatch):
    settings = Settings(_env_file=None)
    assert availability.policy_from_settings(settings) == availability.DISABLED
    assert settings.availability_priorities == availability.DEFAULT_PRIORITIES
    limits = (("AVAILABILITY_MAX_WAIT_SECONDS", availability.MIN_MAX_WAIT_SECONDS, availability.MAX_MAX_WAIT_SECONDS),
              ("AVAILABILITY_HEARTBEAT_SECONDS", availability.MIN_HEARTBEAT_SECONDS, availability.MAX_HEARTBEAT_SECONDS),
              ("AVAILABILITY_ORCHESTRATOR_THRESHOLD", *availability.ORCHESTRATOR_THRESHOLD_RANGE))
    for name, low, high in limits:
        for edge in (low, high):                                                    # the edges are valid in both
            monkeypatch.setenv(name, str(edge))
            availability.policy_from_settings(Settings(_env_file=None))
        for outside in (low - 0.01, high + 0.01, "nan", "inf", "text"):             # and just outside, in neither
            monkeypatch.setenv(name, str(outside))
            with pytest.raises(ValidationError):
                Settings(_env_file=None)
            with pytest.raises(AvailabilityPolicyError):
                availability.policy_from_settings(SimpleNamespace(**{name.lower(): _number_of(outside)}))
        monkeypatch.delenv(name)


@pytest.mark.parametrize("text, expected", [
    ("cancel;interrupt;adjust;note", ("cancel", "interrupt", "adjust", "note")),
    ("cancel,note,adjust,interrupt", ("cancel", "note", "adjust", "interrupt")),
    ('["cancel", "adjust", "note", "interrupt"]', ("cancel", "adjust", "note", "interrupt")),
    ("", availability.DEFAULT_PRIORITIES),
])
def test_the_priorities_are_read_from_text_and_checked_like_the_policys(monkeypatch, text, expected):
    monkeypatch.setenv("AVAILABILITY_PRIORITIES", text)
    settings = Settings(_env_file=None)
    assert settings.availability_priorities == expected
    assert availability.policy_from_settings(settings).priorities == expected


@pytest.mark.parametrize("text", ["interrupt;cancel;adjust;note", "cancel;cancel;adjust;note", "cancel;adjust;note",
                                  "cancel;interrupt;adjust;note;extra", "cancel;interrupt;adjust;shout", "[1, 2, 3, 4]",
                                  '["cancel", "cancel", "adjust", "note"]'])
def test_priorities_that_the_policy_would_refuse_are_refused_when_the_settings_load(monkeypatch, text):
    monkeypatch.setenv("AVAILABILITY_PRIORITIES", text)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_a_policy_that_is_not_valid_refuses_the_run_and_leaves_the_task(world, monkeypatch):
    def broken(settings):
        raise AvailabilityPolicyError("max_wait_seconds must be between 1 and 120, got 500")

    monkeypatch.setattr(executor_module, "policy_from_settings", broken)
    error = refused(lambda: world.services.executors.run_task("T1", "claude_cli"),
                    reason="availability_policy_invalid")
    assert "max_wait_seconds" in error.message
    assert world.services.tasks.get_task("T1")["state"] == "open"
    # A caller that asks to wait does not need the policy at all.
    assert world.services.executors.run_task("T1", "claude_cli", wait=True)["outcome"] == "submitted"


@pytest.mark.parametrize("wait", ["yes", 1, 0, [], "false"])
def test_wait_must_be_a_boolean_and_a_refusal_changes_nothing(world, wait):
    error = refused(lambda: world.services.executors.run_task("T1", "claude_cli", wait=wait),
                    code="invalid_request")
    assert error.details["field"] == "wait"
    assert world.services.tasks.get_task("T1")["state"] == "open"
    assert world.fake.calls() == []


def test_wait_true_waits_for_the_whole_attempt_even_with_the_policy_on(background):
    services = background.services
    background.fake.set("claude", sleep=1.0, stdout=claude_out())
    started = time.monotonic()
    out = services.executors.run_task("T1", "claude_cli", wait=True)
    assert time.monotonic() - started >= 1.0
    assert out["outcome"] == "submitted" and services.executors.background.active() == []
    assert events_of(services, "T1", "executor.heartbeat") == []


# --------------------------------------------------------------------------- running, without blocking the caller

def test_a_slow_attempt_answers_running_after_the_wait_and_ends_recorded(background):
    services = background.services
    background.fake.set("claude", sleep=SLOW, stdout=claude_out())

    started = time.monotonic()
    out = services.executors.run_task("T1", "claude_cli")
    answered = time.monotonic() - started

    assert answered < SLOW - 0.4, "the caller was blocked until the attempt ended"
    assert out["outcome"] == "running" and out["run_status"] == "running"
    assert out["task_id"] == "T1" and out["attempt"] == 1 and out["executor_id"] == "claude_cli"
    assert out["actor"] == "executor:claude_cli" and out["permission"] == "read"
    assert out["result"] is None and out["session_id"] is None and out["usage"] is None
    assert out["background"] == {"max_wait_seconds": 0.3, "heartbeat_seconds": 0.2}
    assert out["task"]["state"] == "running" and out["task"]["assignee"] == "executor:claude_cli"
    assert [row["task_id"] for row in services.executors.background.active()] == ["T1"]
    assert "executor.attempt_finished" not in event_types(services, "T1")      # not ended yet

    finished_wait(services)

    task = services.tasks.get_task("T1")
    assert task["state"] == "submitted" and task["attempt"] == 1
    (finished,) = attempt_events(services, "T1")
    assert finished["outcome"] == "submitted" and finished["result_ref"] == "result://T1/1"
    assert services.envelopes.get_result("T1")["summary"] == "Did the work."
    assert services.executors.background.active() == []
    assert event_types(services, "T1")[-1] == "executor.attempt_finished"


def test_a_fast_attempt_answers_as_a_synchronous_run_and_leaves_the_same_events(background, patient):
    services = background.services
    services.executors.availability = patient
    out = services.executors.run_task("T1", "claude_cli")                          # policy on, ends within the wait
    assert out["outcome"] == "submitted" and out["run_status"] == "completed" and "background" not in out
    assert out["result"]["duplicate"] is False and out["task"]["state"] == "submitted"

    services.executors.availability = None                                          # the same run, synchronous
    background.services.executors.run_task("T3", "claude_cli")
    assert event_types(services, "T1") == event_types(services, "T3")
    assert attempt_events(services, "T1")[0]["outcome"] == attempt_events(services, "T3")[0]["outcome"] == "submitted"
    assert services.executors.background.active() == []


def test_a_run_that_ends_at_a_quota_limit_in_the_background_holds_the_task_as_a_synchronous_one(background, patient):
    services = background.services
    services.executors.availability = patient
    background.fake.set("claude", stdout="", stderr="Claude AI usage limit reached|1760000000", exit=1)
    out = services.executors.run_task("T1", "claude_cli")
    assert out["outcome"] == "quota_exhausted" and out["executor_paused_until"]
    task = services.tasks.get_task("T1")
    assert task["state"] == "paused" and task["continuity"]["hold"]["executor_id"] == "claude_cli"
    assert [e["payload"]["outcome"] for e in events_of(services, "T1", "executor.attempt_finished")] == ["quota_exhausted"]
    assert len(events_of(services, "T1", "executor.paused")) == 1


def test_the_checks_before_running_stay_in_the_callers_thread_and_a_refusal_is_never_running(background):
    services = background.services
    background.fake.set("claude", sleep=SLOW, stdout=claude_out())
    refused(lambda: services.executors.run_task("T1", "manual_app"), reason="executor_without_adapter")
    refused(lambda: services.executors.run_task("NOPE", "claude_cli"), code="not_found")
    assert services.tasks.get_task("T1")["state"] == "open" and services.executors.background.active() == []
    out = services.executors.run_task("T1", "claude_cli")
    assert out["outcome"] == "running"
    refused(lambda: services.executors.run_task("T1", "claude_cli"), reason="background_attempt_active")
    assert services.tasks.get_task("T1")["attempt"] == 1                           # the second call changed nothing


# --------------------------------------------------------------------------- the heartbeat and the stall

def test_a_heartbeat_is_written_while_the_attempt_runs_and_holds_no_content(background):
    services = background.services
    background.fake.set("claude", sleep=3.0, stdout=claude_out())      # long enough for a few beats on a busy machine

    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "running"
    finished_wait(services)

    beats = events_of(services, "T1", "executor.heartbeat")
    assert len(beats) >= 2, event_types(services, "T1")
    for beat in beats:
        assert beat["ref"] == "claude_cli" and beat["actor"] == "system:watcher" and beat["task_id"] == "T1"
        assert set(beat["payload"]) == {"attempt", "elapsed_seconds", "idle_seconds", "attention"}
        assert beat["payload"]["attempt"] == 1 and beat["payload"]["attention"] is None
        assert beat["payload"]["idle_seconds"] is None            # a silent Claude CLI without metadata: idle is unknown
    elapsed = [beat["payload"]["elapsed_seconds"] for beat in beats]
    assert elapsed == sorted(elapsed) and elapsed[0] >= 0.2 and elapsed[-1] < SLOW + 3
    sequence = event_types(services, "T1")
    assert sequence.index("executor.heartbeat") > sequence.index("task.started")
    assert max(i for i, name in enumerate(sequence) if name == "executor.heartbeat") < sequence.index(
        "executor.attempt_finished")                                                   # none after the end
    # Nothing the person or the program wrote is in them.
    assert "Summarize the module" not in json.dumps(beats) and "result" not in json.dumps(beats).lower()


def test_the_heartbeat_idle_time_is_the_one_the_stall_was_judged_on(background):
    """The Claude CLI prints nothing while it works: its idle time comes from the session metadata, not from stdout."""
    services = background.services
    services.executors._session_activity = lambda adapter, session_id: {"last_output_at": NOW.isoformat()}   # the clock is NOW
    background.fake.set("claude", sleep=3.0, stdout=claude_out())
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "running"
    finished_wait(services)
    beats = events_of(services, "T1", "executor.heartbeat")
    assert len(beats) >= 2
    # The process itself had printed nothing for more than a second by then, and the heartbeat does not say so.
    assert [beat["payload"]["idle_seconds"] for beat in beats] == [0.0] * len(beats)
    assert all(beat["payload"]["attention"] is None for beat in beats)


def test_the_heartbeat_idle_time_of_a_session_with_open_tests_is_unknown(background):
    services = background.services
    services.executors._session_activity = lambda adapter, session_id: {"open_tests": True}
    background.fake.set("claude", sleep=1.2, stdout=claude_out())
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "running"
    finished_wait(services)
    beats = events_of(services, "T1", "executor.heartbeat")
    assert beats and all(b["payload"]["idle_seconds"] is None and b["payload"]["attention"] is None for b in beats)


def test_a_resume_stays_synchronous_with_the_policy_on(background, patient):
    services = background.services
    services.executors.availability = patient
    background.fake.set("claude", stdout=claude_out("no json here"))
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "failed"      # fast: paused with a session
    services.executors.availability = background_policy_short(patient)
    background.fake.set("claude", sleep=1.0, stdout=claude_out())

    started = time.monotonic()
    out = services.executors.resume_task("T1")

    assert time.monotonic() - started >= 1.0 and out["outcome"] == "submitted" and out["mode"] == "resume"
    assert services.executors.background.active() == [] and events_of(services, "T1", "executor.heartbeat") == []


def test_no_heartbeat_is_written_for_a_synchronous_run(world):
    world.fake.set("claude", sleep=1.0, stdout=claude_out())
    world.services.executors.run_task("T1", "claude_cli")
    assert events_of(world.services, "T1", "executor.heartbeat") == []


def test_a_stalled_attempt_is_detected_and_its_heartbeat_says_so(background):
    services = background.services
    service = services.executors
    real_limit = service._limit
    # The stall is 15 minutes unless the executor declares otherwise; here half a second, so the test is short.
    service._limit = lambda plan, name, default: 0.5 / 60 if name == "stall_minutes" else real_limit(plan, name, default)
    background.fake.set("codex", stdout=codex_out(),
                        live_stdout=json.dumps({"type": "thread.started", "thread_id": THREAD}) + "\n", live_sleep=2.2)

    assert service.run_task("T1", "codex_cli")["outcome"] == "running"
    finished_wait(services)

    attention = events_of(services, "T1", "executor.attention")
    assert [e["payload"]["kind"] for e in attention if not e["payload"]["cleared"]] == ["stalled"]
    assert attention[-1]["payload"]["cleared"] is True                              # cleared when the attempt ended
    stalled = [b["payload"] for b in events_of(services, "T1", "executor.heartbeat") if b["payload"]["attention"]]
    assert stalled and all(p["attention"] == "stalled" and p["idle_seconds"] >= 0.5 for p in stalled)
    assert services.tasks.get_task("T1")["state"] == "submitted"
    assert attempt_events(services, "T1")[0]["outcome"] == "submitted"


def test_the_stall_is_fifteen_minutes_and_a_heartbeat_always_comes_sooner(world):
    assert availability.STALL_SECONDS == 900
    assert AvailabilityPolicy(enabled=True).heartbeat_seconds < availability.STALL_SECONDS
    assert availability.MAX_HEARTBEAT_SECONDS < availability.STALL_SECONDS
    service = world.services.executors
    service._session_activity = lambda adapter, session_id: None
    world.services.tasks.transition("T1", "claim", actor="executor:codex_cli")
    attempt = world.services.tasks.transition("T1", "start", actor="executor:codex_cli")["attempt"]
    adapter = SimpleNamespace(adapter_id="codex_cli")
    base = dict(task_id="T1", attempt=attempt, executor_id="codex_cli", adapter=adapter, session_id="synthetic",
                stall_seconds=15 * 60, deadline=None)
    silent = process.ProcessActivity(last_output_at=None, started_at="2026-10-11T11:00:00+00:00",
                                     idle_seconds=15 * 60 + 1)
    found = service._observe_process(process_activity=silent, **base)
    assert (found.attention, found.idle_seconds) == ("stalled", 15 * 60 + 1)
    live = process.ProcessActivity(last_output_at="2026-10-11T12:00:00+00:00", started_at="2026-10-11T11:00:00+00:00",
                                   idle_seconds=60)
    found = service._observe_process(process_activity=live, **base)
    assert (found.attention, found.idle_seconds) == (None, 60)


# --------------------------------------------------------------------------- the end is always recorded

def test_a_framework_failure_in_the_background_is_recorded_without_its_text(background, patient):
    services = background.services
    services.executors.availability = patient
    service = services.executors
    secret = "C:/Users/someone/secret-folder/token-123"

    def explode(adapter, request, resume_session):
        raise RuntimeError(secret)

    service._call_adapter = explode
    out = service.run_task("T1", "claude_cli")                                      # fails within the wait
    assert out["outcome"] == "failed" and out["evidence"] == "internal_error: RuntimeError"
    assert out["task"]["state"] == "paused" and secret not in json.dumps(out, default=str)
    (finished,) = attempt_events(services, "T1")
    assert finished["outcome"] == "failed" and finished["evidence"] == "internal_error: RuntimeError"
    assert secret not in json.dumps(services.tasks.events(task_id="T1"), default=str)
    assert services.executors.background.active() == []
    # The task can be run again afterwards: the place was released.
    del service._call_adapter
    assert service.run_task("T1", "claude_cli")["outcome"] == "submitted"
    assert [e["outcome"] for e in attempt_events(services, "T1")] == ["failed", "submitted"]


def test_a_failure_after_the_end_was_written_does_not_write_a_second_one(background, patient):
    services = background.services
    services.executors.availability = patient
    service = services.executors
    service._clear_streaks = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("late failure"))
    out = service.run_task("T1", "claude_cli")
    # The answer agrees with the log and the task (submitted); the failure of what came after is only a note.
    assert out["outcome"] == "submitted" and out["run_status"] == "completed" and out["evidence"] is None
    assert out["task"]["state"] == "submitted"
    assert out["notes"] == ["internal_error after the end was recorded: RuntimeError"]
    assert [e["outcome"] for e in attempt_events(services, "T1")] == ["submitted"]  # the one already written stays
    assert services.tasks.get_task("T1")["state"] == "submitted" and service.background.active() == []


@pytest.mark.parametrize("where", ["adapter", "after_the_pause"])
def test_the_failure_record_never_pauses_a_run_that_started_in_the_window(background, patient, where):
    """Attempt 1 settles its task (paused), then its failure is recorded; in between, the task runs again (a resume keeps
    the attempt number, so the number does not tell the two apart) and the failure record must not pause that run."""
    services = background.services
    service = services.executors
    service.availability = patient
    if where == "adapter":
        def explode(adapter, request, resume_session):
            raise RuntimeError("boom")

        service._call_adapter = explode                                              # _call_with_watch pauses the task
    else:
        background.fake.set("claude", stdout=claude_out("no json here"))             # a failed attempt, paused in the end
        service._clear_streaks = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("late failure"))
    real = service._record_background_failure
    seen = {}

    def racing(launch, background_attempt, exc):
        seen["before"] = services.tasks.get_task("T1")["state"]
        seen["resumed"] = services.tasks.transition("T1", "resume", actor="executor:claude_cli")     # the next run
        return real(launch, background_attempt, exc)

    service._record_background_failure = racing
    out = service.run_task("T1", "claude_cli")

    assert seen["before"] == "paused" and seen["resumed"]["state"] == "running"
    assert services.tasks.get_task("T1")["state"] == "running"                       # the new run was left alone
    assert services.tasks.get_task("T1")["attempt"] == seen["resumed"]["attempt"]
    assert out["task_id"] == "T1" and service.background.active() == []


def test_a_failure_that_happens_before_the_task_was_settled_does_pause_it(background, patient):
    services = background.services
    service = services.executors
    service.availability = patient
    real_append = service._append

    def append(event_type, **kwargs):
        if event_type == "executor.session_recorded":              # the first thing _finish_attempt writes
            raise RuntimeError("log down")
        return real_append(event_type, **kwargs)

    service._append = append
    out = service.run_task("T1", "claude_cli")
    assert out["outcome"] == "failed" and out["evidence"] == "internal_error: RuntimeError"
    assert services.tasks.get_task("T1")["state"] == "paused"
    assert [e["outcome"] for e in attempt_events(services, "T1")] == ["failed"]


def test_a_pause_that_names_its_attempt_only_pauses_that_attempt(world):
    services = world.services
    services.tasks.transition("T1", "claim", actor="executor:claude_cli")
    attempt = services.tasks.transition("T1", "start", actor="executor:claude_cli")["attempt"]
    service = services.executors
    assert service._pause_task("T1", "executor:claude_cli", "x", attempt=attempt + 1) == "task_not_paused: attempt_changed"
    assert services.tasks.get_task("T1")["state"] == "running"
    assert service._pause_task("T1", "executor:claude_cli", "x", attempt=attempt) is None
    assert services.tasks.get_task("T1")["state"] == "paused"
    assert service._pause_task("T1", "executor:claude_cli", "x", attempt=attempt) == "task_not_paused: attempt_changed"


def test_a_thread_that_cannot_start_leaves_the_task_paused_and_the_place_free(background, monkeypatch):
    services = background.services

    class NoThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    stand_in = SimpleNamespace(Thread=NoThread, Lock=threading.Lock, Event=threading.Event)
    with monkeypatch.context() as patched:
        patched.setattr(executor_module, "threading", stand_in)
        with pytest.raises(RuntimeError):
            services.executors.run_task("T1", "claude_cli")
    assert services.tasks.get_task("T1")["state"] == "paused"
    assert services.executors.background.active() == []


# --------------------------------------------------------------------------- cancelling and cleaning up

def test_cancel_kills_the_program_and_the_attempt_ends_cancelled(background):
    services = background.services
    background.fake.set("claude", sleep=LONG, stdout=claude_out())
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "running"
    attempt = services.executors.background.get("T1")
    handle = attempt._handle
    assert handle is not None and not handle.done()

    started = time.monotonic()
    assert services.executors.background.cancel("T1") is True
    finished_wait(services, 15)

    assert time.monotonic() - started < 10 and handle.done() and handle.wait(0).cancelled is True
    assert services.tasks.get_task("T1")["state"] == "paused"
    assert [e["outcome"] for e in attempt_events(services, "T1")] == ["cancelled"]
    assert services.executors.background.cancel("T1") is False                      # nothing left to cancel
    assert services.executors.background.cancel("NOPE") is False


def test_shutdown_cancels_every_attempt_and_waits_for_them_to_record_their_end(background):
    services = background.services
    background.fake.set("claude", sleep=LONG, stdout=claude_out())
    background.fake.set("codex", sleep=LONG, stdout=codex_out())
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "running"
    assert services.executors.run_task("T3", "codex_cli")["outcome"] == "running"
    handles = [services.executors.background.get(task)._handle for task in ("T1", "T3")]
    assert sorted(row["task_id"] for row in services.executors.background.active()) == ["T1", "T3"]

    assert services.executors.background.shutdown(15) == 0

    assert all(handle.done() and handle.wait(0).cancelled for handle in handles)
    for task_id in ("T1", "T3"):
        assert services.tasks.get_task(task_id)["state"] == "paused"
        assert [e["outcome"] for e in attempt_events(services, task_id)] == ["cancelled"]
    assert services.executors.background.active() == []


def test_a_cancel_asked_before_the_program_exists_kills_it_when_it_starts():
    attempt = executor_module.BackgroundAttempt("T1", "claude_cli")
    assert attempt.cancel() is False                                                # no program yet
    killed = []
    attempt.adopt(SimpleNamespace(cancel=lambda: killed.append(True)))
    assert killed == [True]


def test_after_a_shutdown_nothing_new_is_launched_until_the_background_is_reopened():
    places = BackgroundAttempts(limit=2)
    places.reserve("T1", "claude_cli")
    assert places.shutdown(0.05) == 1                                               # the one that was there is cancelled
    with pytest.raises(ServiceError) as closed:
        places.reserve("T2", "claude_cli")
    assert closed.value.details["reason"] == "background_closed" and closed.value.status == 409
    assert [row["task_id"] for row in places.active()] == ["T1"]                     # nothing was added
    places.reopen()
    assert places.reserve("T2", "claude_cli").task_id == "T2"


def test_a_run_after_the_shutdown_is_refused_before_the_task_changes(background):
    services = background.services
    assert services.executors.background.shutdown(5) == 0
    refused(lambda: services.executors.run_task("T1", "claude_cli"), reason="background_closed")
    assert services.tasks.get_task("T1")["state"] == "open" and event_types(services, "T1") == ["task.created"]
    assert background.fake.calls() == [] and services.executors.background.active() == []
    # A synchronous run does not use the background and is not affected.
    assert services.executors.run_task("T1", "claude_cli", wait=True)["outcome"] == "submitted"


def test_the_api_opens_the_background_again_when_it_starts(background, patient):
    services = background.services
    services.executors.availability = patient
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        assert client.post("/api/v1/executors/run", json={"task_id": "T1", "executor_id": "claude_cli"}).status_code == 200
    refused(lambda: services.executors.run_task("T3", "claude_cli"), reason="background_closed")    # stopped: closed
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        assert client.post("/api/v1/executors/run", json={"task_id": "T3", "executor_id": "claude_cli"}).status_code == 200


def test_a_denied_effect_with_the_policy_on_reserves_nothing_and_changes_nothing(background):
    services = background.services
    arm(services)                                                                   # a document policy, no approval at all
    with pytest.raises(ServiceError) as caught:
        services.executors.run_task("T1", "claude_cli")
    assert caught.value.details["reason"] == "effect_not_authorized" and caught.value.status == 409
    untouched(background)
    assert services.executors.background.active() == [] and services.executors.background.get("T1") is None
    # Nothing stayed reserved: with the barrier off the same task runs in the background.
    services.system.engine.document_policy = None
    background.fake.set("claude", sleep=SLOW, stdout=claude_out())
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "running"


def test_the_number_of_background_attempts_is_bounded_and_there_is_one_per_task():
    places = BackgroundAttempts(limit=2)
    first = places.reserve("T1", "claude_cli")
    with pytest.raises(ServiceError) as same:
        places.reserve("T1", "claude_cli")
    assert same.value.details["reason"] == "background_attempt_active"
    places.reserve("T2", "codex_cli")
    with pytest.raises(ServiceError) as full:
        places.reserve("T3", "codex_cli")
    assert full.value.details["reason"] == "background_capacity" and full.value.details["limit"] == 2
    places.release(first)
    assert places.reserve("T3", "codex_cli").task_id == "T3"
    assert sorted(row["task_id"] for row in places.active()) == ["T2", "T3"]
    assert places.join(0.05) is False                                               # two are "running": bounded wait
    assert places.shutdown(0.05) == 2                                               # nothing ends them here: still bounded


def test_a_full_house_refuses_a_new_background_run_before_the_task_changes(background):
    services = background.services
    services.executors.background = BackgroundAttempts(limit=1)
    background.fake.set("claude", sleep=LONG, stdout=claude_out())
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "running"
    refused(lambda: services.executors.run_task("T3", "claude_cli"), reason="background_capacity")
    assert services.tasks.get_task("T3")["state"] == "open"                          # not claimed, not started
    assert event_types(services, "T3") == ["task.created"]
    services.executors.background.shutdown(15)
    assert services.tasks.get_task("T1")["state"] == "paused"


# --------------------------------------------------------------------------- the process executor's handle sink

def test_capturing_hands_the_handle_of_a_started_program_to_the_callback(tmp_path):
    seen = []
    with process.capturing(seen.append):
        outcome = process.run([sys.executable, "-c", "print('hi')"], cwd=tmp_path, stdin_text=None, timeout_seconds=30)
    assert outcome.exit_code == 0 and len(seen) == 1 and seen[0].done() and seen[0].wait(0) is outcome
    outside = process.start([sys.executable, "-c", "pass"], cwd=tmp_path, stdin_text=None, timeout_seconds=30)
    outside.wait(30)
    assert len(seen) == 1                                                           # only inside the context


def test_a_failing_callback_never_loses_the_program(tmp_path):
    def broken(handle):
        raise RuntimeError("sink failed")

    with process.capturing(broken):
        outcome = process.run([sys.executable, "-c", "print('hi')"], cwd=tmp_path, stdin_text=None, timeout_seconds=30)
    assert outcome.exit_code == 0 and outcome.stdout_tail.strip() == "hi"
    with pytest.raises(ValueError):
        with process.capturing(None):
            pass


def test_an_interrupt_in_the_callback_kills_the_program_nobody_would_wait_for(tmp_path):
    seen = []

    def interrupt(handle):
        seen.append(handle)
        raise KeyboardInterrupt

    with process.capturing(interrupt):
        with pytest.raises(KeyboardInterrupt):
            process.start([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, stdin_text=None,
                          timeout_seconds=120)
    assert seen[0].wait(15).cancelled is True


def test_the_handle_sink_can_kill_a_program_started_through_run(tmp_path):
    handles = []
    with process.capturing(handles.append):
        killer = threading.Timer(0.5, lambda: handles and handles[0].cancel())
        killer.start()
        outcome = process.run([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, stdin_text=None,
                              timeout_seconds=120)
        killer.cancel()
    assert outcome.cancelled is True and outcome.timed_out is False


# --------------------------------------------------------------------------- the API

def test_the_api_answers_202_running_and_200_when_the_attempt_ended(background, patient, policy):
    services = background.services
    background.fake.set("claude", sleep=SLOW, stdout=claude_out())
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        slow = client.post("/api/v1/executors/run", json={"task_id": "T1", "executor_id": "claude_cli"})
        assert slow.status_code == 202, slow.text
        body = slow.json()
        assert body["outcome"] == "running" and body["task_id"] == "T1" and body["attempt"] == 1
        finished_wait(services)
        assert services.tasks.get_task("T1")["state"] == "submitted"

        background.fake.set("claude", stdout=claude_out())
        services.executors.availability = patient
        fast = client.post("/api/v1/executors/run", json={"task_id": "T3", "executor_id": "claude_cli"})
        assert fast.status_code == 200 and fast.json()["outcome"] == "submitted"

        services.executors.availability = policy
        background.fake.set("claude", sleep=1.0, stdout=claude_out())
        started = time.monotonic()
        forced = client.post("/api/v1/executors/run", json={"task_id": "T2", "executor_id": "claude_cli", "wait": True})
        assert forced.status_code == 200 and forced.json()["outcome"] == "submitted"
        assert time.monotonic() - started >= 1.0
        invalid = client.post("/api/v1/executors/run", json={"task_id": "T4", "executor_id": "claude_cli", "wait": "maybe"})
        assert invalid.status_code == 422
        assert services.tasks.get_task("T4")["state"] == "open"


def test_the_api_without_the_policy_is_unchanged(world):
    with TestClient(create_app(services=world.services), base_url="http://127.0.0.1") as client:
        response = client.post("/api/v1/executors/run", json={"task_id": "T1", "executor_id": "claude_cli", "wait": False})
        assert response.status_code == 200 and response.json()["outcome"] == "submitted"


def test_stopping_the_api_kills_the_programs_it_left_in_the_background(background):
    services = background.services
    background.fake.set("claude", sleep=LONG, stdout=claude_out())
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        response = client.post("/api/v1/executors/run", json={"task_id": "T1", "executor_id": "claude_cli"})
        assert response.status_code == 202
        handle = services.executors.background.get("T1")._handle
        assert handle is not None and not handle.done()
    assert handle.done() and handle.wait(0).cancelled is True
    assert services.executors.background.active() == []
    assert [e["outcome"] for e in attempt_events(services, "T1")] == ["cancelled"]


# --------------------------------------------------------------------------- what the code may not do

def test_no_shell_and_no_unbounded_wait_in_the_background_code():
    source = Path(executor_module.__file__).read_text(encoding="utf-8")
    for text in (source, Path(process.__file__).read_text(encoding="utf-8")):
        assert "shell=True" not in text and "os.system(" not in text
    assert not re.search(r"\.join\(\s*\)", source), "a thread is joined without a limit"
    assert "daemon=True" in source                        # a background attempt never keeps the interpreter alive
