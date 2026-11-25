# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected routes of the "Home" steps (INI-T02). Browser only: nothing here is under ``/api/v1``."""
from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


class ModelsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: str


def add_start_routes(api: APIRouter, services: Any) -> None:
    def first_run() -> Any:
        # Looked up on each call, like the other GUI routes: mounting the GUI never needs the service.
        return services.first_run

    def read() -> dict[str, Any]:
        status = first_run().status()
        return {**status, "times_text": first_run().times_text(status)}

    @api.get("/start")
    async def start_status():
        return await anyio.to_thread.run_sync(read)

    @api.post("/settings/models")  # POST because js/api.js only has apiGet and apiPost
    async def set_models(body: ModelsBody):
        return await anyio.to_thread.run_sync(first_run().set_models, body.models)
