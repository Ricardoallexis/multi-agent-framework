# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Model-backed proposal through the same router and call guards as new session."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..budget_cut import policy_from_settings
from ..call_guards import SCOPE_RUN_STEP, CallContext, GuardEnvironment, Pause, Stop, build_call_guards
from ..contracts import ModelTier, TaskRequirements
from ..document_trust import FIXED_CLAUSE, Provenance, wrap_untrusted
from ..model_router import ModelBinding
from ..observability import TokenUsageRecord, estimate_call_cost
from ..services.errors import INVALID_REQUEST, ServiceError

_MOCK_LOCK = threading.Lock()
_USAGE_ID = "planning:propose"


class PlanningOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[dict[str, Any]]
    rationale: str = ""


def _error(message: str) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422)


def _catalog(services: Any) -> list[dict[str, Any]]:
    entries = []
    for bundle in [None, *services.bundle_names()]:
        for item in services.definitions.list_workflows(bundle=bundle):
            try:
                definition = services.definitions.get_workflow(item["id"], bundle=bundle)
                input_spec = services.definitions.workflow_inputs(item["id"], bundle=bundle)
            except ServiceError:
                continue
            entries.append({"id": item["id"], "bundle": bundle,
                            "description": str(definition.get("description") or "")[:240],
                            "inputs": [field["name"] for field in input_spec.get("inputs", [])]})
    return entries


def propose(services: Any, request: Mapping[str, Any], confirm_cloud: bool) -> Mapping[str, Any]:
    catalog = _catalog(services)
    if not catalog:
        raise _error("No workflows are available to plan")
    settings, system = services.settings, services.system
    model_id = settings.gui_new_session_model
    try:
        tier = ModelTier(system.catalog.model(model_id).tier)
    except KeyError:
        tier = ModelTier.CLOUD
    if tier != ModelTier.LOCAL and not confirm_cloud:
        raise _error(f"Cloud model {model_id} requires confirm_cloud=true")
    prefix = request.get("prefix", "plan")
    if not isinstance(prefix, str):
        raise _error("prefix must be text")
    instructions = (
        "Propose concrete tasks for the operator's goal. Use only workflows and bundles from CATALOG_JSON. "
        "Return one JSON object with tasks and rationale. Each task needs id, title, depends_on (task ids), "
        "workflow, and optional objective, bundle, inputs. Task ids begin with PREFIX followed by a hyphen. "
        "Use only input names from that workflow's catalog entry, with text values. "
        "Return no other fields. Do not execute anything."
    )
    prompt = "\n\n".join([
        instructions, FIXED_CLAUSE, "PREFIX: " + prefix,
        "CATALOG_JSON: " + json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
        "Operator goal:", wrap_untrusted(request["goal"], Provenance(kind="planning_goal", label="operator goal")),
    ])
    step_id = f"{_USAGE_ID}:{uuid.uuid4().hex}"
    outgoing = [prompt]
    guards = build_call_guards(GuardEnvironment(
        scope=SCOPE_RUN_STEP, catalog=system.catalog, egress={"action": settings.egress_default},
        budgets=policy_from_settings(settings, services.usage), emit=lambda *_: None))

    def before_call(call: CallContext) -> None:
        def replace(value: str) -> None:
            outgoing[0] = value
            if call.replace_outgoing is not None:
                call.replace_outgoing(value)

        verdict = guards.check(call.with_fields(
            step_id=step_id, step_kind="planning", data_class="non_sensitive", replace_outgoing=replace),
            {"llm_calls": 0, "max_llm_calls": 1})
        if isinstance(verdict, (Stop, Pause)):
            raise _error(getattr(verdict, "message", None) or "Model call refused by policy")

    def record_attempt(event: dict[str, Any]) -> None:
        if not event.get("provider_invoked"):
            return
        attempted = str(event.get("attempted_model") or event.get("model") or "")
        spec = system.catalog.models.get(attempted)
        cost = estimate_call_cost(spec, tokens_in=event.get("tokens_in"), tokens_out=event.get("tokens_out")) if spec else None
        system.engine.usage_recorder.record(TokenUsageRecord(
            run_id=_USAGE_ID, step_id=step_id, agent_id=_USAGE_ID, attempt=1,
            provider=str(event.get("provider") or ""),
            model=str(event.get("observed_model") or event.get("effective_model") or event.get("model") or ""),
            model_digest=str(event.get("model_digest") or ""), prompt_id=_USAGE_ID, prompt_version=1,
            prompt_sha256=hashlib.sha256(outgoing[0].encode("utf-8")).hexdigest(), brand_version=None,
            tokens_in=event.get("tokens_in"), tokens_out=event.get("tokens_out"),
            num_ctx=event.get("num_ctx"), duration_ms=event.get("duration_ms"),
            estimated_cost_usd=cost.estimated_cost_usd if cost else None,
            cost_basis=cost.basis if cost else None, success=event.get("status") == "success",
            error_type=str(event.get("error_type") or "")))

    router = system.engine.router
    fake = router.adapters.get("fake") if router.dry_run else None
    # FakeAdapter accepts fixtures by schema name. Keep the fixture local to this call; the lock is only needed
    # while a fixture may be installed, so real model calls are not serialized.
    samples = getattr(fake, "sample_outputs", None)
    with _MOCK_LOCK if samples is not None else nullcontext():
        previous = samples.get(PlanningOutput.__name__) if samples is not None else None
        if samples is not None and previous is None:
            first = catalog[0]
            samples[PlanningOutput.__name__] = {
                "tasks": [{"id": f"{prefix}-1", "title": "Plan the goal", "depends_on": [],
                           "workflow": first["id"], "bundle": first["bundle"], "inputs": {}}],
                "rationale": "Mock planning fixture",
            }
        try:
            response, _ = router.execute(
                binding=ModelBinding(preferred_model=model_id, fallback_model=None),
                requirements=TaskRequirements(requires_web=False, output_schema=PlanningOutput.__name__, tier=tier),
                prompt=prompt, output_schema=PlanningOutput, before_call=before_call, on_attempt=record_attempt)
        except ServiceError:
            raise
        except Exception as exc:
            raise _error(f"Model planner failed: {exc}") from exc
        finally:
            if samples is not None and previous is None:
                samples.pop(PlanningOutput.__name__, None)
    return response.parsed.model_dump(mode="json")
