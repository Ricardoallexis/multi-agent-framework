# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Browser-only decision routes for one-use user approvals."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..services.errors import INVALID_STATE, ServiceError
from .security import APPROVAL_COOKIE, APPROVAL_COOKIE_PATH, require_approval_session


class SessionBody(BaseModel):
    code: str = Field(min_length=1, max_length=256)


class DecisionBody(BaseModel):
    code: str = Field(default="", max_length=256)
    decision: Literal["approve", "reject"]


def add_approval_routes(api: APIRouter, services: Any) -> None:
    """``api`` already requires the GUI bearer token and JSON for writes."""

    @api.get("/approvals")
    def pending():
        return {"items": services.user_approvals.pending()}

    @api.post("/approvals/session")
    def session(body: SessionBody, request: Request, response: Response):
        value = request.app.state.approval_sessions.exchange(body.code)
        response.set_cookie(APPROVAL_COOKIE, value, httponly=True, samesite="strict",
                            path=APPROVAL_COOKIE_PATH, secure=request.url.scheme == "https")
        response.headers["Cache-Control"] = "no-store"
        return {"active": True}

    @api.post("/approvals/{approval_id}/challenge")
    def challenge(approval_id: str, request: Request,
                  browser_session: str = Depends(require_approval_session)):
        approval = services.user_approvals.status(approval_id)
        if approval["state"] != "pending":
            raise ServiceError(INVALID_STATE, "Approval is no longer pending", status=409)
        code = request.app.state.approval_sessions.challenge(approval_id, approval["sha256"], browser_session)
        return {"code": code, "expires_in": 120, "sha256": approval["sha256"]}

    @api.post("/approvals/{approval_id}/decision")
    def decision(approval_id: str, body: DecisionBody, request: Request,
                 browser_session: str = Depends(require_approval_session)):
        approval = services.user_approvals.status(approval_id)
        request.app.state.approval_sessions.verify(approval_id, approval["sha256"], browser_session, body.code)
        return services.user_approvals.decide(approval_id, body.decision)
