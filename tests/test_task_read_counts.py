# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The task graph is read once per operation (RND-T04): counts, not times, and the same views as before."""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from multiagent.config import Settings
from multiagent.db.task_store import TaskStore
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.tasks import TaskServices
from multiagent.tasks import graph


def make_tasks(tmp_path: Path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True).tasks


@pytest.fixture
def tasks(tmp_path: Path):
    return make_tasks(tmp_path)


@contextmanager
def counted():
    """Count SQLite connections opened and reads of the whole task table during one operation."""
    opened = [0]
    reads = [0]
    original_connect = sqlite3.connect
    original_list = TaskStore.list_tasks

    def connect(*args, **kwargs):
        opened[0] += 1
        return original_connect(*args, **kwargs)

    def list_tasks(self, *args, **kwargs):
        reads[0] += 1
        return original_list(self, *args, **kwargs)

    with patch.object(sqlite3, "connect", connect), patch.object(TaskStore, "list_tasks", list_tasks):
        yield opened, reads


def fill(tasks, count: int, *, group: str = "G") -> None:
    """A layered graph: every task after the fifth waits for the one five places before it."""
    tasks.create_group(group, "Group", actor="user", authorized=True)
    for i in range(count):
        deps = [f"T{i - 5:04d}"] if i >= 5 else []
        tasks.create_task(group, f"T{i:04d}", f"Task {i}", actor="user", depends_on=deps)


@pytest.mark.parametrize("count", [10, 300])
def test_reading_operations_open_one_connection_and_read_the_task_table_once(tmp_path, count):
    tasks = make_tasks(tmp_path)
    fill(tasks, count)
    middle = f"T{count // 2:04d}"
    for name, call in (("list_tasks", lambda: tasks.list_tasks(group_id="G")),
                       ("get_task", lambda: tasks.get_task(middle)),
                       ("get_group", lambda: tasks.get_group("G")),
                       ("list_groups", lambda: tasks.list_groups()),
                       ("ready_tasks", lambda: tasks.ready_tasks())):
        with counted() as (opened, reads):
            call()
        # One task is read from its own rows and its neighbours': the table is not read at all.
        assert (name, opened[0], reads[0]) == (name, 1, 0 if name == "get_task" else 1)


def test_list_groups_reads_the_graph_once_for_many_groups(tmp_path):
    tasks = make_tasks(tmp_path)
    for index in range(6):
        tasks.create_group(f"G{index}", f"Group {index}", actor="user", authorized=True)
        for n in range(3):
            tasks.create_task(f"G{index}", f"G{index}-T{n}", "t", actor="user")
    with counted() as (opened, reads):
        groups = tasks.list_groups()
    assert len(groups) == 6 and all(group["counts"] == {"ready": 3} for group in groups)
    assert (opened[0], reads[0]) == (1, 1)


@pytest.mark.parametrize("count", [10, 300])
def test_create_task_reads_the_task_table_at_most_once(tmp_path, count):
    tasks = make_tasks(tmp_path)
    fill(tasks, count)
    with counted() as (opened, reads):
        tasks.create_task("G", "NEW", "New", actor="user", depends_on=[f"T{count - 1:04d}"])
    assert reads[0] <= 1
    assert opened[0] == 1


@pytest.mark.parametrize("count", [10, 300])
def test_every_transition_reads_the_task_table_at_most_twice(tmp_path, count, monkeypatch):
    # The plan auto-advance that follows an accept runs after the commit and is not part of the transition.
    monkeypatch.setattr("multiagent.services.plan_autoadvance.notify_task_accepted", lambda *args, **kwargs: None)
    tasks = make_tasks(tmp_path)
    fill(tasks, count)
    for action in ("claim", "start", "submit", "accept"):
        with counted() as (opened, reads):
            tasks.transition("T0000", action, actor="user" if action == "accept" else "agent:a")
        assert (action, reads[0] <= 2, opened[0]) == (action, True, 1)
    # The accepted task released its dependent: the read after the change is what tells it.
    assert tasks.get_task("T0005")["status"] == "ready"


def test_dependency_edits_read_the_task_table_at_most_twice_or_thrice(tmp_path):
    tasks = make_tasks(tmp_path)
    fill(tasks, 12)
    with counted() as (_, reads):
        tasks.remove_dependency("T0005", "T0000", actor="user")
    assert reads[0] <= 2
    assert tasks.get_task("T0005")["status"] == "ready"


