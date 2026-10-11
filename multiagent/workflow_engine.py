from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, NamedTuple

from pydantic import BaseModel, ValidationError

from .adapters.base import LLMResponse
from .adapters.fake import FakeAdapter
from .artifacts import ArtifactWriter
from .catalog import Catalog, CatalogError, validate_routing_profile
from .contracts import (
    ContentOutput,
    ExecutionMode,
    GroundingStatus,
    OUTPUT_SCHEMAS,
    ResearchMode,
    ResearchOutput,
    RunStatus,
    StrategyOutput,
    TaskRequirements,
    ModelTier,
    VisualBriefOutput,
    WaitingReason,
)
from .db.store import Store
from .errors import (
    BudgetExceeded, EscalationBlocked, HumanSubmissionError, InvalidStateTransition, StructuredOutputError,
    ValidationFailed,
)
from .model_router import ModelBinding, ModelRouter, failure_kind
from .observability import TokenUsageRecord, UsageRecorder, estimate_call_cost
from .prompts import PromptManager
from .routing_policy import (
    ESCALATION_APPROVAL_LEVEL, MODEL_LEVELS, RoutingDecision, RoutingEvidence, RoutingRequest,
    summarize_routing_evidence,
)
from .observability.usage import read_usage, summarize_usage
from .schema_utils import estimate_tokens_conservative
from .workflows import WorkflowCatalog, WorkflowDefinition, WorkflowStep, condition_is_true
from .workflow_validation import (
    WorkflowDefinitionError, validate_resume_point, validate_workflow_definition,
)


if TYPE_CHECKING:
    from .bundles import DefinitionBundle

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _EscalationPolicy:
    """What an economic profile allows when a step fails on the quality of its output (ECO-T06)."""

    mode: str  # supervised | automatic
    ceiling: str
    max_escalations: int


class _Route(NamedTuple):
    """The outcome of resolving a routing profile for one step: the decision and what to run."""

    decision: RoutingDecision
    binding: ModelBinding | None
    requirements: TaskRequirements | None
    reason: str
    effort: str | None


class _Escalation(NamedTuple):
    """Result of trying to escalate a step: paused for a human, or the route to continue with."""

    route: _Route | None
    info: dict[str, Any] | None
    paused: bool = False


