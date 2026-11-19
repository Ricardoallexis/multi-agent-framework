# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Advance a task group by queueing ordinary framework runs."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..run_request import RunRequest
from .errors import INVALID_REQUEST, NOT_FOUND, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices


_FAILED_RUNS = {"failed", "cancelled", "rejected"}


class PlanServices:
    def __init__(self, services: ApplicationServices):
        self._services = services

    @staticmethod
    def _document(document: Any) -> tuple[str, dict[str, dict[str, Any]]]:
        def invalid(message: str) -> ServiceError:
            return ServiceError(INVALID_REQUEST, message, status=422)

        if not isinstance(document, Mapping) or document.get("schema") != "multiagent.plan/1":
            raise invalid("Plan schema must be multiagent.plan/1")
        group_id = document.get("group_id")
        if not isinstance(group_id, str) or not group_id:
            raise invalid("Plan group_id must be a non-empty string")
        entries = document.get("tasks")
        if not isinstance(entries, Mapping):
            raise invalid("Plan tasks must map task ids to run specifications")
        specs = {}
        for task_id, spec in entries.items():
            if not isinstance(task_id, str) or not task_id or not isinstance(spec, Mapping):
                raise invalid("Each plan task needs an id and a run specification")
            if set(spec) - {"workflow", "bundle", "inputs"}:
                raise invalid(f"Unknown run specification field for task {task_id}")
            workflow = spec.get("workflow")
            bundle = spec.get("bundle")
            inputs = spec.get("inputs", {})
            if not isinstance(workflow, str) or not workflow:
                raise invalid(f"Plan task {task_id} needs a workflow")
            if bundle is not None and (not isinstance(bundle, str) or not bundle):
                raise invalid(f"Plan task {task_id} bundle must be a non-empty string")
            if not isinstance(inputs, Mapping) or not all(isinstance(k, str) and isinstance(v, str)
                                                         for k, v in inputs.items()):
                raise invalid(f"Plan task {task_id} inputs must map names to strings")
            specs[task_id] = {"workflow": workflow, "bundle": bundle, "inputs": dict(inputs)}
        return group_id, specs

    def _snapshot(self, document: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        group_id, specs = self._document(document)
        group = self._services.tasks.get_group(group_id)
        tasks = self._services.tasks.list_tasks(group_id=group_id)
        known = {task["id"] for task in tasks}
        unknown = sorted(set(specs) - known)
        if unknown:
            raise ServiceError(NOT_FOUND, f"Task not found in group {group_id}: {unknown[0]}", status=404,
                               details={"task": unknown[0]})
        runs = {}
        # The public run list has a 500-run cap. Look up the existing task_id index in each run store instead.
        placeholders = ",".join("?" for _ in known)
        for runtime in self._services.runs._runtimes():
            with runtime.store.db.connect() as conn:
                rows = conn.execute(f"SELECT id,task_id,status,created_at FROM runs WHERE task_id IN ({placeholders}) "
                                    "ORDER BY created_at DESC, rowid DESC", tuple(known)).fetchall() if known else []
            for row in rows:
                if row["task_id"] not in runs or row["created_at"] > runs[row["task_id"]]["created_at"]:
                    runs[row["task_id"]] = {"id": row["id"], "status": row["status"],
                                            "created_at": row["created_at"]}
        views = []
        for task in tasks:
            views.append({"id": task["id"], "status": task["status"], "state": task["state"],
                          "blockers": task["blockers"],
                          "run": ({"id": runs[task["id"]]["id"], "status": runs[task["id"]]["status"]}
                                  if task["id"] in runs else None)})
        if any(task["state"] in {"failed", "cancelled"} or
               (task["run"] and task["run"]["status"] in _FAILED_RUNS) for task in views):
            state = "failed"
        elif any(task["state"] in {"claimed", "running", "paused", "validating", "needs_changes"} or
                 (task["run"] and task["run"]["status"] not in {"completed", *_FAILED_RUNS}) for task in views):
            state = "running"
        elif views and all(task["state"] in {"submitted", "accepted"} for task in views):
            state = "completed"
        else:
            state = "waiting"
        return {"group_id": group_id, "status": state, "tasks": views}, specs

    @service_errors
    def status(self, document: Any) -> dict[str, Any]:
        """Read task states, latest linked runs and dependency blockers without writing."""
        return self._snapshot(document)[0]

    @service_errors
    def advance(self, document: Any, *, actor: str) -> dict[str, Any]:
        """Reconcile finished runs, then start ready planned tasks once."""
        if not isinstance(actor, str) or not actor.strip() or len(actor.strip()) > 100:
            raise ServiceError(INVALID_REQUEST, "actor must be non-empty text of at most 100 characters", status=422)
        current, specs = self._snapshot(document)
        tasks = self._services.tasks
        for task in current["tasks"]:
            run = task["run"]
            if task["state"] != "running" or run is None:
                continue
            if run["status"] == "completed":
                assignee = tasks.get_task(task["id"])["assignee"]
                tasks.transition(task["id"], "submit", actor=assignee, cause=f"Run {run['id']} completed")
            elif run["status"] in _FAILED_RUNS:
                tasks.transition(task["id"], "fail", actor=actor, cause=f"Run {run['id']} {run['status']}")
        current, _ = self._snapshot(document)
        group = tasks.get_group(current["group_id"])
        for task in current["tasks"]:
            if (task["id"] not in specs or task["status"] != "ready" or
                    (task["run"] is not None and task["run"]["status"] not in {"completed", *_FAILED_RUNS})):
                continue
            spec = specs[task["id"]]
            project_name = group["title"][:120]
            if len(project_name) < 2:
                project_name = f"{group['id']} plan"[:120]
            request = RunRequest(workflow_id=spec["workflow"], project_name=project_name,
                                 inputs=spec["inputs"], task_id=task["id"])
            self._services.definitions.get_workflow(spec["workflow"], bundle=spec["bundle"])
            tasks.transition(task["id"], "claim", actor=actor)
            tasks.transition(task["id"], "start", actor=actor)
            try:
                self._services.runs.create(request, bundle=spec["bundle"])
            except Exception as exc:
                # Task and run live in different databases: a task left running without a run would never move,
                # so it fails visibly (its dependents stay blocked) and the error still reaches the caller.
                tasks.transition(task["id"], "fail", actor=actor, cause=f"Run not created: {type(exc).__name__}")
                raise
        return self.status(document)
