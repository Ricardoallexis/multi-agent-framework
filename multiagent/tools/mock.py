"""Synthetic local tools for consumer and permission tests."""
from __future__ import annotations

from typing import Any, Callable

from multiagent.document_actions import ActionKind
from multiagent.tasks.envelopes import envelope_sha256
from .contracts import EffectDecl, ImpactClass, InvocationKind, SchemaRef, ToolCallContext, ToolSpec
from .registry import ToolRegistry


def _ref(name: str, schema: dict[str, Any]) -> SchemaRef:
    return SchemaRef(schema_id=name, json_schema=schema, sha256=envelope_sha256(schema))


def register_mock_tools(registry: ToolRegistry) -> None:
    echo_in = {"type": "object", "properties": {"text": {"type": "string"}},
               "required": ["text"], "additionalProperties": False}
    echo_out = {"type": "object", "properties": {"text": {"type": "string"}},
                "required": ["text"], "additionalProperties": False}
    registry.register(ToolSpec(tool_id="mock.echo", version=1, title="Echo", invocation=InvocationKind.LOCAL,
                               impacts=frozenset({ImpactClass.READ}), input_schema=_ref("mock.echo.input/1", echo_in),
                               output_schema=_ref("mock.echo.output/1", echo_out), declared_by="builtin"),
                      lambda arguments, context, *, should_cancel: {"text": arguments["text"]})
    write_in = {"type": "object", "properties": {"target": {"type": "string"},
                                                  "text": {"type": "string"}},
                "required": ["target", "text"], "additionalProperties": False}
    registry.register(ToolSpec(tool_id="mock.note_write", version=1, title="Write note",
                               invocation=InvocationKind.LOCAL, impacts=frozenset({ImpactClass.WRITE}),
                               effects=(EffectDecl(kind=ActionKind.WRITE, target_from="/target"),),
                               input_schema=_ref("mock.note_write.input/1", write_in),
                               output_schema=_ref("mock.note_write.output/1", echo_out), declared_by="builtin"),
                      lambda arguments, context, *, should_cancel: {"text": arguments["text"]})
