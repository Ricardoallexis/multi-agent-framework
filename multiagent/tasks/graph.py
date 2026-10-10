# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure dependency graph of tasks (ORQ-T04): no database, no services.

A dependency is an edge ``{"task_id", "depends_on_id", "requires"}`` (the rows
of the task store): ``task_id`` waits for ``depends_on_id``. It is satisfied
when the task it waits for is ``accepted`` (``requires="accepted"``) or, with
``requires="submitted"``, ``submitted``, ``validating`` (its gate is running) or
``accepted``.

Only a stored ``open`` task is reported as ``blocked`` or ``ready``; every
other stored state is reported as it is. A dependency on a task that does not
exist is never satisfied (its blocker has ``state=None``); ``validate`` rejects
such graphs before they are stored.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

REQUIRES = ("accepted", "submitted")
_SATISFYING: dict[str, frozenset[str]] = {
    "accepted": frozenset({"accepted"}),
    # A task being validated by its gate (ORQ-2) was already submitted.
    "submitted": frozenset({"submitted", "validating", "accepted"}),
}

Dependency = Mapping[str, Any]


class GraphError(ValueError):
    """The dependencies do not form a valid graph."""


class CycleError(GraphError):
    """The dependencies contain a cycle; ``cycle`` lists it, first task repeated at the end."""

    def __init__(self, cycle: list[str]):
        super().__init__("Dependency cycle: " + " -> ".join(cycle))
        self.cycle = cycle


class UnknownTaskError(GraphError):
    """A dependency names a task that does not exist; ``missing`` lists the unknown ids."""

    def __init__(self, missing: list[str]):
        super().__init__("Unknown task in dependencies: " + ", ".join(missing))
        self.missing = missing


def is_satisfied(requires: str, state: str | None) -> bool:
    """Whether a dependency with ``requires`` is met by a task in stored ``state``."""
    if requires not in _SATISFYING:
        raise GraphError(f"Unknown requires: {requires!r}")
    return state in _SATISFYING[requires]


def _depends_on(dependencies: Iterable[Dependency]) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    for dep in dependencies:
        graph.setdefault(dep["task_id"], []).append(dep["depends_on_id"])
    return graph


def find_cycle(dependencies: Iterable[Dependency]) -> list[str] | None:
    """A cycle as ``[a, b, ..., a]`` (each waits for the next), or ``None``.

    Kahn's algorithm removes every task that is not on or behind a cycle; a
    walk through what remains finds one cycle to report.
    """
    graph = _depends_on(dependencies)
    nodes = set(graph) | {d for targets in graph.values() for d in targets}
    waiting = {node: len(graph.get(node, [])) for node in nodes}
    dependents: dict[str, list[str]] = {}
    for task, targets in graph.items():
        for target in targets:
            dependents.setdefault(target, []).append(task)
    queue = [node for node, count in waiting.items() if count == 0]
    while queue:
        node = queue.pop()
        for task in dependents.get(node, []):
            waiting[task] -= 1
            if waiting[task] == 0:
                queue.append(task)
    remaining = {node for node, count in waiting.items() if count > 0}
    if not remaining:
        return None
    # Every remaining task waits for at least one remaining task: walk until a task repeats.
    node = min(remaining)
    path: list[str] = []
    seen: dict[str, int] = {}
    while node not in seen:
        seen[node] = len(path)
        path.append(node)
        node = min(t for t in graph[node] if t in remaining)
    return path[seen[node]:] + [node]


def would_create_cycle(dependencies: Iterable[Dependency], task_id: str, depends_on_id: str) -> list[str] | None:
    """The cycle that adding ``task_id -> depends_on_id`` would create, or ``None``."""
    if task_id == depends_on_id:
        return [task_id, task_id]
    edges = list(dependencies) + [{"task_id": task_id, "depends_on_id": depends_on_id, "requires": "accepted"}]
    return find_cycle(edges)


def validate(task_ids: Iterable[str], dependencies: Iterable[Dependency]) -> None:
    """Raise ``UnknownTaskError``, ``GraphError`` (self-dependency, unknown ``requires``) or ``CycleError``."""
    known = set(task_ids)
    deps = list(dependencies)
    missing = sorted({d[key] for d in deps for key in ("task_id", "depends_on_id") if d[key] not in known})
    if missing:
        raise UnknownTaskError(missing)
    for dep in deps:
        if dep["task_id"] == dep["depends_on_id"]:
            raise GraphError(f"Task depends on itself: {dep['task_id']}")
        if dep.get("requires", "accepted") not in _SATISFYING:
            raise GraphError(f"Unknown requires: {dep.get('requires')!r}")
    cycle = find_cycle(deps)
    if cycle:
        raise CycleError(cycle)


