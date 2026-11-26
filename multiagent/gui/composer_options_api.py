# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only connection and executor choices for the composer and Apps & agents."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..services.connections import ConnectionServices

_PICKER_EXECUTOR_IDS = {"claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"}


def composer_options(services, *, local_only: bool = False, connections=None) -> dict:
    """Join existing snapshots without probing a provider or launching an app."""
    connection_service = connections or getattr(services, "connections", None) or ConnectionServices(services)
    rows = connection_service.list().get("connections", [])
    executors = getattr(services, "executors", None)
    by_id = {row["executor_id"]: row for row in
             (executors._composer_options().get("executors", []) if executors is not None else [])}
    options = []
    for row in rows:
        identifier = row.get("id")
        if not identifier:
            continue
        is_app = row.get("kind") == "executor"
        if is_app and identifier not in _PICKER_EXECUTOR_IDS:
            continue
        kind = "app" if is_app else "local" if identifier == "ollama" else "cloud"
        evidence = by_id.get(identifier, {})
        blocked = local_only and kind == "cloud"
        selectable = row.get("enabled") is True and row.get("available") is True and not blocked
        if kind == "app" and (not evidence or evidence.get("available") is not True):
            selectable = False
        option = {
            "id": identifier, "kind": kind, "status": "available" if selectable else "blocked",
            "selectable": selectable, "enabled": row.get("enabled"), "available": row.get("available"),
            "checked_at": row.get("checked_at"), "verification": row.get("verification"),
            "limit": row.get("limit"), "resets_at": row.get("resets_at"), "paused_until": row.get("paused_until"),
            "reason": "local_only" if blocked else row.get("reason") or
                ("executor_unavailable" if kind == "app" and not selectable else None),
            "missing_step": row.get("missing_step"), "quota": row.get("quota"),
            "warning": row.get("warning"), "models": evidence.get("models", []),
            "efforts": evidence.get("efforts", []),
        }
        context_window = evidence.get("context_window", row.get("context_window"))
        if context_window is not None:
            option["context_window"] = context_window
        options.append(option)
    return {"options": options}


def add_composer_options_routes(api: APIRouter, services) -> None:
    connections = getattr(services, "connections", None) or ConnectionServices(services)

    @api.get("/composer/options")
    def list_composer_options(local_only: bool = Query(False)):
        return composer_options(services, local_only=local_only, connections=connections)
