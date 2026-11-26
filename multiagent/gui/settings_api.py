# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token protected routes of the server-side GUI settings (SIM-T01)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr


class ModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str


class ConcurrencyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: StrictStr | None = None
    margin: StrictInt | None = None
    manual: StrictInt | None = None
    per_app: dict[StrictStr, StrictInt] | None = None
    per_model: dict[StrictStr, StrictInt] | None = None
    allow_t4: StrictBool | None = None
    duration: StrictStr | None = None
    expires_at: StrictStr | None = None
    selection: StrictStr | None = None


class ModelLevelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: StrictStr
    level: StrictStr
    source: StrictStr


def add_settings_routes(api: APIRouter, services: Any) -> None:
    @api.get("/settings/concurrency")
    def read_concurrency():
        return services.concurrency.effective()

    @api.put("/settings/concurrency")
    def set_concurrency(body: ConcurrencyBody):
        services.gui_settings.set_concurrency(body.model_dump(exclude_unset=True))
        return services.concurrency.effective()

    @api.put("/settings/model-level")
    def confirm_model_level(body: ModelLevelBody):
        return services.gui_settings.confirm_model_level(body.model_id, body.level, body.source)

    @api.get("/settings")
    def read_settings():
        return services.gui_settings.read()

    @api.post("/settings/mode")
    def set_mode(body: ModeBody):  # POST because js/api.js only has apiGet and apiPost
        return services.gui_settings.set_mode(body.mode)
