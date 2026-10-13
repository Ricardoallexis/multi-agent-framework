# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CNT-T12: waits by condition (contract ``CNT-T01-contrato.md`` Amendment 2).

Two parts. The first tests ``multiagent/services/waits.py`` alone: it is pure, so every case is a fixed clock and a
hand-made :class:`WaitFacts`, with no database and no executor. The second part tests the service around it
(``hold_on_condition``, ``check_waits``, ``resume_met``, ``record_approval``) with the fake executor, the fake clock and the
real lifecycle, event log and envelope store of ``test_quota_hold.py``. No model is called, no real agent is started and no
real application folder is read.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from multiagent.executors.limits import utc_iso
from multiagent.services import hold as hold_module
from multiagent.services import waits
from multiagent.services.waits import ApprovalFact, IntegrationFacts, Progress, WaitError, WaitFacts
from test_quota_hold import at, events, limit, make_world, refused

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def moment(**delta: float) -> datetime:
    return NOW + timedelta(**delta)


def z(value: datetime) -> str:
    return utc_iso(value)


def make_facts(now: datetime = NOW, **parts) -> WaitFacts:
    return WaitFacts(now=now, **parts)


def block(spec: dict, **options) -> dict:
    return waits.normalize_condition(spec, now=NOW, **options)


def time_spec(when: datetime, **extra) -> dict:
    return {"type": "time", "params": {"at": z(when), **extra}}


def dependency_spec(*task_ids: str, requires: str = "accepted") -> dict:
    return {"type": "dependency", "params": {"task_ids": list(task_ids), "requires": requires}}


def integration_spec(window: str = "release", **extra) -> dict:
    return {"type": "integration_window", "params": {"window": window, **extra}}


def approval_spec(approval: str = "ship", **extra) -> dict:
    return {"type": "approval", "params": {"approval": approval, **extra}}


def all_of(*specs: dict) -> dict:
    return {"type": "all", "params": {"conditions": list(specs)}}


DURATIONS = (600.0, 660.0, 720.0, 780.0, 840.0)        # median 720 s, quartiles 660 and 780, 90th percentile 816


# =========================================================================== the condition block

def test_a_condition_is_built_with_closed_structured_parameters():
    built = block(dependency_spec("T1", "T2", requires="submitted"), watcher_id="watch-1")

    assert built == {
        "type": "dependency", "params": {"task_ids": ["T1", "T2"], "requires": "submitted"},
        "since": "2026-10-11T12:00:00Z", "deadline_at": "2026-10-12T00:00:00Z", "met_at": None, "checked_at": None,
        "next_check_at": None, "estimate": None, "watcher_id": "watch-1"}
    assert waits.summarize(built) == {"type": "dependency", "parts": ["dependency"], "since": "2026-10-11T12:00:00Z",
                                      "deadline_at": "2026-10-12T00:00:00Z"}
    several = block(all_of(time_spec(moment(hours=2)), approval_spec("ship", approver="owner")))
    assert several["type"] == "all"
    assert [leaf["type"] for leaf in several["params"]["conditions"]] == ["time", "approval"]
    assert all(leaf["met_at"] is None for leaf in several["params"]["conditions"])
    assert block(time_spec(moment(hours=1)), deadline=timedelta(hours=2))["deadline_at"] == "2026-10-11T14:00:00Z"
    assert block({**dependency_spec("T1"), "deadline_at": "2026-10-11T18:00:00Z"})["deadline_at"] == "2026-10-11T18:00:00Z"


@pytest.mark.parametrize("spec", [
    {"type": "script", "params": {}},                                                    # an unknown type
    {"type": "dependency", "params": {"task_ids": [], "requires": "accepted"}},          # nothing to wait for
    {"type": "dependency", "params": {"task_ids": ["T1", "T1"]}},                        # repeated
    {"type": "dependency", "params": {"task_ids": ["has space"]}},                       # not an id
    {"type": "dependency", "params": {"task_ids": ["T1"], "requires": "whenever"}},
    {"type": "dependency", "params": {"task_ids": [f"T{n}" for n in range(9)]}},         # too many
    {"type": "dependency", "params": {"task_ids": ["T1"], "action": "rm -rf /"}},        # free keys are refused
    {"type": "time", "params": {"at": "tomorrow"}},
    {"type": "time", "params": {"at": "2026-10-11T13:00:00Z", "confidence": "certain"}},
    {"type": "integration_window", "params": {"window": "a b"}},
    {"type": "approval", "params": {"approval": "ship", "approver": "x y"}},
    {"type": "approval", "params": {}},
    {"type": "all", "params": {"conditions": [dependency_spec("T1")]}},                 # an all needs two or more
    {"type": "all", "params": {"conditions": [all_of(dependency_spec("T1"), approval_spec())] * 2}},   # no nesting
    {"type": "dependency", "params": {"task_ids": ["T1"]}, "script": "x.ps1"},
])
def test_a_malformed_condition_is_refused(spec):
    with pytest.raises(WaitError):
        block(spec)


def test_the_deadline_is_bounded_and_a_time_after_it_is_refused():
    with pytest.raises(WaitError):
        block(dependency_spec("T1"), deadline=timedelta(0))
    with pytest.raises(WaitError):
        block(dependency_spec("T1"), deadline=timedelta(days=10))
    with pytest.raises(WaitError):
        block(time_spec(moment(hours=13)))                    # the default deadline is 12 hours: it could never be met
    block(time_spec(moment(hours=13)), deadline=timedelta(hours=14))


def test_the_reason_of_a_wait_is_the_one_it_really_waits_for():
    assert waits.reason_for(block(dependency_spec("T1"))) == "dependency"
    assert waits.reason_for(block(all_of(time_spec(moment(hours=1)), integration_spec()))) == "integration_window"
    assert waits.reason_for(block(all_of(approval_spec(), dependency_spec("T1"))), "dependency") == "dependency"
    assert set(waits.WAIT_REASONS) == {"dependency", "integration_window", "approval"}
    assert set(waits.HOLD_REASONS) == set(hold_module.LIMIT_REASONS) | set(waits.WAIT_REASONS)
    with pytest.raises(WaitError):
        waits.reason_for(block(time_spec(moment(hours=1))))   # a time alone is a limit's reset, not a wait
    with pytest.raises(WaitError):
        waits.reason_for(block(dependency_spec("T1")), "approval")


# =========================================================================== time: exact

def test_a_time_wait_is_exact_and_never_reaches_one_hundred_before_it_is_met():
    condition = block(time_spec(moment(hours=4)), deadline=timedelta(hours=5))

    half = waits.evaluate(condition, make_facts(moment(hours=2)))
    assert (half.state, half.met_at) == ("waiting", None)
    assert half.progress.percent == 50 and half.progress.kind == "exact" and half.progress.basis == "exact_time"
    assert half.progress.eta == moment(hours=4)
    last = waits.evaluate(condition, make_facts(moment(hours=4) - timedelta(seconds=1)))
    assert last.progress.percent == 99                         # not 100: nothing has been seen to hold yet
    start = waits.evaluate(condition, make_facts(NOW))
    assert start.progress.percent == 0

    done = waits.evaluate(condition, make_facts(moment(hours=4, minutes=3)))
    assert done.state == "met" and done.met_at == moment(hours=4, minutes=3)      # when it was seen, not when it was due
    assert (done.progress.percent, done.progress.kind, done.progress.met_at) == (100, "met", done.met_at)


