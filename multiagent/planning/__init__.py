# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Trusted, named plan proposal strategies.

Each strategy returns task candidates. Validation and the executable document
are shared, so a strategy cannot bypass the proposal contract.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from ..tasks import graph
from ..services.errors import INVALID_REQUEST, NOT_FOUND, ServiceError, service_errors

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_GROUP = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
Planner = Callable[[Any, Mapping[str, Any], bool], Mapping[str, Any]]
_REGISTRY: dict[str, tuple[str, Planner]] = {}


def _invalid(message: str) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422)


def register_planner(name: str, planner: Planner, *, description: str) -> None:
    """Register trusted host code under a new name."""
    if not isinstance(name, str) or not _ID.fullmatch(name) or not callable(planner):
        raise _invalid("Planner needs a valid name and callable")
    if not isinstance(description, str) or not description.strip():
        raise _invalid("Planner needs a description")
    if name in _REGISTRY:
        raise _invalid(f"Planner already registered: {name}")
    _REGISTRY[name] = (description.strip(), planner)


def planners() -> list[dict[str, str]]:
    return [{"name": name, "description": description}
            for name, (description, _) in sorted(_REGISTRY.items())]


def propose(services: Any, name: str, request: Any, *, confirm_cloud: bool = False) -> dict[str, Any]:
    if not isinstance(name, str) or name not in _REGISTRY:
        raise ServiceError(NOT_FOUND, f"Planner not found: {name}", status=404)
    if not isinstance(request, Mapping):
        raise _invalid("Planner request must be an object")
    group_id, goal = request.get("group_id"), request.get("goal")
    if not isinstance(group_id, str) or not _GROUP.fullmatch(group_id):
        raise _invalid("group_id must be a valid task group id")
    if not isinstance(goal, str) or not goal.strip():
        raise _invalid("goal must be non-empty text")
    raw = _REGISTRY[name][1](services, request, confirm_cloud)
    if not isinstance(raw, Mapping):
        raise _invalid("Planner output must be an object")
    return validate_proposal(services, {**raw, "schema": "multiagent.plan_proposal/1",
                                        "planner": name, "group_id": group_id, "goal": goal})


def validate_proposal(services: Any, proposal: Any) -> dict[str, Any]:
    """Normalize a generated or edited proposal through the same task contract."""
    if not isinstance(proposal, Mapping) or proposal.get("schema") != "multiagent.plan_proposal/1":
        raise _invalid("Proposal schema must be multiagent.plan_proposal/1")
    name = proposal.get("planner")
    if not isinstance(name, str) or name not in _REGISTRY:
        raise _invalid("Proposal planner must be registered")
    group_id, goal = proposal.get("group_id"), proposal.get("goal")
    if not isinstance(group_id, str) or not _GROUP.fullmatch(group_id):
        raise _invalid("group_id must be a valid task group id")
    if not isinstance(goal, str) or not goal.strip():
        raise _invalid("goal must be non-empty text")
    raw = proposal
    if not isinstance(raw, Mapping):
        raise _invalid("Planner output must be an object")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise _invalid("Planner must propose a non-empty task list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    edges: list[dict[str, str]] = []
    for index, item in enumerate(tasks):
        if not isinstance(item, Mapping) or set(item) - {
                "id", "title", "objective", "depends_on", "workflow", "bundle", "inputs", "preferred"}:
            raise _invalid(f"Task {index} has unknown fields or is not an object")
        task_id, title, workflow = item.get("id"), item.get("title"), item.get("workflow")
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise _invalid(f"Task {index} has an invalid id")
        if task_id in seen:
            raise _invalid(f"Repeated task id: {task_id}")
        seen.add(task_id)
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise _invalid(f"Task {task_id} needs a title of at most 200 characters")
        objective = item.get("objective", "")
        if not isinstance(objective, str) or len(objective) > 4000:
            raise _invalid(f"Task {task_id} objective must be text of at most 4000 characters")
        dependencies = item.get("depends_on", [])
        if not isinstance(dependencies, list) or any(
                not isinstance(dep, str) or not _ID.fullmatch(dep) for dep in dependencies):
            raise _invalid(f"Task {task_id} depends_on must be a list of task ids")
        if len(set(dependencies)) != len(dependencies):
            raise _invalid(f"Task {task_id} has repeated dependencies")
        edges.extend({"task_id": task_id, "depends_on_id": dep} for dep in dependencies)
        if not isinstance(workflow, str) or not workflow:
            raise _invalid(f"Task {task_id} needs a workflow")
        bundle = item.get("bundle")
        if bundle is not None and (not isinstance(bundle, str) or not bundle):
            raise _invalid(f"Task {task_id} bundle must be non-empty text")
        preferred = item.get("preferred")
        if preferred is not None and (not isinstance(preferred, str) or len(preferred) > 100):
            raise _invalid(f"Task {task_id} preferred must be text of at most 100 characters")
        inputs = item.get("inputs", {})
        if not isinstance(inputs, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str) for key, value in inputs.items()):
            raise _invalid(f"Task {task_id} inputs must map names to text")
        services.definitions.get_workflow(workflow, bundle=bundle)
        entry = {"id": task_id, "title": title.strip(), "depends_on": dependencies,
                 "workflow": workflow}
        if objective:
            entry["objective"] = objective
        if bundle is not None:
            entry["bundle"] = bundle
        if preferred is not None:
            entry["preferred"] = preferred
        if "inputs" in item:
            entry["inputs"] = dict(inputs)
        normalized.append(entry)
    try:
        graph.validate(seen, edges)
    except graph.GraphError as exc:
        raise _invalid(str(exc)) from exc
    rationale = raw.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        raise _invalid("rationale must be text")
    document = {"schema": "multiagent.plan/1", "group_id": group_id,
                "tasks": {task["id"]: {key: task[key] for key in ("workflow", "bundle", "inputs") if key in task}
                          for task in normalized}}
    result = {"schema": "multiagent.plan_proposal/1", "planner": name, "group_id": group_id,
              "goal": goal.strip(), "tasks": normalized, "plan": document}
    if rationale:
        result["rationale"] = rationale
    return result