class Indexes:
    """The edge indexes built while an operation runs, to add up the edges its questions looked at."""

    def __init__(self):
        self.built: list[graph.EdgeIndex] = []

    @property
    def visited(self) -> int:
        return sum(index.visited for index in self.built)


@contextmanager
def watching_indexes():
    seen = Indexes()
    original = graph.EdgeIndex.__init__

    def init(self, dependencies):
        original(self, dependencies)
        seen.built.append(self)

    with patch.object(graph.EdgeIndex, "__init__", init):
        yield seen


def edges_visited(tmp_path: Path, count: int, operation: str) -> tuple[int, int]:
    tasks = make_tasks(tmp_path / f"n{count}")
    fill(tasks, count)
    with watching_indexes() as seen:
        if operation == "list_tasks":
            tasks.list_tasks(group_id="G")
        else:
            tasks.list_groups()
    return seen.visited, count + (count - 5)


@pytest.mark.parametrize("operation", ["list_tasks", "list_groups"])
def test_edge_comparisons_grow_with_tasks_plus_edges_not_with_their_product(tmp_path, operation):
    small, small_size = edges_visited(tmp_path, 100, operation)
    large, large_size = edges_visited(tmp_path, 400, operation)
    assert small > 0
    # Four times the graph: a product would be sixteen times the work; a sum stays near four.
    assert large / small <= 1.5 * large_size / small_size


# ------------------------------------------------------------------ equivalence with the pure functions


def reference(tasks: TaskServices) -> dict[str, dict]:
    """What each view must say, from the rows and the pure functions that stay in the repository."""
    with tasks.store.db.connect() as conn:
        rows = {task["id"]: task for task in tasks.store.list_tasks(conn=conn)}
        native = tasks.store.dependencies(conn=conn)
        legacy = tasks.store.legacy_release_dependencies(conn=conn)
        context = tasks._release_context(conn)
    states = {task_id: task["state"] for task_id, task in rows.items()}
    expected = {}
    for task_id, task in rows.items():
        release_edges = [TaskServices._release_edge_view(edge, rows, states, context)
                         for edge in legacy if edge["task_id"] == task_id]
        blockers = graph.blockers(task_id, states, native) + [edge for edge in release_edges if not edge["satisfied"]]
        status = states[task_id]
        if status == "open":
            status = "blocked" if blockers else "ready"
        own = [dep for dep in native if dep["task_id"] == task_id]
        expected[task_id] = {
            "status": status,
            "blockers": blockers,
            "depends_on": [{"task_id": dep["depends_on_id"], "requires": dep["requires"],
                            "satisfied": graph.is_satisfied(dep["requires"], states.get(dep["depends_on_id"]))}
                           for dep in own],
            "dependents": list(dict.fromkeys(graph.dependents(task_id, native) +
                                             [edge["task_id"] for edge in legacy
                                              if edge["depends_on_id"] == task_id])),
            "release_dependencies": release_edges,
            "group_id": task["group_id"],
        }
    return expected


def check_views(tasks: TaskServices) -> None:
    expected = reference(tasks)
    listed = tasks.list_tasks()
    assert [view["id"] for view in listed] == list(expected)
    for view in listed:
        want = expected[view["id"]]
        for field in ("status", "blockers", "depends_on", "dependents", "release_dependencies", "group_id"):
            assert view[field] == want[field], (view["id"], field)
        assert view == tasks.get_task(view["id"])
    for status in ("blocked", "ready", "claimed", "submitted", "accepted"):
        assert [v["id"] for v in tasks.list_tasks(status=status)] == [t for t, w in expected.items()
                                                                       if w["status"] == status]
    for group in tasks.list_groups():
        counts: dict[str, int] = {}
        for want in expected.values():
            if want["group_id"] == group["id"]:
                counts[want["status"]] = counts.get(want["status"], 0) + 1
        assert group["counts"] == counts
        assert tasks.get_group(group["id"]) == group


