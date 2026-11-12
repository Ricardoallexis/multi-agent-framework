"""Fail-closed tool permission checks independent of document barrier settings."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from multiagent.document_actions import Action, authorize
from multiagent.tasks.envelopes import envelope_sha256
from .contracts import ActionRef, ImpactClass, PermissionRequest, ToolRequest, ToolSpec


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
    source = f"{request.request_id}|{spec.tool_id}|{spec.version}|{envelope_sha256([r.model_dump(mode='json') for r in refs])}"
    identifier = "tpr_" + hashlib.sha256(source.encode()).hexdigest()[:32]
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
    decisions = [authorize(action, evidence) for action in actions]
    if ImpactClass.DESTRUCTIVE in spec.impacts or any(not item.allowed for item in decisions):
        reason = next((item.reason for item in decisions if not item.allowed), "destructive approval required")
        return "approval_required", permission_request_for(spec, request, actions, reason)
    return None, None