def test_a_time_that_came_from_a_limit_says_how_far_to_trust_it():
    condition = block(time_spec(moment(hours=4), confidence="medium", source="app_text"), deadline=timedelta(hours=5))
    pending = waits.evaluate(condition, make_facts(moment(hours=1))).progress
    assert (pending.kind, pending.basis, pending.percent) == ("approximate", "time_from_limit", 25)
    assert pending.detail["confidence"] == "medium" and pending.detail["source"] == "app_text"
    high = block(time_spec(moment(hours=4), confidence="high"), deadline=timedelta(hours=5))
    assert waits.evaluate(high, make_facts(moment(hours=1))).progress.kind == "exact"


def test_a_progress_is_one_hundred_percent_only_with_its_met_at():
    with pytest.raises(ValueError):
        Progress(100, None, "exact", "exact_time")
    with pytest.raises(ValueError):
        Progress(50, None, "met", "met", met_at=NOW)
    with pytest.raises(ValueError):
        Progress(101, None, "exact", "x")
    assert Progress.done(NOW).to_dict()["percent"] == 100
    # whatever the checkers answer for a wait that is not met, the figure stays under one hundred
    for hours in (0, 1, 3, 4):
        condition = block(all_of(time_spec(moment(hours=4)), dependency_spec("T1")), deadline=timedelta(hours=9))
        result = waits.evaluate(condition, make_facts(moment(hours=hours), task_states={"T1": "running"}))
        assert result.progress.percent is None or result.progress.percent < 100


# =========================================================================== dependency: no estimate

def test_a_dependency_is_met_by_the_rule_of_the_task_graph_and_has_no_estimate():
    condition = block(dependency_spec("T1", "T2"))
    pending = waits.evaluate(condition, make_facts(task_states={"T1": "accepted", "T2": "running"}))
    assert pending.state == "waiting" and pending.parts[0].detail == "waiting_for_tasks"
    assert pending.parts[0].info["pending"] == ["T2"]
    assert pending.progress.percent is None and pending.progress.eta is None
    assert pending.progress.basis == "depends_on_agent" and pending.progress.detail["tasks"] == ["T2"]
    assert pending.blocked is None
    assert waits.evaluate(condition, make_facts(task_states={"T1": "accepted", "T2": "accepted"})).met

    submitted = block(dependency_spec("T1", requires="submitted"))
    for state, expected in (("running", False), ("submitted", True), ("validating", True), ("accepted", True)):
        assert waits.evaluate(submitted, make_facts(task_states={"T1": state})).met is expected
    assert not waits.evaluate(condition, make_facts(task_states={"T1": "submitted", "T2": "accepted"})).met


def test_a_dependency_that_can_never_be_met_says_so_but_keeps_waiting_for_the_deadline():
    condition = block(dependency_spec("T1"))
    dead = waits.evaluate(condition, make_facts(task_states={"T1": "failed"}))
    assert dead.state == "waiting" and dead.blocked == "dependency_failed"
    assert waits.evaluate(condition, make_facts(task_states={"T1": "cancelled"})).blocked == "dependency_failed"
    assert waits.evaluate(condition, make_facts(task_states={})).blocked == "dependency_missing"
    assert waits.evaluate(condition, make_facts(task_states={"T1": "paused"})).blocked is None


def test_a_dependency_on_a_task_that_waits_for_a_time_gives_a_floor_and_no_forecast():
    condition = block(dependency_spec("T1", "T2"))
    result = waits.evaluate(condition, make_facts(task_states={"T1": "paused", "T2": "paused"},
                                                  not_before={"T1": moment(hours=3), "T2": moment(hours=5)}))
    assert result.progress.percent is None and result.progress.eta is None
    assert result.progress.detail["not_before"] == z(moment(hours=5))


# =========================================================================== integration window: two phases

def test_an_integration_has_no_percentage_until_it_starts():
    condition = block(integration_spec())
    unknown = waits.evaluate(condition, make_facts())                                  # nobody has said anything
    assert unknown.parts[0].detail == "no_evidence" and unknown.progress.percent is None
    waiting = waits.evaluate(condition, make_facts(integrations={"release": IntegrationFacts(durations=DURATIONS)}))
    assert waiting.parts[0].detail == "not_started"
    assert (waiting.progress.percent, waiting.progress.basis) == (None, "integration_not_started")


def test_a_running_integration_is_estimated_from_the_median_with_its_band_and_a_cap_of_95():
    condition = block(integration_spec())

    def at_elapsed(seconds: float) -> Progress:
        window = IntegrationFacts(started_at=NOW, durations=DURATIONS)
        return waits.evaluate(condition, make_facts(NOW + timedelta(seconds=seconds), integrations={"release": window})).progress

    start = at_elapsed(0)
    assert (start.percent, start.kind, start.basis) == (0, "approximate", "integration_median")
    half = at_elapsed(360)
    assert half.percent == 50 and half.eta == NOW + timedelta(seconds=720) and not half.frozen
    assert half.detail["median_s"] == 720 and half.detail["band_s"] == [660, 780] and half.detail["samples"] == 5
    assert at_elapsed(719).percent == 95                                    # 99 % of the median, capped
    over = at_elapsed(720)
    assert (over.percent, over.eta, over.frozen, over.basis) == (95, None, True, "integration_over_median")
    assert at_elapsed(815).basis == "integration_over_median"
    late = at_elapsed(816)
    assert (late.percent, late.eta, late.frozen, late.basis) == (95, None, True, "integration_longer_than_usual")
    assert at_elapsed(4000).percent == 95                                    # frozen, never past the cap


def test_an_integration_needs_three_earlier_windows_and_ignores_bad_samples():
    condition = block(integration_spec())
    few = IntegrationFacts(started_at=NOW, durations=(600.0, 700.0))
    result = waits.evaluate(condition, make_facts(moment(minutes=3), integrations={"release": few})).progress
    assert (result.percent, result.basis, result.detail["have"], result.detail["need"]) == (
        None, "integration_few_samples", 2, 3)
    junk = IntegrationFacts(started_at=NOW, durations=(-5.0, 0.0, True, 99999.0, 600.0, 660.0))   # only two are usable
    assert waits.evaluate(condition, make_facts(moment(minutes=3), integrations={"release": junk})).progress.percent is None
    only_last_five = IntegrationFacts(started_at=NOW, durations=(9.0, 9.0, *DURATIONS))
    estimate = waits.evaluate(condition, make_facts(moment(minutes=3), integrations={"release": only_last_five})).progress
    assert estimate.detail["samples"] == 5 and estimate.detail["median_s"] == 720


def test_an_integration_is_met_only_by_a_window_that_closed_well_after_the_wait_began():
    condition = block(integration_spec())

    def met(window: IntegrationFacts, **options) -> bool:
        return waits.evaluate(options.get("condition", condition),
                              make_facts(moment(hours=1), integrations={"release": window})).met

    assert met(IntegrationFacts(started_at=moment(minutes=5), ended_at=moment(minutes=20), outcome="ok"))
    assert not met(IntegrationFacts(started_at=moment(minutes=5), ended_at=moment(minutes=20), outcome="failed"))
    assert not met(IntegrationFacts(started_at=moment(minutes=-30), ended_at=moment(minutes=-10), outcome="ok"))   # older
    assert not met(IntegrationFacts(started_at=moment(minutes=5)))                                               # still open
    # a new window opened after the good one closed is running, not met
    again = IntegrationFacts(started_at=moment(minutes=40), ended_at=moment(minutes=20), outcome="ok", durations=DURATIONS)
    result = waits.evaluate(condition, make_facts(moment(hours=1), integrations={"release": again}))
    assert not result.met and result.parts[0].detail == "running"
    failed = waits.evaluate(condition, make_facts(moment(hours=1), integrations={"release": IntegrationFacts(
        started_at=moment(minutes=5), ended_at=moment(minutes=20), outcome="failed")}))
    assert failed.parts[0].detail == "last_failed" and failed.blocked is None and failed.progress.percent is None
    # "after" moves the line: a window that closed before it does not count
    later = block(integration_spec(after=z(moment(minutes=30))))
    window = IntegrationFacts(started_at=moment(minutes=5), ended_at=moment(minutes=20), outcome="ok")
    assert not met(window, condition=later)


