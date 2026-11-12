"""Provider-neutral mapping surface and a deterministic Mock mapper."""
from __future__ import annotations

from typing import Any, Protocol

from multiagent.document_actions import ActionKind
from multiagent.executors.contract import Permission
from multiagent.tasks.envelopes import envelope_sha256
from .contracts import (EffectDecl, ImpactClass, InvocationKind, SchemaRef, ToolCallContext,
                        ToolError, ToolErrorCode, ToolOrigin, ToolRequest, ToolResult, ToolSpec,
                        request_id_for)


class ToolCallMapper(Protocol):
    def definitions(self, specs: tuple[ToolSpec, ...]) -> list[dict[str, Any]]: ...
    def parse_calls(self, raw: Any, *, context: ToolCallContext,
                    origin: ToolOrigin) -> tuple[list[ToolRequest], list[ToolResult]]: ...


def impacts_for_permission(permission: Permission) -> frozenset[ImpactClass]:
    return frozenset({ImpactClass.READ if permission == Permission.READ else ImpactClass.WRITE})


def provider_hosted_spec(tool_id: str = "web.search", *, title: str = "Web search",
                         version: int = 1) -> ToolSpec:
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    output_schema = {"type": "object"}
    return ToolSpec(tool_id=tool_id, version=version, title=title, invocation=InvocationKind.PROVIDER_HOSTED,
                    impacts=frozenset({ImpactClass.EXTERNAL}), effects=(EffectDecl(kind=ActionKind.NETWORK),),
                    input_schema=SchemaRef(schema_id=f"{tool_id}/1", json_schema=input_schema,
                                           sha256=envelope_sha256(input_schema)),
                    output_schema=SchemaRef(schema_id=f"{tool_id}.result/1", json_schema=output_schema,
                                            sha256=envelope_sha256(output_schema)), declared_by="builtin")


class MockToolCallMapper:
    def definitions(self, specs: tuple[ToolSpec, ...]) -> list[dict[str, Any]]:
        return [{"name": spec.tool_id, "description": spec.description if spec.declared_by != "adapter" else "",
                 "parameters": spec.input_schema.json_schema} for spec in specs]

    def parse_calls(self, raw: Any, *, context: ToolCallContext,
                    origin: ToolOrigin) -> tuple[list[ToolRequest], list[ToolResult]]:
        calls: list[ToolRequest] = []
        rejects: list[ToolResult] = []
        entries = raw.get("tool_calls", []) if isinstance(raw, dict) else []
        for index, item in enumerate(entries):
            identifier = request_id_for(context, index)
            try:
                if not isinstance(item, dict):
                    raise ValueError("Invalid call")
                request = ToolRequest(request_id=identifier, tool_id=item["name"],
                                      arguments=item["arguments"], context=context, origin=origin)
                calls.append(request)
            except (ValueError, TypeError, KeyError):
                rejects.append(ToolResult(request_id=identifier, tool_id="invalid.call", tool_version=None,
                                          spec_sha256=None, ok=False, provenance_ref=f"tool:{identifier}",
                                          error=ToolError(code=ToolErrorCode.INVALID_REQUEST,
                                                          message="Tool call invalid_request.", retryable=False)))
        return calls, rejects
