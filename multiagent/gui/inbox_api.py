# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected GUI routes for the person's run and task inboxes (AVA-T05)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field


ACTOR = "user:operator"


class MessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64)
    kind: Literal["cancel", "interrupt", "adjust", "note"]
    text: str = ""


def add_inbox_routes(api: APIRouter, services: Any) -> None:
    """Register inbox reads and writes using the GUI's existing token dependency."""

    @api.get("/runs/{run_id}/messages")
    def run_messages(run_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return services.inbox.messages("run", run_id, after=after, limit=limit)

    @api.post("/runs/{run_id}/messages")
    def post_run_message(run_id: str, body: MessageBody):
        return services.inbox.post("run", run_id, message_id=body.message_id, kind=body.kind,
                                   text=body.text, actor=ACTOR)

    @api.get("/tasks/{task_id}/messages")
    def task_messages(task_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return services.inbox.messages("task", task_id, after=after, limit=limit)

    @api.post("/tasks/{task_id}/messages")
    def post_task_message(task_id: str, body: MessageBody):
        return services.inbox.post("task", task_id, message_id=body.message_id, kind=body.kind,
                                   text=body.text, actor=ACTOR)

    @api.get("/inbox/policy")
    def inbox_policy():
        policy = services.inbox.policy()
        return {"enabled": policy.enabled,
                "reason": "La política de disponibilidad está apagada en este servidor." if not policy.enabled else ""}
