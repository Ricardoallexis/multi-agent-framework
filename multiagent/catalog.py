from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .errors import MultiAgentError
from .executors.privacy import HOSTS, RETENTION_STATUSES
from .routing.executor_capabilities import CapabilityEvidence, SupportState
from .routing_policy import EFFORT_LEVELS, MODEL_LEVELS, STEP_KINDS

_logger = logging.getLogger(__name__)

MODEL_CLASSIFICATIONS = ("classified", "unclassified")
# MOD-T04 (contract MOD section 2): what a model entry may declare about where it runs and what it keeps, and the
# evidence for it. Every one of them is optional; what is missing is ``unknown``, never a guess.
MODEL_EVIDENCE_FIELDS = ("provider", "host", "retention")
MODEL_EVIDENCE_PROVENANCES = ("provider_docs", "operator_test", "catalog_declared", "runtime_setting")
UNKNOWN_RETENTION = {"status": "unknown", "period_days": None}
LOCAL_NO_CHARGE = "local_no_charge"
_PRICE_COMPONENTS = ("input", "output")
_PRICE_FIELDS = ("amount", "denominator", "unit", "currency", "source", "valid_from", "valid_until", "provenance")
_PRICE_TEXT_FIELDS = ("unit", "currency", "source", "provenance")
_PRICE_WINDOW_FIELDS = ("valid_from", "valid_until")

# Context window evidence (CMP-T08). ``provider_docs`` is the only provenance a catalog file may declare (cloud
# models, copied from the provider's official documentation); ``runtime_setting`` is produced by the loader for
# Ollama models from the ``num_ctx`` the adapter sends. Anything else has no evidence and stays unknown.
PROVIDER_DOCS = "provider_docs"
RUNTIME_SETTING = "runtime_setting"
CONTEXT_WINDOW_PROVENANCES = (PROVIDER_DOCS, RUNTIME_SETTING)
_CONTEXT_WINDOW_FIELDS = ("tokens", "source", "checked_at", "provenance", "model")
_CONTEXT_WINDOW_OPTIONAL = ("note",)
OLLAMA_NUM_CTX_SOURCE = (
    "num_ctx sent to Ollama on every call (the model's catalog num_ctx, or OLLAMA_NUM_CTX when it sets none)"
)

# Routing profile fields that make a profile an "economic" one (ECO-T05). A profile that declares none
# of them is a legacy profile and is validated and routed exactly as before.
ROUTING_MODES = ("supervised", "automatic", "single")
DEFAULT_ROUTING_MODE = "supervised"
ESCALATION_CEILINGS = ("T1", "T2", "T3")  # T4 is never reached automatically (ECO-T01, decision 2)
DEFAULT_ESCALATION = {"max_escalations_per_step": 1, "ceiling": "T3"}
ECONOMIC_PROFILE_KEYS = ("mode", "levels", "effort", "model_id", "escalation")

# Context profiles (CMP-T02, contract CMP-T01 section 11): declared in ``models.yaml`` under ``context_profiles``,
# apart from ``routing_profiles`` so compaction can be switched on without changing how models are chosen.
CONTEXT_UNKNOWN_WINDOW_POLICIES = ("skip", "block")
CONTEXT_SUMMARY_LEVELS = ("T1", "T2")  # the summary is never made by a T3 or T4 model
DEFAULT_CONTEXT_SUMMARY: dict[str, Any] = {
    "first_level": "T1", "retry_level": "T2", "effort": "low", "model_check": True, "max_calls": 4, "max_ratio": 0.5,
}
DEFAULT_CONTEXT_PROFILE: dict[str, Any] = {
    "version": 1, "enabled": False, "warning_at": 0.62, "threshold": 0.70, "unknown_window": "skip",
    "keep_recent": 1, "summary": DEFAULT_CONTEXT_SUMMARY, "tasks": {"window_tokens": None},
}
CONTEXT_TASKS_ASSIGNMENT = "tasks"  # the key of ``assignments`` that assigns a profile to task envelopes


def normalize_price_unit(unit: Any) -> str:
    """Canonical form of a price unit for comparison: ``token`` and ``tokens`` are the same unit."""
    text = str(unit).strip().casefold()
    return "token" if text in {"token", "tokens"} else text


class CatalogError(MultiAgentError, ValueError):
    """A catalog file declares something the framework cannot trust (reported when it is loaded)."""


def _price_timestamp(where: str, field_name: str, value: Any) -> tuple[str, datetime]:
    """Return ``(text, moment)`` for a timezone-aware ISO 8601 timestamp, or raise ``CatalogError``."""
    if isinstance(value, datetime):
        moment, text = value, value.isoformat()
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise CatalogError(f"{where}.{field_name} {value!r} is not an ISO 8601 timestamp") from None
    elif isinstance(value, date):
        raise CatalogError(f"{where}.{field_name} {value!r} must be a timestamp with a timezone, not a bare date")
    else:
        raise CatalogError(f"{where}.{field_name} is required (ISO 8601 timestamp with a timezone)")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise CatalogError(f"{where}.{field_name} {text!r} needs a timezone (for example a trailing 'Z')")
    return text, moment


