# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected, explicitly approved launches from "Single session"."""
from __future__ import annotations

from uuid import uuid4

import anyio
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..services.errors import INVALID_REQUEST, ServiceError
from ..services.limits import LIMIT_REASONS, limit_notice

_EXECUTORS = frozenset({"claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"})
_SESSION_SOURCES = {"claude_cli": "claude", "codex_cli": "codex", "copilot_cli": "copilot",
                    "antigravity_cli": "antigravity"}


class ExecutorLaunchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=4000)
    executor_id: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    effort: str | None = Field(default=None, max_length=32)
    confirmed: StrictBool = False
    accept_quota_warning: StrictBool = False


def add_executor_launch_routes(api: APIRouter, services) -> None:
    def launch_status(task_id: str):
        status = services.executors.status(task_id)
        results = services.envelopes.list_results(task_id, latest_only=True)
        latest = results[0] if results else None
        status["latest_result"] = ({key: latest[key] for key in ("summary", "attempt", "ref")}
                                   if latest is not None else None)
        for session in status["task"]["sessions"]:
            source = _SESSION_SOURCES.get(session.get("executor_id"))
            session["readable"] = False
            if source and session.get("session_id"):
                try:
                    services.sessions.get_session(f"{source}:{session['session_id']}")
                except ServiceError:
                    pass
                else:
                    session["readable"] = True
        return status

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
        services.tasks.create_group(group_id, "Single session with executor", actor="user:operator", authorized=True)
        services.tasks.create_task(group_id, task_id, "Single session with executor", actor="user:operator",
                                   objective=task, continuity={"mode": "manual"})
        try:
            result = await anyio.to_thread.run_sync(lambda: services.executors.run_task(
                task_id, body.executor_id, model, effort, actor="user:operator",
                accept_quota_warning=body.accept_quota_warning))
        except ServiceError as exc:
            # A refused launch (disabled executor, quota floor, agy busy) leaves no open task behind; the refusal and
            # its reason still reach the page unchanged.
            try:
                services.tasks.transition(task_id, "cancel", actor="user:operator",
                                          cause=f"executor launch refused: {exc.code}")
            except ServiceError:
                pass
            reason = str(exc.details.get("reason") or exc.details.get("code") or "")
            if reason in LIMIT_REASONS or "quota" in reason:
                raise ServiceError(exc.code, exc.message, status=exc.status,
                                   details={**exc.details, "limit": limit_notice(services, body.executor_id)}) from exc
            raise
        if isinstance(result, dict) and result.get("outcome") == "waiting_turn":
            # PAR-T03: no place for this launch now. The single-session task is born here and nothing started, so it is
            # retired instead of left open; the page is told why and the person launches again when a place frees.
            try:
                services.tasks.transition(task_id, "cancel", actor="user:operator", cause="executor launch waiting for a turn")
            except ServiceError:
                pass
            waiting = dict(result.get("waiting") or {})
            raise ServiceError("waiting_turn", f"No free place now: {waiting.get('reason') or 'concurrency limit reached'}. "
                               "Nothing was started; launch again when a running session ends.", status=409,
                               details={"reason": "waiting_turn", "waiting": waiting, "task_id": None})
        if isinstance(result, dict) and str(result.get("reason") or "") in LIMIT_REASONS:
            raise ServiceError("limit_reached", "Executor limit reached.", status=409,
                               details={"limit": limit_notice(services, body.executor_id)})
        if isinstance(result, dict) and result.get("outcome") == "quota_exhausted" and any(
            str(note).startswith("not_started:") for note in result.get("notes") or []
        ):
            try:
                services.tasks.transition(task_id, "cancel", actor="user:operator",
                                          cause="executor launch refused: limit_reached")
            except ServiceError:
                pass
            raise ServiceError("limit_reached", "Executor limit reached.", status=409,
                               details={"task_id": task_id,
                                        "limit": limit_notice(services, body.executor_id,
                                                              retry_at=(result.get("hold") or {}).get("reset_at"))})
        status = launch_status(task_id)
        if isinstance(result, dict) and result.get("outcome") == "running":
            response.status_code = 202
        return {"task_id": task_id, "launch": result, "status": status,
                "limit": (limit_notice(services, body.executor_id,
                                       retry_at=(result.get("hold") or {}).get("reset_at"))
                          if isinstance(result, dict) and result.get("outcome") == "quota_exhausted" else None)}

    @api.get("/executors/launch/{task_id}")
    def get_launch_status(task_id: str):
        """Return the task, attempt and latest submitted result without launching anything."""
        return launch_status(task_id)
