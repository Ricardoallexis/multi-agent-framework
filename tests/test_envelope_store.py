# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Envelope store (migration 006): task context, results, blobs and cursors, no envelope rules."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from multiagent.config import Settings
from multiagent.db import Database
from multiagent.db import envelope_store
from multiagent.db.envelope_store import EnvelopeStore
from multiagent.db.task_store import TaskStore


@pytest.fixture
def stores(tmp_path: Path) -> tuple[TaskStore, EnvelopeStore]:
    db = Database(Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db"))
    db.migrate()
    tasks = TaskStore(db)
    with tasks.transaction() as conn:
        tasks.insert_group(conn, "G", "Group", actor="user")
        tasks.insert_task(conn, "G-T01", "G", "Task", actor="user")
    return tasks, EnvelopeStore(db)


def canonical(value) -> tuple[str, str]:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def add_result(tasks, envelopes, conn=None, *, attempt=1, patch="", body=None, task_id="G-T01"):
    text, digest = canonical(body or {"summary": "done", "attempt": attempt})
    kwargs = dict(task_id=task_id, attempt=attempt, agent="agent:a", result_json=text, result_sha256=digest,
                  patch_sha256=patch)
    if conn is not None:
        return envelopes.insert_result(conn, **kwargs)
    with tasks.transaction() as own:
        return envelopes.insert_result(own, **kwargs)


def test_migration_006_adds_the_tables_and_defaults_without_touching_tasks(stores):
    tasks, envelopes = stores
    assert envelopes.db.schema_version() == 6
    with envelopes.db.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"blobs", "task_results", "event_cursors"} <= tables
    assert envelopes.task_context("G-T01") == {"base_commit": "", "context_refs": [], "constraints": []}
    task = tasks.get_task("G-T01")
    assert (task["state"], task["revision"], task["gate"]) == ("open", 1, [])


