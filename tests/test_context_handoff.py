# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""AVA-T04: synthetic CMP measurements at executor attempt boundaries."""
from __future__ import annotations

import uuid

import pytest
import yaml

import test_quota_hold as quota
from test_context_snapshot import PROFILE, Summarizer, model
from test_executor_services import claude_out, make_world
from multiagent.catalog import Catalog
from multiagent.model_router import ModelRouter
from multiagent.services import ServiceError


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path, tmp_path / "bin")


def paused(world):
    world.fake.set("claude", stdout=claude_out("no envelope here"))
    first = world.services.executors.run_task("T1", "claude_cli")
    assert first["outcome"] == "failed"
    world.fake.set("claude", stdout=claude_out())
    return first


def measure(monkeypatch, world, *, ratio, threshold=.7, enabled=True, failure=False):
    def refresh(task_id, *, model_id=None):
        assert task_id == "T1"
        if failure:
            raise RuntimeError("synthetic measurement failure")
        if not enabled:
            return {"scope": "task:T1", "enabled": False, "action": "none"}
        return {"scope": "task:T1", "enabled": True, "ratio": ratio,
                "level": "threshold" if ratio >= threshold else "warning"}
    monkeypatch.setattr(world.services.envelopes, "refresh_task_context", refresh)
    monkeypatch.setattr(world.services.envelopes, "get_context_snapshot",
                        lambda scope: {"ref": "snapshot-test"})


def events(world, kind):
    return [e for e in world.services.tasks.events(task_id="T1") if e["type"] == kind]


def test_below_threshold_resumes_the_same_session(world, monkeypatch):
    first = paused(world)
    measure(monkeypatch, world, ratio=.69)
    again = world.services.executors.resume_task("T1")
    assert again["mode"] == "resume"
    assert again["session_id"] == first["session_id"]
    assert again["context_handoff"] is None
    assert not events(world, "session.retired")


def test_threshold_retires_once_and_launches_with_checkpoint_and_snapshot(world, monkeypatch):
    first = paused(world)
    measure(monkeypatch, world, ratio=.71)
    again = world.services.executors.resume_task("T1")
    handoff = again["context_handoff"]
    assert again["mode"] == "launch" and again["session_id"] != first["session_id"]
    assert handoff["ratio"] == .71 and handoff["level"] == "threshold"
    assert handoff["retired_session"] == first["session_id"]
    assert handoff["snapshot_ref"] == "snapshot-test"
    assert handoff["checkpoint"]["saved"] and handoff["inbox_cursor"] == 0
    assert world.services.executors.latest_checkpoint("T1")["seq"] == handoff["checkpoint"]["seq"]
    retired = events(world, "session.retired")
    assert len(retired) == 1 and retired[0]["payload"]["reason"] == "context_threshold"
    assert len(events(world, "task.checkpointed")) == 1
    assert "--resume" not in world.fake.calls("claude")[-1]["argv"]
    assert world.services.executors.retire_session(first["session_id"], reason="context_threshold")["created"] is False
    assert len(events(world, "session.retired")) == 1


def test_measurement_is_only_at_the_next_attempt(world, monkeypatch):
    calls = []

    def refresh(task_id, *, model_id=None):
        calls.append(task_id)
        return {"enabled": True, "level": "threshold", "ratio": .8, "scope": "task:T1"}
    # Installed before the first attempt: neither run_task nor its watch may measure mid-attempt.
    monkeypatch.setattr(world.services.envelopes, "refresh_task_context", refresh)
    monkeypatch.setattr(world.services.envelopes, "get_context_snapshot", lambda scope: {"ref": "snapshot-test"})
    first = paused(world)
    assert calls == [] and not events(world, "session.retired")
    assert first["context_handoff"] is None
    world.services.executors.resume_task("T1")
    assert calls == ["T1"]


