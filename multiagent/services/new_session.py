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
from typing import Any

from ..budget_cut import policy_from_settings
from ..call_guards import SCOPE_RUN_STEP, CallContext, GuardEnvironment, Pause, Stop, build_call_guards
from ..contracts import ModelTier, NewSessionProposal, TaskRequirements
from ..document_trust import FIXED_CLAUSE, Provenance, wrap_untrusted
from ..model_router import ModelBinding
from ..observability import TokenUsageRecord, estimate_call_cost
from .errors import INVALID_REQUEST, ServiceError, service_errors
from .usage import NEW_SESSION_USAGE_ID

SOURCE_MODEL = "model"
SOURCE_KEYWORDS = "keywords"
TEXT_MAX = 2000
MAX_WORKFLOWS = 60
DESCRIPTION_MAX = 240
LABEL_MAX = 80
VALUE_MAX = 4000
CATALOG_MARKER = "CATALOG_JSON:"
PROVENANCE = Provenance(kind="gui_free_text", label="free text typed in the new session chat")

INSTRUCTIONS = (
    "You help an operator start a workflow of a framework. Choose the workflow from the catalog that best fits the "
    "request and fill its inputs from the request. Reply with one JSON object and nothing else: "
    '{"workflow_id": "<id from the catalog or empty>", "bundle": "<bundle of that entry or null>", '
    '"inputs": {"<input name>": "<text>"}, "project_name": "<short name or empty>", "reason": "<one sentence>"}. '
    "Use only ids and input names that appear in the catalog. For an input with choices use exactly one of them. "
    "Leave an input out when the request does not give it. If nothing fits, leave workflow_id empty. "
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

    def _catalog(self) -> list[dict[str, Any]]:
        """Every workflow of every origin with its description and the inputs the chat asks (declared by the workflow)."""
        definitions = self._services.definitions
        entries: list[dict[str, Any]] = []
        for bundle in [None, *[item["name"] for item in definitions.list_bundles()]]:
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

    def _ask(self, prompt: str, *, approval_id: str | None = None) -> NewSessionProposal:
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

            verdict = chain.check(call.with_fields(step_id=step_id, step_kind="work", data_class="non_sensitive",
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
                  approval_id: str | None = None) -> dict[str, Any]:
        """Propose a workflow and inputs for ``text``.

        Returns ``{"source": "model" | "keywords", "reason": str, "proposal": {...} | None}``. ``proposal`` is
        ``{"workflow_id", "bundle", "inputs", "project_name", "explanation", "dropped"}`` and always names a workflow
        that exists; ``source`` ``keywords`` means the caller must use its own word matching, and ``reason`` says why
        (``disabled``, ``no_workflows``, ``model_error``, ``refused``, ``invalid_output`` or ``no_match``).
        """
        clean = str(text or "").strip()
        if not clean:
            raise ServiceError(INVALID_REQUEST, "text must not be empty", status=422, details={"field": "text"})
        if len(clean) > TEXT_MAX:
            raise ServiceError(INVALID_REQUEST, f"text admits up to {TEXT_MAX} characters", status=422, details={"field": "text"})
        if not self.enabled:
            return _fallback("disabled")
        catalog = self._catalog()
        if not catalog:
            return _fallback("no_workflows")
        tier = self._model_tier()
        if tier != ModelTier.LOCAL and not confirm_cloud:
            return {**_fallback("cloud_confirmation_required"),
                    "model": self._services.settings.gui_new_session_model, "tier": tier.value}
        try:
            proposed = self._ask(self.build_prompt(clean, catalog), approval_id=approval_id)
        except _SecretReview as held:
            return {**_fallback("secret_review_required"), "approval_id": held.approval_id}
        except _BudgetCut:
            return _fallback("budget_cut")
        except _Refused:
            return _fallback("refused")
        except Exception as exc:  # noqa: BLE001 - no model, a provider error or a bad answer all end in the word matching
            from ..errors import StructuredOutputError
            return _fallback("invalid_output" if isinstance(exc, StructuredOutputError) else "model_error")
        if not proposed.workflow_id.strip():
            return _fallback("no_match")
        checked = validate_proposal(proposed, catalog)
        return _fallback("invalid_output") if checked is None else {"source": SOURCE_MODEL, "reason": "", "proposal": checked}


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
