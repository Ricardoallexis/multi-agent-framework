"""Result cache of a step (CAP-T13, contract CAP-T02 sections 2.2 and 9).

Off unless asked for: nothing here acts until a host builds a ``StepCache`` and a ``StepCachePolicy`` turns it on
(``WorkflowEngine(step_cache=...)``, or ``CachedToolInvoker`` around a ``ToolInvoker``). Without that the engine and the
invoker behave exactly as before.

A key is the sha256 of the canonical JSON of everything that changes the output, with a schema version. The result is an
entry stored as a blob of the ``EnvelopeStore`` (its public ``put_blob``/``read_blob``; nothing in the store changes) whose
``created_by`` is ``step_cache:<key>``, which is how it is found again. An entry that cannot be read, does not hash to its
address, names another key or another schema version is ignored; a failure of the cache never breaks a run: the step runs
as usual and an event says what happened. Events and logs carry keys and blob references, never the cached content.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .db.envelope_store import EnvelopeStore
from .version import __version__
from .tasks.envelopes import blob_ref, canonical_json, envelope_sha256

_logger = logging.getLogger(__name__)

KEY_SCHEMA = "multiagent.step_cache_key/1"       # bump when a field of the key changes meaning
ENTRY_SCHEMA = "multiagent.step_cache_entry/1"
# Part of every model key: a release of the code (adapters, validation) or a bump of the cache logic invalidates entries.
STEP_CACHE_CODE_VERSION = f"{__version__}/1"
ACTOR_PREFIX = "step_cache:"
KIND_MODEL = "model_step"
KIND_TOOL = "tool_call"
EVENT_HIT = "step_cache_hit"
EVENT_STORED = "step_cache_stored"
EVENT_ERROR = "step_cache_error"
ZERO_USAGE = {"tokens_in": 0, "tokens_out": 0, "estimated_cost_usd": 0.0, "cached": True}

EventSink = Callable[[str, Mapping[str, Any]], None]


@dataclass(frozen=True)
class StepCachePolicy:
    """Which steps may use the cache. Off by default; ``step_ids=None`` means every eligible step."""
    enabled: bool = False
    step_ids: frozenset[str] | None = None

    def allows(self, step_id: str) -> bool:
        return self.enabled and (self.step_ids is None or step_id in self.step_ids)


def model_step_key(parts: Mapping[str, Any]) -> str:
    """Key of a model step; ``parts`` holds everything that changes the output (model, prompt, inputs, parameters)."""
    return envelope_sha256({"schema": KEY_SCHEMA, "kind": KIND_MODEL, **parts})


def tool_cacheable(spec: Any) -> bool:
    """Only a deterministic tool whose sole impact is ``read`` and that declares no effect."""
    impacts = {getattr(item, "value", item) for item in spec.impacts}
    return bool(spec.deterministic) and impacts == {"read"} and not spec.effects


def tool_key(spec: Any, spec_sha256: str, arguments: Any) -> str:
    return envelope_sha256({"schema": KEY_SCHEMA, "kind": KIND_TOOL, "tool_id": spec.tool_id,
                            "tool_version": spec.version, "spec_sha256": spec_sha256,
                            "arguments_sha256": envelope_sha256(arguments)})


class StepCache:
    """Reads and writes entries by key. ``emit(kind, payload)`` receives the cache events; a failing sink is ignored."""

    def __init__(self, blobs: EnvelopeStore, db: Any, *, emit: EventSink | None = None) -> None:
        self.blobs = blobs
        self.db = db
        self.emit = emit

    def event(self, kind: str, payload: Mapping[str, Any], *, emit: EventSink | None = None) -> None:
        sink = emit or self.emit
        if sink is None:
            # No sink: a minimal trace (kind, cache key, blob reference), never the content.
            _logger.info("step cache %s kind=%s key=%s blob=%s", kind, payload.get("kind"), payload.get("key"),
                         payload.get("blob_ref"))
            return
        try:
            sink(kind, dict(payload))
        except Exception:
            _logger.warning("The step cache event sink failed", exc_info=True)

    def lookup(self, key: str, *, kind: str, emit: EventSink | None = None) -> tuple[dict[str, Any], str] | None:
        """``(result, blob_ref)`` of the newest valid entry of this key, or ``None``. Never raises."""
        try:
            with self.db.connect() as conn:
                rows = conn.execute("SELECT sha256 FROM blobs WHERE created_by=? ORDER BY created_at DESC, rowid DESC",
                                    (ACTOR_PREFIX + key,)).fetchall()
            for row in rows:
                found = self._read(row[0], key, kind)
                if found is not None:
                    return found, blob_ref(row[0])
        except Exception as exc:
            self.event(EVENT_ERROR, {"key": key, "kind": kind, "stage": "read", "error_type": type(exc).__name__},
                       emit=emit)
            _logger.warning("Step cache read failed; running the step", exc_info=True)
        return None

    def _read(self, sha256: str, key: str, kind: str) -> dict[str, Any] | None:
        try:
            content = self.blobs.read_blob(sha256)
            if hashlib.sha256(content).hexdigest() != sha256:
                return None
            entry = json.loads(content.decode("utf-8"))
        except (OSError, ValueError):
            return None
        if (not isinstance(entry, dict) or entry.get("schema") != ENTRY_SCHEMA or entry.get("key") != key
                or entry.get("kind") != kind or not isinstance(entry.get("result"), dict)):
            return None
        return entry["result"]

    def store(self, key: str, *, kind: str, result: Mapping[str, Any], emit: EventSink | None = None,
              **event_fields: Any) -> str | None:
        """Keep ``result`` under ``key``; the blob reference, or ``None`` when it could not be stored. Never raises."""
        try:
            content = canonical_json({"schema": ENTRY_SCHEMA, "key": key, "kind": kind, "result": result})
            with self.db.tx() as conn:
                row, _ = self.blobs.put_blob(conn, content, actor=ACTOR_PREFIX + key, media_type="application/json")
            ref = blob_ref(row["sha256"])
        except Exception as exc:
            self.event(EVENT_ERROR, {"key": key, "kind": kind, "stage": "write", "error_type": type(exc).__name__,
                                     **event_fields}, emit=emit)
            _logger.warning("Step cache write failed; the step result is unaffected", exc_info=True)
            return None
        self.event(EVENT_STORED, {"key": key, "kind": kind, "blob_ref": ref, **event_fields}, emit=emit)
        return ref


class CachedToolInvoker:
    """Wraps a ``ToolInvoker``: a deterministic ``read`` tool with the same spec and arguments is answered from the cache.

    Permission, grants and arguments are still checked before a hit counts (a denied or malformed call goes to the real
    invoker, which refuses it); the extra invoker guards run too, and any verdict but Proceed (a Stop or Pause even from
    an observer, a failing guard) means no hit: the call goes to the real invoker. Everything else (a
    tool with effects, a non-deterministic one, an unknown tool, any failure) is delegated untouched. Only successful
    results are stored.
    """

    def __init__(self, invoker: Any, cache: StepCache, policy: StepCachePolicy,
                 *, event_sink: EventSink | None = None) -> None:
        self.invoker = invoker
        self.cache = cache
        self.policy = policy
        self.event_sink = event_sink if event_sink is not None else getattr(invoker, "event_sink", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.invoker, name)

    def invoke(self, request: Any, *, grants: Iterable[str] | None = None, evidence: Iterable[Any] | None = None,
               should_cancel: Callable[[], bool] = lambda: False, state: Any = None) -> Any:
        def delegate() -> Any:
            return self.invoker.invoke(request, grants=grants, evidence=evidence, should_cancel=should_cancel,
                                       state=state)

        key = self._key(request)
        if key is None or should_cancel():
            return delegate()
        found = self.cache.lookup(key, kind=KIND_TOOL, emit=self.event_sink)
        if found is not None and self._permitted(request, grants, evidence) and self._guards_proceed(request, state):
            result = self._hit(request, key, found)
            if result is not None:
                return result
        result = delegate()
        if getattr(result, "ok", False) and getattr(result, "output", None) is not None:
            self.cache.store(key, kind=KIND_TOOL, emit=self.event_sink, tool_id=request.tool_id,
                             result={"output": result.output, "output_sha256": result.output_sha256,
                                     "output_bytes": result.output_bytes, "tool_version": result.tool_version,
                                     "spec_sha256": result.spec_sha256})
        return result

    def _key(self, request: Any) -> str | None:
        if not self.policy.enabled:
            return None
        try:
            registered = self.invoker.registry.lookup(request.tool_id, request.tool_version)
            if registered.executor is None or not tool_cacheable(registered.spec):
                return None
            return tool_key(registered.spec, registered.spec_sha256, request.arguments)
        except Exception:
            return None

    def _permitted(self, request: Any, grants: Any, evidence: Any) -> bool:
        from .tools.permissions import check_call
        try:
            registered = self.invoker.registry.lookup(request.tool_id, request.tool_version)
            code, _ = check_call(registered.spec, request, grants=grants, evidence=evidence)
            return not code
        except Exception:
            return False

    def _guards_proceed(self, request: Any, state: Any) -> bool:
        """The extra guards of the invoker run before a hit is served. Anything but ``Proceed`` (a ``Stop``, a
        ``Pause``, a failing guard) sends the call to the real invoker, which answers it exactly as without cache."""
        from multiagent.call_guards import Pause, Proceed, Stop
        try:
            registered = self.invoker.registry.lookup(request.tool_id, request.tool_version)
            for guard in getattr(self.invoker, "guards", ()):
                verdict = guard.check(registered.spec, request, state)
                if isinstance(verdict, (Stop, Pause)) or (
                        not isinstance(verdict, Proceed) and not getattr(guard, "observer", False)):
                    return False
        except Exception:
            return False
        return True

    def _hit(self, request: Any, key: str, found: tuple[dict[str, Any], str]) -> Any:
        from .tools.contracts import ToolResult
        from .tools.invoker import event_payload
        result, ref = found
        try:
            output = result["output"]
            output_bytes = canonical_json(output)
            digest = hashlib.sha256(output_bytes).hexdigest()
            if digest != result.get("output_sha256"):
                return None
            built = ToolResult(request_id=request.request_id, tool_id=request.tool_id,
                               tool_version=result["tool_version"], spec_sha256=result["spec_sha256"], ok=True,
                               output=output, output_sha256=digest, output_bytes=len(output_bytes), duration_ms=0,
                               provenance_ref=f"tool:{request.request_id}")
        except Exception:
            return None
        self.cache.event(EVENT_HIT, {**event_payload(request, built.tool_version, built.spec_sha256),
                                     "key": key, "kind": KIND_TOOL, "blob_ref": ref, "usage": dict(ZERO_USAGE)},
                         emit=self.event_sink)
        return built
