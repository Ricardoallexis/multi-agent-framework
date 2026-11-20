# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token protected, read-only bundle scan status for the GUI."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter


def add_bundle_scan_routes(api: APIRouter, services: Any) -> None:
    @api.get("/bundles/{name}/scan")
    def bundle_scan_status(name: str):
        return services.bundle_scans.status(name)
