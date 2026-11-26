# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Create a project by talking (PRY-T06): a model drafts it once, the plan check verifies it, the person confirms once.

The person says what they want to achieve. :meth:`ProjectDraftServices.draft` asks the model of "New session"
(``settings.gui_new_session_model``, through the same guarded call as ``new_session``: egress policy, budget, secret
review, usage record and the cloud confirmation) for a draft: a title, stages with a title and tasks with a title and
an objective, and nothing else. The service, never the model, turns that into a ``maf.project-template/1`` document
(:func:`to_document`): the project key comes from the title, stage codes and task ids are numbered, and the owners,
dependencies, context, constraints and visibility are not the model's to give (a new project is private). The document
goes through ``projects.plan``, which says what would be created and gives ``plan_sha256``.

* Nothing is created until :meth:`create` receives the ``plan_sha256`` the person confirmed: if the draft changed since
  (``plan_changed``) or the project exists now, nothing is written. A draft is a proposal, never a project.
* A correction asks for the draft again with the previous one and the correction in the prompt, with no limit; every
  draft passes ``projects.plan`` again.
* The text of the person, the previous draft and the correction enter the prompt as untrusted data
  (``document_trust.wrap_untrusted``). What the model returns is data: it is checked by structure and by
  ``projects.plan`` and chooses no file, permission or executor.
* This service never raises for a model problem: the answer has ``draft: None`` and a ``reason``. Errors of the plan
  check and of the creation are ``ServiceError`` with the project service's own codes; a name that is taken adds
  ``details.reason = "project_exists"``.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from ..contracts import ModelTier, ProjectDraftProposal
from ..document_trust import FIXED_CLAUSE, Provenance, wrap_untrusted
from .errors import INVALID_REQUEST, INVALID_STATE, ServiceError, service_errors
from .new_session import TEXT_MAX, _BudgetCut, _Refused, _SecretReview

DRAFT_ID = "conversation-draft"      # the ``id`` of the document the service builds: it names no ready-made project
KEY_FALLBACK = "project"
KEY_MAX = 16
LOCAL_MODELS_MAX = 5
CORRECTION_MAX = 1000
REQUEST_PROVENANCE = Provenance(kind="gui_free_text", label="project request typed in the new project chat")
PREVIOUS_PROVENANCE = Provenance(kind="model_output", label="previous project draft")
CORRECTION_PROVENANCE = Provenance(kind="gui_free_text", label="correction typed in the new project chat")

INSTRUCTIONS = (
    "You help a person plan a project. Read their request and draft the project in stages. Reply with one JSON object "
    'and nothing else: {"title": "<short project name>", "stages": [{"title": "<stage name>", "tasks": '
    '[{"title": "<task name>", "objective": "<one or two sentences on what the task achieves>"}]}]}. '
    "Order the stages as the work goes. Use at most 12 stages and 12 tasks in each, and only as many as the request needs. "
    "Write the draft in the language of the request. Give no ids, dates, owners, dependencies, file paths, commands or "
    "permissions: only titles and objectives. If a previous draft and a correction are given, return the whole draft "
    "again with the correction applied and keep what the correction does not touch. Nothing is created until the "
    "person confirms what you propose."
)


def project_key(title: str) -> str:
    """The project key for a title: lowercase letters and digits, starting with a letter, at most 16 characters."""
    folded = unicodedata.normalize("NFKD", str(title)).encode("ascii", "ignore").decode().lower()
    key = re.sub(r"[^a-z0-9]+", "", folded).lstrip("0123456789")[:KEY_MAX]
    return key if len(key) >= 2 else KEY_FALLBACK


