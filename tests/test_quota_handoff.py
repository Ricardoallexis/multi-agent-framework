# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CNT-T05: the continuity policy and the hand-off of a held task (contract ``CNT-T01-contrato.md`` sections 7 to 9).

No real Claude, Codex or Copilot is started. The executors are fake adapters that follow a script (a usage limit with a
reset time, or a result) and record every launch and resume; the clock is a fake the test moves. The lifecycle (ORQ-1), the
envelope store, the event log, the checkpoint (CNT-T03) and the hold (CNT-T04) are the real services. The decision of a
person on the card (CNT-T06) is simulated by writing exactly what that card records: ``hold.decision`` ``delegate``,
``hold.candidate``, ``hold.blocked`` ``delegation_requested`` and the event ``continuity.decided``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.executors.contract import (
    CommandRecord, ExecutorRunResult, FailureKind, OpenTarget, RunStatus,
)
from multiagent.executors.registry import ExecutorRegistry
from multiagent.executors.selection import select_for_handoff
from multiagent.gui.token import ensure_token
from multiagent.routing.executor_capabilities import descriptor_from_declaration, validate_executor_descriptor
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services import handoff
from multiagent.services import hold as hold_module
from multiagent.services.assignment import AssignmentServices
from multiagent.tasks.envelopes import TASK_RESULT_SCHEMA

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
RESULT = {"schema": TASK_RESULT_SCHEMA, "base_commit": "", "patch": None, "summary": "Finished the work.",
          "decisions": [], "contract_changes": "", "tests_not_run": [], "requests": []}
EVIDENCE = {"source": "test", "provenance": "operator_test", "scope": "claude_cli", "observed_at": "2026-10-11"}
DECISION = "decision-0001"


def z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def at(hours: int = 0, minutes: int = 0) -> datetime:
    return NOW + timedelta(hours=hours, minutes=minutes)


# --------------------------------------------------------------------------- the clock and the fake executors

class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def to(self, moment: datetime) -> None:
        self.now = moment


def limit(kind: str = "plan_limit", *, reset_at: datetime | None = None, confidence: str | None = None,
          window: str = "five_hour") -> dict:
    reset = z(reset_at) if reset_at is not None else None
    return {"kind": kind, "window": window, "reset_at": reset, "reset_source": "app_epoch" if reset else "none",
            "reset_confidence": confidence or ("high" if reset else "none"), "reset_zone": "UTC" if reset else None,
            "evidence": "You've hit your limit"}


@dataclass(frozen=True)
class FakeResult(ExecutorRunResult):
    effective: Mapping[str, Any] | None = None


class FakeApp:
    """An executor application that follows a script and records what it receives."""
    version = "1"

    def __init__(self, executor_id: str, surface: str, models: tuple[str, ...]):
        self.executor_id = executor_id
        self.surface = surface
        self.models = models
        self.adapter_id = f"fake_{executor_id}"
        self.script: list[dict] = []
        self.calls: list[SimpleNamespace] = []
        self.work = None

    def describe(self, config):
        base = descriptor_from_declaration(self.executor_id, {"surface": self.surface, **dict(config or {})})
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
        step = dict(self.script.pop(0)) if self.script else {}
        sid = resume_session or str(uuid.uuid4())
        self.calls.append(SimpleNamespace(kind=kind, request=request, session_id=sid, step=step))
        if self.work is not None:
            self.work(request.workdir)
        command = CommandRecord(argv=("fake",), cwd=str(request.workdir), started_at=NOW.isoformat(),
                                exit_code=1 if step.get("limit") else 0)
        if step.get("limit") is not None:
            return FakeResult(status=RunStatus.QUOTA_EXHAUSTED, session_id=sid, result_envelope=None, usage=None,
                              command=command, stderr_tail="usage limit reached", limit=step["limit"])
        return FakeResult(status=RunStatus.COMPLETED, session_id=sid, result_envelope=dict(RESULT),
                          usage={"output_tokens": 1}, command=command)


# --------------------------------------------------------------------------- the world

# executor id -> (surface, models the app declares); every model has a catalog level (below)
APPS = {
    "claude_cli": ("claude_cli", ("claude-small", "claude-big")),
    "codex_cli": ("codex_cli", ("codex-small", "codex-mid", "codex-big")),
    "copilot_cli": ("copilot_cli", ("copilot-mid",)),
    "paid_api": ("gemini_api", ("api-mid",)),
}
LEVELS = {"claude-small": "T2", "claude-big": "T3", "codex-small": "T1", "codex-mid": "T2", "codex-big": "T3",
          "copilot-mid": "T2", "api-mid": "T2"}


def declaration(app: FakeApp, **extra: Any) -> dict:
    return {"surface": app.surface, "adapter_id": app.adapter_id, "enabled": True, "mode": "automatic",
            "model_support": {"status": "supported", "values": list(app.models), **EVIDENCE},
            "effort_support": {"status": "supported", "values": ["low", "high"], **EVIDENCE},
            "resume": {"status": "supported", "values": ["--resume <id>"], **EVIDENCE}, **extra}


def make_world(tmp_path: Path, *, workdir: Path | None = None, apps: tuple[str, ...] = ("claude_cli", "codex_cli"),
               declared: Mapping[str, Mapping[str, Any]] | None = None, **settings: Any) -> SimpleNamespace:
    config_settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                               gemini_api_key="", openai_api_key="", openai_enabled=False, **settings)
    services = ApplicationServices.create(config_settings)
    clock = Clock()
    folder = workdir or (tmp_path / "work")
    folder.mkdir(parents=True, exist_ok=True)
    fakes = {name: FakeApp(name, *APPS[name]) for name in apps}
    config = {"version": 1, "requirement_map": {}, "data_classifications": {}, "profiles": {},
              "executors": {name: declaration(app, **dict((declared or {}).get(name, {}))) for name, app in fakes.items()},
              "task_workdirs": {name: str(folder) for name in ("T1", "T2", "T3")},
              "operator_boundary": {"operator_id": "ops", "authorized_aliases": ["agent:verified"],
                                    "claim_actor_alias": "agent:verified", "operator_grants": [],
                                    "alias_grants": {"agent:verified": []}}}
    services.system.catalog.executor_routing = config
    services.system.catalog.executor_routing_valid = True
    services.assignment = AssignmentServices(services, registry=ExecutorRegistry(config, list(fakes.values())))
    services.system.catalog.models.update({name: SimpleNamespace(level=level, model=name) for name, level in LEVELS.items()})
    services.executors.clock = clock
    tasks = services.tasks
    tasks.create_group("CNT", "Continuity", actor="user", authorized=True)
    tasks.create_task("CNT", "T1", "Fix the parser", actor="user", objective="Fix the parser.", file_scope=["src"])
    tasks.create_task("CNT", "T2", "Update the docs", actor="user", objective="Update the docs.", file_scope=["docs"])
    tasks.create_task("CNT", "T3", "Read the module", actor="user", objective="Read the module.")
    world = SimpleNamespace(services=services, fakes=fakes, clock=clock, workdir=folder, config=config)
    world.hold = lambda task_id="T1": (services.tasks.get_task(task_id).get("continuity") or {}).get("hold")
    world.continuity = lambda task_id="T1": services.tasks.get_task(task_id).get("continuity") or {}
    return world


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)


def events(services, event_type: str, task_id: str | None = None) -> list[dict]:
    found = services.tasks.events(task_id=task_id) if task_id else services.tasks.events()
    return [event for event in found if event["type"] == event_type]


def refused(call, reason: str | None = None, code: str = "invalid_state") -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call()
    assert caught.value.code == code, caught.value
    if reason is not None:
        assert caught.value.details.get("reason") == reason, caught.value.details
    return caught.value


def hit_limit(world, task_id: str = "T1", *, executor: str = "claude_cli", reset: datetime | None = None,
              kind: str = "plan_limit", confidence: str | None = None, patch: bool = True, model: str = "claude-big",
              effort: str | None = "high") -> dict:
    """Run ``task_id`` in ``executor`` until it ends at a limit that resets at ``reset`` (default in 8 hours)."""
    when = reset if reset is not None else (at(8) if kind != "credits_exhausted" else None)
    world.services.executors.lift_pause(executor, reason="the test needs the executor again")     # an earlier limit paused it
    world.fakes[executor].script.append({"limit": limit(kind, reset_at=when, confidence=confidence)})
    out = world.services.executors.run_task(task_id, executor, model=model, effort=effort)
    assert out["outcome"] == "quota_exhausted", out
    if patch:
        give_patch(world, task_id)
    return out


def give_patch(world, task_id: str = "T1") -> str:
    """Put a saved patch in the hold, as the checkpoint does when the work folder is a git repository."""
    services = world.services
    blob = services.envelopes.put_blob(b"diff --git a/src/x b/src/x\n", actor="executor:claude_cli", media_type="text/x-diff")
    task = services.tasks.get_task(task_id)
    hold = {**task["continuity"]["hold"], "checkpoint": blob["ref"]}
    services.tasks._edit_continuity(task_id, {"hold": hold}, actor="test", expected_revision=task["revision"])
    return blob["ref"]


def card_decides(world, candidate: str | None, decision: str = "delegate", task_id: str = "T1",
                 decision_id: str = DECISION) -> int:
    """What the decision card of CNT-T06 records when a person confirms: the hold and ``continuity.decided``."""
    services = world.services
    store = services.tasks.store
    with store.transaction() as conn:
        task = store.get_task(task_id, conn=conn)
        hold = dict(task["execution"]["continuity"]["hold"])
        if decision == "delegate":
            hold.update(decision="delegate", candidate=candidate, decided_at=z(NOW), decided_by="user",
                        blocked="delegation_requested")
        else:
            hold.update(decision="wait", decided_at=z(NOW), decided_by="user")
            hold.pop("candidate", None)
        store.update_task(conn, task_id, expected_revision=task["revision"],
                          execution=hold_module.merge_continuity(task["execution"], {"hold": hold}))
        store.append_event(conn, "continuity.decided", actor="user:gui", group_id=str(task["group_id"]), task_id=task_id,
                           cause="decision on the usage limit", ref=decision_id,
                           payload={"decision": decision, "by": "user", "executor_id": hold["executor_id"],
                                    **({"candidate": candidate} if candidate else {})})
        return store.get_task(task_id, conn=conn)["revision"]


def calls(world, name: str) -> list[SimpleNamespace]:
    return world.fakes[name].calls


# --------------------------------------------------------------------------- the settings

def test_the_settings_accept_only_what_the_contract_allows():
    base = Settings(_env_file=None)
    assert (base.continuity_mode, base.continuity_wait_threshold_hours, base.continuity_max_handoffs) == ("semi_auto", 5, 1)
    assert base.continuity_candidates == {"claude_cli": "no_ask", "codex_cli": "no_ask", "copilot_cli": "ask"}
    custom = Settings(_env_file=None, continuity_mode="manual", continuity_wait_threshold_hours=24,
                      continuity_max_handoffs=3, continuity_candidates="codex_cli=ask;extra=never")
    assert (custom.continuity_mode, custom.continuity_wait_threshold_hours, custom.continuity_max_handoffs) == ("manual", 24, 3)
    assert custom.continuity_candidates == {"claude_cli": "no_ask", "codex_cli": "ask", "copilot_cli": "ask", "extra": "never"}
    assert Settings(_env_file=None, continuity_candidates='{"copilot_cli": "never"}').continuity_candidates["copilot_cli"] == "never"
    for bad in ({"continuity_mode": "auto"}, {"continuity_mode": "other"}, {"continuity_wait_threshold_hours": 2},
                {"continuity_max_handoffs": 0}, {"continuity_max_handoffs": 4}, {"continuity_candidates": "x=maybe"},
                {"continuity_candidates": "not a mapping"}, {"continuity_candidates": ["a"]}):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **bad)


