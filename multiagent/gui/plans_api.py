# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Routes of the plan proposals view (PLN-T04) under ``/gui/api/plan-proposals``.

Everything comes from ``services.plan_proposals`` (PLN-T03), so HTTP, CLI and GUI answer alike, errors included
(``not_found``, ``invalid_request``, ``conflict``).

* ``GET /plan-proposals``: list saved proposals, optionally filtered by ``status`` or ``group_id``.
* ``GET /plan-proposals/{id}``: one proposal with its tasks, revision and change history.
* ``POST /plan-proposals/{id}/edit``: replace the proposal's tasks at ``expected_revision``.
* ``POST /plan-proposals/{id}/reassign``: change one task's ``preferred``, ``workflow`` or ``bundle`` at ``expected_revision``.
* ``POST /plan-proposals/{id}/approve``: approve ``expected_revision`` and create the plan's tasks.
* ``POST /plan-proposals/{id}/reject``: reject the proposal with an optional ``note``.

The actor is fixed by the server (``user:operator``, the person holding the token) and the channel is ``gui``; the page
cannot pick either.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..services import ApplicationServices

DECIDER = "user:operator"   # who the token holder is for the services; approving or rejecting asks for a ``user:`` actor
CHANNEL = "gui"


class EditBody(BaseModel):
    tasks: list[dict[str, Any]]
    expected_revision: int
    note: str | None = Field(None, max_length=4000)   # actor and channel come from the server, never the page


class ReassignBody(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    expected_revision: int
    preferred: str | None = Field(None, max_length=100)
    workflow: str | None = Field(None, max_length=120)
    bundle: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=4000)


class ApproveBody(BaseModel):
    expected_revision: int
    note: str | None = Field(None, max_length=4000)


class RejectBody(BaseModel):
    note: str | None = Field(None, max_length=4000)


def add_plans_routes(api: APIRouter, services: ApplicationServices) -> None:
    """Register the routes; ``services.plan_proposals`` is read per request, like the other GUI routers do."""

    @api.get("/plan-proposals")
    def list_proposals(status: str | None = Query(None, max_length=20),
                       group_id: str | None = Query(None, max_length=64)):
        proposals = services.plan_proposals.list(group_id=group_id, status=status)
        return {"proposals": proposals, "count": len(proposals)}

    @api.get("/plan-proposals/{proposal_id}")
    def get_proposal(proposal_id: str):
        return services.plan_proposals.get(proposal_id)

    @api.post("/plan-proposals/{proposal_id}/edit")
    def edit_proposal(proposal_id: str, body: EditBody):
        return services.plan_proposals.edit(
            proposal_id, body.tasks, actor=DECIDER, channel=CHANNEL,
            note=body.note or "", expected_revision=body.expected_revision)

    @api.post("/plan-proposals/{proposal_id}/reassign")
    def reassign_proposal(proposal_id: str, body: ReassignBody):
        return services.plan_proposals.reassign(
            proposal_id, body.task_id, actor=DECIDER, channel=CHANNEL,
            note=body.note or "", expected_revision=body.expected_revision,
            preferred=body.preferred, workflow=body.workflow, bundle=body.bundle)

    @api.post("/plan-proposals/{proposal_id}/approve")
    def approve_proposal(proposal_id: str, body: ApproveBody):
        return services.plan_proposals.approve(
            proposal_id, actor=DECIDER, channel=CHANNEL,
            note=body.note or "", expected_revision=body.expected_revision)

    @api.post("/plan-proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: str, body: RejectBody | None = None):
        note = body.note if body else None
        return services.plan_proposals.reject(
            proposal_id, actor=DECIDER, channel=CHANNEL, note=note or "")
