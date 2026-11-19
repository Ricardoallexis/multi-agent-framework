# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic mock execution backend (BKD-T03, roadmap S4.A3).

Executes validated workflow definitions using the shared ``Store`` and ``ArtifactWriter``
without calling any model provider, adapter or router. Model steps produce outputs from
``context.sample_outputs`` or minimal valid examples derived from the step's Pydantic
contract, recording zero-token, zero-cost usage rows through ``UsageRecorder``.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import types
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal, Mapping, Sequence, Union, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, ValidationError

from ..context.workflow import OutputRecord, StepContext, build_context_parts
from ..contracts import (
    OUTPUT_SCHEMAS,
    ExecutionMode,
    HumanStepSubmission,
    RunStatus,
    WaitingReason,
)
from ..db.database import utcnow
from ..errors import (
    BudgetExceeded,
    HumanSubmissionError,
    InvalidStateTransition,
    ValidationFailed,
)
from ..functions import FunctionStepError, execute_function_step
from ..observability.usage import TokenUsageRecord, UsageRecorder
from ..teams.step_roles import bind_step_roles, bound_team
from ..workflow_validation import (
    WorkflowDefinitionError,
    validate_resume_point,
    validate_workflow_definition,
)
from ..workflows import WorkflowDefinition, WorkflowStep, condition_is_true
from .base import BackendContext


class _CancelledSignal(Exception):
    """Internal control-flow signal when a run has ``cancel_requested`` set."""