# --------------------------------------------------------------------------- the pure policy

def the_hold(**fields: Any) -> dict:
    base = {"reason": "plan_limit", "window": "five_hour", "outcome": "wait_until_reset", "reset_at": z(at(8)),
            "resume_after": z(at(8, 2)), "reset_confidence": "high", "executor_id": "claude_cli",
            "actor": "executor:claude_cli", "checkpoint": "blob:sha256:abc", "open_ended": False, "decision": "pending"}
    base.update(fields)
    return base


def candidate(executor_id: str = "codex_cli", **fields: Any) -> handoff.Candidate:
    base = {"surface": executor_id, "policy": "no_ask", "model": "m", "level": "T3", "within_level": True}
    base.update(fields)
    return handoff.Candidate(executor_id, **base)


def decided(policy: handoff.Policy, hold: Mapping[str, Any], candidates=(), *, handoffs: int = 0, patch: bool = True,
            private: bool = False, budget: bool = True, session_open: bool = False,
            now: datetime = NOW) -> handoff.Decision:
    return handoff.decide(policy, hold, handoffs=handoffs, now=now, candidates=list(candidates), has_patch=patch,
                          private_project=private, budget_ok=budget, session_open=session_open)


SEMI = handoff.Policy("semi_auto", 5, 1)
MANUAL = handoff.Policy("manual", 5, 1)
AUTO_OK = [candidate("codex_cli")]


@pytest.mark.parametrize("name, policy, hold, candidates, extra, expected", [
    # rate_limited with retries left: every mode waits (the contract's first row)
    ("retry-manual", MANUAL, the_hold(reason="rate_limited", outcome="wait_retry", reset_at=None, reset_confidence=None),
     AUTO_OK, {}, ("wait", "rate_limited_retry")),
    ("retry-semi", SEMI, the_hold(reason="rate_limited", outcome="wait_retry", reset_at=None, reset_confidence=None),
     AUTO_OK, {}, ("wait", "rate_limited_retry")),
    # manual always asks, even when the reset is close and even with a candidate
    ("manual-close", MANUAL, the_hold(reset_at=z(at(1)), resume_after=z(at(1, 2))), AUTO_OK, {}, ("ask", "manual_mode")),
    ("manual-far", MANUAL, the_hold(), AUTO_OK, {}, ("ask", "manual_mode")),
    ("manual-credits", MANUAL, the_hold(reason="credits_exhausted", outcome="open_ended", reset_at=None, resume_after=None,
                                        open_ended=True), AUTO_OK, {}, ("ask", "manual_mode")),
    ("manual-exhausted", MANUAL, the_hold(), AUTO_OK, {"handoffs": 1}, ("ask", "handoffs_exhausted")),
    # semi-automatic: close reset waits, far reset hands over to a candidate that may be asked nothing
    ("semi-close", SEMI, the_hold(reset_at=z(at(3)), resume_after=z(at(3, 2))), AUTO_OK, {}, ("wait", "reset_within_threshold")),
    ("semi-at-the-threshold", SEMI, the_hold(reset_at=z(at(5)), resume_after=z(at(5, 2))), AUTO_OK, {},
     ("wait", "reset_within_threshold")),
    ("semi-far", SEMI, the_hold(), AUTO_OK, {}, ("delegate", "reset_beyond_threshold")),
    ("semi-far-no-candidate", SEMI, the_hold(), [], {}, ("ask", "no_candidate_without_asking")),
    ("semi-far-asks-first", SEMI, the_hold(), [candidate("copilot_cli", policy="ask")], {},
     ("ask", "no_candidate_without_asking")),
    # no reliable time: ask, even with a candidate
    ("semi-low-confidence", SEMI, the_hold(outcome="wait_fixed_pause", reset_confidence="low"), AUTO_OK, {},
     ("ask", "no_reliable_reset")),
    ("semi-limit-unknown", SEMI, the_hold(reason="limit_unknown"), AUTO_OK, {}, ("ask", "limit_unknown")),
    ("semi-rate-limited-after-retries", SEMI, the_hold(reason="rate_limited", outcome="wait_fixed_pause",
                                                       reset_confidence="low"), AUTO_OK, {}, ("ask", "no_reliable_reset")),
    ("semi-credits", SEMI, the_hold(reason="credits_exhausted", outcome="open_ended", reset_at=None, resume_after=None,
                                    open_ended=True), AUTO_OK, {}, ("ask", "credits_exhausted")),
    # hand-offs used up: wait when the reset is reliable, ask when it is not
    ("semi-exhausted-reliable", SEMI, the_hold(), AUTO_OK, {"handoffs": 1}, ("wait", "handoffs_exhausted")),
    ("semi-exhausted-unreliable", SEMI, the_hold(outcome="wait_fixed_pause", reset_confidence="low"), AUTO_OK,
     {"handoffs": 1}, ("ask", "no_reliable_reset")),
    # things that stop an automatic hand-off and turn it into a question
    ("semi-private", SEMI, the_hold(), AUTO_OK, {"private": True}, ("ask", "no_candidate_without_asking")),
    ("semi-no-patch", SEMI, the_hold(), AUTO_OK, {"patch": False}, ("ask", "no_candidate_without_asking")),
    ("semi-budget", SEMI, the_hold(), AUTO_OK, {"budget": False}, ("ask", "no_candidate_without_asking")),
    ("semi-session-open", SEMI, the_hold(), AUTO_OK, {"session_open": True}, ("ask", "no_candidate_without_asking")),
    # a person who chose to wait is obeyed by the policy too, whatever the mode, the reset or the candidates
    ("person-waits-far", SEMI, the_hold(decision="wait", decided_by="user"), AUTO_OK, {}, ("wait", "decided:wait:user")),
    ("person-waits-manual", MANUAL, the_hold(decision="wait", decided_by="user"), AUTO_OK, {},
     ("wait", "decided:wait:user")),
    ("semi-paid-api", SEMI, the_hold(), [candidate("paid_api", surface="gemini_api")], {},
     ("ask", "no_candidate_without_asking")),
    ("semi-level-above", SEMI, the_hold(), [candidate(within_level=False, level=None, model=None)], {},
     ("ask", "no_candidate_without_asking")),
    ("semi-level-unknown", SEMI, the_hold(), [candidate(within_level=None, level=None, model=None)], {},
     ("ask", "no_candidate_without_asking")),
    ("semi-other-folder", SEMI, the_hold(), [candidate(same_folder=False)], {}, ("ask", "no_candidate_without_asking")),
    ("semi-failed-resumes", SEMI, the_hold(blocked="failed_resumes"), AUTO_OK, {}, ("ask", "blocked:failed_resumes")),
    # other waits are not for this module
    ("condition", SEMI, the_hold(reason="dependency", condition={"type": "time"}), AUTO_OK, {}, ("none", "not_a_limit_hold")),
    ("not-a-limit", SEMI, the_hold(reason="approval"), AUTO_OK, {}, ("none", "not_a_limit_hold")),
    # an unknown mode is as strict as manual
    ("unknown-mode", handoff.Policy("auto", 5, 1), the_hold(), AUTO_OK, {}, ("ask", "manual_mode")),
])
def test_each_row_of_the_policy_table(name, policy, hold, candidates, extra, expected):
    decision = decided(policy, hold, candidates, **extra)
    assert (decision.action, decision.why) == expected, decision
    if decision.action == "delegate":
        assert decision.candidate == "codex_cli" and decision.by == "policy:semi_auto"


def test_a_candidate_that_can_never_take_the_task_is_not_even_proposed():
    refusals = {"disabled": candidate(enabled=False), "paused": candidate(paused=True), "used": candidate(used=True),
                "never": candidate(policy="never")}
    for name, item in refusals.items():
        assert item.refusal() is not None, name
        decision = decided(SEMI, the_hold(), [item])
        assert (decision.action, decision.candidate) == ("ask", None), name
    assert candidate().refusal() is None and candidate().ask_reasons() == []


def test_a_proposal_is_the_candidate_closest_to_the_original_level_and_ties_go_by_id():
    items = [candidate("a_cli", level="T1"), candidate("c_cli", level="T3"), candidate("b_cli", level="T3"),
             candidate("d_cli", level="T4", within_level=False), candidate("e_cli", level=None, within_level=None)]
    assert handoff.best_candidate(items).executor_id == "b_cli"
    assert handoff.best_candidate([candidate(used=True)]) is None
    # asking still proposes the best candidate, and says what stopped the automatic one
    decision = decided(SEMI, the_hold(), [candidate("copilot_cli", policy="ask"), candidate("paid_api", surface="gemini_api")])
    assert decision.action == "ask" and decision.candidate in ("copilot_cli", "paid_api")
    assert {"candidate_asks", "paid_api"} <= set(decision.ask)


def test_a_level_only_restricts_the_one_above():
    profile = handoff.Policy("semi_auto", 3, 2, {"claude_cli": "no_ask", "codex_cli": "ask"})
    same = handoff.resolve_policy(profile, None)
    assert same == profile
    restricted = handoff.resolve_policy(profile, {"mode": "manual", "max_handoffs": 1, "wait_threshold_hours": 24,
                                                  "candidates": {"claude_cli": "never", "codex_cli": "no_ask"}})
    assert (restricted.mode, restricted.max_handoffs, restricted.wait_threshold_hours) == ("manual", 1, 24)
    assert restricted.candidates["claude_cli"] == "never" and restricted.candidates["codex_cli"] == "ask"    # no loosening
    loosened = handoff.resolve_policy(handoff.Policy("manual", 5, 1), {"mode": "semi_auto", "max_handoffs": 3,
                                                                       "wait_threshold_hours": 1, "candidates": "junk"})
    assert (loosened.mode, loosened.max_handoffs, loosened.wait_threshold_hours) == ("manual", 1, 5)
    assert handoff.resolve_policy(profile, {"max_handoffs": True, "mode": 5}).max_handoffs == 2         # malformed: ignored
    assert handoff.resolve_policy(handoff.Policy("semi_auto", 5, 3), None).max_handoffs == 3
    assert handoff.Policy().candidate_policy("unknown_cli") == "ask"
    assert handoff.Policy().candidate_policy("my_codex", "codex_cli") == "no_ask"


def test_a_persons_decision_is_read_from_the_hold():
    chosen = the_hold(decision="delegate", candidate="codex_cli", blocked="delegation_requested")
    assert handoff.requested_delegation(chosen) == "codex_cli"
    for hold in (None, {}, the_hold(), the_hold(decision="wait"), the_hold(decision="delegate", candidate="codex_cli"),
                 the_hold(decision="delegate", blocked="delegation_requested"),
                 the_hold(decision="delegate", candidate=5, blocked="delegation_requested")):
        assert handoff.requested_delegation(hold) is None


# --------------------------------------------------------------------------- the model of the delegate

def descriptor_of(models: tuple[str, ...] | None, efforts: tuple[str, ...] | None = ("low", "high")):
    declared: dict[str, Any] = {"surface": "x_cli"}
    if models is not None:
        declared["model_support"] = {"status": "supported", "values": list(models), **EVIDENCE}
    if efforts is not None:
        declared["effort_support"] = {"status": "supported", "values": list(efforts), **EVIDENCE}
    return descriptor_from_declaration("x_cli", declared)


