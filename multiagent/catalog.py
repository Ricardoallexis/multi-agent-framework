from __future__ import annotations

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

_logger = logging.getLogger(__name__)

MODEL_LEVELS = ("T1", "T2", "T3", "T4")
MODEL_CLASSIFICATIONS = ("classified", "unclassified")
LOCAL_NO_CHARGE = "local_no_charge"
_PRICE_COMPONENTS = ("input", "output")
_PRICE_FIELDS = ("amount", "denominator", "unit", "currency", "source", "valid_from", "valid_until", "provenance")
_PRICE_TEXT_FIELDS = ("unit", "currency", "source", "provenance")
_PRICE_WINDOW_FIELDS = ("valid_from", "valid_until")


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
        if result["input"][name] != result["output"][name]:
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
        components.append((float(amount), str(currency), int(denominator), str(unit)))
    if components[0][1:] != components[1][1:]:
        return None
    return components


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

    def __post_init__(self) -> None:
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
        self.routing_profiles = dict(routing.get("profiles", {}) or {})
        self.routing_assignments = dict(routing.get("assignments", {}) or {})
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
            spec = ModelSpec(id=model_id, **raw)
            self._warn_legacy_cost(spec)
            self.models[model_id] = spec

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

    def model(self, model_id: str) -> ModelSpec:
        try:
            return self.models[model_id]
        except KeyError as exc:
            raise KeyError(f"Model is not configured: {model_id}") from exc