def test_retry_after_handoff_does_not_retire_or_checkpoint_again(world, monkeypatch):
    paused(world)
    measure(monkeypatch, world, ratio=.8)
    world.fake.set("claude", stdout=claude_out("no envelope here"))
    first = world.services.executors.resume_task("T1")
    assert first["mode"] == "launch" and first["outcome"] == "failed"
    second = world.services.executors.resume_task("T1")
    assert second["mode"] == "resume" and second["context_handoff"] is None
    assert len(events(world, "session.retired")) == 1
    assert len(events(world, "task.checkpointed")) == 1


@pytest.mark.parametrize("threshold,expected", [(.7, "launch"), (.8, "resume")])
def test_cmp_profile_threshold_changes_result(world, monkeypatch, threshold, expected):
    paused(world)
    measure(monkeypatch, world, ratio=.75, threshold=threshold)
    assert world.services.executors.resume_task("T1")["mode"] == expected


def test_disabled_profile_keeps_existing_resume(world, monkeypatch):
    first = paused(world)
    measure(monkeypatch, world, ratio=.9, enabled=False)
    again = world.services.executors.resume_task("T1")
    assert again["mode"] == "resume" and again["session_id"] == first["session_id"]
    assert again["context_handoff"] is None


def test_cancel_aborts_before_retiring(world, monkeypatch):
    paused(world)
    measure(monkeypatch, world, ratio=.8)
    monkeypatch.setattr(world.services.inbox, "require_no_cancel",
                        lambda scope, task_id: (_ for _ in ()).throw(ServiceError(
                            "invalid_state", "cancelled", status=409, details={"reason": "inbox_cancelled"})))
    with pytest.raises(ServiceError) as caught:
        world.services.executors.resume_task("T1")
    assert caught.value.details["reason"] == "inbox_cancelled"
    assert not events(world, "session.retired")


def test_measurement_failure_does_not_block_resume(world, monkeypatch, caplog):
    first = paused(world)
    measure(monkeypatch, world, ratio=.8, failure=True)
    again = world.services.executors.resume_task("T1")
    assert again["mode"] == "resume" and again["session_id"] == first["session_id"]
    assert "Context measurement" in caplog.text


def test_the_retired_event_records_the_snapshot_and_the_cursor(world, monkeypatch):
    first = paused(world)
    measure(monkeypatch, world, ratio=.71)
    monkeypatch.setattr(world.services.inbox, "cursor", lambda scope, scope_id: 7)
    handoff = world.services.executors.resume_task("T1")["context_handoff"]
    assert handoff["inbox_cursor"] == 7
    (retired,) = events(world, "session.retired")
    assert retired["ref"] == first["session_id"]
    payload = retired["payload"]
    assert payload["reason"] == "context_threshold" and payload["snapshot_ref"] == "snapshot-test"
    assert payload["inbox_cursor"] == 7 and payload["ratio"] == .71 and payload["level"] == "threshold"
    assert payload["checkpoint_seq"] == handoff["checkpoint"]["seq"]


def test_the_checkpoint_is_always_of_the_session_that_is_retired(world, monkeypatch):
    first = paused(world)
    other = str(uuid.uuid4())
    older = world.services.executors.checkpoint_limit("T1", session_id=other, attempt=1, record_limit=False)
    assert older["session_id"] == other
    measure(monkeypatch, world, ratio=.8)
    handoff = world.services.executors.resume_task("T1")["context_handoff"]
    assert handoff["checkpoint"]["session_id"] == first["session_id"] != other


def test_a_session_retired_while_measuring_is_not_replaced_twice(world, monkeypatch):
    first = paused(world)
    launches = len(world.fake.calls("claude"))

    def refresh(task_id, *, model_id=None):      # the other resume wins while this one measures
        world.services.executors.retire_session(first["session_id"], reason="handoff")
        return {"enabled": True, "level": "threshold", "ratio": .9, "scope": "task:T1"}
    monkeypatch.setattr(world.services.envelopes, "refresh_task_context", refresh)
    monkeypatch.setattr(world.services.envelopes, "get_context_snapshot", lambda scope: {"ref": "snapshot-test"})
    with pytest.raises(ServiceError) as caught:
        world.services.executors.resume_task("T1")
    assert caught.value.details.get("session_id") == first["session_id"]
    assert len(world.fake.calls("claude")) == launches
    assert len(events(world, "session.retired")) == 1


