# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Execution backends (roadmap S4): the interface, its contract and the registry by name. See ``docs/BACKENDS.md``."""
from .base import (
    CAPABILITY_DESCRIPTIONS,
    CAPABILITY_EVENTS,
    KNOWN_CAPABILITIES,
    REQUIRED_CAPABILITIES,
    REQUIRED_EVENTS,
    BackendContext,
    ExecutionBackend,
    backend_problems,
)
from .registry import (
    BUILTIN_BACKENDS,
    DEFAULT_BACKEND,
    BackendFactory,
    InvalidBackend,
    UnknownBackend,
    available_backends,
    build_backend,
    register_backend,
    unregister_backend,
)
from .telemetry import attach_telemetry

__all__ = [
    "BUILTIN_BACKENDS",
    "CAPABILITY_DESCRIPTIONS",
    "CAPABILITY_EVENTS",
    "DEFAULT_BACKEND",
    "KNOWN_CAPABILITIES",
    "REQUIRED_CAPABILITIES",
    "REQUIRED_EVENTS",
    "BackendContext",
    "BackendFactory",
    "ExecutionBackend",
    "InvalidBackend",
    "UnknownBackend",
    "available_backends",
    "attach_telemetry",
    "backend_problems",
    "build_backend",
    "register_backend",
    "unregister_backend",
]
