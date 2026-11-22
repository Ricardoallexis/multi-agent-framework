# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token protected routes of the server-side GUI settings (SIM-T01)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


class ModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str


def add_settings_routes(api: APIRouter, services: Any) -> None:
    @api.get("/settings")
    def read_settings():
        return services.gui_settings.read()

    @api.post("/settings/mode")
    def set_mode(body: ModeBody):  # POST because js/api.js only has apiGet and apiPost
        return services.gui_settings.set_mode(body.mode)
