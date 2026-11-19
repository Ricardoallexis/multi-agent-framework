# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected, read-only connection checks."""
from __future__ import annotations

from fastapi import APIRouter
from ..services.connections import ConnectionServices


def add_connection_routes(api: APIRouter, services) -> None:
    connections = getattr(services, "connections", None) or ConnectionServices(services)
    @api.get("/connections")
    def list_connections():
        return connections.list()

    @api.post("/connections/{connection_id}/check")
    def check_connection(connection_id: str):
        return connections.check(connection_id)
