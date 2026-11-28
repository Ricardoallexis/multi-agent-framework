# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Run reads of the store: listing runs and finding a run take a fixed number of queries (RND-T03)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.run_request import RunRequest


@pytest.fixture
def store(tmp_path: Path) -> Store:
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    store = Store(Database(settings))
    store.db.migrate()
    return store


def add_run(store: Store, index: int) -> str:
    run = store.create_run(RunRequest(workflow_id="two_steps", project_name="Read tests",
                                      inputs={"topic": f"topic {index}"}), 10, 3600)
    return run["id"]


def count_queries(store: Store, call) -> tuple[Any, list[str]]:
    """Run ``call`` and return its result with every SQL statement the store's connections executed."""
    statements: list[str] = []
    real_connect = store.db.connect

    @contextmanager
    def traced():
        with real_connect() as conn:
            conn.set_trace_callback(statements.append)
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)

    store.db.connect = traced  # type: ignore[method-assign]
    try:
        result = call()
    finally:
        store.db.connect = real_connect  # type: ignore[method-assign]
    return result, [s for s in statements if s.lstrip().upper().startswith("SELECT")]


def legacy_list_runs(store: Store, *, limit: int = 20, status: str = "", project: str = "") -> list[dict[str, Any]]:
    """The implementation before RND-T03, kept as the reference: two more queries per run."""
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("r.status=?")
        params.append(status)
    if project:
        clauses.append("p.name=?")
        params.append(project)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(max(1, min(int(limit), 500)))
    with store.db.connect() as conn:
        rows = conn.execute(
            f"""SELECT r.id,r.workflow_id,r.status,r.current_step,r.brand_version,r.llm_calls,
                       r.max_llm_calls,r.waiting_reason,r.waiting_step,r.created_at,r.updated_at,
                       r.error,p.id AS project_id,p.name AS project_name
                FROM runs r JOIN projects p ON p.id=r.project_id
                {where}
                ORDER BY r.created_at DESC LIMIT ?""",
            params,
        ).fetchall()
        results = [dict(row) for row in rows]
        for item in results:
            saved = conn.execute("SELECT waiting_attempt FROM runs WHERE id=?", (item["id"],)).fetchone()
            item["waiting_attempt"] = saved["waiting_attempt"]
            events = [{"id": row["id"], "event_type": row["event_type"],
                       "payload": json.loads(row["payload_json"])} for row in conn.execute(
                "SELECT id,event_type,payload_json FROM run_events WHERE run_id=? "
                "AND event_type IN ('run.approval_pending','run.approval_overdue') ORDER BY id", (item["id"],))]
            item["attention"] = store._approval_attention(item, events)
        return results


def make_wait(store: Store, run_id: str, *, attempt: int, event_attempt: int, overdue: bool = False,
              reason: str = "approval_pending") -> None:
    """Put a run in waiting_human with a pending approval event (and optionally an overdue one)."""
    with store.db.connect() as conn:
        conn.execute("UPDATE runs SET status='waiting_human',waiting_reason=?,waiting_step='review',"
                     "waiting_attempt=? WHERE id=?", (reason, attempt, run_id))
        cursor = conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                              (run_id, "2026-01-01T00:00:00+00:00", "run.approval_pending",
                               json.dumps({"step_id": "review", "attempt": event_attempt})))
        if overdue:
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run_id, "2026-01-02T00:00:00+00:00", "run.approval_overdue",
                          json.dumps({"wait_id": cursor.lastrowid})))
        conn.commit()


@pytest.mark.parametrize("count", [1, 25, 200])
def test_list_runs_takes_at_most_two_queries(store, count):
    ids = [add_run(store, index) for index in range(count)]
    for run_id in ids[::3]:
        make_wait(store, run_id, attempt=1, event_attempt=1)
    rows, selects = count_queries(store, lambda: store.list_runs(limit=count))
    assert len(rows) == count
    assert len(selects) <= 2
    assert any(row["attention"] for row in rows)


def test_list_runs_without_pending_approvals_takes_one_query(store):
    for index in range(10):
        add_run(store, index)
    rows, selects = count_queries(store, lambda: store.list_runs(limit=10))
    assert len(rows) == 10 and len(selects) == 1


def test_list_runs_matches_the_previous_implementation(store):
    ids = [add_run(store, index) for index in range(9)]
    with store.db.connect() as conn:
        conn.execute("UPDATE runs SET status='completed' WHERE id=?", (ids[0],))
        conn.execute("UPDATE runs SET status='running',current_step=1 WHERE id=?", (ids[1],))
        conn.execute("UPDATE runs SET status='failed',error='boom' WHERE id=?", (ids[2],))
        conn.commit()
    make_wait(store, ids[3], attempt=2, event_attempt=2)                       # pending approval
    make_wait(store, ids[4], attempt=1, event_attempt=1, overdue=True)         # overdue approval
    make_wait(store, ids[5], attempt=3, event_attempt=1)                       # stale waiting_attempt
    make_wait(store, ids[6], attempt=1, event_attempt=1, reason="review")      # waiting, but not for approval
    with store.db.connect() as conn:
        conn.execute("UPDATE runs SET status='paused' WHERE id=?", (ids[7],))
        conn.commit()
    # ids[8] stays queued. A waiting run with no event at all:
    with store.db.connect() as conn:
        conn.execute("UPDATE runs SET status='waiting_human',waiting_reason='approval_pending',"
                     "waiting_step='review',waiting_attempt=1 WHERE id=?", (ids[8],))
        conn.commit()

    for kwargs in ({"limit": 20}, {"limit": 4}, {"limit": 20, "status": "waiting_human"},
                   {"limit": 20, "project": "Read tests"}, {"limit": 20, "project": "other"}):
        new = store.list_runs(**kwargs)
        old = legacy_list_runs(store, **kwargs)
        assert new == old
        assert [list(item) for item in new] == [list(item) for item in old]   # same keys, same order
    by_id = {row["id"]: row for row in store.list_runs(limit=20)}
    assert by_id[ids[3]]["attention"] and not by_id[ids[3]]["attention"]["overdue"]
    assert by_id[ids[4]]["attention"]["overdue"] is True
    assert by_id[ids[5]]["attention"] is None and by_id[ids[5]]["waiting_attempt"] == 3
    assert by_id[ids[6]]["attention"] is None and by_id[ids[8]]["attention"] is None


def test_has_run_answers_true_and_false_without_raising(store):
    run_id = add_run(store, 0)
    assert store.has_run(run_id) is True
    assert store.has_run("missing-run") is False
    assert store.has_run("") is False
    _, selects = count_queries(store, lambda: store.has_run(run_id))
    assert len(selects) == 1
    with pytest.raises(KeyError):
        store.get_run("missing-run")      # get_run keeps raising for the engine
