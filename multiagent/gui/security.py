# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Security pieces of the GUI: CSP headers and the API dependencies."""
from __future__ import annotations

import hmac
import secrets
import threading
import time

from fastapi import HTTPException, Request
from starlette.datastructures import MutableHeaders

from .token import read_token

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'; require-trusted-types-for 'script'"
)


def is_gui_path(path: str) -> bool:
    return path == "/gui" or path.startswith("/gui/")


class GUISecurityHeadersMiddleware:
    """Apply the GUI policy to documents, assets, redirects, API answers and errors."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not is_gui_path(path):
            await self.app(scope, receive, send)
            return

        cacheable_path = path in {"/gui", "/gui/", "/gui/static"} or path.startswith("/gui/static/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                if cacheable_path and "cache-control" not in headers:
                    headers["Cache-Control"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail={
        "code": "unauthorized", "message": "Missing or invalid GUI token", "details": {},
    }, headers={"WWW-Authenticate": "Bearer"})


def require_token(request: Request) -> None:
    """Dependency: ``Authorization: Bearer <token>`` compared in constant time."""
    expected = read_token(request.app.state.gui_token_file)
    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    if expected is None or scheme.lower() != "bearer" or not supplied.strip():
        raise _unauthorized()
    if not hmac.compare_digest(supplied.strip().encode("utf-8"), expected.encode("utf-8")):
        raise _unauthorized()


def require_json(request: Request) -> None:
    """Dependency: state-changing requests must declare ``application/json``."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail={
            "code": "unsupported_media_type", "message": "Content-Type must be application/json", "details": {},
        })


# Approval sessions are process-local. Restarting the GUI invalidates every cookie and challenge.
APPROVAL_COOKIE = "multiagent_approval_session"
APPROVAL_COOKIE_PATH = "/gui/api/approvals"
APPROVAL_WINDOW_SECONDS = 120


def _click_required(reason: str) -> HTTPException:
    return HTTPException(status_code=409, detail={
        "code": "invalid_state", "message": "Aprueba en la GUI → Aprobaciones",
        "details": {"reason": "user_click_required", "cause": reason,
                    "where": "Aprueba en la GUI → Aprobaciones"},
    })


class ApprovalSessions:
    """One bootstrap exchange and one short-lived challenge for each decision."""

    def __init__(self, bootstrap_code: str | None = None, *, clock=None):
        self.bootstrap_code = bootstrap_code
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._session: str | None = None
        self._challenges: dict[str, tuple[str, str, str, float]] = {}

    def exchange(self, code: str) -> str:
        with self._lock:
            if self.bootstrap_code is None or self._session is not None or not code or not hmac.compare_digest(
                    code, self.bootstrap_code):
                raise HTTPException(status_code=409, detail={
                    "code": "invalid_state", "message": "Another tab or browser has already taken the approvals; restart the GUI",
                    "details": {"reason": "approval_session_unavailable"},
                })
            self._session = secrets.token_urlsafe(32)
            self.bootstrap_code = None
            return self._session

    def valid(self, cookie: str | None) -> bool:
        with self._lock:
            return bool(cookie and self._session and hmac.compare_digest(cookie, self._session))

    def challenge(self, approval_id: str, sha256: str, session: str) -> str:
        with self._lock:
            code = secrets.token_urlsafe(32)
            self._challenges[approval_id] = (code, sha256, session, self.clock() + APPROVAL_WINDOW_SECONDS)
            return code

    def verify(self, approval_id: str, sha256: str, session: str, code: str) -> None:
        with self._lock:
            challenge = self._challenges.get(approval_id)
            if (challenge is None or not code or not hmac.compare_digest(code, challenge[0])
                    or sha256 != challenge[1] or session != challenge[2]
                    or self.clock() >= challenge[3]):
                raise _click_required("invalid_or_expired_code")
            del self._challenges[approval_id]


def require_approval_session(request: Request) -> str:
    """Dependency for a browser session cookie, in addition to the GUI bearer token."""
    session = request.cookies.get(APPROVAL_COOKIE)
    if not request.app.state.approval_sessions.valid(session):
        raise _click_required("missing_or_invalid_session")
    return session
