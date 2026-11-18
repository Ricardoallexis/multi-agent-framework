# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only routes of the evaluation view (EVA-T03) under ``/gui/api/evaluations``.

They only read what ``services.evaluation`` already saved (runs, baselines and comparisons, EVA-T02): nothing here runs a
set or sets a baseline. A regression is a result (``regression: true``), not an error. Errors come from the service
(``not_found``, ``invalid_request``), so HTTP, CLI and GUI answer alike.

* ``GET /evaluations``: one entry per set with its latest run, its baseline and a ``status``: ``regression`` (the latest
  run regressed against the baseline), ``no_baseline`` or ``up_to_date``. The sets are read from the newest
  ``MAX_RUNS`` saved runs.
* ``GET /evaluations/runs?set_name=&limit=``: saved runs, newest first.
* ``GET /evaluations/runs/{run_id}``: one run with its report and fingerprint.
* ``GET /evaluations/comparison?set_name=``: the latest run of the set against its baseline (404 ``not_found`` when the
  set has no run or no baseline).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..services.errors import NOT_FOUND, ServiceError
from ..services.evaluation import EvaluationServices

MAX_RUNS = 1000   # the service's own cap for one listing
STATUS_REGRESSION, STATUS_NO_BASELINE, STATUS_UP_TO_DATE = "regression", "no_baseline", "up_to_date"


def _latest(evaluation: EvaluationServices, set_name: str) -> dict[str, Any] | None:
    runs = evaluation.list_runs(set_name=set_name, limit=1)["runs"]
    return runs[0] if runs else None


def _sets(evaluation: EvaluationServices) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for run in evaluation.list_runs(limit=MAX_RUNS)["runs"]:   # newest first: the first of each set is its latest
        latest.setdefault(run["set_name"], run)
    entries = []
    for set_name, run in sorted(latest.items()):
        try:
            baseline = evaluation.get_baseline(set_name)
        except ServiceError as error:   # a set without a baseline is a 404; any other error is not ours to hide
            if error.code != NOT_FOUND:
                raise
            baseline = None
        if baseline is None:
            status = STATUS_NO_BASELINE
        elif baseline["run_id"] == run["id"]:
            status = STATUS_UP_TO_DATE
        else:
            status = STATUS_REGRESSION if evaluation.compare(run["id"], baseline["run_id"])["regression"] else STATUS_UP_TO_DATE
        entries.append({"set_name": set_name, "status": status, "latest_run": run, "baseline": baseline})
    return entries


def add_evaluation_routes(api: APIRouter, services: Any) -> None:
    """Register the routes; ``services.evaluation`` is read per request, like the other GUI routers do."""

    @api.get("/evaluations")
    def evaluation_sets():
        sets = _sets(services.evaluation)
        return {"sets": sets, "count": len(sets)}

    @api.get("/evaluations/runs")
    def evaluation_runs(set_name: str | None = Query(None, max_length=200), limit: int = 100):
        return services.evaluation.list_runs(set_name=set_name, limit=limit)

    @api.get("/evaluations/runs/{run_id}")
    def evaluation_run(run_id: str):
        return services.evaluation.get_run(run_id)

    @api.get("/evaluations/comparison")
    def evaluation_comparison(set_name: str = Query(..., max_length=200)):
        evaluation = services.evaluation
        baseline = evaluation.get_baseline(set_name)   # 404 when the set has no baseline, so it has at least that run
        candidate = _latest(evaluation, set_name)
        comparison = evaluation.compare(candidate["id"], baseline["run_id"])
        return {**comparison, "candidate": candidate, "base": baseline["run"]}
