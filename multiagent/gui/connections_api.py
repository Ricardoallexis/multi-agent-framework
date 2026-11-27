# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected, read-only connection checks."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from ..services.connections import ConnectionServices


class TlsTrustBody(BaseModel):
    """ARR3-T07: ``system`` or ``certifi``. A ``file:`` value is only for ``.local/.env`` and gets a 422."""
    model_config = ConfigDict(extra="forbid")
    trust: str


class AcpConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle: str = Field(min_length=1, max_length=128)
    allow_write: bool = False
    keep_history: bool | None = None


class DiscoveryBody(BaseModel):
    """EXA-T05: the person's answer. ``declined`` is also how the consent is withdrawn."""
    model_config = ConfigDict(extra="forbid")
    answer: Literal["accepted", "declined"]


def add_connection_routes(api: APIRouter, services) -> None:
    connections = getattr(services, "connections", None) or ConnectionServices(services)
    @api.get("/connections")
    def list_connections():
        return connections.list()

    @api.get("/connections/discovery")
    def get_discovery():
        return connections.discovery()

    @api.put("/connections/discovery")
    def set_discovery(body: DiscoveryBody):    # only this GUI route writes the answer; no /api/v1 or CLI route does
        return connections.set_discovery(body.answer)

    @api.post("/connections/{connection_id}/check")
    def check_connection(connection_id: str):
        return connections.check(connection_id)

    @api.post("/connections/{connection_id}/probe")
    def probe_connection(connection_id: str):    # explicit provider probe; the person requested it
        return connections.probe(connection_id)

    @api.post("/connections/{connection_id}/acp-probe")
    def probe_acp_connection(connection_id: str):
        return connections.probe_acp(connection_id)

    @api.get("/connections/codex_acp/consent")
    def get_acp_consent(bundle: str = Query(min_length=1, max_length=128)):
        return connections.acp_consent(bundle)

    @api.put("/connections/codex_acp/consent")
    def set_acp_consent(body: AcpConsentBody):
        return connections.set_acp_consent(body.bundle, body.allow_write, body.keep_history)

    @api.post("/connections/{connection_id}/tls-trust")
    def set_tls_trust(connection_id: str, body: TlsTrustBody):
        return connections.set_tls_trust(connection_id, body.trust)