class WorkflowEngine:
    """Deterministic workflow orchestrator.

    The workflow defines step order, agent, prompt, and contract. Execution can
    be local, cloud, auto, or human_guided without changing inter-agent contracts.
    """

    def __init__(self, *, store: Store, catalog: Catalog, prompts: PromptManager,
                 workflows: WorkflowCatalog, router: ModelRouter,
                 artifacts: ArtifactWriter, settings,
                 output_schemas: Mapping[str, type[BaseModel]] | None = None):
        self.store = store
        self.catalog = catalog
        self.prompts = prompts
        self.workflows = workflows
        self.router = router
        self.artifacts = artifacts
        self.settings = settings
        self.output_schemas = dict(OUTPUT_SCHEMAS if output_schemas is None else output_schemas)
        self.usage_recorder = UsageRecorder(store)

    @classmethod
    def from_bundle(cls, bundle: DefinitionBundle, *, store: Store,
                    artifacts: ArtifactWriter, settings, adapters: Mapping[str, Any],
                    dry_run: bool = False) -> WorkflowEngine:
        """Wire a trusted definition bundle into a runtime without changing defaults.

        Callers keep ownership of adapter lifetimes and the worker. Each engine
        uses one bundle; use a dedicated worker/store for that bundle's queue.
        """
        configured_adapters = dict(adapters)
        if dry_run:
            configured_adapters["fake"] = FakeAdapter(
                sample_outputs=bundle.sample_outputs, output_schemas=bundle.output_schemas,
            )
        router = ModelRouter(bundle.catalog, configured_adapters, dry_run=dry_run)
        return cls(
            store=store, catalog=bundle.catalog, prompts=bundle.prompts,
            workflows=bundle.workflows, router=router, artifacts=artifacts,
            settings=settings, output_schemas=bundle.output_schemas,
        )

    def preflight(self, workflow_id: str) -> WorkflowDefinition:
        """Load and validate every step before creating or executing a run.

        Preserve KeyError and WorkflowDefinitionError for callers, including
        the validator's structured issues. No provider work occurs here.
        """
        definition = self.workflows.load(workflow_id)
        validate_workflow_definition(
            definition, catalog=self.catalog, settings=self.settings,
            output_schemas=self.output_schemas, prompts=self.prompts,
        )
        return definition

    def validate_waiting_step(self, run: dict[str, Any], saved: dict[str, Any], *,
                              definition: WorkflowDefinition | None = None,
                              current_step: int | None = None) -> WorkflowDefinition:
        """Check durable step identity before accepting a human decision."""
        definition = definition or self.preflight(run["workflow_id"])
        index = run["current_step"] if current_step is None else current_step
        validate_resume_point(
            definition, current_step=index, step_id=saved["step_id"],
            expected_contract=saved.get("expected_contract", saved.get("kind")),
        )
        step = definition.steps[index]
        if (step.prompt_id != saved["prompt_id"]
                or step.prompt_version != saved["prompt_version"]):
            raise InvalidStateTransition("The paused step prompt or version changed")
        if not condition_is_true(step.when, run["request"]):
            raise InvalidStateTransition("The paused step is no longer enabled for this request")
        if type(saved["attempt"]) is not int or saved["attempt"] < 1:
            raise InvalidStateTransition("The paused step has an invalid attempt")
        return definition

    def review_point(self, run: dict[str, Any]) -> tuple[WorkflowDefinition, dict[str, Any]]:
        """Resolve the exact result under review; legacy waits must be unambiguous."""
        try:
            definition = self.preflight(run["workflow_id"])
            budget = bool(run["budget_exhausted"]) or run["waiting_reason"] == WaitingReason.BUDGET_EXHAUSTED.value
            step_id, attempt = run["waiting_step"], run["waiting_attempt"]
            if step_id and type(attempt) is int and attempt > 0:
                artifact = self.store.review_artifact(run["id"], step_id=step_id, attempt=attempt)
            elif not step_id and attempt is None and budget:
                artifact = self.store.review_artifact(run["id"])
            elif (not step_id and attempt is None and not run["waiting_reason"]
                  and run["current_step"] == len(definition.steps) - 1):
                # Before durable wait metadata, only terminal checkpoints existed.
                last = definition.steps[-1]
                artifact = self.store.review_artifact(run["id"], step_id=last.id)
            else:
                raise InvalidStateTransition("The review has incomplete step or attempt metadata")
            index = run["current_step"]
            if budget:
                # A budget may expire before a later step, leaving a previous result.
                index = next((i for i, step in enumerate(definition.steps)
                              if step.id == artifact["step_id"]), -1)
            self.validate_waiting_step(run, artifact, definition=definition, current_step=index)
            if not budget:
                if artifact["approved"] or self.store.latest_step_attempt(run["id"], artifact["step_id"]) != artifact["attempt"]:
                    raise InvalidStateTransition("The review does not identify the current unapproved attempt")
            return definition, artifact
        except (KeyError, WorkflowDefinitionError) as err:
            message = err.to_payload()["error"] if isinstance(err, WorkflowDefinitionError) else str(err)
            raise InvalidStateTransition(message) from err

    # ---------- public execution ----------
    def process_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.QUEUED.value:
            return run

        base_active = float(run.get("active_seconds") or 0.0)
        started_mono = time.monotonic()
        try:
            self._check_cancel(run_id)
            try:
                definition = self.preflight(run["workflow_id"])
            except (KeyError, WorkflowDefinitionError) as exc:
                payload = exc.to_payload() if isinstance(exc, WorkflowDefinitionError) else {}
                self.store.fail_active_run(run_id, exc, issues=payload.get("issues"), message=payload.get("error"))
                return self._finalize_return(run_id)
            index = int(run["current_step"])
            if not 0 <= index <= len(definition.steps):
                raise ValidationFailed("current_step is outside the workflow")
            request = run["request"]
            self.store.update_run(
                run_id,
                status=RunStatus.RUNNING.value,
                error="",
                waiting_reason="",
                waiting_step="",
                waiting_attempt=None,
                heartbeat_at=self._now(),
            )
            self.store.db.log_event(run_id, "run_started", {})
            while index < len(definition.steps):
                self._check_cancel(run_id)
                self._check_active_budget(run_id, base_active, started_mono)
                self.store.heartbeat(run_id)

                step = definition.steps[index]
                if not condition_is_true(step.when, request):
                    self.store.db.log_event(run_id, "step_skipped", {"step_id": step.id, "condition": step.when})
                    index += 1
                    self.store.update_run(run_id, current_step=index)
                    continue

                attempt = self.store.latest_step_attempt(run_id, step.id) + 1
                if attempt > definition.max_iterations + 1:
                    raise BudgetExceeded(f"Maximum revision count reached for step={step.id}")

                fresh = self.store.get_run(run_id)
                if int(fresh["llm_calls"]) >= int(fresh["max_llm_calls"]) and self._step_execution_mode(fresh, step) != ExecutionMode.HUMAN_GUIDED:
                    raise BudgetExceeded("max_llm_calls reached")

                output_schema = self.output_schemas[step.contract]
                requires_web = bool(request.get("requires_web")) if step.requires_web == "from_request" else bool(step.requires_web)
                context = self._context_for_step(
                    run_id, step.id, request, fresh.get("revision_feedback", ""),
                    brand_version=fresh.get("brand_version"),
                    prior_step_ids=[item.id for item in definition.steps[:index]],
                )
                prompt = self.prompts.render(step.prompt_id, step.prompt_version, **context)
                skill_text = "\n\n".join(self.prompts.skill_text(skill) for skill in step.skills)
                final_prompt = prompt.text + ("\n\n## Active skill\n" + skill_text if skill_text else "")
                final_prompt += self._output_contract_instruction(output_schema)
                prompt_sha256 = hashlib.sha256(final_prompt.encode("utf-8")).hexdigest()
                estimated_input = estimate_tokens_conservative(final_prompt)

                execution_mode = self._step_execution_mode(fresh, step)
                if step.id == "research" and requires_web:
                    research_mode = ResearchMode(fresh.get("research_mode") or ResearchMode.HUMAN_BRIDGE.value)
                    if research_mode == ResearchMode.HUMAN_BRIDGE:
                        execution_mode = ExecutionMode.HUMAN_GUIDED
                    elif research_mode == ResearchMode.GEMINI_GROUNDED:
                        execution_mode = ExecutionMode.CLOUD
                    elif research_mode == ResearchMode.NONE:
                        raise ValidationFailed("requires_web=True but research_mode=none")

                profile_id, profile = self.catalog.routing_profile_for(run["workflow_id"], step.id)
                binding, requirements, routing_reason = self._routing_for_step(
                    step=step,
                    execution_mode=execution_mode,
                    requires_web=requires_web,
                    sensitive=bool(request.get("sensitive", False)),
                )
                static_binding = binding  # the workflow's own binding; escalation re-routes from it, not from the selection
                routing_decision = None
                effective_effort = step.effort
                escalation_policy = self._escalation_policy(profile_id, profile)
                escalations_done = (self._escalations_made(fresh.get("events", []), step.id, attempt)
                                    if escalation_policy is not None else 0)
                # A supervised escalation a human approved earlier: this run resumes the step with the stronger model.
                approved_escalation = (self._approved_escalation(fresh.get("events", []), step.id, attempt)
                                       if escalation_policy is not None else None)
                escalation_info: dict[str, Any] | None = None
                if profile_id is not None:
                    route = self._route_with_profile(
                        run_id=run_id, run=run, state=fresh, step=step, attempt=attempt, profile_id=profile_id,
                        profile=profile, request=request, execution_mode=execution_mode, requires_web=requires_web,
                        estimated_input=estimated_input, binding=static_binding, requirements=requirements,
                        routing_reason=routing_reason,
                        escalation=self._escalation_request(approved_escalation, attempt)
                        if approved_escalation is not None else None,
                    )
                    routing_decision, binding, requirements, routing_reason, effective_effort = route
                    if approved_escalation is not None:
                        escalation_info = self._resume_escalation(
                            run_id=run_id, step=step, attempt=attempt, profile_id=profile_id,
                            approved=approved_escalation, decision=routing_decision,
                        )
                        escalations_done += 1
                else:
                    self.store.db.log_event(run_id, "routing_decision", {
                        "step_id": step.id,
                        "execution_mode": execution_mode.value,
                        "research_mode": fresh.get("research_mode", "none"),
                        "preferred_model": binding.preferred_model if binding else None,
                        "fallback_model": binding.fallback_model if binding else None,
                        "reason": routing_reason,
                        "estimated_input_tokens": estimated_input,
                        "requires_web": requires_web,
                    })
                self.store.db.log_event(run_id, "step_started", {
                    "step_id": step.id,
                    "attempt": attempt,
                    "preferred_model": binding.preferred_model if binding else "human_guided",
                    "fallback_model": binding.fallback_model if binding else None,
                    "prompt_id": step.prompt_id,
                    "prompt_version": step.prompt_version,
                    "execution_mode": execution_mode.value,
                    "step_kind": step.kind,
                    "effort": effective_effort,
                    **({"escalation": escalation_info} if escalation_info is not None else {}),
                })

                if execution_mode == ExecutionMode.HUMAN_GUIDED:
                    if request.get("sensitive"):
                        final_prompt = "# PRIVACY WARNING\nThis run is marked SENSITIVE. Review your data-handling policy before copying this prompt to an external service.\n\n" + final_prompt
                        prompt_sha256 = hashlib.sha256(final_prompt.encode("utf-8")).hexdigest()
                    return self._pause_for_human(
                        run_id=run_id,
                        index=index,
                        step=step,
                        attempt=attempt,
                        context=context,
                        final_prompt=final_prompt,
                        prompt_sha256=prompt_sha256,
                        requires_web=requires_web,
                    )

                assert binding is not None and requirements is not None

                profile_models = ([binding.preferred_model] + ([binding.fallback_model] if binding.fallback_model else [])) if profile_id is not None else []
                next_profile_model = profile_models[0] if profile_models else None

                def consume_llm_call() -> None:
                    budget_state = self.store.get_run(run_id)
                    attempted_model = next_profile_model
                    self._check_cancel(run_id)
                    if profile_id is not None:
                        used = sum(
                            1 for event in budget_state.get("events", [])
                            if event.get("event_type") == "routing_dispatch_reserved"
                            and event.get("payload", {}).get("profile_id") == profile_id
                        )
                        profile_limit = profile.get("max_provider_invocations") if isinstance(profile, dict) else None
                        if profile_limit is not None and used >= int(profile_limit):
                            raise BudgetExceeded("routing profile max_provider_invocations reached")
                    if int(budget_state["llm_calls"]) >= int(budget_state["max_llm_calls"]):
                        raise BudgetExceeded("max_llm_calls reached")
                    if profile_id is not None:
                        self.store.db.log_event(run_id, "routing_dispatch_reserved", {
                            "profile_id": profile_id,
                            "step_id": step.id,
                            "attempted_model": attempted_model,
                            "scope": {"run_id": run_id, "profile_id": profile_id},
                        })
                    self.store.increment_llm_calls(run_id)

                def validate_response(parsed, response: LLMResponse) -> None:
                    self._prepare_and_semantic_validate(
                        step_id=step.id,
                        parsed=parsed,
                        requires_web=(bool(request.get("requires_web")) if step.id == "create" else requires_web),
                        cited_sources=response.sources,
                        revision_feedback=fresh.get("revision_feedback", ""),
                    )

                def record_failed_attempt(failure: dict[str, Any]) -> None:
                    if not failure.get("provider_invoked", True):
                        return
                    self.usage_recorder.record(TokenUsageRecord(
                        run_id=run_id,
                        step_id=step.id,
                        agent_id=step.agent,
                        attempt=attempt,
                        provider=failure.get("provider", ""),
                        model=failure.get("observed_model") or failure.get("effective_model") or failure.get("model", ""),
                        model_digest=failure.get("model_digest") or "",
                        prompt_id=step.prompt_id,
                        prompt_version=step.prompt_version,
                        prompt_sha256=prompt_sha256,
                        brand_version=fresh.get("brand_version"),
                        tokens_in=failure.get("tokens_in"),
                        tokens_out=failure.get("tokens_out"),
                        num_ctx=failure.get("num_ctx"),
                        duration_ms=failure.get("duration_ms"),
                        **self._call_cost(
                            failure.get("attempted_model") or failure.get("model"),
                            failure.get("tokens_in"), failure.get("tokens_out"),
                        ),
                        success=False,
                        error_type=failure.get("error_type", "UnknownError"),
                    ))

                captured_attempts: list[dict[str, Any]] = []  # attempts of the router call in flight

                def record_attempt(event: dict[str, Any]) -> None:
                    nonlocal next_profile_model
                    captured_attempts.append(dict(event))
                    if (profile_models and event.get("stage") == "preflight"
                            and event.get("provider_invoked") is False
                            and (event.get("attempted_model") or event.get("model")) == profile_models[0]
                            and len(profile_models) > 1):
                        next_profile_model = profile_models[1]
                    elif profile_models and event.get("provider_invoked") is True and event.get("status") == "failed":
                        retry_limit = int(profile.get("max_retries_per_candidate", 0)) if isinstance(profile, dict) else 0
                        if event.get("error_type") == "StructuredOutputError" and int(event.get("retry", 0)) < retry_limit:
                            next_profile_model = event.get("attempted_model") or event.get("model")
                        elif next_profile_model == profile_models[0] and len(profile_models) > 1:
                            next_profile_model = profile_models[1]
                    attempt_event = dict(event)
                    if routing_decision is not None:
                        attempt_event["routing_requested_model"] = routing_decision.requested_model
                    self.store.db.log_event(run_id, "model_attempt", {
                        "step_id": step.id,
                        **({"profile_id": profile_id} if profile_id is not None else {}),
                        **({"escalation": escalations_done} if escalations_done else {}),
                        **attempt_event,
                    })

                carried_attempts: list[dict[str, Any]] = []  # attempts made before an escalation
                while True:
                    captured_attempts.clear()
                    try:
                        response, attempts = self.router.execute(
                            binding=binding,
                            requirements=requirements,
                            prompt=final_prompt,
                            output_schema=output_schema,
                            before_call=consume_llm_call,
                            validate_response=validate_response,
                            on_failed_attempt=record_failed_attempt,
                            on_attempt=record_attempt,
                            effort=effective_effort,
                        )
                        break
                    except StructuredOutputError as failure:
                        # Only a quality failure that survived its retries (and the fallback) can escalate;
                        # provider errors (network, authentication, quota) never reach this handler, and a
                        # profile without escalation, or a dry run, keeps raising exactly as before.
                        if escalation_policy is None or self.router.dry_run:
                            raise
                        carried_attempts += captured_attempts
                        outcome = self._escalate_step(
                            failure=failure, attempts=captured_attempts, policy=escalation_policy,
                            escalations_done=escalations_done, run_id=run_id, run=run, index=index, step=step,
                            attempt=attempt, profile_id=profile_id, profile=profile, request=request,
                            execution_mode=execution_mode, requires_web=requires_web,
                            estimated_input=estimated_input, binding=static_binding, requirements=requirements,
                            routing_reason=routing_reason, decision=routing_decision,
                            base_active=base_active, started_mono=started_mono,
                        )
                        if outcome.paused:
                            return self._finalize_return(run_id)
                        routing_decision, binding, requirements, routing_reason, effective_effort = outcome.route
                        escalation_info = outcome.info
                        escalations_done += 1
                        profile_models = [binding.preferred_model] + (
                            [binding.fallback_model] if binding.fallback_model else [])
                        next_profile_model = profile_models[0]
                if carried_attempts:
                    attempts = carried_attempts + attempts

                # Soft cancellation discards the result before persisting an artifact if a human
                # requested cancellation while inference was still running.
                self._check_cancel(run_id)
                self.store.increment_successful_llm_calls(run_id)
                data = response.parsed.model_dump(mode="json")
                self._persist_completed_step(
                    run_id=run_id,
                    index=index,
                    step=step,
                    attempt=attempt,
                    context=context,
                    data=data,
                    model_id=response.model,
                    prompt_sha256=prompt_sha256,
                    response=response,
                    attempts=attempts,
                    fresh=fresh,
                    escalation=escalation_info,
                )
                self._check_cancel(run_id)

                self.store.update_run(run_id, revision_feedback="")
                if step.checkpoint_after:
                    self.store.update_run(
                        run_id,
                        status=RunStatus.WAITING_HUMAN.value,
                        current_step=index,
                        waiting_reason=WaitingReason.REVIEW.value,
                        waiting_step=step.id,
                        waiting_attempt=attempt,
                    )
                    self.store.db.log_event(run_id, "checkpoint", {"step_id": step.id, "attempt": attempt})
                    return self._finalize_return(run_id)

                index += 1
                self.store.update_run(run_id, current_step=index)

            self.store.update_run(run_id, status=RunStatus.COMPLETED.value, waiting_reason="", waiting_step="", waiting_attempt=None)
            self.store.db.log_event(run_id, "workflow_completed", {})
            return self._finalize_return(run_id)

        except _CancelledSignal:
            self.store.update_run(
                run_id,
                status=RunStatus.CANCELLED.value,
                waiting_reason="",
                waiting_step="",
                waiting_attempt=None,
            )
            self.store.db.log_event(run_id, "workflow_cancelled", {})
            return self._finalize_return(run_id)
        except BudgetExceeded as exc:
            if self.store.artifact_count(run_id) > 0:
                artifact = self.store.review_artifact(run_id)
                self.store.update_run(
                    run_id,
                    status=RunStatus.WAITING_HUMAN.value,
                    error=str(exc),
                    budget_exhausted=1,
                    waiting_reason=WaitingReason.BUDGET_EXHAUSTED.value,
                    waiting_step=artifact["step_id"],
                    waiting_attempt=artifact["attempt"],
                )
                self.store.db.log_event(run_id, "budget_exhausted", {"error": str(exc), "actionable_artifact": True})
            else:
                self.store.update_run(run_id, status=RunStatus.FAILED.value, error=str(exc), budget_exhausted=1)
                self.store.db.log_event(run_id, "workflow_failed", {"error": str(exc), "type": type(exc).__name__})
            return self._finalize_return(run_id)
        except Exception as exc:
            self.store.fail_active_run(run_id, exc)
            return self._finalize_return(run_id)
        finally:
            try:
                current = self.store.get_run(run_id)
                elapsed = max(0.0, time.monotonic() - started_mono)
                self.store.update_run(run_id, active_seconds=base_active + elapsed)
                self.artifacts.write_summary(self.store.get_run(run_id))
            except Exception:
                pass

    def submit_human_step(self, run_id: str, submission) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.WAITING_HUMAN.value or run.get("waiting_reason") not in {
            WaitingReason.EXECUTE_STEP.value, WaitingReason.RESEARCH.value
        }:
            raise HumanSubmissionError("The run is not waiting for human execution of a step")
        pending = self.store.pending_human_step(run_id)
        if not pending:
            raise HumanSubmissionError("No pending HumanStepRequest exists")

        try:
            definition = self.validate_waiting_step(run, pending)
            if (run["cancel_requested"] or run["waiting_step"] != pending["step_id"]
                    or run["waiting_attempt"] != pending["attempt"]
                    or self.store.latest_step_attempt(run_id, pending["step_id"]) != pending["attempt"]):
                raise InvalidStateTransition("The pending HumanStepRequest does not match the persisted wait")
        except (KeyError, WorkflowDefinitionError, InvalidStateTransition) as err:
            message = err.to_payload()["error"] if isinstance(err, WorkflowDefinitionError) else str(err)
            raise HumanSubmissionError(message) from err
        index = run["current_step"]
        step = definition.steps[index]
        output_schema = self.output_schemas[pending["expected_contract"]]
        raw = submission.raw_response
        raw_path = self.artifacts.write_human_raw(
            run_id=run_id,
            step_index=index,
            step_id=step.id,
            attempt=int(pending["attempt"]),
            raw_response=raw,
            provider=submission.provider,
            model=submission.model,
            prompt_used=submission.prompt_used,
        )
        try:
            parsed_payload = self._extract_json_payload(raw)
            parsed = output_schema.model_validate(parsed_payload)
            self._prepare_and_semantic_validate(
                step_id=step.id,
                parsed=parsed,
                requires_web=(bool(run["request"].get("requires_web")) if step.id in {"research", "create"} else False),
                cited_sources=[],
                revision_feedback=run.get("revision_feedback", ""),
            )
        except (HumanSubmissionError, ValidationError, ValueError, TypeError) as exc:
            self.store.db.log_event(run_id, "human_submission_invalid", {
                "step_id": step.id,
                "attempt": pending["attempt"],
                "error": str(exc),
                "raw_path": str(raw_path.relative_to(self.settings.data_dir)),
            })
            raise HumanSubmissionError(f"Human response does not satisfy {pending['expected_contract']}: {exc}") from exc

        data = parsed.model_dump(mode="json")
        prompt_used = submission.prompt_used or pending["prompt_base"]
        self.store.save_human_submission(
            request_id=pending["id"],
            run_id=run_id,
            step_id=step.id,
            attempt=int(pending["attempt"]),
            provider=submission.provider,
            model=submission.model,
            prompt_used=prompt_used,
            raw_response=raw,
            normalized=data,
            validation={"ok": True, "contract": pending["expected_contract"]},
            notes=submission.notes,
        )
        model_id = f"human:{submission.provider}:{submission.model or 'unspecified'}"
        self.store.record_step(
            run_id=run_id,
            step_id=step.id,
            attempt=int(pending["attempt"]),
            status="completed",
            input_data=pending["context"],
            output_data=data,
            model_id=model_id,
            prompt_id=pending["prompt_id"],
            prompt_version=int(pending["prompt_version"]),
        )
        self.usage_recorder.record(TokenUsageRecord(
            run_id=run_id,
            step_id=step.id,
            agent_id=step.agent,
            attempt=int(pending["attempt"]),
            provider="human",
            model=submission.model or "",
            model_digest="",
            prompt_id=pending["prompt_id"],
            prompt_version=int(pending["prompt_version"]),
            prompt_sha256=pending["prompt_sha256"],
            brand_version=run.get("brand_version"),
            tokens_in=None,
            tokens_out=None,
            num_ctx=None,
            duration_ms=None,
            # A human or external model reports no token counts and has no catalog price: unknown.
            estimated_cost_usd=None,
            success=True,
        ))
        path = self.artifacts.write(
            run_id=run_id,
            step_index=index,
            step_id=step.id,
            attempt=int(pending["attempt"]),
            data=data,
        )
        self.store.add_artifact(
            run_id=run_id,
            step_id=step.id,
            attempt=int(pending["attempt"]),
            kind=pending["expected_contract"],
            path=str(path.relative_to(self.settings.data_dir)),
            data=data,
        )
        self.store.db.log_event(run_id, "human_step_submitted", {
            "step_id": step.id,
            "attempt": pending["attempt"],
            "provider": submission.provider,
            "model": submission.model,
            "provenance": "human_reported",
            "raw_path": str(raw_path.relative_to(self.settings.data_dir)),
        })
        self.store.db.log_event(run_id, "step_completed", {
            "step_id": step.id,
            "attempt": pending["attempt"],
            "artifact_path": str(path.relative_to(self.settings.data_dir)),
            "execution_mode": "human_guided",
        })
        self.store.update_run(run_id, revision_feedback="")
        self._check_cancel(run_id)

        if step.checkpoint_after:
            self.store.update_run(
                run_id,
                status=RunStatus.WAITING_HUMAN.value,
                waiting_reason=WaitingReason.REVIEW.value,
                waiting_step=step.id,
                waiting_attempt=int(pending["attempt"]),
            )
            self.store.db.log_event(run_id, "checkpoint", {"step_id": step.id, "attempt": pending["attempt"]})
        else:
            self.store.update_run(
                run_id,
                status=RunStatus.QUEUED.value,
                current_step=index + 1,
                waiting_reason="",
                waiting_step="",
                waiting_attempt=None,
            )
        return self._finalize_return(run_id)

    # ---------- routing with a profile and bounded escalation (ECO-T06) ----------
    def _route_with_profile(self, *, run_id: str, run: dict[str, Any], state: dict[str, Any], step: WorkflowStep,
                            attempt: int, profile_id: str, profile: dict[str, Any] | None, request: dict[str, Any],
                            execution_mode: ExecutionMode, requires_web: bool, estimated_input: int,
                            binding: ModelBinding | None, requirements: TaskRequirements | None,
                            routing_reason: str, escalation: dict[str, Any] | None = None) -> _Route:
        """Select a model with the step's routing profile, record the decision and build what to run.

        ``state`` is a fresh read of the run (its events feed the routing evidence). With ``escalation`` the
        request asks for one exact model level instead of the step kind's bounds (see ``RoutingRequest``);
        a decision that cannot be satisfied then raises ``EscalationBlocked`` instead of ``ValidationFailed``.
        """
        profile_required = profile.get("required_permissions", []) if isinstance(profile, dict) else []
        caller_grants = request.get("effective_permissions")
        if caller_grants is None:
            caller_grants = request.get("permissions")
        requested_model = (
            profile.get("requested_model") if isinstance(profile, dict) else None
        ) or (binding.preferred_model if binding is not None else None)
        authorized_classes = request.get("authorized_data_classes")
        if authorized_classes is None:
            authorized_classes = request.get("data_classes")
        routing_request = RoutingRequest(
            profile_id=profile_id,
            workflow_id=run["workflow_id"],
            step_id=step.id,
            requested_model=requested_model,
            required_capabilities=frozenset({"structured_output"} | ({"web"} if requires_web else set())),
            required_role=step.agent,
            required_permissions=frozenset(profile_required or ()),
            effective_permissions=frozenset(caller_grants) if caller_grants is not None else None,
            authorized_data_classes=frozenset(authorized_classes) if authorized_classes is not None else None,
            authorization_provenance=request.get("authorization_provenance"),
            data_class="sensitive" if request.get("sensitive") else request.get("data_class", "non_sensitive"),
            execution_mode=execution_mode.value,
            requires_web=requires_web,
            output_schema=step.contract,
            estimated_input_tokens=estimated_input,
            step_kind=step.kind,
            effort=step.effort,
            attempt=attempt,
            escalation=escalation,
        )
        evidence_summary = summarize_routing_evidence(
            run_id, list(state.get("events", [])), read_usage(self.store, run_id)
        )
        routing_decision = self.router.select(
            routing_request,
            RoutingEvidence(scope=run_id, stats_by_model=evidence_summary,
                            provenance="single_run_snapshot"),
        )
        decision_data = routing_decision.to_dict()
        decision_data.update({
            "step_id": step.id,
            "execution_mode": execution_mode.value,
            "research_mode": state.get("research_mode", "none"),
            "estimated_input_tokens": estimated_input,
            "requires_web": requires_web,
            "evidence": evidence_summary,
        })
        self.store.db.log_event(run_id, "routing_decision", decision_data)
        effective_effort = step.effort
        if routing_decision.status == "selected":
            selected = routing_decision.selected_model
            if not selected:
                raise ValidationFailed("Routing profile selected no model")
            selected_spec = self.catalog.model(selected)
            fallbacks = [model for model in routing_decision.fallback_models if model != selected]
            retries = int(profile.get("max_retries_per_candidate", 0))
            binding = ModelBinding(
                preferred_model=selected,
                fallback_model=fallbacks[0] if fallbacks else None,
                max_retries=retries,
                fallback_max_retries=retries,
            )
            requirements = TaskRequirements(
                requires_web=requires_web,
                output_schema=step.contract,
                tier=ModelTier.CLOUD if str(selected_spec.tier).casefold() == "cloud" else ModelTier.LOCAL,
            )
            routing_reason = "explicit routing profile selected"
            if routing_decision.effort is not None:
                # The decision keeps the step's own effort and only adds the profile's per-kind one
                # when the step asked for none.
                effective_effort = routing_decision.effort
        elif routing_decision.status == "manual" and execution_mode == ExecutionMode.HUMAN_GUIDED:
            binding, requirements = None, None
            routing_reason = "human_guided requested"
        elif escalation is not None:
            codes = tuple(routing_decision.reason_codes)
            raise self._escalation_blocked(
                run_id, step, attempt,
                code="no_eligible_candidate" if "no_eligible_candidate" in codes else "selection_blocked",
                message=(f"Step {step.id!r} cannot be escalated to level {escalation['to_level']}: no model of that "
                         f"level is eligible under the profile ({', '.join(codes)}). Human intervention is required."),
                reason_codes=codes,
                details={"from_level": escalation.get("from_level"), "from_model": escalation.get("from_model"),
                         "to_level": escalation["to_level"]},
            )
        else:
            raise ValidationFailed(
                "Routing profile requires manual selection or is blocked: "
                + ", ".join(routing_decision.reason_codes)
            )
        return _Route(routing_decision, binding, requirements, routing_reason, effective_effort)

    def _escalation_policy(self, profile_id: str | None, profile: Any) -> _EscalationPolicy | None:
        """The escalation a step's profile allows, or None: then a failed step fails exactly as it always did.

        Only an economic profile in ``supervised`` or ``automatic`` mode that declares ``escalation`` with a
        limit of at least 1 escalates. Profiles without it, ``single`` mode, a limit of 0 and an invalid
        declaration all behave as before. A dry run never escalates.
        """
        if profile_id is None or not isinstance(profile, dict) or "escalation" not in profile or self.router.dry_run:
            return None
        try:
            checked = validate_routing_profile(profile_id, profile)
        except CatalogError:
            return None  # the selector blocks an invalid profile before any step could fail
        declared = checked.get("escalation")
        if checked.get("mode") not in ("supervised", "automatic") or not isinstance(declared, dict):
            return None
        if declared["max_escalations_per_step"] < 1:
            return None
        return _EscalationPolicy(checked["mode"], declared["ceiling"], declared["max_escalations_per_step"])

    @staticmethod
    def _events_of(events: list[dict[str, Any]], step_id: str, attempt: int):
        for event in events:
            payload = event.get("payload") or {}
            if payload.get("step_id") == step_id and payload.get("attempt") == attempt:
                yield event["event_type"], payload

    @classmethod
    def _escalations_made(cls, events: list[dict[str, Any]], step_id: str, attempt: int) -> int:
        """Escalations already carried out for this attempt of the step (the limit applies per attempt)."""
        return sum(1 for kind, _ in cls._events_of(events, step_id, attempt) if kind == "model_escalated")

    @classmethod
    def _approved_escalation(cls, events: list[dict[str, Any]], step_id: str, attempt: int) -> dict[str, Any] | None:
        """The escalation a human approved for this attempt and the engine has not yet carried out."""
        approved = None
        for kind, payload in cls._events_of(events, step_id, attempt):
            if kind == "escalation_approved":
                approved = payload
            elif kind == "model_escalated":
                approved = None
        return approved

    @staticmethod
    def _escalation_request(proposal: Mapping[str, Any], attempt: int) -> dict[str, Any]:
        """The ``RoutingRequest.escalation`` mapping for an escalation a human approved."""
        return {
            "number": proposal["escalation"], "attempt": attempt, "from_level": proposal["from_level"],
            "from_model": proposal["from_model"], "to_level": proposal["to_level"], "reason": proposal["reason"],
            "approved_by_human": True,
        }

    def _cost_so_far(self, run_id: str, step_id: str) -> dict[str, Any]:
        """Estimated cost spent so far by the run and by the step; unknown costs stay apart, never 0."""
        records = read_usage(self.store, run_id)
        keys = ("estimated_cost_usd", "cost_known_records", "cost_unknown_records", "cost_other_currencies",
                "cost_complete")

        def totals(rows):
            summary = summarize_usage(rows)["run"]
            return {"currency": "USD", **{key: summary[key] for key in keys}}

        return {"run": totals(records), "step": totals([row for row in records if row["step_id"] == step_id])}

    def _escalation_blocked(self, run_id: str, step: WorkflowStep, attempt: int, *, code: str, message: str,
                            reason_codes: tuple[str, ...] = (),
                            details: dict[str, Any] | None = None) -> EscalationBlocked:
        """Record why a step could not be escalated and return the error that fails it."""
        self.store.db.log_event(run_id, "escalation_blocked", {
            "step_id": step.id, "attempt": attempt, "code": code, "reason_codes": list(reason_codes),
            "message": message, **(details or {}),
        })
        return EscalationBlocked(message, code=code, reason_codes=reason_codes)

    def _resume_escalation(self, *, run_id: str, step: WorkflowStep, attempt: int, profile_id: str,
                           approved: dict[str, Any], decision: RoutingDecision) -> dict[str, Any]:
        """Carry out an escalation a human approved: the step was just re-routed to the escalated level.

        The selection is made again, so a changed catalog, grant or budget still applies. If it no longer
        picks the model the human approved, nothing runs and the step fails asking for a person.
        """
        if decision.selected_model != approved["to_model"]:
            raise self._escalation_blocked(
                run_id, step, attempt, code="approved_model_no_longer_selected",
                message=(f"Step {step.id!r}: the approved escalation to {approved['to_model']!r} no longer matches "
                         f"the eligible choice ({decision.selected_model!r}). Human intervention is required."),
                details={"from_level": approved["from_level"], "from_model": approved["from_model"],
                         "to_level": approved["to_level"]},
            )
        info = self._escalation_info(
            run_id, step, attempt, profile_id, decision=decision, number=approved["escalation"],
            mode=approved["mode"], from_level=approved["from_level"], from_model=approved["from_model"],
            reason=approved["reason"], error=approved.get("error"), error_type=approved.get("error_type"),
            approved_by_human=True,
        )
        self.store.db.log_event(run_id, "model_escalated", info)
        return info

    def _escalation_info(self, run_id: str, step: WorkflowStep, attempt: int, profile_id: str, *,
                         decision: RoutingDecision, number: int, mode: str, from_level: str, from_model: str,
                         reason: str, error: str | None, error_type: str | None,
                         approved_by_human: bool) -> dict[str, Any]:
        """The record of an escalation, shared by ``escalation_requested`` and ``model_escalated``."""
        evidence = decision.price_evidence or {}
        projected = evidence.get("estimated_cost")
        return {
            "step_id": step.id, "attempt": attempt, "escalation": number, "mode": mode, "profile_id": profile_id,
            "from_level": from_level, "from_model": from_model,
            "to_level": decision.selected_level, "to_model": decision.selected_model,
            "reason": reason, "error": error, "error_type": error_type,
            "estimated_cost_so_far": self._cost_so_far(run_id, step.id),
            # Projected cost of the escalated call: null while the request carries no output-token estimate.
            "estimated_cost": ({"amount": projected, "currency": evidence.get("estimated_cost_currency")}
                               if projected is not None else None),
            "price_evidence": evidence or None,
            "approved_by_human": approved_by_human,
        }

    def _escalate_step(self, *, failure: StructuredOutputError, attempts: list[dict[str, Any]],
                       policy: _EscalationPolicy, escalations_done: int, run_id: str, run: dict[str, Any],
                       index: int, step: WorkflowStep, attempt: int, profile_id: str, profile: dict[str, Any] | None,
                       request: dict[str, Any], execution_mode: ExecutionMode, requires_web: bool,
                       estimated_input: int, binding: ModelBinding | None, requirements: TaskRequirements | None,
                       routing_reason: str, decision: RoutingDecision | None, base_active: float,
                       started_mono: float) -> _Escalation:
        """A step exhausted its retries on the quality of its output: escalate once to the next level, or stop.

        Raises ``EscalationBlocked`` (recording ``escalation_blocked``) when the profile's limit or ceiling is
        reached, a budget is exhausted, or no model of the next level is eligible. Otherwise either pauses the
        step for a human (supervised mode and a target level of T3 or above) or logs ``model_escalated`` and
        returns the route of the escalated model.
        """
        self._check_cancel(run_id)
        invoked = [item for item in attempts if item.get("provider_invoked")]
        if invoked:
            from_model = invoked[-1].get("attempted_model") or invoked[-1].get("model")
        else:
            from_model = decision.selected_model if decision is not None else None
        spec = self.catalog.models.get(str(from_model)) if from_model else None
        from_level = spec.level if spec is not None and spec.classification == "classified" else None
        error_type = type(failure).__name__
        reason = failure_kind(failure)
        context = {"from_level": from_level, "from_model": from_model, "reason": reason}

        def blocked(code: str, message: str) -> EscalationBlocked:
            return self._escalation_blocked(run_id, step, attempt, code=code, message=message, details={
                **context, "error": str(failure), "error_type": error_type, "ceiling": policy.ceiling})

        failed = f"Step {step.id!r} failed at level {from_level} with model {from_model!r}: {failure}"
        if from_level is None:
            raise blocked("from_level_unknown", f"{failed}. Its level is unknown, so it cannot be escalated. "
                                               "Human intervention is required.")
        if escalations_done >= policy.max_escalations:
            raise blocked("max_escalations_reached",
                          f"{failed}. The step was already escalated {escalations_done} time(s), the profile's "
                          "limit. Human intervention is required.")
        position = MODEL_LEVELS.index(from_level)
        if position >= MODEL_LEVELS.index(policy.ceiling):
            raise blocked("ceiling_reached", f"{failed}. The profile's escalation ceiling is {policy.ceiling} "
                                             "(T4 is never automatic). Human intervention is required.")
        to_level = MODEL_LEVELS[position + 1]

        # Budgets are checked before anything is proposed or run: an exhausted one means no escalation.
        latest = self.store.get_run(run_id)
        if int(latest["llm_calls"]) >= int(latest["max_llm_calls"]):
            raise blocked("max_llm_calls_reached", f"{failed}. Escalation to {to_level} needs another model call "
                                                   "but max_llm_calls is reached.")
        profile_limit = profile.get("max_provider_invocations") if isinstance(profile, dict) else None
        if profile_limit is not None:
            used = sum(1 for event in latest.get("events", [])
                       if event.get("event_type") == "routing_dispatch_reserved"
                       and event.get("payload", {}).get("profile_id") == profile_id)
            if used >= int(profile_limit):
                raise blocked("max_provider_invocations_reached",
                              f"{failed}. Escalation to {to_level} needs another dispatch but the profile's "
                              "max_provider_invocations is reached.")
        try:
            self._check_active_budget(run_id, base_active, started_mono)
        except BudgetExceeded as exc:
            raise blocked("max_run_seconds_reached", f"{failed}. Escalation to {to_level} is skipped: {exc}.") from exc

        escalation = {"number": escalations_done + 1, "attempt": attempt, "from_level": from_level,
                      "from_model": from_model, "to_level": to_level, "reason": reason}
        # Same profile, role, permissions, data class and mode as the first selection; only the level differs.
        # The profile's planning budgets apply to the candidate, and no eligible one blocks the step.
        route = self._route_with_profile(
            run_id=run_id, run=run, state=latest, step=step, attempt=attempt, profile_id=profile_id, profile=profile,
            request=request, execution_mode=execution_mode, requires_web=requires_web,
            estimated_input=estimated_input, binding=binding, requirements=requirements,
            routing_reason=routing_reason, escalation=escalation,
        )
        info = self._escalation_info(
            run_id, step, attempt, profile_id, decision=route.decision, number=escalations_done + 1,
            mode=policy.mode, from_level=from_level, from_model=from_model, reason=reason, error=str(failure),
            error_type=error_type, approved_by_human=False,
        )
        if (policy.mode == "supervised"
                and MODEL_LEVELS.index(to_level) >= MODEL_LEVELS.index(ESCALATION_APPROVAL_LEVEL)):
            # A move to T3 or above waits for a person, through the same human checkpoint as a review.
            self.store.db.log_event(run_id, "escalation_requested", info)
            self.store.update_run(
                run_id,
                status=RunStatus.WAITING_HUMAN.value,
                current_step=index,
                waiting_reason=WaitingReason.ESCALATION.value,
                waiting_step=step.id,
                waiting_attempt=attempt,
            )
            return _Escalation(None, None, paused=True)
        self.store.db.log_event(run_id, "model_escalated", info)
        return _Escalation(route, info)

    # ---------- helpers ----------
    def _pause_for_human(self, *, run_id: str, index: int, step: WorkflowStep, attempt: int,
                         context: dict[str, str], final_prompt: str, prompt_sha256: str,
                         requires_web: bool) -> dict[str, Any]:
        self.store.create_human_step_request(
            run_id=run_id,
            step_id=step.id,
            attempt=attempt,
            prompt_id=step.prompt_id,
            prompt_version=step.prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_base=final_prompt,
            expected_contract=step.contract,
            context=context,
        )
        request_path = self.artifacts.write_human_request(
            run_id=run_id,
            step_index=index,
            step_id=step.id,
            attempt=attempt,
            prompt=final_prompt,
            expected_contract=step.contract,
        )
        reason = WaitingReason.RESEARCH if step.id == "research" and requires_web else WaitingReason.EXECUTE_STEP
        self.store.update_run(
            run_id,
            status=RunStatus.WAITING_HUMAN.value,
            waiting_reason=reason.value,
            waiting_step=step.id,
            waiting_attempt=attempt,
        )
        self.store.db.log_event(run_id, "human_step_requested", {
            "step_id": step.id,
            "attempt": attempt,
            "expected_contract": step.contract,
            "prompt_path": str(request_path.relative_to(self.settings.data_dir)),
        })
        return self._finalize_return(run_id)

    def _call_cost(self, catalog_model_id: str | None, tokens_in: int | None,
                   tokens_out: int | None) -> dict[str, Any]:
        """``estimated_cost_usd`` and ``cost_basis`` of one provider call, from the catalog price evidence.

        Both stay ``None`` (unknown, never 0) when the model is not in the catalog, has no price in force
        now, or a token count is missing. A costing problem never affects the step: telemetry only.
        """
        unknown: dict[str, Any] = {"estimated_cost_usd": None, "cost_basis": None}
        if not catalog_model_id:
            return unknown
        try:
            spec = self.catalog.models.get(str(catalog_model_id))
            estimate = estimate_call_cost(spec, tokens_in=tokens_in, tokens_out=tokens_out) if spec else None
        except Exception:
            _logger.warning("Cost estimate failed for %s; recording it as unknown", catalog_model_id, exc_info=True)
            return unknown
        if estimate is None:
            return unknown
        return {"estimated_cost_usd": estimate.estimated_cost_usd, "cost_basis": estimate.basis}

    def _persist_completed_step(self, *, run_id: str, index: int, step: WorkflowStep, attempt: int,
                                context: dict[str, Any], data: dict[str, Any], model_id: str,
                                prompt_sha256: str, response: LLMResponse, attempts: list[dict], fresh: dict[str, Any],
                                escalation: dict[str, Any] | None = None) -> None:
        self.store.record_step(
            run_id=run_id,
            step_id=step.id,
            attempt=attempt,
            status="completed",
            input_data=context,
            output_data=data,
            model_id=model_id,
            prompt_id=step.prompt_id,
            prompt_version=step.prompt_version,
        )
        path = self.artifacts.write(run_id=run_id, step_index=index, step_id=step.id, attempt=attempt, data=data)
        relative_path = path.relative_to(self.settings.data_dir)
        self.store.add_artifact(run_id=run_id, step_id=step.id, attempt=attempt, kind=step.contract, path=str(relative_path), data=data)
        tokens_in = response.tokens_in if response.tokens_in > 0 else None
        tokens_out = response.tokens_out if response.tokens_out > 0 else None
        self.usage_recorder.record(TokenUsageRecord(
            run_id=run_id,
            step_id=step.id,
            agent_id=step.agent,
            attempt=attempt,
            provider=response.provider,
            model=response.model,
            model_digest=response.model_digest,
            prompt_id=step.prompt_id,
            prompt_version=step.prompt_version,
            prompt_sha256=prompt_sha256,
            brand_version=fresh.get("brand_version"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            num_ctx=int(response.metadata.get("num_ctx") or 0) or None,
            duration_ms=response.latency_ms,
            **self._call_cost(response.metadata.get("catalog_model_id"), tokens_in, tokens_out),
            success=True,
            error_type="",
        ))
        self.store.db.log_event(run_id, "model_selected", {
            "step_id": step.id,
            "provider": response.provider,
            "model": response.model,
            "model_digest": response.model_digest,
            "attempts": attempts,
            "estimated_input_tokens": response.metadata.get("estimated_input_tokens"),
            "thinking": response.metadata.get("thinking"),
            **({"escalation": escalation} if escalation is not None else {}),
        })
        self.store.db.log_event(run_id, "step_completed", {"step_id": step.id, "attempt": attempt, "artifact_path": str(relative_path)})

    def _context_for_step(self, run_id: str, step_id: str, request: dict[str, Any], revision_feedback: str,
                          *, brand_version: int | None,
                          prior_step_ids: list[str] | None = None) -> dict[str, str]:
        if "workflow_id" in request:
            context = dict(request["inputs"])
            for prior_id in prior_step_ids or []:
                output = self.store.latest_step_output(run_id, prior_id)
                context[f"{prior_id}_output"] = (
                    json.dumps(output, ensure_ascii=False, indent=2) if output is not None else ""
                )
            previous = self.store.latest_step_output(run_id, step_id)
            context["previous_output"] = (
                json.dumps(previous, ensure_ascii=False, indent=2) if previous is not None else ""
            )
            context["revision_feedback"] = revision_feedback
            return context
        brand_sections = ["core", "voice"]
        if step_id == "design":
            brand_sections = ["core", "voice", "visual"]
        elif step_id == "strategy":
            brand_sections = ["core", "voice", "strategy"]
        brand = self.store.brand_context(brand_sections, version=brand_version) if request.get("use_brand_context", True) and brand_version is not None else ""
        research = self.store.latest_step_output(run_id, "research") or {}
        strategy = self.store.latest_step_output(run_id, "strategy") or {}
        content = self.store.latest_step_output(run_id, "create") or {}
        previous = self.store.latest_step_output(run_id, step_id) or {}
        return {
            "objective": request.get("objective", ""),
            "topic": request.get("topic", ""),
            "platform": request.get("platform", ""),
            "audience": request.get("audience", ""),
            "instructions": request.get("instructions", ""),
            "brand_context": brand,
            "brand_visual_context": brand,
            "research_context": json.dumps(research, ensure_ascii=False, indent=2) if research else "",
            "strategy_context": json.dumps(strategy, ensure_ascii=False, indent=2) if strategy else "",
            "content_context": json.dumps(content, ensure_ascii=False, indent=2) if content else "",
            "previous_output": json.dumps(previous, ensure_ascii=False, indent=2) if previous else "",
            "revision_feedback": revision_feedback,
        }

    def _step_execution_mode(self, run: dict[str, Any], step: WorkflowStep) -> ExecutionMode:
        overrides = run.get("step_modes") or {}
        if step.id in overrides:
            return ExecutionMode(overrides[step.id])
        return ExecutionMode(run.get("execution_mode") or ExecutionMode.AUTO.value)

    def _routing_for_step(self, *, step: WorkflowStep, execution_mode: ExecutionMode, requires_web: bool, sensitive: bool = False):
        if execution_mode == ExecutionMode.HUMAN_GUIDED:
            return None, None, "human_guided requested" + ("; SENSITIVE DATA warning" if sensitive else "")
        if execution_mode == ExecutionMode.LOCAL:
            preferred = "local_reasoning" if step.id in {"strategy"} else "local_default"
            req = TaskRequirements(requires_web=False, output_schema=step.contract, tier=ModelTier.LOCAL)
            return ModelBinding(preferred_model=preferred, fallback_model=None), req, "explicit local"
        if execution_mode == ExecutionMode.CLOUD:
            if sensitive:
                raise ValidationFailed("Content marked sensitive cannot use execution_mode=cloud")
            preferred = "gemini_grounded" if requires_web else ("cloud_advanced" if step.id in {"strategy", "design"} else "cloud_fast")
            req = TaskRequirements(requires_web=requires_web, output_schema=step.contract, tier=ModelTier.CLOUD)
            return ModelBinding(preferred_model=preferred, fallback_model=None), req, "explicit cloud"
        # AUTO: sensitive content forces local execution without cloud fallback.
        if sensitive:
            preferred = "local_reasoning" if step.id in {"strategy"} else "local_default"
            req = TaskRequirements(requires_web=False, output_schema=step.contract, tier=ModelTier.LOCAL)
            return ModelBinding(preferred_model=preferred, fallback_model=None), req, "sensitive -> local"
        # AUTO: static workflow binding, maximum one fallback.
        req = TaskRequirements(requires_web=requires_web, output_schema=step.contract,
                               tier=ModelTier.CLOUD if requires_web else ModelTier.LOCAL)
        return ModelBinding(preferred_model=step.preferred_model, fallback_model=step.fallback_model), req, "workflow static binding"

    def _prepare_and_semantic_validate(self, *, step_id: str, parsed, requires_web: bool,
                                       cited_sources: list[str], revision_feedback: str = "") -> None:
        if step_id == "research" and isinstance(parsed, ResearchOutput):
            merged = list(dict.fromkeys([*parsed.source_urls, *cited_sources]))
            parsed.source_urls = merged
            if requires_web and not parsed.source_urls:
                raise ValueError("ResearchOutput has no sources despite requires_web=True")
            if requires_web and parsed.grounding_status == GroundingStatus.UNVERIFIED:
                parsed.grounding_status = GroundingStatus.PARTIAL if parsed.source_urls else GroundingStatus.UNVERIFIED
        elif step_id == "strategy" and isinstance(parsed, StrategyOutput):
            if not parsed.supporting_points:
                raise ValueError("StrategyOutput has no supporting_points")
        elif step_id == "create" and isinstance(parsed, ContentOutput):
            if not parsed.title.strip():
                raise ValueError("ContentOutput requires a non-empty title")
            if not parsed.hashtags:
                raise ValueError("ContentOutput requires hashtags for a social post")
            match = re.search(r"exactly\s+(\d+)\s+hashtags?", revision_feedback or "", flags=re.I)
            if match and len(parsed.hashtags) != int(match.group(1)):
                raise ValueError(f"Human feedback requires exactly {match.group(1)} hashtags")
            if requires_web and not parsed.source_urls_used:
                raise ValueError("Web-based ContentOutput must provide source_urls_used")
        elif step_id == "design" and isinstance(parsed, VisualBriefOutput):
            if not parsed.technical_prompt.strip() or not parsed.human_brief.strip():
                raise ValueError("VisualBriefOutput requires human_brief and technical_prompt")

    @staticmethod
    def _extract_json_payload(raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    payload = json.loads(text[start:end+1])
                except json.JSONDecodeError:
                    raise HumanSubmissionError("The human response does not contain valid JSON") from exc
            else:
                raise HumanSubmissionError("The human response does not contain valid JSON") from exc
        if not isinstance(payload, dict):
            raise HumanSubmissionError("The human response must be a JSON object")
        return payload

    @staticmethod
    def _output_contract_instruction(output_schema) -> str:
        schema = json.dumps(output_schema.model_json_schema(), ensure_ascii=False, indent=2)
        return (
            "\n\n## REQUIRED OUTPUT CONTRACT\n"
            "Return only a valid JSON object. Do not use Markdown or text outside the JSON. "
            "The object must satisfy this JSON Schema:\n" + schema
        )

    def _check_cancel(self, run_id: str) -> None:
        if self.store.get_run(run_id)["cancel_requested"]:
            raise _CancelledSignal()

    def _check_active_budget(self, run_id: str, base_active: float, started_mono: float) -> None:
        fresh = self.store.get_run(run_id)
        if base_active + (time.monotonic() - started_mono) > float(fresh["max_run_seconds"]):
            raise BudgetExceeded("max_run_seconds reached")

    def _finalize_return(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        self.artifacts.write_summary(run)
        return run

    @staticmethod
    def _now() -> str:
        from .db.database import utcnow
        return utcnow()

    def _estimate_cost(self, model_id: str, tokens_in: int, tokens_out: int) -> float | None:
        # Catalog values have no verified source, date, or billing unit.
        return None


class _CancelledSignal(Exception):
    pass
