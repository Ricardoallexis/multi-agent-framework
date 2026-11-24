# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Browser-only decision routes for one-use user approvals."""
from __future__ import annotations

import logging
import threading
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..services.errors import INVALID_STATE, ServiceError
from .security import APPROVAL_COOKIE, APPROVAL_COOKIE_PATH, require_approval_session

_logger = logging.getLogger(__name__)


class SessionBody(BaseModel):
    code: str = Field(min_length=1, max_length=256)


class DecisionBody(BaseModel):
    code: str = Field(default="", max_length=256)
    decision: Literal["approve", "reject"]
    risk_ack: bool = False


def add_approval_routes(api: APIRouter, services: Any) -> None:
    """``api`` already requires the GUI bearer token and JSON for writes."""

    @api.get("/approvals")
    def pending():
        return {"items": [_with_clash(services, item) for item in services.user_approvals.pending()]}

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
        location = approval.get("location")
        risk_ack_required = (approval["kind"] == "bundle_load"
                             or approval["kind"] == "office_install" and isinstance(location, dict)
                             and location.get("risk_ack_required") is True)
        if risk_ack_required and body.decision == "approve" and body.risk_ack is not True:
            message = ("Acknowledge the office install risk" if approval["kind"] == "office_install"
                       else "Acknowledge the bundle load risk")
            raise ServiceError("risk_ack_required", message, status=422)
        request.app.state.approval_sessions.verify(approval_id, approval["sha256"], browser_session, body.code)
        decided = services.user_approvals.decide(approval_id, body.decision, risk_ack=body.risk_ack)
        return _check_task_hold(services, decided)


# MEM-T02: the card of a memory_trust that clashes with an entry the person confirmed shows both, read by id
# (cut to a fixed size; the approval itself holds only the ids). The texts are data: the browser shows them as text.
def _with_clash(services: Any, item: dict[str, Any]) -> dict[str, Any]:
    location, destination = item.get("location"), item.get("destination")
    if (item.get("kind") != "memory_trust" or not isinstance(location, dict) or not isinstance(destination, dict)
            or not location.get("conflicts_with")):
        return item
    pair = services.memory.conflict_pair(str(destination.get("memory")), str(location["conflicts_with"]))
    return {**item, "conflict": pair} if pair else item


# SEC-T03: after the click, the task the gate held is checked at once instead of waiting for a watcher.
def _check_task_hold(services: Any, decided: dict[str, Any]) -> dict[str, Any]:
    location = decided.get("location")
    if (decided.get("kind") != "secret_send" or not isinstance(location, dict)
            or location.get("source") != "task" or not location.get("task_id")):
        return decided
    outcome: dict[str, Any] = {}

    def check() -> None:
        try:
            done = services.executors.resume_met(task_id=str(location["task_id"]))
            outcome["resume"] = {"state": "checked", "resumed": [item.get("outcome") for item in done.get("resumed", [])]}
        except Exception as exc:  # noqa: BLE001 - the decision stands; the next check can still launch
            _logger.warning("the check of the held task after the decision failed: %s", type(exc).__name__)
            outcome["resume"] = {"state": "error", "code": str(getattr(exc, "code", None) or type(exc).__name__)}

    worker = threading.Thread(target=check, name="secret-gate-resume", daemon=True)
    worker.start()
    worker.join(2.0)           # a run can take minutes: answer now and let the thread go on
    return {**decided, "resume": outcome.get("resume", {"state": "running"})}
