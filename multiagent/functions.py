# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Registered, synchronous functions for deterministic workflow steps.

Only trusted host code registers functions. Workflow documents name them by id.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from .capabilities import validate_capability_id


_FUNCTIONS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]] = {}


class FunctionStepError(ValueError):
    def __init__(self, code: str, step_id: str):
        self.code = code
        super().__init__(f"{code}: function step {step_id!r}")


def register_function(function_id: str, fn: Callable, *, description: str) -> None:
    """Register trusted host code before workflows start."""
    validate_capability_id(function_id)
    if not callable(fn) or not isinstance(description, str) or not description.strip():
        raise ValueError("A function needs a callable and a description")
    if function_id in _FUNCTIONS:
        raise ValueError(f"Function already registered: {function_id}")
    _FUNCTIONS[function_id] = fn


def get_function(function_id: str) -> Callable | None:
    return _FUNCTIONS.get(function_id)


def _readonly(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_readonly(item) for item in value)
    return value


def _merge_outputs(context: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Collect completed outputs under their step ids."""
    def copy(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: copy(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [copy(item) for item in value]
        return value

    return {"steps": copy(context["steps"])}


register_function("core.merge_outputs", _merge_outputs, description="Collect completed step outputs")


def execute_function_step(*, store, artifacts, settings, output_schemas, run_id: str, index: int,
                          step, attempt: int, request: dict[str, Any], prior_step_ids: list[str]) -> None:
    """Shared Native/Mock execution: no provider, call guard or usage record."""
    function_id = step.function
    store.db.log_event(run_id, "step_started", {
        "step_id": step.id, "attempt": attempt, "function": function_id,
    })
    previous = {step_id: store.latest_step_output(run_id, step_id) for step_id in prior_step_ids}
    context = {"inputs": request.get("inputs", {}),
               "steps": {step_id: output for step_id, output in previous.items() if output is not None}}
    fn = get_function(function_id)
    if fn is None:
        raise FunctionStepError("function_failed", step.id)
    try:
        result = fn(_readonly(context), _readonly(step.arguments or {}))
    except Exception as exc:
        raise FunctionStepError("function_failed", step.id) from exc
    if not isinstance(result, dict):
        raise FunctionStepError("function_output_invalid", step.id)
    try:
        data = output_schemas[step.contract].model_validate(result).model_dump(mode="json")
    except (ValidationError, ValueError, TypeError) as exc:
        raise FunctionStepError("function_output_invalid", step.id) from exc
    store.record_step(run_id=run_id, step_id=step.id, attempt=attempt, status="completed",
                      input_data=context, output_data=data, model_id="", prompt_id="", prompt_version=0)
    path = artifacts.write(run_id=run_id, step_index=index, step_id=step.id, attempt=attempt, data=data)
    relative_path = str(path.relative_to(settings.data_dir))
    store.add_artifact(run_id=run_id, step_id=step.id, attempt=attempt, kind=step.contract,
                       path=relative_path, data=data)
    store.db.log_event(run_id, "step_completed", {
        "step_id": step.id, "attempt": attempt, "artifact_path": relative_path, "function": function_id,
    })