def test_the_delegate_takes_its_model_at_the_original_level_or_the_closest_cheaper_one():
    level_of = LEVELS.get
    models = ("codex-small", "codex-mid", "codex-big")
    same = select_for_handoff("codex_cli", descriptor_of(models), ceiling_level="T3", level_of=level_of)
    assert (same.model, same.level, same.within_level) == ("codex-big", "T3", True)
    cheaper = select_for_handoff("codex_cli", descriptor_of(models), ceiling_level="T2", level_of=level_of)
    assert (cheaper.model, cheaper.level, cheaper.within_level) == ("codex-mid", "T2", True)
    lowest = select_for_handoff("codex_cli", descriptor_of(models), ceiling_level="T1", level_of=level_of)
    assert (lowest.model, lowest.within_level) == ("codex-small", True)
    # the same model is kept when the delegate declares it
    kept = select_for_handoff("codex_cli", descriptor_of(("a", "codex-mid", "codex-small")), recorded_model="codex-small",
                              ceiling_level="T2", level_of={"a": "T2", "codex-mid": "T2", "codex-small": "T1"}.get)
    assert kept.model == "codex-small"


def test_a_limit_never_climbs_a_level():
    level_of = LEVELS.get
    above = select_for_handoff("x_cli", descriptor_of(("codex-big",)), ceiling_level="T2", level_of=level_of)
    assert (above.model, above.level, above.within_level) == (None, None, False)
    assert any("none_at_or_below" in note for note in above.notes)
    unknown_ceiling = select_for_handoff("x_cli", descriptor_of(("codex-mid", "codex-big")), ceiling_level=None,
                                         level_of=level_of)
    assert (unknown_ceiling.model, unknown_ceiling.within_level) == ("codex-mid", None)
    unclassified = select_for_handoff("x_cli", descriptor_of(("mystery",)), ceiling_level="T4", level_of=level_of)
    assert (unclassified.model, unclassified.within_level) == (None, None)
    undeclared = select_for_handoff("x_cli", descriptor_of(None), ceiling_level="T3", level_of=level_of)
    assert (undeclared.model, undeclared.within_level) == (None, None) and undeclared.notes


def test_a_model_the_adapter_refuses_is_never_chosen_and_the_effort_is_carried_only_when_listed():
    class Refuses:
        def refuses_model(self, name):
            return "not_allowed" if name == "codex-big" else None

    picked = select_for_handoff("copilot_cli", descriptor_of(("codex-mid", "codex-big")), Refuses(),
                                recorded_effort="high", ceiling_level="T3", level_of=LEVELS.get)
    assert (picked.model, picked.effort) == ("codex-mid", "high")
    dropped = select_for_handoff("copilot_cli", descriptor_of(("codex-mid",), ("low",)), recorded_effort="high",
                                 ceiling_level="T3", level_of=LEVELS.get)
    assert dropped.effort is None and any("effort_not_carried" in note for note in dropped.notes)
    assert dropped.to_dict()["model"] == "codex-mid"


# --------------------------------------------------------------------------- the hold carries the policy

def test_the_hold_records_the_mode_and_the_hand_off_limit_of_the_policy(tmp_path):
    custom = make_world(tmp_path / "custom", continuity_mode="manual", continuity_max_handoffs=2)
    hit_limit(custom)
    assert custom.hold()["mode"] == "manual"
    assert custom.continuity()["max_handoffs"] == 2 and custom.continuity()["handoffs"] == 0
    assert custom.continuity()["chain"] == ["claude_cli"]
    (held,) = events(custom.services, "continuity.held", "T1")
    assert held["payload"]["mode"] == "manual" and held["payload"]["max_handoffs"] == 2


def test_the_policy_in_force_can_be_read_and_a_task_can_only_restrict_it(world):
    services = world.services
    assert services.executors.continuity_policy() == {
        "mode": "semi_auto", "wait_threshold_hours": 5, "max_handoffs": 1,
        "candidates": {"claude_cli": "no_ask", "codex_cli": "no_ask", "copilot_cli": "ask"}, "source": "settings",
        "never_during_integration": True, "subscription_surfaces": ["claude_cli", "codex_cli", "copilot_cli"]}
    hit_limit(world)
    services.tasks._edit_continuity("T1", {"mode": "manual", "candidates": {"codex_cli": "never"}}, actor="user")
    task_policy = services.executors.continuity_policy("T1")
    assert (task_policy["source"], task_policy["mode"]) == ("task", "manual")
    assert task_policy["candidates"]["codex_cli"] == "never" and task_policy["candidates"]["claude_cli"] == "no_ask"
    refused(lambda: services.executors.continuity_policy("NOPE"), code="not_found")


# --------------------------------------------------------------------------- a person's decision is carried out

def test_a_persons_delegate_decision_hands_the_task_from_one_executor_to_the_other(world):
    services = world.services
    first = hit_limit(world, reset=at(2))                       # even a close reset: the person decided
    revision = card_decides(world, "codex_cli")
    assert world.hold()["blocked"] == "delegation_requested"
    # The automatic resume leaves it alone while the delegation is pending.
    world.clock.to(at(3))
    assert [item["why"] for item in services.executors.resume_due()["skipped"]] == ["blocked:delegation_requested"]
    assert calls(world, "claude_cli")[-1].kind == "launch" and len(calls(world, "claude_cli")) == 1

    out = services.executors.decide_holds()

    (done,) = out["delegated"]
    assert done["to_executor"] == "codex_cli" and done["by"] == "user" and done["executed"] is True
    assert done["decision_id"] == DECISION and done["handoff_no"] == 1 and done["run_outcome"] == "submitted"
    task = services.tasks.get_task("T1")
    assert task["state"] == "submitted" and task["assignee"] == "executor:codex_cli"
    assert task["continuity"]["handoffs"] == 1 and task["continuity"]["chain"] == ["claude_cli", "codex_cli"]
    assert "hold" not in task["continuity"]
    # The delegate ran once, as a new session; the original executor was never called again.
    assert [call.kind for call in calls(world, "codex_cli")] == ["launch"] and len(calls(world, "claude_cli")) == 1
    assert calls(world, "codex_cli")[0].session_id != first["session_id"]
    # The original session is retired and cannot be resumed.
    (retired,) = events(services, "session.retired", "T1")
    assert retired["ref"] == first["session_id"] and retired["payload"]["reason"] == "handoff"
    assert services.executors.session_guard(first["session_id"]) is False
    # The events say what happened, in order, with the id the card recorded.
    types = [event["type"] for event in services.tasks.events(task_id="T1")]
    assert [name for name in types if name in ("continuity.decided", "session.retired", "task.unclaimed", "task.claimed",
                                               "continuity.handoff", "task.started")][-6:] == [
        "continuity.decided", "session.retired", "task.unclaimed", "task.claimed", "continuity.handoff", "task.started"]
    assert len(events(services, "continuity.decided", "T1")) == 1                   # the card's own: no duplicate
    (handed,) = events(services, "continuity.handoff", "T1")
    assert handed["ref"] == "codex_cli" and handed["actor"] == "user:gui"
    assert handed["payload"]["from"] == "claude_cli" and handed["payload"]["decision_id"] == DECISION
    assert handed["payload"]["handoff_no"] == 1 and handed["payload"]["by"] == "user"
    assert handed["payload"]["session_retired"] == first["session_id"] and handed["payload"]["same_folder"] is True
    assert revision < task["revision"]


def test_the_delegate_starts_from_the_checkpoint_and_runs_with_a_model_at_the_original_level(world):
    services = world.services
    hit_limit(world, model="claude-big", effort="high")                # T3
    patch_ref = world.hold()["checkpoint"]
    card_decides(world, "codex_cli")
    services.executors.decide_holds()

    (launch,) = calls(world, "codex_cli")
    assert launch.request.model == "codex-big" and launch.request.effort == "high"       # same level, effort carried
    routing = [event for event in events(services, "executor.routing_selected", "T1") if event["ref"] == "codex_cli"]
    (selected,) = routing
    assert selected["payload"]["model_source"] == "handoff" and selected["payload"]["effort_source"] == "handoff"
    (handed,) = events(services, "continuity.handoff", "T1")
    assert (handed["payload"]["model"], handed["payload"]["level"], handed["payload"]["within_level"]) == (
        "codex-big", "T3", True)
    assert handed["payload"]["sends"] == "task envelope and patch" and handed["payload"]["patch"] == patch_ref


def test_a_delegate_with_no_model_at_the_original_level_runs_with_the_apps_default(tmp_path):
    world = make_world(tmp_path, declared={"codex_cli": {}})
    world.services.system.catalog.models.update({"codex-small": SimpleNamespace(level="T4", model="codex-small"),
                                                 "codex-mid": SimpleNamespace(level="T4", model="codex-mid"),
                                                 "codex-big": SimpleNamespace(level="T4", model="codex-big")})
    hit_limit(world, model="claude-small")                              # T2: every codex model is above it
    out = world.services.executors.handoff_task("T1", "codex_cli", decision_id=DECISION)
    assert out["model"] is None and out["within_level"] is False and out["run"]["outcome"] == "submitted"
    assert calls(world, "codex_cli")[0].request.model is None


def test_repeating_a_decision_returns_the_first_result_and_does_nothing_more(world):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    first = services.executors.handoff_task("T1", decision_id=DECISION)
    seen = services.tasks.events()
    again = services.executors.handoff_task("T1", "codex_cli", decision_id=DECISION)
    assert first["repeated"] is False and again["repeated"] is True and again["run"] is None
    assert (again["from_executor"], again["to_executor"], again["handoff_no"]) == ("claude_cli", "codex_cli", 1)
    assert services.tasks.events() == seen and len(calls(world, "codex_cli")) == 1
    assert world.continuity()["handoffs"] == 1


def test_a_decision_id_belongs_to_one_task(world):
    services = world.services
    hit_limit(world)
    hit_limit(world, "T2")
    card_decides(world, "codex_cli", task_id="T2", decision_id="decision-for-t2")
    refused(lambda: services.executors.handoff_task("T1", "codex_cli", decision_id="decision-for-t2"),
            "decision_id_reused")
    assert world.hold()["executor_id"] == "claude_cli" and services.tasks.get_task("T1")["state"] == "paused"


def test_a_stale_revision_is_refused_and_nothing_changes(world):
    services = world.services
    hit_limit(world)
    revision = services.tasks.get_task("T1")["revision"]
    before = services.tasks.events()
    error = refused(lambda: services.executors.handoff_task("T1", "codex_cli", expected_revision=revision - 1),
                    "stale_revision")
    assert error.details["revision"] == revision
    assert services.tasks.events() == before and services.tasks.get_task("T1")["assignee"] == "executor:claude_cli"
    ok = services.executors.handoff_task("T1", "codex_cli", expected_revision=revision, run=False)
    assert ok["to_executor"] == "codex_cli"


