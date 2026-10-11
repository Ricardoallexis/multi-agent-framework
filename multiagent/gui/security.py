# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Security pieces of the GUI: CSP headers and the API dependencies."""
from __future__ import annotations

import hmac

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
        if scope["type"] != "http" or not is_gui_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
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
