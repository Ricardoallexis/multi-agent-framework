# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected routes of the session viewer (GUI-T04) under ``/gui/api/sessions``.

They expose private local data (the user's Claude, Codex and Copilot sessions), so they live in the
GUI router, behind ``require_token``, and are not part of the public ``/api/v1`` API. All the logic
and every validation is in ``services.sessions``: the routes only pass query values through, which
keeps HTTP, CLI and Python answers (and errors) identical.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from ..services.sessions import DEFAULT_ITEMS_LIMIT, DEFAULT_LIST_LIMIT, SessionServices
from .continuity_api import with_extra_children
from .open_in_app import add_open_routes


def add_session_routes(api: APIRouter, sessions: SessionServices,
                       extra_children: Callable[[str], list[dict[str, Any]]] | None = None) -> None:
    @api.get("/sessions/counts")
    def session_counts():
        """Counts of allowed sessions by source and status."""
        return sessions.counts()

    @api.get("/sessions")
    def list_sessions(source: str | None = None, surface: str | None = None, cwd: str | None = None,
                      since: str | None = None, until: str | None = None, text: str | None = None,
                      limit: int = DEFAULT_LIST_LIMIT, after: str | None = None):
        """Sessions, newest first; empty with a ``hint`` until ``gui_session_roots`` is configured."""
        return sessions.list_sessions(source=source, surface=surface, cwd=cwd, since=since, until=until,
                                      text=text, limit=limit, after=after)

    @api.get("/sessions/{session_id}")
    def get_session(session_id: str):
        return sessions.get_session(session_id)

    @api.get("/sessions/{session_id}/items")
    def session_items(session_id: str, after: str | None = None, limit: int = DEFAULT_ITEMS_LIMIT):
        """Items after the cursor (``next_cursor`` of the previous page); text is plain text, never HTML."""
        return sessions.read_items(session_id, after=after, limit=limit)

    @api.get("/sessions/{session_id}/children")
    def session_children(session_id: str, status: str | None = None):
        """Subagents, spawned threads and workers; ``status`` is ``active`` or ``done`` (omit for both)."""
        result = sessions.list_children(session_id, status=status)
        # extra_children: nodes the services do not know, such as the tasks held on this session by a usage limit (CNT-T06)
        return with_extra_children(result, extra_children(session_id), status) if extra_children else result

    add_open_routes(api, sessions)  # POST /sessions/{id}/open and GET /open-in-app (GUI-T06)
