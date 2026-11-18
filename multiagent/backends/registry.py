# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Execution backends by name (roadmap S4.A1).

The built-in backends are declared here as ``module:function`` and imported only when they are built, so importing
the registry never imports the engine or an adapter. A host or a test registers its own with ``register_backend``.
Every backend that comes out of ``build_backend`` has passed ``backend_problems``.
"""
from __future__ import annotations

import importlib
from typing import Callable

from ..errors import MultiAgentError
from .base import BackendContext, ExecutionBackend, backend_problems
from .telemetry import attach_telemetry

BackendFactory = Callable[[BackendContext], ExecutionBackend]

# Built-in backends: the module and the factory function that builds each one (BKD-T02 and BKD-T03 add them).
BUILTIN_BACKENDS: dict[str, str] = {
    "native": "multiagent.backends.native:build_native_backend",
    "mock": "multiagent.backends.mock:build_mock_backend",
}
DEFAULT_BACKEND = "native"

_registered: dict[str, BackendFactory] = {}


class UnknownBackend(MultiAgentError):
    """No backend has that name, or its module is not available. The message lists the available ones."""


class InvalidBackend(MultiAgentError):
    """A factory returned an object that does not meet the backend contract (``backend_problems``)."""


def register_backend(name: str, factory: BackendFactory, *, replace: bool = False) -> None:
    """Register ``factory`` under ``name``. A taken name (built-in or registered) needs ``replace=True``."""
    if not name or not name.replace("_", "").replace("-", "").isalnum() or name != name.lower():
        raise ValueError("A backend name is lowercase letters, digits, '-' or '_'")
    if not replace and (name in _registered or name in BUILTIN_BACKENDS):
        raise ValueError(f"A backend named {name!r} already exists; pass replace=True to replace it")
    _registered[name] = factory


def unregister_backend(name: str) -> None:
    """Remove a backend registered with ``register_backend`` (built-ins stay declared)."""
    _registered.pop(name, None)


def _builtin_factory(name: str) -> BackendFactory | None:
    target = BUILTIN_BACKENDS.get(name)
    if target is None:
        return None
    module_name, function_name = target.split(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:  # the backend exists but one of its own imports is missing: that is a real fault
            raise
        return None
    return getattr(module, function_name)


def _factory(name: str) -> BackendFactory | None:
    return _registered.get(name) or _builtin_factory(name)


def available_backends() -> tuple[str, ...]:
    """The names ``build_backend`` can build now: registered ones and built-ins whose module is installed."""
    names = set(_registered)
    names.update(name for name in BUILTIN_BACKENDS if name not in _registered and _builtin_factory(name) is not None)
    return tuple(sorted(names))


def build_backend(name: str, context: BackendContext) -> ExecutionBackend:
    """Build the backend called ``name`` with ``context``; ``UnknownBackend`` or ``InvalidBackend`` otherwise."""
    factory = _factory(name)
    if factory is None:
        available = ", ".join(available_backends()) or "none"
        raise UnknownBackend(f"Unknown execution backend {name!r}; available: {available}")
    backend = factory(context)
    problems = backend_problems(backend, name=name)
    if problems:
        raise InvalidBackend(f"Execution backend {name!r} does not meet the contract: {'; '.join(problems)}")
    return attach_telemetry(backend, context.settings, store=context.store)
