# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Task store (migration 004): persistence only, no transition rules."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from multiagent.config import Settings
from multiagent.db import Database
from multiagent.db.task_store import MAX_EVENT_PAYLOAD_BYTES, StaleRevision, TaskStore


@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    db = Database(Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db"))
    db.migrate()
    return TaskStore(db)


def test_migration_004_adds_the_task_tables(store: TaskStore):
    assert store.db.schema_version() >= 4
    with store.db.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"task_groups", "tasks", "task_deps", "team_events"} <= tables
    assert {"runs", "run_events", "telemetry"} <= tables


def test_groups_are_created_read_and_updated(store: TaskStore):
    with store.transaction() as conn:
        group = store.insert_group(conn, "ORQ", "Orchestration", actor="user")
    assert group["authorized"] is False and group["default_requires"] == "accepted"
    assert group["default_gate"] == [] and "default_gate_json" not in group
    assert group["created_by"] == "user"
    with store.transaction() as conn:
        updated = store.update_group(conn, "ORQ", authorized=True, default_requires="submitted")
        assert store.update_group(conn, "NOPE", title="x") is None
    assert updated["authorized"] is True and updated["default_requires"] == "submitted"
    assert updated["title"] == "Orchestration"
    assert [g["id"] for g in store.list_groups()] == ["ORQ"]
    assert store.get_group("NOPE") is None


def _group_with_tasks(store: TaskStore, *ids: str) -> None:
    with store.transaction() as conn:
        store.insert_group(conn, "G", "Group", actor="user")
        for task_id in ids:
            store.insert_task(conn, task_id, "G", f"Task {task_id}", actor="user",
                              file_scope=[f"src/{task_id}.py"], requirements=["python"])


def test_tasks_decode_json_and_keep_creation_order(store: TaskStore):
    _group_with_tasks(store, "G-T02", "G-T01")
    task = store.get_task("G-T02")
    assert task["state"] == "open" and task["revision"] == 1 and task["attempt"] == 0
    assert task["file_scope"] == ["src/G-T02.py"] and task["requirements"] == ["python"]
    assert task["gate"] == [] and task["execution"] == {} and task["exclusive_resources"] == []
    assert "file_scope_json" not in task
    assert [t["id"] for t in store.list_tasks()] == ["G-T02", "G-T01"]
    assert store.get_task("NOPE") is None


def test_list_tasks_filters_by_group_state_and_assignee(store: TaskStore):
    _group_with_tasks(store, "G-T01", "G-T02", "G-T03")
    with store.transaction() as conn:
        store.update_task(conn, "G-T02", expected_revision=1, state="claimed", assignee="agent:a")
    assert [t["id"] for t in store.list_tasks(states=["claimed"])] == ["G-T02"]
    assert [t["id"] for t in store.list_tasks(assignee="agent:a")] == ["G-T02"]
    assert [t["id"] for t in store.list_tasks(group_id="G", states=["open"])] == ["G-T01", "G-T03"]
    assert store.list_tasks(states=[]) == []
    assert store.list_tasks(group_id="OTHER") == []


def test_update_task_bumps_revision_and_rejects_a_stale_one(store: TaskStore):
    _group_with_tasks(store, "G-T01")
    with store.transaction() as conn:
        task = store.update_task(conn, "G-T01", expected_revision=1, state="running", assignee="agent:a", attempt=1)
    assert (task["state"], task["assignee"], task["attempt"], task["revision"]) == ("running", "agent:a", 1, 2)
    with pytest.raises(StaleRevision) as stale:
        with store.transaction() as conn:
            store.update_task(conn, "G-T01", expected_revision=1, state="paused")
    assert (stale.value.expected, stale.value.current) == (1, 2)
    assert store.get_task("G-T01")["state"] == "running"
    with store.transaction() as conn:
        assert store.update_task(conn, "NOPE", expected_revision=1, state="paused") is None
        bumped = store.update_task(conn, "G-T01", expected_revision=2)
    assert bumped["revision"] == 3 and bumped["state"] == "running"