def test_the_original_session_is_never_resumed_after_a_hand_off(world):
    services = world.services
    first = hit_limit(world)
    out = services.executors.handoff_task("T1", "codex_cli", run=False)
    assert out["session_retired"] == first["session_id"] and out["run"] is None
    task = services.tasks.get_task("T1")
    assert (task["state"], task["assignee"]) == ("claimed", "executor:codex_cli")
    # The old session is retired, the old agent can no longer move the task, and it has not been called again.
    refused(lambda: services.executors.resume_task("T1"), "session_retired")
    refused(lambda: services.tasks.transition("T1", "start", actor="executor:claude_cli"))
    assert len(calls(world, "claude_cli")) == 1
    # The delegate runs it whenever it is asked to (run_task accepts a task its own agent claimed).
    ran = services.executors.run_task("T1", "codex_cli")
    assert ran["outcome"] == "submitted" and ran["attempt"] == 2


def test_nothing_is_half_done_when_the_claim_is_refused(world, monkeypatch):
    services = world.services
    first = hit_limit(world)
    before_events = services.tasks.events()
    before = services.tasks.get_task("T1")
    real = services.tasks._apply

    def refuse_the_claim(conn, task_id, action, **kwargs):
        if action == "claim":
            raise ServiceError("invalid_state", "Files are locked by another task", status=409)
        return real(conn, task_id, action, **kwargs)

    monkeypatch.setattr(services.tasks, "_apply", refuse_the_claim)
    refused(lambda: services.executors.handoff_task("T1", "codex_cli", decision_id="decision-9999"))
    monkeypatch.undo()

    after = services.tasks.get_task("T1")
    assert (after["state"], after["assignee"], after["revision"]) == ("paused", "executor:claude_cli", before["revision"])
    assert after["continuity"] == before["continuity"]
    assert services.tasks.events() == before_events                     # no decision, no retired session, no hand-off
    assert services.executors.session_guard(first["session_id"]) is True


def test_only_a_person_hands_a_task_over_by_hand(world):
    services = world.services
    hit_limit(world)
    for actor in ("executor:codex_cli", "system:watcher", "policy:semi_auto"):
        refused(lambda: services.executors.handoff_task("T1", "codex_cli", actor=actor), "person_required")
    refused(lambda: services.executors.handoff_task("T1"), "no_delegate_decision")
    refused(lambda: services.executors.handoff_task("T1", "bad id!"), code="invalid_request")
    refused(lambda: services.executors.handoff_task("T1", "codex_cli", decision_id="x"), code="invalid_request")
    assert services.tasks.get_task("T1")["state"] == "paused"


# --------------------------------------------------------------------------- the hard limits

def test_only_a_task_paused_by_a_limit_is_handed_over(world):
    services = world.services
    services.tasks.transition("T3", "claim", actor="executor:claude_cli")
    refused(lambda: services.executors.handoff_task("T3", "codex_cli"), "not_held")            # claimed, not held
    services.executors.run_task("T2", "claude_cli")                                           # submitted: the gate decides
    refused(lambda: services.executors.handoff_task("T2", "codex_cli"), "not_held")
    services.tasks.transition("T3", "start", actor="executor:claude_cli")
    services.tasks.transition("T3", "pause", actor="executor:claude_cli")                      # paused, but not by a limit
    refused(lambda: services.executors.handoff_task("T3", "codex_cli"), "not_held")
    assert services.executors.decide_holds()["held"] == 0


def test_the_hand_off_goes_to_a_delegate_that_is_enabled_unpaused_and_new(tmp_path):
    world = make_world(tmp_path, apps=("claude_cli", "codex_cli", "copilot_cli"), declared={"copilot_cli": {"enabled": False}})
    services = world.services
    hit_limit(world)
    refused(lambda: services.executors.handoff_task("T1", "copilot_cli"), "executor_disabled")
    refused(lambda: services.executors.handoff_task("T1", "claude_cli"), "executor_already_used")      # the first executor
    refused(lambda: services.executors.handoff_task("T1", "nope_cli"), code="not_found")
    services.executors._append("executor.paused", actor="executor:codex_cli", ref="codex_cli", payload={
        "executor_id": "codex_cli", "until": z(at(9)), "open_ended": False, "limit_kind": "plan_limit"})
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "executor_paused")
    task = services.tasks.get_task("T1")
    assert (task["state"], task["assignee"]) == ("paused", "executor:claude_cli") and world.hold() is not None
    assert events(services, "continuity.handoff") == [] and len(calls(world, "codex_cli")) == 0


def test_the_hand_offs_of_a_task_are_limited_and_the_original_executor_is_not_taken_back(tmp_path):
    world = make_world(tmp_path, apps=("claude_cli", "codex_cli", "copilot_cli"), continuity_max_handoffs=2)
    services = world.services
    hit_limit(world)
    world.fakes["codex_cli"].script.append({"limit": limit(reset_at=at(9))})        # the delegate runs out too
    out = services.executors.handoff_task("T1", "codex_cli")
    assert out["run"]["outcome"] == "quota_exhausted" and out["handoff_no"] == 1
    task = services.tasks.get_task("T1")
    assert (task["state"], task["assignee"]) == ("paused", "executor:codex_cli")
    assert task["continuity"]["handoffs"] == 1 and task["continuity"]["chain"] == ["claude_cli", "codex_cli"]
    assert task["continuity"]["hold"]["executor_id"] == "codex_cli" and task["continuity"]["max_handoffs"] == 2
    give_patch(world)
    # No ping-pong: neither executor that already worked on it is chosen again, whoever decides.
    refused(lambda: services.executors.handoff_task("T1", "claude_cli"), "executor_already_used")
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "executor_already_used")
    third = services.executors.handoff_task("T1", "copilot_cli", run=False)
    assert third["handoff_no"] == 2 and services.tasks.get_task("T1")["continuity"]["chain"] == [
        "claude_cli", "codex_cli", "copilot_cli"]
    # Two were allowed: a third is not.
    services.tasks.transition("T1", "start", actor="executor:copilot_cli")
    services.tasks.transition("T1", "pause", actor="executor:copilot_cli", continuity={"hold": {
        **the_hold(executor_id="copilot_cli", actor="executor:copilot_cli", since=z(NOW), pause_seq=None, session_id=None,
                   attempt=3)}})
    refused(lambda: services.executors.handoff_task("T1", "claude_cli"), "handoffs_exhausted")


def test_the_delegate_that_also_runs_out_makes_the_next_decision_a_new_one(world):
    services = world.services
    hit_limit(world)
    world.fakes["codex_cli"].script.append({"limit": limit(reset_at=at(9))})
    services.executors.handoff_task("T1", "codex_cli")
    # One hand-off was allowed and is used: the policy waits for the reset it can trust instead of handing over again.
    out = services.executors.decide_holds()
    assert [item["why"] for item in out["waiting"]] == ["handoffs_exhausted"] and out["delegated"] == []
    assert len(events(services, "continuity.handoff", "T1")) == 1


def test_nothing_is_handed_over_during_an_integration(world):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    services.executors.integration_active = lambda: True
    out = services.executors.decide_holds()
    assert out["integration"] is True and out["skipped"] == [{"task_id": "T1", "why": "integration_in_progress"}]
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "integration_in_progress")
    assert services.tasks.get_task("T1")["assignee"] == "executor:claude_cli" and len(calls(world, "codex_cli")) == 0

    def broken():
        raise RuntimeError("cannot tell")

    services.executors.integration_active = broken                      # a hook that fails is not a green light
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "integration_in_progress")
    services.executors.integration_active = lambda: False
    assert services.executors.decide_holds()["delegated"][0]["executed"] is True


def test_a_budget_at_its_limit_stops_the_automatic_hand_off_and_a_cut_budget_stops_every_one(world, monkeypatch):
    services = world.services
    hit_limit(world)
    monkeypatch.setattr(services.usage, "budgets", lambda **kwargs: {"status": [
        {"budget_id": "month", "state": "limit_reached", "mode": "warn"}]})
    asked = services.executors.decide_holds()
    assert asked["delegated"] == [] and asked["asked"][0]["ask"] == ["budget"]
    assert asked["asked"][0]["candidate"] == "codex_cli"                  # still proposed: a person can say yes
    warned = services.executors.handoff_task("T1", "codex_cli", run=False)          # a person may, with a warn budget
    assert warned["to_executor"] == "codex_cli"
    hit_limit(world, "T2")
    monkeypatch.setattr(services.usage, "budgets", lambda **kwargs: {"status": [
        {"budget_id": "month", "state": "limit_reached", "mode": "cut"}]})
    error = refused(lambda: services.executors.handoff_task("T2", "codex_cli"), "budget_exhausted")
    assert error.details["budget"] == "cut_reached"

    def unreadable(**kwargs):
        raise RuntimeError("no telemetry")

    monkeypatch.setattr(services.usage, "budgets", unreadable)               # cannot be read: not a green light
    hit_limit(world, "T3", model="claude-big")
    assert services.executors.decide_holds(task_id="T3")["delegated"] == []


# --------------------------------------------------------------------------- the semi-automatic policy

def test_semi_auto_hands_a_far_reset_over_to_a_candidate_without_asking(world):
    services = world.services
    first = hit_limit(world)                                              # resets in 8 hours: beyond the 5 hour threshold
    plan = services.executors.decide_holds(dry_run=True)
    (planned,) = plan["delegated"]
    assert planned["executed"] is False and planned["to_executor"] == "codex_cli" and planned["by"] == "policy:semi_auto"
    assert services.tasks.get_task("T1")["assignee"] == "executor:claude_cli" and len(calls(world, "codex_cli")) == 0

    out = services.executors.decide_holds()

    (done,) = out["delegated"]
    assert done["executed"] is True and done["by"] == "policy:semi_auto" and done["run_outcome"] == "submitted"
    task = services.tasks.get_task("T1")
    assert (task["state"], task["assignee"]) == ("submitted", "executor:codex_cli")
    (decision,) = events(services, "continuity.decided", "T1")
    assert decision["payload"]["by"] == "policy:semi_auto" and decision["payload"]["candidate"] == "codex_cli"
    assert decision["payload"]["sends"] == "task envelope and patch" and decision["actor"] == "policy:semi_auto"
    assert decision["ref"] == done["decision_id"]
    (retired,) = events(services, "session.retired", "T1")
    assert retired["ref"] == first["session_id"]
    assert calls(world, "codex_cli")[0].request.model == "codex-big"
    assert services.executors.decide_holds()["held"] == 0


def test_semi_auto_waits_for_a_close_reset_and_tells_it_once(world):
    services = world.services
    hit_limit(world, reset=at(3))
    for _ in range(2):
        out = services.executors.decide_holds()
        assert out["delegated"] == [] and out["asked"] == []
    (note,) = events(services, "continuity.decided", "T1")
    assert note["payload"]["decision"] == "wait" and note["payload"]["by"] == "policy:semi_auto"
    assert note["payload"]["why"] == "reset_within_threshold"
    assert world.hold()["decision"] == "wait" and world.hold()["decided_by"] == "policy:semi_auto"
    assert "blocked" not in world.hold()
    # The wait is the normal one: the task resumes by itself at the reset.
    assert services.executors.resume_due()["resumed"] == []
    world.clock.to(at(3, 5))
    (resumed,) = services.executors.resume_due()["resumed"]
    assert resumed["task_id"] == "T1" and resumed["outcome"] == "submitted" and resumed["mode"] == "resume"
    assert len(calls(world, "codex_cli")) == 0 and len(events(services, "continuity.handoff")) == 0


def test_semi_auto_dry_run_writes_nothing_even_for_a_wait(world):
    services = world.services
    hit_limit(world, reset=at(3))
    before = services.tasks.events()
    out = services.executors.decide_holds(dry_run=True)
    assert [item["why"] for item in out["waiting"]] == ["reset_within_threshold"]
    assert services.tasks.events() == before and "decision" in world.hold() and world.hold()["decision"] == "pending"


