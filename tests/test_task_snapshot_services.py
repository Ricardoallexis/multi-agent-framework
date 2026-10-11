# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
import shutil

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
    data = bundle(task("G-T01", state="submitted", delivery={
        "delivery_ref": "report://verified-delivery", "delivery_sha256": "a" * 64,
        "has_patch": False, "patchset_sha256": "", "test_verdict": None,
        "test_patchset_sha256": "", "test_ref": "", "integration_commit": "", "integration_ref": "",
    }))
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


def test_snapshot_b_imports_release_policy_edges_and_current_delivery_atomically(tasks):
    data = bundle(task("G-T01", state="submitted", delivery={
        "delivery_ref": "report://delivery-G-T01", "delivery_sha256": "a" * 64,
        "has_patch": False, "patchset_sha256": "", "test_verdict": None,
        "test_patchset_sha256": "", "test_ref": "", "integration_commit": "", "integration_ref": "",
    }), task("G-T02"))
    data["groups"][0]["legacy_release_mode"] = "probada"
    data["dependencies"] = [{"task_id": "G-T02", "depends_on_id": "G-T01",
                             "kind": "legacy_release", "source_ref": "doc://fixture/release.md"}]
    args = envelope(data, "orq4:test:legacy-snapshot")
    plan = tasks.plan_snapshot(data, actor="user", **args)
    assert plan["status"] == "planned"
    imported = tasks.import_snapshot(data, actor="user", **args)
    assert imported["state"] == "applied"
    assert tasks.get_task("G-T02")["status"] == "ready"
    assert tasks.get_release_facts("G-T01")["has_patch"] is False
    rolled = tasks.rollback_snapshot(args["source_key"], actor="user")
    assert rolled["state"] == "rolled_back"
    assert tasks.list_tasks() == []


def test_schema_007_snapshot_a_receipt_remains_idempotent_and_rollbackable_after_008(tmp_path, monkeypatch):
    from multiagent.db.database import utcnow
    from multiagent.config import Settings

    project = Path(__file__).resolve().parents[1]
    legacy_migrations = tmp_path / "migrations-007"
    legacy_migrations.mkdir()
    for path in (project / "migrations").glob("00[1-7]_*.sql"):
        shutil.copy2(path, legacy_migrations / path.name)
    current_migrations = tmp_path / "migrations-008"
    current_migrations.mkdir()
    for path in (project / "migrations").glob("00[1-8]_*.sql"):
        shutil.copy2(path, current_migrations / path.name)
    local_dir = tmp_path / "upgrade-workspace"
    monkeypatch.setattr(Settings, "migrations_dir", property(lambda _self: legacy_migrations))
    old_settings = Settings(_env_file=None, local_dir=local_dir, worker_enabled=False,
                            gemini_api_key="", openai_api_key="", openai_enabled=False)
    old_services = ApplicationServices.create(old_settings, dry_run=True)
    assert old_services.system.db.schema_version() == 7

    snapshot = {
        "groups": [{"id": "G", "title": "A-era group", "authorized": True,
                    "source_id": "legacy-g", "source_state": "active"}],
        "tasks": [
            task("G-T00", state="open", source_id="T00", source_state="available"),
            task("G-T01", state="submitted", source_id="T01", source_state="terminated"),
        ],
        "dependencies": [],
    }
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    source_key, source_ref, actor = "orq4:compat:a007", "fixture://orq4/a007", "user"
    timestamp = utcnow()
    with old_services.system.db.tx() as conn:
        conn.execute("INSERT INTO task_groups(id,title,authorized,default_requires,created_by,created_at,updated_at) "
                     "VALUES(?,?,?,?,?,?,?)", ("G", "A-era group", 1, "accepted", actor, timestamp, timestamp))
        for task_id, title, state in (("G-T00", "G-T00", "open"), ("G-T01", "G-T01", "submitted")):
            conn.execute("INSERT INTO tasks(id,group_id,title,state,created_by,created_at,updated_at,lock_policy_json) "
                         "VALUES(?,?,?,?,?,?,?,?)", (task_id, "G", title, state, actor, timestamp, timestamp,
                                                       '{"shared_scope":[],"workspace_scope":[]}'))
        cursor = conn.execute("INSERT INTO team_events(type,group_id,actor,ref,payload_json,created_at) "
                              "VALUES('snapshot.imported','G',?,?,?,?)",
                              (actor, source_ref, json.dumps({"source_key": source_key,
                               "snapshot_sha256": digest}, separators=(",", ":")), timestamp))
        event_seq = int(cursor.lastrowid)
        conn.execute("INSERT INTO task_snapshots(source_key,source_ref,snapshot_sha256,actor,snapshot_json,imported_at,event_seq) "
                     "VALUES(?,?,?,?,?,?,?)", (source_key, source_ref, digest, actor, canonical, timestamp, event_seq))
        conn.executemany("INSERT INTO task_snapshot_objects(source_key,object_type,object_id,source_id,source_state) "
                         "VALUES(?,?,?,?,?)", [
            (source_key, "group", "G", "legacy-g", "active"),
            (source_key, "task", "G-T00", "T00", "available"),
            (source_key, "task", "G-T01", "T01", "terminated"),
        ])

    monkeypatch.setattr(Settings, "migrations_dir", property(lambda _self: current_migrations))
    upgraded_settings = Settings(_env_file=None, local_dir=local_dir, worker_enabled=False,
                                 gemini_api_key="", openai_api_key="", openai_enabled=False)
    upgraded = ApplicationServices.create(upgraded_settings, dry_run=True)
    assert upgraded.system.db.schema_version() == 8
    assert upgraded.system.db.migrate() == []
    assert upgraded.tasks.get_snapshot(source_key)["state"] == "applied"
    prior_events = upgraded.tasks.events()
    duplicate = upgraded.tasks.import_snapshot(snapshot, source_key=source_key, source_ref=source_ref,
                                               snapshot_sha256=digest, actor=actor)
    assert duplicate["state"] == "applied"
    assert upgraded.tasks.events() == prior_events
    assert upgraded.tasks.get_task("G-T01")["state"] == "submitted"
    assert upgraded.tasks.get_release_facts("G-T01")["delivery_sha256"] == ""
    rolled = upgraded.tasks.rollback_snapshot(source_key, actor=actor)
    assert rolled["state"] == "rolled_back"
    assert upgraded.tasks.get_snapshot(source_key)["state"] == "rolled_back"
    assert upgraded.tasks.list_tasks() == []
    assert upgraded.tasks.list_groups() == []