# =========================================================================== approval

def test_an_approval_has_no_estimate_and_a_rejection_cannot_be_waited_out():
    condition = block(approval_spec())
    waiting = waits.evaluate(condition, make_facts())
    assert waiting.state == "waiting" and waiting.progress.percent is None and waiting.progress.basis == "needs_a_decision"
    assert waiting.progress.detail["waiting_since"] == z(NOW)
    pending = waits.evaluate(condition, make_facts(approvals={"ship": ApprovalFact("pending")}))
    assert not pending.met and pending.blocked is None
    approved = waits.evaluate(condition, make_facts(approvals={"ship": ApprovalFact("approved", "owner", moment(minutes=3))}))
    assert approved.met and approved.parts[0].info["by"] == "owner"
    rejected = waits.evaluate(condition, make_facts(approvals={"ship": ApprovalFact("rejected", "owner")}))
    assert not rejected.met and rejected.blocked == "approval_rejected"
    assert waits.evaluate(condition, make_facts(approvals={"other": ApprovalFact("approved")})).met is False


# =========================================================================== several conditions

def test_with_several_conditions_the_unknown_one_dominates():
    deadline = timedelta(hours=9)
    pending_deps = {"T1": "running"}
    one_unknown = waits.evaluate(block(all_of(time_spec(moment(hours=4)), dependency_spec("T1")), deadline=deadline),
                                 make_facts(moment(hours=2), task_states=pending_deps)).progress
    # the time is exact (50 %) but the dependency depends on an agent: the wait has no estimate
    assert (one_unknown.percent, one_unknown.eta, one_unknown.basis) == (None, None, "depends_on_agent")
    assert one_unknown.detail["unknown"] == ["dependency"]

    two_unknown = waits.evaluate(block(all_of(dependency_spec("T1"), approval_spec()), deadline=deadline),
                                 make_facts(task_states=pending_deps)).progress
    assert (two_unknown.percent, two_unknown.basis) == (None, "unknown_dominates")
    assert two_unknown.detail["unknown"] == ["dependency", "approval"]

    # a part that is already met no longer counts: the time alone then gives the estimate
    time_left = waits.evaluate(block(all_of(time_spec(moment(hours=4)), dependency_spec("T1")), deadline=deadline),
                               make_facts(moment(hours=2), task_states={"T1": "accepted"})).progress
    assert (time_left.percent, time_left.kind, time_left.eta) == (50, "exact", moment(hours=4))


def test_with_only_known_estimates_the_slowest_pending_part_gives_the_figure():
    deadline = timedelta(hours=9)
    window = IntegrationFacts(started_at=NOW, durations=DURATIONS)
    both = block(all_of(time_spec(moment(minutes=30)), integration_spec()), deadline=deadline)
    result = waits.evaluate(both, make_facts(moment(minutes=6), integrations={"release": window})).progress
    # the time ends at 12:30 (20 %), the integration at 12:12 (50 %): the slowest is the time
    assert (result.percent, result.eta, result.detail["slowest"]) == (20, moment(minutes=30), "time")
    # past its median an integration has no end time and counts as the slowest
    overdue = waits.evaluate(both, make_facts(moment(minutes=13), integrations={"release": window})).progress
    assert (overdue.percent, overdue.eta, overdue.frozen, overdue.detail["slowest"]) == (95, None, True, "integration_window")


def test_when_everything_holds_the_wait_is_at_one_hundred_with_the_latest_met_at():
    condition = block(all_of(time_spec(moment(hours=1)), dependency_spec("T1")), deadline=timedelta(hours=9))
    done = waits.evaluate(condition, make_facts(moment(hours=2), task_states={"T1": "accepted"}))
    assert done.met and done.met_at == moment(hours=2)
    assert done.progress.percent == 100 and done.progress.met_at == moment(hours=2)
    assert [part.met_at for part in done.parts] == [moment(hours=2), moment(hours=2)]


# =========================================================================== a worse estimate is said, not hidden

def test_an_estimate_that_gets_worse_is_marked_revised_and_not_changed_in_silence():
    better = Progress(60, moment(hours=1), "approximate", "integration_median")
    worse_percent = Progress(40, moment(hours=1), "approximate", "integration_median")
    later = Progress(60, moment(hours=1, minutes=5), "approximate", "integration_median")
    jitter = Progress(60, moment(hours=1, seconds=30), "approximate", "integration_median")
    gone = Progress.unknown("integration_not_started")
    previous = better.to_dict()

    assert waits.mark_revised(previous, worse_percent).revised and waits.mark_revised(previous, worse_percent).percent == 40
    assert waits.mark_revised(previous, later).revised
    assert waits.mark_revised(previous, gone).revised
    assert not waits.mark_revised(previous, jitter).revised
    assert not waits.mark_revised(previous, Progress(80, moment(hours=1), "approximate", "integration_median")).revised
    assert not waits.mark_revised(None, worse_percent).revised
    assert not waits.mark_revised(previous, Progress.done(NOW)).revised


def test_an_evaluation_marks_the_revision_from_the_stored_estimate():
    condition = block(integration_spec(), deadline=timedelta(hours=9))
    window = IntegrationFacts(started_at=NOW, durations=DURATIONS)
    first = waits.evaluate(condition, make_facts(moment(seconds=360), integrations={"release": window}))
    assert not first.progress.revised
    recorded = waits.apply_evaluation(condition, first)
    assert recorded["estimate"]["percent"] == 50
    restarted = IntegrationFacts(started_at=moment(seconds=300), durations=DURATIONS)       # the window began again
    second = waits.evaluate(recorded, make_facts(moment(seconds=360), integrations={"release": restarted}))
    assert second.progress.percent == 8 and second.progress.revised


# =========================================================================== evaluating a stored block

def test_the_same_block_and_facts_always_give_the_same_answer():
    condition = block(all_of(time_spec(moment(hours=2)), integration_spec(), dependency_spec("T1")),
                      deadline=timedelta(hours=9))
    world = make_facts(moment(hours=1), task_states={"T1": "running"},
                       integrations={"release": IntegrationFacts(started_at=NOW, durations=DURATIONS)})
    assert waits.evaluate(condition, world).to_dict() == waits.evaluate(condition, world).to_dict()
    assert json.dumps(waits.evaluate(condition, world).to_dict())            # plain JSON, ready for an interface


def test_a_part_that_was_met_keeps_its_instant_and_one_that_no_longer_holds_loses_it():
    condition = block(all_of(time_spec(moment(hours=1)), dependency_spec("T1")), deadline=timedelta(hours=9))
    first = waits.evaluate(condition, make_facts(moment(hours=2), task_states={"T1": "running"}))
    assert first.parts[0].met_at == moment(hours=2) and not first.met
    stored = waits.apply_evaluation(condition, first)
    second = waits.evaluate(stored, make_facts(moment(hours=3), task_states={"T1": "accepted"}))
    assert second.parts[0].met_at == moment(hours=2)                       # the time was seen at 14:00 and stays there
    assert second.met and second.met_at == moment(hours=3)

    recorded = waits.apply_evaluation(stored, second)
    assert recorded["met_at"] == z(moment(hours=3))
    reopened = waits.evaluate(recorded, make_facts(moment(hours=4), task_states={"T1": "running"}))
    assert not reopened.met and reopened.revoked and reopened.met_at is None
    assert waits.apply_evaluation(recorded, reopened)["met_at"] is None


