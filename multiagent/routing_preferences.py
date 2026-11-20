"""Run-scoped ECO preferences: pure validation, without dispatch or catalog changes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from .catalog import MODEL_LEVELS, validate_routing_profile
from .contracts import ExecutionMode
from .routing_policy import RoutingRequest
from .run_request import FunctionStepRequestError, validate_function_step_options


def resolve_profile(catalog, workflow_id, step_id, kind, preference):
    profile_id, profile = catalog.routing_profile_for(workflow_id, step_id)
    if not preference:
        return profile_id, profile
    profile_id = preference.get("profile_id") or profile_id
    profile = deepcopy(catalog.routing_profiles.get(profile_id))
    if not isinstance(profile, dict) or profile.get("enabled") is not True:
        raise ValueError(f"Step {step_id}: an enabled ECO profile is required")
    if preference.get("level"):
        level = preference["level"]
        bounds = profile.get("levels", {}).get(kind, {})
        if (MODEL_LEVELS.index(level) < MODEL_LEVELS.index(bounds.get("min", "T1"))
                or MODEL_LEVELS.index(level) > MODEL_LEVELS.index(bounds.get("max", "T4"))):
            raise ValueError(f"Step {step_id}: level is outside the ECO profile bounds")
        profile.setdefault("levels", {})[kind] = {"min": level, "max": level}
    if preference.get("mode"):
        profile["mode"] = preference["mode"]
    if preference.get("model"):
        model = preference["model"]
        allowed = profile.get("candidate_model_ids")
        if model not in catalog.models or (allowed is not None and model not in allowed):
            raise ValueError(f"Step {step_id}: model is outside the ECO profile")
        if profile.get("mode") == "single" and profile.get("model_id") not in (None, model):
            raise ValueError(f"Step {step_id}: single profile fixes another model")
        profile["candidate_model_ids"] = [model]
        if profile.get("mode") == "single":
            profile["model_id"] = model
    return profile_id, validate_routing_profile(profile_id, profile)


def preference_request(engine, workflow_id, step, inputs, execution_mode, sensitive, preference,
                       *, effective_permissions=None, authorized_data_classes=None):
    if step.function is not None:
        raise FunctionStepRequestError(step.id)
    profile_id, profile = resolve_profile(engine.catalog, workflow_id, step.id, step.kind, preference)
    requires_web = (str(inputs.get("requires_web", "")).strip().casefold() in {"true", "1", "yes", "sí", "si"}
                    if step.requires_web == "from_request" else step.requires_web is True)
    binding, _, _ = engine._routing_for_step(step=step, execution_mode=execution_mode,
                                           requires_web=requires_web, sensitive=sensitive)
    request = RoutingRequest(
        profile_id=profile_id, workflow_id=workflow_id, step_id=step.id,
        requested_model=preference.get("model") or (profile or {}).get("requested_model")
                        or (binding.preferred_model if binding else None),
        required_capabilities=frozenset({"structured_output"} | ({"web"} if requires_web else set())),
        required_role=step.agent, required_permissions=frozenset((profile or {}).get("required_permissions") or ()),
        effective_permissions=frozenset(effective_permissions) if effective_permissions is not None else None,
        authorized_data_classes=frozenset(authorized_data_classes) if authorized_data_classes is not None else None,
        data_class="sensitive" if sensitive else "non_sensitive", execution_mode=execution_mode.value,
        requires_web=requires_web, output_schema=step.contract, step_kind=step.kind,
        effort=preference.get("effort") if "effort" in preference else step.effort,
    )
    return request, profile


def validate_run_preferences(engine, request, definition):
    validate_function_step_options(request, definition)
    preferences = getattr(request, "routing_preferences", {})
    by_id = {step.id: step for step in definition.steps}
    for step_id, choice in preferences.items():
        if step_id not in by_id:
            raise ValueError(f"Unknown routing step: {step_id}")
        preference = choice.model_dump(exclude_unset=True) if hasattr(choice, "model_dump") else choice
        if not preference:
            continue
        mode = request.step_modes.get(step_id, request.execution_mode)
        step = by_id[step_id]
        routing, profile = preference_request(engine, request.workflow_id, step, request.inputs,
                                             mode, request.sensitive, preference,
                                             effective_permissions=request.effective_permissions,
                                             authorized_data_classes=request.authorized_data_classes)
        # Human-guided execution must not bypass validation of model choices.
        check = replace(routing, execution_mode=ExecutionMode.AUTO.value) if mode is ExecutionMode.HUMAN_GUIDED else routing
        # Switching to single mode does not bypass the original level/candidate policy.
        base_id = preference.get("profile_id") or engine.catalog.routing_profile_for(request.workflow_id, step_id)[0]
        base_profile = engine.catalog.routing_profiles[base_id]
        if profile.get("mode") == "single" and base_profile.get("mode") != "single":
            baseline = deepcopy(profile)
            baseline["mode"] = base_profile.get("mode", "supervised")
            baseline.pop("model_id", None)
            prior = engine.router.select(check, profile_override=baseline)
            if prior.status != "selected":
                raise ValueError(f"Step {step_id}: model is outside the original ECO policy")
        decision = engine.router.select(check, profile_override=profile)
        if decision.status != "selected":
            raise ValueError(f"Step {step_id}: ECO refused preferences ({', '.join(decision.reason_codes)})")
        if preference.get("model") and decision.selected_model != preference["model"]:
            raise ValueError(f"Step {step_id}: ECO did not select the requested model")
        if preference.get("level") and decision.selected_level != preference["level"]:
            raise ValueError(f"Step {step_id}: ECO did not select the requested level")