def validate_pricing(model_id: str, pricing: Any, *, tier: str | None = None) -> dict[str, Any]:
    """Check a model's ``pricing`` block and return a normalized copy.

    The accepted shape is the one the model router trusts as price evidence: ``input`` and ``output``
    prices, each with ``amount``, an integer ``denominator``, ``unit``, ``currency``, ``source``,
    ``valid_from``, ``valid_until`` and ``provenance``. ``input`` and ``output`` must agree on
    currency, unit and denominator. A block whose validity window has already ended is still a valid
    declaration (the router ignores it when it is not current). ``provenance: local_no_charge`` is the
    way to declare an amount of 0 for a local model; it requires amount 0 and a local tier.
    """
    where = f"Model {model_id!r}: pricing"
    if not isinstance(pricing, dict):
        raise CatalogError(f"{where} must be a mapping with 'input' and 'output' prices")
    unknown = sorted(str(key) for key in pricing if key not in _PRICE_COMPONENTS)
    if unknown:
        raise CatalogError(f"{where} has unknown keys {unknown}; expected only 'input' and 'output'")
    result: dict[str, Any] = {}
    for component in _PRICE_COMPONENTS:
        price = pricing.get(component)
        label = f"{where}.{component}"
        if price is None:
            raise CatalogError(f"{label} is missing; pricing needs both 'input' and 'output'")
        if not isinstance(price, dict):
            raise CatalogError(f"{label} must be a mapping with {', '.join(_PRICE_FIELDS)}")
        extra = sorted(str(key) for key in price if key not in _PRICE_FIELDS)
        if extra:
            raise CatalogError(f"{label} has unknown keys {extra}; expected {', '.join(_PRICE_FIELDS)}")
        missing = [name for name in _PRICE_FIELDS if price.get(name) is None]
        if missing:
            raise CatalogError(f"{label} is incomplete: missing {', '.join(missing)}")
        amount = price["amount"]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(float(amount)) or amount < 0:
            raise CatalogError(f"{label}.amount must be a finite number >= 0, got {amount!r}")
        denominator = price["denominator"]
        if type(denominator) is not int or denominator <= 0:
            raise CatalogError(f"{label}.denominator must be a positive integer (for example 1000000), got {denominator!r}")
        entry: dict[str, Any] = {"amount": amount, "denominator": denominator}
        for name in _PRICE_TEXT_FIELDS:
            value = price[name]
            if not isinstance(value, str) or not value.strip():
                raise CatalogError(f"{label}.{name} must be a non-empty string, got {value!r}")
            entry[name] = value.strip()
        if entry["provenance"] == "unknown":
            raise CatalogError(f"{label}.provenance cannot be 'unknown'; unknown prices are left out, not declared")
        moments = {}
        for name in _PRICE_WINDOW_FIELDS:
            entry[name], moments[name] = _price_timestamp(label, name, price[name])
        if moments["valid_until"] <= moments["valid_from"]:
            raise CatalogError(f"{label}.valid_until must be later than valid_from")
        if entry["provenance"] == LOCAL_NO_CHARGE:
            if amount != 0:
                raise CatalogError(f"{label}: provenance {LOCAL_NO_CHARGE!r} requires amount 0, got {amount!r}")
            if tier is not None and str(tier).casefold() != "local":
                raise CatalogError(f"{label}: provenance {LOCAL_NO_CHARGE!r} is only valid for tier 'local', not {tier!r}")
        result[component] = entry
    for name in ("currency", "unit", "denominator"):
        left, right = result["input"][name], result["output"][name]
        if name == "unit":
            left, right = normalize_price_unit(left), normalize_price_unit(right)
        if left != right:
            raise CatalogError(
                f"{where}: input and output must use the same {name} "
                f"(input {result['input'][name]!r}, output {result['output'][name]!r})"
            )
    return result


def current_price(pricing: Any, *, now: datetime) -> list[tuple[float, str, int, str]] | None:
    """Return ``[input, output]`` as ``(amount, currency, denominator, unit)`` when both are usable at ``now``.

    ``None`` means "no price evidence": the block is missing, incomplete, malformed, outside its validity
    window, has ``unknown`` provenance, or ``input`` and ``output`` disagree on currency, unit or
    denominator. The model router (selection by price) and the usage telemetry (estimated cost) both
    rely on this single check; ``now`` must be timezone-aware.
    """
    if not isinstance(pricing, dict):
        return None
    components = []
    for component in _PRICE_COMPONENTS:
        price = pricing.get(component)
        if not isinstance(price, dict):
            return None
        amount = price.get("amount")
        denominator = price.get("denominator")
        unit = price.get("unit")
        currency = price.get("currency")
        source = price.get("source")
        valid_from = price.get("valid_from")
        valid_until = price.get("valid_until")
        if (not isinstance(amount, (int, float)) or isinstance(amount, bool)
                or not math.isfinite(float(amount)) or amount < 0
                or type(denominator) is not int or denominator <= 0
                or not unit or not currency or not source or not valid_from or not valid_until
                or price.get("provenance") in {None, "unknown"}):
            return None
        try:
            starts = datetime.fromisoformat(str(valid_from).replace("Z", "+00:00"))
            ends = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
            if starts.tzinfo is None or ends.tzinfo is None or starts > now or ends <= now:
                return None
        except (TypeError, ValueError):
            return None
        components.append((float(amount), str(currency), int(denominator), normalize_price_unit(unit)))
    if components[0][1:] != components[1][1:]:
        return None
    return components