def test_a_deadline_that_passes_with_the_condition_unmet_expires_the_wait():
    condition = block(dependency_spec("T1"), deadline=timedelta(hours=2))
    running = {"T1": "running"}
    before = waits.evaluate(condition, make_facts(moment(hours=2) - timedelta(seconds=1), task_states=running))
    assert (before.state, before.expired) == ("waiting", False)
    at_deadline = waits.evaluate(condition, make_facts(moment(hours=2), task_states=running))
    assert (at_deadline.state, at_deadline.expired, at_deadline.next_check_at) == ("expired", True, None)
    assert waits.apply_evaluation(condition, at_deadline)["expired_at"] == z(moment(hours=2))
    # a condition that holds at the very instant of the deadline is met, not expired
    assert waits.evaluate(condition, make_facts(moment(hours=2), task_states={"T1": "accepted"})).state == "met"
    # no deadline: it never expires
    open_ended = {**condition, "deadline_at": None}
    assert not waits.evaluate(open_ended, make_facts(moment(days=30), task_states=running)).expired


def test_the_next_check_is_the_earliest_of_the_interval_the_next_time_and_the_deadline():
    interval = waits.CHECK_INTERVAL
    plain = block(dependency_spec("T1"), deadline=timedelta(hours=9))
    assert waits.evaluate(plain, make_facts(task_states={"T1": "running"})).next_check_at == NOW + interval
    timed = block(all_of(time_spec(moment(seconds=45)), dependency_spec("T1")), deadline=timedelta(hours=9))
    assert waits.evaluate(timed, make_facts(task_states={"T1": "running"})).next_check_at == moment(seconds=45)
    near = block(dependency_spec("T1"), deadline=timedelta(seconds=30))
    assert waits.evaluate(near, make_facts(task_states={"T1": "running"})).next_check_at == moment(seconds=30)
    assert waits.evaluate(plain, make_facts(task_states={"T1": "accepted"})).next_check_at is None


def test_applying_an_evaluation_writes_the_durable_facts_and_the_last_check_only_when_asked():
    condition = block(all_of(time_spec(moment(hours=1)), dependency_spec("T1")), deadline=timedelta(hours=9))
    result = waits.evaluate(condition, make_facts(moment(hours=2), task_states={"T1": "accepted"}))
    quiet = waits.apply_evaluation(condition, result, volatile=False)
    assert quiet["met_at"] == z(moment(hours=2)) and quiet["checked_at"] is None and quiet["estimate"] is None
    assert [leaf["met_at"] for leaf in quiet["params"]["conditions"]] == [z(moment(hours=2))] * 2
    full = waits.apply_evaluation(condition, result)
    assert full["checked_at"] == z(moment(hours=2)) and full["estimate"]["percent"] == 100
    assert condition["met_at"] is None and condition["params"]["conditions"][0]["met_at"] is None   # the original is untouched


@pytest.mark.parametrize("broken", [
    {"type": "mystery", "params": {}, "since": z(NOW)},
    {"type": "all", "params": {}, "since": z(NOW)},
    {"type": "all", "params": {"conditions": [{"type": "all", "params": {}}]}, "since": z(NOW)},
    {"type": "dependency", "params": {"task_ids": ["T1"]}, "since": "not a time"},
    {"type": "time", "params": {"at": "soon"}, "since": z(NOW)},
    {"type": "dependency", "params": {"task_ids": ["T1"], "requires": "whenever"}, "since": z(NOW)},
])
def test_a_stored_block_that_is_malformed_is_reported_not_guessed(broken):
    with pytest.raises(WaitError):
        waits.evaluate(broken, make_facts())


# =========================================================================== when a hold may be resumed

def test_a_wait_is_due_only_when_its_condition_holds_and_nothing_else_stops_it():
    base = waits.build_wait_hold(block(dependency_spec("T1")), reason="dependency", now=NOW, hold_id="h1",
                                 executor_id="fake_cli", actor="executor:fake_cli", session_id=None, attempt=1,
                                 settings={"model": None, "permission": "write"})

    def due(hold=base, **options):
        values = {"now": moment(minutes=5), "executor_paused": False, "condition_met": True}
        return waits.wait_due(hold, **{**values, **options})

    assert due() == (True, "condition_met")
    assert due(condition_met=False) == (False, "condition_pending")
    assert due(executor_paused=True) == (False, "executor_paused")
    assert due({**base, "blocked": "condition_expired"}) == (False, "blocked:condition_expired")
    assert due({**base, "resume_after": z(moment(hours=1))}) == (False, "not_before")
    assert due({**base, "resume_after": z(moment(minutes=1))}) == (True, "condition_met")
    assert due({k: v for k, v in base.items() if k != "condition"}) == (False, "no_condition")
    # a hold of a usage limit also needs its own time: the condition is not enough
    limit_hold = {**base, "reason": "plan_limit", "resume_after": z(moment(hours=1))}
    assert due(limit_hold) == (False, "not_due")
    assert due(limit_hold, now=moment(hours=1)) == (True, "reset_reached")
    assert due(limit_hold, condition_met=False, now=moment(hours=1)) == (False, "condition_pending")
    # hold_due, the check of resume_due, does not release a hold with a condition on its own
    assert hold_module.hold_due(limit_hold, now=moment(hours=2), executor_paused=False) == (False, "has_condition")
    assert hold_module.hold_due(limit_hold, now=moment(hours=2), executor_paused=False, condition_met=True) == (
        True, "reset_reached")


def test_the_hold_of_a_wait_has_the_keys_of_a_limit_hold_without_the_limit_fields():
    condition = block(dependency_spec("T1"))
    hold = waits.build_wait_hold(condition, reason="dependency", now=NOW, hold_id="h1", executor_id="fake_cli",
                                 actor="executor:fake_cli", session_id="s1", attempt=2,
                                 settings={"model": "m", "effort": None, "permission": "write", "adapter": "a"},
                                 resume_after=moment(hours=1))
    assert hold["id"] == "h1" and hold["reason"] == "dependency" and hold["outcome"] == "wait_condition"
    assert hold["resume_after"] == z(moment(hours=1)) and hold["open_ended"] is False
    assert hold["settings"] == {"model": "m", "permission": "write", "adapter": "a"}
    assert {"window", "reset_at", "reset_source", "reset_confidence"}.isdisjoint(hold)
    assert hold["condition"] == condition and hold["pause_seq"] is None and hold["decision"] == "pending"
    assert hold_module.is_condition_hold(hold) and not hold_module.is_limit_hold(hold)
    assert hold_module.condition_of({"reason": "plan_limit"}) is None


def test_the_earliest_a_held_task_can_restart_is_a_floor_made_of_its_own_times():
    assert waits.not_before_of_hold({"reason": "plan_limit", "resume_after": z(moment(hours=3))}) == moment(hours=3)
    timed = block(all_of(time_spec(moment(hours=5)), dependency_spec("T1")), deadline=timedelta(hours=9))
    hold = {"reason": "dependency", "resume_after": z(moment(hours=2)), "condition": timed}
    assert waits.not_before_of_hold(hold) == moment(hours=5)
    assert waits.not_before_of_hold({"reason": "approval", "condition": block(approval_spec())}) is None
    met_time = waits.apply_evaluation(timed, waits.evaluate(timed, make_facts(moment(hours=6), task_states={"T1": "running"})))
    assert waits.not_before_of_hold({"reason": "dependency", "condition": met_time}) is None     # the time has come
    assert waits.not_before_of_hold({"reason": "dependency", "condition": {"type": "all"}}) is None


