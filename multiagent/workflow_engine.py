from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import re
import time
import math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Mapping, NamedTuple, Sequence

from pydantic import BaseModel, ValidationError

from .adapters.base import LLMResponse
from .adapters.fake import FakeAdapter
from .artifacts import ArtifactWriter
from .budget_cut import BudgetPolicy, policy_from_settings
from .call_guards import (
    SCOPE_RUN_STEP, STOP_USER_CANCEL, CallContext, CallPaused, GuardEnvironment, Pause, Stop, Verdict, build_call_guards,
)
from .catalog import Catalog, CatalogError, ModelSpec, validate_routing_profile
from .context.project_memory import (
    SECTION_HEADING as MEMORY_HEADING, MemorySetting, entry_provenance, entry_text, omitted_note, render_entry,
    resolve_setting, select_entries,
)
from .context.estimate import ContextSettings, compaction_pending, context_settings_for
from .context.summary import (
    CONTEXT_AGENT_ID, SummaryAccess, SummaryPolicy, SummarySchemaError, content_text, render_summary_for_prompt,
    summarize_context,
)
from .context.workflow import (
    EVENT_CONTEXT_COMPACTED, EVENT_CONTEXT_COMPACTION_SKIPPED, KIND_CONTEXT_SUMMARY, KIND_INPUT, KIND_PREVIOUS_OUTPUT,
    KIND_PROJECT_MEMORY, PLACEMENT_SECTION,
    KIND_REVISION_FEEDBACK, KIND_STEP_OUTPUT, ORIGINAL_MEDIA_TYPE, REASON_INTERNAL_ERROR, SUMMARY_MEDIA_TYPE,
    CompactionPlan, CompactionState, ContextPart, OutputRecord, StepContext, build_context_parts,
    check_pinned_in_context, compacted_event, minimum_summary_tokens, next_state, output_text, plan_compaction,
    resolve_compaction_state, skipped_event,
)
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
from .db.envelope_store import EnvelopeStore
from .db.memory_store import MemoryStore
from .db.store import Store
from .document_trust import (
    ACTION_PASS, ACTION_PAUSE, ACTION_QUARANTINE, EVENT_QUARANTINED, EVENT_REVIEW_REQUESTED, BlobSink,
    DetectionPolicy, Provenance, Screening, SourceKind, derive, render_for_prompt, resolve_policy, screen,
)
from .errors import (
    BudgetExceeded, EscalationBlocked, HumanSubmissionError, InvalidStateTransition, StructuredOutputError,
    ValidationFailed,
)
from .model_router import ModelBinding, ModelRouter, failure_kind
from .observability import TokenUsageRecord, UsageRecorder, estimate_call_cost
from .prompts import PromptManager
from .routing_preferences import resolve_profile
from .routing_policy import (
    ESCALATION_APPROVAL_LEVEL, MODEL_LEVELS, RoutingDecision, RoutingEvidence, RoutingRequest,
    summarize_routing_evidence,
)
from .observability.otel import build_telemetry
from .observability.usage import read_usage, summarize_usage
from .schema_utils import estimate_tokens_conservative
from .step_cache import (EVENT_HIT, KIND_MODEL, STEP_CACHE_CODE_VERSION, ZERO_USAGE, StepCache, StepCachePolicy,
                         model_step_key)
from .tasks.envelopes import blob_ref, envelope_sha256
from .tools.contracts import ToolCallContext, ToolErrorCode, ToolOrigin, ToolRequest, request_id_for
from .tools.invoker import ToolInvoker
from .tools.permissions import InvalidArguments, check_call, required_permissions
from .tools.registry import ToolLookupError, ToolRegistry
from .tools.selection import SelectionConfig, SelectionError, load_selection_config, select_tool
from .tools.trust import render_result_for_prompt
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


# ---------- untrusted sources in the context of a step (BDI-T02, contract BDI-T01 sections 4 to 8) ----------
DOCUMENT_ACTOR = "document_trust"        # the actor that stores the blob of a quarantined fragment
OUTPUT_CONTRACT_REMINDER = (          # fixed tail of every step prompt: trusted text after any untrusted data
    "## REQUIRED OUTPUT CONTRACT\n"
    "Return only a valid JSON object that satisfies the OUTPUT SCHEMA above. "
    "Do not use Markdown or text outside the JSON."
)
SUMMARY_HEADING = "## Context summary"   # first line of the CMP summary section; it stays outside the envelope
_SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}
MAX_EVENT_SIGNALS = 100                  # what one document_quarantined / document_review_requested event lists
MAX_EVENT_FRAGMENTS = 50
# The policy an engine uses when its caller names none. ``None`` is "no policy profile active": the barrier is
# off and the prompts are exactly what they were before BDI-T02. Contract BDI-T01 section 8 fixes this default
# to off (only the user can change it); switching it on by default is changing this one line to a policy
# ("quarantine_warn" is the contract's default policy once a profile is active).
DEFAULT_DOCUMENT_POLICY: str | DetectionPolicy | None = None


def _clean_tree(node: Any, provenance: Provenance, *, policy: DetectionPolicy, store: BlobSink | None,
                found: list[Screening]) -> Any:
    """``node`` (parsed JSON) with every text screened one by one; the screenings that did not pass go to ``found``.

    A step output is JSON the model wrote, so its texts are screened as texts (what the model reads) and not as
    the escaped JSON line. A quarantined paragraph is replaced inside its own text, so the rest of the output
    keeps its shape. Object keys are texts too.
    """
    if isinstance(node, str):
        screening = screen(node, provenance, policy=policy, store=store)
        if screening.action == ACTION_PASS:
            return node
        found.append(screening)
        return screening.text
    if isinstance(node, (list, tuple)):
        return [_clean_tree(item, provenance, policy=policy, store=store, found=found) for item in node]
    if isinstance(node, Mapping):
        cleaned: dict[Any, Any] = {}
        for key, value in node.items():
            name = _clean_tree(key, provenance, policy=policy, store=store, found=found)
            while name in cleaned:          # two quarantined keys never take the same place
                name = f"{name}'"
            cleaned[name] = _clean_tree(value, provenance, policy=policy, store=store, found=found)
        return cleaned
    return node


def _screen_text(text: str, provenance: Provenance, *, policy: DetectionPolicy,
                 store: BlobSink | None) -> tuple[str, list[Screening]]:
    """``text`` screened: as a tree of texts when it is a JSON object or list, else as one text.

    Returns the text for the prompt (the same string when nothing was found) and the screenings that did not pass.
    """
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        data = None
    if isinstance(data, (dict, list)):
        found: list[Screening] = []
        try:
            cleaned = _clean_tree(data, provenance, policy=policy, store=store, found=found)
        except RecursionError:
            found = []                      # too deep to walk: screen it as one text below
        else:
            return (output_text(cleaned), found) if found else (text, [])
    screening = screen(text, provenance, policy=policy, store=store)
    return (screening.text, [screening]) if screening.action != ACTION_PASS else (text, [])


