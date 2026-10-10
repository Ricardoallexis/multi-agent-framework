# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Gate store (migration 005): declared gates and stored check results, no gate rules."""
from __future__ import annotations

import sqlite3
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from multiagent.config import Settings
from multiagent.db import Database
from multiagent.db.gate_store import OUTPUT_EXCERPT_CHARS, GateStore
from multiagent.db.task_store import TaskStore


def _legacy_database(tmp_path: Path, through: int) -> tuple[Database, Path]:
    source = Path(__file__).parents[1] / "migrations"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    for path in sorted(source.glob("*.sql")):
        if int(path.stem.split("_", 1)[0]) <= through:
            shutil.copyfile(path, migrations_dir / path.name)
    db = Database(SimpleNamespace(db_path=tmp_path / "data" / "multiagent.db",
                                  migrations_dir=migrations_dir))
    return db, migrations_dir


def _enable_migration(migrations_dir: Path, name: str) -> None:
    source = Path(__file__).parents[1] / "migrations" / name
    shutil.copyfile(source, migrations_dir / name)


@pytest.fixture
def stores(tmp_path: Path) -> tuple[TaskStore, GateStore]:
    db = Database(Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db"))
    db.migrate()
    tasks = TaskStore(db)
    with tasks.transaction() as conn:
        tasks.insert_group(conn, "G", "Group", actor="user")
        tasks.insert_task(conn, "G-T01", "G", "Task", actor="user")
    return tasks, GateStore(db)


def result(gates: GateStore, conn, **overrides):
    values = {"task_id": "G-T01", "attempt": 1, "run": 1, "position": 1, "check_name": "command:test",
              "status": "passed", "actor": "user", "started_at": "t0", "ended_at": "t1"}
    values.update(overrides)
    return gates.insert_result(conn, **values)


def test_migration_005_adds_gates_without_touching_tasks(tmp_path: Path):
    db, migrations_dir = _legacy_database(tmp_path, 4)
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO task_groups(id,title,created_by,created_at,updated_at) VALUES('G','Group','user','t','t')")
        conn.execute("INSERT INTO tasks(id,group_id,title,created_by,created_at,updated_at) VALUES('G-T01','G','Task','user','t','t')")
        conn.commit()
    _enable_migration(migrations_dir, "005_gates.sql")
    assert db.migrate() == ["005_gates.sql"]
    assert db.schema_version() == 5
    with db.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        group_gate = conn.execute("SELECT default_gate_json FROM task_groups WHERE id='G'").fetchone()[0]
        task = conn.execute("SELECT title,gate_json FROM tasks WHERE id='G-T01'").fetchone()
    assert "gate_results" in tables
    assert group_gate == "[]"
    assert (task["title"], task["gate_json"]) == ("Task", "[]")


def test_task_and_group_gates_are_stored(stores):
    tasks, gates = stores
    with tasks.transaction() as conn:
        assert gates.set_task_gate(conn, "G-T01", ["command:test", "review"]) is True
        assert gates.set_group_gate(conn, "G", ["command:test"]) is True
        assert gates.set_task_gate(conn, "NOPE", []) is False
        assert gates.set_group_gate(conn, "NOPE", []) is False
    assert gates.task_gate("G-T01") == ["command:test", "review"]
    assert gates.group_gate("G") == ["command:test"]
    assert tasks.get_task("G-T01")["gate"] == ["command:test", "review"]
    assert tasks.get_group("G")["default_gate"] == ["command:test"]
    assert tasks.get_task("G-T01")["revision"] == 1  # the services decide when to bump it
    assert gates.task_gate("NOPE") is None and gates.group_gate("NOPE") is None


def test_results_are_numbered_by_attempt_run_and_position(stores):
    tasks, gates = stores
    assert gates.next_run("G-T01", 1) == 1
    with tasks.transaction() as conn:
        result(gates, conn, position=1)
        result(gates, conn, position=2, check_name="review", status="pending")
        result(gates, conn, run=2, position=1, status="failed", command="test", exit_code=3, duration_ms=12)
        result(gates, conn, attempt=2, position=1)
    assert gates.next_run("G-T01", 1) == 3
    assert gates.next_run("G-T01", 2) == 2
    rows = gates.results("G-T01")
    assert [(r["attempt"], r["run"], r["position"]) for r in rows] == [(1, 1, 1), (1, 1, 2), (1, 2, 1), (2, 1, 1)]
    failed = rows[2]
    assert (failed["status"], failed["command"], failed["exit_code"], failed["duration_ms"]) == ("failed", "test", 3, 12)
    assert [(r["run"], r["position"]) for r in gates.results("G-T01", attempt=1)] == [(1, 1), (1, 2), (2, 1)]
    assert [(r["attempt"], r["run"]) for r in gates.last_run("G-T01")] == [(2, 1)]
    assert gates.last_run("NOPE") == []


def test_output_keeps_only_the_last_characters(stores):
    tasks, gates = stores
    output = "x" * 10 + "y" * OUTPUT_EXCERPT_CHARS
    with tasks.transaction() as conn:
        row = result(gates, conn, output=output)
    assert row["output_excerpt"] == "y" * OUTPUT_EXCERPT_CHARS


def test_invalid_results_are_rejected(stores):
    tasks, gates = stores
    with pytest.raises(ValueError):
        with tasks.transaction() as conn:
            result(gates, conn, status="done")
    with pytest.raises(sqlite3.IntegrityError):
        with tasks.transaction() as conn:
            result(gates, conn)
            result(gates, conn)  # same task, attempt, run and position
    with pytest.raises(sqlite3.IntegrityError):
        with tasks.transaction() as conn:
            result(gates, conn, task_id="NOPE")
    assert gates.results("G-T01") == []


def test_migration_005_keeps_existing_tasks(tmp_path: Path):
    db, migrations_dir = _legacy_database(tmp_path, 4)
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO task_groups(id,title,created_by,created_at,updated_at) VALUES('G','Old','user','t','t')")
        conn.commit()
    _enable_migration(migrations_dir, "005_gates.sql")
    assert db.migrate() == ["005_gates.sql"]
    assert db.schema_version() == 5
    with db.connect() as conn:
        group = conn.execute("SELECT title,default_gate_json FROM task_groups WHERE id='G'").fetchone()
    assert tuple(group) == ("Old", "[]")