def _evidence_date(where: str, value: Any) -> str:
    """Return ``YYYY-MM-DD`` for a date, a datetime or an ISO 8601 date/timestamp string, or raise ``CatalogError``."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    raise CatalogError(f"{where}.checked_at {value!r} must be the date the source was read (YYYY-MM-DD)")


def validate_context_window(model_id: str, window: Any) -> dict[str, Any]:
    """Check a model's declared ``context_window`` block and return a normalized copy.

    A catalog file may only declare window evidence copied from the provider's official documentation:
    ``tokens`` (a positive integer), ``source`` (the page or reference), ``checked_at`` (the date it was read),
    ``provenance: provider_docs`` and ``model`` (the provider model name that source describes), plus an
    optional ``note``. The window of a local Ollama model is not declared; the catalog derives it from ``num_ctx``.
    An unknown window is expressed by leaving the block out, never by a guess.
    """
    where = f"Model {model_id!r}: context_window"
    if not isinstance(window, dict):
        raise CatalogError(f"{where} must be a mapping with {', '.join(_CONTEXT_WINDOW_FIELDS)}")
    allowed = _CONTEXT_WINDOW_FIELDS + _CONTEXT_WINDOW_OPTIONAL
    extra = sorted(str(key) for key in window if key not in allowed)
    if extra:
        raise CatalogError(f"{where} has unknown keys {extra}; expected {', '.join(allowed)}")
    missing = [name for name in _CONTEXT_WINDOW_FIELDS if window.get(name) is None]
    if missing:
        raise CatalogError(f"{where} is incomplete: missing {', '.join(missing)} (leave the block out if the window is unknown)")
    tokens = window["tokens"]
    if type(tokens) is not int or tokens <= 0:
        raise CatalogError(f"{where}.tokens must be a positive integer, got {tokens!r}")
    result: dict[str, Any] = {"tokens": tokens}
    for name in ("source", "provenance", "model"):
        value = window[name]
        if not isinstance(value, str) or not value.strip():
            raise CatalogError(f"{where}.{name} must be a non-empty string, got {value!r}")
        result[name] = value.strip()
    if result["provenance"] != PROVIDER_DOCS:
        raise CatalogError(
            f"{where}.provenance must be {PROVIDER_DOCS!r} (a declared window comes from the provider's official "
            f"documentation; {RUNTIME_SETTING!r} is derived by the catalog for Ollama models), got {window['provenance']!r}")
    result["checked_at"] = _evidence_date(where, window["checked_at"])
    if window.get("note") is not None:
        note = window["note"]
        if not isinstance(note, str) or not note.strip():
            raise CatalogError(f"{where}.note must be a non-empty string when present, got {note!r}")
        result["note"] = note.strip()
    order = ("tokens", "source", "checked_at", "provenance", "model", "note")
    return {name: result[name] for name in order if name in result}


def usable_context_window(window: Any) -> dict[str, Any] | None:
    """Return a copy of ``window`` when it is usable evidence of a context window, else ``None`` (unknown).

    Usable means a positive integer ``tokens``, a ``source``, a known ``provenance`` and, for
    ``provider_docs``, the date it was checked. The compaction guard and anything else that needs the window
    of a model rely on this single check, as price consumers rely on ``current_price``: ``None`` is "no
    evidence", never a number to assume.
    """
    if not isinstance(window, dict):
        return None
    tokens, source, provenance = window.get("tokens"), window.get("source"), window.get("provenance")
    if type(tokens) is not int or tokens <= 0 or not isinstance(source, str) or not source.strip():
        return None
    if provenance not in CONTEXT_WINDOW_PROVENANCES:
        return None
    checked_at = window.get("checked_at")
    if provenance == PROVIDER_DOCS and not (isinstance(checked_at, str) and checked_at.strip()):
        return None
    return dict(window)


def _level_index(level: str) -> int:
    return MODEL_LEVELS.index(level)


def validate_routing_profile(profile_id: str, profile: Any) -> Any:
    """Check the economic fields of a routing profile and return a copy with them normalized.

    The economic fields are ``mode`` (``supervised`` by default, ``automatic`` or ``single``), ``levels``
    (per step kind a ``min`` and/or ``max`` model level), ``effort`` (per step kind), ``model_id``
    (required when ``mode`` is ``single``) and ``escalation`` (``max_escalations_per_step`` and
    ``ceiling``). Escalation is only declared here; the selector and the workflow engine act on it (ECO-T06).
    A profile that declares none of them, or a value that is not a mapping, is returned unchanged. A
    malformed declaration raises ``CatalogError`` naming the profile.
    """
    if not isinstance(profile, dict) or not any(key in profile for key in ECONOMIC_PROFILE_KEYS):
        return profile
    where = f"Routing profile {profile_id!r}"
    result = dict(profile)

    mode = profile.get("mode", DEFAULT_ROUTING_MODE)
    if mode not in ROUTING_MODES:
        raise CatalogError(f"{where}: mode must be one of {', '.join(ROUTING_MODES)}, got {mode!r}")
    result["mode"] = mode

    model_id = profile.get("model_id")
    if "model_id" in profile and (not isinstance(model_id, str) or not model_id.strip()):
        raise CatalogError(f"{where}: model_id must be a non-empty catalog model id, got {model_id!r}")
    if mode == "single" and model_id is None:
        raise CatalogError(f"{where}: mode 'single' requires model_id (the one model the profile may use)")
    if model_id is not None:
        result["model_id"] = model_id.strip()

    if "candidate_model_ids" in profile:
        candidates = profile["candidate_model_ids"]
        if (not isinstance(candidates, list) or not candidates
                or any(not isinstance(item, str) or not item.strip() for item in candidates)
                or len(set(candidates)) != len(candidates)):
            raise CatalogError(f"{where}: candidate_model_ids must be a non-empty list of unique model ids")
        if mode == "single" and result["model_id"] not in candidates:
            raise CatalogError(f"{where}: model_id {result['model_id']!r} is not in candidate_model_ids")

    if "levels" in profile:
        levels = profile["levels"]
        if not isinstance(levels, dict) or not levels:
            raise CatalogError(
                f"{where}: levels must be a mapping from step kind ({', '.join(STEP_KINDS)}) to a min/max level")
        normalized: dict[str, dict[str, str]] = {}
        for kind, bounds in levels.items():
            if kind not in STEP_KINDS:
                raise CatalogError(f"{where}: levels has unknown step kind {kind!r}; expected one of {', '.join(STEP_KINDS)}")
            if not isinstance(bounds, dict) or not bounds:
                raise CatalogError(f"{where}: levels.{kind} must be a mapping with 'min' and/or 'max' (for example {{min: T3}})")
            unknown = sorted(str(key) for key in bounds if key not in ("min", "max"))
            if unknown:
                raise CatalogError(f"{where}: levels.{kind} has unknown keys {unknown}; expected only 'min' and 'max'")
            for bound, level in bounds.items():
                if level not in MODEL_LEVELS:
                    raise CatalogError(
                        f"{where}: levels.{kind}.{bound} must be one of {', '.join(MODEL_LEVELS)}, got {level!r}")
            if "min" in bounds and "max" in bounds and _level_index(bounds["min"]) > _level_index(bounds["max"]):
                raise CatalogError(f"{where}: levels.{kind}.min {bounds['min']} is above max {bounds['max']}")
            normalized[kind] = {bound: bounds[bound] for bound in ("min", "max") if bound in bounds}
        result["levels"] = normalized

    if "effort" in profile:
        effort = profile["effort"]
        if not isinstance(effort, dict):
            raise CatalogError(
                f"{where}: effort must be a mapping from step kind ({', '.join(STEP_KINDS)}) to an effort level")
        for kind, level in effort.items():
            if kind not in STEP_KINDS:
                raise CatalogError(f"{where}: effort has unknown step kind {kind!r}; expected one of {', '.join(STEP_KINDS)}")
            if level not in EFFORT_LEVELS:
                raise CatalogError(
                    f"{where}: effort.{kind} must be one of {', '.join(EFFORT_LEVELS)} "
                    f"(leave the step kind out to keep the provider default), got {level!r}")
        result["effort"] = dict(effort)

    if "escalation" in profile:
        escalation = profile["escalation"]
        if not isinstance(escalation, dict):
            raise CatalogError(f"{where}: escalation must be a mapping with max_escalations_per_step and ceiling")
        unknown = sorted(str(key) for key in escalation if key not in DEFAULT_ESCALATION)
        if unknown:
            raise CatalogError(f"{where}: escalation has unknown keys {unknown}; expected {', '.join(DEFAULT_ESCALATION)}")
        limit = escalation.get("max_escalations_per_step", DEFAULT_ESCALATION["max_escalations_per_step"])
        if type(limit) is not int or not 0 <= limit <= 1:
            raise CatalogError(
                f"{where}: escalation.max_escalations_per_step must be 0 or 1 (one escalation per step), got {limit!r}")
        ceiling = escalation.get("ceiling", DEFAULT_ESCALATION["ceiling"])
        if ceiling not in ESCALATION_CEILINGS:
            raise CatalogError(
                f"{where}: escalation.ceiling must be one of {', '.join(ESCALATION_CEILINGS)} "
                f"(T4 is never automatic), got {ceiling!r}")
        result["escalation"] = {"max_escalations_per_step": limit, "ceiling": ceiling}
    return result


def _context_fraction(where: str, name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CatalogError(f"{where}: {name} must be a number between 0 and 1, got {value!r}")
    return float(value)


def _context_positive_int(where: str, name: str, value: Any, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if type(value) is not int or value < 1:
        raise CatalogError(f"{where}: {name} must be an integer of 1 or more{' or null' if allow_none else ''}, got {value!r}")
    return value


def _validate_context_summary(where: str, summary: Any) -> dict[str, Any]:
    """The ``summary`` block of a context profile with every default filled in (see ``DEFAULT_CONTEXT_SUMMARY``)."""
    if summary is None:
        return dict(DEFAULT_CONTEXT_SUMMARY)
    if not isinstance(summary, dict):
        raise CatalogError(f"{where}: summary must be a mapping with {', '.join(DEFAULT_CONTEXT_SUMMARY)}")
    unknown = sorted(str(key) for key in summary if key not in DEFAULT_CONTEXT_SUMMARY)
    if unknown:
        raise CatalogError(f"{where}: summary has unknown keys {unknown}; expected {', '.join(DEFAULT_CONTEXT_SUMMARY)}")
    result = {**DEFAULT_CONTEXT_SUMMARY, **summary}
    if result["first_level"] not in CONTEXT_SUMMARY_LEVELS:
        raise CatalogError(f"{where}: summary.first_level must be one of {', '.join(CONTEXT_SUMMARY_LEVELS)} "
                           f"(a summary is never made by T3 or T4), got {result['first_level']!r}")
    retry = result["retry_level"]
    if retry is not None:
        if retry not in CONTEXT_SUMMARY_LEVELS:
            raise CatalogError(f"{where}: summary.retry_level must be one of {', '.join(CONTEXT_SUMMARY_LEVELS)} "
                               f"or null (no retry), got {retry!r}")
        if _level_index(retry) <= _level_index(result["first_level"]):
            raise CatalogError(f"{where}: summary.retry_level {retry} must be above first_level {result['first_level']}")
    if result["effort"] is not None and result["effort"] not in EFFORT_LEVELS:
        raise CatalogError(f"{where}: summary.effort must be one of {', '.join(EFFORT_LEVELS)} or null, "
                           f"got {result['effort']!r}")
    if type(result["model_check"]) is not bool:
        raise CatalogError(f"{where}: summary.model_check must be true or false, got {result['model_check']!r}")
    _context_positive_int(where, "summary.max_calls", result["max_calls"])
    ratio = _context_fraction(where, "summary.max_ratio", result["max_ratio"])
    if not 0 < ratio < 1:
        raise CatalogError(f"{where}: summary.max_ratio must be above 0 and below 1, got {result['max_ratio']!r}")
    result["max_ratio"] = ratio
    return {name: result[name] for name in DEFAULT_CONTEXT_SUMMARY}


def validate_context_profile(profile_id: str, profile: Any) -> dict[str, Any]:
    """Check one entry of ``context_profiles.profiles`` and return a normalized copy with the defaults filled in.

    Fields: ``version`` (1), ``enabled`` (``false`` unless set: a profile does nothing until it is switched on),
    ``warning_at`` and ``threshold`` (fractions of the model window, ``0 < warning_at < threshold < 1``),
    ``unknown_window`` (``skip`` lets a call to a model of unknown window go on with one warning, ``block``
    stops it), ``keep_recent`` (outputs never summarized, at least 1), ``summary`` (how summaries are made; it is
    the mapping ``SummaryPolicy.from_profile`` takes) and ``tasks.window_tokens`` (the window to measure task
    context against, or ``null`` to use the requested model's). A malformed entry raises ``CatalogError``
    naming the profile; an unknown key is an error, never ignored.
    """
    where = f"Context profile {profile_id!r}"
    if not isinstance(profile, dict):
        raise CatalogError(f"{where} must be a mapping ({', '.join(DEFAULT_CONTEXT_PROFILE)})")
    unknown = sorted(str(key) for key in profile if key not in DEFAULT_CONTEXT_PROFILE)
    if unknown:
        raise CatalogError(f"{where}: unknown keys {unknown}; expected {', '.join(DEFAULT_CONTEXT_PROFILE)}")
    merged = {**DEFAULT_CONTEXT_PROFILE, **profile}
    if merged["version"] != 1 or type(merged["version"]) is not int:
        raise CatalogError(f"{where}: version must be 1, got {merged['version']!r}")
    if type(merged["enabled"]) is not bool:
        raise CatalogError(f"{where}: enabled must be true or false, got {merged['enabled']!r}")
    warning_at = _context_fraction(where, "warning_at", merged["warning_at"])
    threshold = _context_fraction(where, "threshold", merged["threshold"])
    if not 0 < warning_at < threshold < 1:
        raise CatalogError(f"{where}: needs 0 < warning_at < threshold < 1, got warning_at={merged['warning_at']!r} "
                           f"and threshold={merged['threshold']!r}")
    if merged["unknown_window"] not in CONTEXT_UNKNOWN_WINDOW_POLICIES:
        raise CatalogError(f"{where}: unknown_window must be one of {', '.join(CONTEXT_UNKNOWN_WINDOW_POLICIES)}, "
                           f"got {merged['unknown_window']!r}")
    _context_positive_int(where, "keep_recent", merged["keep_recent"])
    tasks = merged["tasks"]
    if tasks is None:
        tasks = {}
    if not isinstance(tasks, dict):
        raise CatalogError(f"{where}: tasks must be a mapping with window_tokens")
    extra = sorted(str(key) for key in tasks if key != "window_tokens")
    if extra:
        raise CatalogError(f"{where}: tasks has unknown keys {extra}; expected window_tokens")
    window_tokens = _context_positive_int(where, "tasks.window_tokens", tasks.get("window_tokens"), allow_none=True)
    return {
        "version": 1, "enabled": merged["enabled"], "warning_at": warning_at, "threshold": threshold,
        "unknown_window": merged["unknown_window"], "keep_recent": merged["keep_recent"],
        "summary": _validate_context_summary(where, merged["summary"]), "tasks": {"window_tokens": window_tokens},
    }


def _validate_retention(model_id: str, value: Any) -> dict[str, Any]:
    """``{status, period_days}``; a model that declares nothing keeps the unknown retention (never none)."""
    if value is None:
        return dict(UNKNOWN_RETENTION)
    if not isinstance(value, dict):
        raise CatalogError(f"Model {model_id!r}: retention must be a mapping with status and period_days")
    extra = sorted(str(key) for key in value if key not in ("status", "period_days"))
    if extra:
        raise CatalogError(f"Model {model_id!r}: retention has unknown keys {extra}; expected status, period_days")
    status = value.get("status", "unknown")
    if status not in RETENTION_STATUSES:
        raise CatalogError(f"Model {model_id!r}: retention.status must be one of {', '.join(RETENTION_STATUSES)}, got {status!r}")
    days = value.get("period_days")
    if days is not None and (type(days) is not int or days < 0):
        raise CatalogError(f"Model {model_id!r}: retention.period_days must be an integer of 0 or more or null, got {days!r}")
    return {"status": status, "period_days": days}


def _validate_model_evidence(model_id: str, value: Any) -> dict[str, CapabilityEvidence] | None:
    """Evidence by field. Each entry needs ``source``, ``provenance`` and ``observed_at`` (YYYY-MM-DD); ``scope``, ``model``
    and ``note`` are optional. A field with no entry is unknown."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CatalogError(f"Model {model_id!r}: evidence must be a mapping from field to evidence")
    checked: dict[str, CapabilityEvidence] = {}
    for name, entry in value.items():
        where = f"Model {model_id!r}: evidence.{name}"
        if name not in MODEL_EVIDENCE_FIELDS:
            raise CatalogError(f"Model {model_id!r}: evidence has unknown field {name!r}; expected {', '.join(MODEL_EVIDENCE_FIELDS)}")
        if isinstance(entry, CapabilityEvidence):
            entry = entry.to_dict()
        if not isinstance(entry, dict):
            raise CatalogError(f"{where} must be a mapping")
        extra = sorted(str(key) for key in entry if key not in ("source", "provenance", "observed_at", "scope", "model", "note"))
        if extra:
            raise CatalogError(f"{where} has unknown keys {extra}")
        if type(entry.get("observed_at")) is date:          # YAML reads an unquoted 2026-10-27 as a date
            entry = {**entry, "observed_at": entry["observed_at"].isoformat()}
        for key in ("source", "provenance", "observed_at"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise CatalogError(f"{where}.{key} is required and must be text")
        if entry["provenance"] not in MODEL_EVIDENCE_PROVENANCES:
            raise CatalogError(f"{where}.provenance must be one of {', '.join(MODEL_EVIDENCE_PROVENANCES)}, got {entry['provenance']!r}")
        try:
            observed = date.fromisoformat(entry["observed_at"])
        except ValueError:
            raise CatalogError(f"{where}.observed_at must be a date YYYY-MM-DD, got {entry['observed_at']!r}") from None
        for key in ("scope", "model", "note"):
            if entry.get(key) is not None and not isinstance(entry[key], str):
                raise CatalogError(f"{where}.{key} must be text")
        checked[str(name)] = CapabilityEvidence(
            status=SupportState.SUPPORTED, source=entry["source"], provenance=entry["provenance"],
            scope=entry.get("scope"), observed_at=observed.isoformat(), model=entry.get("model"), note=entry.get("note"))
    return checked


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    model: str
    tier: str
    structured_output: bool
    web: bool
    num_ctx: int | None = None
    keep_alive: str | None = None
    thinking: bool | str | None = None
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    free_tier_eligible: bool = False
    capabilities: frozenset[str] | None = None
    roles: frozenset[str] | None = None
    permissions: frozenset[str] | None = None
    permissions_required: frozenset[str] | None = None
    allowed_data_classes: frozenset[str] | None = None
    pricing: dict[str, Any] | None = None
    level: str | None = None
    classification: str = "unclassified"
    # Window evidence (see ``validate_context_window``); ``None`` means unknown. Read it through
    # ``Catalog.context_window`` or ``usable_context_window`` rather than assuming a number.
    context_window: dict[str, Any] | None = None
    # The most output tokens one call to the model can return, as the provider's own documentation states it (USO-T04: the
    # budget cut prices a call at this bound, because the router cannot yet ask the provider for less). ``None`` means
    # unknown, never a number to assume.
    max_output_tokens: int | None = None
    # MOD-T04: where the model runs (``local``, ``organization``, ``provider_hosted``, ``third_party``, ``unknown``), what
    # the provider keeps (``{status, period_days}``) and the evidence by field (``provider``, ``host``, ``retention``).
    host: str = "unknown"
    retention: dict[str, Any] | None = None
    evidence: dict[str, CapabilityEvidence] | None = None

    def __post_init__(self) -> None:
        if self.host not in HOSTS:
            raise CatalogError(f"Model {self.id!r}: host must be one of {', '.join(HOSTS)}, got {self.host!r}")
        object.__setattr__(self, "retention", _validate_retention(self.id, self.retention))
        object.__setattr__(self, "evidence", _validate_model_evidence(self.id, self.evidence))
        if self.max_output_tokens is not None and (type(self.max_output_tokens) is not int or self.max_output_tokens <= 0):
            raise CatalogError(f"Model {self.id!r}: max_output_tokens must be a positive integer, got {self.max_output_tokens!r}")
        if self.level is not None and self.level not in MODEL_LEVELS:
            raise CatalogError(f"Model {self.id!r}: level must be one of {', '.join(MODEL_LEVELS)}, got {self.level!r}")
        if self.classification not in MODEL_CLASSIFICATIONS:
            raise CatalogError(
                f"Model {self.id!r}: classification must be one of {', '.join(MODEL_CLASSIFICATIONS)}, "
                f"got {self.classification!r}"
            )
        if self.classification == "classified" and self.level is None:
            raise CatalogError(f"Model {self.id!r}: classification 'classified' requires a level ({', '.join(MODEL_LEVELS)})")
        for name in ("capabilities", "roles", "permissions", "permissions_required", "allowed_data_classes"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, frozenset):
                object.__setattr__(self, name, frozenset([value] if isinstance(value, str) else value))


class Catalog:
    """Agents and models from ``catalog_dir`` (the bundled catalog by default)."""

    def __init__(self, settings: Settings, *, catalog_dir: Path | None = None):
        self.settings = settings
        self.catalog_dir = Path(catalog_dir) if catalog_dir is not None else settings.catalog_dir
        self.agents = self._load_yaml(self.catalog_dir / "agents.yaml").get("agents", {})
        models_document = self._load_yaml(self.catalog_dir / "models.yaml")
        raw_models = models_document.get("models", {})
        routing = models_document.get("routing_profiles", {}) or {}
        self.routing_profiles = {
            profile_id: validate_routing_profile(str(profile_id), profile)
            for profile_id, profile in dict(routing.get("profiles", {}) or {}).items()
        }
        self.routing_assignments = dict(routing.get("assignments", {}) or {})
        self.context_profiles, self.context_assignments = self._load_context_profiles(
            models_document.get("context_profiles"))
        executor_routing = models_document.get("executor_routing", {}) or {}
        self.executor_routing_valid = isinstance(executor_routing, dict)
        self.executor_routing = dict(executor_routing) if self.executor_routing_valid else {}
        self.executor_routing_hash = hashlib.sha256(json.dumps(
            self.executor_routing, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            default=str,
        ).encode("utf-8")).hexdigest()
        self.models: dict[str, ModelSpec] = {}
        for model_id, raw in raw_models.items():
            raw = dict(raw)
            if model_id in {"local_default", "local_reasoning"}:
                raw["model"] = settings.ollama_model
                raw["num_ctx"] = settings.ollama_num_ctx
                raw["keep_alive"] = settings.ollama_keep_alive
            elif model_id in {"gemini_fast", "cloud_fast"}:
                raw["model"] = settings.gemini_model
            elif model_id == "cloud_advanced":
                raw["model"] = settings.gemini_advanced_model
            elif model_id == "gemini_grounded":
                raw["model"] = settings.gemini_grounded_model
            elif model_id == "openai_optional":
                raw["model"] = settings.openai_model
            if raw.get("pricing") is not None:
                raw["pricing"] = validate_pricing(model_id, raw["pricing"], tier=raw.get("tier"))
            raw["context_window"] = self._resolve_context_window(model_id, raw)
            spec = ModelSpec(id=model_id, **raw)
            self._warn_legacy_cost(spec)
            self.models[model_id] = spec
        for profile_id, profile in self.routing_profiles.items():
            if isinstance(profile, dict) and profile.get("mode") == "single" and profile["model_id"] not in self.models:
                raise CatalogError(
                    f"Routing profile {str(profile_id)!r}: model_id {profile['model_id']!r} is not a model in the catalog")

    @staticmethod
    def _load_context_profiles(document: Any) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        """The validated ``context_profiles`` of ``models.yaml`` and the profile assigned to each workflow.

        ``assignments`` maps a workflow id (or ``tasks``, for task envelopes) to a profile. A profile that does
        not exist is an error here, so a typo cannot silently leave compaction off.
        """
        if document is None:
            return {}, {}
        if not isinstance(document, dict):
            raise CatalogError("context_profiles must be a mapping with 'profiles' and 'assignments'")
        unknown = sorted(str(key) for key in document if key not in ("profiles", "assignments"))
        if unknown:
            raise CatalogError(f"context_profiles has unknown keys {unknown}; expected profiles, assignments")
        declared = document.get("profiles", {})
        if declared is None:
            declared = {}
        if not isinstance(declared, dict):
            raise CatalogError("context_profiles.profiles must be a mapping from profile id to profile")
        profiles = {str(profile_id): validate_context_profile(str(profile_id), profile)
                    for profile_id, profile in declared.items()}
        declared_assignments = document.get("assignments", {})
        if declared_assignments is None:
            declared_assignments = {}
        if not isinstance(declared_assignments, dict):
            raise CatalogError("context_profiles.assignments must be a mapping from workflow id (or 'tasks') to profile id")
        assignments: dict[str, str] = {}
        for target, profile_id in declared_assignments.items():
            if not isinstance(profile_id, str) or profile_id not in profiles:
                raise CatalogError(f"context_profiles.assignments.{target}: {profile_id!r} is not a declared context profile "
                                   f"({', '.join(sorted(profiles)) or 'none declared'})")
            assignments[str(target)] = profile_id
        return profiles, assignments

    def _resolve_context_window(self, model_id: str, raw: dict[str, Any]) -> dict[str, Any] | None:
        """The model's window evidence, or ``None`` when it is unknown.

        An Ollama model has no declared window: the window is the ``num_ctx`` the adapter sends (the model's
        own, else ``OLLAMA_NUM_CTX``), with provenance ``runtime_setting``. Any other model has a window only
        if its catalog entry declares evidence for the very model name in force; a name changed by a setting
        after the evidence was written (for example ``GEMINI_MODEL``) makes the evidence not apply.
        """
        declared = raw.get("context_window")
        model = raw.get("model")
        if str(raw.get("provider") or "").strip().casefold() == "ollama":
            if declared is not None:
                raise CatalogError(
                    f"Model {model_id!r}: context_window cannot be declared for an Ollama model; its window is the "
                    f"num_ctx sent on every call (set num_ctx, or OLLAMA_NUM_CTX)")
            configured = raw.get("num_ctx") or self.settings.ollama_num_ctx
            if isinstance(configured, bool):
                return None
            try:
                num_ctx = int(configured)
            except (TypeError, ValueError):
                return None
            if num_ctx <= 0:
                return None
            return {"tokens": num_ctx, "source": OLLAMA_NUM_CTX_SOURCE, "checked_at": None,
                    "provenance": RUNTIME_SETTING, "model": str(model) if model is not None else None}
        if declared is None:
            return None
        window = validate_context_window(model_id, declared)
        if str(model or "").strip().casefold() != window["model"].casefold():
            _logger.warning(
                "Model %r: context_window evidence is for provider model %r but the model in force is %r; "
                "treating the window as unknown.", model_id, window["model"], model,
            )
            return None
        return window

    @staticmethod
    def _warn_legacy_cost(spec: ModelSpec) -> None:
        """Legacy per-million costs are not price evidence; say so when they are set without ``pricing``."""
        def declared(value: Any) -> bool:
            return isinstance(value, (int, float)) and not isinstance(value, bool) and value != 0

        if spec.pricing is None and (declared(spec.input_cost_per_million) or declared(spec.output_cost_per_million)):
            _logger.warning(
                "Model %r sets input_cost_per_million/output_cost_per_million without a 'pricing' block; "
                "those legacy values are not price evidence and the router ignores them. "
                "Declare 'pricing' with a source and a validity window.", spec.id,
            )

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def routing_profile_for(self, workflow_id: str, step_id: str) -> tuple[str | None, dict[str, Any] | None]:
        """Return only an explicitly configured profile assignment for this workflow step."""
        workflow = self.routing_assignments.get(workflow_id, {})
        profile_id = workflow.get(step_id) if isinstance(workflow, dict) else None
        if profile_id is None:
            return None, None
        profile_id = str(profile_id)
        profile = self.routing_profiles.get(profile_id)
        return profile_id, dict(profile) if isinstance(profile, dict) else None

    def context_profile_for(self, workflow_id: str) -> tuple[str | None, dict[str, Any] | None]:
        """The context profile assigned to a workflow as ``(profile_id, profile)``, or ``(None, None)``.

        Only an explicit assignment counts, as for routing: with none, compaction is off and prompts are exactly
        what they were. The profile is returned even when it is ``enabled: false`` (callers check ``enabled``); a
        copy, so a caller cannot change the catalog. ``tasks`` is reserved for task envelopes
        (``context_profile_for_tasks``) and is never a workflow id here. The profile's ``summary`` mapping is what
        ``SummaryPolicy.from_profile`` takes.
        """
        if workflow_id == CONTEXT_TASKS_ASSIGNMENT:
            return None, None
        return self._context_profile(self.context_assignments.get(workflow_id))

    def context_profile_for_tasks(self) -> tuple[str | None, dict[str, Any] | None]:
        """The context profile assigned to task envelopes (``assignments.tasks``), same shape as ``context_profile_for``."""
        return self._context_profile(self.context_assignments.get(CONTEXT_TASKS_ASSIGNMENT))

    def _context_profile(self, profile_id: Any) -> tuple[str | None, dict[str, Any] | None]:
        if profile_id is None:
            return None, None
        profile = self.context_profiles.get(str(profile_id))
        return str(profile_id), (copy.deepcopy(profile) if isinstance(profile, dict) else None)

    def model(self, model_id: str) -> ModelSpec:
        try:
            return self.models[model_id]
        except KeyError as exc:
            raise KeyError(f"Model is not configured: {model_id}") from exc

    def context_window(self, model_id: str) -> dict[str, Any] | None:
        """The model's context window as ``{tokens, source, checked_at, provenance, model}`` (plus ``note``).

        ``None`` means the window is unknown: the model is not in the catalog, declares no evidence, or the
        evidence is not usable. Callers must treat ``None`` as unknown and never substitute a number.
        """
        spec = self.models.get(model_id)
        return usable_context_window(spec.context_window) if spec is not None else None
