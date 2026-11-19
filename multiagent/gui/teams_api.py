# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Routes of the teams view (EQP-T05) under ``/gui/api/teams``: team versions, the org chart and the human decision.

Everything comes from ``services.teams`` (EQP-T03 and EQP-T04), so HTTP, CLI and GUI answer alike, errors included
(``not_found``, ``invalid_request``, ``invalid_state``, ``invalid_definition``).

* ``GET /teams``: the bundles with a ``team.yaml``, each with its approved version (or ``null``) and its latest pending
  proposal (or ``null``). The versions are summaries: no team body.
* ``GET /teams/{bundle}/versions``: the versions of a bundle, oldest first.
* ``GET /teams/versions/{id}``: one version with its diff against the approved one (an empty team when there is none)
  and its ``card`` (CHT-T05): per role the rules, permissions and the model its agent resolves to in the catalog, and
  the team mode.
* ``GET /teams/{bundle}/org-chart?version_id=``: the organization chart and its Mermaid text.
* ``POST /teams/versions/{id}/approve`` and ``/reject`` with ``{"note"?}``: the decision. The actor is fixed by the
  server (``user:operator``, the person holding the token) and the channel is ``gui``; the page cannot pick either.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..services import ApplicationServices

DECIDER = "user:operator"   # who the token holder is for the services; approving or rejecting asks for a ``user:`` actor
CHANNEL = "gui"
TEAM_FILE = "team.yaml"


class DecisionBody(BaseModel):
    note: str | None = Field(None, max_length=4000)   # any other field is ignored: the actor and the channel are not the page's


def _summary(version: dict[str, Any] | None) -> dict[str, Any] | None:
    if version is None:
        return None
    return {key: value for key, value in version.items() if key not in ("team", "diff", "card")}


def add_teams_routes(api: APIRouter, services: ApplicationServices) -> None:
    """Register the routes; ``services.teams`` is read per request, like the other GUI routers do."""

    def has_team(name: str) -> bool:
        try:
            return (services.bundle_path(name) / TEAM_FILE).is_file()
        except Exception:   # a bundle whose folder cannot be read has no team to show; the list must not fail for it
            return False

    @api.get("/teams")
    def teams():
        entries = []
        for bundle in services.definitions.list_bundles():
            name = bundle["name"]
            if not has_team(name):
                continue
            pending = services.teams.list(name, status="proposed")["versions"]
            entries.append({"bundle": name, "approved": _summary(services.teams.approved(name)),
                            "pending": _summary(pending[-1]) if pending else None})
        return {"teams": entries, "count": len(entries)}

    @api.get("/teams/versions/{version_id}")
    def team_version(version_id: str):
        return services.teams.get_card(version_id)

    @api.post("/teams/versions/{version_id}/approve")
    def approve(version_id: str, body: DecisionBody | None = None):
        note = body.note if body else None
        return services.teams.approve(version_id, actor=DECIDER, channel=CHANNEL, note=note or "")

    @api.post("/teams/versions/{version_id}/reject")
    def reject(version_id: str, body: DecisionBody | None = None):
        note = body.note if body else None
        return services.teams.reject(version_id, actor=DECIDER, channel=CHANNEL, note=note or "")

    @api.get("/teams/{bundle}/versions")
    def team_versions(bundle: str, status: str | None = Query(None, max_length=20)):
        listing = services.teams.list(bundle, status=status)
        return {**listing, "versions": [_summary(version) for version in listing["versions"]]}

    @api.get("/teams/{bundle}/org-chart")
    def team_org_chart(bundle: str, version_id: str | None = Query(None, max_length=64)):
        return services.teams.org_chart(bundle, version_id=version_id)