# =========================================================================== events

def test_the_events_of_a_wait_are_small_and_carry_no_parameters():
    parts = [dependency_spec(*[f"TASK-{'x' * 100}-{n}" for n in range(8)]), integration_spec("window-" + "w" * 80),
             approval_spec("approval-" + "a" * 80, approver="owner"), time_spec(moment(hours=1))]
    condition = block(all_of(*parts), deadline=timedelta(hours=9))
    hold = {"id": "h1", "reason": "dependency", "executor_id": "fake_cli", "attempt": 3, "condition": condition}
    states = {f"TASK-{'x' * 100}-{n}": "running" for n in range(8)}
    waiting = waits.evaluate(condition, make_facts(moment(hours=3), task_states=states,
                                                   approvals={"approval-" + "a" * 80: ApprovalFact("rejected", "owner")}))
    expired_payload = waits.expired_payload(hold, waiting)
    assert len(json.dumps(expired_payload).encode()) <= 2048
    assert expired_payload["reason"] == "dependency" and expired_payload["hold_id"] == "h1"
    assert expired_payload["blocked"] == "approval_rejected" and expired_payload["waited_minutes"] == 180
    assert "params" not in json.dumps(expired_payload) and "owner" not in json.dumps(expired_payload["parts"][:1])

    done = waits.evaluate(block(dependency_spec("T1")), make_facts(moment(minutes=7), task_states={"T1": "accepted"}))
    met = waits.met_payload({"id": "h2", "reason": "dependency", "executor_id": "e", "attempt": 1,
                             "condition": block(dependency_spec("T1"))}, done)
    assert met["met_at"] == z(moment(minutes=7)) and met["waited_minutes"] == 7 and met["type"] == "dependency"
    assert met["parts"] == [{"type": "dependency", "met": True, "detail": "satisfied", "met_at": z(moment(minutes=7))}]
    assert len(json.dumps(met).encode()) <= 2048


def test_the_core_of_the_waits_reads_no_file_no_process_no_clock_and_no_model():
    source = (Path(waits.__file__)).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    assert imported <= {"__future__", "json", "math", "re", "collections.abc", "dataclasses", "datetime", "typing",
                        "..executors.limits", "..tasks.graph", ".hold", "..tasks"}, imported
    assert "datetime.now" not in source and "utcnow" not in source and "time.time" not in source
    assert "open(" not in source.replace("is_open(", "")


# =========================================================================== the service around it

@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)


def finish_task(world, task_id: str = "T3", agent: str = "agent:a") -> None:
    tasks = world.services.tasks
    tasks.transition(task_id, "claim", actor=agent)
    tasks.transition(task_id, "start", actor=agent)
    tasks.transition(task_id, "submit", actor=agent)
    tasks.transition(task_id, "accept", actor="user")


def paused_t1(world) -> dict:
    """T1 ran once and ended without a result: paused, owned by the executor, with its session recorded."""
    world.fake.script.append({"fail": True})
    out = world.run()
    assert out["outcome"] == "failed" and world.services.tasks.get_task("T1")["state"] == "paused"
    return out


def hold_t1(world, spec: dict | None = None, **options) -> dict:
    spec = spec or dependency_spec("T3")
    return world.services.executors.hold_on_condition("T1", spec, **options)


def the_hold(world, task_id: str = "T1") -> dict:
    return world.hold(task_id)


def count(world, event_type: str, task_id: str = "T1") -> int:
    return len(events(world.services, event_type, task_id))


def assert_small(world) -> None:
    for event in world.services.tasks.events(limit=500):
        assert len(json.dumps(event["payload"]).encode()) <= 2048, event["type"]


# ----------------------------------------------------------------------- holding

def test_a_paused_task_is_held_on_a_condition_with_its_session_and_a_deadline(world):
    first = paused_t1(world)
    held = hold_t1(world, reason="dependency")

    hold = the_hold(world)
    assert held["hold"] == hold and held["task"]["state"] == "paused"
    assert hold["reason"] == "dependency" and hold["outcome"] == "wait_condition"
    assert (hold["executor_id"], hold["actor"], hold["session_id"], hold["attempt"]) == (
        "fake_cli", "executor:fake_cli", first["session_id"], 1)
    assert hold["resume_after"] is None and hold["open_ended"] is False and hold["checkpoint"] is None
    assert hold["settings"]["permission"] == "write" and hold["settings"]["adapter"] == "fake_hold"
    assert len(hold["id"]) == 16 and str(world.workdir) not in json.dumps(hold)
    condition = hold["condition"]
    assert condition["type"] == "dependency" and condition["params"] == {"task_ids": ["T3"], "requires": "accepted"}
    assert (condition["since"], condition["deadline_at"], condition["met_at"]) == (
        "2026-10-11T12:00:00Z", "2026-10-12T00:00:00Z", None)
    assert world.continuity()["chain"] == ["fake_cli"] and world.continuity()["handoffs"] == 0

    [event] = events(world.services, "continuity.held", "T1")
    assert event["payload"]["reason"] == "dependency" and event["payload"]["hold_id"] == hold["id"]
    assert event["payload"]["outcome"] == "wait_condition" and event["payload"]["condition"]["parts"] == ["dependency"]
    assert event["ref"] == "fake_cli"
    assert_small(world)


def test_a_running_task_is_paused_with_its_hold_in_one_update(world):
    tasks = world.services.tasks
    tasks.transition("T2", "claim", actor="agent:a")
    tasks.transition("T2", "start", actor="agent:a")

    held = world.services.executors.hold_on_condition("T2", approval_spec("ship", approver="owner"),
                                                      executor_id="fake_cli", deadline_minutes=90, watcher_id="watch-7")
    hold = the_hold(world, "T2")
    assert held["task"]["state"] == "paused" and held["task"]["assignee"] == "agent:a"
    assert hold["reason"] == "approval" and hold["actor"] == "agent:a" and hold["session_id"] is None
    assert hold["condition"]["deadline_at"] == "2026-10-11T13:30:00Z" and hold["condition"]["watcher_id"] == "watch-7"
    assert count(world, "task.paused", "T2") == 1


def test_a_task_that_cannot_wait_or_a_condition_that_is_wrong_is_refused(world):
    executors = world.services.executors
    refused(lambda: executors.hold_on_condition("T2", dependency_spec("T3"), executor_id="fake_cli"),
            reason="task_not_holdable")                                     # T2 is only open
    paused_t1(world)
    refused(lambda: hold_t1(world, {"type": "script", "params": {}}), code="invalid_request")
    refused(lambda: hold_t1(world, time_spec(at(hours=1))), code="invalid_request")          # a time alone is no wait
    refused(lambda: hold_t1(world, reason="approval"), code="invalid_request")
    refused(lambda: hold_t1(world, deadline_minutes=0), code="invalid_request")
    refused(lambda: hold_t1(world, resume_after="whenever"), code="invalid_request")
    assert the_hold(world) is None
    hold_t1(world)
    refused(lambda: hold_t1(world), reason="already_held")
    tasks = world.services.tasks
    tasks.transition("T2", "claim", actor="agent:a")
    tasks.transition("T2", "start", actor="agent:a")
    refused(lambda: executors.hold_on_condition("T2", dependency_spec("T3")), code="invalid_request")   # no executor known
    refused(lambda: executors.hold_on_condition("T2", dependency_spec("T3"), executor_id="nope"), code="not_found")


