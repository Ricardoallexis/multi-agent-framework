# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""New GUI: static files at ``/gui`` plus a token-protected ``/gui/api``.

The shell is a client of ``/gui/api`` only. Static files carry no data, so
they need no token; every ``/gui/api/*`` route requires the local token.
Host and Origin checks come from ``LocalAPISecurityMiddleware`` in ``api.py``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..version import __version__
from . import events
from .security import GUISecurityHeadersMiddleware, require_json, require_token

STATIC_DIR = Path(__file__).resolve().parent / "static"


def mount_gui(app: FastAPI) -> None:
    """Serve the GUI at ``/gui`` (same origin as the API)."""
    if not (STATIC_DIR / "index.html").is_file():
        return
    services = app.state.services
    settings = services.system.settings
    app.state.gui_token_file = settings.gui_token_file
    app.add_middleware(GUISecurityHeadersMiddleware)

    api = APIRouter(prefix="/gui/api", dependencies=[Depends(require_token), Depends(require_json)])

    @api.get("/session")
    def session():
        return {"mock": bool(settings.mock_mode), "version": __version__}

    @api.get("/stream")
    async def stream(request: Request, after: int = Query(0, ge=0),
                     last_event_id: str | None = Header(None, alias="Last-Event-ID")):
        cursor = after
        if last_event_id and last_event_id.strip().isdigit():
            cursor = max(cursor, int(last_event_id))
        body = events.stream(
            services.system.store, after=cursor, heartbeat=settings.gui_heartbeat_seconds,
            poll=settings.gui_poll_seconds, max_seconds=settings.gui_stream_max_seconds,
        )
        return StreamingResponse(body, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    app.include_router(api)
    app.mount("/gui/static", StaticFiles(directory=STATIC_DIR), name="gui-static")

    @app.get("/gui", include_in_schema=False)
    def gui_redirect():
        return RedirectResponse("/gui/")

    @app.get("/gui/", include_in_schema=False)
    def gui_index():
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
