"""Evaluation set loading and execution."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import yaml

from ..run_request import RunRequest
from .cases import CaseResult, CheckSpec, EvalCase, EvalReport, EvalSet
from .checks import evaluate_check

if TYPE_CHECKING:
    from ..services import ApplicationServices


def load_eval_set(path: str | Path) -> EvalSet:
    """Load and validate an evaluation set from YAML."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid evaluation YAML: {exc}") from exc
    return EvalSet.model_validate(raw)


def _artifact_for_check(check: CheckSpec, artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if check.step is None:
        return artifacts[-1] if artifacts else None
    return next((artifact for artifact in artifacts if artifact.get("step_id") == check.step), None)


def _project_name(eval_set: EvalSet, case: EvalCase) -> str:
    name = f"eval-{eval_set.name}-{case.id}"
    return name[:120] if len(name) >= 2 else "eval-run"


def _hash(value: Any) -> str:
    content = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _matching_step(services: ApplicationServices, eval_set: EvalSet):
    target = eval_set.target
    engine = services.system.engine
    if target.kind == "agent" and target.id not in engine.catalog.agents:
        raise KeyError(f"Agent not found: {target.id}")
    given = {key for case in eval_set.cases for key in case.inputs}
    first = None
    for path in sorted(engine.workflows.workflows_dir.glob("*.yaml")):
        definition = engine.workflows.load(path.stem)
        declared = {item.name for item in definition.inputs}
        for step in definition.steps:
            if (target.kind == "agent" and step.agent == target.id or
                    target.kind == "prompt" and step.prompt_id == target.id and step.prompt_version == target.version):
                if given <= declared:
                    engine.preflight(path.stem)
                    return step
                if first is None:
                    first = (path.stem, step)
    if first is not None:
        engine.preflight(first[0])
        return first[1]
    raise KeyError(f"No workflow step defines {target.kind} {target.id!r}")


def _standalone_artifact(services: ApplicationServices, case: EvalCase, step) -> dict[str, Any]:
    engine = services.system.engine
    schema = engine.output_schemas[step.contract]
    rendered = engine.prompts.render(step.prompt_id, step.prompt_version, **case.inputs)
    skill_text = "\n\n".join(engine.prompts.skill_text(skill) for skill in step.skills)
    from ..workflow_engine import OUTPUT_CONTRACT_REMINDER
    prompt = engine.prompts.assemble(
        engine.prompts.stable_prefix(skill_text, engine._output_schema_block(schema)),
        rendered.text, suffix=OUTPUT_CONTRACT_REMINDER)
    from ..catalog import ModelSpec
    response = engine.router.adapters["fake"].generate_structured(
        spec=ModelSpec(id="fixture", provider="fake", model="fixture", tier="local",
                       structured_output=True, web=True),
        prompt=prompt, output_schema=schema, requires_web=False)
    parsed = schema.model_validate(response.parsed)
    engine._prepare_and_semantic_validate(step_id=step.id, parsed=parsed,
                                          requires_web=False, cited_sources=[])
    return {"step_id": step.id, "kind": step.contract, "data": parsed.model_dump(mode="json")}


def _run_case(
    services: ApplicationServices, eval_set: EvalSet, case: EvalCase, *, live: bool = False,
    clock: Callable[[], float] = time.monotonic, wait: Callable[[float], Any] | None = None,
) -> tuple[CaseResult, str, str, list[dict[str, str]]]:
    request = RunRequest(
        workflow_id=eval_set.target.id if eval_set.target else case.workflow,
        project_name=_project_name(eval_set, case),
        inputs=case.inputs,
    )
    run = services.runs.create(request)
    run_id = run["id"]
    if live:
        deadline = clock() + services.settings.eval_live_timeout_seconds
        pause = wait or threading.Event().wait
        while True:
            state = services.system.store.get_run(run_id)
            if state["status"] not in {"queued", "running"}:
                break
            remaining = deadline - clock()
            if remaining <= 0:
                break
            pause(min(services.settings.worker_poll_seconds, remaining))
        timed_out = state["status"] in {"queued", "running"}
    else:
        worker = getattr(services.system, "worker", None)
        if worker is None:
            raise RuntimeError("ApplicationServices system has no local worker")
        worker.process_once()
        state = services.runs.get(run_id)
        timed_out = False
    artifacts = state.get("artifacts", [])
    models = [{"provider": event["payload"]["provider"], "model": event["payload"]["model"]}
              for event in state.get("events", []) if event.get("event_type") == "model_selected"]
    check_results = [
        evaluate_check(check, _artifact_for_check(check, artifacts))
        for check in case.checks
    ]
    passed_count = sum(result.passed for result in check_results)
    score = round(passed_count / len(check_results), 4) if check_results else 1.0
    return (
        CaseResult(
            id=case.id,
            passed=not timed_out and all(result.passed for result in check_results),
            score=0.0 if timed_out else score,
            checks=check_results,
        ),
        run_id,
        "timeout" if timed_out else state.get("status", "unknown"),
        models,
    )


def run_eval_set(services: ApplicationServices, eval_set: EvalSet, *, live: bool = False,
                 clock: Callable[[], float] = time.monotonic,
                 wait: Callable[[float], Any] | None = None) -> EvalReport:
    """Run cases in Mock by default; live workflows use the configured engine."""
    router = getattr(getattr(getattr(services, "system", None), "engine", None), "router", None)
    if live and eval_set.target is not None and eval_set.target.kind != "workflow":
        raise ValueError("Live evaluation is supported only for workflows")
    if not live and not getattr(router, "dry_run", False):
        raise ValueError("Evaluation requires Mock mode (router.dry_run must be true)")
    if live and getattr(router, "dry_run", False):
        raise ValueError("Live evaluation requires a configured provider")

    results: list[CaseResult] = []
    run_ids: dict[str, str] = {}
    statuses: dict[str, str] = {}
    fingerprints: dict[str, str] = {}
    providers_by_case: dict[str, list[dict[str, str]]] = {}
    engine = services.system.engine
    step = _matching_step(services, eval_set) if eval_set.target and eval_set.target.kind != "workflow" else None
    passed_checks = total_checks = 0
    for case in eval_set.cases:
        if step is None:
            result, run_id, status, models = _run_case(services, eval_set, case, live=live, clock=clock, wait=wait)
            providers_by_case[case.id] = models
            workflow_id = eval_set.target.id if eval_set.target else case.workflow
            fingerprints[case.id] = _hash(dataclasses.asdict(engine.preflight(workflow_id)))
        else:
            artifact = _standalone_artifact(services, case, step)
            checks = [evaluate_check(check, _artifact_for_check(check, [artifact])) for check in case.checks]
            count = sum(check.passed for check in checks)
            result = CaseResult(id=case.id, passed=all(check.passed for check in checks),
                                score=round(count / len(checks), 4) if checks else 1.0, checks=checks)
            run_id, status = "", "completed"
            providers_by_case[case.id] = [{"provider": "fake", "model": "fixture"}]
        results.append(result)
        if live and run_id:
            run_ids[case.id] = run_id
        statuses[case.id] = status
        if status == "timeout":
            total_checks += max(1, len(result.checks))
        else:
            passed_checks += sum(check.passed for check in result.checks)
            total_checks += len(result.checks)

    target = eval_set.target
    if target and target.kind == "agent":
        source = {"kind": "agent", "id": target.id, "sha256": _hash(engine.catalog.agents[target.id])}
    elif target and target.kind == "prompt":
        template = engine.prompts.load(target.id, target.version)
        source = {"kind": "prompt", "id": target.id, "version": target.version,
                  "sha256": hashlib.sha256(template.text.encode("utf-8")).hexdigest()}
    else:
        source = {"kind": "workflow", "sha256_by_case": fingerprints}
    return EvalReport(
        name=eval_set.name,
        version=eval_set.version,
        cases=results,
        score=round(passed_checks / total_checks, 4) if total_checks else 1.0,
        meta={**({"run_ids": run_ids} if live else {}), "statuses": statuses,
              "fingerprint": {"target": source, "set_sha256": _hash(eval_set.model_dump(mode="json")),
                              "provider_models_by_case": providers_by_case}},
    )
