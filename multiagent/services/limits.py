# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""One limit notice for new messages, executor launches and failed runs."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


LIMIT_REASONS = frozenset({"limit_reached", "quota_paused", "quota_below_pause_floor",
                           "quota_below_launch_floor"})
LIMIT_ERRORS = frozenset({"QuotaExceeded", "RateLimitExceeded"})
APP_OF = {"claude_cli": "claude", "codex_cli": "codex", "copilot_cli": "copilot",
          "antigravity_cli": "antigravity"}


def _instant(value: Any) -> str | None:
    if isinstance(value, datetime):
        value = value.astimezone(timezone.utc).isoformat() if value.tzinfo else None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo else None


def limit_notice(services: Any, target: str, *, retry_at: Any = None,
                 rows: Iterable[dict[str, Any]] | None = None, provider: str | None = None) -> dict[str, Any]:
    """Return a JSON-ready notice. A reset is reported only from observed evidence."""
    if rows is None:
        rows = services.connections.list()["connections"]
    rows = list(rows)
    by_id = {row.get("id"): row for row in rows}
    provider = provider or target
    if target not in by_id:
        try:
            provider = services.system.catalog.model(target).provider
        except (AttributeError, KeyError):
            pass
    row = by_id.get(target) or by_id.get(provider) or {}
    resets_at = _instant(retry_at) or _instant(row.get("resets_at")) or _instant(row.get("paused_until"))
    if resets_at is None:
        windows = (row.get("quota") or {}).get("windows") or []
        binding = [item for item in windows if item.get("state") == "limit_reached"
                   or isinstance(item.get("used_percent"), (float, int)) and item["used_percent"] >= 100]
        resets_at = next((_instant(item.get("resets_at")) for item in binding if _instant(item.get("resets_at"))), None)
    if resets_at is None and provider in APP_OF:
        try:
            entry = next((item for item in services.usage.plans()["apps"] if item.get("app") == APP_OF[provider]), {})
            resets_at = next((_instant(item.get("resets_at")) for item in entry.get("windows", [])
                              if item.get("state") == "limit_reached" and _instant(item.get("resets_at"))), None)
        except Exception:
            pass
    kind = row.get("kind") or ("executor" if target in APP_OF else "model")
    eligible = [item for item in rows if item.get("available") is True and item.get("enabled") is True
                and item.get("id") not in {target, provider}]
    eligible.sort(key=lambda item: item.get("kind") != kind)
    return {"code": "limit_reached", "resets_at": resets_at, "target": target,
            "suggestions": [item["id"] for item in eligible[:3]]}


def require_cloud_confirmation(request: Any, catalog: Any) -> None:
    """Validate the person's cloud choice before a run is stored."""
    from ..contracts import ExecutionMode, ModelTier
    from .errors import ServiceError

    selected_cloud = request.execution_mode is ExecutionMode.CLOUD
    for choice in request.routing_preferences.values():
        if choice.model:
            try:
                selected_cloud |= ModelTier(catalog.model(choice.model).tier) is not ModelTier.LOCAL
            except KeyError:
                pass  # The existing preference validator reports an unknown model.
    if request.confirm_cloud and request.sensitive:
        raise ServiceError("cloud_confirmation_required", "Sensitive data cannot be sent to a cloud model.", status=422)
    if selected_cloud and not request.confirm_cloud:
        raise ServiceError("cloud_confirmation_required", "Confirm cloud use before creating this run.", status=422)


def require_fixed_models_allowed(request: Any, workflow: Any, catalog: Any) -> None:
    """Check fixed workflow model choices before they can bypass routing permissions."""
    from ..contracts import ExecutionMode
    from .errors import INVALID_DEFINITION, ServiceError

    for step in workflow.steps:
        if step.function is not None:
            continue  # a function step keeps its own error for run options (function_step_option_invalid)
        mode = request.step_modes.get(step.id, request.execution_mode)
        if mode is ExecutionMode.CLOUD and step.id in request.step_modes and not request.confirm_cloud:
            raise ServiceError(
                "cloud_confirmation_required",
                f"Confirm cloud use before step {step.id} runs in the cloud.",
                status=422,
                details={"step": step.id},
            )
        profile_id, _ = catalog.routing_profile_for(request.workflow_id, step.id)
        if profile_id is not None or step.id in request.routing_preferences:
            continue
        if mode in (ExecutionMode.HUMAN_GUIDED, ExecutionMode.CLOUD):
            continue

        if mode is ExecutionMode.LOCAL or (mode is ExecutionMode.AUTO and request.sensitive):
            model_id = "local_reasoning" if step.id == "strategy" else "local_default"
            spec = catalog.models.get(model_id)
            if spec is not None and str(spec.tier).casefold() != "local":
                raise ServiceError(
                    INVALID_DEFINITION,
                    f"Step {step.id} requires local model {model_id!r}, but that model is not local.",
                    status=422,
                    details={"reason": "local_model_not_local", "step": step.id, "model": model_id},
                )
            continue
        # ``auto`` without sensitive data keeps the workflow's own ``preferred_model`` and ``fallback_model`` as in 1.0
        # (``social_post`` falls back to ``cloud_fast``): whether that needs the confirmation is the user's decision.
