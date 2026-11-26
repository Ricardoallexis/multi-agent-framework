# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic concurrency and model-level policy for an active user mode."""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from ..catalog import MODEL_LEVELS, current_price
from ..routing_policy import STEP_KINDS

MODES = ("eco", "normal", "performance", "maximum")
MARGINS = (3, 5, 10, 15, 20, 30, 40)
DEFAULT_MARGINS = {"eco": 30, "normal": 10, "performance": 3, "maximum": 3}
LEVEL_WEIGHTS = {"T1": 1.0, "T2": 2.0, "T3": 4.0, "T4": 8.0}
DEFAULT_STEP_COST = {"codex": 5.0, "claude": 5.0, "copilot": 5.0, "antigravity": 5.0}


def _number(value: Any) -> bool:
    """A real, finite number; bool, NaN and inf are not measurements."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _instant(value: Any) -> datetime | None:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def active_mode(config: Mapping[str, Any], *, now: datetime) -> str:
    """Temporary high-throughput modes revert to Normal after their deadline."""
    mode = config.get("mode", "normal")
    if mode not in MODES:
        return "normal"
    if mode in {"performance", "maximum"}:
        deadline = _instant(config.get("expires_at"))
        if deadline is not None and now >= deadline:
            return "normal"
    return mode


def effective_profile(profile: Mapping[str, Any], mode: str | None, *, allow_t4: bool = False,
                      notes: list[str] | None = None) -> dict[str, Any]:
    """Compose level bounds without changing the catalog or the input profile.

    The result is never an invalid profile: the profile's own floor beats Eco's ceiling, and Maximum with a
    profile that demands T4 while ``allow_t4`` is off reports ``t4_not_allowed`` in ``notes``. A ``single``
    profile names one model and is left alone.
    """
    result = dict(profile)
    if mode not in {"eco", "maximum"} or profile.get("mode") == "single":
        return result
    notes = notes if notes is not None else []
    existing = profile.get("levels") if isinstance(profile.get("levels"), dict) else {}
    levels = {kind: dict(existing.get(kind, {})) for kind in STEP_KINDS}
    index = MODEL_LEVELS.index
    for kind, bounds in levels.items():
        if mode == "eco":
            if kind == "work":
                ceiling = min((bounds.get("max", "T2"), "T2"), key=index)
                if "min" in bounds and index(bounds["min"]) > index(ceiling):
                    ceiling = bounds["min"]  # the profile's floor wins over Eco's ceiling
                    notes.append("eco_ceiling_raised_to_profile_floor")
                bounds["max"] = ceiling
            elif "min" not in bounds:
                top = bounds.get("max")
                bounds["min"] = top if top is not None and index(top) < index("T3") else "T3"
                if bounds["min"] != "T3":
                    notes.append("eco_floor_lowered_to_profile_ceiling")
        else:
            bounds["min"] = max((bounds.get("min", "T3"), "T3"), key=index)
            if bounds["min"] == "T4" and not allow_t4:
                notes.append("t4_not_allowed")
            bounds["max"] = "T4" if allow_t4 else "T3"
    result["levels"] = levels
    if mode == "eco":
        result["economic_selection"] = True
    return result


def calculate_auto(plans: Mapping[str, Any], *, mode: str, margin: int, cost_per_step: Mapping[str, float],
                   manual_limit: int, machine_limit: int, now: datetime) -> dict[str, Any]:
    """The tightest known quota window sets the cap; no quota data permits one slot."""
    if mode not in MODES or margin not in MARGINS or min(manual_limit, machine_limit) < 1:
        raise ValueError("Invalid concurrency policy")
    choices = []
    for app in plans.get("apps", []):
        if not isinstance(app, Mapping):
            continue
        name = str(app.get("app", ""))
        cost = cost_per_step.get(name)
        if not _number(cost) or cost <= 0:
            continue
        for window in app.get("windows", []):
            if not isinstance(window, Mapping):
                continue
            used, reset = window.get("used_percent"), _instant(window.get("resets_at"))
            if not _number(used) or not 0 <= used <= 100 or reset is None or reset <= now:
                continue
            free = max(0.0, 100.0 - used - margin)
            steps = math.floor(free / cost)
            blocks = max(1, math.ceil((reset - now).total_seconds() / 900))
            if steps == 0:
                pace = 0  # the margin is already reached: the reserve is not spent in any mode
            else:
                pace = max(1, math.floor(steps / blocks)) if mode in {"eco", "normal"} else steps
            cap = min(pace, manual_limit, machine_limit)
            choices.append((cap, name, window.get("window", ""), free, blocks, steps))
    if not choices:
        return {"limit": 1, "reason": "no_quota_data", "code": "no_quota_data"}
    cap, name, window, free, blocks, steps = min(choices)
    if steps == 0:
        return {"limit": 0, "reason": f"{name} ({window}): the {margin}% margin is reached, no new step starts",
                "code": "quota_margin_reached", "app": name, "window": window}
    return {"limit": cap, "reason": f"{name} ({window}): {free:g}% free after {margin}% margin, {blocks} blocks -> {cap} concurrent",
            "code": "quota_window", "app": name, "window": window}


def economic_measure(spec: Any, *, now: datetime, observations: Mapping[str, Any] | None = None) -> tuple[str, float] | None:
    """Cost within a provider's native unit; unlike units are never converted."""
    observations = observations or {}
    provider = str(spec.provider).casefold()
    if str(spec.tier).casefold() == "local":
        return "local_no_charge", 0.0
    observed = observations.get(spec.id, {})
    if provider in {"gemini", "openai"}:
        price = current_price(spec.pricing, now=now)
        return ("usd_per_token", (price[0][0] + price[1][0]) / price[0][2]) if price and price[0][1] == "USD" else None
    if provider in {"copilot", "copilot_cli"}:
        for key in ("premium_interaction", "ai_credit"):
            if isinstance(observed, Mapping) and _number(observed.get(key)) and observed[key] >= 0:
                return key, float(observed[key])
        return None
    if provider in {"codex", "codex_cli", "claude", "claude_cli"}:
        value = observed.get("quota_percent_per_step") if isinstance(observed, Mapping) else None
        return "quota_percent_per_step", float(value) if _number(value) and value >= 0 else LEVEL_WEIGHTS.get(spec.level, math.inf)
    if provider in {"agy", "antigravity", "antigravity_cli"}:
        free = observed.get("bag_free_percent") if isinstance(observed, Mapping) else None
        return ("bag_free_percent", -float(free)) if _number(free) and 0 <= free <= 100 else None
    return None


