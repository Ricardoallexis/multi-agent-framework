# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CNT-T04: holding a task at a usage limit and resuming it when the time comes (contract ``CNT-T01-contrato.md`` 4 and 10).

No real Claude, Codex or Copilot is started. The executor is a fake adapter that follows a script (a usage limit of a
given class and reset time, a result, a failure) and records every launch and resume it receives; the clock is a fake
that the test moves. The lifecycle (ORQ-1), the envelope store (ORQ-3), the event log and the checkpoint (CNT-T03) are
the real services. ``git`` is real only in the test that needs a patch.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from multiagent.config import Settings
from multiagent.contracts import WaitingReason
from multiagent.executors.codex import CodexExecutorAdapter
from multiagent.executors.contract import (
    CommandRecord, ExecutorRunResult, FailureKind, OpenTarget, Permission, RunStatus,
)
from multiagent.executors.registry import ExecutorRegistry
from multiagent.routing.executor_capabilities import descriptor_from_declaration, validate_executor_descriptor
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services import hold as hold_module
from multiagent.services.assignment import AssignmentServices
from multiagent.tasks.envelopes import TASK_RESULT_SCHEMA

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
RESULT = {"schema": TASK_RESULT_SCHEMA, "base_commit": "", "patch": None, "summary": "Finished the work.",
          "decisions": [], "contract_changes": "", "tests_not_run": [], "requests": []}
EVIDENCE = {"source": "test", "provenance": "operator_test", "scope": "claude_cli", "observed_at": "2026-10-11"}


def z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def at(hours: int = 0, minutes: int = 0, seconds: int = 0) -> datetime:
    return NOW + timedelta(hours=hours, minutes=minutes, seconds=seconds)


# --------------------------------------------------------------------------- the clock and the fake executor