def test_dependencies_are_stored_filtered_and_removed(store: TaskStore):
    _group_with_tasks(store, "G-T01", "G-T02", "G-T03")
    with store.transaction() as conn:
        store.insert_dependency(conn, "G-T02", "G-T01")
        store.insert_dependency(conn, "G-T03", "G-T01", "submitted")
        store.insert_dependency(conn, "G-T03", "G-T02")
    assert store.dependencies(task_id="G-T03") == [
        {"task_id": "G-T03", "depends_on_id": "G-T01", "requires": "submitted"},
        {"task_id": "G-T03", "depends_on_id": "G-T02", "requires": "accepted"},
    ]
    assert [d["task_id"] for d in store.dependencies(depends_on_id="G-T01")] == ["G-T02", "G-T03"]
    with store.transaction() as conn:
        assert store.delete_dependency(conn, "G-T03", "G-T02") is True
        assert store.delete_dependency(conn, "G-T03", "G-T02") is False
    assert len(store.dependencies()) == 2


def test_the_schema_rejects_self_dependencies_and_unknown_references(store: TaskStore):
    _group_with_tasks(store, "G-T01")
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as conn:
            store.insert_dependency(conn, "G-T01", "G-T01")
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as conn:
            store.insert_dependency(conn, "G-T01", "NOPE")
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as conn:
            store.insert_task(conn, "X-T01", "NOPE", "Orphan", actor="user")
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as conn:
            store.insert_task(conn, "G-T01", "G", "Duplicate", actor="user")


def test_events_have_a_team_wide_increasing_seq_and_filters(store: TaskStore):
    _group_with_tasks(store, "G-T01")
    with store.transaction() as conn:
        store.insert_group(conn, "H", "Other", actor="user")
        first = store.append_event(conn, "group.created", actor="user", group_id="G")
        second = store.append_event(conn, "task.created", actor="user", group_id="G", task_id="G-T01",
                                    payload={"status": "ready"})
        third = store.append_event(conn, "group.created", actor="user", group_id="H")
    assert first < second < third
    events = store.events()
    assert [e["seq"] for e in events] == [first, second, third]
    assert events[1]["payload"] == {"status": "ready"} and "payload_json" not in events[1]
    assert [e["seq"] for e in store.events(after=first)] == [second, third]
    assert [e["seq"] for e in store.events(group_id="G")] == [first, second]
    assert [e["seq"] for e in store.events(task_id="G-T01")] == [second]
    assert [e["seq"] for e in store.events(limit=1)] == [first]


def test_event_payload_is_limited_to_2048_bytes(store: TaskStore):
    with store.transaction() as conn:
        store.append_event(conn, "task.created", actor="user", payload={"x": "a" * 1990})
    with pytest.raises(ValueError):
        with store.transaction() as conn:
            store.append_event(conn, "task.created", actor="user", payload={"x": "é" * MAX_EVENT_PAYLOAD_BYTES})
    assert len(store.events()) == 1


def test_a_failed_transaction_keeps_neither_the_change_nor_its_event(store: TaskStore):
    _group_with_tasks(store, "G-T01")
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            store.update_task(conn, "G-T01", expected_revision=1, state="claimed", assignee="agent:a")
            store.append_event(conn, "task.claimed", actor="agent:a", group_id="G", task_id="G-T01")
            raise RuntimeError("boom")
    task = store.get_task("G-T01")
    assert (task["state"], task["revision"]) == ("open", 1)
    assert store.events() == []
    with store.transaction() as conn:
        seq = store.append_event(conn, "task.claimed", actor="agent:a", group_id="G", task_id="G-T01")
    assert [e["seq"] for e in store.events()] == [seq]


def test_migration_keeps_existing_data(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db")
    db = Database(settings)
    migrations = sorted(settings.migrations_dir.glob("*.sql"))
    with db.connect() as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
        for version, path in enumerate(migrations[:3], start=1):
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version(version,name,applied_at) VALUES(?,?,'old')", (version, path.name))
        conn.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Old Project','old')")
        conn.commit()
    assert db.migrate()[0] == "004_tasks.sql"
    with db.connect() as conn:
        assert conn.execute("SELECT name FROM projects WHERE id='p'").fetchone()[0] == "Old Project"
    assert db.schema_version() >= 4
