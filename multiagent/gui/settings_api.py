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


class BudgetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providers: dict[str, dict[str, Any]]


class ContinuityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: StrictStr
    wait_threshold_hours: StrictInt
    candidates: dict[str, StrictStr]


def add_settings_routes(api: APIRouter, services: Any) -> None:
    def budget_view():
        resolved = services.gui_settings.resolve("budget")
        return {"source": resolved["source"], "providers": resolved["providers"],
                "known_providers": sorted(services.gui_settings.known_providers() or ()),
                "budgets": [{"id": budget.id, "scope": budget.scope, "project": budget.project,
                             "currency": budget.currency, "amount": budget.amount,
                             "mode": budget.mode or resolved["mode"],
                             "provider": getattr(budget, "provider", None)} for budget in resolved["budgets"]]}

    @api.get("/settings/budget")
    def read_budget():
        return budget_view()

    @api.put("/settings/budget")
    def set_budget(body: BudgetBody):
        services.gui_settings.set_usage("budget", body.model_dump())
        return budget_view()

    @api.get("/settings/continuity")
    def read_continuity():
        return services.gui_settings.resolve("continuity")

    @api.put("/settings/continuity")
    def set_continuity(body: ContinuityBody):
        return services.gui_settings.set_usage("continuity", body.model_dump())

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
