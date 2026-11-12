"""Evaluation set loading and Mock-mode workflow execution."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

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


def _run_case(
    services: ApplicationServices, eval_set: EvalSet, case: EvalCase
) -> tuple[CaseResult, str, str]:
    request = RunRequest(
        workflow_id=case.workflow,
        project_name=_project_name(eval_set, case),
        inputs=case.inputs,
    )
    run = services.runs.create(request)
    run_id = run["id"]
    worker = getattr(services.system, "worker", None)
    if worker is None:
        raise RuntimeError("ApplicationServices system has no local worker")
    worker.process_once()
    state = services.runs.get(run_id)
    artifacts = state.get("artifacts", [])
    check_results = [
        evaluate_check(check, _artifact_for_check(check, artifacts))
        for check in case.checks
    ]
    passed_count = sum(result.passed for result in check_results)
    score = round(passed_count / len(check_results), 4) if check_results else 1.0
    return (
        CaseResult(
            id=case.id,
            passed=all(result.passed for result in check_results),
            score=score,
            checks=check_results,
        ),
        run_id,
        state.get("status", "unknown"),
    )


def run_eval_set(services: ApplicationServices, eval_set: EvalSet) -> EvalReport:
    """Run every case through the ApplicationServices facade in Mock mode."""
    router = getattr(getattr(getattr(services, "system", None), "engine", None), "router", None)
    if not getattr(router, "dry_run", False):
        raise ValueError("Evaluation requires Mock mode (router.dry_run must be true)")

    results: list[CaseResult] = []
    run_ids: dict[str, str] = {}
    statuses: dict[str, str] = {}
    passed_checks = total_checks = 0
    for case in eval_set.cases:
        result, run_id, status = _run_case(services, eval_set, case)
        results.append(result)
        run_ids[case.id] = run_id
        statuses[case.id] = status
        passed_checks += sum(check.passed for check in result.checks)
        total_checks += len(result.checks)

    return EvalReport(
        name=eval_set.name,
        version=eval_set.version,
        cases=results,
        score=round(passed_checks / total_checks, 4) if total_checks else 1.0,
        meta={"run_ids": run_ids, "statuses": statuses},
    )