def random_graph(tasks: TaskServices, seed: int) -> list[str]:
    """Tasks in three groups, native edges of both kinds, release edges, and a group with a release mode."""
    rng = random.Random(seed)
    for group in ("A", "B", "R"):
        tasks.create_group(group, f"Group {group}", actor="user", authorized=True)
    order: list[str] = []
    for i in range(36):
        group = rng.choice(("A", "B", "R"))
        wanted = rng.sample(order, k=min(len(order), rng.choice((0, 0, 1, 1, 2, 3)))) if order else []
        deps = [{"task_id": dep, "requires": rng.choice(("accepted", "submitted"))} for dep in wanted]
        task_id = f"T{i:02d}"
        tasks.create_task(group, task_id, f"Task {i}", actor="user", depends_on=deps)
        order.append(task_id)
    tasks.set_release_mode("R", "terminada", actor="user")
    tasks.set_release_mode("B", "probada", actor="user")
    pairs = {(dep["task_id"], dep["depends_on_id"]) for dep in tasks.store.dependencies()}
    added = 0
    while added < 8:
        later, earlier = sorted(rng.sample(range(len(order)), 2), reverse=True)
        pair = (order[later], order[earlier])
        if pair in pairs:
            continue
        pairs.add(pair)
        tasks.add_release_dependency(pair[0], pair[1], actor="user", source_ref="doc://fixtures/edge.md")
        added += 1
    return order


def ready_events_after(tasks: TaskServices, seq: int) -> list[str]:
    return [event["task_id"] for event in tasks.events(after=seq, limit=1000) if event["type"] == "task.ready"]


@pytest.mark.parametrize("seed", [1, 7, 2026])
def test_views_states_and_ready_events_are_the_ones_the_pure_functions_give(tmp_path, seed):
    tasks = make_tasks(tmp_path)
    order = random_graph(tasks, seed)
    rng = random.Random(seed + 1)
    check_views(tasks)
    path = ("claim", "start", "submit", "accept")
    for _ in range(40):
        ready = [view["id"] for view in tasks.list_tasks(status="ready")]
        if not ready:
            break
        task_id = rng.choice(ready)
        for action in path[:rng.randint(1, 4)]:
            before = {t: w["status"] for t, w in reference(tasks).items()}
            last = tasks.events(limit=1000)[-1]["seq"]
            tasks.transition(task_id, action, actor="user" if action == "accept" else "agent:a")
            after = {t: w["status"] for t, w in reference(tasks).items()}
            expected = [t for t in order if before[t] == "blocked" and after[t] == "ready"]
            assert ready_events_after(tasks, last) == expected
        if rng.random() < 0.4:
            fact = {"delivery_ref": "doc://fixtures/delivery.md", "delivery_sha256": "a" * 64,
                    "has_patch": False, "patchset_sha256": ""}
            try:
                tasks.record_delivery_fact(task_id, actor="user", **fact)
            except ServiceError:
                pass
        check_views(tasks)
    dependency = next(dep for dep in tasks.store.dependencies())
    before = {t: w["status"] for t, w in reference(tasks).items()}
    last = tasks.events(limit=1000)[-1]["seq"]
    tasks.remove_dependency(dependency["task_id"], dependency["depends_on_id"], actor="user")
    after = {t: w["status"] for t, w in reference(tasks).items()}
    assert ready_events_after(tasks, last) == [t for t in order if before[t] == "blocked" and after[t] == "ready"]
    check_views(tasks)


def test_the_listed_provenance_is_the_one_of_each_task(tmp_path):
    tasks = make_tasks(tmp_path)
    data = {"groups": [{"id": "G", "title": "Imported", "authorized": True}],
            "tasks": [{"id": f"G-T0{i}", "group_id": "G", "title": f"T{i}", "state": "open",
                       "source_id": f"T0{i}", "source_state": "open", "file_scope": []} for i in (1, 2)],
            "dependencies": []}
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    tasks.import_snapshot(data, actor="user", source_key="rnd:t04", source_ref="fixture://rnd/t04",
                          snapshot_sha256=hashlib.sha256(canonical.encode()).hexdigest())
    tasks.create_task("G", "PLAIN", "Plain", actor="user")
    listed = {view["id"]: view for view in tasks.list_tasks()}
    assert listed["G-T01"]["provenance"]["source_ref"] == "fixture://rnd/t04"
    assert "provenance" not in listed["PLAIN"]
    for task_id, view in listed.items():
        assert view == tasks.get_task(task_id)
    assert tasks.get_group("G")["provenance"]["source_key"] == "rnd:t04"