def _one_task_b_snapshot(task_id: str, *, state: str = "open", delivery: dict | None = None):
    item = task(task_id, state=state)
    if delivery is not None:
        item["delivery"] = delivery
    return {"groups": [{"id": "G", "title": "B snapshot", "authorized": True,
                        "legacy_release_mode": "probada"}],
            "tasks": [item], "dependencies": []}


def _snapshot_args(data: dict, key: str):
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"source_key": key, "source_ref": f"fixture://{key}",
            "snapshot_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def test_snapshot_b_rollback_refuses_mutated_release_facts(tasks):
    delivery = {"delivery_ref": "report://imported", "delivery_sha256": "a" * 64,
                "has_patch": False, "patchset_sha256": "", "test_verdict": None,
                "test_patchset_sha256": "", "test_ref": "", "integration_commit": "", "integration_ref": ""}
    data = _one_task_b_snapshot("G-T01", state="submitted", delivery=delivery)
    args = _snapshot_args(data, "orq4:test:b-mutated-facts")
    tasks.import_snapshot(data, actor="user", **args)
    tasks.record_delivery_fact("G-T01", delivery_ref="report://changed", delivery_sha256="b" * 64,
                               has_patch=False, patchset_sha256="", actor="user")
    with pytest.raises(ServiceError):
        tasks.rollback_snapshot(args["source_key"], actor="user")
    assert tasks.get_snapshot(args["source_key"])["state"] == "applied"
    assert tasks.get_task("G-T01")["state"] == "submitted"


def test_snapshot_b_rollback_refuses_external_legacy_dependency(tasks):
    tasks.create_group("OTHER", "External", actor="user", authorized=True)
    tasks.create_task("OTHER", "OTHER-T01", "External dependent", actor="user")
    data = _one_task_b_snapshot("G-T01")
    args = _snapshot_args(data, "orq4:test:b-external-edge")
    tasks.import_snapshot(data, actor="user", **args)
    tasks.add_release_dependency("OTHER-T01", "G-T01", actor="user", source_ref="doc://fixture/external.md")
    with pytest.raises(ServiceError) as caught:
        tasks.rollback_snapshot(args["source_key"], actor="user")
    assert "external legacy release dependents" in caught.value.message
    assert tasks.get_task("G-T01")["id"] == "G-T01"


