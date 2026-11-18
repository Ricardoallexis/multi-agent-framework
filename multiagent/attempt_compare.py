# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic field-by-field comparison of two JSON step outputs."""
from __future__ import annotations

import difflib
from typing import Any

LONG_TEXT_CHARS = 200


def _is_long_text(value: Any) -> bool:
    return isinstance(value, str) and ("\n" in value or "\r" in value or len(value) > LONG_TEXT_CHARS)


def _json_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[key], b[key]) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _unified_diff(before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile="before",
        tofile="after",
        lineterm="",
    )
    return "\n".join(lines)


def _join_key(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def _join_index(prefix: str, index: int) -> str:
    return f"{prefix}[{index}]"


def _diff_values(prefix: str, a: Any, b: Any, out: list[dict[str, Any]]) -> None:
    if _json_equal(a, b):
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(a.keys() | b.keys(), key=str):
            child_path = _join_key(prefix, str(key))
            if key not in a:
                out.append({"path": child_path, "change": "added", "before": None, "after": b[key]})
            elif key not in b:
                out.append({"path": child_path, "change": "removed", "before": a[key], "after": None})
            else:
                _diff_values(child_path, a[key], b[key], out)
        return
    if isinstance(a, list) and isinstance(b, list):
        common = min(len(a), len(b))
        for index in range(common):
            _diff_values(_join_index(prefix, index), a[index], b[index], out)
        for index in range(common, len(b)):
            out.append({"path": _join_index(prefix, index), "change": "added", "before": None, "after": b[index]})
        for index in range(common, len(a)):
            out.append({"path": _join_index(prefix, index), "change": "removed", "before": a[index], "after": None})
        return
    entry: dict[str, Any] = {"path": prefix, "change": "changed", "before": a, "after": b}
    if isinstance(a, str) and isinstance(b, str) and (_is_long_text(a) or _is_long_text(b)):
        entry["diff"] = _unified_diff(a, b)
    out.append(entry)


def compare_outputs(a: Any, b: Any) -> list[dict[str, Any]]:
    """Compare two JSON-serializable step outputs field by field.

    Returns a deterministic list of ``{path, change, before, after}`` entries
    where ``change`` is ``added``, ``removed``, or ``changed``. When a string
    field changes and either side spans multiple lines or exceeds 200
    characters, ``diff`` is included with a unified line diff.
    """
    changes: list[dict[str, Any]] = []
    _diff_values("", a, b, changes)
    return changes
