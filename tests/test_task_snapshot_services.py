# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json

import pytest

from pathlib import Path
from multiagent.config import Settings
from multiagent.services import ApplicationServices
from multiagent.services import ServiceError


@pytest.fixture
def tasks(tmp_path: Path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True).tasks


def bundle(*tasks):
    return {"groups": [{"id": "G", "title": "Imported", "authorized": True}],
            "tasks": list(tasks), "dependencies": []}


def task(task_id="G-T01", *, state="open", **extra):
    return {"id": task_id, "group_id": "G", "title": task_id, "state": state,
            "source_id": task_id.removeprefix("G-"), "source_state": state, "file_scope": [], **extra}


def envelope(data, key="orq4:test:a", ref="fixture://orq4/test"):
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"source_key": key, "source_ref": ref, "snapshot_sha256": hashlib.sha256(canonical.encode()).hexdigest()}


def test_import_is_atomic_idempotent_truthful_and_rollback_keeps_receipt(tasks):
    data = bundle(task("G-T01", state="submitted"))
    args = envelope(data)
    imported = tasks.import_snapshot(data, actor="user", **args)
    assert imported["state"] == "applied"
    assert tasks.import_snapshot(data, actor="user", **args) == imported
    row = tasks.get_task("G-T01")
    assert row["state"] == "submitted" and row["attempt"] == 0
    assert row["attempt_history"] == "unknown"
    assert row["provenance"]["source_state"] == "submitted"
    assert not [e for e in tasks.events() if e["type"] in ("task.accepted", "result.submitted", "gate.passed")]
    rolled = tasks.rollback_snapshot(args["source_key"], actor="user")
    assert rolled["state"] == "rolled_back"
    assert tasks.get_snapshot(args["source_key"])["state"] == "rolled_back"
    assert tasks.list_tasks() == []


@pytest.mark.parametrize("state", ["running", "accepted", "validating", "failed", "cancelled"])
def test_snapshot_rejects_states_that_claim_unavailable_history(tasks, state):
    data = bundle(task(state=state))
    with pytest.raises(ServiceError):
        tasks.plan_snapshot(data, actor="user", **envelope(data))
    assert tasks.list_groups() == []


def test_pause_epoch_and_lock_scope_escalation_revalidate(tasks):
    paused = task("G-T01", state="paused", assignee="agent:old", file_scope=["src/shared"],
                  lock_policy={"shared_scope": [], "workspace_scope": ["src/shared"]},
                  workspace_domain="workspace:one")
    data = bundle(paused)
    # A different workspace may claim the path while paused; resume promotes to global and must refuse.
    tasks.import_snapshot(data, actor="user", **envelope(data))
    tasks.create_task("G", "G-T02", "other workspace", actor="user", file_scope=["src/shared"])
    tasks.transition("G-T02", "claim", actor="agent:other", actor_domain="workspace:two")
    err = None
    try:
        tasks.transition("G-T01", "resume", actor="agent:old", actor_domain="workspace:one")
    except ServiceError as exc:
        err = exc
    assert err is not None and err.code == "invalid_state"
    assert tasks.get_task("G-T01")["attempt"] == 0


def test_imported_paused_task_starts_new_epoch_and_keeps_unknown_history(tasks):
    paused = task(state="paused", assignee="agent:a", file_scope=["src/a"],
                  lock_policy={"shared_scope": [], "workspace_scope": ["src/a"]},
                  workspace_domain="workspace:a")
    data = bundle(paused)
    tasks.import_snapshot(data, actor="user", **envelope(data, "orq4:test:resume"))
    resumed = tasks.transition("G-T01", "resume", actor="agent:a", actor_domain="workspace:a")
    assert resumed["state"] == "running" and resumed["attempt"] == 1
    assert resumed["attempt_history"] == "unknown"
    event = [e for e in tasks.events(task_id="G-T01") if e["type"] == "task.resumed"][-1]
    assert event["payload"]["epoch_start"] is True


