# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""«Nueva sesión» reads free text with the configured provider (GUI2-T17).

The GUI chat used to compare the words of the text with workflow names and descriptions. :meth:`interpret` asks the
framework's own provider layer (Mock, Ollama or a cloud model, through the same ``ModelRouter`` the steps use) to
propose a workflow and its inputs, and checks the answer against the declared workflows and their inputs before it
leaves. Nothing is started: the answer is a proposal that the operator confirms in the chat.

* The text of the person enters the prompt as untrusted data (``document_trust.wrap_untrusted``), like any content
  the engine does not control, and the outbound prompt passes the egress policy of the framework (``egress_default``:
  redact, block or escalate; an escalation has nobody to ask here, so it counts as a refusal).
* Anything else than a valid proposal (the setting is off, no model, an error, a blocked prompt, JSON that does not
  fit, a workflow or input that does not exist) answers ``source: "keywords"`` with the reason and no proposal: the
  client then uses its deterministic word matching, and says so. This service never raises for a model problem.
* With Mock the answer is deterministic (a fixture of the fake adapter), so tests and the default mode need no model.
"""
from __future__ import annotations

import json
import hashlib
import re
import uuid
from collections.abc import Mapping
from typing import Any

from ..adapters.fake import best_catalog_match
from ..budget_cut import policy_from_settings
from ..call_guards import SCOPE_RUN_STEP, CallContext, GuardEnvironment, Pause, Stop, build_call_guards
from ..contracts import ModelTier, NewSessionProposal, TaskRequirements
from ..document_trust import FIXED_CLAUSE, Provenance, wrap_untrusted
from ..model_router import ModelBinding
from ..offices.plan import default_plan_template, office_group_id, office_privacy
from ..offices.schema import OfficeDefinition, area_bundle_name
from ..observability import TokenUsageRecord, estimate_call_cost
from ..planning import validate_proposal as validate_plan_proposal
from ..planning.chat import split_request
from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors
from .usage import NEW_SESSION_USAGE_ID

SOURCE_MODEL = "model"
SOURCE_KEYWORDS = "keywords"
TEXT_MAX = 2000
MAX_WORKFLOWS = 60
DESCRIPTION_MAX = 240
LABEL_MAX = 80
VALUE_MAX = 4000
CATALOG_MARKER = "CATALOG_JSON:"
PLAN_DRAFT_SCHEMA = "maf.new-session-plan/1"
PLAN_GROUP_PREFIX = "chat-"
PLAN_TASK_PREFIX = "plan"
PLAN_NOTE = "Prepared from «Nueva sesión»"
PROVENANCE = Provenance(kind="gui_free_text", label="free text typed in the new session chat")

INSTRUCTIONS = (
    "You help an operator start a workflow of a framework. Choose the workflow from the catalog that best fits the "
    "request and fill its inputs from the request. Reply with one JSON object and nothing else: "
    '{"workflow_id": "<id from the catalog or empty>", "bundle": "<bundle of that entry or null>", '
    '"inputs": {"<input name>": "<text>"}, "project_name": "<short name or empty>", "reason": "<one sentence>", "needs_plan": false}. '
    "Use only ids and input names that appear in the catalog. For an input with choices use exactly one of them. "
    "Leave an input out when the request does not give it. If nothing fits, leave workflow_id empty. "
    "Set needs_plan to true only when the request needs more than one workflow, one after the other. "
    "Nothing runs until the operator confirms what you propose."
)


class NewSessionServices:
    """``services.new_session``: interpret the free text of the new-session chat."""

    def __init__(self, services) -> None:
        self._services = services

    @property
    def enabled(self) -> bool:
        return bool(self._services.settings.gui_new_session_interpret)

    # --- the catalog the model chooses from -------------------------------------------------------------------

    def _catalog(self, office: OfficeDefinition | None = None) -> list[dict[str, Any]]:
        """Every workflow of every origin with its description and the inputs the chat asks (declared by the workflow)."""
        definitions = self._services.definitions
        entries: list[dict[str, Any]] = []
        bundles = ([area_bundle_name(office.id, area.id) for area in office.areas] if office is not None
                   else [None, *[item["name"] for item in definitions.list_bundles()]])
        for bundle in bundles:
            try:
                listed = definitions.list_workflows(bundle=bundle)
            except Exception:  # noqa: BLE001 - one unreadable origin must not hide the others
                continue
            for item in listed:
                if len(entries) >= MAX_WORKFLOWS:
                    return entries
                try:
                    spec = definitions.workflow_inputs(item["id"], bundle=bundle)
                except Exception:  # noqa: BLE001 - an unreadable workflow is simply not offered
                    continue
                inputs = [{"name": field["name"], "label": str(field.get("label") or field["name"])[:LABEL_MAX],
                           "type": field.get("type") or "text", "required": bool(field.get("required")),
                           "choices": [str(choice) for choice in field.get("choices") or []]}
                          for field in spec.get("inputs") or [] if not (field.get("context") and not field.get("required"))]
                entries.append({"id": item["id"], "bundle": bundle,
                                "description": str(spec.get("description") or "")[:DESCRIPTION_MAX], "inputs": inputs})
        return entries

    @staticmethod
    def build_prompt(text: str, catalog: list[dict[str, Any]]) -> str:
        """The prompt: fixed instructions and catalog (trusted), then the text of the person as untrusted data."""
        return "\n\n".join([
            INSTRUCTIONS, FIXED_CLAUSE,
            CATALOG_MARKER + " " + json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
            "Request of the operator:", wrap_untrusted(text, PROVENANCE),
        ])

    # --- the call, with the framework's guards ----------------------------------------------------------------

    def _model_tier(self) -> ModelTier:
        system = self._services.system
        model_id = self._services.settings.gui_new_session_model
        try:
            return ModelTier(system.catalog.model(model_id).tier)
        except KeyError:
            return ModelTier.CLOUD

    def _ask(self, prompt: str, *, approval_id: str | None = None,
             data_class: str = "non_sensitive") -> NewSessionProposal:
        settings = self._services.settings
        system = self._services.system
        router = system.engine.router
        model_id = settings.gui_new_session_model
        tier = self._model_tier()
        step_id = f"{NEW_SESSION_USAGE_ID}:{uuid.uuid4().hex}"
        outgoing = [prompt]
        chain = build_call_guards(GuardEnvironment(
            scope=SCOPE_RUN_STEP, catalog=system.catalog, egress={"action": settings.egress_default},
            budgets=policy_from_settings(settings, self._services.usage), emit=lambda *_: None,
            secret_approvals=self._services.user_approvals, secret_approval_id=approval_id))

        def before_call(call: CallContext) -> None:     # the router passes the outgoing text and takes back a redaction
            def replace(value: str) -> None:
                outgoing[0] = value
                if call.replace_outgoing is not None:
                    call.replace_outgoing(value)

            verdict = chain.check(call.with_fields(step_id=step_id, step_kind="work", data_class=data_class,
                                                   replace_outgoing=replace),
                                 {"llm_calls": 0, "max_llm_calls": 1})     # one call, no run behind it
            if isinstance(verdict, (Stop, Pause)):
                if isinstance(verdict, Pause) and verdict.guard == "secret":
                    raise _SecretReview(verdict.request["approval_id"])
                if isinstance(verdict, Pause) and verdict.guard == "budget":
                    raise _BudgetCut(verdict.message)
                raise _Refused(getattr(verdict, "message", None) or "the egress policy refused the prompt")

        def record_attempt(event: dict[str, Any]) -> None:
            if not event.get("provider_invoked"):
                return
            attempted = str(event.get("attempted_model") or event.get("model") or "")
            try:
                spec = system.catalog.models.get(attempted)
                cost = estimate_call_cost(spec, tokens_in=event.get("tokens_in"), tokens_out=event.get("tokens_out")) if spec else None
            except Exception:
                cost = None
            system.engine.usage_recorder.record(TokenUsageRecord(
                run_id=NEW_SESSION_USAGE_ID, step_id=step_id, agent_id=NEW_SESSION_USAGE_ID, attempt=1,
                provider=str(event.get("provider") or ""),
                model=str(event.get("observed_model") or event.get("effective_model") or event.get("model") or ""),
                model_digest=str(event.get("model_digest") or ""), prompt_id=NEW_SESSION_USAGE_ID,
                prompt_version=1, prompt_sha256=hashlib.sha256(outgoing[0].encode("utf-8")).hexdigest(),
                brand_version=None, tokens_in=event.get("tokens_in"), tokens_out=event.get("tokens_out"),
                num_ctx=event.get("num_ctx"), duration_ms=event.get("duration_ms"),
                estimated_cost_usd=cost.estimated_cost_usd if cost else None,
                cost_basis=cost.basis if cost else None, success=event.get("status") == "success",
                error_type=str(event.get("error_type") or "")))

        response, _ = router.execute(
            binding=ModelBinding(preferred_model=model_id, fallback_model=None),
            requirements=TaskRequirements(requires_web=False, output_schema=NewSessionProposal.__name__, tier=tier),
            prompt=prompt, output_schema=NewSessionProposal, before_call=before_call, on_attempt=record_attempt)
        return response.parsed

    # --- the public call -------------------------------------------------------------------------------------

    @service_errors
    def interpret(self, text: str, *, confirm_cloud: bool = False,
                  approval_id: str | None = None, office: str | None = None) -> dict[str, Any]:
        """Propose a workflow and inputs for ``text``.

        Returns ``{"source": "model" | "keywords", "reason": str, "proposal": {...} | None}``. ``proposal`` is
        ``{"workflow_id", "bundle", "inputs", "project_name", "explanation", "dropped"}`` and always names a workflow
        that exists; ``source`` ``keywords`` means the caller must use its own word matching, and ``reason`` says why
        (``disabled``, ``no_workflows``, ``office_not_normal``, ``model_error``, ``refused``, ``invalid_output`` or
        ``no_match``).
        """
        clean = self._clean(text)
        office_definition, _, office_classes = (
            self._office_context(office) if office is not None else (None, [], [])
        )
        if not self.enabled:
            return _fallback("disabled")
        catalog = self._catalog(office_definition)
        if not catalog:
            return _fallback("no_workflows")
        tier = self._model_tier()
        privacy = office_privacy(office_classes) if office is not None else "normal"
        data_class = "sensitive" if office is not None and privacy != "normal" else "non_sensitive"
        if office is not None and privacy != "normal" and tier != ModelTier.LOCAL:
            return _fallback("office_not_normal")
        if tier != ModelTier.LOCAL and not confirm_cloud:
            return {**_fallback("cloud_confirmation_required"),
                    "model": self._services.settings.gui_new_session_model, "tier": tier.value}
        try:
            proposed = self._ask(self.build_prompt(clean, catalog), approval_id=approval_id, data_class=data_class)
        except _SecretReview as held:
            return {**_fallback("secret_review_required"), "approval_id": held.approval_id}
        except _BudgetCut:
            return _fallback("budget_cut")
        except _Refused:
            return _fallback("refused")
        except Exception as exc:  # noqa: BLE001 - no model, a provider error or a bad answer all end in the word matching
            from ..errors import QuotaExceeded, RateLimitExceeded, StructuredOutputError
            from .limits import limit_notice
            if isinstance(exc, (QuotaExceeded, RateLimitExceeded)):
                return {**_fallback("limit_reached"),
                        "limit": limit_notice(self._services, self._services.settings.gui_new_session_model,
                                              retry_at=exc.retry_at)}
            return _fallback("invalid_output" if isinstance(exc, StructuredOutputError) else "model_error")
        if proposed.needs_plan:
            suggested = self._plan_suggested(clean, proposed, catalog, confirm_cloud, approval_id, office=office)
            if suggested is not None:
                return suggested
        if not proposed.workflow_id.strip():
            return _fallback("no_match")
        checked = validate_proposal(proposed, catalog)
        return _fallback("invalid_output") if checked is None else {"source": SOURCE_MODEL, "reason": "", "proposal": checked}

    def _plan_suggested(self, text: str, proposed: NewSessionProposal, catalog: list[dict[str, Any]],
                        confirm_cloud: bool, approval_id: str | None, *,
                        office: str | None = None) -> dict[str, Any] | None:
        """The answer for a request that needs more than one workflow, or ``None`` to answer with one workflow as before
        (the plan could not be drafted: the single-workflow proposal is still a valid answer)."""
        try:
            draft = self.draft_plan(text, confirm_cloud=confirm_cloud, approval_id=approval_id, office=office)
        except ServiceError:
            return None
        if draft.get("reason") == "secret_review_required":
            return {**_fallback("secret_review_required"), "approval_id": draft["approval_id"]}
        if "proposal" not in draft:
            return None
        checked = validate_proposal(proposed, catalog) if proposed.workflow_id.strip() else None
        return {"source": SOURCE_MODEL, "reason": "plan_suggested", "proposal": checked, "plan_draft": draft}

    # --- a plan from the same text (ARR3-T01) ------------------------------------------------------------------

    @service_errors
    def draft_plan(self, text: str, *, confirm_cloud: bool = False, approval_id: str | None = None,
                   office: str | None = None) -> dict[str, Any]:
        """Draft a plan of several workflows for ``text``. Nothing is saved, approved or started.

        Returns ``{schema, goal, group_id, planner, tasks, teams, proposal}``: ``planner`` is ``model`` (a real model
        plans) or ``deterministic`` (Mock: one stage for each part of the request, each after the previous one),
        with ``office`` and ``fallback_reason`` when an office is selected,
        ``tasks`` the readable list, ``teams`` the bundles with ``role:`` steps and no approved team (``action``
        ``propose``) and ``proposal`` the ``multiagent.plan_proposal/1`` document. When the draft cannot be made the
        answer has no ``proposal`` and says why in ``reason``: ``disabled``, ``no_workflows``, ``no_match``,
        ``cloud_confirmation_required`` (the same confirmation as ``interpret``) or ``secret_review_required``
        (with its ``approval_id``)."""
        clean = self._clean(text)
        office_definition, office_bundles, office_classes = (
            self._office_context(office) if office is not None else (None, [], [])
        )
        if not self.enabled:
            result = {"reason": "disabled"}
            return {**result, "office": office, "fallback_reason": None} if office is not None else result
        catalog = self._catalog(office_definition)
        if not catalog:
            result = {"reason": "no_workflows"}
            return {**result, "office": office, "fallback_reason": None} if office is not None else result
        tier = self._model_tier()
        fallback_reason = None
        if office is None:
            if tier != ModelTier.LOCAL and not confirm_cloud:
                return {"reason": "cloud_confirmation_required",
                        "model": self._services.settings.gui_new_session_model, "tier": tier.value}
            group_id = PLAN_GROUP_PREFIX + uuid.uuid4().hex[:12]
            if self._services.system.engine.router.dry_run:
                template = self._template(clean, catalog)
                if template is None:
                    return {"reason": "no_match"}
                planner = "deterministic"
                request = {"group_id": group_id, "goal": clean, "template": template}
            else:
                planner = "model"
                request = {"group_id": group_id, "goal": clean, "prefix": PLAN_TASK_PREFIX,
                           "approval_id": approval_id}
        else:
            privacy = office_privacy(office_classes)
            group_id = office_group_id(office)
            deterministic = self._services.system.engine.router.dry_run
            if deterministic:
                planner, fallback_reason = "deterministic", "mock"
            elif tier == ModelTier.LOCAL:
                planner = "model"
            elif privacy == "protected":
                planner, fallback_reason = "deterministic", "office_not_normal"
            elif privacy == "unset":
                if confirm_cloud:
                    area = next(area.id for area, data_class in zip(office_definition.areas, office_classes)
                                if data_class is None)
                    raise ServiceError(INVALID_STATE, "Esta área no tiene nivel de privacidad: asígnale uno antes de pedir el plan",
                                       status=409, details={"reason": "office_level_required",
                                                            "office": office, "area": area})
                result = {"reason": "cloud_confirmation_required",
                          "model": self._services.settings.gui_new_session_model, "tier": tier.value}
                return {**result, "office": office, "fallback_reason": None}
            elif not confirm_cloud:
                result = {"reason": "cloud_confirmation_required",
                          "model": self._services.settings.gui_new_session_model, "tier": tier.value}
                return {**result, "office": office, "fallback_reason": None}
            else:
                planner = "model"

            if planner == "deterministic":
                template = default_plan_template(
                    office_definition, clean, self._first_required_text_input)
                if template is None:
                    template = self._template(clean, catalog)
                if template is None:
                    message = "Esta oficina no trae un plan por defecto: pide una cosa por vez"
                    raise ServiceError(INVALID_STATE, message, status=409,
                                       details={"reason": "office_plan_unavailable", "fix": message})
                request = {"group_id": group_id, "goal": clean, "template": template}
            else:
                request = {"group_id": group_id, "goal": clean, "prefix": PLAN_TASK_PREFIX,
                           "approval_id": approval_id, "bundles": office_bundles,
                           "data_class": "sensitive" if privacy != "normal" else "non_sensitive"}
        try:
            proposal = self._services.planning.propose(planner, request, confirm_cloud=confirm_cloud)
        except ServiceError as exc:
            if (exc.details or {}).get("reason") == "secret_review_required":
                result = {"reason": "secret_review_required", "approval_id": exc.details["approval_id"]}
                return {**result, "office": office, "fallback_reason": None} if office is not None else result
            raise
        if office is not None:
            allowed = set(office_bundles)
            for task in proposal["tasks"]:
                if task.get("bundle") not in allowed:
                    raise ServiceError(INVALID_REQUEST, "El borrador contiene una tarea de un área ajena a esta oficina",
                                       status=422,
                                       details={"reason": "office_plan_outside_office", "task": task})
        teams = [{"bundle": item["bundle"], "roles": item["roles"], "action": "propose"}
                 for item in self._services.plan_proposals.team_blockers(proposal) if item["bundle"] is not None]
        tasks = [{"id": task["id"], "title": task["title"], "workflow": task["workflow"], "bundle": task.get("bundle"),
                  "inputs": dict(task.get("inputs") or {}), "depends_on": list(task["depends_on"])}
                 for task in proposal["tasks"]]
        result = {"schema": PLAN_DRAFT_SCHEMA, "goal": proposal["goal"], "group_id": proposal["group_id"],
                  "planner": planner, "tasks": tasks, "teams": teams, "proposal": proposal}
        if office is not None:
            result.update({"office": office, "fallback_reason": fallback_reason})
        return result

    def _office_context(self, office_id: str) -> tuple[OfficeDefinition, list[str], list[str | None]]:
        try:
            self._services.offices.get(office_id)
            definition = self._services.offices.plan_definition(office_id)
        except ServiceError as exc:
            if (exc.details or {}).get("reason") == "office_not_installed":
                raise ServiceError(NOT_FOUND, exc.message, status=404, details=exc.details) from exc
            raise
        bundles = [area_bundle_name(office_id, area.id) for area in definition.areas]
        classes = [self._services.offices.area_class(bundle) for bundle in bundles]
        return definition, bundles, classes

    def _first_required_text_input(self, workflow_id: str, bundle: str) -> str | None:
        spec = self._services.definitions.workflow_inputs(workflow_id, bundle=bundle)
        return next((field["name"] for field in spec.get("inputs") or []
                     if field.get("required") and field.get("type", "text") in {"text", "long_text"}
                     and not field.get("choices")), None)

    @staticmethod
    def _clean(text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            raise ServiceError(INVALID_REQUEST, "text must not be empty", status=422, details={"field": "text"})
        if len(clean) > TEXT_MAX:
            raise ServiceError(INVALID_REQUEST, f"text admits up to {TEXT_MAX} characters", status=422, details={"field": "text"})
        return clean

    @staticmethod
    def _template(text: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
        """One stage for each part of ``text`` (each after the previous one); the part is the first required text input
        of the workflow it shares the most words with. ``None`` when a part matches no workflow."""
        stages = []
        for index, part in enumerate(split_request(text), start=1):
            entry, _ = best_catalog_match(part, catalog)
            if entry is None:
                return None
            target = next((item for item in entry["inputs"] if item["required"] and not item["choices"]), None)
            stages.append({"id": f"s{index}", "title": part[:120], "workflow": entry["id"], "bundle": entry["bundle"],
                           "inputs": {target["name"]: part} if target else {}})
        return {"prefix": PLAN_TASK_PREFIX, "stages": stages} if stages else None

    @service_errors
    def save_plan(self, draft: Any, *, actor: str = "user:operator", channel: str = "gui") -> dict[str, Any]:
        """Save a drafted plan as a plan proposal (``proposed``) and, for each bundle that lacks an approved team, propose
        its team version. Approves nothing and creates no run or task.

        The proposal is validated again, and the teams come from that check, not from the draft. Returns
        ``{proposal_id, revision, team_versions, team_errors, href}``; ``href`` opens the proposal as a thread, where
        **Aprobar** and **Rechazar** are."""
        if not isinstance(draft, Mapping) or draft.get("schema") != PLAN_DRAFT_SCHEMA:
            raise ServiceError(INVALID_REQUEST, f"draft schema must be {PLAN_DRAFT_SCHEMA}", status=422,
                               details={"field": "draft"})
        try:
            proposal = validate_plan_proposal(self._services, draft.get("proposal"))
        except ServiceError as exc:
            if exc.status == 404:       # a workflow the draft names does not exist: the draft is not valid
                raise ServiceError(INVALID_REQUEST, exc.message, status=422, details={"field": "draft"}) from exc
            raise
        blockers = [item for item in self._services.plan_proposals.team_blockers(proposal) if item["bundle"] is not None]
        saved = self._services.plan_proposals.save(proposal, actor=actor, channel=channel, note=PLAN_NOTE)
        versions: list[str] = []
        errors: list[dict[str, str]] = []
        for item in blockers:
            bundle = item["bundle"]
            try:
                waiting = self._services.teams.list(bundle, status="proposed")["versions"]
                version = waiting[-1] if waiting else self._services.teams.propose(
                    bundle, actor=actor, channel=channel, draft=False)
            except ServiceError as exc:     # the plan stays saved; Aprobar keeps asking for the team
                errors.append({"bundle": bundle, "code": exc.code})
                continue
            versions.append(version["id"])
        return {"proposal_id": saved["id"], "revision": saved["revision"], "team_versions": versions,
                "team_errors": errors, "href": "#new/plan/" + saved["id"]}


class _Refused(Exception):
    """The egress policy did not let the prompt out."""


class _BudgetCut(Exception):
    """A configured cut budget held the call."""


class _SecretReview(Exception):
    """A browser decision is needed before the model call."""

    def __init__(self, approval_id: str):
        super().__init__("Secret review required")
        self.approval_id = approval_id


def _fallback(reason: str) -> dict[str, Any]:
    return {"source": SOURCE_KEYWORDS, "reason": reason, "proposal": None}


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def validate_proposal(proposed: NewSessionProposal, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The proposal checked against the catalog, or ``None`` when its workflow does not exist.

    Inputs the workflow does not declare, values that are not text or exceed the limit, and choices outside the
    declared ones are dropped (and listed in ``dropped``); a proposal never carries more than the form accepts."""
    bundle = proposed.bundle or None
    entry = next((item for item in catalog if item["id"] == proposed.workflow_id.strip() and item["bundle"] == bundle), None)
    if entry is None and bundle is None:       # a model may leave the bundle out: accept it only when the id is not ambiguous
        same = [item for item in catalog if item["id"] == proposed.workflow_id.strip()]
        entry = same[0] if len(same) == 1 else None
    if entry is None:
        return None
    declared = {field["name"]: field for field in entry["inputs"]}
    inputs: dict[str, str] = {}
    dropped: list[str] = []
    for name, value in proposed.inputs.items():
        field = declared.get(name)
        text = str(value).strip()
        if field is None or not text or len(text) > VALUE_MAX:
            dropped.append(name)
            continue
        if field["choices"]:
            hit = next((choice for choice in field["choices"] if _norm(choice) == _norm(text)), None)
            if hit is None:
                dropped.append(name)
                continue
            text = hit
        inputs[name] = text
    project = proposed.project_name.strip()
    return {"workflow_id": entry["id"], "bundle": entry["bundle"], "inputs": inputs,
            "project_name": project if 2 <= len(project) <= 120 else "",
            "explanation": proposed.reason.strip(), "dropped": sorted(dropped)}
