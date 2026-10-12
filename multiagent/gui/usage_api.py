# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected routes of the usage service (USO-T02) under ``/gui/api/usage``.

They expose private local data (what the user's Claude, Codex and Copilot plans allow and how much of it
their sessions used), so they live in the GUI router, behind ``require_token``, like the session viewer's.
All the logic and every validation is in ``services.usage``: the routes only pass query values through,
which keeps HTTP, CLI and Python answers (and errors) identical. The framework's own provider costs are
also served, without a token, at ``/api/v1/usage/costs`` (see ``api.py``).
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..services.usage import DEFAULT_RUN_LIMIT, UsageServices


def add_usage_routes(api: APIRouter, usage: UsageServices) -> None:
    @api.get("/usage/plans")
    def usage_plans():
        """Plan usage per app: percent and reset only where the app leaves them on disk, else ``not_available``."""
        return usage.plans()

    @api.get("/usage/tokens")
    def usage_tokens(since: str | None = None, until: str | None = None, apps: list[str] | None = Query(None),
                     group_by: list[str] | None = Query(None)):
        """Tokens per app, model, level and role inside the window (default: the last 7 days)."""
        return usage.tokens(since=since, until=until, apps=apps, group_by=group_by)

    @api.get("/usage/costs")
    def usage_costs(since: str | None = None, until: str | None = None, limit: int = DEFAULT_RUN_LIMIT):
        """Estimated cost of the framework's provider calls per run, model, day and month, with price evidence."""
        return usage.costs(since=since, until=until, limit=limit)