def test_a_session_opened_while_measuring_is_neither_checkpointed_nor_retired(world, monkeypatch):
    first = paused(world)

    def refresh(task_id, *, model_id=None):
        world.services.executors.record_app_open(first["session_id"], source="test")
        return {"enabled": True, "level": "threshold", "ratio": .9, "scope": "task:T1"}
    monkeypatch.setattr(world.services.envelopes, "refresh_task_context", refresh)
    monkeypatch.setattr(world.services.envelopes, "get_context_snapshot", lambda scope: {"ref": "snapshot-test"})
    with pytest.raises(ServiceError) as caught:
        world.services.executors.resume_task("T1")
    assert caught.value.code == "invalid_state" and "12 hours" in caught.value.message
    assert not events(world, "session.retired") and not events(world, "task.checkpointed")


def test_a_session_opened_just_before_retiring_is_not_retired(world, monkeypatch):
    first = paused(world)
    measure(monkeypatch, world, ratio=.9)
    launches = len(world.fake.calls("claude"))
    real = world.services.executors._require_launch_effects

    def effects(*args, **kwargs):
        real(*args, **kwargs)
        world.services.executors.record_app_open(first["session_id"], source="test")
    monkeypatch.setattr(world.services.executors, "_require_launch_effects", effects)
    with pytest.raises(ServiceError) as caught:
        world.services.executors.resume_task("T1")
    assert "12 hours" in caught.value.message
    assert not events(world, "session.retired") and len(world.fake.calls("claude")) == launches


def test_the_replacement_is_checked_before_the_session_is_retired(world, monkeypatch):
    paused(world)
    measure(monkeypatch, world, ratio=.9)

    def refuse(*args, **kwargs):
        raise ServiceError("invalid_state", "launch refused", status=409)
    monkeypatch.setattr(world.services.executors, "_preflight", refuse)
    with pytest.raises(ServiceError):
        world.services.executors.resume_task("T1")
    assert not events(world, "session.retired")


def test_a_failed_snapshot_lookup_leaves_the_task_resumable_twice(world, monkeypatch):
    first = paused(world)
    world.fake.set("claude", stdout=claude_out("no envelope here"))
    monkeypatch.setattr(world.services.envelopes, "refresh_task_context", lambda task_id, *, model_id=None: {
        "enabled": True, "level": "threshold", "ratio": .9, "scope": "task:T1"})

    def broken(scope):
        raise RuntimeError("synthetic lookup failure")
    monkeypatch.setattr(world.services.envelopes, "get_context_snapshot", broken)
    for _ in range(2):
        again = world.services.executors.resume_task("T1")
        assert again["mode"] == "resume" and again["session_id"] == first["session_id"]
    assert not events(world, "session.retired") and not events(world, "task.checkpointed")