def test_semi_auto_asks_when_it_cannot_hand_over_without_asking(tmp_path):
    world = make_world(tmp_path, apps=("claude_cli", "copilot_cli", "paid_api"))
    services = world.services
    hit_limit(world)
    before = services.tasks.events()
    out = services.executors.decide_holds()
    (asked,) = out["asked"]
    assert asked["why"] == "no_candidate_without_asking" and asked["candidate"] in ("copilot_cli", "paid_api")
    assert {"candidate_asks", "paid_api"} <= set(asked["ask"])
    assert out["delegated"] == [] and services.tasks.events() == before            # asking writes nothing
    assert world.hold()["decision"] == "pending" and "blocked" not in world.hold()


def test_semi_auto_does_not_hand_over_a_task_of_a_private_project(world):
    services = world.services
    store = services.projects.store
    with services.tasks.store.transaction() as conn:
        store.insert_project(conn, "p1", "Private", visibility="private", created_by="user")
        conn.execute("UPDATE task_groups SET project_id='p1' WHERE id='CNT'")
    hit_limit(world)
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and out["asked"][0]["ask"] == ["private_project"]
    with services.tasks.store.transaction() as conn:
        conn.execute("UPDATE projects SET visibility='normal' WHERE id='p1'")
    assert services.executors.decide_holds()["delegated"][0]["executed"] is True


def test_semi_auto_needs_a_saved_patch_to_hand_over_alone(world):
    services = world.services
    hit_limit(world, patch=False)
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and out["asked"][0]["ask"] == ["no_patch"]
    # A person may still decide to send the envelope alone; the card said so.
    card_decides(world, "codex_cli")
    assert services.executors.decide_holds()["delegated"][0]["executed"] is True
    (handed,) = events(services, "continuity.handoff", "T1")
    assert handed["payload"]["sends"] == "task envelope (no patch)"


def test_semi_auto_does_not_pick_a_candidate_that_works_in_another_folder(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    world = make_world(tmp_path, declared={"codex_cli": {"workdir": str(other)}})
    world.config["task_workdirs"] = {}                                  # no per-task folder: the executor's own applies
    world.services.assignment = AssignmentServices(
        world.services, registry=ExecutorRegistry(world.config, list(world.fakes.values())))
    hit_limit(world)
    out = world.services.executors.decide_holds()
    assert out["delegated"] == [] and out["asked"][0]["ask"] == ["workdir_differs"]


def test_semi_auto_never_hands_over_to_a_level_above_or_to_a_paid_api(tmp_path):
    world = make_world(tmp_path, apps=("claude_cli", "codex_cli", "paid_api"))
    services = world.services
    services.system.catalog.models.update({name: SimpleNamespace(level="T4", model=name)
                                           for name in ("codex-small", "codex-mid", "codex-big")})
    hit_limit(world, model="claude-small")                              # T2: codex only has T4 models
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and {"level_above", "paid_api"} <= set(out["asked"][0]["ask"])


def test_manual_mode_always_asks_but_still_carries_out_a_persons_decision(tmp_path):
    world = make_world(tmp_path, continuity_mode="manual")
    services = world.services
    hit_limit(world, reset=at(1))
    out = services.executors.decide_holds()
    assert [item["why"] for item in out["asked"]] == ["manual_mode"] and out["asked"][0]["candidate"] == "codex_cli"
    assert out["waiting"] == [] and out["delegated"] == [] and world.hold()["mode"] == "manual"
    card_decides(world, "codex_cli")
    assert services.executors.decide_holds()["delegated"][0]["executed"] is True


def test_a_persons_decision_to_wait_is_left_to_the_normal_resume(world):
    services = world.services
    hit_limit(world)
    card_decides(world, None, decision="wait")
    out = services.executors.decide_holds()
    assert out["waiting"][0]["why"] == "decided:wait:user" and out["delegated"] == [] and out["asked"] == []
    assert world.hold()["decision"] == "wait" and len(events(services, "continuity.decided", "T1")) == 1


def test_a_persons_choice_overrides_the_policys_wait(world):
    services = world.services
    hit_limit(world, reset=at(2))
    services.executors.decide_holds()                                   # the policy waits and says so
    assert world.hold()["decision"] == "wait"
    card_decides(world, "codex_cli", decision_id="decision-0002")
    (done,) = services.executors.decide_holds()["delegated"]
    assert done["by"] == "user" and done["decision_id"] == "decision-0002" and done["executed"] is True


def test_decisions_are_made_one_task_at_a_time_up_to_the_limit(world):
    services = world.services
    hit_limit(world)
    hit_limit(world, "T2")
    out = services.executors.decide_holds(limit=1, run=False)
    assert len(out["delegated"]) == 1 and [item["why"] for item in out["waiting"]] == ["limit"]
    only = services.executors.decide_holds(task_id="T2", run=False)
    assert [item["task_id"] for item in only["delegated"]] == ["T2"]
    refused(lambda: services.executors.decide_holds(limit=0), code="invalid_request")
    refused(lambda: services.executors.decide_holds(task_id="bad id!"), code="invalid_request")


def test_a_hand_off_that_is_refused_while_deciding_is_reported_and_the_task_waits_for_a_person(world):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    services.executors._append("executor.paused", actor="executor:codex_cli", ref="codex_cli", payload={
        "executor_id": "codex_cli", "until": z(at(9)), "open_ended": False, "limit_kind": "plan_limit"})
    out = services.executors.decide_holds()
    (skipped,) = out["skipped"]
    assert skipped["why"] == "executor_paused" and skipped["task_id"] == "T1" and out["delegated"] == []
    assert world.hold()["blocked"] == "delegation_requested"             # it stays put; the card shows it
    world.clock.to(at(10))                                               # the delegate is free again: the next tick does it
    assert services.executors.decide_holds()["delegated"][0]["executed"] is True


# --------------------------------------------------------------------------- the delegate, the routes and the real patch

def test_a_task_handed_over_can_be_handed_over_again_only_through_its_new_hold(world):
    services = world.services
    hit_limit(world)
    services.executors.handoff_task("T1", "codex_cli", run=False)
    refused(lambda: services.executors.handoff_task("T1", "codex_cli", decision_id="decision-0042"), "not_held")


HANDOFF = "/api/v1/executors/handoff"


def route_reason(response) -> str | None:
    return response.json()["detail"]["details"].get("reason")


def snapshot(world, task_id: str = "T1") -> tuple:
    """Everything a refused hand-off must leave as it was: the task, its events and what the delegate was asked."""
    services = world.services
    return (services.tasks.get_task(task_id), services.tasks.events(), [len(calls(world, name)) for name in sorted(world.fakes)])


def test_the_http_route_carries_out_only_a_decision_a_person_recorded(world):
    services = world.services
    hit_limit(world)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        before = snapshot(world)
        bad = client.post(HANDOFF, json={"task_id": "T1", "executor_id": "codex_cli", "actor": "executor:codex_cli"})
        assert bad.status_code == 409 and route_reason(bad) == "person_required"
        # No token and no recorded decision: a claimed "user" (the default) is not a person who decided.
        for body in ({"task_id": "T1", "executor_id": "codex_cli"},
                     {"task_id": "T1", "executor_id": "codex_cli", "actor": "user", "decision_id": "decision-http-0"}):
            claimed = client.post(HANDOFF, json=body)
            assert claimed.status_code == 409 and route_reason(claimed) == "person_required", claimed.json()
        none = client.post(HANDOFF, json={"task_id": "T1"})
        assert none.status_code == 409 and route_reason(none) == "no_delegate_decision"
        assert snapshot(world) == before                               # nothing moved, nobody was launched
        # The service refuses the same way when its caller says it cannot be trusted; a trusted caller (the token route
        # of the GUI) can still choose.
        refused(lambda: services.executors.handoff_task("T1", "codex_cli", recorded_only=True), "person_required")
        assert snapshot(world) == before
        # The policy, not a caller, decides what happens without a person: it is untouched by this.
        plan = client.post("/api/v1/executors/decide-holds", json={"dry_run": True})
        assert plan.status_code == 200 and plan.json()["delegated"][0]["to_executor"] == "codex_cli"


def test_the_http_route_refuses_any_other_executor_than_the_recorded_one(tmp_path):
    world = make_world(tmp_path, apps=("claude_cli", "codex_cli", "copilot_cli"))
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        before = snapshot(world)
        other = client.post(HANDOFF, json={"task_id": "T1", "executor_id": "copilot_cli"})
        assert other.status_code == 409 and route_reason(other) == "person_required"
        # The decision id is the one the card recorded too: an invented one does not make a new decision.
        invented = client.post(HANDOFF, json={"task_id": "T1", "executor_id": "codex_cli", "decision_id": "decision-http-9"})
        assert invented.status_code == 409 and route_reason(invented) == "person_required"
        assert snapshot(world) == before
        assert world.hold()["blocked"] == "delegation_requested" and world.hold()["candidate"] == "codex_cli"
        # The person changes their mind on the card (waits): there is nothing left to carry out.
        card_decides(world, None, decision="wait", decision_id="decision-0002")
        undone = client.post(HANDOFF, json={"task_id": "T1", "executor_id": "codex_cli"})
        assert undone.status_code == 409 and route_reason(undone) == "person_required"
    assert services.tasks.get_task("T1")["assignee"] == "executor:claude_cli" and len(calls(world, "codex_cli")) == 0


def test_the_http_route_carries_out_the_recorded_decision_and_a_repeat_returns_the_first_result(world):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli", decision_id="decision-http-1")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        ok = client.post(HANDOFF, json={"task_id": "T1", "actor": "someone-else"})        # the executor is the recorded one
        assert ok.status_code == 200, ok.json()
        body = ok.json()
        assert (body["to_executor"], body["handoff_no"], body["repeated"]) == ("codex_cli", 1, False)
        assert body["decision_id"] == "decision-http-1" and body["by"] == "user"
        assert body["run"]["outcome"] == "submitted" and body["task"]["assignee"] == "executor:codex_cli"
        # The record says who decided (the card), not who the caller claimed to be.
        (handed,) = events(services, "continuity.handoff", "T1")
        assert handed["actor"] == "user:gui" and handed["payload"]["decision_id"] == "decision-http-1"
        assert len(events(services, "continuity.decided", "T1")) == 1
        again = client.post(HANDOFF, json={"task_id": "T1", "executor_id": "codex_cli", "decision_id": "decision-http-1"})
        assert again.status_code == 200 and again.json()["repeated"] is True
    assert len(calls(world, "codex_cli")) == 1


def test_the_http_route_carries_out_the_decision_of_a_person_for_an_executor_marked_never(tmp_path):
    world = make_world(tmp_path, continuity_candidates="codex_cli=never")
    services = world.services
    hit_limit(world)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        refusal = client.post(HANDOFF, json={"task_id": "T1", "executor_id": "codex_cli"})       # nobody chose it
        assert refusal.status_code == 409 and route_reason(refusal) == "person_required"
        assert services.tasks.get_task("T1")["assignee"] == "executor:claude_cli"
        card_decides(world, "codex_cli")                                                          # a person chose it
        ok = client.post(HANDOFF, json={"task_id": "T1", "executor_id": "codex_cli"})
        assert ok.status_code == 200 and ok.json()["to_executor"] == "codex_cli"


def test_the_default_integration_hook_is_the_marker_of_the_watcher(world):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    marker = services.settings.integration_marker
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")                       # the integration (or a watcher resuming) holds the marker
    out = services.executors.decide_holds()
    assert out["integration"] is True and out["skipped"] == [{"task_id": "T1", "why": "integration_in_progress"}]
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "integration_in_progress")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        blocked = client.post(HANDOFF, json={"task_id": "T1"})
        assert blocked.status_code == 409 and route_reason(blocked) == "integration_in_progress"
        assert client.post("/api/v1/executors/decide-holds", json={"dry_run": True}).json()["integration"] is True
        assert services.tasks.get_task("T1")["assignee"] == "executor:claude_cli" and len(calls(world, "codex_cli")) == 0
        marker.unlink()
        done = client.post(HANDOFF, json={"task_id": "T1"})
        assert done.status_code == 200 and done.json()["to_executor"] == "codex_cli"