def _screening_event(screenings: Sequence[Screening], *, original_sha256: str) -> tuple[str, dict[str, Any]] | None:
    """One ``(event_type, payload)`` for all the screenings of one context part, or ``None`` when none applies.

    The payload is the ``multiagent.document_screening/1`` of BDI-T01 with the signals and fragments of every
    screening; ``original_sha256`` is the hash of the whole text of the part before it was screened.
    """
    events = [event for event in (item.event() for item in screenings) if event is not None]
    if not events:
        return None
    paused = any(event_type == EVENT_REVIEW_REQUESTED for event_type, _ in events)
    signals = [signal for _, payload in events for signal in payload["signals"]]
    fragments = [fragment for _, payload in events for fragment in payload["fragments"]]
    severities = [payload["max_severity"] for _, payload in events if payload["max_severity"]]
    payload = dict(events[0][1])
    payload.update({
        "action": ACTION_PAUSE if paused else ACTION_QUARANTINE, "original_sha256": original_sha256,
        "max_severity": max(severities, key=_SEVERITY_ORDER.__getitem__) if severities else None,
        "signals": signals[:MAX_EVENT_SIGNALS], "fragments": fragments[:MAX_EVENT_FRAGMENTS],
        "fragment_count": len(fragments),
    })
    return (EVENT_REVIEW_REQUESTED if paused else EVENT_QUARANTINED), payload


@dataclass(frozen=True)
class _ScreenedContext(StepContext):
    """The context a prompt is built from (the untrusted parts screened and quoted) and, in ``original``, the
    plain one: the step is recorded with the plain values, the model reads the quoted ones."""

    original: StepContext = StepContext(())


