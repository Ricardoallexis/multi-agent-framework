# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""AVA-T03: the person's inbox and its guard, offline and synthetic (no real model, CLI or provider).

Layers:

* the pure part (``collect_messages``, ``pending_messages``) and ``UserInboxGuard`` on its own and inside the call-guard chain;
* posting (``InboxServices.post``): append only, idempotent by ``message_id``, ``seq`` without gaps, races between threads;
* the engine end to end (scripted provider): ``cancel`` ends the run as cancelled, ``interrupt`` and ``adjust`` hold it for
  the person, ``note`` goes on, and the inbox is read only by the worker thread;
* tasks (fake programs of the executor tests): a background attempt reads the inbox at the tick of its process watch;
* the hand-off primitives (``cursor``, ``messages(after=...)``, ``require_no_cancel``) and a session that was retired;
* the HTTP routes.

Nothing in the inbox approves: the tests check that no approval, in a run or in a task, comes from it.
"""
from __future__ import annotations

import ast
import itertools
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent import call_guards
from multiagent.api import create_app
from multiagent.call_guards import (
    ORDER_CONTEXT, ORDER_INBOX, ORDER_LIMITS, PROCEED, STOP_USER_CANCEL, CallContext, CallGuardChain, GuardEnvironment,
    Pause, Stop, build_call_guards,
)
from multiagent.catalog import ModelSpec
from multiagent.config import Settings
from multiagent.contracts import RunStatus, WaitingReason
from multiagent.errors import InvalidStateTransition
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services import inbox as inbox_module
from multiagent.services.availability import USER_MESSAGE_EVENT, AvailabilityPolicy
from multiagent.services.errors import NOT_FOUND
from multiagent.services.inbox import (
    APPLIED_EVENT, CONSUMED_EVENT, INBOX_ACTOR, MAX_TEXT_BYTES, InboxServices, UserInboxGuard, collect_messages,
    pending_messages,
)
from multiagent.worker import LocalWorker
from test_background_runs import LONG, background, finished_wait, patient, policy  # noqa: F401  (fixtures by name)
from test_context_compaction import STEPS, filler, runtime, start
from test_quota_hold import at as hours_from_now, hold_at_the_reset, make_world as make_quota_world
from test_executor_services import (  # noqa: F401  (the fixtures are used by name)
    RESULT, attempt_events, claude_out, event_types, never_a_real_cli, refused, world,
)

ANA = "user:ana"
_AUTO_IDS = itertools.count(1)          # message ids for tests that do not care (the Windows clock is too coarse for ids)
BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
GENERIC = {"project_name": "Synthetic review", "workflow_id": "spec_review",
           "inputs": {"spec_text": "Users can reset a password by email."}}


def until(check, timeout: float = 20.0, what: str = "the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError(f"timed out waiting for {what}")


# =============================================================================================================
# Records, the pure part
# =============================================================================================================

def record(seq, kind="note", text="x", message_id=None, actor=ANA, **extra) -> dict:
    return {"type": USER_MESSAGE_EVENT, "actor": actor, "at": f"t{seq}",
            "payload": {"message_id": message_id or f"m{seq}", "seq": seq, "kind": kind, "priority": 3, "text": text, **extra}}


def marker(message_id, action="noted", seq=1, kind="note") -> dict:
    return {"type": CONSUMED_EVENT, "actor": INBOX_ACTOR, "at": "tc",
            "payload": {"message_id": message_id, "seq": seq, "kind": kind, "action": action}}


def test_messages_come_in_arrival_order_with_what_was_read():
    messages = collect_messages([record(2, "cancel"), record(1, "note"), marker("m1", "noted", 1, "note")])
    assert [(m["seq"], m["message_id"], m["state"]) for m in messages] == [(1, "m1", "consumed"), (2, "m2", "pending")]
    assert messages[0]["consumed"]["action"] == "noted" and messages[1]["consumed"] is None
    assert messages[0]["actor"] == ANA and messages[0]["text"] == "x"


def test_a_message_id_seen_twice_is_one_message_and_the_first_marker_counts():
    twice = [record(5, "note", message_id="same"), record(3, "note", message_id="same"),
             marker("same", "noted", 3), marker("same", "paused", 3)]
    (message,) = collect_messages(twice)
    assert message["seq"] == 3 and message["state"] == "consumed" and message["consumed"]["action"] == "noted"


@pytest.mark.parametrize("bad", [
    {"payload": {"message_id": "m1", "seq": 0, "kind": "note", "text": "x"}},            # seq starts at 1
    {"payload": {"message_id": "m1", "seq": True, "kind": "note", "text": "x"}},
    {"payload": {"message_id": "m1", "seq": 1, "kind": "approve", "text": "x"}},         # not a kind of this inbox
    {"payload": {"message_id": "m 1", "seq": 1, "kind": "note", "text": "x"}},
    {"payload": {"message_id": "m1", "seq": 1, "kind": "note", "text": None}},
    {"payload": "text"}, {"payload": None}, {},
])
def test_events_without_the_shape_of_a_message_are_skipped(bad):
    assert collect_messages([{"type": USER_MESSAGE_EVENT, "actor": ANA, "at": "t", **bad}]) == []


def test_unread_messages_are_ordered_by_the_policy_then_by_arrival():
    log = [record(1, "note"), record(2, "adjust"), record(3, "cancel"), record(4, "interrupt"), record(5, "cancel")]
    assert [m["message_id"] for m in pending_messages(collect_messages(log), AvailabilityPolicy())] == [
        "m3", "m5", "m4", "m2", "m1"]
    custom = AvailabilityPolicy(priorities=("cancel", "note", "adjust", "interrupt"))
    assert [m["message_id"] for m in pending_messages(collect_messages(log), custom)] == ["m3", "m5", "m1", "m2", "m4"]


def test_what_was_read_is_not_unread_again():
    log = [record(1, "cancel"), record(2, "note"), marker("m1", "cancelled", 1, "cancel")]
    assert [m["message_id"] for m in pending_messages(collect_messages(log))] == ["m2"]


# =============================================================================================================
# The guard on its own and in the chain
# =============================================================================================================

def make_call(step_id="b", attempt=1) -> CallContext:
    spec = ModelSpec(id="m", provider="lab", model="m", tier="local", structured_output=True, web=False)
    return CallContext(model_id="m", spec=spec, step_id=step_id, attempt=attempt)


def run_event(seq, kind="note", text="x", message_id=None) -> dict:
    return {"event_type": USER_MESSAGE_EVENT, "ts": f"t{seq}",
            "payload": {"message_id": message_id or f"m{seq}", "seq": seq, "kind": kind, "priority": 3, "text": text,
                        "actor": ANA}}


def state_of(*events) -> dict:
    return {"llm_calls": 0, "max_llm_calls": 10, "events": list(events)}


class Emitted(list):
    def __call__(self, event_type, payload):
        self.append((event_type, payload))


def guard_with(emitted: Emitted, **policy) -> UserInboxGuard:
    return UserInboxGuard(AvailabilityPolicy(enabled=True, **policy), emit=emitted)


def test_the_guard_is_the_first_of_the_chain_and_its_order_is_fixed():
    assert ORDER_INBOX == 50 < ORDER_CONTEXT < ORDER_LIMITS
    guard = guard_with(Emitted())
    assert (guard.name, guard.order, guard.observer, guard.skippable) == ("user_inbox", 50, True, False)


@pytest.mark.parametrize("availability, names", [
    (None, ("limits",)), (AvailabilityPolicy(), ("limits",)), (AvailabilityPolicy(enabled=True), ("user_inbox", "limits"))])
def test_the_chain_has_the_guard_only_with_an_enabled_policy(availability, names):
    chain = build_call_guards(GuardEnvironment(availability=availability, emit=Emitted()))
    assert chain.names == names


def test_an_empty_inbox_lets_the_call_through_and_writes_nothing():
    emitted = Emitted()
    assert guard_with(emitted).check(make_call(), state_of()) is PROCEED and emitted == []


def test_a_cancel_is_a_stop_that_is_not_a_budget_stop():
    emitted = Emitted()
    verdict = guard_with(emitted).check(make_call(), state_of(run_event(1, "cancel", "stop it")))
    assert isinstance(verdict, Stop) and verdict.code == STOP_USER_CANCEL == "user_cancel"
    assert verdict.details == {"message_id": "m1", "seq": 1}
    assert emitted == [(CONSUMED_EVENT, {"message_id": "m1", "seq": 1, "kind": "cancel", "action": "cancelled",
                                         "step_id": "b", "attempt": 1})]


@pytest.mark.parametrize("kind", ["interrupt", "adjust"])
def test_interrupt_and_adjust_pause_the_call_for_the_person(kind):
    emitted = Emitted()
    verdict = guard_with(emitted).check(make_call(), state_of(run_event(1, kind, "wait, look at this")))
    assert isinstance(verdict, Pause) and verdict.waiting_reason == WaitingReason.USER_MESSAGE.value == "user_message"
    assert verdict.request == {"message_id": "m1", "seq": 1, "kind": kind, "text": "wait, look at this"}
    assert emitted[0][1]["action"] == "paused" and emitted[0][1]["kind"] == kind


def test_a_note_is_recorded_and_the_call_goes_on():
    emitted = Emitted()
    assert guard_with(emitted).check(make_call(), state_of(run_event(1, "note", "FYI"))) is PROCEED
    assert [(kind, payload["message_id"], payload["action"]) for kind, payload in emitted] == [
        (CONSUMED_EVENT, "m1", "noted")]


def test_one_verdict_per_boundary_in_the_order_of_the_policy():
    emitted = Emitted()
    log = state_of(run_event(1, "note"), run_event(2, "interrupt"), run_event(3, "cancel"), run_event(4, "note"))
    assert isinstance(guard_with(emitted).check(make_call(), log), Stop)
    assert [payload["message_id"] for _, payload in emitted] == ["m3"]            # the cancel won; nothing else was read
    emitted = Emitted()
    log = state_of(run_event(1, "note"), run_event(2, "interrupt"), run_event(3, "note"))
    assert isinstance(guard_with(emitted).check(make_call(), log), Pause)
    assert [payload["message_id"] for _, payload in emitted] == ["m2"]            # the notes wait behind the interrupt
    emitted = Emitted()
    log = state_of(run_event(1, "note"), run_event(2, "note"))
    assert guard_with(emitted).check(make_call(), log) is PROCEED
    assert [payload["message_id"] for _, payload in emitted] == ["m1", "m2"]      # notes are all recorded in one boundary


def test_a_message_that_was_read_is_never_read_again():
    emitted = Emitted()
    first = state_of(run_event(1, "interrupt"))
    assert isinstance(guard_with(emitted).check(make_call(), first), Pause)
    (kind, payload), = emitted
    after = state_of(run_event(1, "interrupt"), {"event_type": kind, "payload": payload, "ts": "t"})
    assert guard_with(Emitted()).check(make_call(), after) is PROCEED


def test_a_duplicate_in_the_log_is_read_once():
    emitted = Emitted()
    twice = state_of(run_event(1, "cancel", message_id="same"), run_event(2, "cancel", message_id="same"))
    assert isinstance(guard_with(emitted).check(make_call(), twice), Stop)
    assert len(emitted) == 1 and emitted[0][1]["seq"] == 1


def test_without_a_way_to_write_the_marker_the_guard_reads_nothing():
    assert UserInboxGuard(AvailabilityPolicy(enabled=True)).check(make_call(), state_of(run_event(1, "cancel"))) is PROCEED


def test_the_guard_is_an_observer_a_bug_in_it_never_cuts_the_call():
    def broken(event_type, payload):
        raise RuntimeError("the log is down")

    chain = CallGuardChain([UserInboxGuard(AvailabilityPolicy(enabled=True), emit=broken)])
    assert chain.check(make_call(), state_of(run_event(1, "interrupt"))) is PROCEED


def test_the_chain_reads_the_inbox_before_the_other_guards():
    emitted = Emitted()
    chain = build_call_guards(GuardEnvironment(availability=AvailabilityPolicy(enabled=True), emit=emitted))
    exhausted = {"llm_calls": 10, "max_llm_calls": 10, "events": [run_event(1, "cancel")]}
    verdict = chain.check(make_call(), exhausted)
    assert isinstance(verdict, Stop) and verdict.guard == "user_inbox" and verdict.code == STOP_USER_CANCEL


def test_the_module_never_calls_an_approval_path():
    tree = ast.parse(Path(inbox_module.__file__).read_text(encoding="utf-8"))
    named = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    named |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    forbidden = {"record_approval", "approve", "reject", "regenerate", "changes", "finish_review", "finish_escalation",
                 "finish_content_review", "finish_user_message", "resume_met", "resume_due", "resume_task", "run_task",
                 "submit_result", "human_submit"}
    assert not forbidden & named, forbidden & named
    # What it does touch on a task: the lifecycle cancel, the checkpoint and the hold that waits for a person.
    assert {"transition", "checkpoint_limit", "hold_on_condition"} <= named


# =============================================================================================================
# Posting
# =============================================================================================================

def stub_inbox(system) -> InboxServices:
    """The inbox service over the store of a ``runtime`` world (run scope only)."""
    def find(run_id):
        try:
            system.store.get_run(run_id)
        except KeyError:
            raise ServiceError(NOT_FOUND, f"Run not found: {run_id}", status=404) from None
        return SimpleNamespace(store=system.store)

    facade = SimpleNamespace(runs=SimpleNamespace(_find=find), settings=system.settings, executors=None, tasks=None)
    return InboxServices(facade)


def inbox_world(tmp_path, *, enabled=True, **options):
    """A real engine over a temp store with the throwaway bundle, and its inbox; the policy on unless told otherwise."""
    system = runtime(tmp_path, assign=False, **options)
    system.settings.availability_enabled = enabled
    system.inbox = stub_inbox(system)
    return system


def run_log(system, run_id, *types) -> list[dict]:
    wanted = types or (USER_MESSAGE_EVENT, CONSUMED_EVENT)
    return [e for e in system.store.get_run(run_id)["events"] if e["event_type"] in wanted]


def post(system, run_id, kind="note", text="x", message_id=None, actor=ANA):
    return system.inbox.post("run", run_id, message_id=message_id or f"{kind}-{next(_AUTO_IDS)}", kind=kind, text=text,
                             actor=actor)


def test_with_the_policy_off_nothing_can_be_posted_and_nothing_is_written(tmp_path):
    system = inbox_world(tmp_path, enabled=False)
    run_id = start(system)
    caught = refused(lambda: post(system, run_id, "cancel"), reason="availability_disabled")
    assert caught.status == 409
    assert run_log(system, run_id) == []
    run = system.engine.process_run(run_id)
    assert run["status"] == RunStatus.COMPLETED.value and not run_log(system, run_id)


def test_a_posted_message_has_its_own_seq_a_priority_and_the_text_the_person_wrote(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    first = post(system, run_id, "note", "  prefer short titles  ", "n1")
    second = post(system, run_id, "interrupt", "", "i1")
    assert (first["seq"], first["kind"], first["priority"], first["text"], first["actor"]) == (
        1, "note", 3, "prefer short titles", ANA)
    assert (second["seq"], second["kind"], second["priority"], second["text"]) == (2, "interrupt", 1, "")
    assert first["created"] is True and first["state"] == "pending" and first["consumed"] is None
    assert [e["payload"]["seq"] for e in run_log(system, run_id)] == [1, 2]
    assert [m["message_id"] for m in system.inbox.messages("run", run_id)] == ["n1", "i1"]


def test_seq_is_per_run_and_never_has_gaps(tmp_path):
    system = inbox_world(tmp_path)
    one, other = start(system), start(system)
    for index in range(3):
        post(system, one, "note", "x", f"a{index}")
    assert post(system, other, "note", "x", "b0")["seq"] == 1
    assert [m["seq"] for m in system.inbox.messages("run", one)] == [1, 2, 3]
    assert system.inbox.cursor("run", one) == 3 and system.inbox.cursor("run", other) == 1


def test_the_same_message_id_is_one_message_however_often_it_is_sent(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    first = post(system, run_id, "adjust", "make it shorter", "same")
    again = post(system, run_id, "adjust", "  make it shorter ", "same")
    assert first["created"] is True and again["created"] is False
    assert (again["seq"], again["message_id"], again["text"]) == (first["seq"], "same", "make it shorter")
    assert len(run_log(system, run_id)) == 1
    conflict = refused(lambda: post(system, run_id, "adjust", "something else", "same"), reason="message_id_conflict")
    assert conflict.status == 409
    refused(lambda: post(system, run_id, "note", "make it shorter", "same"), reason="message_id_conflict")
    assert len(run_log(system, run_id)) == 1


def test_a_duplicate_that_gets_into_the_log_by_another_way_is_still_processed_once(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post(system, run_id, "cancel", "", "same")
    system.store.db.log_event(run_id, USER_MESSAGE_EVENT, {"message_id": "same", "seq": 2, "kind": "cancel", "priority": 0,
                                                           "text": "", "actor": ANA})          # a second writer, no dedupe
    assert len(run_log(system, run_id)) == 2
    assert [m["seq"] for m in system.inbox.messages("run", run_id)] == [1]
    run = system.engine.process_run(run_id)
    assert run["status"] == RunStatus.CANCELLED.value
    assert len([e for e in run_log(system, run_id, CONSUMED_EVENT)]) == 1


def test_many_threads_sending_the_same_message_write_it_once(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    results, start_gate = [], threading.Barrier(8)

    def send():
        start_gate.wait(10)
        results.append(system.inbox.post("run", run_id, message_id="race", kind="interrupt", text="now", actor=ANA))

    threads = [threading.Thread(target=send) for _ in range(8)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert len(results) == 8 and sum(1 for r in results if r["created"]) == 1
    assert {r["seq"] for r in results} == {1}
    assert len(run_log(system, run_id)) == 1


def test_many_threads_sending_different_messages_get_numbers_without_gaps_or_repeats(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    seqs, gate = [], threading.Barrier(10)

    def send(index):
        gate.wait(10)
        seqs.append(system.inbox.post("run", run_id, message_id=f"m{index}", kind="note", text=str(index), actor=ANA)["seq"])

    threads = [threading.Thread(target=send, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(seqs) == list(range(1, 11))
    assert [m["seq"] for m in system.inbox.messages("run", run_id)] == list(range(1, 11))


@pytest.mark.parametrize("field, value, code", [
    ("message_id", "", "message_id"), ("message_id", "has space", "message_id"), ("message_id", "x" * 65, "message_id"),
    ("message_id", "-start", "message_id"), ("message_id", 7, "message_id"), ("kind", "approve", "kind"),
    ("kind", "", "kind"), ("kind", None, "kind"), ("text", 5, "text"), ("text", "a\x00b", "text"),
    ("text", "é" * (MAX_TEXT_BYTES // 2 + 1), "text"), ("actor", "", "actor"), ("actor", "x" * 101, "actor"), ("actor", "Ana Lopez", "actor"), ("actor", "a/b", "actor"),
    ("actor", "ｓｙｓｔｅｍ：x", "actor"),
])
def test_a_message_that_is_not_valid_is_refused_and_nothing_is_written(tmp_path, field, value, code):
    system = inbox_world(tmp_path)
    run_id = start(system)
    arguments = {"message_id": "ok", "kind": "note", "text": "hi", "actor": ANA, field: value}
    with pytest.raises(ServiceError) as caught:
        system.inbox.post("run", run_id, **arguments)
    assert caught.value.code == "invalid_request" and caught.value.status == 422
    assert caught.value.details["field"] == code
    assert run_log(system, run_id) == []


def test_a_note_and_an_adjust_need_their_text_but_a_cancel_and_an_interrupt_do_not(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    for kind in ("note", "adjust"):
        with pytest.raises(ServiceError) as caught:
            post(system, run_id, kind, "  ")
        assert caught.value.details["field"] == "text"
    assert post(system, run_id, "cancel", "")["created"] and post(system, run_id, "interrupt", "")["created"]


def test_the_text_may_be_as_long_as_the_limit_and_a_text_full_of_escapes_is_refused_not_cut(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    assert post(system, run_id, "note", "a" * MAX_TEXT_BYTES)["created"]
    with pytest.raises(ServiceError) as caught:                       # 1 200 quotes are 2 400 bytes once encoded
        post(system, run_id, "note", '"' * MAX_TEXT_BYTES)
    assert caught.value.details["field"] == "text"
    assert [m["seq"] for m in system.inbox.messages("run", run_id)] == [1]


@pytest.mark.parametrize("actor", ["system:watcher", "executor:claude_cli", "system:inbox", "System:x", "SYSTEM:x",
                                   "Executor:claude_cli", "EXECUTOR:x", "agent:verified", "Agent:x", "AGENT:codex"])
def test_a_program_or_an_agent_is_not_the_person_the_inbox_listens_to(tmp_path, actor):
    system = inbox_world(tmp_path)
    run_id = start(system)
    refused(lambda: post(system, run_id, "cancel", actor=actor), reason="sender_not_allowed")
    assert run_log(system, run_id) == []


def test_an_unknown_run_is_not_found_and_a_closed_run_does_not_read_messages(tmp_path):
    system = inbox_world(tmp_path)
    with pytest.raises(ServiceError) as caught:
        post(system, "nope")
    assert caught.value.code == "not_found" and caught.value.status == 404
    run_id = start(system)
    system.engine.process_run(run_id)
    assert system.store.get_run(run_id)["status"] == RunStatus.COMPLETED.value
    refused(lambda: post(system, run_id), reason="run_closed")
    assert run_log(system, run_id) == []
    with pytest.raises(ServiceError) as bad_scope:
        system.inbox.post("project", run_id, message_id="m", kind="note", text="x", actor=ANA)
    assert bad_scope.value.details["field"] == "scope"


def test_an_inbox_nobody_reads_does_not_grow_without_a_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox_module, "MAX_PENDING", 3)
    system = inbox_world(tmp_path)
    run_id = start(system)
    for index in range(3):
        post(system, run_id, "note", "x", f"n{index}")
    refused(lambda: post(system, run_id, "note", "x", "n3"), reason="inbox_full")
    assert post(system, run_id, "note", "x", "n0")["created"] is False        # an old one answers, it is not new
    assert len(system.inbox.messages("run", run_id)) == 3


def test_reading_filters_by_cursor_state_and_page(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    for index in range(5):
        post(system, run_id, "note", "x", f"n{index}")
    system.store.db.log_event(run_id, CONSUMED_EVENT, {"message_id": "n1", "seq": 2, "kind": "note", "action": "noted"})
    ids = lambda **kw: [m["message_id"] for m in system.inbox.messages("run", run_id, **kw)]      # noqa: E731
    assert ids(after=2) == ["n2", "n3", "n4"] and ids(limit=2) == ["n0", "n1"]
    assert ids(state="consumed") == ["n1"] and ids(state="pending") == ["n0", "n2", "n3", "n4"]
    for bad in ({"after": -1}, {"after": True}, {"limit": 0}, {"limit": 501}, {"state": "read"}):
        with pytest.raises(ServiceError) as caught:
            system.inbox.messages("run", run_id, **bad)
        assert caught.value.code == "invalid_request"


# =============================================================================================================
# The engine end to end (runs)
# =============================================================================================================

def post_while_step_a_runs(system, run_id, *messages):
    """Make the person write while the first step is being generated: they arrive between two steps."""
    def generate():
        for kind, text in messages:
            post(system, run_id, kind, text, f"{kind}-while-a")
        return filler("a")

    system.adapter.notes[0] = generate


def calls_made(system) -> int:
    return len(system.adapter.of("note"))


def test_posting_changes_nothing_in_the_run_and_reads_nothing(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    before = system.store.get_run(run_id)
    for kind in ("note", "adjust", "interrupt", "cancel"):
        post(system, run_id, kind, "x")
    after = system.store.get_run(run_id)
    for key in ("status", "current_step", "waiting_reason", "cancel_requested", "llm_calls", "error"):
        assert after[key] == before[key], key
    assert not run_log(system, run_id, CONSUMED_EVENT) and calls_made(system) == 0


def test_a_note_is_recorded_at_the_next_boundary_and_the_run_goes_on(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post_while_step_a_runs(system, run_id, ("note", "keep it formal"))
    run = system.engine.process_run(run_id)
    assert run["status"] == RunStatus.COMPLETED.value and calls_made(system) == len(STEPS)
    (read,) = [e["payload"] for e in run_log(system, run_id, CONSUMED_EVENT)]
    assert (read["message_id"], read["action"], read["step_id"]) == ("note-while-a", "noted", "b")   # the boundary after a


def test_a_cancel_ends_the_run_as_cancelled_before_the_next_call(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post_while_step_a_runs(system, run_id, ("cancel", "stop"))
    run = system.engine.process_run(run_id)
    assert run["status"] == RunStatus.CANCELLED.value and calls_made(system) == 1          # only step a was called
    names = [e["event_type"] for e in run["events"]]
    assert "workflow_cancelled" in names and not {"workflow_failed", "budget_exhausted", "call_paused"} & set(names)
    assert not run["budget_exhausted"] and run["waiting_reason"] == ""
    (read,) = [e["payload"] for e in run_log(system, run_id, CONSUMED_EVENT)]
    assert read["action"] == "cancelled" and read["step_id"] == "b"


@pytest.mark.parametrize("kind", ["interrupt", "adjust"])
def test_an_interrupt_or_an_adjust_holds_the_run_for_the_person_and_asks_nothing_of_the_model(tmp_path, kind):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post_while_step_a_runs(system, run_id, (kind, "wait, I want to look first"))
    run = system.engine.process_run(run_id)
    assert run["status"] == RunStatus.WAITING_HUMAN.value
    assert (run["waiting_reason"], run["waiting_step"], run["waiting_attempt"]) == ("user_message", "b", 1)
    assert calls_made(system) == 1 and run["llm_calls"] == 1                                # step b was not called
    (paused,) = [e["payload"] for e in run["events"] if e["event_type"] == "call_paused"]
    assert (paused["guard"], paused["waiting_reason"], paused["step_id"]) == ("user_inbox", "user_message", "b")
    assert paused["request"] == {"message_id": f"{kind}-while-a", "seq": 1, "kind": kind,
                                 "text": "wait, I want to look first"}
    assert not {"workflow_failed", "budget_exhausted", "workflow_cancelled"} & {e["event_type"] for e in run["events"]}


def test_the_person_settles_the_hold_with_the_approval_path_that_exists_and_the_message_is_not_read_twice(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post_while_step_a_runs(system, run_id, ("interrupt", "look first"))
    assert system.engine.process_run(run_id)["status"] == RunStatus.WAITING_HUMAN.value
    still = system.engine.process_run(run_id)                  # nobody approved: processing it again does nothing
    assert still["status"] == RunStatus.WAITING_HUMAN.value and calls_made(system) == 1

    queued = system.service.approve(run_id)                    # the person, through the existing approval path
    assert queued["status"] == RunStatus.QUEUED.value and (queued["waiting_reason"], queued["waiting_step"]) == ("", "")
    assert [e["payload"]["decision"] for e in queued["events"] if e["event_type"] == "user_message_resolved"] == ["approved"]
    run = system.engine.process_run(run_id)
    assert run["status"] == RunStatus.COMPLETED.value and calls_made(system) == len(STEPS)
    assert len([e for e in run["events"] if e["event_type"] == "call_paused"]) == 1
    assert len(run_log(system, run_id, CONSUMED_EVENT)) == 1


def test_a_new_message_while_the_run_waits_holds_it_again_after_the_first_approval(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post_while_step_a_runs(system, run_id, ("interrupt", "first"))
    system.engine.process_run(run_id)
    post(system, run_id, "adjust", "second", "second")
    system.service.approve(run_id)
    run = system.engine.process_run(run_id)
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "user_message"
    assert [e["payload"]["request"]["message_id"] for e in run["events"] if e["event_type"] == "call_paused"] == [
        "interrupt-while-a", "second"]
    system.service.approve(run_id)
    assert system.engine.process_run(run_id)["status"] == RunStatus.COMPLETED.value


def test_the_person_can_reject_or_cancel_the_hold_and_nothing_else_is_asked_of_them(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post_while_step_a_runs(system, run_id, ("adjust", "change the tone"))
    system.engine.process_run(run_id)
    with pytest.raises(InvalidStateTransition):                 # asking for changes needs an artifact to review
        system.service.changes(run_id, "different tone")
    rejected = system.service.reject(run_id, "no thanks")
    assert rejected["status"] == RunStatus.REJECTED.value and rejected["revision_feedback"] == "no thanks"
    assert [e["payload"]["decision"] for e in rejected["events"] if e["event_type"] == "user_message_resolved"] == ["rejected"]

    other = start(system)
    system.adapter.notes[:0] = [filler("a")]                    # a second run: step a, then the hold
    post_while_step_a_runs(system, other, ("interrupt", "x"))
    system.engine.process_run(other)
    assert system.store.get_run(other)["waiting_reason"] == "user_message"
    assert system.service.cancel(other)["status"] == RunStatus.CANCELLED.value


def test_a_decision_on_a_hold_that_changed_is_refused(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post_while_step_a_runs(system, run_id, ("interrupt", "x"))
    system.engine.process_run(run_id)
    stale = system.store.get_run(run_id)
    system.service.approve(run_id)
    with pytest.raises(InvalidStateTransition):
        system.store.finish_user_message(stale, approve=True)
    with pytest.raises(InvalidStateTransition):
        system.service.approve(run_id)                          # it is queued now: not a wait any more


def test_the_policy_sorts_what_the_person_wrote_cancel_first(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post_while_step_a_runs(system, run_id, ("note", "n"), ("interrupt", "i"), ("cancel", "c"), ("adjust", "a"))
    run = system.engine.process_run(run_id)
    assert run["status"] == RunStatus.CANCELLED.value
    assert [e["payload"]["message_id"] for e in run_log(system, run_id, CONSUMED_EVENT)] == ["cancel-while-a"]
    unread = system.inbox.pending("run", run_id)
    assert [m["kind"] for m in unread] == ["interrupt", "adjust", "note"]                    # still there, in order


def test_the_order_comes_from_the_settings(tmp_path):
    system = inbox_world(tmp_path)
    system.settings.availability_priorities = ("cancel", "note", "adjust", "interrupt")
    run_id = start(system)
    post_while_step_a_runs(system, run_id, ("interrupt", "i"), ("note", "n"))
    run = system.engine.process_run(run_id)
    # the note is read first (it goes on), then the interrupt holds the run
    assert [e["payload"]["action"] for e in run_log(system, run_id, CONSUMED_EVENT)] == ["noted", "paused"]
    assert run["waiting_reason"] == "user_message"


def test_without_the_policy_the_chain_of_every_step_has_no_inbox_guard(tmp_path, monkeypatch):
    system = inbox_world(tmp_path, enabled=False)
    chains = []
    original = call_guards.build_call_guards

    def capture(env, **kwargs):
        chains.append(original(env, **kwargs))
        return chains[-1]

    from multiagent import workflow_engine
    monkeypatch.setattr(workflow_engine, "build_call_guards", capture)
    run_id = start(system)
    system.engine.process_run(run_id)
    assert chains and all("user_inbox" not in chain.names for chain in chains)
    system.settings.availability_enabled = True
    run_id = start(system)
    system.engine.process_run(run_id)
    assert {chain.names[0] for chain in chains[-3:]} == {"user_inbox"}


def test_a_message_written_before_a_restart_is_read_when_the_run_goes_on(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post(system, run_id, "interrupt", "before it even started", "early")
    system.store.update_run(run_id, status=RunStatus.RUNNING.value)
    system.store.recover_running_runs("worker-2")                 # the backend restarted: the run is queued again
    run = system.engine.process_run(run_id)
    assert run["waiting_reason"] == "user_message" and calls_made(system) == 0


def test_the_inbox_is_read_only_by_the_worker_thread(tmp_path, monkeypatch):
    system = inbox_world(tmp_path)
    run_id = start(system)
    in_step, release = threading.Event(), threading.Event()

    def slow_first_step():
        in_step.set()
        assert release.wait(30)
        return filler("a")

    system.adapter.notes[0] = slow_first_step
    readers: list[str] = []
    original = system.store.db.log_event

    def spy(run, event_type, payload=None):
        if event_type == CONSUMED_EVENT:
            readers.append(threading.current_thread().name)
        return original(run, event_type, payload)

    monkeypatch.setattr(system.store.db, "log_event", spy)
    worker = LocalWorker(system.store, system.engine, 0.02)
    worker.start()
    try:
        assert in_step.wait(30), "the worker never started the first step"
        main_thread = threading.current_thread().name
        for kind in ("note", "interrupt", "cancel"):
            post(system, run_id, kind, "x", f"from-{kind}")           # the person writes from another thread
        during = system.store.get_run(run_id)
        assert during["status"] == RunStatus.RUNNING.value and during["cancel_requested"] == 0
        assert readers == [], "posting read the inbox"
        release.set()
        until(lambda: system.store.get_run(run_id)["status"] in (RunStatus.CANCELLED.value, RunStatus.WAITING_HUMAN.value),
              what="the worker to read the inbox")
    finally:
        release.set()
        worker.stop()
    # The worker runs each run in its own thread (S4.D1), named after the run.
    assert len(readers) == 1 and readers[0] == f"multiagent-run-{run_id[:8]}" and main_thread not in readers
    assert system.store.get_run(run_id)["status"] == RunStatus.CANCELLED.value


def test_a_chain_in_the_threads_of_the_api_cannot_read_a_run_by_posting_to_it(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    names = {name for name in dir(system.inbox) if not name.startswith("_")}
    # The only public ways to read the inbox of a run are listings; consuming is the guard's, in the chain of the engine.
    assert names >= {"post", "messages", "pending", "cursor", "require_no_cancel"}
    assert not {name for name in names if "consume" in name or "apply" in name}
    post(system, run_id, "cancel")
    assert system.inbox.pending("run", run_id)[0]["state"] == "pending"
    assert system.store.get_run(run_id)["status"] == RunStatus.QUEUED.value


# =============================================================================================================
# Tasks
# =============================================================================================================

def inbox_events(services, task_id, *types) -> list[dict]:
    wanted = types or inbox_module.EVENT_TYPES
    return [e for e in services.tasks.events(task_id=task_id) if e["type"] in wanted]


def running_attempt(background, task_id="T1", kind="claude"):
    """A long fake program in the background; returns once its process is up."""
    services = background.services
    background.fake.set(kind, sleep=LONG, stdout=claude_out())
    assert services.executors.run_task(task_id, f"{kind}_cli")["outcome"] == "running"
    attempt = services.executors.background.get(task_id)
    until(lambda: attempt._handle is not None, what="the program to start")
    return attempt


def say(services, task_id, kind, text="x", message_id=None, actor=ANA):
    return services.inbox.post("task", task_id, message_id=message_id or f"{kind}-1", kind=kind, text=text, actor=actor)


def test_a_task_note_is_recorded_by_the_attempt_and_the_program_is_left_alone(background):
    services = background.services
    attempt = running_attempt(background)
    say(services, "T1", "note", "FYI: the spec changed")
    marker_event = until(lambda: inbox_events(services, "T1", CONSUMED_EVENT), what="the note to be read")[0]
    assert marker_event["actor"] == INBOX_ACTOR and marker_event["payload"]["action"] == "noted"
    assert marker_event["ref"] == "note-1"
    assert not attempt._handle.done() and services.tasks.get_task("T1")["state"] == "running"
    assert services.inbox.messages("task", "T1")[0]["state"] == "consumed"
    services.executors.background.cancel("T1")
    finished_wait(services, 15)


def test_a_task_cancel_cancels_the_task_and_kills_the_process_tree(background):
    services = background.services
    attempt = running_attempt(background)
    say(services, "T1", "cancel", "stop")
    until(lambda: inbox_events(services, "T1", CONSUMED_EVENT), what="the cancel to be read")
    finished_wait(services, 15)
    assert attempt._handle.done() and attempt._handle.wait(0).cancelled is True
    task = services.tasks.get_task("T1")
    assert task["state"] == "cancelled"                                   # the end of the attempt did not pause it again
    (read,) = [e["payload"] for e in inbox_events(services, "T1", CONSUMED_EVENT)]
    assert read["action"] == "cancelled" and read["kind"] == "cancel"
    cancelled = [e for e in services.tasks.events(task_id="T1") if e["type"] == "task.cancelled"]
    assert cancelled and cancelled[-1]["actor"] == ANA                     # the person cancelled it, not the inbox
    assert [e["outcome"] for e in attempt_events(services, "T1")] == ["cancelled"]


@pytest.mark.parametrize("kind", ["interrupt", "adjust"])
def test_a_task_interrupt_kills_the_tree_saves_a_checkpoint_and_waits_for_the_person(background, kind):
    services = background.services
    attempt = running_attempt(background)
    say(services, "T1", kind, "stop and let me look", "stop-1")
    finished_wait(services, 30)
    assert attempt._handle.done() and attempt._handle.wait(0).cancelled is True

    task = services.tasks.get_task("T1")
    assert task["state"] == "paused"
    hold = task["continuity"]["hold"]
    assert hold["reason"] == "approval"
    (leaf,) = [hold["condition"]["params"]] if hold["condition"]["type"] == "approval" else [None]
    assert leaf["approval"] == "user-message:stop-1"
    types = event_types(services, "T1")
    assert "task.checkpointed" in types and "continuity.held" in types
    (read,) = [e["payload"] for e in inbox_events(services, "T1", CONSUMED_EVENT)]
    assert read["action"] == "interrupting" and read["kind"] == kind
    (applied,) = [e["payload"] for e in inbox_events(services, "T1", APPLIED_EVENT)]
    assert applied["action"] == "held" and applied["message_id"] == "stop-1" and "error" not in applied
    assert applied["checkpoint"] and not str(applied["checkpoint"]).startswith("error")
    assert not services.executors.background.active()


def test_an_interrupted_task_is_never_approved_by_the_inbox(background):
    services = background.services
    running_attempt(background)
    say(services, "T1", "interrupt", "wait", "stop-1")
    finished_wait(services, 30)
    wait = services.executors.check_waits(task_id="T1")["waits"][0]
    assert wait["state"] == "waiting" and wait["reason"] == "approval"
    hold = services.tasks.get_task("T1")["continuity"]["hold"]
    assert not hold["condition"].get("decisions")                         # nobody decided
    assert hold["condition"]["params"]["approver"] == ANA                 # only the person who wrote it may decide
    for program in (INBOX_ACTOR, "system:watcher", "executor:claude_cli"):
        refused(lambda: services.executors.record_approval("T1", "user-message:stop-1", approved=True, by=program),
                reason="approver_not_allowed")
    refused(lambda: services.executors.record_approval("T1", "user-message:stop-1", approved=True, by="user:bob"),
            reason="approver_mismatch")                                    # another person (or the agent) cannot lift it
    refused(lambda: services.executors.record_approval("T1", "user-message:stop-1", approved=True, by="agent:verified"),
            reason="approver_mismatch")
    assert services.executors.check_waits(task_id="T1")["waits"][0]["state"] == "waiting"
    # Only the person's own decision lifts it.
    decided = services.executors.record_approval("T1", "user-message:stop-1", approved=True, by=ANA)
    assert decided["status"] == "approved" and decided["by"] == ANA
    assert services.executors.check_waits(task_id="T1")["waits"][0]["state"] == "met"
    assert services.tasks.get_task("T1")["state"] == "paused"             # met is not resumed by the inbox either


def test_the_attempt_reads_one_message_that_stops_it_and_leaves_the_rest_for_the_next_one(background):
    services = background.services
    running_attempt(background)
    say(services, "T1", "note", "n", "n1")
    until(lambda: inbox_events(services, "T1", CONSUMED_EVENT), what="the note")
    say(services, "T1", "interrupt", "i", "i1")
    say(services, "T1", "note", "n2", "n2")
    finished_wait(services, 30)
    read = [e["payload"]["message_id"] for e in inbox_events(services, "T1", CONSUMED_EVENT)]
    assert read == ["n1", "i1"]                                           # the interrupt outranks the second note
    assert [m["message_id"] for m in services.inbox.pending("task", "T1")] == ["n2"]     # it waits for the next attempt
    assert services.tasks.get_task("T1")["state"] == "paused"


def test_a_message_is_applied_once_even_if_the_tick_runs_again(background):
    services = background.services
    running_attempt(background)
    say(services, "T1", "interrupt", "wait", "stop-1")
    finished_wait(services, 30)
    for _ in range(3):
        services.inbox.on_attempt_tick("T1", 1, "claude_cli", None)      # the attempt is over: not its boundary any more
    assert len(inbox_events(services, "T1", CONSUMED_EVENT)) == 1 and len(inbox_events(services, "T1", APPLIED_EVENT)) == 1
    assert services.inbox.finish_interrupt("T1", 1) is None


def test_a_tick_outside_the_live_attempt_reads_nothing(background):
    services = background.services
    assert services.tasks.get_task("T1")["state"] == "open"
    # Posting to an open task is fine (the message waits); reading needs a running attempt of the right number.
    say(services, "T1", "cancel", "x")
    services.inbox.on_attempt_tick("T1", 1, "claude_cli", None)
    services.inbox.on_attempt_tick("NOPE", 1, "claude_cli", None)
    assert inbox_events(services, "T1", CONSUMED_EVENT) == []
    assert services.tasks.get_task("T1")["state"] == "open"


def test_a_synchronous_attempt_does_not_read_the_inbox(background):
    services = background.services
    say(services, "T1", "cancel", "x")
    out = services.executors.run_task("T1", "claude_cli", wait=True)
    assert out["outcome"] == "submitted"
    assert inbox_events(services, "T1", CONSUMED_EVENT) == []
    assert [m["state"] for m in services.inbox.messages("task", "T1")] == ["pending"]


def test_with_the_policy_off_the_attempt_does_not_touch_the_inbox(world):
    services = world.services
    refused(lambda: say(services, "T1", "cancel"), reason="availability_disabled")
    out = services.executors.run_task("T1", "claude_cli")
    assert out["outcome"] == "submitted" and not inbox_events(services, "T1")


def test_a_task_that_is_closed_does_not_read_messages(background):
    services = background.services
    services.tasks.transition("T3", "cancel", actor=ANA)
    refused(lambda: say(services, "T3", "note", "x"), reason="task_closed")
    with pytest.raises(ServiceError) as caught:
        say(services, "NOPE", "note", "x")
    assert caught.value.status == 404


def test_task_messages_have_their_own_seq_and_are_idempotent(background):
    services = background.services
    one = say(services, "T1", "note", "a", "a")
    two = say(services, "T1", "adjust", "b", "b")
    other = say(services, "T3", "note", "c", "c")
    again = say(services, "T1", "note", "a", "a")
    assert (one["seq"], two["seq"], other["seq"]) == (1, 2, 1) and again["created"] is False and again["seq"] == 1
    assert [e["payload"]["seq"] for e in inbox_events(services, "T1", USER_MESSAGE_EVENT)] == [1, 2]
    events = inbox_events(services, "T1", USER_MESSAGE_EVENT)
    assert all(e["ref"] == e["payload"]["message_id"] and e["actor"] == ANA for e in events)
    refused(lambda: say(services, "T1", "note", "other text", "a"), reason="message_id_conflict")


def test_threads_writing_to_a_task_get_numbers_without_gaps(background):
    services = background.services
    seqs, gate = [], threading.Barrier(8)

    def send(index):
        gate.wait(10)
        seqs.append(say(services, "T1", "note", str(index), f"t{index}")["seq"])

    threads = [threading.Thread(target=send, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(seqs) == list(range(1, 9))


def test_the_assignee_of_a_task_cannot_write_to_its_inbox(background):
    services = background.services
    services.tasks.transition("T3", "claim", actor="user:ana")
    for same in ("user:ana", "User:Ana", "USER:ANA"):
        caught = refused(lambda same=same: say(services, "T3", "cancel", "stop", f"c-{same}", actor=same),
                         reason="sender_not_allowed")
        assert caught.status == 409
    assert inbox_events(services, "T3", USER_MESSAGE_EVENT) == []
    assert say(services, "T3", "note", "from somebody else", "b1", actor="user:bob")["created"] is True
    # Another task of the same actor is not affected: only the task it works on is closed to it.
    assert say(services, "T1", "note", "x", "free", actor="user:ana")["created"] is True


def test_an_interrupt_added_to_the_hold_of_a_limit_keeps_the_task_held_after_the_reset(tmp_path):
    world = make_quota_world(tmp_path)
    services = world.services
    services.inbox.availability = AvailabilityPolicy(enabled=True)
    hold_at_the_reset(world, reset=hours_from_now(40))                        # a reset past the 12 hour default deadline
    task = services.tasks.get_task("T1")
    assert task["state"] == "paused" and task["continuity"]["hold"]["reason"] == "plan_limit"
    attempt = task["attempt"]
    services.inbox._interrupts["T1"] = {"message_id": "stop-1", "attempt": attempt, "executor_id": "fake_cli",
                                        "session_id": "", "actor": ANA}
    outcome = services.inbox.finish_interrupt("T1", attempt)
    assert outcome["action"] == "held" and "error" not in outcome, outcome
    hold = services.tasks.get_task("T1")["continuity"]["hold"]
    assert hold["reason"] == "plan_limit"                                      # the limit keeps its reason
    assert hold["condition"]["type"] == "approval" and hold["condition"]["params"] == {
        "approval": "user-message:stop-1", "approver": ANA}
    assert hold["condition"]["deadline_at"] > hold["resume_after"]            # it does not expire before the limit resets
    world.clock.to(hours_from_now(45))                                         # the limit has reset...
    assert services.executors.check_waits(task_id="T1")["waits"][0]["state"] == "waiting"   # ...the approval has not come
    report = services.executors.resume_due()
    assert report["resumed"] == [] and services.tasks.get_task("T1")["state"] == "paused"
    services.executors.record_approval("T1", "user-message:stop-1", approved=True, by=ANA)
    assert services.executors.check_waits(task_id="T1")["waits"][0]["state"] == "met"


def test_an_interrupt_on_a_task_with_a_near_limit_is_held_too(tmp_path):
    world = make_quota_world(tmp_path)
    services = world.services
    services.inbox.availability = AvailabilityPolicy(enabled=True)
    hold_at_the_reset(world, reset=hours_from_now(1))
    attempt = services.tasks.get_task("T1")["attempt"]
    services.inbox._interrupts["T1"] = {"message_id": "stop-2", "attempt": attempt, "executor_id": "fake_cli",
                                        "session_id": "", "actor": ANA}
    assert services.inbox.finish_interrupt("T1", attempt)["action"] == "held"
    assert services.tasks.get_task("T1")["continuity"]["hold"]["condition"]["params"]["approval"] == "user-message:stop-2"


def test_a_reader_that_lost_the_race_for_a_message_does_nothing(background, monkeypatch):
    services = background.services
    attempt = running_attempt(background)
    killed = []
    original = services.executors.background.cancel
    monkeypatch.setattr(services.executors.background, "cancel", lambda task_id: killed.append(task_id) or original(task_id))
    monkeypatch.setattr(services.inbox, "_mark_consumed", lambda *args, **kwargs: False)    # somebody else marked it first
    say(services, "T1", "cancel", "stop", "c1")
    say(services, "T1", "interrupt", "wait", "i1")
    for _ in range(5):                                                     # several ticks of the watch go by
        services.inbox.on_attempt_tick("T1", 1, "claude_cli", None)
    time.sleep(0.5)
    assert killed == [] and not attempt._handle.done() and services.inbox._interrupts == {}
    assert services.tasks.get_task("T1")["state"] == "running" and not inbox_events(services, "T1", APPLIED_EVENT)
    monkeypatch.undo()
    services.executors.background.cancel("T1")
    finished_wait(services, 15)


def test_the_message_is_marked_as_read_before_the_task_is_touched(background, monkeypatch):
    services = background.services
    running_attempt(background)
    seen = []
    original = services.executors.background.cancel

    def spy(task_id):
        seen.append([e["payload"]["action"] for e in inbox_events(services, task_id, CONSUMED_EVENT)])
        return original(task_id)

    monkeypatch.setattr(services.executors.background, "cancel", spy)
    say(services, "T1", "cancel", "stop", "c1")
    finished_wait(services, 15)
    assert seen and seen[0] == ["cancelled"]                              # the marker was there when the tree was killed
    (applied,) = [e["payload"] for e in inbox_events(services, "T1", APPLIED_EVENT)]
    assert applied["action"] == "cancelled" and applied["message_id"] == "c1" and applied["killed"] is True


def test_a_cancel_the_task_refuses_is_marked_as_read_and_recorded_as_refused(background, monkeypatch):
    services = background.services
    running_attempt(background)

    def refuse(*args, **kwargs):
        raise ServiceError("invalid_state", "no", status=409)

    monkeypatch.setattr(services.tasks, "transition", refuse)
    say(services, "T1", "cancel", "stop", "c1")
    until(lambda: inbox_events(services, "T1", APPLIED_EVENT), what="the cancel to be applied")
    assert [e["payload"]["action"] for e in inbox_events(services, "T1", CONSUMED_EVENT)] == ["cancelled"]
    (applied,) = [e["payload"] for e in inbox_events(services, "T1", APPLIED_EVENT)]
    assert applied["action"] == "refused" and applied["error"] == "invalid_state"
    assert not services.executors.background.get("T1")._handle.done()      # nothing was killed
    monkeypatch.undo()
    services.executors.background.cancel("T1")
    finished_wait(services, 15)


# --------------------------------------------------------------------------- one policy for the inbox and the guard

def test_the_engine_and_the_inbox_resolve_the_policy_in_the_same_place(tmp_path):
    services = make_stack(tmp_path / "local", enabled=False)
    run = services.runs.create(RunRequest(**GENERIC), bundle="team")
    bundle_engine = services.runs._bundle_runtime("team").service.engine
    assert services.system.engine.availability_source == services.inbox.policy
    assert bundle_engine.availability_source == services.inbox.policy
    assert services.system.engine._availability_policy() is None and bundle_engine._availability_policy() is None
    refused(lambda: services.inbox.post("run", run["id"], message_id="m1", kind="interrupt", text="", actor=ANA),
            reason="availability_disabled")

    services.inbox.availability = AvailabilityPolicy(enabled=True)             # an override that the settings do not know
    assert services.inbox.post("run", run["id"], message_id="m1", kind="interrupt", text="", actor=ANA)["created"]
    assert bundle_engine._availability_policy().enabled and services.system.engine._availability_policy().enabled
    process_bundle_run(services)
    held = services.runs.get(run["id"])
    assert (held["status"], held["waiting_reason"]) == ("waiting_human", "user_message")   # the message was read, not lost
    assert [m["state"] for m in services.inbox.messages("run", run["id"])] == ["consumed"]

    services.inbox.availability = AvailabilityPolicy()                           # off by the override although the settings say on
    services.settings.availability_enabled = True
    assert bundle_engine._availability_policy() is None
    refused(lambda: services.inbox.post("run", run["id"], message_id="m2", kind="note", text="x", actor=ANA),
            reason="availability_disabled")


def test_the_executors_override_counts_for_the_inbox_and_the_guard_alike(tmp_path):
    services = make_stack(tmp_path / "local", enabled=False)
    run = services.runs.create(RunRequest(**GENERIC), bundle="team")
    services.executors.availability = AvailabilityPolicy(enabled=True)
    assert services.inbox.post("run", run["id"], message_id="m1", kind="cancel", text="", actor=ANA)["created"]
    process_bundle_run(services)
    assert services.runs.get(run["id"])["status"] == "cancelled"


def test_a_policy_that_cannot_be_resolved_is_off_for_both(tmp_path):
    services = make_stack(tmp_path / "local")
    run = services.runs.create(RunRequest(**GENERIC), bundle="team")
    engine = services.runs._bundle_runtime("team").service.engine
    services.settings.availability_max_wait_seconds = 99999                        # outside the range of the policy
    assert engine._availability_policy() is None
    refused(lambda: services.inbox.post("run", run["id"], message_id="m1", kind="note", text="x", actor=ANA),
            reason="availability_policy_invalid")


# =============================================================================================================
# The hand-off primitives and a session that was retired
# =============================================================================================================

def test_the_package_keeps_the_cursor_and_the_new_session_reads_from_there(background):
    services = background.services
    for index in range(3):
        say(services, "T1", "note", f"before {index}", f"b{index}")
    package = {"task_id": "T1", "inbox_cursor": services.inbox.cursor("task", "T1")}
    assert package["inbox_cursor"] == 3
    say(services, "T1", "adjust", "after the package", "a0")
    say(services, "T1", "note", "after, too", "a1")
    new_session = services.inbox.messages("task", "T1", after=package["inbox_cursor"])
    assert [(m["seq"], m["message_id"]) for m in new_session] == [(4, "a0"), (5, "a1")]
    assert services.inbox.messages("task", "T1", after=services.inbox.cursor("task", "T1")) == []
    assert services.inbox.cursor("task", "T3") == 0


def handoff(services, task_id: str, calls: list[str]) -> dict:
    """The order the hand-off of AVA-T04 must follow: cursor, re-read the inbox, only then claim."""
    package = {"inbox_cursor": services.inbox.cursor("task", task_id)}
    calls.append("package")
    services.inbox.require_no_cancel("task", task_id)
    calls.append("claim")
    return package


def test_the_hand_off_reads_the_inbox_again_before_claim_and_stops_for_a_cancel(background):
    services = background.services
    calls: list[str] = []
    say(services, "T1", "note", "n", "n")
    say(services, "T1", "interrupt", "i", "i")
    assert handoff(services, "T1", calls)["inbox_cursor"] == 2 and calls == ["package", "claim"]   # only a cancel stops it

    say(services, "T1", "cancel", "never mind", "c")                      # written after the package was built
    calls.clear()
    caught = refused(lambda: handoff(services, "T1", calls), reason="inbox_cancelled")
    assert calls == ["package"] and caught.details["message_id"] == "c" and caught.details["seq"] == 3
    assert services.tasks.get_task("T1")["state"] == "open"               # nothing was claimed or started

    services.inbox._mark_consumed(inbox_module._TaskLog(services.tasks.store, "T1"),
                                  services.inbox.pending("task", "T1")[0], {"action": "cancelled"})
    assert services.inbox.pending("task", "T1")[0]["kind"] != "cancel"
    services.inbox.require_no_cancel("task", "T1")                        # a cancel that was acted on does not stop it again


def test_require_no_cancel_works_for_runs_too(tmp_path):
    system = inbox_world(tmp_path)
    run_id = start(system)
    post(system, run_id, "interrupt", "x", "i")
    system.inbox.require_no_cancel("run", run_id)
    post(system, run_id, "cancel", "x", "c")
    refused(lambda: system.inbox.require_no_cancel("run", run_id), reason="inbox_cancelled")


def test_a_session_that_was_retired_does_not_act(background):
    services = background.services
    background.fake.set("claude", stdout=claude_out("no envelope here"))
    first = services.executors.run_task("T1", "claude_cli", wait=True)
    sid = first["session_id"]
    assert first["outcome"] == "failed" and sid and services.tasks.get_task("T1")["state"] == "paused"
    assert services.executors.session_guard(sid) is not False

    services.executors.retire_session(sid, reason="handoff", actor="agent:old")          # the hand-off retired it
    assert services.executors.session_guard(sid) is False
    background.fake.set("claude", stdout=claude_out())
    caught = refused(lambda: services.executors.resume_task("T1"), reason="session_retired")
    assert caught.status == 409 and caught.details["session_id"] == sid and caught.details["retired_as"] == "handoff"
    assert len(background.fake.calls("claude")) == 1                      # the retired session was not started again
    assert services.tasks.get_task("T1")["state"] == "paused"


def test_start_pause_resume_and_submit_are_the_assignees_alone(background):
    services = background.services
    tasks = services.tasks
    tasks.transition("T3", "claim", actor="agent:new")
    for action in ("start", "pause", "resume", "submit"):
        caught = refused(lambda action=action: tasks.transition("T3", action, actor="agent:old"))
        assert caught.status == 409, action
    tasks.transition("T3", "start", actor="agent:new")
    for action in ("pause", "submit"):
        refused(lambda action=action: tasks.transition("T3", action, actor="agent:old"))
    refused(lambda: services.envelopes.submit_result("T3", RESULT, actor="agent:old"))
    assert tasks.get_task("T3")["state"] == "running" and tasks.get_task("T3")["assignee"] == "agent:new"
    tasks.transition("T3", "pause", actor="agent:new")
    refused(lambda: tasks.transition("T3", "resume", actor="agent:old"))
    assert tasks.transition("T3", "resume", actor="agent:new")["state"] == "running"


# =============================================================================================================
# The HTTP routes
# =============================================================================================================

def make_stack(root: Path, *, enabled=True) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True, gemini_api_key="",
                        openai_api_key="", openai_enabled=False, availability_enabled=enabled)
    return ApplicationServices.create(settings, bundles={"team": BUNDLE})


def process_bundle_run(services) -> dict:
    runtime_ = services.runs._bundle_runtime("team")
    assert runtime_ is not None and runtime_.worker.process_once()
    return runtime_


BODY = {"message_id": "m1", "kind": "interrupt", "text": "wait, let me look", "actor": ANA}


def test_the_routes_post_and_read_messages_of_a_run_and_nothing_else_changes(tmp_path):
    services = make_stack(tmp_path / "local")
    run = services.runs.create(RunRequest(**GENERIC), bundle="team")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        url = f"/api/v1/runs/{run['id']}/messages"
        created = client.post(url, json=BODY)
        assert created.status_code == 201, created.text
        sent = created.json()
        assert (sent["seq"], sent["kind"], sent["state"], sent["created"]) == (1, "interrupt", "pending", True)
        again = client.post(url, json=BODY)
        assert again.status_code == 200 and again.json()["created"] is False and again.json()["seq"] == 1
        clash = client.post(url, json={**BODY, "text": "other"})
        assert clash.status_code == 409 and clash.json()["detail"]["details"]["reason"] == "message_id_conflict"
        client.post(url, json={**BODY, "message_id": "m2", "kind": "note", "text": "later"})
        listed = client.get(url).json()
        assert [m["message_id"] for m in listed] == ["m1", "m2"]
        assert [m["message_id"] for m in client.get(url, params={"after": 1}).json()] == ["m2"]
        assert [m["message_id"] for m in client.get(url, params={"limit": 1}).json()] == ["m1"]
        assert [m["message_id"] for m in client.get(url, params={"state": "consumed"}).json()] == []
        assert client.get(url, params={"after": -1}).status_code == 422
        assert client.get(url, params={"limit": 501}).status_code == 422
        assert client.get(url, params={"state": "x"}).status_code == 422
        after = client.get(f"/api/v1/runs/{run['id']}").json()
        assert after["status"] == "queued" and after["waiting_reason"] == "" and after["llm_calls"] == 0
        assert not [e for e in after["events"] if e["event_type"] in ("workflow_cancelled", "call_paused", "human_approved")]


def test_the_routes_keep_the_validation_and_the_error_shape(tmp_path):
    services = make_stack(tmp_path / "local")
    run = services.runs.create(RunRequest(**GENERIC), bundle="team")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        url = f"/api/v1/runs/{run['id']}/messages"
        for broken in ({**BODY, "extra": 1}, {"kind": "note", "actor": ANA}, {**BODY, "kind": "approve"},
                       {**BODY, "actor": "system:watcher"}, {**BODY, "message_id": "has space"}, {**BODY, "text": 5}):
            response = client.post(url, json=broken)
            assert response.status_code in (409, 422), broken
            assert set(response.json()["detail"]) == {"code", "message", "details"}
        assert client.post("/api/v1/runs/nope/messages", json=BODY).status_code == 404
        assert client.get("/api/v1/runs/nope/messages").status_code == 404
        assert client.post("/api/v1/tasks/NOPE/messages", json=BODY).status_code == 404
        assert client.get(url).json() == []                                           # nothing broken was written


def test_a_cross_site_or_foreign_host_request_never_reaches_the_inbox(tmp_path):
    services = make_stack(tmp_path / "local")
    run = services.runs.create(RunRequest(**GENERIC), bundle="team")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        url = f"/api/v1/runs/{run['id']}/messages"
        hostile = client.post(url, json=BODY, headers={"origin": "https://attacker.example"})
        assert hostile.status_code == 403 and hostile.json()["detail"]["code"] == "forbidden_origin"
        browser = client.post(url, json=BODY, headers={"sec-fetch-site": "cross-site"})
        assert browser.status_code == 403
        assert client.post(url, json=BODY, headers={"host": "evil.example"}).status_code == 400
        assert client.get(url).json() == []


def test_with_the_policy_off_the_routes_answer_409_and_write_nothing(tmp_path):
    services = make_stack(tmp_path / "local", enabled=False)
    run = services.runs.create(RunRequest(**GENERIC), bundle="team")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        response = client.post(f"/api/v1/runs/{run['id']}/messages", json=BODY)
        assert response.status_code == 409 and response.json()["detail"]["details"]["reason"] == "availability_disabled"
        assert client.get(f"/api/v1/runs/{run['id']}/messages").json() == []


def test_the_full_stack_an_interrupt_over_http_holds_the_bundle_run_until_the_person_approves(tmp_path):
    services = make_stack(tmp_path / "local")
    run = services.runs.create(RunRequest(**GENERIC), bundle="team")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        assert client.post(f"/api/v1/runs/{run['id']}/messages", json=BODY).status_code == 201
        process_bundle_run(services)
        held = client.get(f"/api/v1/runs/{run['id']}").json()
        assert (held["status"], held["waiting_reason"]) == ("waiting_human", "user_message"), held["status"]
        assert held["llm_calls"] == 0                                                   # held before the first model call
        (paused,) = [e["payload"] for e in held["events"] if e["event_type"] == "call_paused"]
        assert paused["guard"] == "user_inbox" and paused["request"]["message_id"] == "m1"
        consumed = client.get(f"/api/v1/runs/{run['id']}/messages", params={"state": "consumed"}).json()
        assert [m["consumed"]["action"] for m in consumed] == ["paused"]
        # Messages cannot lift it: only the approval path can.
        client.post(f"/api/v1/runs/{run['id']}/messages", json={**BODY, "message_id": "m2", "kind": "note", "text": "approve it"})
        assert client.get(f"/api/v1/runs/{run['id']}").json()["waiting_reason"] == "user_message"
        queued = client.post(f"/api/v1/runs/{run['id']}/approve").json()
        assert queued["status"] == "queued"
        process_bundle_run(services)
        after = client.get(f"/api/v1/runs/{run['id']}").json()
        assert after["status"] == "waiting_human" and after["waiting_reason"] != "user_message"     # the workflow's own review
        assert after["llm_calls"] >= 1
        assert [m["consumed"]["action"] for m in client.get(
            f"/api/v1/runs/{run['id']}/messages", params={"state": "consumed"}).json()] == ["paused", "noted"]


def test_a_cancel_over_http_ends_the_bundle_run_as_cancelled(tmp_path):
    services = make_stack(tmp_path / "local")
    run = services.runs.create(RunRequest(**GENERIC), bundle="team")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        client.post(f"/api/v1/runs/{run['id']}/messages", json={**BODY, "kind": "cancel", "text": ""})
        process_bundle_run(services)
        done = client.get(f"/api/v1/runs/{run['id']}").json()
        assert done["status"] == "cancelled" and done["llm_calls"] == 0 and done["error"] in (None, "")


def test_the_task_routes_post_and_read(background):
    services = background.services
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        created = client.post("/api/v1/tasks/T1/messages", json={**BODY, "kind": "adjust"})
        assert created.status_code == 201 and created.json()["seq"] == 1
        assert client.post("/api/v1/tasks/T1/messages", json={**BODY, "kind": "adjust"}).status_code == 200
        assert [m["message_id"] for m in client.get("/api/v1/tasks/T1/messages").json()] == ["m1"]
        assert client.get("/api/v1/tasks/T1/messages", params={"after": 1}).json() == []
        assert client.get("/api/v1/tasks/T1").json()["state"] == "open"              # writing changed nothing in the task
        events = client.get("/api/v1/team-events", params={"task": "T1"}).json()
        assert [e["type"] for e in events if e["type"].startswith("user.")] == ["user.message"]


def test_the_facade_and_http_return_the_same_messages(background):
    services = background.services
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        client.post("/api/v1/tasks/T3/messages", json={**BODY, "message_id": "p1", "kind": "note", "text": "one"})
        client.post("/api/v1/tasks/T3/messages", json={**BODY, "message_id": "p2", "kind": "cancel", "text": ""})
        assert client.get("/api/v1/tasks/T3/messages").json() == services.inbox.messages("task", "T3")
        assert client.get("/api/v1/tasks/T3/messages", params={"state": "pending", "after": 1}).json() == \
            services.inbox.messages("task", "T3", state="pending", after=1)
