# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Registered, synchronous functions for deterministic workflow steps.

Only trusted host code registers functions. Workflow documents name them by id.
"""
from __future__ import annotations

import contextvars
import importlib.util
import json
import re
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


# An absolute path inside an exception text names a local folder (and often a person's user name). The detail keeps
# ``<path>`` instead: a quoted path whole, spaces included (``OSError`` quotes its file name), and a bare one up to
# the first space (a Windows one also across spaces between separators). URLs and relative paths are left alone.
_QUOTED_PATH = re.compile(r"""(['"])(?:[A-Za-z]:[\\/]|[\\/]{2}|/(?=[^\s/'"]))(?:(?!\1)[^\r\n])*\1""")
_BARE_PATH = re.compile(
    r"""(?<![A-Za-z0-9_])[A-Za-z]:[\\/](?:[^\\/\r\n'"<>|]*[\\/])*[^\s\\/'"<>|]*"""   # C:\Users\some one\file.csv
    r"""|(?<!\\)\\\\[^\s\\/'"<>|]+(?:[\\/][^\s\\/'"<>|]*)*"""                        # \\server\share\file.csv
    r"""|(?<![\w.:/\\~-])/[^\s/'"<>|]+/[^\s'"<>|]*"""                                # /home/someone/file.csv
)


def _redact_paths(text: str) -> str:
    text = _QUOTED_PATH.sub(lambda match: f"{match.group(1)}<path>{match.group(1)}", text)
    return _BARE_PATH.sub("<path>", text)


def failure_detail(exc: BaseException, *, limit: int = FAILURE_DETAIL_LIMIT) -> str:
    """Return a bounded exception summary without a traceback, secrets or absolute paths."""
    text = f"{type(exc).__name__}: {exc}".replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = _redact_paths(text)
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


# What ``hold_before_function`` tells the engine: call the function, stop because the run now waits for a person, or
# stop because the run is no longer running (a cancel came in) and nothing was recorded.
FUNCTION_RUN, FUNCTION_WAIT, FUNCTION_CHANGED = "run", "wait", "changed"


def _matches(event: Mapping[str, Any], kind: str, step_id: str, attempt: int) -> bool:
    payload = event.get("payload") or {}
    return event.get("event_type") == kind and payload.get("step_id") == step_id and payload.get("attempt") == attempt


def _function_started(event: Mapping[str, Any], step_id: str, attempt: int) -> bool:
    return _matches(event, "step_started", step_id, attempt) and bool((event.get("payload") or {}).get("function"))


def function_review_approved(events: list[dict[str, Any]], step_id: str, attempt: int) -> bool:
    """Whether a person's approval covers the next start of ``attempt`` of a function step.

    One approval is spent by one start (``step_started``, recorded before the function is called). A start that left
    no result (the process stopped while the function ran) has spent it: the same attempt comes back without an
    approval and waits for a new one instead of calling the function a second time."""
    approved = False
    for event in events or ():
        if _matches(event, "function_review_approved", step_id, attempt):
            approved = True
        elif _function_started(event, step_id, attempt):
            approved = False
    return approved


def function_start_interrupted(events: list[dict[str, Any]], step_id: str, attempt: int) -> bool:
    """Whether ``attempt`` started its function before. The engine computes ``attempt`` as the one after the recorded
    attempts, so an earlier start of it was cut off before it recorded a result."""
    return any(_function_started(event, step_id, attempt) for event in events or ())


def function_retry_requested(events: list[dict[str, Any]], step_id: str, attempt: int) -> bool:
    """A person's correction permits exactly the next failed function attempt."""
    return any(_matches(event, "function_retry_requested", step_id, attempt) for event in events or ())


def unanswered_function_failure(store, events: list[dict[str, Any]], run_id: str, step_id: str,
                                attempt: int) -> dict[str, Any] | None:
    """The recoverable failure of the attempt before ``attempt`` when no person answered it with a correction.

    A recoverable failure is a ``failed`` attempt row; only ``RunService.changes`` (``function_retry_requested``)
    permits the next attempt. Whatever else queues the run again, the function is not called without that decision."""
    previous = attempt - 1
    if previous < 1 or function_retry_requested(events, step_id, attempt):
        return None
    row = next((item for item in store.list_step_attempts(run_id, step_id) if item["attempt"] == previous), None)
    if row is None or row["status"] != "failed":
        return None
    failed = next(((event.get("payload") or {}) for event in reversed(list(events or ()))
                   if _matches(event, "function_step_failed", step_id, previous)), {})
    return {"attempt": previous, "code": failed.get("code") or "function_failed",
            "detail": failed.get("detail") or "", "error": row.get("error") or ""}


def insert_run_events(conn, run_id: str,
                      events) -> list[tuple[str, str, dict[str, Any], int | None]]:
    """Insert run events inside the caller's transaction; pass the result to ``announce_run_events`` after it."""
    from .db.database import utcnow

    now = utcnow()
    inserted: list[tuple[str, str, dict[str, Any], int | None]] = []
    for event_type, payload in events:
        cursor = conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                              (run_id, now, event_type, json.dumps(payload, ensure_ascii=False)))
        inserted.append((run_id, event_type, payload, cursor.lastrowid))
    return inserted