def test_claim_and_running_locks_are_global_across_actor_domains(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    for task_id in ("G-T01", "G-T02"):
        tasks.create_task("G", task_id, task_id, actor="user", file_scope=["src/a"])
    tasks.transition("G-T01", "claim", actor="agent:a", actor_domain="workspace:one")
    tasks.transition("G-T01", "start", actor="agent:a", actor_domain="workspace:one")
    with pytest.raises(ServiceError) as caught:
        tasks.transition("G-T02", "claim", actor="agent:b", actor_domain="workspace:two")
    assert caught.value.details["conflicts"] == [{
        "task_id": "G-T01", "assignee": "agent:a", "status": "running", "paths": ["src/a"],
    }]


def test_needs_changes_snapshot_does_not_lock_until_start(tasks):
    change = task("G-T01", state="needs_changes", assignee="agent:a", file_scope=["src/a"],
                  lock_policy={"shared_scope": [], "workspace_scope": []})
    other = task("G-T02", state="claimed", assignee="agent:b", file_scope=["src/a"],
                 lock_policy={"shared_scope": ["src/a"], "workspace_scope": []})
    data = bundle(change, other)
    tasks.import_snapshot(data, actor="user", **envelope(data))
    with pytest.raises(ServiceError):
        tasks.transition("G-T01", "start", actor="agent:a")
    assert tasks.get_task("G-T01")["state"] == "needs_changes"


def test_snapshot_rollback_refuses_consumed_marker(tasks):
    data = bundle(task())
    args = envelope(data, "orq4:test:cursor")
    receipt = tasks.import_snapshot(data, actor="user", **args)
    from multiagent.db.envelope_store import EnvelopeStore
    store = EnvelopeStore(tasks.store.db)
    with tasks.store.transaction() as conn:
        store.set_cursor(conn, "test-consumer", "", receipt["event_seq"])
    with pytest.raises(ServiceError):
        tasks.rollback_snapshot(args["source_key"], actor="user")
    assert tasks.get_task("G-T01")["state"] == "open"


def test_snapshot_rollback_allows_unrelated_events_but_checks_baseline(tasks):
    data = bundle(task())
    args = envelope(data, "orq4:test:unrelated-event")
    receipt = tasks.import_snapshot(data, actor="user", **args)
    tasks.create_group("OTHER", "Unrelated", actor="user")
    rolled = tasks.rollback_snapshot(args["source_key"], actor="user")
    assert rolled["state"] == "rolled_back"
    assert tasks.get_group("OTHER")["id"] == "OTHER"

    next_data = bundle(task("G2-T01", group_id="G2"))
    next_data["groups"][0]["id"] = "G2"
    next_args = envelope(next_data, "orq4:test:baseline")
    tasks.import_snapshot(next_data, actor="user", **next_args)
    from multiagent.db.envelope_store import EnvelopeStore
    with tasks.store.transaction() as conn:
        EnvelopeStore(tasks.store.db).set_task_context(conn, "G2-T01", base_commit="1234567")
    with pytest.raises(ServiceError):
        tasks.rollback_snapshot(next_args["source_key"], actor="user")


def test_snapshot_rollback_refuses_exact_external_result_reference(tasks):
    tasks.create_group("OTHER", "External", actor="user")
    tasks.create_task("OTHER", "OTHER-T01", "External consumer", actor="user")
    with tasks.store.transaction() as conn:
        from multiagent.db.envelope_store import EnvelopeStore
        EnvelopeStore(tasks.store.db).set_task_context(
            conn, "OTHER-T01", context_refs=["result://G-T01/1"])
    data = bundle(task())
    args = envelope(data, "orq4:test:external-reference")
    tasks.import_snapshot(data, actor="user", **args)
    with pytest.raises(ServiceError) as caught:
        tasks.rollback_snapshot(args["source_key"], actor="user")
    assert caught.value.details["task_id"] == "OTHER-T01"
    assert tasks.get_task("G-T01")["state"] == "open"


def test_snapshot_plan_rejects_conflicting_active_locks(tasks):
    tasks.create_group("OLD", "Existing", actor="user", authorized=True)
    tasks.create_task("OLD", "OLD-T01", "Existing", actor="user", file_scope=["src/shared"])
    tasks.transition("OLD-T01", "claim", actor="agent:old")
    imported = task("NEW-T01", group_id="NEW", state="claimed", assignee="agent:new", file_scope=["src/shared"])
    data = {"groups": [{"id": "NEW", "title": "Imported", "authorized": True}],
            "tasks": [imported], "dependencies": []}
    with pytest.raises(ServiceError) as caught:
        tasks.plan_snapshot(data, actor="user", **envelope(data, "orq4:test:lock-conflict"))
    assert caught.value.details["conflicts"] == [{
        "task_id": "OLD-T01", "paths": ["src/shared"],
    }]


@pytest.mark.parametrize("reverse", [False, True])
def test_two_paused_mixed_lock_policies_conflict_across_domains(tasks, reverse):
    shared = task("G-T01", state="paused", assignee="agent:one", file_scope=["src/a"],
                  lock_policy={"shared_scope": ["src/a"], "workspace_scope": []},
                  workspace_domain="workspace:one")
    workspace = task("G-T02", state="paused", assignee="agent:two", file_scope=["src/a"],
                     lock_policy={"shared_scope": [], "workspace_scope": ["src/a"]},
                     workspace_domain="workspace:two")
    ordered = [workspace, shared] if reverse else [shared, workspace]
    data = bundle(*ordered)
    with pytest.raises(ServiceError) as caught:
        tasks.plan_snapshot(data, actor="user", **envelope(data, f"orq4:test:paused-mixed:{reverse}"))
    assert caught.value.details["conflicts"] == [{
        "task_id": "G-T01" if reverse else "G-T02", "paths": ["src/a"],
    }]


def test_two_paused_workspace_locks_can_coexist_across_domains(tasks):
    one = task("G-T01", state="paused", assignee="agent:one", file_scope=["src/a"],
               lock_policy={"shared_scope": [], "workspace_scope": ["src/a"]},
               workspace_domain="workspace:one")
    two = task("G-T02", state="paused", assignee="agent:two", file_scope=["src/a"],
               lock_policy={"shared_scope": [], "workspace_scope": ["src/a"]},
               workspace_domain="workspace:two")
    data = bundle(one, two)
    plan = tasks.plan_snapshot(data, actor="user", **envelope(data, "orq4:test:paused-workspaces"))
    assert plan["status"] == "planned"


def test_snapshot_b_capability_fails_closed(tasks):
    data = bundle(task())
    data["groups"][0]["legacy_release_mode"] = "probada"
    with pytest.raises(ServiceError) as caught:
        tasks.plan_snapshot(data, actor="user", **envelope(data))
    assert caught.value.details["required_capability"] == "release_dependencies/v1"


@pytest.mark.parametrize("field,value", [
    ("context_refs", ["unknown://bad"]),
    ("gate", ["invented-check"]),
])
def test_snapshot_validates_core_context_and_gate_fields(tasks, field, value):
    data = bundle(task(**{field: value}))
    with pytest.raises(ServiceError):
        tasks.plan_snapshot(data, actor="user", **envelope(data, "orq4:test:bad:" + field))
    assert tasks.list_groups() == []
