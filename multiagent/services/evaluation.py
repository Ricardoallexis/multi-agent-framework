# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Evaluation service shared by Python, CLI and HTTP."""
from __future__ import annotations

import re
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..db.evaluation_store import EvaluationStore
from ..evaluation import EvalSet, run_eval_set
from ..evaluation.regression import compare_reports
from ..config import Settings
from .errors import INVALID_REQUEST, NOT_FOUND, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices


class EvaluationServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._store: EvaluationStore | None = None

    def _run_report(self, value: EvalSet | dict[str, Any], *, live: bool = False) -> dict[str, Any]:
        """Validate and run one set, returning a JSON-ready report."""
        try:
            eval_set = value if isinstance(value, EvalSet) else EvalSet.model_validate(value)
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_context=False, include_input=False)
            raise ServiceError(INVALID_REQUEST, "Invalid evaluation set", status=422,
                               details={"errors": [{**error, "loc": list(error["loc"])} for error in errors]}) from exc
        if live and eval_set.target is not None and eval_set.target.kind != "workflow":
            raise ServiceError("eval_live_target_unsupported", "Live evaluation requires a workflow target",
                               status=422, details={"errors": [{"loc": ["live"],
                                                               "msg": "Only workflow targets support live evaluation"}]})
        if live:
            worker = getattr(self._services.system, "worker", None)
            thread = getattr(worker, "_thread", None)
            if not self._services.settings.worker_enabled or thread is None or not thread.is_alive():
                raise ServiceError("eval_live_worker_unavailable", "Live evaluation requires a running worker",
                                   status=409)
            if self._services.system.engine.router.dry_run:
                raise ServiceError("eval_live_provider_unavailable", "Live evaluation requires a configured provider",
                                   status=422, details={"errors": [{"loc": ["live"],
                                                                   "msg": "The current runtime is in Mock mode"}]})
            return run_eval_set(self._services, eval_set, live=True).model_dump(mode="json")
        # Definitions are package resources; only runtime data needs a temporary home.
        from . import ApplicationServices
        with tempfile.TemporaryDirectory(prefix="multiagent-eval-") as tmp:
            settings = Settings(
                _env_file=None,
                **self._services.settings.model_dump(exclude={"data_dir", "db_path", "runs_dir", "assets_dir",
                                                             "integration_marker", "antigravity_kill_switch",
                                                             "antigravity_lock_file", "worker_enabled"}),
                data_dir=Path(tmp), db_path=None, runs_dir=None, assets_dir=None,
                integration_marker=None, antigravity_kill_switch=None, antigravity_lock_file=None,
                worker_enabled=False,
            )
            mock = ApplicationServices.create(settings, dry_run=True)
            try:
                return run_eval_set(mock, eval_set).model_dump(mode="json")
            except (KeyError, FileNotFoundError) as exc:
                raise ServiceError(INVALID_REQUEST, "Invalid evaluation target", status=422,
                                   details={"errors": [{"loc": ["target", "id"], "msg": str(exc)}]}) from exc
            finally:
                mock.runs.close()

    # --- saved runs, baselines and comparison (EVA-T02) ---------------------

    @property
    def store(self) -> EvaluationStore:
        if self._store is None:
            self._store = EvaluationStore(self._services.system.db)
        return self._store

    def run(self, value: EvalSet | dict[str, Any], *, live: bool = False, actor: str | None = None) -> dict[str, Any]:
        """Run one set and save the report in the system database; ``meta.evaluation_run_id`` names it."""
        actor = _actor(actor)
        report = self._run_report(value, live=live)
        run_id = f"evr-{uuid.uuid4().hex[:12]}"
        report["meta"] = {**report["meta"], "evaluation_run_id": run_id}
        with self.store.db.tx() as conn:
            self.store.insert_run(conn, run_id=run_id, report=report, live=live, actor=actor)
        return report

    @service_errors
    def list_runs(self, *, set_name: str | None = None, limit: int = 100) -> dict[str, Any]:
        if set_name is not None:
            set_name = _name(set_name)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise _invalid("limit", "limit must be an integer from 1 to 1000")
        runs = self.store.list_runs(set_name=set_name, limit=limit)
        return {"runs": runs, "count": len(runs)}

    @service_errors
    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._run(run_id)

    @service_errors
    def set_baseline(self, set_name: str, run_id: str, actor: str | None = None) -> dict[str, Any]:
        set_name, actor = _name(set_name), _actor(actor)
        run = self._run(run_id)
        if run["set_name"] != set_name:
            raise _mismatch(set_name, run["set_name"], run_id=run["id"])
        with self.store.db.tx() as conn:
            self.store.set_baseline(conn, set_name=set_name, run_id=run["id"], actor=actor)
        return self.get_baseline(set_name)

    @service_errors
    def get_baseline(self, set_name: str) -> dict[str, Any]:
        set_name = _name(set_name)
        baseline = self.store.get_baseline(set_name)
        if baseline is None:
            raise _no_baseline(set_name)
        run = self.store.get_run(baseline["run_id"])
        return {**baseline, "run": {key: value for key, value in run.items() if key not in ("report", "fingerprint")}}

    @service_errors
    def compare(self, candidate_id: str, base_id: str | None = None, threshold: float | None = None) -> dict[str, Any]:
        """Compare a run with a base run, by default the baseline of its set. A regression is a result, not an error."""
        if threshold is None:
            threshold = self._services.settings.eval_regression_threshold
        elif isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            raise _invalid("threshold", "threshold must be a number from 0 to 1")
        candidate = self._run(candidate_id)
        if base_id is None:
            base_id = (self.store.get_baseline(candidate["set_name"]) or {}).get("run_id")
            if base_id is None:
                raise _no_baseline(candidate["set_name"])
        base = self._run(base_id)
        if base["set_name"] != candidate["set_name"]:
            raise _mismatch(base["set_name"], candidate["set_name"], base_id=base["id"], candidate_id=candidate["id"])
        return {"set_name": candidate["set_name"], "base_id": base["id"], "candidate_id": candidate["id"],
                **compare_reports(base["report"], candidate["report"], threshold=float(threshold))}

    def _run(self, run_id: Any) -> dict[str, Any]:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise _invalid("run_id", f"Invalid evaluation run id: {run_id!r}")
        run = self.store.get_run(run_id)
        if run is None:
            raise ServiceError(NOT_FOUND, f"Evaluation run not found: {run_id}", status=404,
                               details={"evaluation_run": run_id})
        return run


_RUN_ID = re.compile(r"^evr-[0-9a-f]{12}$")


def _invalid(field: str, message: str) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": field})


def _no_baseline(set_name: str) -> ServiceError:
    return ServiceError(NOT_FOUND, f"No baseline for evaluation set: {set_name}", status=404,
                        details={"set_name": set_name})


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise _invalid("set_name", "set_name must be text of 1 to 200 characters")
    return value


def _actor(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise _invalid("actor", "actor must be text of 1 to 100 characters")
    return value.strip()


def _mismatch(expected: str, found: str, **details: Any) -> ServiceError:
    return ServiceError("evaluation_set_mismatch",
                        f"Evaluation runs belong to different sets: {expected!r} and {found!r}",
                        status=409, details={"expected_set": expected, "found_set": found, **details})
