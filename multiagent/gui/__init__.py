# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""New GUI: static files at ``/gui`` plus a token-protected ``/gui/api``.

The shell is a client of ``/gui/api`` only. Static files carry no data, so
they need no token; every ``/gui/api/*`` route requires the local token.
Host and Origin checks come from ``LocalAPISecurityMiddleware`` in ``api.py``.

``/gui/api/sessions`` (list, one session, its items, its children) and ``/gui/api/stream?session=`` (live
follow; ``&only=children`` for the subagents panel) serve the session viewer; see ``sessions_api`` and ``events``.
"""
from __future__ import annotations

import re
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..services.errors import INVALID_REQUEST, ServiceError
from ..services.sessions import SessionServices
from ..version import __version__
from . import events
from .runs import build_router as build_runs_router
from .security import GUISecurityHeadersMiddleware, require_json, require_token
from .sessions_api import add_session_routes

STATIC_DIR = Path(__file__).resolve().parent / "static"
_INT_CURSOR = re.compile(r"[0-9]{1,15}")
CHILDREN_POLL_FACTOR = 10  # the children are looked at every 10 polls of the item stream (5 s by default)


def mount_gui(app: FastAPI) -> None:
    """Serve the GUI at ``/gui`` (same origin as the API)."""
    if not (STATIC_DIR / "index.html").is_file():
        return
    services = app.state.services
    settings = services.system.settings
    sessions = getattr(services, "sessions", None) or SessionServices(services)
    app.state.gui_token_file = settings.gui_token_file
    app.add_middleware(GUISecurityHeadersMiddleware)

    api = APIRouter(prefix="/gui/api", dependencies=[Depends(require_token), Depends(require_json)])

    @api.get("/session")
    def session():
        return {"mock": bool(settings.mock_mode), "version": __version__}

    @api.get("/stream")
    async def stream(request: Request, after: str | None = Query(None, max_length=64),
                     session: str | None = Query(None, max_length=200),
                     only: str | None = Query(None, pattern="^children$"),
                     last_event_id: str | None = Header(None, alias="Last-Event-ID")):
        timing = dict(heartbeat=settings.gui_heartbeat_seconds, poll=settings.gui_poll_seconds,
                      max_seconds=settings.gui_stream_max_seconds)
        if only is not None and session is None:
            raise ServiceError(INVALID_REQUEST, "only=children needs a session", status=422,
                               details={"field": "only"})
        if session is None:  # every run event of the system store
            if after is not None and _INT_CURSOR.fullmatch(after) is None:
                raise ServiceError(INVALID_REQUEST, "after must be an integer >= 0", status=422,
                                   details={"field": "after"})
            cursor = int(after or 0)
            if last_event_id and _INT_CURSOR.fullmatch(last_event_id.strip()):  # a bad header is ignored, like on a session
                cursor = max(cursor, int(last_event_id))
            body = events.stream(services.system.store, after=cursor, **timing)
        elif only == "children":  # the subagents panel: just "something changed", validated up front too
            await anyio.to_thread.run_sync(sessions.get_session, session)
            body = events.stream_children(lambda: sessions.poll_children(session),
                                          interval=max(settings.gui_poll_seconds * CHILDREN_POLL_FACTOR, 0.05),
                                          heartbeat=timing["heartbeat"], max_seconds=timing["max_seconds"])
        else:  # one session: validated up front, so a bad id or cursor is a 400/404 and not a dead stream
            follow = await anyio.to_thread.run_sync(sessions.open_follow, session, after, last_event_id)
            if follow.is_framework:
                body = events.stream(follow.store, after=int(follow.cursor), run_id=follow.run_id, **timing)
            else:
                body = events.stream_session(lambda cursor: sessions.poll_items(follow.session_id, cursor),
                                             after=follow.cursor, **timing)
        return StreamingResponse(body, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    add_session_routes(api, sessions)
    api.include_router(build_runs_router(services))
    app.include_router(api)
    app.mount("/gui/static", StaticFiles(directory=STATIC_DIR), name="gui-static")

    @app.get("/gui", include_in_schema=False)
    def gui_redirect():
        return RedirectResponse("/gui/")

    @app.get("/gui/", include_in_schema=False)
    def gui_index():
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