def real_profile(world, tmp_path, window=50):
    """A real CMP ``tasks`` profile (small window) with a scripted local summarizer: nothing is stubbed."""
    models = {"sum_t1": model("sum-t1", window=8000, level="T1"), "sum_t2": model("sum-t2", window=8000, level="T2"),
              "step_main": model("step-main", window=3000)}
    document = {"models": models, "routing_profiles": {"profiles": {}, "assignments": {}},
                "context_profiles": {"profiles": {PROFILE: {"version": 1, "enabled": True,
                                                            "tasks": {"window_tokens": window}}},
                                     "assignments": {"tasks": PROFILE}}}
    folder = tmp_path / "cmp-catalog"
    folder.mkdir()
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (folder / "models.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    old = world.services.system.catalog
    catalog = Catalog(world.services.settings, catalog_dir=folder)
    catalog.executor_routing, catalog.executor_routing_valid = old.executor_routing, True
    adapter = Summarizer()
    world.services.system.catalog = catalog
    world.services.system.engine.router = ModelRouter(catalog, {"ollama": adapter})
    return catalog, adapter


def test_real_cmp_profile_hands_off_and_resumes_twice_without_a_stale_revision(world, tmp_path):
    first = paused(world)
    catalog, summarizer = real_profile(world, tmp_path)
    world.fake.set("claude", stdout=claude_out("no envelope here"))
    handoff_run = world.services.executors.resume_task("T1")
    handoff = handoff_run["context_handoff"]
    assert handoff["level"] == "threshold" and handoff["retired_session"] == first["session_id"]
    assert handoff["snapshot_ref"] and handoff["snapshot_ref"].startswith("blob:sha256:")
    assert handoff["checkpoint"]["session_id"] == first["session_id"]
    assert handoff_run["mode"] == "launch" and handoff_run["session_id"] != first["session_id"]
    for _ in range(2):      # the window still measures a threshold: the guard says None and the task is re-read
        again = world.services.executors.resume_task("T1")
        assert again["mode"] == "resume" and again["session_id"] == handoff_run["session_id"]
        assert again["context_handoff"] is None
    assert len(events(world, "session.retired")) == 1 and len(events(world, "task.checkpointed")) == 1
    assert summarizer.calls


def test_real_cmp_profile_threshold_is_the_only_threshold(world, tmp_path):
    first = paused(world)
    world.fake.set("claude", stdout=claude_out("no envelope here"))
    catalog, _ = real_profile(world, tmp_path, window=10_000_000)
    again = world.services.executors.resume_task("T1")
    assert again["mode"] == "resume" and again["session_id"] == first["session_id"]
    assert again["context_handoff"] is None and not events(world, "session.retired")
    catalog.context_profiles[PROFILE]["tasks"]["window_tokens"] = 50      # the profile's window, not a test constant
    assert world.services.executors.resume_task("T1")["context_handoff"]["level"] == "threshold"


def resumed_by_resume_due(tmp_path, monkeypatch, level, ratio):
    world = quota.make_world(tmp_path)
    services = world.services
    monkeypatch.setattr(services.envelopes, "refresh_task_context", lambda task_id, *, model_id=None: {
        "enabled": True, "scope": f"task:{task_id}", "level": level, "ratio": ratio})
    monkeypatch.setattr(services.envelopes, "get_context_snapshot", lambda scope: {"ref": "snapshot-due"})
    first = quota.hold_at_the_reset(world)
    world.clock.to(quota.at(4, 14, 32))
    (item,) = services.executors.resume_due()["resumed"]
    return world, first, item


def test_resume_due_keeps_its_shape_below_the_threshold(tmp_path, monkeypatch):
    world, first, item = resumed_by_resume_due(tmp_path, monkeypatch, "warning", .5)
    assert item["mode"] == "resume" and "context_handoff" not in item
    assert item["session_id"] == first["session_id"] and world.fake.calls[-1].kind == "resume"


def test_resume_due_relaunches_at_the_threshold(tmp_path, monkeypatch):
    world, first, item = resumed_by_resume_due(tmp_path, monkeypatch, "threshold", .9)
    assert item["mode"] == "relaunch" and item["session_id"] != first["session_id"]
    assert item["context_handoff"]["snapshot_ref"] == "snapshot-due"
    assert item["context_handoff"]["retired_session"] == first["session_id"]
    assert world.fake.calls[-1].kind == "launch"
    (retired,) = [e for e in world.services.tasks.events(task_id="T1") if e["type"] == "session.retired"]
    assert retired["payload"]["reason"] == "context_threshold"