# ----------------------------------------------------------------------- checking

def test_checking_a_wait_reports_the_honest_progress_and_writes_nothing(world):
    paused_t1(world)
    hold_t1(world, all_of(time_spec(at(hours=4)), dependency_spec("T3")))
    revision = world.services.tasks.get_task("T1")["revision"]
    world.clock.advance(hours=2)

    out = world.services.executors.check_waits()

    assert out["held"] == 1 and out["dry_run"] is False
    [wait] = out["waits"]
    assert (wait["task_id"], wait["state"], wait["due"], wait["why"]) == ("T1", "waiting", False, "condition_pending")
    progress = wait["evaluation"]["progress"]
    assert (progress["percent"], progress["eta"], progress["basis"]) == (None, None, "depends_on_agent")   # the unknown dominates
    assert [part["type"] for part in wait["evaluation"]["parts"]] == ["time", "dependency"]
    assert wait["evaluation"]["parts"][0]["progress"]["percent"] == 50                        # the time alone is exact
    assert wait["evaluation"]["next_check_at"] == "2026-10-11T14:02:00Z"
    assert world.services.tasks.get_task("T1")["revision"] == revision              # a check is not a write
    assert count(world, "continuity.condition_met") == 0


def test_a_condition_that_holds_is_recorded_once_with_its_event(world):
    paused_t1(world)
    hold_t1(world)
    finish_task(world)
    world.clock.advance(minutes=7)
    executors = world.services.executors

    [wait] = executors.check_waits(watcher_id="watch-1", dry_run=True)["waits"]
    assert wait["state"] == "met" and wait["due"] is True and wait["why"] == "condition_met"
    assert the_hold(world)["condition"]["met_at"] is None and count(world, "continuity.condition_met") == 0   # dry run

    [wait] = executors.check_waits(watcher_id="watch-1")["waits"]
    assert wait["state"] == "met"
    condition = the_hold(world)["condition"]
    assert condition["met_at"] == "2026-10-11T12:07:00Z" and condition["watcher_id"] == "watch-1"
    [event] = events(world.services, "continuity.condition_met", "T1")
    assert event["actor"] == "system:watcher" and event["ref"] == "fake_cli"
    assert event["payload"]["met_at"] == "2026-10-11T12:07:00Z" and event["payload"]["waited_minutes"] == 7
    assert event["payload"]["reason"] == "dependency" and event["payload"]["hold_id"] == the_hold(world)["id"]

    world.clock.advance(minutes=3)
    executors.check_waits()
    assert count(world, "continuity.condition_met") == 1                    # once, not once per check
    assert the_hold(world)["condition"]["met_at"] == "2026-10-11T12:07:00Z"
    assert_small(world)


def test_a_condition_that_stops_holding_loses_its_met_at(world):
    paused_t1(world)
    hold_t1(world)
    finish_task(world)
    executors = world.services.executors
    executors.check_waits()
    assert the_hold(world)["condition"]["met_at"] is not None

    world.services.tasks.transition("T3", "reopen", actor="user", expected_revision=world.services.tasks.get_task("T3")["revision"],
                                    cause="more work")
    [wait] = executors.check_waits()["waits"]
    assert wait["state"] == "waiting" and wait["evaluation"]["revoked"] is True
    assert the_hold(world)["condition"]["met_at"] is None
    assert executors.resume_met()["resumed"] == [] and world.fake.calls[-1].kind == "launch"


def test_recording_the_last_check_is_optional_because_it_costs_a_write_every_time(world):
    paused_t1(world)
    hold_t1(world, all_of(time_spec(at(hours=4)), dependency_spec("T3")))
    revision = world.services.tasks.get_task("T1")["revision"]
    world.clock.advance(hours=1)
    world.services.executors.check_waits(record=True)

    condition = the_hold(world)["condition"]
    assert condition["checked_at"] == "2026-10-11T13:00:00Z" and condition["next_check_at"] == "2026-10-11T13:02:00Z"
    assert condition["estimate"]["basis"] == "depends_on_agent" and condition["estimate"]["percent"] is None
    assert world.services.tasks.get_task("T1")["revision"] == revision + 1


# ----------------------------------------------------------------------- resuming

def test_resume_met_resumes_the_session_when_the_dependency_is_accepted(world):
    first = paused_t1(world)
    hold_t1(world)
    executors = world.services.executors

    nothing = executors.resume_met()
    assert nothing["held"] == 1 and nothing["resumed"] == [] and nothing["due"] == []
    assert nothing["waiting"][0]["why"] == "condition_pending" and len(world.fake.calls) == 1

    finish_task(world)
    world.clock.advance(minutes=5)
    out = executors.resume_met()

    assert [item["task_id"] for item in out["due"]] == ["T1"] and out["expired"] == [] and out["skipped"] == []
    [resumed] = out["resumed"]
    assert (resumed["mode"], resumed["outcome"], resumed["session_id"]) == ("resume", "submitted", first["session_id"])
    assert world.fake.calls[-1].kind == "resume" and world.fake.calls[-1].session_id == first["session_id"]
    assert the_hold(world) is None and world.services.tasks.get_task("T1")["state"] == "submitted"
    assert count(world, "continuity.condition_met") == 1
    [event] = events(world.services, "continuity.resumed", "T1")
    assert event["payload"]["trigger"] == "condition" and event["payload"]["reason"] == "dependency"
    assert event["payload"]["mode"] == "resume" and event["payload"]["minutes"] == 5
    assert executors.resume_met()["held"] == 0
    assert_small(world)


def test_resume_met_checks_the_condition_again_and_does_not_trust_a_stale_record(world):
    paused_t1(world)
    hold_t1(world)
    finish_task(world)
    executors = world.services.executors
    executors.check_waits()                                         # the record says: met
    assert the_hold(world)["condition"]["met_at"] is not None
    world.services.tasks.transition("T3", "reopen", actor="user", expected_revision=world.services.tasks.get_task("T3")["revision"],
                                    cause="more work")

    out = executors.resume_met()                                    # ... but it no longer holds when resuming

    assert out["resumed"] == [] and out["waiting"][0]["why"] == "condition_pending"
    assert len(world.fake.calls) == 1 and the_hold(world) is not None


def test_resume_met_revalidates_the_hold_and_the_attempt_before_resuming(world):
    paused_t1(world)
    hold_t1(world)
    finish_task(world)
    executors = world.services.executors

    wrong_hold = executors.resume_met(hold_id="not-this-one")
    assert [item["why"] for item in wrong_hold["skipped"]] == ["hold_changed"] and wrong_hold["resumed"] == []
    wrong_attempt = executors.resume_met(attempt=7)
    assert [item["why"] for item in wrong_attempt["skipped"]] == ["attempt_changed"]
    assert len(world.fake.calls) == 1 and the_hold(world) is not None

    edited = dict(the_hold(world), attempt=9)                        # a hold that belongs to another attempt
    world.services.tasks._edit_continuity("T1", {"hold": edited}, actor="test")
    assert [item["why"] for item in executors.resume_met()["skipped"]] == ["attempt_changed"]
    assert len(world.fake.calls) == 1

    world.services.tasks._edit_continuity("T1", {"hold": dict(edited, attempt=1)}, actor="test")
    hold_id = the_hold(world)["id"]
    out = executors.resume_met(hold_id=hold_id, attempt=1)
    assert len(out["resumed"]) == 1 and out["resumed"][0]["hold_id"] == hold_id
    assert world.fake.calls[-1].kind == "resume"


