# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The executor-application adapters that ship with the framework (ORQ-T48 Codex, T49 Claude, T50 Copilot).

``default_adapters`` builds them with their default program names (``codex``, ``claude``, ``copilot``, looked up only in
absolute PATH entries when a task is launched). Nothing is started here: building an adapter never probes or launches,
and the catalog declares all three executors ``enabled: false`` until the operator enables them one by one.

``session_guard`` is the check the executor service (ORQ-T51) hands to the adapters that need one (Copilot): it says
whether a session was created by the framework and is not open in an app (ORQ-6 section 6).
"""
from __future__ import annotations

from collections.abc import Callable

from .claude import ClaudeAdapter
from .codex import CodexExecutorAdapter
from .copilot import CopilotAdapter


def default_adapters(*, session_guard: Callable[[str], bool] | None = None) -> list:
    """Fresh instances of the Codex, Claude and Copilot adapters (``adapter_id`` ``codex_cli``, ``claude_cli``, ``copilot_cli``)."""
    return [CodexExecutorAdapter(), ClaudeAdapter(), CopilotAdapter(session_guard=session_guard)]