class Clock:
    """The service clock: tests move it, nothing else does."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def to(self, moment: datetime) -> None:
        self.now = moment

    def advance(self, **delta: int) -> None:
        self.now += timedelta(**delta)


def limit(kind: str = "plan_limit", *, reset_at: datetime | str | None = None, confidence: str | None = None,
          source: str | None = None, window: str = "five_hour", evidence: str = "You've hit your session limit") -> dict:
    """What an adapter reports (CNT-T02): the class, the window and the reset time with its source and confidence."""
    reset = z(reset_at) if isinstance(reset_at, datetime) else reset_at
    return {"kind": kind, "window": window, "reset_at": reset,
            "reset_source": source or ("app_epoch" if reset else "none"),
            "reset_confidence": confidence or ("high" if reset else "none"),
            "reset_zone": "UTC" if reset else None, "evidence": evidence}


def hit(clock: Clock, kind: str = "plan_limit", *, in_: timedelta | None = None, **kwargs: Any):
    """A scripted step that ends at a limit whose reset is ``in_`` after the moment the executor is called."""
    effective = kwargs.pop("effective", None)

    def step() -> dict:
        reset = clock.now + in_ if in_ is not None else None
        return {"limit": limit(kind, reset_at=reset, **kwargs), "effective": effective}
    return step


@dataclass(frozen=True)
class FakeResult(ExecutorRunResult):
    effective: Mapping[str, Any] | None = None      # what the app says it ran with


class FakeExecutor:
    """An executor application that follows a script and records what it receives."""
    adapter_id = "fake_hold"
    version = "1"

    def __init__(self, executor_id: str = "fake_cli"):
        self.executor_id = executor_id
        self.script: list[Any] = []              # a step (dict) or a function that returns one; empty means "finish"
        self.calls: list[SimpleNamespace] = []
        self.work = None                         # called with the work folder: what the agent edits

    def describe(self, config):
        base = descriptor_from_declaration(self.executor_id, {"surface": "claude_cli", **dict(config or {})})
        return validate_executor_descriptor(replace(base, mode="automatic", adapter_id=self.adapter_id,
                                                    adapter_version=self.version))

    def build_command(self, request):
        return [sys.executable, "-c", "pass"]

    def build_resume_command(self, request, session_id):
        return [sys.executable, "-c", "pass"]

    def render_prompt(self, request):
        return "prompt"

    def parse_output(self, stdout):
        return None, None, None

    def classify_failure(self, exit_code, stderr):
        return FailureKind.PLAN_LIMIT

    def open_target(self, session_id):
        return OpenTarget("url", f"fake://{session_id}")

    def launch(self, request):
        return self._run("launch", request, None)

    def resume(self, request, session_id):
        return self._run("resume", request, session_id)

    def _run(self, kind, request, resume_session):
        step = self.script.pop(0) if self.script else {}
        step = step() if callable(step) else dict(step)
        sid = resume_session or str(uuid.uuid4())
        self.calls.append(SimpleNamespace(kind=kind, request=request, session_id=sid, step=step))
        if self.work is not None:
            self.work(request.workdir)
        failed = step.get("limit") is not None or step.get("fail")
        command = CommandRecord(argv=("fake",), cwd=str(request.workdir), started_at=NOW.isoformat(),
                                exit_code=1 if failed else 0)
        if step.get("limit") is not None:
            return FakeResult(status=RunStatus.QUOTA_EXHAUSTED, session_id=sid, result_envelope=None, usage=None,
                              command=command, stderr_tail="usage limit reached", limit=step["limit"],
                              effective=step.get("effective"))
        if step.get("fail"):
            return FakeResult(status=RunStatus.FAILED, session_id=sid, result_envelope=None, usage=None,
                              command=command, stderr_tail="the program crashed", effective=step.get("effective"))
        return FakeResult(status=RunStatus.COMPLETED, session_id=sid, result_envelope=dict(RESULT),
                          usage={"output_tokens": 1}, command=command, effective=step.get("effective"))


# --------------------------------------------------------------------------- the world

def declaration(*, resume: bool = True, models: tuple[str, ...] = ("fake-small", "fake-big"),
                efforts: tuple[str, ...] = ("low", "high")) -> dict:
    decl: dict[str, Any] = {"surface": "claude_cli", "adapter_id": FakeExecutor.adapter_id, "enabled": True,
                            "mode": "automatic",
                            "model_support": {"status": "supported", "values": list(models), **EVIDENCE},
                            "effort_support": {"status": "supported", "values": list(efforts), **EVIDENCE}}
    if resume:
        decl["resume"] = {"status": "supported", "values": ["--resume <id>"], **EVIDENCE}
    return decl


def routing_config(workdir: Path, decl: dict) -> dict:
    return {"version": 1, "requirement_map": {}, "data_classifications": {}, "profiles": {},
            "executors": {"fake_cli": decl},
            "task_workdirs": {name: str(workdir) for name in ("T1", "T2", "T3")},
            "operator_boundary": {"operator_id": "ops", "authorized_aliases": ["agent:verified"],
                                  "claim_actor_alias": "agent:verified", "operator_grants": [],
                                  "alias_grants": {"agent:verified": []}}}


def install(services: ApplicationServices, fake: FakeExecutor, config: dict) -> None:
    services.system.catalog.executor_routing = config
    services.system.catalog.executor_routing_valid = True
    services.assignment = AssignmentServices(services, registry=ExecutorRegistry(config, [fake]))


def make_world(tmp_path: Path, *, workdir: Path | None = None, **decl_options: Any) -> SimpleNamespace:
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings)
    clock, fake = Clock(), FakeExecutor()
    folder = workdir or (tmp_path / "work")
    folder.mkdir(parents=True, exist_ok=True)
    config = routing_config(folder, declaration(**decl_options))
    install(services, fake, config)
    services.executors.clock = clock
    tasks = services.tasks
    tasks.create_group("CNT", "Continuity", actor="user", authorized=True)
    tasks.create_task("CNT", "T1", "Fix the parser", actor="user", objective="Fix the parser.", file_scope=["src"])
    tasks.create_task("CNT", "T2", "Update the docs", actor="user", objective="Update the docs.", file_scope=["docs"])
    tasks.create_task("CNT", "T3", "Read the module", actor="user", objective="Read the module.")
    world = SimpleNamespace(services=services, fake=fake, clock=clock, config=config, workdir=folder)
    world.run = lambda task_id="T1", **kwargs: services.executors.run_task(task_id, "fake_cli", **kwargs)
    world.hold = lambda task_id="T1": (services.tasks.get_task(task_id).get("continuity") or {}).get("hold")
    world.continuity = lambda task_id="T1": services.tasks.get_task(task_id).get("continuity") or {}
    return world


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)


def events(services, event_type: str, task_id: str | None = None) -> list[dict]:
    found = services.tasks.events(task_id=task_id) if task_id else services.tasks.events()
    return [event for event in found if event["type"] == event_type]


def refused(call, code: str = "invalid_state", reason: str | None = None) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call()
    assert caught.value.code == code, caught.value
    if reason is not None:
        assert caught.value.details.get("reason") == reason, caught.value.details
    return caught.value


def hold_at_the_reset(world, reset: datetime | None = None, **run_options: Any) -> dict:
    """Run T1 until it ends at a plan limit that resets at ``reset`` (default 16:12:32); returns the run."""
    world.fake.script.append({"limit": limit(reset_at=reset or at(4, 12, 32))})
    return world.run(**run_options)


# --------------------------------------------------------------------------- the hold of a limit

def test_a_plan_limit_with_a_reliable_time_holds_the_task_until_the_reset_plus_the_margin(world):
    services = world.services
    out = hold_at_the_reset(world, model="fake-big", effort="high")

    assert out["outcome"] == "quota_exhausted"
    # The fixed 300 minute pause (17:00) is replaced: the executor and the hold wait until 16:12:32 + 2 minutes.
    until = at(4, 14, 32)
    assert out["executor_paused_until"] == until.isoformat() != (NOW + timedelta(minutes=300)).isoformat()
    task = services.tasks.get_task("T1")
    assert task["state"] == "paused" and task["assignee"] == "executor:fake_cli"
    hold = world.hold()
    assert out["hold"] == hold
    assert {key: hold[key] for key in (
        "reason", "window", "reset_at", "reset_source", "reset_confidence", "resume_after", "open_ended", "outcome",
        "executor_id", "actor", "session_id", "attempt", "mode", "decision", "since")} == {
        "reason": "plan_limit", "window": "five_hour", "reset_at": "2026-10-11T16:12:32Z",
        "reset_source": "app_epoch", "reset_confidence": "high", "resume_after": "2026-10-11T16:14:32Z",
        "open_ended": False, "outcome": "wait_until_reset", "executor_id": "fake_cli", "actor": "executor:fake_cli",
        "session_id": out["session_id"], "attempt": 1, "mode": "semi_auto", "decision": "pending",
        "since": "2026-10-11T12:00:00Z"}
    assert hold["settings"]["model"] == "fake-big" and hold["settings"]["effort"] == "high"
    assert hold["settings"]["permission"] == "write" and hold["settings"]["adapter"] == "fake_hold"
    assert len(hold["settings"]["workdir"]) == 12          # a fingerprint, never a local path
    assert str(world.workdir) not in json.dumps(hold)
    continuity = world.continuity()
    assert continuity["failed_resumes"] == 0 and continuity["rate_retries"] == 0
    assert continuity["handoffs"] == 0 and continuity["chain"] == ["fake_cli"]
    # No state was added: the task is `paused`, and the executor service shows the same hold.
    assert services.executors.status("T1")["task"]["hold"] == hold


def test_the_pause_event_and_the_held_event_carry_the_class_and_the_reset(world):
    services = world.services
    out = hold_at_the_reset(world)

    (pause,) = events(services, "executor.paused", "T1")
    assert pause["ref"] == "fake_cli" and pause["task_id"] == "T1"
    assert pause["payload"] == {
        "executor_id": "fake_cli", "until": at(4, 14, 32).isoformat(), "minutes": 255, "reason": "quota_exhausted",
        "limit_kind": "plan_limit", "window": "five_hour", "reset_at": "2026-10-11T16:12:32Z",
        "reset_source": "app_epoch", "reset_confidence": "high", "open_ended": False, "outcome": "wait_until_reset"}
    (held,) = events(services, "continuity.held", "T1")
    assert held["payload"] == {
        "reason": "plan_limit", "window": "five_hour", "resume_after": "2026-10-11T16:14:32Z", "open_ended": False,
        "outcome": "wait_until_reset", "mode": "semi_auto", "executor_id": "fake_cli", "attempt": 1,
        "reset_source": "app_epoch", "reset_confidence": "high", "handoffs": 0, "max_handoffs": 1, "failed_resumes": 0}
    # The hold remembers which pause goes with it.
    assert world.hold()["pause_seq"] == pause["seq"]
    assert [event["type"] for event in services.tasks.events(task_id="T1")][-7:] == [
        "executor.attempt_finished", "executor.paused", "task.paused", "continuity.held", "task.context_updated",
        "continuity.limit_detected", "task.checkpointed"]
    assert out["checkpoint"]["saved"] is True
    for event in services.tasks.events():
        assert len(json.dumps(event["payload"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 2048


def test_the_task_pause_and_its_hold_are_one_lifecycle_update(world):
    services, real, seen = world.services, world.services.tasks.transition, []

    def spy(task_id, action, **kwargs):
        seen.append((action, kwargs.get("continuity")))
        return real(task_id, action, **kwargs)

    services.tasks.transition = spy
    hold_at_the_reset(world)
    (pause,) = [continuity for action, continuity in seen if action == "pause"]
    assert pause["hold"]["reason"] == "plan_limit" and pause["failed_resumes"] == 0
    assert services.tasks.get_task("T1")["state"] == "paused" and world.hold()["resume_after"] == "2026-10-11T16:14:32Z"


def test_a_task_that_moved_on_keeps_no_hold_but_the_executor_stays_paused(world):
    services, real = world.services, world.services.tasks.transition

    def moved_on(task_id, action, **kwargs):
        if action == "pause":
            raise ServiceError("invalid_state", "The task is no longer running", status=409)
        return real(task_id, action, **kwargs)

    services.tasks.transition = moved_on
    out = hold_at_the_reset(world)
    assert out["hold"] is None and "task_not_paused: invalid_state" in out["notes"]
    assert world.hold() is None and events(services, "continuity.held", "T1") == []
    (pause,) = events(services, "executor.paused", "T1")
    assert pause["payload"]["until"] == at(4, 14, 32).isoformat()


def test_without_a_reliable_time_the_operators_fixed_pause_applies(world):
    services = world.services
    services.settings.executor_quota_pause_minutes = 45
    world.fake.script.append({"limit": limit("limit_unknown", window="unknown")})
    out = world.run()

    assert out["executor_paused_until"] == at(minutes=45).isoformat()
    hold = world.hold()
    assert (hold["reason"], hold["outcome"], hold["reset_source"], hold["reset_confidence"]) == (
        "limit_unknown", "wait_fixed_pause", "configured", "low")
    assert hold["reset_at"] == "2026-10-11T12:45:00Z" and hold["resume_after"] == "2026-10-11T12:45:00Z"
    assert "reported_reset_at" not in hold
    assert events(services, "executor.paused", "T1")[0]["payload"]["minutes"] == 45


@pytest.mark.parametrize("confidence, source", [("low", "app_text"), ("none", "app_text")])
def test_a_time_that_is_not_reliable_is_kept_apart_and_the_fixed_pause_applies(world, confidence, source):
    world.fake.script.append({"limit": limit(reset_at=at(2), confidence=confidence, source=source)})
    out = world.run()
    hold = world.hold()
    assert hold["outcome"] == "wait_fixed_pause" and hold["reset_source"] == "configured"
    assert hold["reported_reset_at"] == "2026-10-11T14:00:00Z"
    assert out["executor_paused_until"] == at(minutes=300).isoformat()


def test_a_reset_more_than_eight_days_ahead_is_not_believed(world):
    world.fake.script.append({"limit": limit(reset_at=at(24 * 9))})
    world.run()
    assert world.hold()["outcome"] == "wait_fixed_pause" and world.hold()["reset_source"] == "configured"


def test_a_limit_the_adapter_did_not_classify_counts_as_unknown(world):
    original = world.fake._run                 # an old adapter: a limit status and no limit information

    def old_adapter(kind, request, resume_session):
        result = original(kind, request, resume_session)
        return replace(result, status=RunStatus.QUOTA_EXHAUSTED, result_envelope=None, limit=None)

    world.fake._run = old_adapter
    out = world.run()
    assert out["outcome"] == "quota_exhausted"
    assert world.hold()["reason"] == "limit_unknown" and world.hold()["outcome"] == "wait_fixed_pause"


# --------------------------------------------------------------------------- credits: no end until a person lifts it

def test_credits_exhausted_pauses_the_executor_with_no_end_until_a_person_lifts_it(world):
    services = world.services
    world.fake.script.append({"limit": limit("credits_exhausted", window="spend", evidence="credit balance is too low")})
    out = world.run()

    assert out["executor_paused_until"] is None
    hold = world.hold()
    assert (hold["reason"], hold["outcome"], hold["open_ended"], hold["resume_after"]) == (
        "credits_exhausted", "open_ended", True, None)
    (pause,) = events(services, "executor.paused", "T1")
    assert pause["payload"]["open_ended"] is True and pause["payload"]["until"] is None
    executor = next(e for e in services.executors.status()["executors"] if e["executor_id"] == "fake_cli")
    assert executor["paused_open_ended"] is True and executor["available"] is False
    assert executor["paused_until"] is None and executor["pause_reason"] == "credits_exhausted"

    # Another task is refused on this executor, and a month later it still is.
    error = refused(lambda: services.executors.run_task("T2", "fake_cli"), reason="executor_paused")
    assert error.details["open_ended"] is True and error.details["until"] is None
    world.clock.advance(days=30)
    report = services.executors.resume_due()
    assert report["resumed"] == [] and report["waiting"][0]["why"] == "executor_paused"
    assert len(world.fake.calls) == 1

    # A person lifts it: the event says who and why, and the held task becomes due.
    lifted = services.executors.lift_pause("fake_cli", actor="user", reason="added credit")
    assert lifted == {"executor_id": "fake_cli", "lifted": True, "was_open_ended": True, "was_until": None,
                      "held_tasks": ["T1"]}
    (event,) = events(services, "executor.pause_lifted")
    assert event["ref"] == "fake_cli" and event["actor"] == "user"
    assert event["payload"]["by"] == "user" and event["payload"]["reason"] == "added credit"
    assert event["payload"]["was"] == {"until": None, "open_ended": True, "limit_kind": "credits_exhausted"}
    again = services.executors.lift_pause("fake_cli")                    # idempotent: nothing is paused any more
    assert again["lifted"] is False and len(events(services, "executor.pause_lifted")) == 1

    report = services.executors.resume_due()
    assert [(item["task_id"], item["mode"], item["outcome"]) for item in report["resumed"]] == [
        ("T1", "resume", "submitted")]
    assert report["due"][0]["why"] == "lifted" and world.hold() is None


def test_lifting_a_timed_pause_makes_the_hold_due_before_its_time(world):
    services = world.services
    hold_at_the_reset(world)
    world.clock.advance(minutes=30)
    assert services.executors.resume_due()["resumed"] == []
    lifted = services.executors.lift_pause("fake_cli", actor="user")
    assert lifted["lifted"] is True and lifted["was_until"] == at(4, 14, 32).isoformat()
    report = services.executors.resume_due()
    assert [item["task_id"] for item in report["resumed"]] == ["T1"] and report["due"][0]["why"] == "lifted"
    assert world.fake.calls[1].kind == "resume"


def test_lift_pause_and_resume_due_check_their_input(world):
    executors = world.services.executors
    refused(lambda: executors.lift_pause("bad id!"), "invalid_request")
    refused(lambda: executors.lift_pause("nobody"), "not_found")
    refused(lambda: executors.lift_pause("fake_cli", reason=3), "invalid_request")
    refused(lambda: executors.resume_due(limit=0), "invalid_request")
    refused(lambda: executors.resume_due(limit=True), "invalid_request")
    refused(lambda: executors.resume_due(task_id="bad id!"), "invalid_request")
    assert executors.resume_due() == {"at": z(NOW).replace("Z", "+00:00"), "dry_run": False, "held": 0, "due": [],
                                      "resumed": [], "waiting": [], "skipped": []}


# --------------------------------------------------------------------------- resuming when the time comes

def test_when_the_time_comes_the_session_is_resumed_with_its_history_and_every_recorded_setting(world):
    services = world.services
    first = hold_at_the_reset(world, model="fake-big", effort="high")
    world.clock.to(at(4, 14, 31))
    early = services.executors.resume_due()
    assert early["resumed"] == [] and early["waiting"][0]["why"] == "executor_paused"
    assert early["waiting"][0]["paused_until"] == at(4, 14, 32).isoformat()

    world.clock.to(at(4, 14, 32))
    report = services.executors.resume_due()

    (resumed,) = report["resumed"]
    assert resumed == {"task_id": "T1", "mode": "resume", "outcome": "submitted", "executor_id": "fake_cli",
                       "session_id": first["session_id"], "attempt": 1, "config_drift": [], "minutes_waited": 254,
                       "hold": None}
    # The fake executor received a resume of the same session with everything that was recorded.
    launch, resume = world.fake.calls
    assert (launch.kind, resume.kind) == ("launch", "resume") and resume.session_id == first["session_id"]
    request = resume.request
    assert (request.model, request.effort, request.permission) == ("fake-big", "high", Permission.WRITE)
    assert request.workdir == world.workdir and request.task_envelope["task_id"] == "T1"
    # The task moved on with the lifecycle's own transitions, and the hold is gone.
    task = services.tasks.get_task("T1")
    assert task["state"] == "submitted" and task["attempt"] == 1 and world.hold() is None
    (event,) = events(services, "continuity.resumed", "T1")
    assert event["ref"] == "fake_cli" and event["actor"] == "executor:fake_cli"
    assert event["payload"] == {"mode": "resume", "trigger": "auto", "minutes": 254, "config_drift": [],
                                "reason": "plan_limit", "executor_id": "fake_cli",
                                "previous_session": first["session_id"], "attempt": 1}
    assert report["due"][0]["why"] == "reset_reached" and report["held"] == 1
    assert [e["type"] for e in services.tasks.events(task_id="T1")].count("task.resumed") == 1


def test_the_real_codex_adapter_gets_the_whole_recorded_configuration_in_its_resume_command(world):
    services = world.services
    hold_at_the_reset(world, model="fake-big", effort="high")
    world.clock.to(at(4, 14, 32))
    services.executors.resume_due()
    request = world.fake.calls[1].request
    thread = "0199a213-81c0-7800-8aa1-bbab2a035a53"
    argv = CodexExecutorAdapter(command=("codex",)).build_resume_command(request, thread)
    assert argv[1:5] == ["exec", "resume", "--json", "-c"] and argv[5] == "sandbox_mode=workspace-write"
    assert argv[argv.index("-m") + 1] == "fake-big"
    assert "model_reasoning_effort=high" in argv and argv[-2:] == [thread, "-"]


def test_a_task_that_is_no_longer_owned_by_the_same_agent_is_not_resumed(world):
    services = world.services
    hold_at_the_reset(world)
    # Another agent takes the task over by hand: it is paused again, now owned by someone else, with the old hold still in it.
    services.tasks.transition("T1", "unclaim", actor="user")
    services.tasks.transition("T1", "claim", actor="agent:other")
    services.tasks.transition("T1", "start", actor="agent:other")
    services.tasks.transition("T1", "pause", actor="agent:other")
    world.clock.to(at(5))
    report = services.executors.resume_due()
    assert report["resumed"] == [] and len(world.fake.calls) == 1
    (skipped,) = report["skipped"]
    assert skipped["task_id"] == "T1" and skipped["why"] == "assignee_changed" and skipped["assignee"] == "agent:other"


def test_a_hold_that_is_not_a_limit_is_left_to_its_own_watcher(world):
    services = world.services
    hold_at_the_reset(world)
    services.tasks._edit_continuity("T1", {"hold": {**world.hold(), "reason": "dependency"}}, actor="user")
    world.clock.to(at(5))
    report = services.executors.resume_due()
    assert report["resumed"] == [] and [(i["why"], i["reason"]) for i in report["skipped"]] == [
        ("not_a_limit_hold", "dependency")]


def test_dry_run_only_reports_and_limit_resumes_that_many(world):
    services = world.services
    hold_at_the_reset(world, reset=at(1))                      # T1 is held until 13:02
    world.clock.to(at(1, 5))
    world.fake.script.append({"limit": limit(reset_at=at(2))})
    world.run("T2")                                           # T2 is held until 14:02
    world.clock.to(at(3))

    dry = services.executors.resume_due(dry_run=True)
    assert dry["dry_run"] is True and [i["task_id"] for i in dry["due"]] == ["T1", "T2"] and dry["resumed"] == []
    assert len(world.fake.calls) == 2 and world.hold("T1") and world.hold("T2")

    one = services.executors.resume_due(limit=1)
    assert [i["task_id"] for i in one["resumed"]] == ["T1"]
    assert [(i["task_id"], i["why"]) for i in one["waiting"]] == [("T2", "limit")] and world.hold("T2")
    only = services.executors.resume_due(task_id="T1")
    assert only["held"] == 0 and only["resumed"] == []
    assert [i["task_id"] for i in services.executors.resume_due(task_id="T2")["resumed"]] == ["T2"]


def test_a_session_opened_in_an_app_is_neither_resumed_nor_replaced(world):
    services = world.services
    out = hold_at_the_reset(world)
    services.executors.record_app_open(out["session_id"], source="claude")
    world.clock.to(at(5))
    report = services.executors.resume_due()
    assert report["resumed"] == [] and len(world.fake.calls) == 1
    (skipped,) = report["skipped"]
    assert skipped["why"] == "session_open_in_app" and skipped["task_id"] == "T1"
    assert events(services, "session.retired") == [] and world.hold()      # nothing changed
    world.clock.to(at(12, 1))                                              # twelve hours after the open
    assert [i["mode"] for i in services.executors.resume_due()["resumed"]] == ["resume"]


def test_an_executor_that_is_paused_again_makes_the_hold_wait(world):
    services = world.services
    hold_at_the_reset(world)
    world.clock.to(at(5))
    world.fake.script.append({"limit": limit("credits_exhausted", window="spend")})
    world.run("T2")                                                     # another task pauses the executor with no end
    report = services.executors.resume_due()
    assert report["resumed"] == [] and report["waiting"][0]["why"] == "executor_paused"


def test_a_failed_attempt_that_is_not_a_limit_holds_nothing(world):
    world.fake.script.append({"fail": True})
    out = world.run()
    assert out["outcome"] == "failed" and out["hold"] is None
    assert world.services.tasks.get_task("T1")["state"] == "paused" and world.hold() is None
    assert events(world.services, "continuity.held") == [] and events(world.services, "executor.paused") == []


# --------------------------------------------------------------------------- rate limits, failures and reclassifying

def test_a_rate_limit_retries_after_60_and_300_seconds_then_falls_back_to_the_fixed_pause(world):
    services = world.services
    world.fake.script.extend([{"limit": limit("rate_limited", window="unknown", evidence="429 Too Many Requests")}] * 3)

    first = world.run()
    assert first["executor_paused_until"] == at(seconds=60).isoformat()
    assert first["checkpoint"]["skipped"] == "rate_limited_retry"            # a retry is coming: nothing is saved
    assert events(services, "task.checkpointed") == [] and len(events(services, "continuity.limit_detected")) == 1
    hold = world.hold()
    assert (hold["outcome"], hold["retry"], hold["resume_after"], hold["reset_at"]) == (
        "wait_retry", {"n": 1, "of": 2}, "2026-10-11T12:01:00Z", None)
    assert world.continuity()["rate_retries"] == 1 and world.continuity()["failed_resumes"] == 0

    world.clock.to(at(seconds=59))
    assert services.executors.resume_due()["resumed"] == []
    world.clock.to(at(seconds=60))
    second = services.executors.resume_due()["resumed"][0]
    assert second["outcome"] == "quota_exhausted" and second["mode"] == "resume"
    hold = world.hold()
    assert hold["retry"] == {"n": 2, "of": 2} and hold["resume_after"] == "2026-10-11T12:06:00Z"
    assert world.continuity()["rate_retries"] == 2 and world.continuity()["failed_resumes"] == 0
    assert events(services, "task.checkpointed") == [] and len(events(services, "continuity.limit_detected")) == 2

    world.clock.to(at(minutes=6))
    third = services.executors.resume_due()["resumed"][0]
    # The retries are used: the fixed pause applies, the checkpoint is finally saved, and this counts as a failed resume.
    assert third["outcome"] == "quota_exhausted" and len(events(services, "task.checkpointed")) == 1
    hold = world.hold()
    assert hold["outcome"] == "wait_fixed_pause" and hold["reset_source"] == "configured" and "retry" not in hold
    assert hold["resume_after"] == "2026-10-11T17:06:00Z"
    assert world.continuity()["failed_resumes"] == 1


def test_a_success_ends_the_streaks(world):
    services = world.services
    world.fake.script.extend([{"limit": limit("rate_limited", window="unknown")}, {}])
    world.run()
    assert world.continuity()["rate_retries"] == 1
    world.clock.advance(seconds=60)
    services.executors.resume_due()
    task = services.tasks.get_task("T1")
    assert task["state"] == "submitted" and task["continuity"].get("hold") is None
    assert task["continuity"]["rate_retries"] == 0 and task["continuity"]["failed_resumes"] == 0


def test_if_the_resumed_run_hits_a_limit_again_it_is_classified_again(world):
    services = world.services
    hold_at_the_reset(world)
    world.fake.script.append({"limit": limit("credits_exhausted", window="spend", evidence="monthly spend limit")})
    world.clock.to(at(4, 14, 32))

    (resumed,) = services.executors.resume_due()["resumed"]

    assert resumed["outcome"] == "quota_exhausted" and resumed["hold"]["reason"] == "credits_exhausted"
    hold = world.hold()
    assert hold["open_ended"] is True and hold["resume_after"] is None and world.continuity()["failed_resumes"] == 1
    executor = next(e for e in services.executors.status()["executors"] if e["executor_id"] == "fake_cli")
    assert executor["paused_open_ended"] is True
    assert [e["payload"]["reason"] for e in events(services, "continuity.held", "T1")] == [
        "plan_limit", "credits_exhausted"]


def test_three_failed_resumptions_in_a_row_stop_the_automatic_resume(world):
    services = world.services
    world.fake.script.extend([hit(world.clock, in_=timedelta(hours=1))] * 4)
    world.run()
    for expected in (1, 2, 3):
        world.clock.advance(hours=2)
        (resumed,) = services.executors.resume_due()["resumed"]
        assert resumed["outcome"] == "quota_exhausted"
        assert world.continuity()["failed_resumes"] == expected
    assert world.hold()["blocked"] == "failed_resumes"
    assert events(services, "continuity.held", "T1")[-1]["payload"]["blocked"] == "failed_resumes"

    world.clock.advance(days=2)
    calls = len(world.fake.calls)
    report = services.executors.resume_due()
    assert report["resumed"] == [] and [(i["task_id"], i["why"]) for i in report["skipped"]] == [
        ("T1", "blocked:failed_resumes")]
    assert len(world.fake.calls) == calls

    # A person decides: running it by hand clears the hold and the count.
    out = world.run()
    assert out["outcome"] == "submitted" and world.hold() is None
    assert world.continuity()["failed_resumes"] == 0
    (manual,) = [e for e in events(services, "continuity.resumed", "T1") if e["payload"]["trigger"] == "manual"]
    assert manual["payload"]["mode"] == "relaunch" and manual["payload"]["reason"] == "plan_limit"


def test_resuming_a_held_task_by_hand_clears_its_hold_too(world):
    services = world.services
    first = hold_at_the_reset(world)
    world.clock.to(at(5))
    out = services.executors.resume_task("T1")
    assert out["outcome"] == "submitted" and out["session_id"] == first["session_id"] and world.hold() is None
    (event,) = events(services, "continuity.resumed", "T1")
    assert event["payload"]["trigger"] == "manual" and event["payload"]["mode"] == "resume"
    assert event["payload"]["minutes"] == 300


def test_a_hold_that_changed_after_it_was_checked_is_not_resumed(world):
    services = world.services
    hold_at_the_reset(world)
    stale = {**world.hold(), "since": "2026-10-11T11:00:00Z"}          # what an earlier call saw
    world.clock.to(at(5))
    error = refused(lambda: services.executors._resume_one({"task_id": "T1"}, stale, None), reason="hold_changed")
    assert error.details["task_id"] == "T1" and len(world.fake.calls) == 1 and world.hold()


def test_running_a_held_task_by_hand_clears_its_hold(world):
    services = world.services
    first = hold_at_the_reset(world)
    world.clock.to(at(5))
    out = world.run()
    assert out["outcome"] == "submitted" and out["session_id"] != first["session_id"] and world.hold() is None
    (event,) = events(services, "continuity.resumed", "T1")
    assert event["payload"]["trigger"] == "manual" and event["payload"]["previous_session"] == first["session_id"]
    assert services.tasks.get_task("T1")["state"] == "submitted"


# --------------------------------------------------------------------------- config drift: relaunch from the checkpoint

def test_a_drift_in_the_permission_relaunches_from_the_checkpoint_and_retires_the_old_session(world):
    services = world.services
    first = hold_at_the_reset(world, model="fake-big", effort="high")
    # The task lost its file scope while it waited: a resume would now be read-only, not what the session started with.
    services.tasks.update_scope("T1", file_scope=[], actor="executor:fake_cli",
                                expected_revision=services.tasks.get_task("T1")["revision"])
    world.clock.to(at(4, 14, 32))

    (resumed,) = services.executors.resume_due()["resumed"]

    assert resumed["mode"] == "relaunch" and resumed["config_drift"] == ["permission"]
    assert resumed["session_id"] != first["session_id"] and resumed["outcome"] == "submitted"
    launch, relaunch = world.fake.calls
    assert (launch.kind, relaunch.kind) == ("launch", "launch")
    assert (relaunch.request.model, relaunch.request.effort) == ("fake-big", "high")
    (retired,) = events(services, "session.retired")
    assert retired["ref"] == first["session_id"] and retired["payload"]["reason"] == "superseded"
    (event,) = events(services, "continuity.resumed", "T1")
    assert event["payload"]["mode"] == "relaunch" and event["payload"]["config_drift"] == ["permission"]
    assert len(events(services, "task.checkpointed", "T1")) == 1             # the saved checkpoint was reused
    # The framework never resumes the retired session again.
    assert services.executors.session_guard(first["session_id"]) is False
    assert services.executors.session_guard(resumed["session_id"]) is True
    sessions = services.executors.status("T1")["task"]["sessions"]
    assert sessions[0]["retired"]["reason"] == "superseded" and "retired" not in sessions[1]


def test_an_executor_that_does_not_declare_resume_is_relaunched(tmp_path):
    world = make_world(tmp_path, resume=False)
    first = hold_at_the_reset(world)
    world.clock.to(at(4, 14, 32))
    (resumed,) = world.services.executors.resume_due()["resumed"]
    assert resumed["mode"] == "relaunch" and resumed["config_drift"] == [] and resumed["session_id"] != first["session_id"]
    assert [call.kind for call in world.fake.calls] == ["launch", "launch"]
    assert events(world.services, "session.retired")[0]["payload"]["reason"] == "superseded"


def test_a_model_the_executor_no_longer_takes_is_dropped_on_the_relaunch(world):
    services = world.services
    hold_at_the_reset(world, model="fake-big", effort="high")
    narrower = routing_config(world.workdir, declaration(models=("fake-small",)))     # fake-big is gone
    install(services, world.fake, narrower)
    world.clock.to(at(4, 14, 32))

    (resumed,) = services.executors.resume_due()["resumed"]

    assert resumed["mode"] == "relaunch" and resumed["config_drift"] == ["model"]
    request = world.fake.calls[1].request
    assert request.model is None and request.effort == "high"


def test_a_drift_the_app_reports_after_a_resume_makes_the_next_resume_a_relaunch(world):
    services = world.services
    hold_at_the_reset(world, model="fake-big", effort="high")
    # The app resumed the session with another model than the one recorded, and hit the limit again.
    world.fake.script.extend([hit(world.clock, in_=timedelta(hours=1), effective={"model": "fake-small", "effort": "high"}),
                              {}])
    world.clock.to(at(4, 14, 32))
    (first,) = services.executors.resume_due()["resumed"]
    assert first["mode"] == "resume" and first["outcome"] == "quota_exhausted"
    assert world.continuity()["config_drift"] == ["model"]
    (finished,) = [e for e in events(services, "executor.attempt_finished", "T1") if e["payload"].get("config_drift")]
    assert finished["payload"]["config_drift"] == ["model"]

    world.clock.advance(hours=2)
    (second,) = services.executors.resume_due()["resumed"]
    assert second["mode"] == "relaunch" and second["config_drift"] == ["model"] and second["outcome"] == "submitted"
    assert "config_drift" not in world.continuity()                     # a new session starts clean
    assert [event["payload"]["mode"] for event in events(services, "continuity.resumed", "T1")] == ["resume", "relaunch"]
    assert len(events(services, "session.retired")) == 1


def test_a_resume_whose_app_reports_nothing_different_records_no_drift(world):
    services = world.services
    hold_at_the_reset(world, model="fake-big", effort="high")
    world.fake.script.append({"effective": {"model": "fake-big", "effort": "high", "permission": "write"}})
    world.clock.to(at(4, 14, 32))
    services.executors.resume_due()
    assert "config_drift" not in world.continuity()
    assert all("config_drift" not in e["payload"] for e in events(services, "executor.attempt_finished", "T1"))


# --------------------------------------------------------------------------- the checkpoint inside the hold

def _git(folder: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    done = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", *args], cwd=folder,
                          env=env, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout


@needs_git
def test_the_patch_of_the_checkpoint_is_written_into_the_hold_and_survives_a_relaunch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "app.py").write_bytes(b"VALUE = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    world = make_world(tmp_path, workdir=repo, resume=False)
    world.fake.work = lambda folder: (folder / "app.py").write_bytes(b"VALUE = 2\n")

    out = hold_at_the_reset(world)

    ref = out["checkpoint"]["patch"]["ref"]
    assert ref.startswith("blob:sha256:") and world.hold()["checkpoint"] == ref and out["hold"]["checkpoint"] == ref
    assert world.services.executors.latest_checkpoint("T1")["patch"]["ref"] == ref
    world.clock.to(at(4, 14, 32))
    (resumed,) = world.services.executors.resume_due()["resumed"]
    assert resumed["mode"] == "relaunch" and len(events(world.services, "task.checkpointed", "T1")) == 1
    # The relaunched agent reads the same checkpoint in its envelope.
    envelope = world.services.envelopes.get_envelope("T1")
    assert ref in envelope["context_refs"] and any(text.startswith("[checkpoint]") for text in envelope["constraints"])


# --------------------------------------------------------------------------- the pure hold module

NOW_UTC = NOW


def info(kind="plan_limit", **fields) -> dict:
    return {"kind": kind, "window": "five_hour", "reset_at": None, "reset_source": None, "reset_confidence": None,
            "reset_zone": None, "evidence": "limit", **fields}


def test_plan_hold_chooses_how_long_a_limit_holds():
    reliable = hold_module.plan_hold(info(reset_at="2026-10-11T16:12:32Z", reset_source="app_text",
                                          reset_confidence="medium"), now=NOW, configured_minutes=300)
    assert (reliable.outcome, reliable.resume_after, reliable.until) == ("wait_until_reset", at(4, 14, 32), at(4, 14, 32))
    assert reliable.reset_source == "app_text" and reliable.reported_reset_at is None

    fixed = hold_module.plan_hold(info("limit_unknown"), now=NOW, configured_minutes=45)
    assert (fixed.outcome, fixed.resume_after, fixed.reset_source, fixed.reset_confidence) == (
        "wait_fixed_pause", at(minutes=45), "configured", "low")

    doubtful = hold_module.plan_hold(info(reset_at="2026-10-11T15:00:00Z", reset_confidence="low"), now=NOW,
                                     configured_minutes=60)
    assert doubtful.outcome == "wait_fixed_pause" and doubtful.reported_reset_at == "2026-10-11T15:00:00Z"
    assert doubtful.resume_after == at(minutes=60)

    credits = hold_module.plan_hold(info("credits_exhausted", reset_at="2026-10-11T15:00:00Z",
                                         reset_confidence="high"), now=NOW, configured_minutes=60)
    assert (credits.open_ended, credits.resume_after, credits.outcome) == (True, None, "open_ended")

    backoff = [hold_module.plan_hold(info("rate_limited"), now=NOW, configured_minutes=90, rate_retries=used)
               for used in range(3)]
    assert [(p.outcome, p.retries_left) for p in backoff] == [("wait_retry", 2), ("wait_retry", 1),
                                                              ("wait_fixed_pause", 0)]
    assert [p.resume_after for p in backoff] == [at(seconds=60), at(seconds=300), at(minutes=90)]
    assert hold_module.plan_hold(info("nonsense"), now=NOW, configured_minutes=30).reason == "limit_unknown"


def test_hold_due_follows_the_time_the_pause_and_the_lift():
    hold = {"reason": "plan_limit", "resume_after": "2026-10-11T16:14:32Z", "open_ended": False}
    assert hold_module.hold_due(hold, now=at(4, 14, 31), executor_paused=False) == (False, "not_due")
    assert hold_module.hold_due(hold, now=at(4, 14, 32), executor_paused=False) == (True, "reset_reached")
    assert hold_module.hold_due(hold, now=at(5), executor_paused=True) == (False, "executor_paused")
    assert hold_module.hold_due(hold, now=at(1), executor_paused=False, lifted=True) == (True, "lifted")
    open_ended = {"reason": "credits_exhausted", "resume_after": None, "open_ended": True}
    assert hold_module.hold_due(open_ended, now=at(24 * 40), executor_paused=False) == (False, "open_ended")
    assert hold_module.hold_due(open_ended, now=at(1), executor_paused=False, lifted=True) == (True, "lifted")
    assert hold_module.hold_due({**hold, "blocked": "failed_resumes"}, now=at(5), executor_paused=False) == (
        False, "blocked:failed_resumes")
    assert hold_module.hold_due({**hold, "reason": "approval"}, now=at(5), executor_paused=False) == (
        False, "not_a_limit_hold")
    assert hold_module.hold_due({**hold, "resume_after": "later"}, now=at(5), executor_paused=False) == (
        False, "resume_after_invalid")


def test_drift_is_a_changed_value_not_an_unknown_one():
    recorded = {"model": "fake-big", "effort": "high", "permission": "write", "adapter": "a"}
    assert hold_module.detect_drift(recorded, {"model": "fake-big", "permission": "write"}) == []
    assert hold_module.detect_drift(recorded, {"model": "fake-small", "permission": "read"}) == ["model", "permission"]
    assert hold_module.detect_drift(recorded, {"effort": None}) == ["effort"]          # the app fell back to its default
    assert hold_module.detect_drift({"model": None}, {"model": "x"}) == []              # nothing was recorded


def test_the_continuity_block_merges_without_touching_its_siblings():
    execution = {"attention": {"stuck": True}, "continuity": {"hold": {"reason": "plan_limit"}, "handoffs": 1}}
    merged = hold_module.merge_continuity(execution, {"hold": None, "failed_resumes": 2})
    assert merged == {"attention": {"stuck": True}, "continuity": {"handoffs": 1, "failed_resumes": 2}}
    assert execution["continuity"]["hold"] == {"reason": "plan_limit"}                   # the input is not changed
    assert hold_module.merge_continuity({"continuity": {"hold": {"a": 1}}}, {"hold": None}) == {}
    assert hold_module.merge_continuity(None, {"hold": {"a": 1}}) == {"continuity": {"hold": {"a": 1}}}
    task = {"execution": merged}
    assert hold_module.hold_of(task) is None and hold_module.continuity_of(task)["failed_resumes"] == 2
    assert hold_module.continuity_of({"execution": "broken"}) == {}


def test_the_hold_of_a_task_keeps_the_other_blocks_of_its_execution(world):
    services = world.services
    hold_at_the_reset(world)
    # A sibling block (for example the attention of CNT-T09) is not lost when the hold is written or cleared.
    row = services.tasks.store.get_task("T1")
    with services.tasks.store.transaction() as conn:
        services.tasks.store.update_task(conn, "T1", expected_revision=row["revision"],
                                         execution={**row["execution"], "attention": {"kind": "stuck"}})
    world.clock.to(at(4, 14, 32))
    services.executors.resume_due()
    assert services.tasks.store.get_task("T1")["execution"].get("attention") == {"kind": "stuck"}
    assert world.hold() is None


def test_a_run_can_wait_for_a_limit():
    assert WaitingReason("limit_reached") is WaitingReason.LIMIT_REACHED
