# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected routes of the project view (PRY-T03) under ``/gui/api/projects``.

They show what the core already knows (``services.projects``, ``services.tasks`` and ``services.envelopes``) and
nothing else: every field in an answer has its source there, and every rule and every error is the service's own, so
HTTP, CLI and Python answer alike. The writes are the review of a submitted task, which is the existing
``tasks.transition`` limited to ``accept`` and ``request_changes`` (PRY-T01 section 7), and the visibility of the
project (CNT-T05: the only way to make a private project ``normal``; ``/api/v1`` refuses that with ``person_required``):

* it needs the GUI token and a JSON body, like every route of ``/gui/api``;
* the actor is fixed by the server (``user:operator``, the person holding the token); the page cannot pick it;
* the page must say which revision of the task it showed (``expected_revision``), so a review never lands on a
  result the reviewer did not see; and asking for changes needs the observations in ``cause``;
* the task must belong to the project in the path (otherwise 404), whatever its id.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ..services.errors import INVALID_REQUEST, NOT_FOUND, ServiceError

if TYPE_CHECKING:
    from ..services import ApplicationServices

REVIEWER = "user:operator"     # who the token holder is for the services; the contract asks for a ``user:`` actor to review


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewBody(_Body):
    action: Literal["accept", "request_changes"]
    expected_revision: StrictInt = Field(ge=1)
    cause: str = Field("", max_length=500)


class VisibilityBody(_Body):
    visibility: Literal["normal", "private"]
    expected_revision: StrictInt = Field(ge=1)    # the revision of the project that the page showed


def _missing(task_id: str, project_id: str) -> ServiceError:
    return ServiceError(NOT_FOUND, f"Task {task_id} not found in project {project_id}", status=404,
                        details={"task_id": task_id, "project_id": project_id})


def add_project_routes(api: APIRouter, services: ApplicationServices) -> None:
    def task_of(project_id: str, task_id: str) -> dict[str, Any]:
        """The task view, only if its group belongs to ``project_id``; a task of another project is as good as missing."""
        task = services.tasks.get_task(task_id)
        group = services.tasks.get_group(task["group_id"])
        if group.get("project_id") != project_id:
            raise _missing(task_id, project_id)
        return task

    @api.get("/projects")
    def list_projects(archived: bool = False) -> dict[str, Any]:
        """The active projects (or, with ``archived``, only the archived ones), each with progress and what needs you."""
        return {"projects": services.projects.list(archived=archived)}

    @api.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        """The aggregated view: progress, counts, stages with phases and tasks, attention, working now and milestones."""
        return services.projects.get(project_id)

    @api.get("/projects/{project_id}/tasks/{task_id}")
    def get_task(project_id: str, task_id: str) -> dict[str, Any]:
        """One task with its dependencies (``satisfied``) and, when it has one, the latest result envelope as ``result``."""
        task = task_of(project_id, task_id)
        try:
            result = services.envelopes.get_result(task_id)
        except ServiceError as error:
            if error.code != NOT_FOUND:
                raise
            result = None       # no result yet: the task was never submitted
        return {**task, "result": result}

    @api.post("/projects/{project_id}/tasks/{task_id}/review")
    def review_task(project_id: str, task_id: str, body: ReviewBody) -> dict[str, Any]:
        """Approve (``accept``) or ask for changes (``request_changes``, with the observations in ``cause``)."""
        task_of(project_id, task_id)
        if body.action == "request_changes" and not body.cause.strip():
            raise ServiceError(INVALID_REQUEST, "Asking for changes needs the observations in cause", status=422,
                               details={"field": "cause"})
        return services.tasks.transition(task_id, body.action, actor=REVIEWER, cause=body.cause.strip(),
                                         expected_revision=body.expected_revision)

    @api.post("/projects/{project_id}/visibility")
    def set_visibility(project_id: str, body: VisibilityBody) -> dict[str, Any]:
        """Make the project ``normal`` or ``private`` (CNT-T05). This is the only way to take privacy off a private project:
        with it a held task may be handed to another executor by the policy, so the token holder (a person) does it, not
        a caller of ``/api/v1`` that only claims to be one (that route refuses with ``person_required``)."""
        return services.projects.update(project_id, actor=REVIEWER, expected_revision=body.expected_revision,
                                        visibility=body.visibility)
