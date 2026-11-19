# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected, explicitly approved launches from «Sesión única»."""
from __future__ import annotations

from uuid import uuid4

import anyio
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..services.errors import INVALID_REQUEST, ServiceError

_EXECUTORS = frozenset({"claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"})


class ExecutorLaunchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=4000)
    executor_id: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    effort: str | None = Field(default=None, max_length=32)
    confirmed: StrictBool = False


def add_executor_launch_routes(api: APIRouter, services) -> None:
    @api.post("/executors/launch")
    async def launch(body: ExecutorLaunchBody, response: Response):
        task = body.task.strip()
        if not task:
            raise ServiceError(INVALID_REQUEST, "task must not be empty", status=422, details={"field": "task"})
        if body.confirmed is not True:
            raise ServiceError("confirmation_required", "Confirm the executor, model and quota before launching.",
                               status=409, details={"field": "confirmed"})
        if body.executor_id not in _EXECUTORS:
            raise ServiceError(INVALID_REQUEST, "Unknown executor", status=422,
                               details={"field": "executor_id", "executor_id": body.executor_id})
        model = body.model.strip() if body.model is not None else None
        effort = body.effort.strip() if body.effort is not None else None
        if body.model is not None and not model:
            raise ServiceError(INVALID_REQUEST, "model must not be empty", status=422, details={"field": "model"})
        if body.effort is not None and not effort:
            raise ServiceError(INVALID_REQUEST, "effort must not be empty", status=422, details={"field": "effort"})
        if body.executor_id == "copilot_cli" and not model:
            raise ServiceError("model_required", "Copilot CLI requires an explicit model.", status=422,
                               details={"executor_id": body.executor_id})

        identity = uuid4().hex
        group_id = "GuiExec-" + identity
        task_id = "gui-executor-" + identity
        services.tasks.create_group(group_id, "Sesión única con ejecutor", actor="user:operator", authorized=True)
        services.tasks.create_task(group_id, task_id, "Sesión única con ejecutor", actor="user:operator",
                                   objective=task)
        try:
            result = await anyio.to_thread.run_sync(lambda: services.executors.run_task(
                task_id, body.executor_id, model, effort, actor="user:operator"))
        except ServiceError as exc:
            # A refused launch (disabled executor, quota floor, agy busy) leaves no open task behind; the refusal and
            # its reason still reach the page unchanged.
            try:
                services.tasks.transition(task_id, "cancel", actor="user:operator",
                                          cause=f"executor launch refused: {exc.code}")
            except ServiceError:
                pass
            raise
        status = services.executors.status(task_id)
        if isinstance(result, dict) and result.get("outcome") == "running":
            response.status_code = 202
        return {"task_id": task_id, "launch": result, "status": status}

    @api.get("/executors/launch/{task_id}")
    def launch_status(task_id: str):
        """Return the executor service's current task and attempt status without launching anything."""
        return services.executors.status(task_id)
