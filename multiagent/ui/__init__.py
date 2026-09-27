# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Stage 0 operator UI: static files served by the same FastAPI app at ``/ui``.

The UI is a client of the public HTTP API only (``/api/v1``); this module just
serves its files. It adds no routes with business logic and no persistence.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"


def mount_ui(app: FastAPI) -> None:
    """Serve the UI at ``/ui`` (same origin as the API, so no CORS is needed)."""
    if not (STATIC_DIR / "index.html").is_file():
        return  # installed without UI assets: the API keeps working
    app.mount("/ui/static", StaticFiles(directory=STATIC_DIR), name="ui-static")

    @app.get("/ui", include_in_schema=False)
    def ui_redirect():
        return RedirectResponse("/ui/")

    @app.get("/ui/", include_in_schema=False)
    def ui_index():
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
