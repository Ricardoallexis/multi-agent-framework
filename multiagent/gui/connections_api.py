# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected, read-only connection checks."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ..services.connections import ConnectionServices


class TlsTrustBody(BaseModel):
    """ARR3-T07: ``system`` or ``certifi``. A ``file:`` value is only for ``.local/.env`` and gets a 422."""
    model_config = ConfigDict(extra="forbid")
    trust: str


def add_connection_routes(api: APIRouter, services) -> None:
    connections = getattr(services, "connections", None) or ConnectionServices(services)
    @api.get("/connections")
    def list_connections():
        return connections.list()

    @api.post("/connections/{connection_id}/check")
    def check_connection(connection_id: str):
        return connections.check(connection_id)

    @api.post("/connections/{connection_id}/probe")
    def probe_connection(connection_id: str):    # explicit provider probe; the person requested it
        return connections.probe(connection_id)

    @api.post("/connections/{connection_id}/acp-probe")
    def probe_acp_connection(connection_id: str):
        return connections.probe_acp(connection_id)

    @api.post("/connections/{connection_id}/tls-trust")
    def set_tls_trust(connection_id: str, body: TlsTrustBody):
        return connections.set_tls_trust(connection_id, body.trust)