def to_document(draft: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    """The ``maf.project-template/1`` document, the key and the title of a clean draft. No model is involved: stage
    ``S1``, ``S2``, ..., tasks ``T01``, ``T02``, ..., no assignee, no dependency, no context."""
    stages = [{"code": f"S{index}", "title": stage["title"],
               "tasks": [{"id": f"T{number:02d}", "title": task["title"],
                          **({"objective": task["objective"]} if task["objective"] else {})}
                         for number, task in enumerate(stage["tasks"], start=1)]}
              for index, stage in enumerate(draft["stages"], start=1)]
    document = {"schema": "maf.project-template/1", "id": DRAFT_ID, "title": draft["title"], "stages": stages}
    return document, project_key(draft["title"]), draft["title"]


def _invalid(message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": "draft", **details})


def clean_draft(draft: Any) -> dict[str, Any]:
    """A draft checked by structure (a title, 1 to 12 stages, 1 to 12 tasks each, nothing else) and with its texts trimmed.

    ``invalid_request`` with the place of the first problem when it does not fit; blank texts do not count."""
    if isinstance(draft, ProjectDraftProposal):
        draft = draft.model_dump()
    if not isinstance(draft, Mapping):
        raise _invalid("The draft must be an object with a title and stages")
    try:
        proposal = ProjectDraftProposal.model_validate(dict(draft))
    except ValidationError as error:
        first = error.errors()[0]
        place = ".".join(str(part) for part in first["loc"])
        raise _invalid(f"The draft does not have the expected structure ({place}: {first['msg']})", path=place) from None
    title = proposal.title.strip()
    if not title:
        raise _invalid("The draft needs a title", path="title")
    stages = []
    for index, stage in enumerate(proposal.stages):
        stage_title = stage.title.strip()
        if not stage_title:
            raise _invalid(f"Stage {index + 1} needs a title", path=f"stages.{index}.title")
        tasks = []
        for number, task in enumerate(stage.tasks):
            task_title = task.title.strip()
            if not task_title:
                raise _invalid(f"Task {number + 1} of stage {index + 1} needs a title",
                               path=f"stages.{index}.tasks.{number}.title")
            tasks.append({"title": task_title, "objective": task.objective.strip()})
        stages.append({"title": stage_title, "tasks": tasks})
    return {"title": title, "stages": stages}


def _mapped(error: ServiceError) -> ServiceError:
    """The error of the project service, with ``reason: project_exists`` when the name is the problem."""
    details = error.details or {}
    if error.code == INVALID_STATE and "project_id" in details and "id" not in details and "reason" not in details:
        return ServiceError(INVALID_STATE, "A project with that name already exists", status=error.status,
                            details={**details, "reason": "project_exists", "key": details["project_id"]})
    return error


class ProjectDraftServices:
    """The conversation that ends in a project: ``draft`` (text to draft), ``plan`` (draft to preview) and ``create``."""

    def __init__(self, services) -> None:
        self._services = services

    # --- the model call -----------------------------------------------------------------------------------------------

    @staticmethod
    def build_prompt(text: str, *, previous: Mapping[str, Any] | None = None, correction: str = "") -> str:
        """The prompt: fixed instructions (trusted), then the request, the previous draft and the correction as untrusted data."""
        parts = [INSTRUCTIONS, FIXED_CLAUSE, "PROJECT_REQUEST:", wrap_untrusted(text, REQUEST_PROVENANCE)]
        if previous is not None and correction:
            parts += ["PREVIOUS_DRAFT_JSON:",
                      wrap_untrusted(json.dumps(previous, ensure_ascii=False, separators=(",", ":")), PREVIOUS_PROVENANCE),
                      "CORRECTION:", wrap_untrusted(correction, CORRECTION_PROVENANCE)]
        return "\n\n".join(parts)

    def _local_models(self) -> list[str]:
        """The catalog's local models other than the one in use (where the person can choose one instead)."""
        current = self._services.settings.gui_new_session_model
        models = self._services.system.catalog.models
        found = [model_id for model_id, spec in models.items()
                 if model_id != current and str(spec.tier).casefold() == ModelTier.LOCAL.value]
        return sorted(found)[:LOCAL_MODELS_MAX]

    def _executors_active(self) -> bool:
        try:
            return bool(self._services.executor_activations.active_ids())
        except Exception:  # noqa: BLE001 - a note for the card; it must not stop a draft
            return False

    def _held(self, reason: str, **extra: Any) -> dict[str, Any]:
        return {"reason": reason, "draft": None, "check": None, **extra}

    @service_errors
    def draft(self, text: str, *, previous: Any = None, correction: str | None = None, confirm_cloud: bool = False,
              approval_id: str | None = None, cloud_model: str | None = None) -> dict[str, Any]:
        """Draft a project for ``text``; with ``previous`` and ``correction``, draft it again with the correction applied.

        Returns ``{reason, draft, check, model, tier, visibility, executors_active}``. On success ``reason`` is empty,
        ``draft`` is ``{title, stages: [{title, tasks: [{title, objective}]}]}`` and ``check`` is
        ``{ok, plan, problems}``: ``plan`` is the answer of ``projects.plan`` (with ``plan_sha256``) or ``None``, and
        ``problems`` says what could not be verified (each ``{reason, message}``, for example ``project_exists``).
        Without a draft ``reason`` is ``disabled``, ``cloud_confirmation_required`` (with ``local_models``: the same
        confirmation as "New session"), ``secret_review_required`` (with ``approval_id``), ``budget_cut``,
        ``refused``, ``limit_reached`` (with ``limit``), ``invalid_output`` or ``model_error``. Nothing is created.

        ``cloud_model`` is the model the person confirmed. When it is given, ``confirm_cloud`` only counts while it equals
        the model in use now: after a change of model the confirmation is asked again (with the new model). Without it,
        ``confirm_cloud`` counts as it always did."""
        request = _text("text", text, TEXT_MAX)
        fix = _text("correction", correction, CORRECTION_MAX, required=False)
        before = clean_draft(previous) if previous is not None else None
        if fix and before is None:
            raise _invalid("A correction needs the previous draft", path="previous")
        owner = self._services.new_session
        settings = self._services.settings
        base = {"model": settings.gui_new_session_model, "visibility": "private",
                "executors_active": self._executors_active()}
        if not owner.enabled:
            return self._held("disabled", **base)
        tier = owner._model_tier()
        base["tier"] = tier.value
        if confirm_cloud and cloud_model is not None and cloud_model != settings.gui_new_session_model:
            confirm_cloud = False
        if tier != ModelTier.LOCAL and not confirm_cloud:
            return self._held("cloud_confirmation_required", **base, local_models=self._local_models())
        try:
            asked = owner._ask(self.build_prompt(request, previous=before, correction=fix),
                               approval_id=approval_id, output_schema=ProjectDraftProposal)
        except _SecretReview as held:
            return self._held("secret_review_required", **base, approval_id=held.approval_id)
        except _BudgetCut:
            return self._held("budget_cut", **base)
        except _Refused:
            return self._held("refused", **base)
        except Exception as exc:  # noqa: BLE001 - no model, a provider error or a bad answer all end in a reason
            from ..errors import QuotaExceeded, RateLimitExceeded, StructuredOutputError
            from .limits import limit_notice
            if isinstance(exc, (QuotaExceeded, RateLimitExceeded)):
                return self._held("limit_reached", **base,
                                  limit=limit_notice(self._services, settings.gui_new_session_model, retry_at=exc.retry_at))
            return self._held("invalid_output" if isinstance(exc, (StructuredOutputError, ValidationError))
                              else "model_error", **base)
        try:
            checked = clean_draft(asked)
        except ServiceError:
            return self._held("invalid_output", **base)
        return {"reason": "", "draft": checked, "check": self._check(checked), **base}

    # --- verify and create --------------------------------------------------------------------------------------------

    def _check(self, draft: dict[str, Any]) -> dict[str, Any]:
        """What ``projects.plan`` says about the draft, as data: ``{ok, plan, problems}``; it never raises."""
        try:
            return {"ok": True, "plan": self.plan(draft), "problems": []}
        except ServiceError as error:
            details = error.details or {}
            return {"ok": False, "plan": None,
                    "problems": [{"reason": details.get("reason") or error.code, "message": error.message,
                                  **({"field": details["field"]} if "field" in details else {})}]}

    @service_errors
    def plan(self, draft: Any) -> dict[str, Any]:
        """The preview of a draft: what ``projects.plan`` says would be created, with ``plan_sha256`` (the confirmation
        key), ``key`` and ``draft`` (the clean one). Writes nothing. A taken name is ``invalid_state`` with
        ``details.reason = "project_exists"``."""
        clean = clean_draft(draft)
        document, key, title = to_document(clean)
        try:
            preview = self._services.projects.plan(document, key=key, title=title)
        except ServiceError as error:
            raise _mapped(error) from None
        return {**preview, "key": key, "draft": clean}

    @service_errors
    def create(self, draft: Any, *, plan_sha256: str, actor: str) -> dict[str, Any]:
        """Create the project of a confirmed draft. ``plan_sha256`` is the one ``plan`` returned for this very draft: any
        change gives ``invalid_state`` (``details.reason = "plan_changed"``) and nothing is written. Needs a ``user:``
        actor. Returns the project view of ``projects.create``."""
        clean = clean_draft(draft)
        document, key, title = to_document(clean)
        try:
            return self._services.projects.create(document, key=key, title=title, plan_sha256=plan_sha256, actor=actor)
        except ServiceError as error:
            raise _mapped(error) from None


def _text(field: str, value: Any, maximum: int, *, required: bool = True) -> str:
    clean = str(value or "").strip()
    if required and not clean:
        raise ServiceError(INVALID_REQUEST, f"{field} must not be empty", status=422, details={"field": field})
    if len(clean) > maximum:
        raise ServiceError(INVALID_REQUEST, f"{field} admits up to {maximum} characters", status=422,
                           details={"field": field})
    return clean
