# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Model and effort an executor application is asked to use (ECO-T08, contracts ECO-T01 section 9 and ORQ-6 section 9).

The assignment preview and the executor service ask this module the same question with the same inputs, so what the
preview shows is what the launch request carries. Pure: no catalog access, no launch, no I/O.

Where each value comes from (``*_source``):

* ``user``: the caller named it (``requested_model`` / ``requested_effort`` of the preview, ``model`` / ``effort`` of
  ``run_task``). It always wins and is never replaced by the profile. The executor's evidence can still refuse it, and
  the service reports that as an error rather than dropping it behind the caller's back.
* ``profile``: the model routing decision of the assignment profile's routing profile (economic or not), made for the
  task's step kind: ``selected_model`` / ``planned_model`` for the model and ``effort`` for the effort.
* ``session``: a resume continues what the recorded session used.

A value that came from the profile is sent only when the executor declares it takes it:

* the model must be listed in ``model_support`` (status ``supported`` with values), by the provider's model name
  (``planned_model``, what the app is given) or by the catalog id; otherwise it is not sent and ``notes`` says why;
* the effort must be listed in ``effort_support`` the same way. Unknown evidence (Claude's effort today) means never
  sent: nothing says the app takes it.

``model_exclusions`` is the other half: it tells the router which catalog models an executor cannot receive, so the
router picks the cheapest one it *can* receive, with the usual evidence, instead of the cheapest overall. An executor
whose adapter has ``refuses_model`` (Copilot refuses every Claude model) gets those models excluded too.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..routing.executor_capabilities import ExecutorDescriptor, SupportState

__all__ = [
    "ExecutorSelection", "model_exclusions", "select_for_executor", "select_for_resume",
    "SOURCE_USER", "SOURCE_PROFILE", "SOURCE_SESSION",
    "NOT_DECLARED", "ADAPTER_REFUSES", "EXCLUSION_PREFIX",
]

SOURCE_USER = "user"
SOURCE_PROFILE = "profile"
SOURCE_SESSION = "session"

EXCLUSION_PREFIX = "executor_"
NOT_DECLARED = "executor_model_not_declared"           # the executor lists models and this one is not among them
ADAPTER_REFUSES = "executor_adapter_refuses_model"     # + ":<adapter code>"

MAX_SKIPPED = 5
_MAX_NOTE = 80


def _mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return value if isinstance(value, Mapping) else {}


def _declared(evidence: Any) -> frozenset[str] | None:
    """The values an executor lists for a capability, or None when it does not (status other than supported)."""
    if evidence.status is SupportState.SUPPORTED and evidence.values:
        return frozenset(evidence.values)
    return None


def _model_name(planned: Any, catalog_id: Any, declared: frozenset[str] | None) -> str | None:
    """The name to give the app: the provider's model name, else the catalog id; the one the executor lists if it lists."""
    names = [str(item) for item in (planned, catalog_id) if item]
    if declared is None:
        return names[0] if names else None
    return next((name for name in names if name in declared), None)


def model_exclusions(models: Mapping[str, Any] | Iterable[Any], descriptor: ExecutorDescriptor,
                     adapter: Any = None) -> dict[str, str]:
    """Catalog model id -> reason code, for the models this executor cannot receive.

    A model is excluded when the executor declares the models it takes (``model_support`` supported, with values) and
    neither the model's name nor its id is listed, or when the adapter's ``refuses_model(name)`` returns a reason. An
    executor that declares nothing about models excludes nothing here: the assignment selector decides about it.
    """
    specs = models.values() if isinstance(models, Mapping) else models
    declared = _declared(descriptor.model_support)
    refuses = getattr(adapter, "refuses_model", None)
    excluded: dict[str, str] = {}
    for spec in specs:
        catalog_id = getattr(spec, "id", None)
        if not catalog_id:
            continue
        name = _model_name(getattr(spec, "model", None), catalog_id, declared)
        if declared is not None and name is None:
            excluded[str(catalog_id)] = NOT_DECLARED
            continue
        if callable(refuses) and name:
            reason = refuses(name)
            if reason:
                excluded[str(catalog_id)] = f"{ADAPTER_REFUSES}:{reason}"
    return excluded


def _price_source(evidence: Any) -> str | None:
    if not isinstance(evidence, Mapping):
        return None
    sources = []
    for component in ("input", "output"):
        price = evidence.get(component)
        source = price.get("source") if isinstance(price, Mapping) else None
        if source and source not in sources:
            sources.append(str(source))
    return " | ".join(sources) if sources else None


def _routing_evidence(route: Mapping[str, Any]) -> dict[str, Any]:
    """The routing decision's evidence that matters for a launch: profile, mode, step kind, level, price, effort."""
    if not route:
        return {}
    price = route.get("price_evidence")
    return {
        "profile_id": route.get("profile_id"), "profile_mode": route.get("profile_mode"),
        "step_kind": route.get("step_kind"), "status": route.get("status"),
        "selected_model": route.get("selected_model"), "level": route.get("selected_level"),
        "price_source": _price_source(price),
        "price_current": price.get("current") if isinstance(price, Mapping) else None,
        "effort_source": route.get("effort_source"),
        "reason_codes": list(route.get("reason_codes") or [])[:4],
    }


def _skipped(route: Mapping[str, Any]) -> list[dict[str, str]]:
    """Candidates left out because of this executor (not because of the profile), with the reason."""
    skipped: list[dict[str, str]] = []
    for candidate in route.get("candidates") or ():
        if not isinstance(candidate, Mapping) or candidate.get("eligible") is not False:
            continue
        reasons = [str(code) for code in candidate.get("reason_codes") or () if str(code).startswith(EXCLUSION_PREFIX)]
        if reasons:
            skipped.append({"model": str(candidate.get("model_id")), "reason": reasons[0]})
    return skipped[:MAX_SKIPPED]


@dataclass(frozen=True)
class ExecutorSelection:
    """What an executor is asked to run with, where each value came from and why anything was not sent."""
    executor_id: str
    model: str | None = None
    model_source: str | None = None
    effort: str | None = None
    effort_source: str | None = None
    notes: tuple[str, ...] = ()
    routing: Mapping[str, Any] = field(default_factory=dict)
    skipped: tuple[Mapping[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """The full record: shown by the assignment preview and returned by ``run_task``."""
        return {"executor_id": self.executor_id, "model": self.model, "model_source": self.model_source,
                "effort": self.effort, "effort_source": self.effort_source, "notes": list(self.notes),
                "routing": dict(self.routing), "skipped": [dict(item) for item in self.skipped]}

    def event_payload(self, *, attempt: int, mode: str) -> dict[str, Any]:
        """The compact record kept on the attempt (``executor.routing_selected``)."""
        routing = dict(self.routing)
        payload: dict[str, Any] = {
            "executor_id": self.executor_id, "attempt": attempt, "mode": mode,
            "model": self.model, "model_source": self.model_source,
            "effort": self.effort, "effort_source": self.effort_source,
            "notes": [note[:_MAX_NOTE] for note in self.notes],
        }
        if routing:
            payload.update({key: routing.get(key) for key in (
                "profile_id", "profile_mode", "step_kind", "level", "price_source", "price_current",
                "selected_model")})
            payload["routing_effort_source"] = routing.get("effort_source")
        if self.skipped:
            payload["skipped"] = [dict(item) for item in self.skipped]
        return payload


def _profile_model(route: Mapping[str, Any], descriptor: ExecutorDescriptor, adapter: Any,
                   notes: list[str]) -> str | None:
    if route.get("status") != "selected" or not route.get("selected_model"):
        if route:
            notes.append("model_not_sent: routing_decision_not_selected")
        return None
    evidence = descriptor.model_support
    declared = _declared(evidence)
    if evidence.status is SupportState.UNSUPPORTED:
        notes.append("model_not_sent: executor_takes_no_model")
        return None
    if declared is None:
        notes.append("model_not_sent: executor_model_support_unknown")
        return None
    name = _model_name(route.get("planned_model"), route.get("selected_model"), declared)
    if name is None:
        notes.append("model_not_sent: model_not_in_executor_support")
        return None
    refuses = getattr(adapter, "refuses_model", None)
    reason = refuses(name) if callable(refuses) else None
    if reason:
        notes.append(f"model_not_sent: adapter_refuses_model:{reason}")
        return None
    return name


def _profile_effort(route: Mapping[str, Any], descriptor: ExecutorDescriptor, notes: list[str]) -> str | None:
    wanted = route.get("effort")
    if not wanted or route.get("status") != "selected":
        return None
    evidence = descriptor.effort_support
    if evidence.status is SupportState.UNSUPPORTED:
        notes.append("effort_not_sent: executor_takes_no_effort")
        return None
    declared = _declared(evidence)
    if declared is None:
        notes.append("effort_not_sent: executor_effort_support_unknown")
        return None
    if str(wanted) not in declared:
        notes.append(f"effort_not_sent: {wanted}_not_in_executor_support")
        return None
    return str(wanted)


def select_for_executor(executor_id: str, descriptor: ExecutorDescriptor, route: Any = None, *,
                        requested_model: str | None = None, requested_effort: str | None = None,
                        adapter: Any = None) -> ExecutorSelection:
    """The model and effort ``executor_id`` is asked to use for a task, from the caller or the routing decision.

    ``route`` is the model routing decision made for this executor (a ``RoutingDecision`` or its dict; None when the
    assignment profile has no routing profile). Explicit ``requested_*`` values win and are returned as given.
    """
    data = _mapping(route)
    notes: list[str] = []
    if requested_model:
        model, model_source = requested_model, SOURCE_USER
    else:
        model = _profile_model(data, descriptor, adapter, notes) if data else None
        model_source = SOURCE_PROFILE if model else None
    if requested_effort:
        effort, effort_source = requested_effort, SOURCE_USER
    else:
        effort = _profile_effort(data, descriptor, notes) if data else None
        effort_source = SOURCE_PROFILE if effort else None
    return ExecutorSelection(executor_id=executor_id, model=model, model_source=model_source, effort=effort,
                             effort_source=effort_source, notes=tuple(notes), routing=_routing_evidence(data),
                             skipped=tuple(_skipped(data)))


def select_for_resume(executor_id: str, *, model: str | None, effort: str | None,
                      recorded_model: str | None, recorded_effort: str | None) -> ExecutorSelection:
    """A resume asks again for what the caller names, else for what the recorded session used."""
    chosen_model, chosen_effort = model or recorded_model, effort or recorded_effort
    return ExecutorSelection(
        executor_id=executor_id, model=chosen_model,
        model_source=(SOURCE_USER if model else SOURCE_SESSION) if chosen_model else None,
        effort=chosen_effort, effort_source=(SOURCE_USER if effort else SOURCE_SESSION) if chosen_effort else None)