def test_without_a_hook_nobody_can_tell_and_nothing_is_handed_over(world):
    services = world.services
    hit_limit(world)                                                # a far reset: the policy would delegate
    services.executors.integration_active = None
    out = services.executors.decide_holds()
    assert out["integration"] is True and out["delegated"] == [] and out["skipped"][0]["why"] == "integration_in_progress"
    card_decides(world, "codex_cli")
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "integration_in_progress")
    assert services.tasks.get_task("T1")["assignee"] == "executor:claude_cli" and len(calls(world, "codex_cli")) == 0
    services.executors.integration_active = lambda: False          # a host that can tell, and says it is calm
    assert services.executors.decide_holds()["delegated"][0]["executed"] is True


# --------------------------------------------------------------------------- nothing changes between the check and the transaction

def after_the_first_check(monkeypatch, services, change) -> list[int]:
    """Run ``change`` once, right after the checks outside the transaction passed and before it opens.

    The returned list gets one item per time the checks ran: two means they were made again inside the transaction.
    """
    real = services.executors._handoff_delegate
    ran: list[int] = []

    def checked(*args, **kwargs):
        first = not ran
        ran.append(1)
        found = real(*args, **kwargs)
        if first:
            change()
        return found

    monkeypatch.setattr(services.executors, "_handoff_delegate", checked)
    return ran


def assert_not_handed_over(world) -> None:
    services = world.services
    task = services.tasks.get_task("T1")
    assert (task["state"], task["assignee"]) == ("paused", "executor:claude_cli")
    assert (task.get("continuity") or {}).get("handoffs", 0) == 0
    assert events(services, "continuity.handoff", "T1") == [] and len(calls(world, "codex_cli")) == 0


def test_a_delegate_paused_after_the_check_is_not_handed_the_task(world, monkeypatch):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    ran = after_the_first_check(monkeypatch, services, lambda: services.executors._append(
        "executor.paused", actor="executor:codex_cli", ref="codex_cli", payload={
            "executor_id": "codex_cli", "until": z(at(9)), "open_ended": False, "limit_kind": "plan_limit"}))
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "executor_paused")
    assert len(ran) == 2
    assert_not_handed_over(world)
    assert world.hold()["blocked"] == "delegation_requested"                # still waiting for the delegate to be free


def test_an_integration_that_starts_after_the_check_stops_the_hand_off(world, monkeypatch):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    ran = after_the_first_check(monkeypatch, services, lambda: setattr(services.executors, "integration_active", lambda: True))
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "integration_in_progress")
    assert len(ran) == 1                                                    # it stopped before the checks were made again
    assert_not_handed_over(world)


def test_a_budget_that_fills_after_the_check_stops_the_hand_off(world, monkeypatch):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    full = {"status": [{"budget_id": "month", "state": "limit_reached", "mode": "cut"}]}
    ran = after_the_first_check(monkeypatch, services, lambda: monkeypatch.setattr(services.usage, "budgets", lambda **kw: full))
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "budget_exhausted")
    assert len(ran) == 2
    assert_not_handed_over(world)


def test_the_person_changing_their_mind_after_the_check_stops_the_hand_off(world, monkeypatch):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    ran = after_the_first_check(monkeypatch, services, lambda: card_decides(world, None, decision="wait",
                                                                           decision_id="decision-0002"))
    refused(lambda: services.executors.handoff_task("T1", "codex_cli", recorded_only=True), "hold_changed")
    assert len(ran) == 1
    assert_not_handed_over(world)
    assert world.hold()["decision"] == "wait"


def test_a_project_turned_private_after_the_policy_decided_is_not_handed_over(world, monkeypatch):
    services = world.services
    store = services.projects.store
    hit_limit(world)

    def make_private():
        with services.tasks.store.transaction() as conn:
            store.insert_project(conn, "p1", "Private", visibility="private", created_by="user")
            conn.execute("UPDATE task_groups SET project_id='p1' WHERE id='CNT'")

    ran = after_the_first_check(monkeypatch, services, make_private)
    out = services.executors.decide_holds()                                 # the policy said "delegate" before the change
    assert out["delegated"] == [] and [item["why"] for item in out["skipped"]] == ["policy_changed"]
    assert len(ran) == 2
    assert_not_handed_over(world)
    monkeypatch.undo()
    with services.tasks.store.transaction() as conn:
        conn.execute("UPDATE projects SET visibility='normal' WHERE id='p1'")
    assert services.executors.decide_holds()["delegated"][0]["executed"] is True


def test_a_candidate_marked_never_after_the_policy_decided_is_not_handed_the_task(world, monkeypatch):
    services = world.services
    hit_limit(world)
    ran = after_the_first_check(monkeypatch, services, lambda: services.settings.continuity_candidates.update(codex_cli="never"))
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and [item["why"] for item in out["skipped"]] == ["policy_changed"]
    assert len(ran) == 2
    assert_not_handed_over(world)


def test_a_delegate_that_cannot_be_launched_still_owns_the_task_and_the_error_is_returned(world, monkeypatch):
    services = world.services
    hit_limit(world)

    def refuse(*args, **kwargs):
        raise ServiceError("invalid_state", "The executor cannot start", status=409, details={"reason": "executor_paused"})

    monkeypatch.setattr(services.executors, "_run_task", refuse)
    out = services.executors.handoff_task("T1", "codex_cli")
    assert out["run"] is None and out["run_error"]["reason"] == "executor_paused"
    task = services.tasks.get_task("T1")
    assert (task["state"], task["assignee"]) == ("claimed", "executor:codex_cli")
    assert task["continuity"]["handoffs"] == 1 and "hold" not in task["continuity"]


def test_an_executor_marked_never_is_not_chosen_by_the_policy_but_a_person_may_choose_it(tmp_path):
    world = make_world(tmp_path, continuity_candidates="codex_cli=never")
    services = world.services
    hit_limit(world)
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and out["asked"][0]["candidate"] is None
    card_decides(world, "codex_cli")
    assert services.executors.decide_holds()["delegated"][0]["executed"] is True