# Copied from multiagent/workflow_engine.py (_extract_json_payload, lines 2150-2173)
# so mock.py never imports multiagent.workflow_engine.
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
                payload = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                raise HumanSubmissionError("The human response does not contain valid JSON") from exc
        else:
            raise HumanSubmissionError("The human response does not contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise HumanSubmissionError("The human response must be a JSON object")
    return payload


def _meta_value(metadata: Sequence[Any], attr: str) -> Any:
    for item in metadata:
        value = getattr(item, attr, None)
        if value is not None:
            return value
    return None


def _synthesize_str(metadata: Sequence[Any]) -> str:
    min_len = _meta_value(metadata, "min_length")
    max_len = _meta_value(metadata, "max_length")
    if min_len is not None and max_len is not None and min_len > max_len:
        raise ValueError(f"Impossible string length bounds: min_length={min_len} > max_length={max_len}")
    if max_len is not None and max_len == 0:
        return ""
    seed = "mock-sample-value"
    target = max(min_len or 0, len(seed))
    if max_len is not None:
        target = min(target, max_len)
    if target <= len(seed):
        return seed[:target]
    copies = (target // (len(seed) + 1)) + 2
    return ("-".join([seed] * copies))[:target]


def _synthesize_int(metadata: Sequence[Any]) -> int:
    gt = _meta_value(metadata, "gt")
    ge = _meta_value(metadata, "ge")
    lt = _meta_value(metadata, "lt")
    le = _meta_value(metadata, "le")
    multiple_of = _meta_value(metadata, "multiple_of")

    low: int | None = None
    if ge is not None:
        low = math.ceil(ge)
    if gt is not None:
        gt_low = math.floor(gt) + 1
        low = gt_low if low is None else max(low, gt_low)

    high: int | None = None
    if le is not None:
        high = math.floor(le)
    if lt is not None:
        lt_high = math.ceil(lt) - 1
        high = lt_high if high is None else min(high, lt_high)

    if low is not None and high is not None and low > high:
        raise ValueError(f"Impossible integer bounds: [{low}, {high}]")

    if multiple_of is not None:
        if multiple_of <= 0:
            raise ValueError(f"Impossible multiple_of constraint: {multiple_of}")
        if low is not None:
            cand = math.ceil(low / multiple_of) * multiple_of
        elif high is not None:
            cand = math.floor(high / multiple_of) * multiple_of
        else:
            cand = int(multiple_of) if float(multiple_of).is_integer() else 0
        if not float(cand).is_integer():
            raise ValueError(f"Cannot satisfy integer multiple_of={multiple_of}")
        value = int(cand)
        if (low is not None and value < low) or (high is not None and value > high):
            raise ValueError(f"No integer multiple of {multiple_of} fits in [{low}, {high}]")
        return value

    if (low is None or 1 >= low) and (high is None or 1 <= high):
        return 1
    return low if low is not None else int(high)


def _synthesize_float(metadata: Sequence[Any]) -> float:
    gt = _meta_value(metadata, "gt")
    ge = _meta_value(metadata, "ge")
    lt = _meta_value(metadata, "lt")
    le = _meta_value(metadata, "le")

    lower = ge if gt is None else (gt if ge is None else max(ge, gt))
    lower_strict = gt is not None and (ge is None or gt >= ge)
    upper = le if lt is None else (lt if le is None else min(le, lt))
    upper_strict = lt is not None and (le is None or lt <= le)

    if lower is not None and upper is not None:
        if lower > upper or (lower == upper and (lower_strict or upper_strict)):
            raise ValueError(f"Impossible float bounds: lower={lower}, upper={upper}")

    def fits(candidate: float) -> bool:
        if lower is not None and (candidate <= lower if lower_strict else candidate < lower):
            return False
        if upper is not None and (candidate >= upper if upper_strict else candidate > upper):
            return False
        return True

    for candidate in (1.0, 0.0):
        if fits(candidate):
            return candidate
    if lower is not None and upper is not None:
        return (float(lower) + float(upper)) / 2.0
    if lower is not None:
        return float(lower) if not lower_strict else float(lower) + 1.0
    return float(upper) if not upper_strict else float(upper) - 1.0


def _synthesize_value(
    annotation: Any,
    metadata: Sequence[Any] = (),
    *,
    seen: tuple[type[BaseModel], ...] = (),
) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Annotated:
        return _synthesize_value(args[0], (*metadata, *args[1:]), seen=seen)

    if origin is Union or origin is types.UnionType:
        non_none = [arg for arg in args if arg is not type(None)]
        last_error: Exception | None = None
        for option in non_none:
            try:
                return _synthesize_value(option, metadata, seen=seen)
            except Exception as exc:
                last_error = exc
        if type(None) in args:
            return None
        raise ValueError(f"Cannot derive sample for union {annotation!r}: {last_error}")

    if origin is Literal:
        if not args:
            raise ValueError("Empty Literal type")
        return args[0]

    if annotation is Any or annotation is object:
        return "mock"

    if annotation is type(None):
        return None

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        members = list(annotation)
        if not members:
            raise ValueError(f"Empty Enum {annotation.__name__}")
        return members[0].value

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _synthesize_model(annotation, seen=seen)

    if annotation is bool:
        return False
    if annotation is int:
        return _synthesize_int(metadata)
    if annotation is float:
        return _synthesize_float(metadata)
    if annotation is Decimal:
        return Decimal(str(_synthesize_float(metadata)))
    if annotation is str:
        return _synthesize_str(metadata)
    if annotation is datetime:
        return datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    if annotation is date:
        return "2026-01-01"
    if annotation is UUID:
        return "00000000-0000-4000-8000-000000000000"

    if origin in (list, Sequence, set, frozenset):
        min_len = int(_meta_value(metadata, "min_length") or 0)
        if min_len <= 0:
            return []
        elem_type = args[0] if args else Any
        return [_synthesize_value(elem_type, seen=seen) for _ in range(min_len)]

    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            min_len = int(_meta_value(metadata, "min_length") or 0)
            return [_synthesize_value(args[0], seen=seen) for _ in range(min_len)]
        if args == ((),):
            return []
        return [_synthesize_value(arg, seen=seen) for arg in args]

    if origin in (dict, Mapping):
        min_len = int(_meta_value(metadata, "min_length") or 0)
        if min_len <= 0:
            return {}
        key_type = args[0] if len(args) >= 1 else str
        val_type = args[1] if len(args) >= 2 else Any
        result: dict[Any, Any] = {}
        for idx in range(min_len):
            key = f"key_{idx}" if key_type is str else _synthesize_value(key_type, seen=seen)
            result[key] = _synthesize_value(val_type, seen=seen)
        return result

    raise ValueError(f"Unsupported field type {annotation!r}")


def _synthesize_model(
    model_cls: type[BaseModel],
    *,
    seen: tuple[type[BaseModel], ...] = (),
) -> dict[str, Any]:
    if model_cls in seen:
        raise ValueError(f"Recursive schema {model_cls.__name__} cannot be synthesized")
    next_seen = (*seen, model_cls)
    payload: dict[str, Any] = {}
    for name, field_info in model_cls.model_fields.items():
        if not field_info.is_required():
            continue
        key = field_info.validation_alias if isinstance(field_info.validation_alias, str) else (field_info.alias or name)
        payload[key] = _synthesize_value(field_info.annotation, field_info.metadata, seen=next_seen)
    return payload


class MockBackend:
    """Deterministic execution backend without providers, routing or tools."""

    name = "mock"
    capabilities = frozenset({"usage", "budgets", "cancellation", "human_steps", "review", "recovery", "external_output"})

    def __init__(self, context: BackendContext):
        self.store = context.store
        self.settings = context.settings
        self.workflows = context.workflows
        self.prompts = context.prompts
        self.catalog = context.catalog
        self.artifacts = context.artifacts
        self.output_schemas = dict(OUTPUT_SCHEMAS if context.output_schemas is None else context.output_schemas)
        self.sample_outputs = deepcopy(dict(context.sample_outputs or {}))
        self.usage_recorder = UsageRecorder(context.store)

    def preflight(self, workflow_id: str) -> WorkflowDefinition:
        """Load and validate ``workflow_id`` with the same validator as the native engine."""
        definition = self.workflows.load(workflow_id)
        validate_workflow_definition(
            definition,
            catalog=self.catalog,
            settings=self.settings,
            output_schemas=self.output_schemas,
            prompts=self.prompts,
        )
        return definition

    def process_run(self, run_id: str) -> dict[str, Any]:
        """Execute a queued run from ``current_step`` until it waits, completes, fails or is cancelled."""
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.QUEUED.value:
            self.check_approval_deadlines(run_id=run_id)
            return self.store.get_run(run_id)
        return self._process_queued_run(run_id, run)

    def _process_queued_run(self, run_id: str, run: dict[str, Any]) -> dict[str, Any]:
        base_active = float(run.get("active_seconds") or 0.0)
        started_mono = time.monotonic()
        try:
            self._check_cancel(run_id)
            try:
                definition = self.preflight(run["workflow_id"])
                # Roles of the team version bound to the run, exactly as the native engine applies them (S6.C1).
                definition, step_roles = bind_step_roles(
                    definition, bound_team(run.get("events")), catalog=self.catalog,
                    skills_dir=self.prompts.skills_dir if self.prompts is not None else None,
                )
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
                heartbeat_at=utcnow(),
            )
            self.store.db.log_event(run_id, "run_started", {})

            while index < len(definition.steps):
                self._check_cancel(run_id)
                self.store.heartbeat(run_id)

                step = definition.steps[index]
                step_role = step_roles.get(step.id)
                if step.function is None:
                    self._check_active_budget(run_id, base_active, started_mono)
                if not condition_is_true(step.when, request):
                    self.store.db.log_event(run_id, "step_skipped", {"step_id": step.id, "condition": step.when})
                    index += 1
                    self.store.update_run(run_id, current_step=index)
                    continue

                attempt = self.store.latest_step_attempt(run_id, step.id) + 1
                if attempt > definition.max_iterations + 1:
                    raise BudgetExceeded(f"Maximum revision count reached for step={step.id}")

                fresh = self.store.get_run(run_id)
                if step.function is not None:
                    execute_function_step(
                        store=self.store, artifacts=self.artifacts, settings=self.settings,
                        output_schemas=self.output_schemas, run_id=run_id, index=index,
                        step=step, attempt=attempt, request=request,
                        prior_step_ids=[item.id for item in definition.steps[:index]],
                    )
                    self.store.update_run(run_id, revision_feedback="")
                    if step.checkpoint_after:
                        self.store.update_run(
                            run_id, status=RunStatus.WAITING_HUMAN.value, current_step=index,
                            waiting_reason=WaitingReason.REVIEW.value, waiting_step=step.id, waiting_attempt=attempt,
                        )
                        self.store.db.log_event(run_id, "checkpoint", {"step_id": step.id, "attempt": attempt})
                        return self._finalize_return(run_id)
                    index += 1
                    self.store.update_run(run_id, current_step=index)
                    continue
                execution_mode = self._step_execution_mode(fresh, step)
                if execution_mode != ExecutionMode.HUMAN_GUIDED and int(fresh["llm_calls"]) >= int(fresh["max_llm_calls"]):
                    raise BudgetExceeded("max_llm_calls reached")

                context = self._step_context(
                    run_id=run_id,
                    definition=definition,
                    index=index,
                    step=step,
                    request=request,
                    revision_feedback=fresh.get("revision_feedback", ""),
                    brand_version=fresh.get("brand_version"),
                )
                prompt_text = self._render_prompt(step, context)
                if step_role is not None:   # the step's own role section, as the native engine adds it
                    prompt_text = f"{prompt_text}\n\n{step_role.section()}"
                prompt_sha256 = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

                self.store.db.log_event(run_id, "step_started", {
                    "step_id": step.id,
                    "attempt": attempt,
                    "preferred_model": "human_guided" if execution_mode == ExecutionMode.HUMAN_GUIDED else "mock",
                    "fallback_model": None,
                    "prompt_id": step.prompt_id,
                    "prompt_version": step.prompt_version,
                    "execution_mode": execution_mode.value,
                    "step_kind": step.kind,
                    "effort": step.effort,
                    **(step_role.fields() if step_role is not None else {}),
                })

                if execution_mode == ExecutionMode.HUMAN_GUIDED:
                    return self._pause_for_human(
                        run_id=run_id,
                        index=index,
                        step=step,
                        attempt=attempt,
                        context=context,
                        prompt_text=prompt_text,
                        prompt_sha256=prompt_sha256,
                    )

                self._check_cancel(run_id)
                output_schema = self.output_schemas.get(step.contract)
                if output_schema is None:
                    raise ValidationFailed(f"Unknown output contract {step.contract!r} for step {step.id!r}")

                self.store.increment_llm_calls(run_id)
                parsed = self._produce_step_output(step, output_schema)
                self.store.increment_successful_llm_calls(run_id)
                data = parsed.model_dump(mode="json")

                self._persist_completed_model_step(
                    run_id=run_id,
                    index=index,
                    step=step,
                    attempt=attempt,
                    context=context,
                    data=data,
                    prompt_sha256=prompt_sha256,
                    brand_version=fresh.get("brand_version"),
                )
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

            self.store.update_run(
                run_id,
                status=RunStatus.COMPLETED.value,
                waiting_reason="",
                waiting_step="",
                waiting_attempt=None,
            )
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
        except FunctionStepError as exc:
            self.store.fail_active_run(run_id, exc, issues=[{
                "code": exc.code, "location": "function", "step_id": step.id, "message": str(exc),
            }])
            return self._finalize_return(run_id)
        except Exception as exc:
            self.store.fail_active_run(run_id, exc)
            return self._finalize_return(run_id)
        finally:
            try:
                elapsed = max(0.0, time.monotonic() - started_mono)
                self.store.update_run(run_id, active_seconds=base_active + elapsed)
                self.artifacts.write_summary(self.store.get_run(run_id))
            except Exception:
                pass

    def _human_step_context(self, run_id: str):
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.WAITING_HUMAN.value or run.get("waiting_reason") not in {
            WaitingReason.EXECUTE_STEP.value,
            WaitingReason.RESEARCH.value,
        }:
            raise HumanSubmissionError("The run is not waiting for human execution of a step")
        pending = self.store.pending_human_step(run_id)
        if not pending:
            raise HumanSubmissionError("No pending HumanStepRequest exists")

        try:
            definition = self._validate_waiting_step(run, pending)
            definition, _ = bind_step_roles(definition, bound_team(run.get("events")))   # the agent a role gives a step
            if (
                run["cancel_requested"]
                or run["waiting_step"] != pending["step_id"]
                or run["waiting_attempt"] != pending["attempt"]
                or self.store.latest_step_attempt(run_id, pending["step_id"]) != pending["attempt"]
            ):
                raise InvalidStateTransition("The pending HumanStepRequest does not match the persisted wait")
        except (KeyError, WorkflowDefinitionError, InvalidStateTransition) as err:
            message = err.to_payload()["error"] if isinstance(err, WorkflowDefinitionError) else str(err)
            raise HumanSubmissionError(message) from err

        index = run["current_step"]
        step = definition.steps[index]
        output_schema = self.output_schemas[pending["expected_contract"]]
        return run, pending, index, step, output_schema

    def _validate_human_response(self, output_schema, raw):
        return output_schema.model_validate(_extract_json_payload(raw))

    def check_human_step(self, run_id: str, raw_response: str) -> dict[str, Any]:
        _, pending, _, _, output_schema = self._human_step_context(run_id)
        try:
            self._validate_human_response(output_schema, raw_response)
        except ValidationError as exc:
            problems = [{"path": ".".join(map(str, error["loc"])) or "$", "message": error["msg"]}
                        for error in exc.errors(include_url=False)]
        except (HumanSubmissionError, ValueError, TypeError) as exc:
            problems = [{"path": "$", "message": str(exc)}]
        else:
            problems = []
        return {"ok": not problems, "contract": pending["expected_contract"], "problems": problems}

    def submit_human_step(self, run_id: str, submission: HumanStepSubmission) -> dict[str, Any]:
        """Validate and store a person's output for the step the run waits on."""
        run, pending, index, step, output_schema = self._human_step_context(run_id)
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
            parsed = self._validate_human_response(output_schema, raw)
        except (HumanSubmissionError, ValidationError, ValueError, TypeError) as exc:
            self.store.db.log_event(run_id, "human_submission_invalid", {
                "step_id": step.id,
                "attempt": pending["attempt"],
                "error": str(exc),
                "raw_path": self._relative_path(raw_path),
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
        model_id = ("external:output" if submission.provider == "external_output"
                    else f"human:{submission.provider}:{submission.model or 'unspecified'}")
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
            provider="external" if submission.provider == "external_output" else "human",
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
        artifact_rel = self._relative_path(path)
        self.store.add_artifact(
            run_id=run_id,
            step_id=step.id,
            attempt=int(pending["attempt"]),
            kind=pending["expected_contract"],
            path=artifact_rel,
            data=data,
        )
        self.store.db.log_event(run_id, "human_step_submitted", {
            "step_id": step.id,
            "attempt": pending["attempt"],
            "provider": submission.provider,
            "model": submission.model,
            "provenance": "external_output" if submission.provider == "external_output" else "human_reported",
            "raw_path": self._relative_path(raw_path),
        })
        self.store.db.log_event(run_id, "step_completed", {
            "step_id": step.id,
            "attempt": pending["attempt"],
            "artifact_path": artifact_rel,
            "execution_mode": "human_guided",
        })
        self.store.update_run(run_id, revision_feedback="")

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

    def external_output(self, run_id: str, output: dict[str, Any], *, commit: bool = False):
        from ..external_output import external_output
        return external_output(self, run_id, output, commit=commit)

    def review_point(self, run: dict[str, Any]) -> tuple[WorkflowDefinition, dict[str, Any]]:
        """Resolve the workflow definition and artifact under review at a checkpoint."""
        try:
            definition = self.preflight(run["workflow_id"])
            budget = bool(run["budget_exhausted"]) or run["waiting_reason"] == WaitingReason.BUDGET_EXHAUSTED.value
            step_id, attempt = run["waiting_step"], run["waiting_attempt"]
            if step_id and type(attempt) is int and attempt > 0:
                artifact = self.store.review_artifact(run["id"], step_id=step_id, attempt=attempt)
            elif not step_id and attempt is None and budget:
                artifact = self.store.review_artifact(run["id"])
            elif (
                not step_id
                and attempt is None
                and not run["waiting_reason"]
                and run["current_step"] == len(definition.steps) - 1
            ):
                last = definition.steps[-1]
                artifact = self.store.review_artifact(run["id"], step_id=last.id)
            else:
                raise InvalidStateTransition("The review has incomplete step or attempt metadata")
            index = run["current_step"]
            if budget:
                index = next((i for i, step in enumerate(definition.steps) if step.id == artifact["step_id"]), -1)
            self._validate_waiting_step(run, artifact, definition=definition, current_step=index)
            if not budget:
                if artifact["approved"] or self.store.latest_step_attempt(run["id"], artifact["step_id"]) != artifact["attempt"]:
                    raise InvalidStateTransition("The review does not identify the current unapproved attempt")
            return definition, artifact
        except (KeyError, WorkflowDefinitionError) as err:
            message = err.to_payload()["error"] if isinstance(err, WorkflowDefinitionError) else str(err)
            raise InvalidStateTransition(message) from err

    def check_approval_deadlines(self, *, run_id: str | None = None) -> int:
        """MockBackend does not declare ``approval_deadlines``, so nothing expires."""
        return 0

    def _validate_waiting_step(
        self,
        run: dict[str, Any],
        saved: dict[str, Any],
        *,
        definition: WorkflowDefinition | None = None,
        current_step: int | None = None,
    ) -> WorkflowDefinition:
        definition = definition or self.preflight(run["workflow_id"])
        index = run["current_step"] if current_step is None else current_step
        validate_resume_point(
            definition,
            current_step=index,
            step_id=saved["step_id"],
            expected_contract=saved.get("expected_contract", saved.get("kind")),
        )
        step = definition.steps[index]
        if step.prompt_id != saved["prompt_id"] or step.prompt_version != saved["prompt_version"]:
            raise InvalidStateTransition("The paused step prompt or version changed")
        if not condition_is_true(step.when, run["request"]):
            raise InvalidStateTransition("The paused step is no longer enabled for this request")
        if type(saved["attempt"]) is not int or saved["attempt"] < 1:
            raise InvalidStateTransition("The paused step has an invalid attempt")
        return definition

    def _produce_step_output(self, step: WorkflowStep, output_schema: type[BaseModel]) -> BaseModel:
        if not (isinstance(output_schema, type) and issubclass(output_schema, BaseModel)):
            raise ValidationFailed(
                f"MockBackend requires a Pydantic BaseModel for contract {step.contract!r} (step {step.id!r})"
            )
        try:
            if step.contract in self.sample_outputs:
                payload = deepcopy(self.sample_outputs[step.contract])
            else:
                payload = _synthesize_model(output_schema)
            return output_schema.model_validate(payload)
        except (ValidationError, ValueError, TypeError) as exc:
            raise ValidationFailed(
                f"MockBackend could not produce a valid output for step {step.id!r} "
                f"(contract {step.contract!r}): {exc}"
            ) from exc

    def _persist_completed_model_step(
        self,
        *,
        run_id: str,
        index: int,
        step: WorkflowStep,
        attempt: int,
        context: dict[str, Any],
        data: dict[str, Any],
        prompt_sha256: str,
        brand_version: int | None,
    ) -> None:
        self.store.record_step(
            run_id=run_id,
            step_id=step.id,
            attempt=attempt,
            status="completed",
            input_data=context,
            output_data=data,
            model_id="mock",
            prompt_id=step.prompt_id,
            prompt_version=step.prompt_version,
        )
        path = self.artifacts.write(
            run_id=run_id,
            step_index=index,
            step_id=step.id,
            attempt=attempt,
            data=data,
        )
        relative_path = self._relative_path(path)
        self.store.add_artifact(
            run_id=run_id,
            step_id=step.id,
            attempt=attempt,
            kind=step.contract,
            path=relative_path,
            data=data,
        )
        self.usage_recorder.record(TokenUsageRecord(
            run_id=run_id,
            step_id=step.id,
            agent_id=step.agent,
            attempt=attempt,
            provider="mock",
            model="mock",
            model_digest="",
            prompt_id=step.prompt_id,
            prompt_version=step.prompt_version,
            prompt_sha256=prompt_sha256,
            brand_version=brand_version,
            tokens_in=0,
            tokens_out=0,
            num_ctx=None,
            duration_ms=0,
            estimated_cost_usd=0.0,
            success=True,
            error_type="",
        ))
        self.store.db.log_event(run_id, "step_completed", {
            "step_id": step.id,
            "attempt": attempt,
            "artifact_path": relative_path,
        })

    def _pause_for_human(
        self,
        *,
        run_id: str,
        index: int,
        step: WorkflowStep,
        attempt: int,
        context: dict[str, Any],
        prompt_text: str,
        prompt_sha256: str,
    ) -> dict[str, Any]:
        self.store.create_human_step_request(
            run_id=run_id,
            step_id=step.id,
            attempt=attempt,
            prompt_id=step.prompt_id,
            prompt_version=step.prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_base=prompt_text,
            expected_contract=step.contract,
            context=context,
        )
        request_path = self.artifacts.write_human_request(
            run_id=run_id,
            step_index=index,
            step_id=step.id,
            attempt=attempt,
            prompt=prompt_text,
            expected_contract=step.contract,
        )
        self.store.update_run(
            run_id,
            status=RunStatus.WAITING_HUMAN.value,
            waiting_reason=WaitingReason.EXECUTE_STEP.value,
            waiting_step=step.id,
            waiting_attempt=attempt,
        )
        self.store.db.log_event(run_id, "human_step_requested", {
            "step_id": step.id,
            "attempt": attempt,
            "expected_contract": step.contract,
            "prompt_path": self._relative_path(request_path),
        })
        return self._finalize_return(run_id)

    def _output_record(self, run_id: str, step_id: str) -> OutputRecord | None:
        latest = self.store.latest_completed_step(run_id, step_id)
        if latest is None:
            return None
        attempt_num, output = latest
        return OutputRecord(step_id=step_id, attempt=attempt_num, output=output)

    def _step_context(
        self,
        *,
        run_id: str,
        definition: WorkflowDefinition,
        index: int,
        step: WorkflowStep,
        request: dict[str, Any],
        revision_feedback: str,
        brand_version: int | None,
    ) -> dict[str, Any]:
        if "workflow_id" in request:
            prior_outputs = [
                (prior.id, self._output_record(run_id, prior.id))
                for prior in definition.steps[:index]
            ]
            parts = build_context_parts(
                inputs=request.get("inputs", {}),
                prior_outputs=prior_outputs,
                previous_output=self.store.latest_step_output(run_id, step.id),
                revision_feedback=revision_feedback,
            )
            return StepContext(parts).variables()

        brand_sections = ["core", "voice"]
        if step.id == "design":
            brand_sections = ["core", "voice", "visual"]
        elif step.id == "strategy":
            brand_sections = ["core", "voice", "strategy"]
        brand = (
            self.store.brand_context(brand_sections, version=brand_version)
            if request.get("use_brand_context", True) and brand_version is not None
            else ""
        )
        research = self.store.latest_step_output(run_id, "research") or {}
        strategy = self.store.latest_step_output(run_id, "strategy") or {}
        content = self.store.latest_step_output(run_id, "create") or {}
        previous = self.store.latest_step_output(run_id, step.id) or {}
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

    def _render_prompt(self, step: WorkflowStep, context: dict[str, Any]) -> str:
        try:
            if self.prompts is not None:
                return self.prompts.render(step.prompt_id, step.prompt_version, **context).text
        except Exception:
            pass
        return f"Step {step.id} (contract {step.contract})"

    @staticmethod
    def _step_execution_mode(run: dict[str, Any], step: WorkflowStep) -> ExecutionMode:
        overrides = run.get("step_modes") or {}
        if step.id in overrides:
            return ExecutionMode(overrides[step.id])
        return ExecutionMode(run.get("execution_mode") or ExecutionMode.AUTO.value)

    def _check_cancel(self, run_id: str) -> None:
        if self.store.get_run(run_id)["cancel_requested"]:
            raise _CancelledSignal()

    def _check_active_budget(self, run_id: str, base_active: float, started_mono: float) -> None:
        fresh = self.store.get_run(run_id)
        if base_active + (time.monotonic() - started_mono) > float(fresh["max_run_seconds"]):
            raise BudgetExceeded("max_run_seconds reached")

    def _relative_path(self, path) -> str:
        try:
            return str(path.relative_to(self.settings.data_dir))
        except ValueError:
            return str(path)

    def _finalize_return(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        self.artifacts.write_summary(run)
        return run


def build_mock_backend(context: BackendContext) -> MockBackend:
    """Build a ``MockBackend`` from a shared ``BackendContext``."""
    return MockBackend(context)
