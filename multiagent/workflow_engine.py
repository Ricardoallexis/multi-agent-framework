from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from pydantic import ValidationError

from .adapters.base import LLMResponse
from .artifacts import ArtifactWriter
from .catalog import Catalog
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
from .errors import BudgetExceeded, HumanSubmissionError, ValidationFailed
from .model_router import ModelBinding, ModelRouter
from .prompts import PromptManager
from .schema_utils import estimate_tokens_conservative
from .workflows import WorkflowCatalog, WorkflowStep, condition_is_true


class WorkflowEngine:
    """Deterministic workflow orchestrator.

    The workflow defines step order, agent, prompt, and contract. Execution can
    be local, cloud, auto, or human_guided without changing inter-agent contracts.
    """

    def __init__(self, *, store: Store, catalog: Catalog, prompts: PromptManager,
                 workflows: WorkflowCatalog, router: ModelRouter,
                 artifacts: ArtifactWriter, settings):
        self.store = store
        self.catalog = catalog
        self.prompts = prompts
        self.workflows = workflows
        self.router = router
        self.artifacts = artifacts
        self.settings = settings

    # ---------- public execution ----------
    def process_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.QUEUED.value:
            return run

        base_active = float(run.get("active_seconds") or 0.0)
        started_mono = time.monotonic()
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
        definition = self.workflows.load(run["workflow_id"])
        request = run["request"]

        try:
            index = int(run["current_step"])
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

                output_schema = OUTPUT_SCHEMAS[step.contract]
                requires_web = bool(request.get("requires_web")) if step.requires_web == "from_request" else bool(step.requires_web)
                context = self._context_for_step(
                    run_id, step.id, request, fresh.get("revision_feedback", ""), brand_version=fresh.get("brand_version")
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

                binding, requirements, routing_reason = self._routing_for_step(
                    step=step,
                    execution_mode=execution_mode,
                    requires_web=requires_web,
                    sensitive=bool(request.get("sensitive", False)),
                )
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

                def consume_llm_call() -> None:
                    budget_state = self.store.get_run(run_id)
                    if int(budget_state["llm_calls"]) >= int(budget_state["max_llm_calls"]):
                        raise BudgetExceeded("max_llm_calls reached")
                    self._check_cancel(run_id)
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
                    self.store.record_telemetry(
                        run_id=run_id,
                        step_id=step.id,
                        provider=failure.get("provider", ""),
                        model=failure.get("model", ""),
                        model_digest="",
                        prompt_id=step.prompt_id,
                        prompt_version=step.prompt_version,
                        prompt_sha256=prompt_sha256,
                        brand_version=fresh.get("brand_version"),
                        tokens_in=0,
                        tokens_out=0,
                        num_ctx=0,
                        latency_ms=0,
                        estimated_cost_usd=0.0,
                        success=0,
                        error_type=failure.get("error_type", "UnknownError"),
                    )
                    self.store.db.log_event(run_id, "model_attempt_failed", {"step_id": step.id, **failure})

                response, attempts = self.router.execute(
                    binding=binding,
                    requirements=requirements,
                    prompt=final_prompt,
                    output_schema=output_schema,
                    before_call=consume_llm_call,
                    validate_response=validate_response,
                    on_failed_attempt=record_failed_attempt,
                )

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
                    self.store.db.log_event(run_id, "checkpoint", {"step_id": step.id})
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
                self.store.update_run(
                    run_id,
                    status=RunStatus.WAITING_HUMAN.value,
                    error=str(exc),
                    budget_exhausted=1,
                    waiting_reason=WaitingReason.BUDGET_EXHAUSTED.value,
                )
                self.store.db.log_event(run_id, "budget_exhausted", {"error": str(exc), "actionable_artifact": True})
            else:
                self.store.update_run(run_id, status=RunStatus.FAILED.value, error=str(exc), budget_exhausted=1)
                self.store.db.log_event(run_id, "workflow_failed", {"error": str(exc), "type": type(exc).__name__})
            return self._finalize_return(run_id)
        except Exception as exc:
            self.store.update_run(run_id, status=RunStatus.FAILED.value, error=str(exc))
            self.store.db.log_event(run_id, "workflow_failed", {"error": str(exc), "type": type(exc).__name__})
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

        definition = self.workflows.load(run["workflow_id"])
        index = int(run["current_step"])
        step = definition.steps[index]
        if step.id != pending["step_id"]:
            raise HumanSubmissionError("The pending HumanStepRequest does not match current_step")
        output_schema = OUTPUT_SCHEMAS[pending["expected_contract"]]
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
            self.store.db.log_event(run_id, "checkpoint", {"step_id": step.id})
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

    def _persist_completed_step(self, *, run_id: str, index: int, step: WorkflowStep, attempt: int,
                                context: dict[str, Any], data: dict[str, Any], model_id: str,
                                prompt_sha256: str, response: LLMResponse, attempts: list[dict], fresh: dict[str, Any]) -> None:
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
        self.store.record_telemetry(
            run_id=run_id,
            step_id=step.id,
            provider=response.provider,
            model=response.model,
            model_digest=response.model_digest,
            prompt_id=step.prompt_id,
            prompt_version=step.prompt_version,
            prompt_sha256=prompt_sha256,
            brand_version=fresh.get("brand_version"),
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            num_ctx=int(response.metadata.get("num_ctx") or 0),
            latency_ms=response.latency_ms,
            estimated_cost_usd=self._estimate_cost(response.metadata.get("catalog_model_id", step.preferred_model), response.tokens_in, response.tokens_out),
            success=1,
            error_type="",
        )
        self.store.db.log_event(run_id, "model_selected", {
            "step_id": step.id,
            "provider": response.provider,
            "model": response.model,
            "model_digest": response.model_digest,
            "attempts": attempts,
            "estimated_input_tokens": response.metadata.get("estimated_input_tokens"),
            "thinking": response.metadata.get("thinking"),
        })
        self.store.db.log_event(run_id, "step_completed", {"step_id": step.id, "attempt": attempt, "artifact_path": str(relative_path)})

    def _context_for_step(self, run_id: str, step_id: str, request: dict[str, Any], revision_feedback: str,
                          *, brand_version: int | None) -> dict[str, str]:
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

    def _estimate_cost(self, model_id: str, tokens_in: int, tokens_out: int) -> float:
        try:
            spec = self.catalog.model(model_id)
        except KeyError:
            return 0.0
        return round((tokens_in / 1_000_000) * spec.input_cost_per_million + (tokens_out / 1_000_000) * spec.output_cost_per_million, 8)


class _CancelledSignal(Exception):
    pass