def test_the_resume_itself_refuses_a_hold_whose_condition_was_not_recorded_as_met(world):
    paused_t1(world)
    hold_t1(world)
    finish_task(world)                                  # it holds, but nobody recorded it: the last guard stops the resume
    executors = world.services.executors
    seen = the_hold(world)

    refused(lambda: executors._resume_one({"task_id": "T1"}, seen, None, trigger="condition"), reason="condition_not_met")
    refused(lambda: executors._resume_one({"task_id": "T1"}, dict(seen, since="2020-01-01T00:00:00Z"), None,
                                          trigger="condition"), reason="hold_changed")
    assert len(world.fake.calls) == 1 and the_hold(world) is not None


def test_resume_met_does_nothing_in_a_dry_run_and_respects_the_limit(world):
    paused_t1(world)
    hold_t1(world)
    finish_task(world)
    executors = world.services.executors

    dry = executors.resume_met(dry_run=True)
    assert [item["task_id"] for item in dry["due"]] == ["T1"] and dry["resumed"] == []
    assert the_hold(world)["condition"]["met_at"] is None and len(world.fake.calls) == 1
    capped = executors.resume_met(limit=1)
    assert len(capped["resumed"]) == 1


def test_resume_due_leaves_the_holds_with_a_condition_to_resume_met(world):
    paused_t1(world)
    hold_t1(world)
    finish_task(world)
    world.clock.advance(days=1)

    out = world.services.executors.resume_due()

    assert out["resumed"] == [] and [(item["task_id"], item["why"]) for item in out["skipped"]] == [("T1", "has_condition")]
    assert len(world.fake.calls) == 1


def test_a_task_resumed_by_hand_clears_a_condition_hold_like_any_other(world):
    paused_t1(world)
    hold_t1(world)

    world.services.executors.resume_task("T1")

    assert the_hold(world) is None
    [event] = events(world.services, "continuity.resumed", "T1")
    assert event["payload"]["trigger"] == "manual" and event["payload"]["reason"] == "dependency"


def test_without_a_resumable_session_the_task_is_relaunched_from_a_checkpoint_that_is_not_a_limit(tmp_path):
    world = make_world(tmp_path, resume=False)
    first = paused_t1(world)
    hold_t1(world)
    finish_task(world)

    out = world.services.executors.resume_met()

    [resumed] = out["resumed"]
    assert resumed["mode"] == "relaunch" and resumed["outcome"] == "submitted"
    assert world.fake.calls[-1].kind == "launch" and world.fake.calls[-1].session_id != first["session_id"]
    assert events(world.services, "continuity.limit_detected", "T1") == []          # a dependency is not a limit
    [checkpoint] = events(world.services, "task.checkpointed", "T1")
    assert "limit_kind" not in checkpoint["payload"] and checkpoint["payload"]["session_id"] == first["session_id"]
    [retired] = events(world.services, "session.retired")
    assert retired["ref"] == first["session_id"] and retired["payload"]["reason"] == "superseded"
    assert events(world.services, "continuity.resumed", "T1")[0]["payload"]["mode"] == "relaunch"


def test_a_minimum_time_holds_the_task_back_even_when_the_condition_already_holds(world):
    paused_t1(world)
    hold_t1(world, resume_after=at(hours=1))
    finish_task(world)
    executors = world.services.executors

    early = executors.resume_met()
    assert early["resumed"] == [] and early["waiting"][0]["why"] == "not_before"
    assert early["waiting"][0]["state"] == "met"                     # the condition holds; only the minimum time is missing
    assert count(world, "continuity.condition_met") == 1

    world.clock.advance(hours=1)
    assert len(executors.resume_met()["resumed"]) == 1


def test_a_wait_on_several_conditions_resumes_only_when_all_of_them_hold(world):
    paused_t1(world)
    hold_t1(world, all_of(dependency_spec("T3"), time_spec(at(hours=2))))
    finish_task(world)
    executors = world.services.executors

    world.clock.advance(hours=1)
    [wait] = executors.check_waits(task_id="T1")["waits"]
    parts = {part["type"]: part for part in wait["evaluation"]["parts"]}
    assert wait["state"] == "waiting" and parts["dependency"]["met"] and not parts["time"]["met"]
    assert wait["evaluation"]["progress"]["percent"] == 50 and wait["evaluation"]["progress"]["kind"] == "exact"
    assert executors.resume_met()["resumed"] == []
    assert count(world, "continuity.condition_met") == 0

    world.clock.advance(hours=1)
    out = executors.resume_met()
    assert len(out["resumed"]) == 1
    [met] = events(world.services, "continuity.condition_met", "T1")
    assert [part["type"] for part in met["payload"]["parts"]] == ["dependency", "time"]


# ----------------------------------------------------------------------- the deadline

def test_a_wait_that_passes_its_deadline_expires_once_and_is_never_resumed(world):
    paused_t1(world)
    hold_t1(world, deadline_minutes=60)
    executors = world.services.executors
    world.clock.advance(minutes=61)

    out = executors.resume_met()

    assert [item["state"] for item in out["expired"]] == ["expired"] and out["resumed"] == []
    hold = the_hold(world)
    assert hold["blocked"] == "condition_expired" and hold["condition"]["expired_at"] == "2026-10-11T13:01:00Z"
    assert world.services.tasks.get_task("T1")["state"] == "paused"
    [event] = events(world.services, "continuity.condition_expired", "T1")
    assert event["actor"] == "system:watcher" and event["payload"]["deadline_at"] == "2026-10-11T13:00:00Z"
    assert event["payload"]["expired_at"] == "2026-10-11T13:01:00Z" and event["payload"]["waited_minutes"] == 61

    again = executors.check_waits()["waits"][0]                      # it is already given up: nothing more is written
    assert (again["state"], again["why"]) == ("expired", "blocked:condition_expired")
    executors.resume_met()
    assert count(world, "continuity.condition_expired") == 1 and len(world.fake.calls) == 1
    finish_task(world)
    assert executors.resume_met()["resumed"] == []                  # even if the condition holds later
    assert_small(world)


def test_a_condition_met_at_the_deadline_is_met_not_expired(world):
    paused_t1(world)
    hold_t1(world, deadline_minutes=60)
    finish_task(world)
    world.clock.advance(minutes=60)

    out = world.services.executors.resume_met()

    assert out["expired"] == [] and len(out["resumed"]) == 1


# ----------------------------------------------------------------------- integration window

def test_an_integration_wait_follows_the_two_phases_and_resumes_when_the_window_closes(world):
    paused_t1(world)
    state = {"window": IntegrationFacts(durations=DURATIONS)}
    calls = []
    world.services.executors.integration_source = lambda window, now: calls.append(window) or state["window"]
    hold_t1(world, integration_spec("round"), deadline_minutes=240)
    executors = world.services.executors

    [wait] = executors.check_waits()["waits"]
    assert wait["evaluation"]["progress"]["percent"] is None and wait["evaluation"]["progress"]["basis"] == "integration_not_started"
    assert wait["evaluation"]["parts"][0]["detail"] == "not_started" and calls == ["round"]

    state["window"] = IntegrationFacts(started_at=at(minutes=1), durations=DURATIONS)
    world.clock.advance(minutes=7)
    [wait] = executors.check_waits()["waits"]
    progress = wait["evaluation"]["progress"]
    assert (progress["percent"], progress["kind"], progress["eta"]) == (50, "approximate", "2026-10-11T12:13:00Z")
    assert wait["state"] == "waiting" and executors.resume_met()["resumed"] == []

    world.clock.advance(minutes=20)
    [wait] = executors.check_waits()["waits"]
    assert wait["evaluation"]["progress"]["percent"] == 95 and wait["evaluation"]["progress"]["frozen"] is True

    state["window"] = IntegrationFacts(started_at=at(minutes=1), ended_at=at(minutes=30), outcome="ok", durations=DURATIONS)
    out = executors.resume_met()
    assert len(out["resumed"]) == 1 and out["resumed"][0]["outcome"] == "submitted"
    [met] = events(world.services, "continuity.condition_met", "T1")
    assert met["payload"]["reason"] == "integration_window" and met["payload"]["type"] == "integration_window"