def validate_union(task_ids: Iterable[str], native: Iterable[Dependency],
                   legacy_release: Iterable[Dependency]) -> None:
    """Validate a graph whose native and legacy release edges live in separate stores.

    The synthetic ``requires`` value is used only by the topology validator; it is
    never persisted or projected into an envelope.
    """
    native_rows, legacy_rows = list(native), list(legacy_release)
    pairs: set[tuple[str, str]] = set()
    for edge in native_rows + legacy_rows:
        pair = (str(edge["task_id"]), str(edge["depends_on_id"]))
        if pair in pairs:
            raise GraphError(f"Dependency pair has multiple kinds: {pair[0]} -> {pair[1]}")
        pairs.add(pair)
    normalized_legacy = [
        {"task_id": edge["task_id"], "depends_on_id": edge["depends_on_id"], "requires": "accepted"}
        for edge in legacy_rows
    ]
    validate(task_ids, native_rows + normalized_legacy)


def blockers(task_id: str, states: Mapping[str, str], dependencies: Iterable[Dependency]) -> list[dict[str, Any]]:
    """Unsatisfied dependencies of ``task_id``: ``{task_id, state, requires}`` in dependency order."""
    found = []
    for dep in dependencies:
        if dep["task_id"] != task_id:
            continue
        requires = dep.get("requires", "accepted")
        state = states.get(dep["depends_on_id"])
        if not is_satisfied(requires, state):
            found.append({"task_id": dep["depends_on_id"], "state": state, "requires": requires})
    return found


def effective_status(task_id: str, states: Mapping[str, str], dependencies: Iterable[Dependency]) -> str:
    """``blocked`` or ``ready`` for an ``open`` task; the stored state otherwise."""
    state = states[task_id]
    if state != "open":
        return state
    return "blocked" if blockers(task_id, states, dependencies) else "ready"


def statuses(states: Mapping[str, str], dependencies: Iterable[Dependency]) -> dict[str, str]:
    """Effective status of every task in ``states``."""
    deps = list(dependencies)
    return {task_id: effective_status(task_id, states, deps) for task_id in states}


def dependents(task_id: str, dependencies: Iterable[Dependency]) -> list[str]:
    """Tasks that wait directly for ``task_id``, in dependency order."""
    return [dep["task_id"] for dep in dependencies if dep["depends_on_id"] == task_id]


def newly_ready(task_id: str, before: Mapping[str, str], after: Mapping[str, str],
                dependencies: Iterable[Dependency]) -> list[str]:
    """Direct dependents of ``task_id`` that were ``blocked`` with ``before`` and are ``ready`` with ``after``.

    ``before`` and ``after`` are the stored states around one change of ``task_id``.
    """
    deps = list(dependencies)
    return [
        dependent for dependent in dependents(task_id, deps)
        if dependent in after
        and effective_status(dependent, before, deps) == "blocked"
        and effective_status(dependent, after, deps) == "ready"
    ]


def parallel_layers(task_ids: Iterable[str], dependencies: Iterable[Dependency]) -> list[list[str]]:
    """Tasks in dependency order, grouped: the tasks of one layer only wait for earlier layers.

    Tasks of the same layer can run in parallel. Dependencies on tasks outside
    ``task_ids`` are ignored. Raises ``CycleError`` if the tasks form a cycle.
    """
    ids = list(dict.fromkeys(task_ids))
    known = set(ids)
    deps = [d for d in dependencies if d["task_id"] in known and d["depends_on_id"] in known]
    cycle = find_cycle(deps)
    if cycle:
        raise CycleError(cycle)
    waits = _depends_on(deps)
    level: dict[str, int] = {}

    def depth(node: str) -> int:
        if node not in level:
            level[node] = 1 + max((depth(t) for t in waits.get(node, [])), default=-1)
        return level[node]

    layers: list[list[str]] = []
    for node in ids:
        index = depth(node)
        while len(layers) <= index:
            layers.append([])
        layers[index].append(node)
    return layers
