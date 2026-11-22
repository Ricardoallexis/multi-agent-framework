# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Registered, synchronous functions for deterministic workflow steps.

Only trusted host code registers functions. Workflow documents name them by id.
"""
from __future__ import annotations

import contextvars
import importlib.util
import sys
import threading
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from .capabilities import validate_capability_id
from .egress_guard import redact
from .secret_gate import scan_text


FAILURE_DETAIL_LIMIT = 300
CORE_OWNER = "core"
_FUNCTIONS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]] = {}
_OWNERS: dict[str, str] = {}
# PAQ-T03: bundle -> (fingerprint, ids) of the ``functions.py`` imported for it; one import per package and fingerprint.
_LOADED: dict[str, tuple[str, list[str]]] = {}
_IMPORTING: contextvars.ContextVar[str | None] = contextvars.ContextVar("functions_importing", default=None)
_LOAD_LOCK = threading.RLock()


def failure_detail(exc: BaseException, *, limit: int = FAILURE_DETAIL_LIMIT) -> str:
    """Return a bounded, secret-redacted exception summary without a traceback."""
    text = f"{type(exc).__name__}: {exc}".replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    redacted = redact(text, scan_text(text))
    if limit <= 0:
        return ""
    if limit == 1:
        return "…" if len(redacted) > 1 else redacted
    if len(redacted) > limit:
        return redacted[:limit - 1] + "…"
    return redacted


class FunctionStepError(ValueError):
    def __init__(self, code: str, step_id: str, detail: str = ""):
        self.code = code
        self.step_id = step_id
        self.detail = detail
        message = f"{code}: function step {step_id!r}"
        if detail:
            message = f"{message} — {detail}"
        super().__init__(message)


class FunctionConflictError(ValueError):
    """A function id that is already registered; ``owner`` is who has it."""

    def __init__(self, function_id: str, owner: str):
        self.function_id = function_id
        self.owner = owner
        super().__init__(f"Function already registered: {function_id} (owner: {owner})")


class FunctionsImportError(RuntimeError):
    """A trusted ``functions.py`` that failed to import; only the exception type is kept, never its text or trace."""

    def __init__(self, bundle: str, error_type: str):
        self.bundle = bundle
        self.error_type = error_type
        super().__init__(f"functions.py of {bundle} could not be imported: {error_type}")


def register_function(function_id: str, fn: Callable, *, description: str, owner: str = CORE_OWNER) -> None:
    """Register trusted host code before workflows start.

    While a package's ``functions.py`` is being imported (``load_trusted_functions``) the owner is always that
    package, whatever ``owner`` says, so a package can never register a function as ``core``.
    """
    validate_capability_id(function_id)
    if not callable(fn) or not isinstance(description, str) or not description.strip():
        raise ValueError("A function needs a callable and a description")
    if not isinstance(owner, str) or not owner:
        raise ValueError("A function needs an owner")
    importing = _IMPORTING.get()
    if importing is not None:
        owner = importing
    if function_id in _FUNCTIONS:
        raise FunctionConflictError(function_id, _OWNERS.get(function_id, CORE_OWNER))
    _FUNCTIONS[function_id] = fn
    _OWNERS[function_id] = owner


def function_owner(function_id: str) -> str | None:
    return _OWNERS.get(function_id) if function_id in _FUNCTIONS else None


def unregister_owner(owner: str) -> list[str]:
    """Remove every function of ``owner`` and return their ids. The functions of ``core`` are never removed."""
    if owner == CORE_OWNER:
        return []
    with _LOAD_LOCK:
        removed = sorted(key for key, who in _OWNERS.items() if who == owner)
        for key in removed:
            _FUNCTIONS.pop(key, None)
            _OWNERS.pop(key, None)
        _LOADED.pop(owner, None)
        return removed


def loaded_functions(bundle: str) -> list[str]:
    """Ids the ``functions.py`` of ``bundle`` has registered now."""
    return sorted(key for key, who in _OWNERS.items() if who == bundle and key in _FUNCTIONS)


def load_trusted_functions(bundle: str, root: Path, fingerprint: str) -> list[str]:
    """Import ``root / "functions.py"`` with ``bundle`` as owner and return the ids it registered.

    Call it only for a package the person trusted, after the scan and approval. A second call with the same
    fingerprint does nothing; with another one the old functions go first. A function that already exists raises
    ``FunctionConflictError`` and an import that fails raises ``FunctionsImportError``; both leave nothing of this
    import registered.
    """
    path = Path(root) / "functions.py"
    with _LOAD_LOCK:
        loaded = _LOADED.get(bundle)
        if loaded is not None and loaded[0] == fingerprint:
            return list(loaded[1])
        unregister_owner(bundle)
        if not path.is_file():
            return []
        module_name = f"_maf_functions_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        token = _IMPORTING.set(bundle)
        try:
            # Run the source that was scanned, never a __pycache__ file (as ``bundles._load_contracts`` does).
            exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
        except FunctionConflictError:
            unregister_owner(bundle)
            raise
        except Exception as exc:  # noqa: BLE001 - any failure of the package's code
            unregister_owner(bundle)
            raise FunctionsImportError(bundle, type(exc).__name__) from None
        except BaseException:
            unregister_owner(bundle)
            raise
        finally:
            _IMPORTING.reset(token)
            sys.modules.pop(module_name, None)
        ids = loaded_functions(bundle)
        _LOADED[bundle] = (fingerprint, ids)
        return list(ids)


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
        raise FunctionStepError("function_failed", step.id, failure_detail(exc)) from exc
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