def test_a_broken_reader_is_no_evidence_and_does_not_stop_the_check(world):
    paused_t1(world)

    def broken(window, now):
        raise RuntimeError("the report folder is gone")

    world.services.executors.integration_source = broken
    hold_t1(world, integration_spec("round"))

    [wait] = world.services.executors.check_waits()["waits"]

    assert wait["state"] == "waiting" and wait["evaluation"]["parts"][0]["detail"] == "no_evidence"


def test_without_a_reader_an_integration_is_never_assumed_to_be_over(world):
    paused_t1(world)
    hold_t1(world, integration_spec("round"))
    world.clock.advance(hours=1)

    assert world.services.executors.resume_met()["resumed"] == []


# ----------------------------------------------------------------------- approval

def test_an_approval_is_recorded_by_a_person_and_never_by_a_program(world):
    paused_t1(world)
    hold_t1(world, approval_spec("ship", approver="owner"))
    executors = world.services.executors

    refused(lambda: executors.record_approval("T1", "ship", approved=True, by="system:watcher"), reason="approver_not_allowed")
    refused(lambda: executors.record_approval("T1", "ship", approved=True, by="executor:fake_cli"), reason="approver_not_allowed")
    refused(lambda: executors.record_approval("T1", "ship", approved=True, by="someone"), reason="approver_mismatch")
    refused(lambda: executors.record_approval("T1", "other", approved=True, by="owner"), reason="not_waiting_for_approval")
    refused(lambda: executors.record_approval("T2", "ship", approved=True, by="owner"), reason="not_waiting_for_approval")
    refused(lambda: executors.record_approval("T1", "ship", approved="yes", by="owner"), code="invalid_request")
    assert executors.resume_met()["resumed"] == []

    done = executors.record_approval("T1", "ship", approved=True, by="owner")
    assert (done["status"], done["created"], done["by"]) == ("approved", True, "owner")
    again = executors.record_approval("T1", "ship", approved=True, by="owner")
    assert again["created"] is False
    refused(lambda: executors.record_approval("T1", "ship", approved=False, by="owner"), reason="already_decided")
    assert the_hold(world)["condition"]["decisions"]["ship"]["by"] == "owner"

    out = executors.resume_met()
    assert len(out["resumed"]) == 1
    [met] = events(world.services, "continuity.condition_met", "T1")
    assert met["payload"]["reason"] == "approval" and met["payload"]["parts"][0]["by"] == "owner"


def test_a_rejection_keeps_the_task_waiting_and_is_reported_until_the_deadline(world):
    paused_t1(world)
    hold_t1(world, approval_spec("ship"), deadline_minutes=30)
    executors = world.services.executors
    executors.record_approval("T1", "ship", approved=False, by="owner")

    [wait] = executors.check_waits()["waits"]

    assert wait["state"] == "waiting" and wait["evaluation"]["blocked"] == "approval_rejected"
    assert executors.resume_met()["resumed"] == []
    world.clock.advance(minutes=31)
    assert executors.resume_met()["expired"][0]["state"] == "expired"


def test_an_approval_can_also_come_from_the_reader_of_a_watcher(world):
    paused_t1(world)
    hold_t1(world, approval_spec("ship"))
    seen = []
    world.services.executors.approval_source = lambda task_id, approval: (
        seen.append((task_id, approval)) or ApprovalFact("approved", "owner", at()))

    out = world.services.executors.resume_met()

    assert len(out["resumed"]) == 1 and seen == [("T1", "ship")]


# ----------------------------------------------------------------------- a usage limit with a condition

def test_a_limit_hold_that_gets_a_condition_needs_the_reset_and_the_condition(world):
    world.fake.script.append({"limit": limit(reset_at=at(2))})
    world.run()
    plain = the_hold(world)
    assert plain["reason"] == "plan_limit" and plain["resume_after"] == "2026-10-11T14:02:00Z"
    executors = world.services.executors

    held = hold_t1(world, dependency_spec("T3"), deadline_minutes=300)
    hold = the_hold(world)
    assert held["hold"]["reason"] == "plan_limit" and hold["reset_at"] == plain["reset_at"]
    assert hold["resume_after"] == plain["resume_after"] and len(hold["id"]) == 16
    assert hold["condition"]["type"] == "dependency"
    refused(lambda: hold_t1(world), reason="already_held")

    finish_task(world)
    world.clock.to(at(1))                                            # the condition holds but the limit has not reset
    early = executors.resume_met()
    assert early["resumed"] == [] and early["waiting"][0]["why"] in ("executor_paused", "not_due")
    assert executors.check_waits()["waits"][0]["state"] == "met"     # recorded all the same

    world.clock.to(at(2, 3))                                         # the limit has reset
    assert executors.resume_due()["resumed"] == []                   # resume_due leaves it
    out = executors.resume_met()
    assert len(out["resumed"]) == 1 and out["resumed"][0]["outcome"] == "submitted"
    assert the_hold(world) is None


def test_a_deadline_before_the_limit_resets_is_refused(world):
    world.fake.script.append({"limit": limit(reset_at=at(4))})
    world.run()

    refused(lambda: hold_t1(world, dependency_spec("T3"), deadline_minutes=60), code="invalid_request")

    assert "condition" not in the_hold(world)


# ----------------------------------------------------------------------- one world, several waits

def test_several_waits_are_checked_and_resumed_one_by_one(world):
    paused_t1(world)
    hold_t1(world, dependency_spec("T3"))
    tasks = world.services.tasks
    tasks.transition("T2", "claim", actor="agent:a")
    tasks.transition("T2", "start", actor="agent:a")
    world.services.executors.hold_on_condition("T2", dependency_spec("T3"), executor_id="fake_cli")
    finish_task(world)

    out = world.services.executors.resume_met()

    assert out["held"] == 2 and sorted(item["task_id"] for item in out["resumed"]) == ["T1", "T2"]
    assert {item["mode"] for item in out["resumed"]} == {"resume", "relaunch"}       # T2 never had a session
    assert count(world, "continuity.condition_met", "T1") == count(world, "continuity.condition_met", "T2") == 1
    assert_small(world)


def test_the_checks_validate_their_arguments(world):
    executors = world.services.executors
    refused(lambda: executors.check_waits(task_id="bad id"), code="invalid_request")
    refused(lambda: executors.check_waits(watcher_id="bad id"), code="invalid_request")
    refused(lambda: executors.resume_met(limit=0), code="invalid_request")
    refused(lambda: executors.resume_met(attempt=0), code="invalid_request")
    refused(lambda: executors.record_approval("T1", "bad id", approved=True, by="owner"), code="invalid_request")
    assert executors.check_waits() == {"at": "2026-10-11T12:00:00+00:00", "dry_run": False, "held": 0, "waits": []}
    assert executors.resume_met()["held"] == 0