def announce_run_events(store, inserted) -> None:
    """Tell the event listeners about committed events, as ``Database.log_event`` does."""
    for notice in inserted:
        for listener in tuple(getattr(store.db, "event_listeners", ())):
            try:
                listener(*notice)
            except Exception:  # noqa: BLE001 - a listener never fails a run
                pass


def _hold_function_step(store, run_id: str, step, attempt: int, *, index: int, reason: str, error: str,
                        events, failed_attempt: bool = False) -> bool:
    """Put the running run in one function wait, with its events (and the failed attempt), in one transaction.

    False when the run is no longer running or a cancel was asked for: then nothing is recorded."""
    from .contracts import RunStatus
    from .db.database import utcnow

    with store.db.tx() as conn:
        now = utcnow()
        changed = conn.execute(
            "UPDATE runs SET status=?,current_step=?,waiting_reason=?,waiting_step=?,waiting_attempt=?,error=?,"
            "updated_at=? WHERE id=? AND status=? AND cancel_requested=0",
            (RunStatus.WAITING_HUMAN.value, index, reason, step.id, attempt, error, now, run_id,
             RunStatus.RUNNING.value),
        ).rowcount
        if not changed:
            return False
        if failed_attempt:
            conn.execute(
                "INSERT OR REPLACE INTO run_steps(run_id,step_id,attempt,status,input_json,output_json,model_id,"
                "prompt_id,prompt_version,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, step.id, attempt, "failed", "{}", "{}", "", "", 0, error, now, now),
            )
        inserted = insert_run_events(conn, run_id, events)
    announce_run_events(store, inserted)
    return True


def wait_for_function_review(store, run_id: str, step, attempt: int, *, index: int,
                             interrupted: bool = False) -> bool:
    """Hold a function attempt until a person approves the inputs and earlier outputs it will read."""
    from .contracts import WaitingReason

    payload: dict[str, Any] = {"step_id": step.id, "attempt": attempt, "function": step.function,
                               "message": f"Review the inputs and earlier outputs before function step {step.id!r} "
                                          f"runs (attempt {attempt})."}
    if interrupted:
        payload.update(interrupted=True,
                       message=f"Function step {step.id!r} (attempt {attempt}) stopped before it recorded a result. "
                               "Review its inputs and earlier outputs again before it runs.")
    return _hold_function_step(store, run_id, step, attempt, index=index, reason=WaitingReason.FUNCTION_REVIEW.value,
                               error="", events=(("function_review_requested", payload),))


def wait_for_function_failure(store, run_id: str, step, attempt: int, exc: FunctionStepError, *,
                              index: int) -> bool:
    """Keep the failed attempt, the wait and its event together, without an artifact or a traceback."""
    from .contracts import WaitingReason

    payload = {"step_id": step.id, "attempt": attempt, "code": exc.code, "detail": exc.detail,
               "message": f"Function step {step.id!r} failed ({exc.code}). Ask for a correction of the previous "
                          "step, or reject the run."}
    return _hold_function_step(store, run_id, step, attempt, index=index, reason=WaitingReason.FUNCTION_FAILURE.value,
                               error=str(exc), events=(("function_step_failed", payload),), failed_attempt=True)


def restore_function_failure(store, run_id: str, step, failure: dict[str, Any], *, index: int) -> bool:
    """Put a run that was queued again after an unanswered recoverable failure back in that wait."""
    from .contracts import WaitingReason

    payload = {"step_id": step.id, "attempt": failure["attempt"], "code": failure["code"],
               "detail": failure["detail"],
               "message": f"Function step {step.id!r} failed ({failure['code']}) and does not run again until a "
                          "correction of the previous step is asked for, or the run is rejected."}
    return _hold_function_step(store, run_id, step, failure["attempt"], index=index,
                               reason=WaitingReason.FUNCTION_FAILURE.value, error=failure["error"],
                               events=(("function_failure_restored", payload),))


def hold_before_function(store, run: dict[str, Any], step, *, index: int, attempt: int) -> str:
    """What Native and Mock do with a function step before calling it (``FUNCTION_RUN``, ``_WAIT`` or ``_CHANGED``).

    An unanswered recoverable failure of the previous attempt puts the run back in that wait, in every mode. In
    ``supervised`` mode (the run's ``operating_mode``) the attempt waits for a person unless an approval that no start
    has spent covers it. A run without a mode, or ``autonomous``, calls the function as before."""
    from .run_request import operating_mode_from_events

    events = run.get("events") or []
    failure = unanswered_function_failure(store, events, run["id"], step.id, attempt)
    if failure is not None:
        held = restore_function_failure(store, run["id"], step, failure, index=index)
    elif (operating_mode_from_events(events) == "supervised"
            and not function_review_approved(events, step.id, attempt)):
        held = wait_for_function_review(store, run["id"], step, attempt, index=index,
                                        interrupted=function_start_interrupted(events, step.id, attempt))
    else:
        return FUNCTION_RUN
    return FUNCTION_WAIT if held else FUNCTION_CHANGED


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
