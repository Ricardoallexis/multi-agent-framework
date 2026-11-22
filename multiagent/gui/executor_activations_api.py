# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected routes of «Activar» (EJE-T01). Browser only: no ``/api/v1`` route, no CLI command, no MCP tool.

The one-use code is the approvals one: ``POST .../activation`` makes the (hidden) ``executor_activate`` approval,
the GUI decides it with ``/gui/api/approvals/{id}/challenge`` and ``/decision``, and ``.../activate`` spends it.
"""
from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActivateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str = Field(min_length=1, max_length=256)


def add_executor_activation_routes(api: APIRouter, services: Any) -> None:
    def activations() -> Any:
        # Looked up on each call, like the other GUI routes: mounting the GUI never needs the service.
        return services.executor_activations

    @api.get("/executors/activations")
    async def status():
        return {"executors": await anyio.to_thread.run_sync(activations().status)}

    @api.post("/executors/stop-all")
    async def stop_all(body: EmptyBody):
        return await anyio.to_thread.run_sync(activations().stop_all)

    @api.post("/executors/{executor_id}/activation")
    async def request_activation(executor_id: str, body: EmptyBody):
        result = await anyio.to_thread.run_sync(activations().request, executor_id)
        return {**result, "text": result["note"]}

    @api.post("/executors/{executor_id}/activate")
    async def activate(executor_id: str, body: ActivateBody):
        return await anyio.to_thread.run_sync(activations().activate, executor_id, body.approval_id)

    @api.post("/executors/{executor_id}/deactivate")
    async def deactivate(executor_id: str, body: EmptyBody):
        return await anyio.to_thread.run_sync(activations().deactivate, executor_id)