def test_snapshot_b_rollback_checks_exact_external_release_result_refs(tasks):
    tasks.create_group("OTHER", "External", actor="user", authorized=True)
    tasks.create_task("OTHER", "OTHER-T01", "External task", actor="user")
    data = _one_task_b_snapshot("G-T01")
    args = _snapshot_args(data, "orq4:test:b-similar-reference")
    tasks.import_snapshot(data, actor="user", **args)
    tasks.record_delivery_fact("OTHER-T01", delivery_ref="result://G-T010/1", delivery_sha256="a" * 64,
                               has_patch=False, patchset_sha256="", actor="user")
    assert tasks.rollback_snapshot(args["source_key"], actor="user")["state"] == "rolled_back"

    exact = _one_task_b_snapshot("H-T01")
    exact_args = _snapshot_args(exact, "orq4:test:b-exact-reference")
    tasks.import_snapshot(exact, actor="user", **exact_args)
    tasks.record_delivery_fact("OTHER-T01", delivery_ref="result://H-T01/1", delivery_sha256="b" * 64,
                               has_patch=False, patchset_sha256="", actor="user")
    with pytest.raises(ServiceError) as caught:
        tasks.rollback_snapshot(exact_args["source_key"], actor="user")
    assert caught.value.details["task_id"] == "OTHER-T01"
    assert tasks.get_task("H-T01")["id"] == "H-T01"


@pytest.mark.parametrize("field,value", [
    ("context_refs", ["unknown://bad"]),
    ("gate", ["invented-check"]),
])
def test_snapshot_validates_core_context_and_gate_fields(tasks, field, value):
    data = bundle(task(**{field: value}))
    with pytest.raises(ServiceError):
        tasks.plan_snapshot(data, actor="user", **envelope(data, "orq4:test:bad:" + field))
    assert tasks.list_groups() == []


def test_snapshot_v2_lifecycle_is_optional_idempotent_and_rollback_safe(tasks):
    data = {"groups": [{"id": "G", "title": "Imported", "authorized": True,
                        "lifecycle_state": "closed"}],
            "tasks": [task("G-T01")], "dependencies": []}
    args = envelope(data, "orq4:test:closed-lifecycle")
    receipt = tasks.import_snapshot(data, actor="user", **args)
    group = tasks.get_group("G")
    assert group["lifecycle_state"] == "closed" and group["revision"] == 1
    assert tasks.import_snapshot(data, actor="user", **args) == receipt
    rolled = tasks.rollback_snapshot(args["source_key"], actor="user")
    assert rolled["state"] == "rolled_back" and tasks.list_groups() == []


def test_snapshot_v2_omitted_lifecycle_defaults_open(tasks):
    data = bundle(task())
    args = envelope(data, "orq4:test:open-lifecycle")
    tasks.import_snapshot(data, actor="user", **args)
    group = tasks.get_group("G")
    assert group["lifecycle_state"] == "open" and group["revision"] == 1
    assert tasks.rollback_snapshot(args["source_key"], actor="user")["state"] == "rolled_back"


def test_pre_lifecycle_v1_receipt_replays_and_rolls_back(tasks):
    data = bundle(task())
    args = envelope(data, "orq4:test:legacy-v1-receipt")
    receipt = tasks.import_snapshot(data, actor="user", **args)
    # Represent a persisted receipt created before contract v2/lifecycle fields.
    with tasks.store.transaction() as conn:
        conn.execute("UPDATE task_snapshots SET contract_version=1 WHERE source_key=?",
                     (args["source_key"],))
        # Version 1 predates the release mode column, so its migrated value is null.
        conn.execute("UPDATE task_groups SET legacy_release_mode=NULL WHERE id='G'")
        conn.execute("UPDATE task_groups SET revision=1 WHERE id='G'")
    assert tasks.import_snapshot(data, actor="user", **args) == receipt
    assert tasks.rollback_snapshot(args["source_key"], actor="user")["state"] == "rolled_back"
