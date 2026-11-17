"""Deterministic tool selection by capability, driven by configuration (S5.C2).

Configuration (a plain mapping, for example loaded from YAML)::

    {"version": 1,
     "tools": {"file.read": "fs.read_text",                              # tool id, highest registered version
               "web.search": {"tool_id": "search.local", "version": 2}}}  # tool id pinned to a version
    # also accepted: "fs.read_text@3" as the string form of a pinned version

Rules, in order:

1. The capability must match the namespaced pattern (``multiagent.capabilities``).
2. If the configuration names a tool for the capability, that exact tool (and version) is used. It must be registered and
   must declare the capability, otherwise selection fails; it never falls back to another tool.
3. With no configuration for the capability, the tie-break is fixed: ascending ``tool_id``, then the highest ``version``.
   Registration order never matters.
4. No registered tool declares the capability: ``no_tool_for_capability``.

Selection only chooses among tools already registered; it grants nothing. The invoker still applies impact, permissions
and approvals to the chosen tool. Error codes (``SelectionError.code``): ``invalid_capability``, ``invalid_config``,
``no_tool_for_capability``, ``config_unknown_tool``, ``config_version_not_found``, ``config_wrong_capability``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from ..capabilities import is_valid_capability_id
from .registry import RegisteredTool, ToolLookupError, ToolRegistry

_TOOL_REF = re.compile(r"(?P<tool_id>[^@\s]+)(?:@(?P<version>[1-9][0-9]*))?\Z")


class SelectionError(LookupError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class ToolChoice:
    tool_id: str
    version: int | None = None  # None means the highest registered version


@dataclass(frozen=True)
class SelectionConfig:
    choices: Mapping[str, ToolChoice]


def parse_selection_config(raw: Mapping[str, Any] | None) -> SelectionConfig:
    """Validate the configuration shape. ``None`` or ``{}`` means no configuration (default tie-break everywhere)."""
    if raw is None:
        return SelectionConfig({})
    if not isinstance(raw, Mapping) or raw.get("version", 1) != 1:
        raise SelectionError("invalid_config", "expected a version 1 mapping")
    tools = raw.get("tools", {})
    if set(raw) - {"version", "tools"} or not isinstance(tools, Mapping):
        raise SelectionError("invalid_config", "only 'version' and 'tools' are allowed")
    choices: dict[str, ToolChoice] = {}
    for capability, entry in tools.items():
        if not is_valid_capability_id(capability):
            raise SelectionError("invalid_config", f"invalid capability id {capability!r}")
        if isinstance(entry, str):
            match = _TOOL_REF.fullmatch(entry)
            if not match:
                raise SelectionError("invalid_config", f"invalid tool reference for {capability}")
            version = int(match["version"]) if match["version"] else None
            choices[capability] = ToolChoice(match["tool_id"], version)
        elif isinstance(entry, Mapping) and set(entry) <= {"tool_id", "version"} and isinstance(entry.get("tool_id"), str):
            version = entry.get("version")
            if version is not None and (type(version) is not int or version < 1):
                raise SelectionError("invalid_config", f"invalid version for {capability}")
            choices[capability] = ToolChoice(entry["tool_id"], version)
        else:
            raise SelectionError("invalid_config", f"invalid entry for {capability}")
    return SelectionConfig(choices)


def select_tool(registry: ToolRegistry, capability: str,
                config: Mapping[str, Any] | SelectionConfig | None = None) -> RegisteredTool:
    """Pick the registered tool for ``capability`` (see the module rules). Same input, same answer, every run."""
    if not is_valid_capability_id(capability):
        raise SelectionError("invalid_capability", repr(capability))
    parsed = config if isinstance(config, SelectionConfig) else parse_selection_config(config)
    choice = parsed.choices.get(capability)
    if choice is not None:
        try:
            tool = registry.lookup(choice.tool_id, choice.version)
        except ToolLookupError as exc:
            code = "config_unknown_tool" if exc.code == "unknown_tool" else "config_version_not_found"
            raise SelectionError(code, f"{capability} -> {choice.tool_id}") from exc
        if capability not in tool.spec.capabilities:
            raise SelectionError("config_wrong_capability", f"{choice.tool_id} v{tool.spec.version} lacks {capability}")
        return tool
    candidates = registry.list(capability=capability)
    if not candidates:
        raise SelectionError("no_tool_for_capability", capability)
    best = min(candidates, key=lambda spec: (spec.tool_id, -spec.version))
    return registry.lookup(best.tool_id, best.version)
