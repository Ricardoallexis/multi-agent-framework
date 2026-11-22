# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""New GUI: static files at ``/gui`` plus a token-protected ``/gui/api``.

The shell is a client of ``/gui/api`` only. Static files carry no data, so
they need no token; every ``/gui/api/*`` route requires the local token.
Host and Origin checks come from ``LocalAPISecurityMiddleware`` in ``api.py``.

``/gui/api/sessions`` (list, counts, one session, its items, its children) and ``/gui/api/stream?session=`` (live
follow; ``&only=children`` for the subagents panel) serve the session viewer; see ``sessions_api`` and ``events``.
``/gui/api/usage/{plans,tokens,costs}`` serve the usage view (plan usage, tokens and estimated cost); see ``usage_api``.
``/gui/api/evaluations`` serves the evaluation view: sets, saved runs and the comparison with the baseline (EVA-T03, read only); see ``evaluation_api``.
``/gui/api/teams`` serves the teams view: versions, the org chart and the human approval of a proposal (EQP-T05); see ``teams_api``.
``/gui/api/plan-proposals`` serves the plan proposals view: list, inspect, edit, reassign and decide (PLN-T04); see ``plans_api``.
``/gui/api/continuity/*`` and ``/gui/api/tasks/{id}/continuity/decision`` serve the decision cards of tasks held by a usage limit
(CNT-T06); see ``continuity_api``.
``/gui/api/extensions`` lists the folders of ``static/extensions/`` that views, panel slots and help texts are added
from without editing shared files; see ``extensions``.
``/gui/api/document-alerts`` lists the quarantines and content reviews of runs for the ``document_alerts`` extension (BDI-T05, read only); see ``document_alerts_api``.
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
from ..services.usage import UsageServices
from ..version import __version__
from . import events
from .continuity_api import add_continuity_routes, held_children, with_extra_children
from .approvals_api import add_approval_routes
from .bundle_scans_api import add_bundle_scan_routes
from .connections_api import add_connection_routes
from .document_alerts_api import add_document_alerts_routes
from .executor_launch_api import add_executor_launch_routes
from .evaluation_api import add_evaluation_routes
from .extensions import discover_extensions
from .inbox_api import add_inbox_routes
from .plans_api import add_plans_routes
from .pending_api import add_pending_routes
from .project_api import add_project_routes
from .runs import build_router as build_runs_router
from .security import ApprovalSessions, GUISecurityHeadersMiddleware, require_json, require_token
from .sessions_api import add_session_routes
from .settings_api import add_settings_routes
from .setup_api import add_setup_routes
from .teams_api import add_teams_routes
from .threads_api import add_thread_routes
from .usage_api import add_usage_routes
from ..services.work_map import build_work_map

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
    usage = getattr(services, "usage", None) or UsageServices(services)
    app.state.gui_token_file = settings.gui_token_file
    app.state.approval_sessions = ApprovalSessions()
    app.add_middleware(GUISecurityHeadersMiddleware)

    api = APIRouter(prefix="/gui/api", dependencies=[Depends(require_token), Depends(require_json)])

    @api.get("/session")
    def session():
        return {"mock": bool(settings.mock_mode), "version": __version__, "mode": services.gui_settings.mode()}

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
            body = events.stream_children(lambda: with_extra_children(sessions.poll_children(session),
                                                                      held_children(services, session)),
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

    @api.get("/extensions")
    def extensions():  # the extension folders the page loads (FLU-T04); found on every call, no list to edit
        return {"extensions": discover_extensions(STATIC_DIR)}

    @api.get("/map")
    def work_map(project: str | None = Query(None, max_length=128),
                 only: str = Query("active", pattern="^(active|all)$")):
        return build_work_map(services, project_id=project, only=only)

    add_session_routes(api, sessions, extra_children=lambda session_id: held_children(services, session_id))  # + held tasks (CNT-T06)
    add_usage_routes(api, usage)  # /gui/api/usage/{plans,tokens,costs} (USO-T02)
    add_connection_routes(api, services)  # SES-T01
    add_executor_launch_routes(api, services)  # SES-T04
    add_setup_routes(api, services)  # /gui/api/setup/* (INS-T05)
    add_project_routes(api, services)  # /gui/api/projects (PRY-T03)
    add_document_alerts_routes(api, services)
    add_continuity_routes(api, services)  # /gui/api/continuity/* (CNT-T06)
    add_evaluation_routes(api, services)  # /gui/api/evaluations/* (EVA-T03, read only)
    add_teams_routes(api, services)  # /gui/api/teams/* (EQP-T05)
    add_plans_routes(api, services)  # /gui/api/plan-proposals/* (PLN-T04)
    add_pending_routes(api, services)  # /gui/api/pending (RWD-T03)
    add_approval_routes(api, services)
    add_bundle_scan_routes(api, services)
    add_settings_routes(api, services)  # /gui/api/settings (SIM-T01)
    add_thread_routes(api, services)  # /gui/api/threads/runs/{run_id} (CHT-T01)
    add_inbox_routes(api, services)  # AVA-T05: run and task messages
    api.include_router(build_runs_router(services))
    app.include_router(api)
    app.mount("/gui/static", StaticFiles(directory=STATIC_DIR), name="gui-static")

    @app.get("/gui", include_in_schema=False)
    def gui_redirect():
        return RedirectResponse("/gui/")

    @app.get("/gui/", include_in_schema=False)
    def gui_index():
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
