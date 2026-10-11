"""Explicit executor adapter registry. Adapters describe; this registry never launches."""
from __future__ import annotations

from collections.abc import Mapping, Iterable
from typing import Any, Protocol, runtime_checkable

from ..routing.executor_capabilities import (
    CapabilityEvidence, ExecutorDescriptor, SupportState, descriptor_from_declaration,
    validate_executor_descriptor,
)


@runtime_checkable
class ExecutorAdapter(Protocol):
    adapter_id: str
    version: str

    def describe(self, config: Mapping[str, Any]) -> ExecutorDescriptor | Mapping[str, Any]:
        """Return a deterministic descriptor from trusted config; never launch or probe."""


class ExecutorRegistry:
    def __init__(self, config: Mapping[str, Any], adapters: Iterable[ExecutorAdapter] = ()):
        self.config = dict(config)
        self._declared = dict(self.config.get("executors", {}) or {})
        self._adapters: dict[str, ExecutorAdapter] = {}
        for adapter in adapters:
            self.register(adapter)
        self._descriptors: dict[str, ExecutorDescriptor] = {}
        self._build_snapshot()

    def register(self, adapter: ExecutorAdapter) -> None:
        if not getattr(adapter, "adapter_id", None) or not getattr(adapter, "version", None):
            raise ValueError("adapter_id and version are required")
        if adapter.adapter_id in self._adapters:
            raise ValueError(f"adapter already registered: {adapter.adapter_id}")
        self._adapters[adapter.adapter_id] = adapter
        self._build_snapshot()

    def resolve(self, executor_id: str) -> ExecutorAdapter | None:
        declaration = self._declared.get(executor_id)
        adapter_id = declaration.get("adapter_id") if isinstance(declaration, Mapping) else None
        return self._adapters.get(str(adapter_id)) if adapter_id else None

    def get(self, executor_id: str) -> ExecutorDescriptor | None:
        return self._descriptors.get(executor_id)

    def snapshot(self) -> Mapping[str, ExecutorDescriptor]:
        return dict(self._descriptors)

    def _build_snapshot(self) -> None:
        descriptors: dict[str, ExecutorDescriptor] = {}
        for executor_id, declaration in sorted(self._declared.items()):
            if not isinstance(declaration, Mapping):
                continue
            adapter = self.resolve(str(executor_id))
            if adapter is None:
                # No adapter: a manual descriptor. Declared evidence is carried for display and audit,
                # but a manual descriptor is never selected (the selector requires mode "automatic").
                descriptor = descriptor_from_declaration(str(executor_id), declaration)
            else:
                described = adapter.describe(dict(declaration))
                descriptor = validate_executor_descriptor(described)
                if descriptor.executor_id != executor_id:
                    raise ValueError("adapter descriptor executor_id differs from config")
                if descriptor.adapter_id not in (None, adapter.adapter_id):
                    raise ValueError("adapter descriptor adapter_id differs from registered adapter")
                # A declaration with ``enabled: false`` (or a non-boolean) always wins over the adapter.
                enabled = descriptor.enabled and declaration.get("enabled", True) is True
                descriptor = ExecutorDescriptor(
                    **{**descriptor.to_dict(), "adapter_id": adapter.adapter_id,
                       "adapter_version": str(adapter.version), "provenance": "adapter_declaration",
                       "enabled": enabled}
                )
            descriptors[str(executor_id)] = validate_executor_descriptor(descriptor)
        self._descriptors = descriptors