def test_migration_006_keeps_existing_tasks(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db")
    db = Database(settings)
    migrations = sorted(settings.migrations_dir.glob("*.sql"))
    with db.connect() as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
        for version, path in enumerate(migrations[:5], start=1):
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version(version,name,applied_at) VALUES(?,?,'old')", (version, path.name))
        conn.execute("INSERT INTO task_groups(id,title,created_by,created_at,updated_at) VALUES('G','Old','user','t','t')")
        conn.execute("INSERT INTO tasks(id,group_id,title,created_by,created_at,updated_at) "
                     "VALUES('G-T01','G','Old task','user','t','t')")
        conn.commit()
    assert db.migrate() == ["006_envelopes.sql"]
    assert EnvelopeStore(db).task_context("G-T01") == {"base_commit": "", "context_refs": [], "constraints": []}
    assert TaskStore(db).get_task("G-T01")["title"] == "Old task"


def test_task_context_is_stored_and_does_not_bump_the_revision(stores):
    tasks, envelopes = stores
    with tasks.transaction() as conn:
        assert envelopes.set_task_context(conn, "G-T01", base_commit="2263537",
                                          context_refs=["doc://docs/A.md@2263537"], constraints=["no push"]) is True
        assert envelopes.set_task_context(conn, "NOPE", base_commit="abc1234") is False
    assert envelopes.task_context("G-T01") == {"base_commit": "2263537", "context_refs": ["doc://docs/A.md@2263537"],
                                               "constraints": ["no push"]}
    with tasks.transaction() as conn:  # only the given fields change
        envelopes.set_task_context(conn, "G-T01", constraints=[])
    assert envelopes.task_context("G-T01") == {"base_commit": "2263537", "context_refs": ["doc://docs/A.md@2263537"],
                                               "constraints": []}
    assert tasks.get_task("G-T01")["revision"] == 1  # the services decide when to bump it
    assert envelopes.task_context("NOPE") is None


def test_a_blob_is_stored_once_by_content_and_read_by_ranges(stores):
    tasks, envelopes = stores
    content = b"diff --git a/x b/x\n" + b"+line\n" * 50
    digest = hashlib.sha256(content).hexdigest()
    with tasks.transaction() as conn:
        row, created = envelopes.put_blob(conn, content, actor="agent:a", media_type="text/x-patch")
    assert created is True
    assert (row["sha256"], row["size"], row["media_type"], row["created_by"]) == (digest, len(content), "text/x-patch", "agent:a")
    path = envelopes.blob_path(digest)
    assert path == envelopes.blobs_dir / digest[:2] / digest and path.read_bytes() == content
    with tasks.transaction() as conn:  # the same content again: nothing new, the first metadata stays
        again, created_again = envelopes.put_blob(conn, content, actor="agent:b", media_type="text/plain")
    assert created_again is False and again["created_by"] == "agent:a" and again["media_type"] == "text/x-patch"
    assert envelopes.read_blob(digest) == content
    assert envelopes.read_blob(digest, offset=5, length=10) == content[5:15]
    assert envelopes.read_blob(digest, offset=len(content) + 10) == b""
    assert envelopes.get_blob(digest)["size"] == len(content) and envelopes.get_blob("0" * 64) is None


def test_an_empty_blob_and_the_size_cap(stores, monkeypatch):
    tasks, envelopes = stores
    with tasks.transaction() as conn:
        row, created = envelopes.put_blob(conn, b"", actor="agent:a")
    assert (row["size"], created) == (0, True) and envelopes.read_blob(row["sha256"]) == b""
    monkeypatch.setattr(envelope_store, "HARD_MAX_BLOB_BYTES", 10)
    with pytest.raises(ValueError):
        with tasks.transaction() as conn:
            envelopes.put_blob(conn, b"x" * 11, actor="agent:a")
    with tasks.transaction() as conn:
        assert envelopes.put_blob(conn, b"x" * 10, actor="agent:a")[1] is True


def test_a_file_left_by_a_rolled_back_transaction_is_adopted(stores):
    tasks, envelopes = stores
    content = b"left behind"
    digest = hashlib.sha256(content).hexdigest()
    with pytest.raises(RuntimeError):
        with tasks.transaction() as conn:
            envelopes.put_blob(conn, content, actor="agent:a")
            raise RuntimeError("the caller failed after storing the blob")
    assert envelopes.get_blob(digest) is None and envelopes.blob_path(digest).is_file()  # file yes, row no
    with tasks.transaction() as conn:
        row, created = envelopes.put_blob(conn, content, actor="agent:b")
    assert created is True and row["created_by"] == "agent:b" and envelopes.get_blob(digest) is not None
    envelopes.blob_path(digest).unlink()  # a row whose file vanished is rewritten, not trusted
    with tasks.transaction() as conn:
        envelopes.put_blob(conn, content, actor="agent:c")
    assert envelopes.read_blob(digest) == content
    with pytest.raises(FileNotFoundError):
        envelopes.read_blob("f" * 64)


def test_results_are_keyed_by_task_attempt_and_patch(stores):
    tasks, envelopes = stores
    patch = "a" * 64
    first = add_result(tasks, envelopes, attempt=1, patch=patch, body={"summary": "one"})
    add_result(tasks, envelopes, attempt=1, patch="", body={"summary": "no patch"})
    add_result(tasks, envelopes, attempt=2, patch=patch, body={"summary": "two"})
    assert first["result"] == {"summary": "one"} and first["patch_sha256"] == patch and first["agent"] == "agent:a"
    assert envelopes.find_result("G-T01", 1, patch)["result"] == {"summary": "one"}
    assert envelopes.find_result("G-T01", 1)["result"] == {"summary": "no patch"}
    assert envelopes.find_result("G-T01", 3) is None
    assert [(r["attempt"], r["patch_sha256"] == patch) for r in envelopes.results("G-T01")] == [(2, True), (1, False), (1, True)]
    assert [r["result"]["summary"] for r in envelopes.results("G-T01", attempt=1)] == ["no patch", "one"]
    assert envelopes.latest_result("G-T01")["attempt"] == 2 and envelopes.latest_result("NOPE") is None


def test_invalid_results_are_rejected(stores):
    tasks, envelopes = stores
    text, digest = canonical({"summary": "x"})
    with pytest.raises(ValueError):
        with tasks.transaction() as conn:
            envelopes.insert_result(conn, task_id="G-T01", attempt=1, agent="a", result_json=text, result_sha256="0" * 64)
    add_result(tasks, envelopes, attempt=1)
    with pytest.raises(sqlite3.IntegrityError):  # the same task, attempt and patch
        add_result(tasks, envelopes, attempt=1, body={"summary": "other"})
    with pytest.raises(sqlite3.IntegrityError):  # unknown task
        add_result(tasks, envelopes, task_id="NOPE")
    assert [r["result"]["summary"] for r in envelopes.results("G-T01")] == ["done"]


def test_cursors_are_per_consumer_and_scope_and_never_go_backwards(stores):
    tasks, envelopes = stores
    assert envelopes.get_cursor("agent:a") == 0
    with tasks.transaction() as conn:
        assert envelopes.set_cursor(conn, "agent:a", "", 5) == 5
        assert envelopes.set_cursor(conn, "agent:a", "", 3) == 5  # never backwards
        assert envelopes.set_cursor(conn, "agent:a", "", 5) == 5
        assert envelopes.set_cursor(conn, "agent:a", "group:G", 2) == 2
        assert envelopes.set_cursor(conn, "agent:b", "", 9) == 9
    assert (envelopes.get_cursor("agent:a"), envelopes.get_cursor("agent:a", "group:G"), envelopes.get_cursor("agent:b")) == (5, 2, 9)
    assert envelopes.get_cursor("agent:a", "task:NOPE") == 0
    with pytest.raises(sqlite3.IntegrityError):
        with tasks.transaction() as conn:
            conn.execute("INSERT INTO event_cursors(consumer,scope,seq,updated_at) VALUES('x','',-1,'t')")


def test_event_head_follows_the_team_event_log(stores):
    tasks, envelopes = stores
    assert envelopes.event_head() == 0
    with tasks.transaction() as conn:
        tasks.append_event(conn, "task.created", actor="user", task_id="G-T01")
        last = tasks.append_event(conn, "task.created", actor="user", task_id="G-T01")
        assert envelopes.event_head(conn=conn) == last
    assert envelopes.event_head() == last


def test_a_result_its_event_and_a_cursor_commit_or_roll_back_together(stores):
    tasks, envelopes = stores
    with pytest.raises(RuntimeError):
        with tasks.transaction() as conn:
            add_result(tasks, envelopes, conn)
            tasks.append_event(conn, "result.submitted", actor="agent:a", task_id="G-T01")
            envelopes.set_cursor(conn, "agent:a", "", 7)
            raise RuntimeError("the transition failed")
    assert envelopes.results("G-T01") == [] and tasks.events() == [] and envelopes.get_cursor("agent:a") == 0