class WorkflowEngine:
    """Deterministic workflow orchestrator.

    The workflow defines step order, agent, prompt, and contract. Execution can
    be local, cloud, auto, or human_guided without changing inter-agent contracts.

    Untrusted sources (BDI-T02), with a document policy active (``document_policy`` is not ``None``): in the
    context of a step only the run inputs and the human revision feedback are the user's words. What a model
    wrote (the output of an earlier step, the previous attempt, the CMP context summary derived from outputs)
    is data: ``_context_for_step`` screens it with the detector of ``document_trust`` and quotes it in the
    untrusted-data envelope. Without a policy (the default, ``DEFAULT_DOCUMENT_POLICY``) the context and the
    prompts are what they always were. The barrier covers what the framework puts in a prompt; it does not
    cover what an executor application reads from the disk by itself.
    """

    def __init__(self, *, store: Store, catalog: Catalog, prompts: PromptManager,
                 workflows: WorkflowCatalog, router: ModelRouter,
                 artifacts: ArtifactWriter, settings,
                 output_schemas: Mapping[str, type[BaseModel]] | None = None,
                 document_policy: str | DetectionPolicy | None = DEFAULT_DOCUMENT_POLICY,
                 approval_timeout_seconds: float | None = None,
                 step_cache: StepCachePolicy | None = None,
                 tool_registry: ToolRegistry | None = None,
                 tool_selection: Mapping[str, Any] | SelectionConfig | None = None):
        self.store = store
        # Tools a step may declare (``WorkflowStep.tools``). ``None``: a step that declares tools fails before any call.
        self.tool_registry = tool_registry
        self.tool_selection: SelectionConfig = (tool_selection if isinstance(tool_selection, SelectionConfig)
                                                else load_selection_config(tool_selection))
        self.catalog = catalog
        self.prompts = prompts
        self.workflows = workflows
        self.router = router
        self.artifacts = artifacts
        self.settings = settings
        if approval_timeout_seconds is not None and (
                isinstance(approval_timeout_seconds, bool) or not math.isfinite(approval_timeout_seconds)
                or approval_timeout_seconds <= 0):
            raise ValueError("approval_timeout_seconds must be finite and positive")
        # Opt in: legacy checkpoints keep their review reason when no deadline policy is configured.
        self.approval_timeout_seconds = approval_timeout_seconds
        self.output_schemas = dict(OUTPUT_SCHEMAS if output_schemas is None else output_schemas)
        self.usage_recorder = UsageRecorder(store)
        # OpenTelemetry (CAP-T21): ``None`` unless ``telemetry_otel_enabled`` is on and the ``otel`` extra is installed.
        # The spans follow the run events and usage records below; they never change them.
        self.telemetry = build_telemetry(settings)
        if self.telemetry is not None:
            store.db.event_listeners.append(self.telemetry.on_event)
            self.usage_recorder.observers.append(self.telemetry.on_usage)
        self._envelope_store: EnvelopeStore | None = None
        # Step result cache (CAP-T13): off unless a policy with ``enabled=True`` is given; then only the steps it allows.
        self.step_cache_policy: StepCachePolicy = step_cache if step_cache is not None else StepCachePolicy()
        # The document barrier of the context of a step (BDI-T02). ``None``: off, nothing is screened, quoted or
        # recorded. A policy ("quarantine_warn" replaces a suspicious fragment, keeps the original, records an
        # event and goes on; "pause" also asks for a review) turns it on. ``ValueError`` if unknown.
        self.document_policy: DetectionPolicy | None = (
            None if document_policy is None else resolve_policy(document_policy))
        # Where the availability policy comes from (AVA-T03). ``ApplicationServices`` sets it to ``inbox.policy`` so that the
        # inbox that accepts a message and the guard that reads it resolve the policy in the same place (an override on the
        # inbox or the executors included); ``None`` reads the ``availability_*`` settings, as a bare engine always did.
        self.availability_source = None
        # The usage service the budget guard reads (USO-T04, ``UsageServices.attach``): it answers ``notify_budgets``. ``None`` (a
        # bare engine) with a budget configured counts as "cannot read the budgets": in cut mode no call is made.
        self.budgets_source = None

    @classmethod
    def from_bundle(cls, bundle: DefinitionBundle, *, store: Store,
                    artifacts: ArtifactWriter, settings, adapters: Mapping[str, Any],
                    dry_run: bool = False,
                    document_policy: str | DetectionPolicy | None = DEFAULT_DOCUMENT_POLICY,
                    approval_timeout_seconds: float | None = None,
                    tool_registry: ToolRegistry | None = None) -> WorkflowEngine:
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
            settings=settings, output_schemas=bundle.output_schemas, document_policy=document_policy,
            approval_timeout_seconds=approval_timeout_seconds, tool_registry=tool_registry,
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
    def request_approval(self, run_id: str, *, deadline: datetime) -> dict[str, Any]:
        """Arm a durable deadline on the current artifact review, without approving it.

        Existing checkpoints remain compatible; applications may opt into a default timeout in the
        engine constructor or arm one review explicitly. A repeated request keeps the original deadline.
        """
        now = datetime.fromisoformat(self._now())
        if deadline.tzinfo is None or deadline.utcoffset() is None or deadline <= now:
            raise ValueError("An approval deadline must be a future timezone-aware datetime")
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.WAITING_HUMAN.value or run["waiting_reason"] not in {
                WaitingReason.REVIEW.value, WaitingReason.APPROVAL_PENDING.value}:
            raise InvalidStateTransition("Only an artifact review can wait for approval")
        _, artifact = self.review_point(run)
        self.store.begin_approval_wait(run, artifact, since=now.isoformat(),
                                       deadline=deadline.astimezone(timezone.utc).isoformat())
        return self.store.get_run(run_id)

    def check_approval_deadlines(self, *, run_id: str | None = None) -> int:
        """Emit one overdue notice per current wait; never resolve, approve or resume a run."""
        return self.store.check_approval_deadlines(now=self._now(), run_id=run_id)

    def _arm_checkpoint_approval(self, run_id: str) -> None:
        if self.approval_timeout_seconds is not None:
            try:
                self.request_approval(run_id, deadline=datetime.fromisoformat(self._now()) +
                                      timedelta(seconds=self.approval_timeout_seconds))
            except InvalidStateTransition:
                # A human can settle the checkpoint before its optional deadline is armed.
                # Preserve that decision, including a run already queued for the next step.
                return

    def process_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.QUEUED.value:
            self.check_approval_deadlines(run_id=run_id)
            return self.store.get_run(run_id)
        if self.telemetry is None:
            return self._process_queued_run(run_id, run)
        self.telemetry.begin_run(run_id, str(run.get("workflow_id", "")))
        final: dict[str, Any] = {}
        try:
            final = self._process_queued_run(run_id, run)
            return final
        finally:
            self.telemetry.end_run(run_id, str(final.get("status") or "unknown"))

    def _process_queued_run(self, run_id: str, run: dict[str, Any]) -> dict[str, Any]:

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
                # Context compaction (CMP-T04) is off unless the workflow has an enabled context profile; a dry
                # run has no real model or window, so it is never measured or compacted.
                context_settings = (None if self.router.dry_run
                                    else context_settings_for(self.catalog, run["workflow_id"]))
                compaction = self._active_compaction(run_id, fresh.get("events", []), context_settings)
                step_context = self._context_for_step(
                    run_id, step.id, request, fresh.get("revision_feedback", ""),
                    brand_version=fresh.get("brand_version"),
                    prior_step_ids=[item.id for item in definition.steps[:index]],
                    compaction=compaction, attempt=attempt,
                    project_memory=self._project_memory_for(definition, step, run),
                )
                context = step_context.variables()                  # fills the template: untrusted parts quoted
                recorded_context = step_context.original.variables()  # what the step is recorded with: plain values
                # Tools the step declares run before its model call; a call that needs a person pauses here.
                tool_sections = self._run_step_tools(run_id=run_id, step=step, attempt=attempt, request=request)
                if tool_sections is None:
                    return self._finalize_return(run_id)
                prompt = self.prompts.render(step.prompt_id, step.prompt_version, **context)
                skill_text = "\n\n".join(self.prompts.skill_text(skill) for skill in step.skills)
                # Stable prefix first (skills, output schema), then the variable part (the rendered step prompt with its
                # input, prior artifacts and feedback, then the context sections), then a short fixed tail, so that
                # untrusted content is always followed by trusted text. Providers cache the leading bytes.
                final_prompt = self.prompts.assemble(
                    self.prompts.stable_prefix(skill_text, self._output_schema_block(output_schema)),
                    prompt.text, [*step_context.sections(), *tool_sections], OUTPUT_CONTRACT_REMINDER)
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

                preference = request.get("routing_preferences", {}).get(step.id, {})
                profile_id, profile = resolve_profile(self.catalog, run["workflow_id"], step.id, step.kind, preference)
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
                    **({"context_summary": {"ref": compaction.summary_ref, "replaced": list(compaction.step_ids)}}
                       if compaction is not None and step_context.summary_ref is not None else {}),
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
                        context=recorded_context,
                        final_prompt=final_prompt,
                        prompt_sha256=prompt_sha256,
                        requires_web=requires_web,
                    )

                assert binding is not None and requirements is not None

                profile_models = ([binding.preferred_model] + ([binding.fallback_model] if binding.fallback_model else [])) if profile_id is not None else []
                next_profile_model = profile_models[0] if profile_models else None

                def reserve_llm_call(call: CallContext) -> None:
                    """Book the call; the chain runs this only after every guard let the call through."""
                    if profile_id is not None:
                        self.store.db.log_event(run_id, "routing_dispatch_reserved", {
                            "profile_id": profile_id,
                            "step_id": step.id,
                            "attempted_model": next_profile_model,
                            "scope": {"run_id": run_id, "profile_id": profile_id},
                        })
                    self.store.increment_llm_calls(run_id)

                # The checks before every model call, in the fixed order of call_guards.py: cancellation, the
                # context estimate (only with an enabled context profile), the content review (only with the
                # document policy ``pause``, BDI-T04), the limits of this call, the budget guards that plug in
                # there, and last the reservation. A dry run has no real model or window, so it is not measured.
                egress_policy = step.egress or {"action": self.settings.egress_default}
                guard_env = GuardEnvironment(
                    scope=SCOPE_RUN_STEP, catalog=self.catalog,
                    routing_profile_id=profile_id, routing_profile=profile,
                    context_settings=context_settings, document_policy=self.document_policy,
                    availability=self._availability_policy(), budgets=self._budget_policy(),
                    egress=egress_policy,
                    emit=lambda event_type, payload: self.store.db.log_event(run_id, event_type, payload),
                )
                call_guards = build_call_guards(
                    guard_env,
                    cancel=lambda call: self._check_cancel(run_id),
                    reserve=reserve_llm_call,
                )

                # Step result cache (CAP-T13): off by default. A hit never skips a guard: the same chain that runs
                # before a model call (inbox, content review, egress, budget, limits; without the reservation) runs
                # before the saved result is used, so a call that a guard would stop or pause behaves the same with
                # the cache. Steps that wait for a person (checkpoint), that need the web or that depend on the
                # request's own web flag are never cached. Redacting egress is also excluded: checking a candidate
                # would emit a redaction event before falling through to the real call, duplicating the event.
                # A hit completes the step without calling the model; it
                # counts as no call and no tokens. Any cache failure falls through to the normal model call.
                cache_key: str | None = None
                if (self.step_cache_policy.allows(step.id) and not step.checkpoint_after and not requires_web
                        and egress_policy.get("action") != "redact"
                        and not request.get("requires_web")):
                    cache_key = self._step_cache_key(
                        step=step, binding=binding, profile_id=profile_id, routing_decision=routing_decision,
                        effort=effective_effort, requires_web=requires_web, execution_mode=execution_mode,
                        output_schema=output_schema, prompt_sha256=prompt_sha256, inputs=recorded_context,
                        sensitive=bool(request.get("sensitive")), run=fresh, egress=egress_policy,
                        data_class=request.get("data_class"))
                    if cache_key is not None and self._try_step_cache_hit(
                            run_id=run_id, index=index, step=step, attempt=attempt, key=cache_key,
                            context=recorded_context, output_schema=output_schema, binding=binding,
                            requirements=requirements, final_prompt=final_prompt, estimated_input=estimated_input,
                            profile_id=profile_id, sensitive=bool(request.get("sensitive")),
                            guards=build_call_guards(guard_env, cancel=lambda call: self._check_cancel(run_id)),
                            requires_web=requires_web, revision_feedback=fresh.get("revision_feedback", "")):
                        self._check_cancel(run_id)
                        self.store.update_run(run_id, revision_feedback="")
                        self._compact_after_step(
                            run_id=run_id, definition=definition, index=index, step=step, attempt=attempt,
                            request=request, settings=context_settings, profile_id=profile_id, profile=profile,
                            brand_version=fresh.get("brand_version"),
                        )
                        index += 1
                        self.store.update_run(run_id, current_step=index)
                        continue

                last_outgoing_prompt = final_prompt

                def consume_llm_call(call: CallContext, *, skip: tuple[str, ...] = ()) -> None:
                    """``before_call`` of the router for this step. ``skip`` names guards to leave out (the
                    summarizer's own calls pass ``("context",)``, so they are not measured for compaction but
                    still count in the limits and budgets)."""
                    nonlocal last_outgoing_prompt
                    last_outgoing_prompt = call.outgoing_text if call.outgoing_text is not None else final_prompt
                    original_replace = call.replace_outgoing

                    def remember_outgoing(value: str) -> None:
                        nonlocal last_outgoing_prompt
                        if original_replace is not None:
                            original_replace(value)
                        last_outgoing_prompt = value

                    call = call.with_fields(
                        scope=SCOPE_RUN_STEP, run_id=run_id, step_id=step.id, step_kind=step.kind, attempt=attempt,
                        profile_id=profile_id,
                        data_class="sensitive" if request.get("sensitive") else "non_sensitive",
                        replace_outgoing=remember_outgoing if original_replace is not None else None,
                    )
                    verdict = call_guards.check(call, self.store.get_run(run_id), skip=skip)
                    self._enforce_call_verdict(verdict, call=call, run_id=run_id, index=index, step=step,
                                               attempt=attempt)

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
                if self.telemetry is not None:
                    self.telemetry.hold_content(run_id, step.id, attempt, last_outgoing_prompt, response.raw_text)
                self._persist_completed_step(
                    run_id=run_id,
                    index=index,
                    step=step,
                    attempt=attempt,
                    context=recorded_context,
                    data=data,
                    model_id=response.model,
                    prompt_sha256=prompt_sha256,
                    response=response,
                    attempts=attempts,
                    fresh=fresh,
                    escalation=escalation_info,
                )
                if cache_key is not None and (escalation_info is not None or carried_attempts):
                    # An escalation changed the binding and the effort: the saved output belongs to the final
                    # ones, never to the key of the original binding.
                    cache_key = self._step_cache_key(
                        step=step, binding=binding, profile_id=profile_id, routing_decision=routing_decision,
                        effort=effective_effort, requires_web=requires_web, execution_mode=execution_mode,
                        output_schema=output_schema, prompt_sha256=prompt_sha256, inputs=recorded_context,
                        sensitive=bool(request.get("sensitive")), run=fresh, egress=egress_policy,
                        data_class=request.get("data_class"))
                if cache_key is not None:
                    self._step_cache().store(
                        cache_key, kind=KIND_MODEL,
                        result={"data": data, "model": response.model, "provider": response.provider},
                        emit=lambda kind, payload: self.store.db.log_event(run_id, kind, payload),
                        step_id=step.id, attempt=attempt)
                self._check_cancel(run_id)

                self.store.update_run(run_id, revision_feedback="")
                # The step finished well: if it crossed the context threshold, compact what is old before the
                # next step (or the review) starts. Never in the middle of a step (contract CMP-T01 section 8).
                self._compact_after_step(
                    run_id=run_id, definition=definition, index=index, step=step, attempt=attempt,
                    request=request, settings=context_settings, profile_id=profile_id, profile=profile,
                    brand_version=fresh.get("brand_version"),
                )
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
                    self._arm_checkpoint_approval(run_id)
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
        except CallPaused:
            # A call guard asked for a human before the call (the run is already WAITING_HUMAN, see
            # _enforce_call_verdict); nothing else to record.
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
            self._arm_checkpoint_approval(run_id)
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
        preference = request.get("routing_preferences", {}).get(step.id, {})
        profile_required = profile.get("required_permissions", []) if isinstance(profile, dict) else []
        caller_grants = request.get("effective_permissions")
        if caller_grants is None:
            caller_grants = request.get("permissions")
        requested_model = preference.get("model") or (
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
            effort=preference.get("effort") if "effort" in preference else step.effort,
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
            **({"profile_override": profile} if preference else {}),
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
    def _enforce_call_verdict(self, verdict: Verdict, *, call: CallContext, run_id: str, index: int,
                              step: WorkflowStep, attempt: int) -> None:
        """The one place the verdict of the call guards becomes engine behaviour (contract CMP-T01 section 4).

        ``Proceed`` does nothing. ``Stop`` raises ``BudgetExceeded`` with the guard's message (its ``code`` and
        ``guard`` are kept on the exception), which ``process_run`` already turns into a failed run or a run
        waiting on its budget. ``Pause`` puts the step in ``WAITING_HUMAN`` with the guard's ``waiting_reason``,
        the way a supervised escalation waits, records ``call_paused`` with what the guard asked, and raises
        ``CallPaused`` to unwind the router; nothing was reserved, so no call is counted.
        """
        if isinstance(verdict, Stop):
            if verdict.code == STOP_USER_CANCEL:
                # A ``cancel`` message of the person (AVA-T03): the run ends as cancelled, not as over budget.
                raise _CancelledSignal()
            if verdict.code == "egress_blocked":
                from .contracts import EgressBlocked
                raise EgressBlocked(verdict.message)
            error = BudgetExceeded(verdict.message)
            error.code = verdict.code
            error.guard = verdict.guard
            raise error
        if isinstance(verdict, Pause):
            self.store.db.log_event(run_id, "call_paused", {
                "step_id": step.id,
                "attempt": attempt,
                "guard": verdict.guard,
                "waiting_reason": verdict.waiting_reason,
                "model_id": call.model_id,
                "ordinal": call.ordinal,
                "message": verdict.message,
                "request": dict(verdict.request),
            })
            self.store.update_run(
                run_id,
                status=RunStatus.WAITING_HUMAN.value,
                current_step=index,
                waiting_reason=verdict.waiting_reason,
                waiting_step=step.id,
                waiting_attempt=attempt,
            )
            raise CallPaused(verdict, call)

    @staticmethod
    def _tool_arguments(arguments: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
        """Literal arguments of a declared tool call; a string written exactly ``${name}`` takes the run input."""
        def fill(value: Any) -> Any:
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                name = value[2:-1]
                found = request.get(name)
                if found is None:
                    found = (request.get("inputs") or {}).get(name) if isinstance(request.get("inputs"), Mapping) else None
                if not isinstance(found, str):
                    raise ValidationFailed(f"Tool argument {value!r} names no text input of the run")
                return found
            if isinstance(value, Mapping):
                return {key: fill(item) for key, item in value.items()}
            if isinstance(value, list):
                return [fill(item) for item in value]
            return value
        return fill(dict(arguments))

    def _run_step_tools(self, *, run_id: str, step: WorkflowStep, attempt: int,
                        request: Mapping[str, Any]) -> list[str] | None:
        """Invoke the tools ``step`` declares through ``ToolInvoker`` (CAP-T02/T19/T12 wiring).

        Returns the prompt sections (each result quoted as untrusted data), or ``None`` when a call needs a person:
        the run is then ``waiting_human`` / ``tool_approval`` with the persisted permission request. Approving it
        (``RunService.approve(..., permission_request_id=..., person=True)``) queues the run; the same step and attempt
        run again, the store releases the one-use grant and the call goes through. Every call leaves its
        ``tool_call_*`` events in the run log. The workflow declaration is the grant of the tool's own permissions;
        impact rules (write, destructive) still require the per-call human approval.
        """
        if not step.tools:
            return []
        if self.tool_registry is None:
            raise ValidationFailed(f"Step {step.id!r} declares tools but no tool registry is configured")
        invoker = ToolInvoker(self.tool_registry,
                              event_sink=lambda kind, payload: self.store.db.log_event(run_id, kind, dict(payload)))
        data_class = "sensitive" if request.get("sensitive") else "non_sensitive"
        sections: list[str] = []
        for ordinal, entry in enumerate(step.tools):
            self._check_cancel(run_id)
            try:
                if "capability" in entry:
                    registered = select_tool(self.tool_registry, entry["capability"], self.tool_selection)
                else:
                    registered = self.tool_registry.lookup(entry["tool"], entry.get("version"))
            except (SelectionError, ToolLookupError) as exc:
                raise ValidationFailed(f"Step {step.id!r} tool {ordinal}: {getattr(exc, 'code', exc)}") from exc
            spec = registered.spec
            context = ToolCallContext(scope="run_step", run_id=run_id, step_id=step.id, attempt=attempt,
                                      ordinal=ordinal, data_class=data_class)
            tool_request = ToolRequest(request_id=request_id_for(context, ordinal), tool_id=spec.tool_id,
                                       tool_version=spec.version,
                                       arguments=self._tool_arguments(entry.get("arguments") or {}, request),
                                       context=context, origin=ToolOrigin(kind="workflow_step", ref=f"step:{step.id}"))
            grants = required_permissions(spec)
            evidence = None
            try:
                code, permission = check_call(spec, tool_request, grants=grants)
            except InvalidArguments:
                code, permission = None, None          # the invoker reports invalid arguments with its own events
            if code == ToolErrorCode.APPROVAL_REQUIRED.value and permission is not None:
                grant = self.store.take_tool_approval(permission)
                if grant is not None:
                    evidence = [grant]
            result = invoker.invoke(tool_request, grants=grants, evidence=evidence)
            if (not result.ok and result.error is not None and result.error.code == ToolErrorCode.APPROVAL_REQUIRED
                    and result.permission_request is not None):
                self.store.hold_tool_approval(result.permission_request)
                return None
            if not result.ok:
                code_value = result.error.code.value if result.error is not None else "unknown"
                raise ValidationFailed(f"Tool {spec.tool_id} failed in step {step.id!r}: {code_value}")
            sections.append(f"## Tool result: {spec.tool_id}\n" + render_result_for_prompt(result))
        return sections

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
            cache_read_tokens=response.metadata.get("cache_read_tokens"),
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

    # ---------- the context of a step: one assembly point (CMP-T04; the seam BDI-T02 works on) ----------
    def _context_for_step(self, run_id: str, step_id: str, request: dict[str, Any], revision_feedback: str,
                          *, brand_version: int | None, prior_step_ids: list[str] | None = None,
                          compaction: CompactionState | None = None, attempt: int | None = None,
                          project_memory: tuple[str, MemorySetting] | None = None) -> StepContext:
        """The context of one step as ordered parts (``StepContext``), the ONE place it is assembled.

        ``_context_parts`` gathers the parts; ``StepContext.variables()`` fills the prompt template and
        ``StepContext.sections()`` are appended to the prompt after the active skills (today only the context
        summary). ``compaction`` is the compaction in force (``_active_compaction``); without it the context
        is what it always was.

        With a document policy active (``self.document_policy`` is not ``None``; contract BDI-T01 section 8:
        without one the context, the prompts and their ``prompt_sha256`` do not change) the untrusted parts are
        treated here, between gathering and assembling (BDI-T02, sections 4 to 8), and nowhere else; what each
        part is says whether it is the user's or data:

        * ``input`` and ``revision_feedback``: the user's words (the run request, the human feedback). Kept.
        * ``step_output`` and ``previous_output``: written by a model, which may have read anything. Data
          (``model_output``): screened and quoted.
        * ``context_summary``: written by a model from those outputs. Data, derived from them
          (``context_summary``): screened and quoted. Its ``## Context summary`` heading stays outside.
        * a summarized output (``[resumido ...]``) or an empty part holds no content. Kept.
        * any other kind: not known to be the user's, so data (``unknown``). Fails closed.

        ``attempt`` identifies the step attempt in the events of a suspicious fragment (it is worked out from
        the store when omitted).
        """
        parts = self._context_parts(
            run_id, step_id, request, revision_feedback, brand_version=brand_version,
            prior_step_ids=prior_step_ids, compaction=compaction)
        if attempt is None and (project_memory is not None or self.document_policy is not None):
            attempt = self.store.latest_step_attempt(run_id, step_id) + 1
        if project_memory is not None:          # opt-in (S12.A1): the memory is data, quoted whatever the policy is
            parts = (*parts, *self._project_memory_parts(run_id, step_id, attempt, *project_memory))
        if self.document_policy is None:        # no policy profile active: the barrier is off, nothing changes
            return _ScreenedContext(parts, original=StepContext(parts))
        return _ScreenedContext(self._untrusted_parts_screened(run_id, step_id, attempt, parts, compaction),
                                original=StepContext(parts))

    @staticmethod
    def _project_memory_for(definition: WorkflowDefinition, step: WorkflowStep,
                            run: dict[str, Any]) -> tuple[str, MemorySetting] | None:
        """``(project_id, setting)`` when the workflow or the step opted in to the project memory, else ``None``."""
        setting = resolve_setting(getattr(definition, "project_memory", None), getattr(step, "project_memory", None))
        project_id = run.get("project_id")
        return (str(project_id), setting) if setting is not None and project_id else None

    def _project_memory_parts(self, run_id: str, step_id: str, attempt: int | None, project_id: str,
                              setting: MemorySetting) -> tuple[ContextPart, ...]:
        """The project memory as context sections: ``## Project memory`` (ours) and each entry quoted as data.

        Entries are chosen under ``setting.max_chars`` (``select_entries``); each is wrapped in the untrusted-data
        envelope with its provenance, and screened by the detector when a document policy is active. Nothing is
        added when the project has no entry that fits (the prompt is then what it was without the opt-in); when
        entries exist but none fits the cap, the event still records how many were left out.
        """
        selection = select_entries(MemoryStore(self.store.db).list(project_id), setting)
        if not selection.entries:
            if selection.omitted:
                self.store.db.log_event(run_id, "project_memory_included", {
                    "step_id": step_id, "attempt": attempt, "project_id": project_id, "entries": [],
                    "omitted": selection.omitted, "chars": 0, "max_chars": setting.max_chars})
            return ()
        sink = self._quarantine_sink() if self.document_policy is not None else None
        parts: list[ContextPart] = []
        for entry in selection.entries:
            text = entry_text(entry)
            name = f"project_memory_{entry['id']}"
            if sink is not None:
                original = ContextPart(name, text, KIND_PROJECT_MEMORY, PLACEMENT_SECTION, ref=f"memory:{entry['id']}",
                                       sha256=entry["sha256"])
                text, screenings = _screen_text(text, entry_provenance(entry), policy=self.document_policy, store=sink)
                if screenings:
                    self._record_screening(run_id, step_id, int(attempt or 1), original, screenings)
            parts.append(ContextPart(name, render_entry(entry, text), KIND_PROJECT_MEMORY, PLACEMENT_SECTION,
                                     ref=f"memory:{entry['id']}", sha256=entry["sha256"]))
        parts[0] = replace(parts[0], value=f"{MEMORY_HEADING}\n{parts[0].value}")
        if selection.omitted:
            parts[-1] = replace(parts[-1], value=f"{parts[-1].value}\n{omitted_note(selection.omitted)}")
        self.store.db.log_event(run_id, "project_memory_included", {
            "step_id": step_id, "attempt": attempt, "project_id": project_id,
            "entries": [entry["id"] for entry in selection.entries], "omitted": selection.omitted,
            "chars": selection.chars, "max_chars": setting.max_chars})
        return tuple(parts)

    @staticmethod
    def _part_provenance(part: ContextPart, compaction: CompactionState | None) -> Provenance | None:
        """Where a context part comes from, or ``None`` when it is the user's own (then it is not touched)."""
        if part.kind in (KIND_INPUT, KIND_REVISION_FEEDBACK, KIND_PROJECT_MEMORY):   # memory arrives quoted already
            return None
        try:
            if part.kind in (KIND_STEP_OUTPUT, KIND_PREVIOUS_OUTPUT):
                return Provenance(SourceKind.MODEL_OUTPUT.value, ref=part.ref, sha256=part.sha256, label=part.name)
            if part.kind == KIND_CONTEXT_SUMMARY:
                origins = tuple(Provenance(SourceKind.MODEL_OUTPUT.value, ref=item.ref, sha256=item.sha256)
                                for item in (compaction.replaced if compaction is not None else ()))
                return derive(SourceKind.CONTEXT_SUMMARY.value, *origins, ref=part.ref, sha256=part.sha256,
                              label=part.name)
            return Provenance(SourceKind.UNKNOWN.value, ref=part.ref, label=part.name)
        except ValueError:      # a reference or name the provenance cannot hold: still data, just anonymous
            return Provenance(SourceKind.UNKNOWN.value)

    def _untrusted_parts_screened(self, run_id: str, step_id: str, attempt: int, parts: Sequence[ContextPart],
                                  compaction: CompactionState | None) -> tuple[ContextPart, ...]:
        """The parts with every untrusted one screened and quoted as data; the order and the names do not change.

        Without a document policy the parts come back as they are.
        """
        if self.document_policy is None:
            return tuple(parts)
        guarded: list[ContextPart] = []
        sink = self._quarantine_sink()
        for part in parts:
            provenance = self._part_provenance(part, compaction)
            if (provenance is None or part.summarized or not isinstance(part.value, str)
                    or not part.value.strip()):
                guarded.append(part)
                continue
            text, screenings = self._screen_part(part, provenance, compaction, sink)
            if screenings:
                self._record_screening(run_id, step_id, attempt, part, screenings)
            if part.kind == KIND_CONTEXT_SUMMARY:
                heading, newline, body = text.partition("\n")
                if newline and heading == SUMMARY_HEADING:     # the heading is ours; only the body is quoted
                    guarded.append(replace(part, value=f"{heading}\n{render_for_prompt(body, provenance)}"))
                    continue
            guarded.append(replace(part, value=render_for_prompt(text, provenance)))
        return tuple(guarded)

    def _screen_part(self, part: ContextPart, provenance: Provenance, compaction: CompactionState | None,
                     sink: BlobSink) -> tuple[str, list[Screening]]:
        """The text of an untrusted part after the detector and the policy, and the screenings that acted."""
        if part.kind == KIND_CONTEXT_SUMMARY and compaction is not None:
            # The summary is JSON in a fence: screen its texts, then render it again the way CMP does.
            found: list[Screening] = []
            try:
                cleaned = _clean_tree(compaction.summary, provenance, policy=self.document_policy, store=sink,
                                      found=found)
                if not found:
                    return part.value, []
                return render_summary_for_prompt(cleaned, ref=compaction.summary_ref), found
            except (SummarySchemaError, RecursionError):
                pass            # screen the rendered text as one text instead
        return _screen_text(part.value, provenance, policy=self.document_policy, store=sink)

    def _quarantine_sink(self) -> BlobSink:
        """``BlobSink`` of ``document_trust.screen``: keeps the original of a quarantined fragment as a blob."""
        blobs = self._blob_store()

        def sink(content: bytes, media_type: str) -> str:
            with self.store.db.tx() as conn:
                row, _ = blobs.put_blob(conn, content, actor=DOCUMENT_ACTOR, media_type=media_type)
            return blob_ref(row["sha256"])

        return sink

    def _record_screening(self, run_id: str, step_id: str, attempt: int, part: ContextPart,
                          screenings: Sequence[Screening]) -> None:
        """One ``document_quarantined`` / ``document_review_requested`` event per (step attempt, part, content).

        The payload is ``Screening.event()`` (BDI-T01) plus ``step_id``, ``attempt``, ``part`` and ``part_kind``.
        The same content is not announced twice for the same step attempt (the context is built again when a run
        resumes), so a review that a person already settled is not asked for again. Only an event of the SAME type
        counts as the announcement: a ``document_quarantined`` recorded under ``quarantine_warn`` must not hide the
        ``document_review_requested`` that the policy ``pause`` owes the same attempt after a policy change (a run
        re-queued by the recovery, an approved escalation), or the content guard would let the call through.
        """
        original_sha256 = hashlib.sha256(str(part.value).encode("utf-8")).hexdigest()
        found = _screening_event(screenings, original_sha256=original_sha256)
        if found is None:
            return
        event_type, payload = found
        payload.update({"step_id": step_id, "attempt": attempt, "part": part.name, "part_kind": part.kind})
        for event in self.store.get_run(run_id).get("events", []):
            known = event.get("payload") or {}
            if (event.get("event_type") == event_type
                    and (known.get("step_id"), known.get("attempt"), known.get("part"),
                         known.get("original_sha256")) == (step_id, attempt, part.name, original_sha256)):
                return
        self.store.db.log_event(run_id, event_type, payload)

    def _context_parts(self, run_id: str, step_id: str, request: dict[str, Any], revision_feedback: str,
                       *, brand_version: int | None, prior_step_ids: list[str] | None = None,
                       compaction: CompactionState | None = None) -> tuple[ContextPart, ...]:
        if "workflow_id" in request:
            return build_context_parts(
                inputs=request["inputs"],
                prior_outputs=[(prior_id, self._output_record(run_id, prior_id)) for prior_id in prior_step_ids or []],
                previous_output=self.store.latest_step_output(run_id, step_id),
                revision_feedback=revision_feedback, state=compaction)
        # The legacy social-post path: fixed variables, never compacted.
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
        variables = {
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
        kinds = {"research_context": KIND_STEP_OUTPUT, "strategy_context": KIND_STEP_OUTPUT,
                 "content_context": KIND_STEP_OUTPUT, "previous_output": KIND_PREVIOUS_OUTPUT,
                 "revision_feedback": KIND_REVISION_FEEDBACK}
        return tuple(ContextPart(name, value, kinds.get(name, KIND_INPUT)) for name, value in variables.items())

    # ---------- step result cache (CAP-T13) ----------
    def _step_cache(self) -> StepCache:
        return StepCache(self._blob_store(), self.store.db)

    def _step_cache_key(self, *, step: WorkflowStep, binding: ModelBinding, profile_id: str | None,
                        routing_decision: Any, effort: str | None, requires_web: bool, execution_mode: ExecutionMode,
                        output_schema: type[BaseModel], prompt_sha256: str, inputs: Mapping[str, Any],
                        sensitive: bool, run: Mapping[str, Any], egress: Mapping[str, Any],
                        data_class: str | None = None) -> str | None:
        """Key of a model step: everything that changes the output. ``None`` (no caching) if it cannot be built."""
        try:
            def catalog_spec(model_id: str | None) -> str | None:
                spec = self.catalog.models.get(str(model_id)) if model_id else None
                return envelope_sha256(_json_safe(dataclasses.asdict(spec))) if spec is not None else None

            policy = self.document_policy
            return model_step_key({
                "code_version": STEP_CACHE_CODE_VERSION,
                "scope": {"project_id": run.get("project_id"), "workflow_id": run.get("workflow_id"),
                          "data_class": "sensitive" if sensitive else str(data_class or "non_sensitive")},
                "step": {"id": step.id, "agent": step.agent, "kind": step.kind, "contract": step.contract,
                         "prompt_id": step.prompt_id, "prompt_version": step.prompt_version,
                         "skills": list(step.skills), "definition_sha256": envelope_sha256(
                             {name: getattr(step, name) for name in step.__dataclass_fields__})},
                "model": {"preferred": binding.preferred_model, "fallback": binding.fallback_model,
                          "preferred_spec_sha256": catalog_spec(binding.preferred_model),
                          "fallback_spec_sha256": catalog_spec(binding.fallback_model),
                          "max_retries": binding.max_retries, "fallback_max_retries": binding.fallback_max_retries,
                          "profile_id": profile_id,
                          "requested": getattr(routing_decision, "requested_model", None),
                          "dry_run": bool(self.router.dry_run)},
                "params": {"effort": effort, "requires_web": requires_web, "execution_mode": execution_mode.value,
                           "sensitive": sensitive,
                           "egress": dict(egress),
                           "document_policy": None if policy is None else str(getattr(policy, "value", policy)),
                           "output_schema_sha256": envelope_sha256(output_schema.model_json_schema())},
                "prompt_sha256": prompt_sha256,
                "inputs_sha256": envelope_sha256(inputs),
            })
        except Exception:
            _logger.warning("Could not build the step cache key; running the step", exc_info=True)
            return None

    def _try_step_cache_hit(self, *, run_id: str, index: int, step: WorkflowStep, attempt: int, key: str,
                            context: dict[str, Any], output_schema: type[BaseModel], binding: ModelBinding,
                            requirements: Any, final_prompt: str, estimated_input: int, profile_id: str | None,
                            sensitive: bool, guards: Any, requires_web: bool, revision_feedback: str) -> bool:
        """Complete the step from a saved result; ``False`` (nothing recorded) when there is none or it is unusable.

        With a saved entry, its output is validated first, then the call guards run as before a model call; a ``Stop`` or ``Pause``
        is enforced exactly as then, and a guard that rewrites the outgoing text (egress ``redact``) means no hit.
        The saved output then goes through the output validation of a new response (contract and semantic).
        """
        found = self._step_cache().lookup(key, kind=KIND_MODEL,
                                          emit=lambda kind, payload: self.store.db.log_event(run_id, kind, payload))
        if found is None:
            return False
        result, ref = found
        try:
            if self.router.dry_run:
                model_id = "fixture"
                spec = ModelSpec(id="fixture", provider="fake", model="fixture", tier=requirements.tier.value,
                                 structured_output=True, web=True)
            else:
                model_id = binding.preferred_model
                spec = self.catalog.model(model_id)
        except Exception:
            return False
        try:
            parsed = output_schema.model_validate(result["data"])
            self._prepare_and_semantic_validate(step_id=step.id, parsed=parsed, requires_web=requires_web,
                                                cited_sources=[], revision_feedback=revision_feedback)
            data = parsed.model_dump(mode="json")
        except Exception:
            return False
        outgoing = [final_prompt]
        call = CallContext(model_id=model_id, spec=spec, retry=0, ordinal=1, estimated_input_tokens=estimated_input,
                           outgoing_text=final_prompt,
                           replace_outgoing=lambda value: outgoing.__setitem__(0, value)).with_fields(
            scope=SCOPE_RUN_STEP, run_id=run_id, step_id=step.id, step_kind=step.kind, attempt=attempt,
            profile_id=profile_id, data_class="sensitive" if sensitive else "non_sensitive")
        verdict = guards.check(call, self.store.get_run(run_id))
        self._enforce_call_verdict(verdict, call=call, run_id=run_id, index=index, step=step, attempt=attempt)
        if outgoing[0] != final_prompt:
            return False
        self.store.db.log_event(run_id, EVENT_HIT, {
            "step_id": step.id, "attempt": attempt, "key": key, "blob_ref": ref, "kind": KIND_MODEL,
            "usage": dict(ZERO_USAGE)})
        self.store.record_step(run_id=run_id, step_id=step.id, attempt=attempt, status="completed",
                               input_data=context, output_data=data, model_id=str(result.get("model") or ""),
                               prompt_id=step.prompt_id, prompt_version=step.prompt_version)
        path = self.artifacts.write(run_id=run_id, step_index=index, step_id=step.id, attempt=attempt, data=data)
        relative_path = path.relative_to(self.settings.data_dir)
        self.store.add_artifact(run_id=run_id, step_id=step.id, attempt=attempt, kind=step.contract,
                                path=str(relative_path), data=data)
        self.store.db.log_event(run_id, "model_selected", {
            "step_id": step.id, "provider": str(result.get("provider") or ""), "model": str(result.get("model") or ""),
            "model_digest": "", "attempts": [], "estimated_input_tokens": None, "thinking": None,
            "cached": True, "cache_key": key})
        self.store.db.log_event(run_id, "step_completed", {
            "step_id": step.id, "attempt": attempt, "artifact_path": str(relative_path), "cached": True})
        return True

    def _output_record(self, run_id: str, step_id: str) -> OutputRecord | None:
        """The latest completed output of a step with the attempt it came from, or None."""
        found = self.store.latest_completed_step(run_id, step_id)
        return OutputRecord(step_id, found[0], found[1]) if found is not None else None

    # ---------- context compaction of workflow steps (CMP-T04, contract CMP-T01 sections 5, 8 and 10) ----------
    def _blob_store(self) -> EnvelopeStore:
        if self._envelope_store is None:
            self._envelope_store = EnvelopeStore(self.store.db)
        return self._envelope_store

    def _read_summary_blob(self, sha256: str) -> Any:
        """The stored context summary (parsed JSON), or None when it is missing or unreadable."""
        try:
            return json.loads(self._blob_store().read_blob(sha256).decode("utf-8"))
        except (OSError, ValueError):
            return None

    def _active_compaction(self, run_id: str, events: list[dict[str, Any]],
                           settings: ContextSettings | None) -> CompactionState | None:
        """The compaction in force for the next prompt of a run, or None (then the originals are used).

        Only with an enabled context profile. The state is the latest ``context_compacted`` event, accepted while
        its summary is intact and every output it replaced still has the hash that was summarized.
        """
        if settings is None or not settings.enabled:
            return None
        return resolve_compaction_state(events, current=lambda step_id: self._output_record(run_id, step_id),
                                        read_summary=self._read_summary_blob)

    def _compact_after_step(self, *, run_id: str, definition: WorkflowDefinition, index: int, step: WorkflowStep,
                            attempt: int, request: dict[str, Any], settings: ContextSettings | None,
                            profile_id: str | None, profile: Any, brand_version: int | None) -> None:
        """Compact the older context after a step finished well and crossed the threshold.

        Runs between the step's persistence and its checkpoint or the next step, so it never happens in the
        middle of a step: retries, fallbacks and a whole attempt always see one prompt. It needs an enabled
        context profile, the generic workflow path, a ``context_warning`` of level ``threshold`` for this step
        attempt (``compaction_pending``) and something that reads the context afterwards (a next step, or a
        review that may ask for a revision). A cancellation propagates; anything else that goes wrong ends in a
        ``context_compaction_skipped`` event and the run goes on with the originals.
        """
        if settings is None or not settings.enabled or "workflow_id" not in request:
            return
        if index >= len(definition.steps) - 1 and not step.checkpoint_after:
            return
        if not compaction_pending(self.store.get_run(run_id).get("events", []), step_id=step.id, attempt=attempt):
            return
        ids = {"run_id": run_id, "step_id": step.id, "attempt": attempt, "index": index}
        try:
            self._compact_context(
                run_id=run_id, definition=definition, index=index, step=step, attempt=attempt, request=request,
                settings=settings, profile_id=profile_id, profile=profile, brand_version=brand_version)
        except (_CancelledSignal, CallPaused):
            raise
        except Exception as exc:
            _logger.warning("Context compaction after step %r failed; continuing with the originals", step.id,
                            exc_info=True)
            self._log_context_event(run_id, EVENT_CONTEXT_COMPACTION_SKIPPED, skipped_event(
                **ids, reason=REASON_INTERNAL_ERROR, detail=f"{type(exc).__name__}: {exc}"[:400]))

    def _compact_context(self, *, run_id: str, definition: WorkflowDefinition, index: int, step: WorkflowStep,
                         attempt: int, request: dict[str, Any], settings: ContextSettings, profile_id: str | None,
                         profile: Any, brand_version: int | None) -> None:
        run = self.store.get_run(run_id)
        ids = {"run_id": run_id, "step_id": step.id, "attempt": attempt, "index": index}
        prior = [(item.id, self._output_record(run_id, item.id)) for item in definition.steps[: index + 1]]
        outputs = [record for _, record in prior if record is not None]
        state = self._active_compaction(run_id, run.get("events", []), settings)
        plan = plan_compaction(outputs, inputs=request["inputs"], keep_recent=settings.keep_recent, state=state)
        policy = SummaryPolicy.from_profile(settings.summary)
        if not plan.sources:
            self._log_context_event(run_id, EVENT_CONTEXT_COMPACTION_SKIPPED,
                                    skipped_event(**ids, reason="no_reduction", detail=plan.detail))
            return
        if plan.tokens_before * policy.max_ratio < minimum_summary_tokens():
            self._log_context_event(run_id, EVENT_CONTEXT_COMPACTION_SKIPPED, skipped_event(
                **ids, reason="no_reduction",
                detail=f"the {plan.tokens_before} tokens that could be replaced cannot shrink to "
                       f"{policy.max_ratio:.0%} as the smallest valid summary needs {minimum_summary_tokens()}"))
            return
        mode = self._step_execution_mode(run, step).value
        result = summarize_context(
            router=self.router, sources=plan.sources, pinned=plan.pinned, previous=plan.previous,
            access=SummaryAccess.from_request(request, execution_mode=mode if mode in ("auto", "local", "cloud") else "auto"),
            policy=policy, scope=f"run:{run_id}", cursor={"kind": "step_index", "value": index},
            before_call=self._summary_before_call(
                run_id=run_id, step=step, attempt=attempt, profile_id=profile_id, profile=profile, request=request),
            on_call=lambda call: self.usage_recorder.record(TokenUsageRecord(
                run_id=run_id, step_id=step.id, attempt=attempt, brand_version=brand_version, **call.usage_fields())),
        )
        if not result.ok:
            self._log_context_event(run_id, EVENT_CONTEXT_COMPACTION_SKIPPED, skipped_event(
                **ids, reason=result.reason or "validation_failed", detail=result.detail, result=result))
            return
        # The pinned items must reach the next prompts untouched: check them in the very context those prompts get.
        applied = StepContext(build_context_parts(
            inputs=request["inputs"], prior_outputs=prior, previous_output=None, revision_feedback="",
            state=next_state(plan, result)))
        problems = check_pinned_in_context(plan, applied)
        if problems or applied.summary_ref is None:
            self._log_context_event(run_id, EVENT_CONTEXT_COMPACTION_SKIPPED, skipped_event(
                **ids, reason="validation_failed", result=result, issues=problems,
                detail="the pinned items would not reach the next prompts unchanged"))
            return
        self._store_compaction_blobs(plan, result)
        self._log_context_event(run_id, EVENT_CONTEXT_COMPACTED, compacted_event(
            **ids, keep_recent=settings.keep_recent, plan=plan, result=result))

    def _store_compaction_blobs(self, plan: CompactionPlan, result) -> None:
        """Keep the originals that are now replaced by a mark, and the summary, as blobs (content addressed)."""
        blobs = self._blob_store()
        with self.store.db.tx() as conn:
            for source in plan.sources:
                blobs.put_blob(conn, content_text(source.content).encode("utf-8"), actor=CONTEXT_AGENT_ID,
                               media_type=ORIGINAL_MEDIA_TYPE)
            row, _ = blobs.put_blob(conn, content_text(result.summary).encode("utf-8"), actor=CONTEXT_AGENT_ID,
                                    media_type=SUMMARY_MEDIA_TYPE)
        if row["sha256"] != result.summary_sha256:
            raise RuntimeError("The stored context summary does not have the hash of the validated summary")

    def _summary_before_call(self, *, run_id: str, step: WorkflowStep, attempt: int, profile_id: str | None,
                             profile: Any, request: dict[str, Any]):
        """``before_call`` of the summarizer's calls: limits, egress and budget, but not context.

        Its calls count in ``max_llm_calls`` and in the step's routing profile like any other call. A ``Stop``,
        or a ``Pause`` (nobody is waiting for a compaction), skips the compaction instead of
        failing or pausing the run; a cancellation is the host's and propagates.
        """
        def reserve(call: CallContext) -> None:
            if profile_id is not None:
                self.store.db.log_event(run_id, "routing_dispatch_reserved", {
                    "profile_id": profile_id, "step_id": step.id, "attempted_model": call.model_id,
                    "scope": {"run_id": run_id, "profile_id": profile_id}, "purpose": "context_summary",
                })
            self.store.increment_llm_calls(run_id)

        # No ``document_policy`` here on purpose: a content review belongs to the step whose prompt carries the
        # untrusted parts, and nobody waits for a compaction (a ``Pause`` would only skip it).
        # Egress still screens each outbound summary; an approval request skips this compaction.
        chain = build_call_guards(
            GuardEnvironment(scope=SCOPE_RUN_STEP, catalog=self.catalog, routing_profile_id=profile_id,
                             routing_profile=profile, budgets=self._budget_policy(),
                             egress=step.egress or {"action": self.settings.egress_default},
                             emit=lambda event_type, payload: self.store.db.log_event(run_id, event_type, payload)),
            cancel=lambda call: self._check_cancel(run_id), reserve=reserve)
        data_class = "sensitive" if request.get("sensitive") else "non_sensitive"

        def before_call(call: CallContext) -> None:
            call = call.with_fields(scope=SCOPE_RUN_STEP, run_id=run_id, step_id=step.id, step_kind="work",
                                    attempt=attempt, profile_id=profile_id, data_class=data_class)
            verdict = chain.check(call, self.store.get_run(run_id))
            if isinstance(verdict, Stop):
                if verdict.guard == "egress":
                    from .contracts import EgressBlocked
                    raise EgressBlocked(verdict.message)
                error = BudgetExceeded(verdict.message)
                error.code, error.guard = verdict.code, verdict.guard
                raise error
            if isinstance(verdict, Pause):
                if verdict.guard == "egress":
                    from .contracts import EgressBlocked
                    raise EgressBlocked(verdict.message or "Outbound content needs review")
                raise BudgetExceeded(verdict.message or f"{verdict.guard or 'A guard'} asked for a decision "
                                                         f"({verdict.waiting_reason}) before a context summary call")

        return before_call

    def _log_context_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.store.db.log_event(run_id, event_type, payload)

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
    def _output_schema_block(output_schema) -> str:
        schema = json.dumps(output_schema.model_json_schema(), ensure_ascii=False, indent=2)
        return "## OUTPUT SCHEMA\nThe object must satisfy this JSON Schema:\n" + schema

    def _availability_policy(self):
        """The availability policy when it is on, else ``None``: no inbox guard, nothing changes.

        It comes from ``availability_source`` (the inbox service, see ``__init__``) or, without one, from the settings. A
        policy that cannot be resolved (an invalid configuration) counts as off: the inbox refuses messages in that case too.
        """
        try:
            if self.availability_source is not None:
                policy = self.availability_source()
            else:
                from .services.availability import policy_from_settings    # imported here: the services import this module
                policy = policy_from_settings(self.settings)
        except Exception:  # noqa: BLE001 - an invalid configuration: the executors report it, the engine reads no inbox
            _logger.warning("The availability policy could not be resolved: the inbox is off", exc_info=True)
            return None
        return policy if policy.enabled else None

    def _budget_policy(self) -> BudgetPolicy | None:
        """How the budget guard reads the budgets (USO-T04), or ``None``: no budget configured (or a dry run, which spends
        nothing), so there is no guard and nothing changes."""
        return None if self.router.dry_run else policy_from_settings(self.settings, self.budgets_source)

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


def _json_safe(value: Any) -> Any:
    """``value`` with every set turned into a sorted list (deterministic); other non-JSON values still fail later."""
    if isinstance(value, Mapping):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


class _CancelledSignal(Exception):
    pass
