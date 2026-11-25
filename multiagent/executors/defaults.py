# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The executor-application adapters that ship with the framework (ORQ-T48 Codex, T49 Claude, T50 Copilot, GEM-T03 agy).

``default_adapters`` builds them with their default program names (``codex``, ``claude``, ``copilot``, ``agy``, looked
up only in absolute PATH entries when a task is launched). Nothing is started here: building an adapter never probes or
launches, and the catalog declares every executor ``enabled: false`` until the operator enables them one by one.

``session_guard`` is the check the executor service (ORQ-T51) hands to the adapters that need one (Copilot): it says
whether a session was created by the framework and is not open in an app (ORQ-6 section 6).

``settings`` gives the Antigravity adapter what the operator configured: the accepted third-party notice, the kill
switch file, the run lock file and the ``--add-dir`` folders. Without it (or with no notice accepted) agy stays off.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from .antigravity import AntigravityAdapter
from .claude import ClaudeAdapter
from .codex import CodexExecutorAdapter
from .copilot import CopilotAdapter


def antigravity_adapter(settings: Any = None, *, notice_accepted: str | None = None) -> AntigravityAdapter:
    """The agy adapter configured from ``settings`` (the ``antigravity_*`` fields); off when there are none.

    ``notice_accepted`` is the notice the person accepted in "Activate" (EJE-T01); it counts like
    ``ANTIGRAVITY_NOTICE_ACCEPTED`` in ``.env``, which keeps working.
    """
    if settings is None:
        return AntigravityAdapter(notice_accepted=notice_accepted or "")
    add_dirs = list(dict.fromkeys((sys.prefix, sys.base_prefix,
                                   *(getattr(settings, "antigravity_add_dirs", ()) or ()))))
    return AntigravityAdapter(
        notice_accepted=(getattr(settings, "antigravity_notice_accepted", "") or "") or notice_accepted or "",
        kill_switch=getattr(settings, "antigravity_kill_switch", None),
        lock_path=getattr(settings, "antigravity_lock_file", None),
        add_dirs=add_dirs)


def default_adapters(*, session_guard: Callable[[str], bool] | None = None, settings: Any = None,
                     antigravity_notice: str | None = None) -> list:
    """Fresh instances of the Codex, Claude, Copilot and Antigravity adapters (``adapter_id`` ``codex_cli``,
    ``claude_cli``, ``copilot_cli``, ``antigravity_cli``)."""
    return [CodexExecutorAdapter(), ClaudeAdapter(), CopilotAdapter(session_guard=session_guard),
            antigravity_adapter(settings, notice_accepted=antigravity_notice)]