def _git(folder: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", *args], cwd=folder,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout


@needs_git
def test_with_a_real_patch_the_semi_auto_hand_off_carries_the_work_to_the_delegate(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "app.py").write_bytes(b"VALUE = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    world = make_world(tmp_path, workdir=repo)
    world.fakes["claude_cli"].work = lambda folder: (folder / "app.py").write_bytes(b"VALUE = 2\n")
    first = hit_limit(world, patch=False)                               # the checkpoint saves the patch by itself
    assert world.hold()["checkpoint"] == first["checkpoint"]["patch"]["ref"]

    (done,) = world.services.executors.decide_holds()["delegated"]

    assert done["executed"] is True and done["by"] == "policy:semi_auto"
    (launch,) = calls(world, "codex_cli")
    assert first["checkpoint"]["patch"]["ref"] in launch.request.task_envelope["context_refs"]
    assert any(text.startswith("[checkpoint]") for text in launch.request.task_envelope["constraints"])
    (handed,) = events(world.services, "continuity.handoff", "T1")
    assert handed["payload"]["sends"].startswith("task envelope and patch (1 file")
    for event in world.services.tasks.events():
        assert len(json.dumps(event["payload"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 2048


# --------------------------------------------------------------------------- the second security review (CNT-T05)

RUN = "/api/v1/executors/run"


def two_held_tasks(world) -> None:
    hit_limit(world)
    hit_limit(world, "T2")


def make_private_project(world) -> int:
    """A private project (the default) that holds the stage CNT; returns its revision."""
    services = world.services
    doc = {"schema": "maf.project-template/1", "id": "p1", "title": "Private work",
           "stages": [{"code": "ONE", "title": "One", "tasks": [{"id": "T01", "title": "Do it"}]}]}
    plan = services.projects.plan(doc, key="p1")
    assert plan["project"]["visibility"] == "private"
    created = services.projects.create(doc, key="p1", plan_sha256=plan["plan_sha256"], actor="user:ana")
    return services.projects.attach_stage("p1", "CNT", actor="user:ana", expected_revision=created["revision"])["revision"]


def with_token(world, tmp_path) -> dict:
    """The GUI token of this world (``/gui/api`` needs it); returns the header."""
    world.services.settings.gui_token_file = tmp_path / "gui-token"
    return {"Authorization": f"Bearer {ensure_token(world.services.settings.gui_token_file)}"}


# ----- A1: run_task of another executor is not a way around the hand-off

def test_a_held_task_is_not_run_by_another_executor_without_the_hand_off(tmp_path):
    world = make_world(tmp_path, continuity_candidates="codex_cli=never")
    services = world.services
    hit_limit(world)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        before = snapshot(world)
        for body in ({"task_id": "T1", "executor_id": "codex_cli", "actor": "executor:claude_cli", "wait": True},
                     {"task_id": "T1", "executor_id": "codex_cli", "wait": True}):
            out = client.post(RUN, json=body)
            assert out.status_code == 409 and route_reason(out) == "person_required", out.json()
        assert snapshot(world) == before                       # nothing launched, nothing moved, no hand-off
    refused(lambda: services.executors.run_task("T1", "codex_cli"), "person_required")
    assert events(services, "continuity.handoff") == [] and world.hold() is not None
    assert services.tasks.get_task("T1")["assignee"] == "executor:claude_cli"


def test_unclaim_claim_and_run_is_not_a_way_around_the_hand_off_either(tmp_path):
    world = make_world(tmp_path, continuity_candidates="codex_cli=never")
    services = world.services
    hit_limit(world)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        for action, actor in (("unclaim", "user:mallory"), ("claim", "executor:codex_cli")):
            moved = client.post("/api/v1/tasks/T1/transitions", json={"action": action, "actor": actor})
            assert moved.status_code == 200, moved.json()
        before = snapshot(world)
        out = client.post(RUN, json={"task_id": "T1", "executor_id": "codex_cli", "wait": True})
        assert out.status_code == 409 and route_reason(out) == "person_required", out.json()
        assert snapshot(world) == before
    assert len(calls(world, "codex_cli")) == 0 and events(services, "continuity.handoff") == []


def test_the_same_executor_still_resumes_its_own_held_task_by_hand(world):
    services = world.services
    hit_limit(world)
    services.executors.lift_pause("claude_cli", reason="the person is back")
    out = services.executors.run_task("T1", "claude_cli")                 # not another executor: the hold is cleared
    assert out["outcome"] == "submitted" and world.hold() is None
    assert events(services, "continuity.resumed", "T1")[0]["payload"]["trigger"] == "manual"


# ----- A2: privacy is taken off a project only with the token

class _Same:
    """The CLI opens its client with ``with api_client() as client``: hand it the shared test client."""

    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *exc_info):
        return False


def test_a_private_project_is_made_normal_only_with_the_token(tmp_path, monkeypatch, capsys):
    world = make_world(tmp_path)
    services = world.services
    header = with_token(world, tmp_path)
    revision = make_private_project(world)
    hit_limit(world)                                         # a far reset: the policy would delegate if it could

    def asked() -> list:
        return services.executors.decide_holds(dry_run=True)["asked"][0]["ask"]

    assert asked() == ["private_project"]
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        lift = {"actor": "user:nobody", "expected_revision": revision, "visibility": "normal"}
        refusal = client.patch("/api/v1/projects/p1", json=lift)
        assert refusal.status_code == 409 and route_reason(refusal) == "person_required", refusal.json()
        assert services.projects.get("p1")["visibility"] == "private" and asked() == ["private_project"]
        # the same through the CLI (it talks to this route)
        monkeypatch.setattr(cli, "api_client", lambda: _Same(client))
        with pytest.raises(SystemExit):
            cli.main(["project", "update", "p1", "--actor", "user:nobody", "--revision", str(revision),
                      "--visibility", "normal"])
        assert "HTTP 409" in capsys.readouterr().err and services.projects.get("p1")["visibility"] == "private"
        # a title is still changed there, and so is the visibility when it only restricts
        renamed = client.patch("/api/v1/projects/p1", json={"actor": "user:ana", "expected_revision": revision,
                                                            "title": "Renamed", "visibility": "private"})
        assert renamed.status_code == 200 and renamed.json()["title"] == "Renamed"
        revision = renamed.json()["revision"]
        # the GUI route needs the token
        body = {"visibility": "normal", "expected_revision": revision}
        assert client.post("/gui/api/projects/p1/visibility", json=body).status_code == 401
        assert client.post("/gui/api/projects/p1/visibility", headers={"Authorization": "Bearer wrong"},
                           json=body).status_code == 401
        assert services.projects.get("p1")["visibility"] == "private"
        # ... and with it the person lifts the privacy; from then on the policy may hand the task over
        lifted = client.post("/gui/api/projects/p1/visibility", headers=header, json=body)
        assert lifted.status_code == 200 and lifted.json()["visibility"] == "normal", lifted.json()
        (planned,) = services.executors.decide_holds(dry_run=True)["delegated"]
        assert planned["to_executor"] == "codex_cli" and planned["by"] == "policy:semi_auto"
        # making it private again needs no token: it restricts
        again = client.patch("/api/v1/projects/p1", json={"actor": "user:nobody", "visibility": "private",
                                                          "expected_revision": lifted.json()["revision"]})
        assert again.status_code == 200 and again.json()["visibility"] == "private"
        assert services.executors.decide_holds(dry_run=True)["asked"][0]["ask"] == ["private_project"]
        stale = client.post("/gui/api/projects/p1/visibility", headers=header, json={**body, "expected_revision": revision})
        assert stale.status_code == 409                                       # the page showed an older revision


def test_the_facade_keeps_changing_the_visibility_in_process(world):
    services = world.services
    revision = make_private_project(world)
    refused(lambda: services.projects.update("p1", actor="user:ana", expected_revision=revision, visibility="normal",
                                              may_lift_private=False), "person_required")
    assert services.projects.get("p1")["visibility"] == "private"
    lifted = services.projects.update("p1", actor="user:ana", expected_revision=revision, visibility="normal")
    assert lifted["visibility"] == "normal"                    # in-process code is trusted; HTTP and the CLI are not
    private = services.projects.update("p1", actor="user:ana", expected_revision=lifted["revision"],
                                       visibility="private", may_lift_private=False)       # restricting needs no token
    assert private["visibility"] == "private"


# ----- M1: the person always wins over the policy

def test_a_person_choosing_to_wait_while_another_task_is_handed_over_is_obeyed(world):
    services = world.services
    two_held_tasks(world)
    # While the delegate of the first task runs (it can take minutes), the person picks "wait" on the second.
    chosen: list[int] = []

    def person_waits(folder):
        if not chosen:
            chosen.append(1)
            card_decides(world, None, decision="wait", task_id="T2", decision_id="decision-wait-2")

    world.fakes["codex_cli"].work = person_waits
    out = services.executors.decide_holds()
    assert [item["task_id"] for item in out["delegated"]] == ["T1"] and chosen == [1]
    assert [(item["task_id"], item["why"]) for item in out["waiting"]] == [("T2", "decided:wait:user")]
    assert services.tasks.get_task("T2")["assignee"] == "executor:claude_cli" and len(calls(world, "codex_cli")) == 1
    assert events(services, "continuity.handoff", "T2") == []
    assert world.hold("T2")["decision"] == "wait" and world.hold("T2")["decided_by"] == "user"


def test_a_person_delegating_before_the_policy_writes_its_wait_is_not_overwritten(world, monkeypatch):
    services = world.services
    hit_limit(world, reset=at(2))                              # a close reset: the policy waits
    real = services.executors._decision_for

    def then_the_person_chooses(*args, **kwargs):
        found = real(*args, **kwargs)
        card_decides(world, "codex_cli")                       # before the policy writes
        return found

    monkeypatch.setattr(services.executors, "_decision_for", then_the_person_chooses)
    out = services.executors.decide_holds()
    assert [item["why"] for item in out["waiting"]] == ["reset_within_threshold"]
    hold = world.hold()
    assert (hold["decision"], hold["candidate"], hold["blocked"]) == ("delegate", "codex_cli", "delegation_requested")
    assert [event for event in events(services, "continuity.decided", "T1") if event["payload"]["by"] != "user"] == []
    monkeypatch.undo()
    (done,) = services.executors.decide_holds()["delegated"]          # the person's choice is not lost
    assert done["by"] == "user" and done["executed"] is True


def test_the_policy_never_writes_over_a_decision_that_is_already_there(world):
    services = world.services
    hit_limit(world, reset=at(2))
    seen = world.hold()
    card_decides(world, None, decision="wait", decision_id="decision-0002")
    decision = handoff.Decision("wait", "reset_within_threshold", "policy:semi_auto")
    services.executors._record_wait(services.tasks.get_task("T1"), seen, decision)
    assert world.hold()["decided_by"] == "user" and len(events(services, "continuity.decided", "T1")) == 1


def test_recording_a_wait_is_one_transaction(world, monkeypatch):
    services = world.services
    hit_limit(world, reset=at(2))
    store = services.tasks.store
    real = store.append_event

    def failing(conn, event_type, **kwargs):
        if event_type == "continuity.decided":
            raise RuntimeError("the disk is full")
        return real(conn, event_type, **kwargs)

    monkeypatch.setattr(store, "append_event", failing)
    decision = handoff.Decision("wait", "reset_within_threshold", "policy:semi_auto")
    with pytest.raises(RuntimeError):
        services.executors._record_wait(services.tasks.get_task("T1"), world.hold(), decision)
    assert world.hold()["decision"] == "pending"                # neither the hold nor the event
    monkeypatch.undo()
    services.executors.decide_holds()
    assert world.hold()["decision"] == "wait" and len(events(services, "continuity.decided", "T1")) == 1


# ----- M2: a cancel the person wrote stops the hand-off

def post_cancel(world, task_id: str = "T1") -> None:
    world.services.settings.availability_enabled = True
    world.services.inbox.post("task", task_id, message_id="cancel-1", kind="cancel", text="stop", actor="user:ana")


def never_asked_for_the_delegate(monkeypatch, services) -> list[int]:
    """Spy on the first reading of the world outside the task row: a refusal that costs nothing never gets that far."""
    real, asked = services.executors._handoff_delegate, []
    monkeypatch.setattr(services.executors, "_handoff_delegate", lambda *args, **kwargs: asked.append(1) or real(*args, **kwargs))
    return asked


def test_a_pending_cancel_stops_every_hand_off(world, monkeypatch):
    services = world.services
    asked_for_the_delegate = never_asked_for_the_delegate(monkeypatch, services)
    hit_limit(world)                                           # a far reset: the policy would delegate
    post_cancel(world)
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and [item["why"] for item in out["skipped"]] == ["inbox_cancelled"]
    card_decides(world, "codex_cli")
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "inbox_cancelled")
    assert [item["why"] for item in services.executors.decide_holds()["skipped"]] == ["inbox_cancelled"]
    assert asked_for_the_delegate == []                         # refused before anything outside the task row was read
    assert_not_handed_over(world)


def test_a_cancel_written_after_the_first_check_stops_the_hand_off_inside_the_transaction(world, monkeypatch):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    services.settings.availability_enabled = True
    ran = after_the_first_check(monkeypatch, services, lambda: post_cancel(world))
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "inbox_cancelled")
    assert len(ran) == 1
    assert_not_handed_over(world)


# ----- M3: one integration marker for the whole installation

def test_the_marker_is_one_setting_and_the_services_read_it(tmp_path):
    default = Settings(_env_file=None, local_dir=tmp_path / "local")
    assert default.integration_marker == default.data_dir / "watchers" / "integration.lock"
    elsewhere = tmp_path / "elsewhere" / "busy.lock"
    world = make_world(tmp_path, integration_marker=elsewhere)
    services = world.services
    assert services.settings.integration_marker == elsewhere.resolve()
    hit_limit(world)
    card_decides(world, "codex_cli")
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("{}", encoding="utf-8")
    out = services.executors.decide_holds()
    assert out["integration"] is True and str(elsewhere.resolve()) in out["integration_message"]
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "integration_in_progress")
    elsewhere.unlink()
    assert services.executors.decide_holds()["delegated"][0]["executed"] is True


def test_neither_state_dir_nor_integration_marker_moves_the_marker_the_watcher_takes(tmp_path, monkeypatch, capsys):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    taken: list = []

    class Spy:
        def __init__(self, root, executors, *, integration_marker, integration_active):
            taken.append((Path(root), Path(integration_marker)))

        def sweep(self):
            return []

    monkeypatch.setattr("multiagent.services.watcher.WatcherProcess", Spy)
    cli.main(["watcher", "sweep", "--state-dir", str(tmp_path / "state")])
    cli.main(["watcher", "sweep", "--integration-marker", str(settings.integration_marker)])
    assert taken == [(tmp_path / "state", settings.integration_marker),
                     (settings.data_dir / "watchers", settings.integration_marker)]
    capsys.readouterr()
    for argv in (["watcher", "sweep", "--integration-marker", str(tmp_path / "hidden.lock")],
                 ["watcher", "run", "--task", "T1", "--hold", "h1", "--attempt", "1",
                  "--integration-marker", str(tmp_path / "state" / "integration.lock")]):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert "marker of this installation" in capsys.readouterr().err
    assert len(taken) == 2


# ----- M4: a session open in the app is not retired and replaced

def open_in_app(world) -> str:
    session = world.hold()["session_id"]
    world.services.executors.record_app_open(session, source="claude")
    return session


def test_the_policy_asks_instead_of_replacing_a_session_open_in_the_app(world):
    services = world.services
    hit_limit(world, reset=at(20))                             # a far reset: the policy would delegate
    session = open_in_app(world)
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and "session_open_in_app" in out["asked"][0]["ask"]
    assert events(services, "session.retired") == [] and len(calls(world, "codex_cli")) == 0
    world.clock.to(at(13))                                     # the 12 hours are over: nobody is working in it any more
    assert services.executors.decide_holds()["delegated"][0]["executed"] is True
    assert events(services, "session.retired", "T1")[0]["ref"] == session


def test_a_person_does_not_replace_an_open_session_unless_the_card_says_so(world, monkeypatch):
    services = world.services
    asked_for_the_delegate = never_asked_for_the_delegate(monkeypatch, services)
    hit_limit(world)
    open_in_app(world)
    card_decides(world, "codex_cli")
    error = refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "session_open_in_app")
    assert "blocked_until" in error.details
    assert [item["why"] for item in services.executors.decide_holds()["skipped"]] == ["session_open_in_app"]
    assert asked_for_the_delegate == []                         # refused before anything outside the task row was read
    assert_not_handed_over(world)
    monkeypatch.undo()
    # The card confirms explicitly that the person accepts replacing it: then a person's hand-off goes ahead...
    task = services.tasks.get_task("T1")
    services.tasks._edit_continuity("T1", {"hold": {**task["continuity"]["hold"], handoff.REPLACE_OPEN_SESSION: True}},
                                    actor="test", expected_revision=task["revision"])
    assert services.executors.handoff_task("T1", "codex_cli")["to_executor"] == "codex_cli"