def resolve_limit(*, gui: int | None, environment: int | None, cli: int | None,
                  app: int | None = None, model: int | None = None) -> tuple[int | None, str]:
    """CLI wins over WORKER_* and GUI; the smallest target cap wins after source precedence."""
    selected = cli if cli is not None else environment if environment is not None else gui
    available = [(name, value) for name, value in (("total", selected), ("app", app), ("model", model)) if value is not None]
    if any(type(value) is not int or value < 1 for _, value in available):
        raise ValueError("Limits must be positive integers")
    if not available:
        return None, "unlimited"
    name, result = min(available, key=lambda pair: pair[1])
    return result, f"{name}_limit"


class ConcurrencyServices:
    """Effective GUI policy and its explanation, with environment and CLI precedence."""

    def __init__(self, services: Any):
        self.services = services

    def routing_options(self, *, now: datetime | None = None) -> dict[str, Any]:
        """The explicit arguments a future caller passes to ModelRouter.select at step start."""
        config = self.services.gui_settings.concurrency(now=now)
        return {"concurrency_mode": config["mode"], "allow_t4": config["allow_t4"],
                "confirmed_levels": self.services.gui_settings.read().get("model_levels", {})}

    def effective(self, *, app: str | None = None, model: str | None = None,
                  cli_limit: int | None = None, cost_per_step: Mapping[str, float] | None = None,
                  now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        config = self.services.gui_settings.concurrency(now=now)
        settings = self.services.settings
        fields = getattr(settings, "model_fields_set", set())
        env_limit = settings.worker_concurrency if "worker_concurrency" in fields else None
        per_app = (settings.worker_max_active_per_app if "worker_max_active_per_app" in fields
                   else config["per_app"].get(app)) if app else None
        per_model = (settings.worker_max_active_per_model if "worker_max_active_per_model" in fields
                     else config["per_model"].get(model)) if model else None
        source_limit, source_reason = resolve_limit(gui=config["manual"], environment=env_limit, cli=cli_limit,
                                                     app=per_app, model=per_model)
        machine_limit = max(1, os.cpu_count() or 1)
        if config["selection"] == "manual":
            limit = min(source_limit or machine_limit, machine_limit)
            return {**config, "limit": limit, "reason": source_reason, "mode_reason": config["reason"]}
        plans = self.services.usage.plans()
        result = calculate_auto(plans, mode=config["mode"], margin=config["margin"],
                                cost_per_step=cost_per_step or DEFAULT_STEP_COST,
                                manual_limit=source_limit or 256, machine_limit=machine_limit, now=now)
        return {**config, **result, "limit_source": source_reason, "mode_reason": config["reason"]}
