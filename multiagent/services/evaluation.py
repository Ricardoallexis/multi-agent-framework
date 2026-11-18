# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Evaluation service shared by Python, CLI and HTTP."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..evaluation import EvalSet, run_eval_set
from ..config import Settings
from .errors import INVALID_REQUEST, ServiceError

if TYPE_CHECKING:
    from . import ApplicationServices


class EvaluationServices:
    def __init__(self, services: ApplicationServices):
        self._services = services

    def run(self, value: EvalSet | dict[str, Any], *, live: bool = False) -> dict[str, Any]:
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