def test_the_policy_does_not_replace_an_open_session_even_when_the_card_confirms(world):
    services = world.services
    hit_limit(world)
    open_in_app(world)
    task = services.tasks.get_task("T1")
    services.tasks._edit_continuity("T1", {"hold": {**task["continuity"]["hold"], handoff.REPLACE_OPEN_SESSION: True}},
                                    actor="test", expected_revision=task["revision"])
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and "session_open_in_app" in out["asked"][0]["ask"]
    refused(lambda: services.executors._handoff(
        "T1", "codex_cli", by="policy:semi_auto", actor="policy:semi_auto", decision_id=None, expected_revision=None,
        run=False, timeout_seconds=None, automatic=True), "session_open_in_app")
    assert_not_handed_over(world)


def test_a_session_opened_after_the_first_check_stops_the_hand_off_inside_the_transaction(world, monkeypatch):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    session = world.hold()["session_id"]
    ran = after_the_first_check(monkeypatch, services, lambda: services.executors.record_app_open(session, source="claude"))
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "session_open_in_app")
    assert len(ran) == 1
    assert_not_handed_over(world)


# ----- the low findings that were fixed

def test_a_marker_that_nobody_removes_says_where_it_is_how_old_and_what_to_do(world):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    marker = services.settings.integration_marker
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"schema": "multiagent.watcher_gate/1", "pid": 4242, "owner": "x"}), encoding="utf-8")
    old = time.time() - 90 * 3600
    os.utime(marker, (old, old))
    error = refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "integration_in_progress")
    assert str(marker) in error.message and "5400 minutes old" in error.message and "4242" in error.message
    assert "multiagent watcher sweep" in error.message
    assert error.details["marker"] == str(marker) and error.details["marker_age_minutes"] >= 5400
    assert marker.exists()                                      # nothing is deleted for the person
    marker.unlink()
    marker.mkdir()                                              # a folder where the file goes: nobody can tell, so closed
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "integration_in_progress")


def test_a_file_where_the_marker_folder_should_be_is_not_calm(world):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli")
    folder = services.settings.integration_marker.parent
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.write_text("not a folder", encoding="utf-8")
    refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "integration_in_progress")


def test_a_delegation_with_no_card_event_behind_it_is_not_a_persons_word(world):
    services = world.services
    hit_limit(world)
    task = services.tasks.get_task("T1")
    hold = {**task["continuity"]["hold"], "decision": "delegate", "candidate": "codex_cli", "blocked": "delegation_requested"}
    services.tasks._edit_continuity("T1", {"hold": hold}, actor="test", expected_revision=task["revision"])
    before = snapshot(world)
    refused(lambda: services.executors.handoff_task("T1", actor="mallory", recorded_only=True), "person_required")
    assert snapshot(world) == before and events(services, "continuity.decided", "T1") == []


def test_the_policy_does_not_reuse_the_id_of_a_decision_the_person_took_back(world):
    services = world.services
    hit_limit(world)
    card_decides(world, "codex_cli", decision_id="decision-D1")
    card_decides(world, None, decision="wait", decision_id="decision-D2")
    hit_limit(world)                                            # it resumes, hits the limit again: a new hold
    (done,) = services.executors.decide_holds()["delegated"]
    assert done["by"] == "policy:semi_auto" and done["decision_id"] not in ("decision-D1", "decision-D2")
    (handed,) = events(services, "continuity.handoff", "T1")
    assert handed["payload"]["by"] == "policy:semi_auto" and handed["payload"]["decision_id"] == done["decision_id"]
    decided_by_policy = [event for event in events(services, "continuity.decided", "T1") if event["ref"] == done["decision_id"]]
    assert [event["payload"]["by"] for event in decided_by_policy] == ["policy:semi_auto"]


def test_a_budget_that_cannot_be_read_stops_a_person_too_and_the_cap_of_a_run_does_not_count(world, monkeypatch):
    services = world.services
    hit_limit(world)

    def unreadable(**kwargs):
        raise RuntimeError("no telemetry")

    monkeypatch.setattr(services.usage, "budgets", unreadable)
    error = refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "budget_exhausted")
    assert error.details["budget"] == "unknown"
    monkeypatch.setattr(services.usage, "budgets", lambda **kwargs: {"status": [
        {"budget_id": "old-run", "scope": "run", "state": "limit_reached", "mode": "cut"}]})
    assert services.executors.handoff_task("T1", "codex_cli", run=False)["to_executor"] == "codex_cli"


def test_repeating_a_hand_off_whose_launch_failed_says_that_the_delegate_never_started(world, monkeypatch):
    services = world.services
    hit_limit(world)

    def refuse(*args, **kwargs):
        raise ServiceError("invalid_state", "The executor cannot start", status=409, details={"reason": "executor_paused"})

    monkeypatch.setattr(services.executors, "_run_task", refuse)
    first = services.executors.handoff_task("T1", "codex_cli", decision_id="decision-0007")
    assert first["run_error"]["reason"] == "executor_paused"
    again = services.executors.handoff_task("T1", "codex_cli", decision_id="decision-0007")
    assert again["repeated"] is True and again["run"] is None
    assert again["run_error"]["reason"] == "delegate_not_started" and "executors run" in again["run_error"]["message"]


def test_one_task_that_fails_does_not_hide_the_report_of_the_others(world, monkeypatch):
    services = world.services
    two_held_tasks(world)
    real = services.executors._run_task

    def launch(task_id, *args, **kwargs):
        if task_id == "T2":
            raise RuntimeError("synthetic crash")
        return real(task_id, *args, **kwargs)

    monkeypatch.setattr(services.executors, "_run_task", launch)
    out = services.executors.decide_holds()
    by_task = {item["task_id"]: item for item in out["delegated"]}
    assert set(by_task) == {"T1", "T2"} and by_task["T1"]["run_outcome"] == "submitted"
    assert by_task["T2"]["run_error"]["code"] == "internal_error"           # the hand-off stood; its launch did not
    assert by_task["T2"]["run_error"]["reason"] == "RuntimeError"
    monkeypatch.undo()
    hit_limit(world, "T3")
    real_delegate = services.executors._handoff_delegate

    def crash_for_t3(task, *args, **kwargs):
        if task["id"] == "T3":
            raise RuntimeError("synthetic crash")
        return real_delegate(task, *args, **kwargs)

    monkeypatch.setattr(services.executors, "_handoff_delegate", crash_for_t3)
    again = services.executors.decide_holds(task_id="T3")
    assert again["delegated"] == [] and [(item["task_id"], item["why"]) for item in again["skipped"]] == [
        ("T3", "internal_error")]


# ----- B7: the GUI shows the policy that is really applied

def test_the_gui_shows_the_policy_in_force_and_the_policy_of_each_candidate_on_the_card(tmp_path):
    world = make_world(tmp_path, continuity_mode="manual", continuity_wait_threshold_hours=24, continuity_max_handoffs=3,
                       continuity_candidates="codex_cli=never")
    header = with_token(world, tmp_path)
    hit_limit(world)
    with TestClient(create_app(services=world.services), base_url="http://localhost") as client:
        policy = client.get("/gui/api/continuity/policy", headers=header).json()
        assert (policy["mode"], policy["wait_threshold_hours"], policy["max_handoffs"], policy["source"]) == (
            "manual", 24, 3, "settings")
        assert {row["executor_id"]: row["policy"] for row in policy["candidates"]} == {
            "claude_cli": "no_ask", "codex_cli": "never"}
        (card,) = client.get("/gui/api/continuity/decisions", headers=header).json()["decisions"]
        assert {item["executor_id"]: item["policy"] for item in card["candidates"]} == {"codex_cli": "never"}
        assert card["handoffs"]["max"] == 3


# ----- third review: N2, N3, N5, N6

def test_n2_a_hold_left_after_unclaim_and_claim_is_cleared_by_the_start_of_the_same_executor(tmp_path):
    world = make_world(tmp_path, continuity_candidates="codex_cli=never")
    services = world.services
    hit_limit(world)
    services.tasks.transition("T1", "unclaim", actor="user:operator", cause="test")
    services.tasks.transition("T1", "claim", actor="executor:claude_cli", cause="test")
    # Not paused any more: another executor is refused and the message names the way out of the dead end.
    error = refused(lambda: services.executors.run_task("T1", "codex_cli"), "person_required")
    assert "not paused" in error.message and "pause" in error.message
    services.executors.lift_pause("claude_cli", reason="the person is back")
    out = services.executors.run_task("T1", "claude_cli")           # the start clears the stale hold (it was kept before)
    assert out["outcome"] == "submitted" and world.hold() is None
    assert len(calls(world, "codex_cli")) == 0 and events(services, "continuity.handoff") == []


def test_n3_the_open_session_message_gives_the_time_and_promises_no_card_confirmation(world):
    services = world.services
    hit_limit(world)
    open_in_app(world)
    card_decides(world, "codex_cli")
    error = refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "session_open_in_app")
    assert "confirms" not in error.message and "card" not in error.message
    assert error.details["blocked_until"] in error.message


@pytest.mark.parametrize("name", ["integration_marker", "assets_dir"])
def test_n5_an_empty_path_setting_is_not_set_and_never_the_current_folder(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name.upper(), "")
    for settings in (Settings(_env_file=None, local_dir=tmp_path / "local"),
                     Settings(_env_file=None, local_dir=tmp_path / "local", **{name: ""})):
        value = getattr(settings, name)
        assert value != Path.cwd().resolve() and value.is_relative_to((tmp_path / "local").resolve())
    assert Settings(_env_file=None, local_dir=tmp_path / "local").integration_marker.name == "integration.lock"


def test_n6_an_error_deciding_one_task_does_not_abort_the_sweep_after_another_moved(world, monkeypatch):
    services = world.services
    hit_limit(world, reset=at(20))
    hit_limit(world, "T2", reset=at(20))
    real = services.executors._decision_for

    def broken_for_t2(view, *args, **kwargs):
        if view["id"] == "T2":
            raise RuntimeError("synthetic crash")
        return real(view, *args, **kwargs)

    monkeypatch.setattr(services.executors, "_decision_for", broken_for_t2)
    out = services.executors.decide_holds()
    assert [item["task_id"] for item in out["delegated"]] == ["T1"]
    assert [(item["task_id"], item["why"]) for item in out["skipped"]] == [("T2", "internal_error")]
