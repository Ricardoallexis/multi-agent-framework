"""Fail-closed tool permission checks independent of document barrier settings."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from multiagent.document_actions import Action, ActionKind, authorize
from multiagent.tasks.envelopes import envelope_sha256
from .contracts import (ActionRef, ImpactClass, PermissionRequest, ToolRequest, ToolSpec,
                        permission_request_id_for)


class InvalidArguments(ValueError):
    pass


def actions_for(spec: ToolSpec, arguments: dict[str, Any]) -> tuple[Action, ...]:
    actions = []
    for effect in spec.effects:
        target = effect.target
        if effect.target_from is not None:
            value: Any = arguments
            for segment in effect.target_from.split("/")[1:]:
                segment = segment.replace("~1", "/").replace("~0", "~")
                if not isinstance(value, dict) or segment not in value:
                    raise InvalidArguments("Effect target missing")
                value = value[segment]
            if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise InvalidArguments("Invalid effect target")
            target = value
        actions.append(Action(effect.kind, target))
    return tuple(actions)


def required_permissions(spec: ToolSpec) -> frozenset[str]:
    return spec.permissions_required | {f"tool:{spec.tool_id}"}


def permission_request_for(spec: ToolSpec, request: ToolRequest, actions: tuple[Action, ...], reason: str) -> PermissionRequest:
    refs = tuple(ActionRef.from_action(action) for action in actions)
    from .contracts import spec_sha256
    identifier = permission_request_id_for(request.request_id, spec.tool_id, spec.version, refs,
                                           envelope_sha256(request.arguments), spec_sha256(spec))
    destinations = ", ".join(a.target for a in actions if a.target)
    summary = (spec.title + (": " + destinations if destinations else ""))[:300]
    from .contracts import spec_sha256
    return PermissionRequest(permission_request_id=identifier, request_id=request.request_id,
                             tool_id=spec.tool_id, tool_version=spec.version, spec_sha256=spec_sha256(spec),
                             context=request.context, impacts=spec.impacts, actions=refs,
                             arguments_sha256=envelope_sha256(request.arguments), summary=summary,
                             reason=reason, origin=request.origin)


def check_call(spec: ToolSpec, request: ToolRequest, *, grants: Iterable[str] | None,
               evidence: Iterable[Any] | None = None) -> tuple[str | None, PermissionRequest | None]:
    """Return an error code and optional human request; no input content grants access."""
    if grants is None or not required_permissions(spec).issubset(set(grants)):
        return "not_granted", None
    if ImpactClass.EXTERNAL in spec.impacts and (spec.allowed_data_classes is None or
                                                 request.context.data_class not in spec.allowed_data_classes):
        return "data_class_not_allowed", None
    actions = actions_for(spec, request.arguments)
    if ImpactClass.DESTRUCTIVE in spec.impacts and not any(
            action.kind is ActionKind.DESTRUCTIVE for action in actions):
        return "approval_required", permission_request_for(spec, request, actions, "destructive effect missing")
    from .approvals import ToolCallApproval
    ordinary = []
    per_call = []
    try:
        supplied = list(evidence) if evidence is not None else []
    except (TypeError, ValueError):
        return "approval_required", permission_request_for(spec, request, actions, "malformed approval evidence")
    for item in supplied:
        if type(item) is ToolCallApproval:
            per_call.append(item)
        else:
            ordinary.append(item)
    # A malformed or unknown item poisons the whole collection, as authorize does.
    if per_call:
        if len(per_call) != 1 or ordinary:
            return "approval_required", permission_request_for(spec, request, actions, "malformed approval evidence")
        grant = per_call[0]
        if (not grant.valid_for(spec, request)
                or tuple(ref.to_action() for ref in grant.permission.actions) != actions):
            return "approval_required", permission_request_for(spec, request, actions, "approval does not match this call")
        decisions = [authorize(action, (grant.approval,)) for action in actions]
    else:
        # Generic write approvals, including approved contracts, never cover destructive actions.
        decisions = [authorize(action, ordinary if evidence is not None else None) for action in actions]
        if any(action.kind is ActionKind.DESTRUCTIVE for action in actions):
            return "approval_required", permission_request_for(spec, request, actions, "per-call destructive approval required")
    if any(not item.allowed for item in decisions):
        reason = next((item.reason for item in decisions if not item.allowed), "destructive approval required")
        return "approval_required", permission_request_for(spec, request, actions, reason)
    return None, None
