# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The existing workflow engine exposed through the execution backend contract."""
from __future__ import annotations

from ..workflow_engine import WorkflowEngine
from .base import BackendContext


class NativeBackend(WorkflowEngine):
    name = "native"
    capabilities = frozenset({
        "usage", "budgets", "cancellation", "human_steps", "review", "recovery",
        "approval_deadlines", "routing", "tools", "step_cache", "context_compaction",
        "document_barrier", "budget_guard", "inbox", "external_output",
    })


def build_native_backend(context: BackendContext) -> NativeBackend:
    """Build the native engine from the shared backend context."""
    if context.router is None:
        raise ValueError("The native execution backend requires a router")
    allowed = {"document_policy", "step_cache", "tool_selection"}
    unknown = set(context.options) - allowed
    if unknown:
        raise ValueError(f"Unknown native backend options: {', '.join(sorted(unknown))}")
    return NativeBackend(
        store=context.store, catalog=context.catalog, prompts=context.prompts,
        workflows=context.workflows, router=context.router, artifacts=context.artifacts,
        settings=context.settings, output_schemas=context.output_schemas,
        approval_timeout_seconds=context.approval_timeout_seconds,
        tool_registry=context.tool_registry, **context.options,
    )