def _manual(_services: Any, request: Mapping[str, Any], _confirm_cloud: bool) -> Mapping[str, Any]:
    return {"tasks": request.get("tasks"), "rationale": request.get("rationale")}


def _deterministic(_services: Any, request: Mapping[str, Any], _confirm_cloud: bool) -> Mapping[str, Any]:
    template = request.get("template")
    if not isinstance(template, Mapping):
        raise _invalid("template must be an object")
    prefix, stages = template.get("prefix"), template.get("stages")
    if not isinstance(prefix, str) or not _ID.fullmatch(prefix):
        raise _invalid("template prefix must be a valid id")
    if not isinstance(stages, list) or not stages:
        raise _invalid("template stages must be a non-empty list")
    ids = []
    for stage in stages:
        if not isinstance(stage, Mapping) or not isinstance(stage.get("id"), str) or not _ID.fullmatch(stage["id"]):
            raise _invalid("Each template stage needs a valid id")
        if set(stage) - {"id", "title", "objective", "depends_on", "workflow", "bundle", "inputs"}:
            raise _invalid(f"Stage {stage['id']} has unknown fields")
        ids.append(stage["id"])
    if len(set(ids)) != len(ids):
        raise _invalid("Template stage ids must be unique")
    tasks = []
    for index, stage in enumerate(stages):
        deps = stage.get("depends_on", [ids[index - 1]] if index else [])
        if not isinstance(deps, list) or any(dep not in ids for dep in deps):
            raise _invalid(f"Stage {stage['id']} depends_on must name template stages")
        task = {"id": f"{prefix}-{stage['id']}", "title": stage.get("title", stage["id"]),
                "depends_on": [f"{prefix}-{dep}" for dep in deps], "workflow": stage.get("workflow")}
        for key in ("objective", "bundle", "inputs"):
            if key in stage:
                task[key] = stage[key]
        tasks.append(task)
    return {"tasks": tasks, "rationale": request.get("rationale")}


register_planner("manual", _manual, description="Validate tasks supplied by the operator")
register_planner("deterministic", _deterministic, description="Expand an ordered stage template")

from .model import propose as _model  # noqa: E402 - register after the shared interface

register_planner("model", _model, description="Ask a configured model to propose tasks")


class PlanningServices:
    """Facade for listed and proposed plans; never creates tasks or runs."""

    def __init__(self, services: Any):
        self._services = services

    def planners(self) -> list[dict[str, str]]:
        return planners()

    @service_errors
    def propose(self, planner: str, request: Any, *, confirm_cloud: bool = False) -> dict[str, Any]:
        return propose(self._services, planner, request, confirm_cloud=confirm_cloud)
