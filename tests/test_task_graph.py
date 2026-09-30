# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure task dependency graph (ORQ-T04)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from multiagent.tasks import (
    CycleError,
    GraphError,
    UnknownTaskError,
    blockers,
    dependents,
    effective_status,
    find_cycle,
    is_satisfied,
    newly_ready,
    parallel_layers,
    statuses,
    validate,
    would_create_cycle,
)


def dep(task: str, on: str, requires: str = "accepted") -> dict[str, str]:
    return {"task_id": task, "depends_on_id": on, "requires": requires}


# A -> B, A -> C, B + C -> D, D -> E  (B and C wait for A, D waits for B and C, E waits for D)
DIAMOND = [dep("B", "A"), dep("C", "A"), dep("D", "B"), dep("D", "C"), dep("E", "D")]
TASKS = ["A", "B", "C", "D", "E"]


def all_open() -> dict[str, str]:
    return {task: "open" for task in TASKS}


def test_requires_accepted_or_submitted():
    assert is_satisfied("accepted", "accepted")
    assert not is_satisfied("accepted", "submitted")
    assert is_satisfied("submitted", "submitted") and is_satisfied("submitted", "accepted")
    assert not is_satisfied("submitted", "running")
    assert not is_satisfied("accepted", None)
    with pytest.raises(GraphError):
        is_satisfied("done", "accepted")


def test_diamond_readiness_follows_accepted_dependencies():
    states = all_open()
    assert statuses(states, DIAMOND) == {"A": "ready", "B": "blocked", "C": "blocked", "D": "blocked", "E": "blocked"}
    states["A"] = "accepted"
    assert statuses(states, DIAMOND)["B"] == "ready" and statuses(states, DIAMOND)["C"] == "ready"
    states["B"] = "accepted"
    # D waits for B and C: one accepted dependency is not enough.
    assert effective_status("D", states, DIAMOND) == "blocked"
    assert blockers("D", states, DIAMOND) == [{"task_id": "C", "state": "open", "requires": "accepted"}]
    states["C"] = "accepted"
    assert effective_status("D", states, DIAMOND) == "ready"
    assert effective_status("E", states, DIAMOND) == "blocked"


def test_submitted_is_not_accepted_unless_required():
    states = {"A": "submitted", "B": "open", "C": "open"}
    deps = [dep("B", "A"), dep("C", "A", "submitted")]
    assert effective_status("B", states, deps) == "blocked"
    assert effective_status("C", states, deps) == "ready"


def test_non_open_tasks_keep_their_stored_state():
    states = {"A": "open", "B": "running"}
    assert effective_status("B", states, [dep("B", "A")]) == "running"


def test_failed_or_cancelled_dependency_keeps_the_task_blocked_with_the_reason():
    states = {"A": "cancelled", "B": "open"}
    assert blockers("B", states, [dep("B", "A")]) == [{"task_id": "A", "state": "cancelled", "requires": "accepted"}]


def test_unknown_dependency_blocks_and_fails_validation():
    states = {"B": "open"}
    deps = [dep("B", "GHOST")]
    assert blockers("B", states, deps) == [{"task_id": "GHOST", "state": None, "requires": "accepted"}]
    with pytest.raises(UnknownTaskError) as error:
        validate(["B"], deps)
    assert error.value.missing == ["GHOST"]


def test_cycles_are_found_and_rejected():
    assert find_cycle(DIAMOND) is None
    validate(TASKS, DIAMOND)
    cyclic = DIAMOND + [dep("A", "E")]
    cycle = find_cycle(cyclic)
    assert cycle is not None and cycle[0] == cycle[-1]
    assert set(cycle) in ({"A", "B", "D", "E"}, {"A", "C", "D", "E"})
    with pytest.raises(CycleError) as error:
        validate(TASKS, cyclic)
    assert error.value.cycle[0] == error.value.cycle[-1]
    two = [dep("X", "Y"), dep("Y", "X")]
    assert find_cycle(two) == ["X", "Y", "X"]


def test_a_task_behind_a_cycle_does_not_hide_it():
    deps = [dep("Z", "X"), dep("X", "Y"), dep("Y", "X")]
    assert find_cycle(deps) == ["X", "Y", "X"]


def test_would_create_cycle_checks_a_new_edge():
    assert would_create_cycle(DIAMOND, "A", "E") is not None
    assert would_create_cycle(DIAMOND, "E", "A") is None
    assert would_create_cycle(DIAMOND, "A", "A") == ["A", "A"]


def test_validate_rejects_self_dependencies_and_unknown_requires():
    with pytest.raises(GraphError):
        validate(["A"], [dep("A", "A")])
    with pytest.raises(GraphError):
        validate(["A", "B"], [dep("B", "A", "done")])


def test_multiple_dependencies_report_every_blocker_in_order():
    states = {"A": "running", "B": "submitted", "C": "accepted", "D": "open"}
    deps = [dep("D", "A"), dep("D", "B"), dep("D", "C")]
    assert [b["task_id"] for b in blockers("D", states, deps)] == ["A", "B"]


def test_dependents_are_direct_only():
    assert dependents("A", DIAMOND) == ["B", "C"]
    assert dependents("D", DIAMOND) == ["E"]
    assert dependents("E", DIAMOND) == []


def test_newly_ready_lists_the_released_dependents():
    before = {"A": "accepted", "B": "accepted", "C": "submitted", "D": "open", "E": "open"}
    after = {**before, "C": "accepted"}
    assert newly_ready("C", before, after, DIAMOND) == ["D"]
    assert newly_ready("C", before, before, DIAMOND) == []
    # D was already cancelled: nothing is released.
    assert newly_ready("C", {**before, "D": "cancelled"}, {**after, "D": "cancelled"}, DIAMOND) == []


def test_parallel_layers_group_independent_tasks():
    assert parallel_layers(TASKS, DIAMOND) == [["A"], ["B", "C"], ["D"], ["E"]]
    assert parallel_layers(["X", "A", "B"], [dep("B", "A")]) == [["X", "A"], ["B"]]
    # Dependencies outside the given tasks are ignored.
    assert parallel_layers(["D", "E"], DIAMOND) == [["D"], ["E"]]
    with pytest.raises(CycleError):
        parallel_layers(["X", "Y"], [dep("X", "Y"), dep("Y", "X")])


def test_the_graph_module_imports_no_database_or_services():
    package = Path(__file__).parents[1] / "multiagent" / "tasks"
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [("." * node.level) + (node.module or "")]
            for name in names:
                assert not any(part in name for part in ("db", "services", "sqlite3")), f"{path.name} imports {name}"
