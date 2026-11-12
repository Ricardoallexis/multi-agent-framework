"""In-memory tool declarations and structural JSON validation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from pydantic import BaseModel, ValidationError

from multiagent.tasks.envelopes import envelope_sha256
from .contracts import ImpactClass, InvocationKind, ToolCallContext, ToolSpec, spec_sha256

_SEGMENT = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")


class ToolExecutor(Protocol):
    def __call__(self, arguments: Mapping[str, Any], context: ToolCallContext, *, should_cancel: Callable[[], bool]) -> Any: ...


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    spec_sha256: str
    executor: ToolExecutor | None
    input_model: type[BaseModel] | None
    output_model: type[BaseModel] | None


class ToolRegistrationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ToolLookupError(LookupError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})


def validate_structure(value: Any, schema: dict[str, Any], path: str = "$") -> str | None:
    """Return the first failed path for the supported, closed structural subset."""
    if schema is True:
        return None
    if not isinstance(schema, dict):
        return path
    kind = schema.get("type")
    if kind is not None and (not isinstance(kind, str) or kind not in _TYPES):
        return path
    if kind == "object":
        if not isinstance(value, dict):
            return path
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                return f"{path}.{name}"
        for name, item in value.items():
            if name not in properties:
                if schema.get("additionalProperties", True) is False:
                    return f"{path}.{name}"
            else:
                problem = validate_structure(item, properties[name], f"{path}.{name}")
                if problem:
                    return problem
    elif kind == "array":
        if not isinstance(value, list):
            return path
        for index, item in enumerate(value):
            problem = validate_structure(item, schema.get("items", {}), f"{path}[{index}]")
            if problem:
                return problem
    elif kind == "string" and not isinstance(value, str):
        return path
    elif kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return path
    elif kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return path
    elif kind == "boolean" and not isinstance(value, bool):
        return path
    elif kind == "null" and value is not None:
        return path
    if "enum" in schema and value not in schema["enum"]:
        return path
    return None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[tuple[str, int], RegisteredTool] = {}
        self._owners: dict[str, str] = {}
        self._frozen = False

    def register(self, spec: ToolSpec, executor: ToolExecutor | None = None, *,
                 input_model: type[BaseModel] | None = None,
                 output_model: type[BaseModel] | None = None) -> RegisteredTool:
        if self._frozen:
            raise ToolRegistrationError("registry_frozen")
        try:
            spec = ToolSpec.model_validate(spec.model_dump(mode="json") if isinstance(spec, ToolSpec) else spec)
        except (ValidationError, AttributeError, TypeError, ValueError) as exc:
            raise ToolRegistrationError("invalid_spec") from exc
        if spec.invocation == InvocationKind.PROVIDER_HOSTED:
            if executor is not None:
                raise ToolRegistrationError("unexpected_executor")
        elif executor is None:
            raise ToolRegistrationError("missing_executor")
        if spec.input_schema.json_schema.get("type") != "object" or spec.input_schema.json_schema.get("additionalProperties") is not False:
            raise ToolRegistrationError("schema_mismatch")
        for model, ref in ((input_model, spec.input_schema), (output_model, spec.output_schema)):
            if model is not None and envelope_sha256(model.model_json_schema()) != ref.sha256:
                raise ToolRegistrationError("schema_mismatch")
        owner = self._owners.get(spec.tool_id)
        if owner is not None and owner != spec.declared_by:
            raise ToolRegistrationError("namespace_conflict")
        digest = spec_sha256(spec)
        key = (spec.tool_id, spec.version)
        existing = self._tools.get(key)
        if existing:
            if existing.spec_sha256 != digest:
                raise ToolRegistrationError("version_conflict")
            return existing
        item = RegisteredTool(spec, digest, executor, input_model, output_model)
        self._tools[key] = item
        self._owners[spec.tool_id] = spec.declared_by
        return item

    def scoped(self, namespace: str) -> "ScopedRegistrar":
        return ScopedRegistrar(self, namespace)

    def lookup(self, tool_id: str, version: int | None = None) -> RegisteredTool:
        versions = [v for (name, v) in self._tools if name == tool_id]
        if not versions:
            raise ToolLookupError("unknown_tool")
        key = (tool_id, max(versions) if version is None else version)
        if key not in self._tools:
            raise ToolLookupError("version_not_found")
        return self._tools[key]

    def list(self, *, capability: str | None = None, impact: ImpactClass | None = None,
             namespace: str | None = None) -> tuple[ToolSpec, ...]:
        specs = (entry.spec for entry in self._tools.values())
        return tuple(sorted((s for s in specs if (capability is None or capability in s.capabilities)
                             and (impact is None or impact in s.impacts)
                             and (namespace is None or s.tool_id.startswith(namespace + "."))),
                            key=lambda s: (s.tool_id, s.version)))

    def unregister(self, tool_id: str, version: int) -> bool:
        if self._frozen:
            raise ToolRegistrationError("registry_frozen")
        removed = self._tools.pop((tool_id, version), None) is not None
        if removed and not any(name == tool_id for name, _ in self._tools):
            self._owners.pop(tool_id, None)
        return removed

    def freeze(self) -> None:
        self._frozen = True


class ScopedRegistrar:
    def __init__(self, registry: ToolRegistry, namespace: str) -> None:
        if not _SEGMENT.fullmatch(namespace):
            raise ToolRegistrationError("namespace_conflict")
        self._registry = registry
        self.namespace = namespace

    def register(self, spec: ToolSpec, executor: ToolExecutor | None = None, **kwargs: Any) -> RegisteredTool:
        if not spec.tool_id.startswith(self.namespace + ".") or spec.declared_by != "adapter":
            raise ToolRegistrationError("namespace_conflict")
        return self._registry.register(spec, executor, **kwargs)
