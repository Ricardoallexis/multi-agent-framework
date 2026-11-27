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
from ..services.project_draft import ProjectDraftServices

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


class DraftBody(_Body):
    """PRY-T06: the text of the person and, to correct a draft, the previous one with what to change."""

    text: str = Field(min_length=1, max_length=2000)
    previous: dict[str, Any] | None = None
    correction: str = Field("", max_length=1000)
    confirm_cloud: bool = False
    cloud_model: str | None = Field(None, max_length=120)    # the model the person confirmed; another one asks again
    approval_id: str | None = None


class PlanBody(_Body):
    draft: dict[str, Any]


class CreateBody(_Body):
    draft: dict[str, Any]
    plan_sha256: str = Field(min_length=64, max_length=64)    # the huella of the preview the person confirmed


class StageDraftBody(DraftBody):
    expected_revision: StrictInt = Field(ge=1)


class StagePlanBody(_Body):
    stage: dict[str, Any]
    expected_revision: StrictInt = Field(ge=1)


class StageCreateBody(StagePlanBody):
    plan_sha256: str = Field(min_length=64, max_length=64)


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

    @api.get("/activities")
    def list_activities() -> dict[str, Any]:
        """Open activities grouped by active project, for the left bar's "Activities" filter (PRY-T10).

        ``{projects: [{project_id, name, title, activities: [...]}]}``: only projects with an open activity (not
        ``done``, ``failed`` or ``cancelled``), in the order of the project list; each activity as
        ``services.activities.list`` gives it. Activities do not count toward project progress."""
        projects = []
        for project in services.projects.list(archived=False):
            counts = project["activities"]
            if counts["total"] - counts["done"] <= 0:
                continue
            activities = [
                item for item in services.activities.list(project["id"])
                if item["label"]["key"] not in {"done", "failed", "cancelled"}
            ]
            if activities:
                projects.append({"project_id": project["id"], "name": project["name"], "title": project.get("title"),
                                 "activities": activities})
        return {"projects": projects}

    drafts = ProjectDraftServices(services)

    @api.post("/projects/draft")
    def draft_project(body: DraftBody) -> dict[str, Any]:
        """PRY-T06: a model drafts a project from the text of the person; with ``previous`` and ``correction`` it drafts it again.

        ``{reason, draft, check, model, tier, visibility, executors_active}``: ``draft`` is ``{title, stages: [{title,
        tasks: [{title, objective}]}]}`` and ``check`` what ``projects.plan`` says about it. Without a draft, ``reason``
        says why (``disabled``, ``cloud_confirmation_required``, ``secret_review_required``, ``budget_cut``,
        ``refused``, ``limit_reached``, ``invalid_output``, ``model_error``). Nothing is created."""
        return drafts.draft(body.text, previous=body.previous, correction=body.correction,
                            confirm_cloud=body.confirm_cloud, approval_id=body.approval_id,
                            cloud_model=body.cloud_model)

    @api.post("/projects/plan")
    def plan_project(body: PlanBody) -> dict[str, Any]:
        """PRY-T06: what creating this draft would make, with ``plan_sha256``, the key to confirm it. Writes nothing."""
        return drafts.plan(body.draft)

    @api.post("/projects/create")
    def create_project(body: CreateBody) -> dict[str, Any]:
        """PRY-T06: create the project of a draft the person confirmed, with the ``plan_sha256`` of its preview (the person
        holding the token is the actor, ``user:operator``). Another draft or hash writes nothing (409 ``plan_changed``)."""
        return drafts.create(body.draft, plan_sha256=body.plan_sha256, actor=REVIEWER)

    @api.post("/projects/{project_id}/stages/draft")
    def draft_stage(project_id: str, body: StageDraftBody) -> dict[str, Any]:
        """Draft one stage for this project; no write occurs."""
        return drafts.draft_stage(project_id, body.text, expected_revision=body.expected_revision,
                                  previous=body.previous, correction=body.correction,
                                  confirm_cloud=body.confirm_cloud, approval_id=body.approval_id,
                                  cloud_model=body.cloud_model)

    @api.post("/projects/{project_id}/stages/plan")
    def plan_stage(project_id: str, body: StagePlanBody) -> dict[str, Any]:
        """Verify the stage and return its revision-bound confirmation hash without writing."""
        return drafts.plan_stage(project_id, body.stage, expected_revision=body.expected_revision)

    @api.post("/projects/{project_id}/stages/create")
    def create_stage(project_id: str, body: StageCreateBody) -> dict[str, Any]:
        """Create the stage only when its draft, revision and confirmation hash still match."""
        return drafts.create_stage(project_id, body.stage, plan_sha256=body.plan_sha256, actor=REVIEWER,
                                   expected_revision=body.expected_revision)

    @api.get("/projects")
    def list_projects(archived: bool = False) -> dict[str, Any]:
        """The active projects (or, with ``archived``, only the archived ones), each with progress and what needs you."""
        return {"projects": services.projects.list(archived=archived)}

    @api.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        """The aggregated view: progress, counts, stages with phases and tasks, attention, working now and milestones."""
        return services.projects.get(project_id)

    @api.get("/projects/{project_id}/costs")
    def get_project_costs(project_id: str) -> dict[str, Any]:
        """What the project's recorded provider calls add up to, per stage and per task (PRY-T07): tokens and API cost, read-only.

        The answer of ``services.usage.project_costs``: a sum of stored records, never an estimate made here; a call without
        tokens or a price is counted apart (``tokens_partial``, ``cost_state``). A missing project answers 404."""
        return services.usage.project_costs(project_id)

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
