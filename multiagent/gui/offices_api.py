# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only GUI routes for installed offices (VOF-T01)."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter

from ..services.errors import NOT_FOUND, ServiceError


def _read_office(office_id: str, read: Callable[[str], Any]) -> Any:
    try:
        return read(office_id)
    except ServiceError as error:
        if error.details.get("reason") == "office_not_installed":
            raise ServiceError(NOT_FOUND, f"Office not found: {office_id}", status=404,
                               details={"office": office_id}) from error
        raise


def add_office_routes(api: APIRouter, services: Any) -> None:
    @api.get("/offices")
    def list_offices() -> dict[str, Any]:
        offices = services.offices.list()
        return {"offices": [
            {"id": office["id"], "name": office["name"], "state": office["state"],
             "areas": len(office["areas"])}
            for office in offices
        ]}

    @api.get("/offices/{office_id}")
    def get_office(office_id: str) -> dict[str, Any]:
        office = _read_office(office_id, services.offices.get)
        requirements = _read_office(office_id, services.offices.requirements)
        return {**office, "requirements": requirements}

    @api.get("/offices/{office_id}/scene")
    def office_scene(office_id: str) -> dict[str, Any]:
        return _read_office(office_id, services.offices.scene)
