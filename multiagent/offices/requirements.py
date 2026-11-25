# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure capability requirements for office areas (S6.E1, design 8.4).

An area declares in ``requires`` what it needs (``model.text``, ``executor.code``...), never a provider. This module
crosses that with a list of *candidates* the caller already gathered (saved connections, active executors, the mock
backend) and says either which candidates can cover the area or what is missing. Nothing here reads files, settings
or the network: candidates come in as plain dicts, so the same code serves any caller and any test.

A candidate is ``{"id", "kind", "label", "capabilities", "local"}``. ``kind`` is ``"model"``, ``"executor"`` or
``"mock"``. ``capabilities`` is an iterable of ids. ``local`` says it never leaves the machine; when it is absent it
is read from ``model.local`` in ``capabilities``.

The texts are the ones the person reads in simple mode; none of them names a provider.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ..capabilities import is_valid_capability_id, validate_capability_ids

KNOWN_REQUIREMENTS = (
    "model.text",
    "model.tools",
    "model.structured",
    "model.local",
    "executor.code",
    "executor.files",
)

# What the catalog's own ``capabilities`` words mean in namespaced ids. The catalog is not edited: new ids come from here.
CAPABILITY_EQUIVALENCES: dict[str, tuple[str, ...]] = {"text_generation": ("model.text",)}

# Ids the model side may cover, and what an active executor adds (``model.text``, ``executor.code``, ``executor.files``).
_EXECUTOR_CAPABILITIES = frozenset({"model.text", "executor.code", "executor.files"})
_MOCK_CAPABILITIES = frozenset(item for item in KNOWN_REQUIREMENTS if item.startswith("model."))

CAPABILITY_TEXT = {
    "model.text": "write text",
    "model.tools": "use tools",
    "model.structured": "give structured responses",
    "model.local": "run on your computer",
    "executor.code": "work with code",
    "executor.files": "work with files",
}

NOTE_SENSITIVE = "This area is Sensitive: it can only use agents on your team today"
NOTE_LOCAL_ONLY = "This area is Local only: it never leaves your computer"
TEXT_NOTHING_NEEDED = "This area does not need anything in particular"


def model_capabilities(spec: Any) -> frozenset[str]:
    """Namespaced capabilities of a catalog model, derived from the table (the catalog is not edited).

    ``text_generation`` gives ``model.text``; ``structured_output`` gives ``model.structured``; ``tier == "local"``
    gives ``model.local``; a valid namespaced id the model already declares in ``capabilities`` passes as it is.
    """
    found: set[str] = set()
    declared = getattr(spec, "capabilities", None) or ()
    for item in declared:
        if item in CAPABILITY_EQUIVALENCES:
            found.update(CAPABILITY_EQUIVALENCES[item])
        elif is_valid_capability_id(item):
            found.add(item)
    if getattr(spec, "structured_output", False) is True:
        found.add("model.structured")
    if str(getattr(spec, "tier", "")).casefold() == "local":
        found.add("model.local")
    return validate_capability_ids(found)


def executor_capabilities() -> frozenset[str]:
    """What an active executor adds: text, code and files."""
    return _EXECUTOR_CAPABILITIES


def mock_capabilities() -> frozenset[str]:
    """The mock backend covers every ``model.*`` requirement and never ``executor.*``."""
    return _MOCK_CAPABILITIES


def _words(requirement: str) -> str:
    return CAPABILITY_TEXT.get(requirement, requirement)


def _join(parts: Sequence[str], last: str) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + f" {last} " + parts[-1]


def _caps(candidate: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(candidate.get("capabilities") or ())


def _is_local(candidate: Mapping[str, Any]) -> bool:
    if "local" in candidate:
        return candidate["local"] is True
    return "model.local" in _caps(candidate)


def coverage(requires: Iterable[str], candidates: Sequence[Mapping[str, Any]], *, local_only_candidates: bool) -> dict[str, Any]:
    """Cross ``requires`` with ``candidates``.

    With ``local_only_candidates`` only local candidates count. A candidate covers the area when it has every requirement.
    When none does, ``missing`` is what the closest candidate (the one with most of them; ties by id) lacks, so the
    person is told one agent to connect. Returns ``{covered, by: [{id, kind, label}], missing, text, action}`` where
    ``action`` is ``"connect"`` when something is missing and ``None`` otherwise.
    """
    needed: list[str] = []
    for item in requires:
        if item not in needed:
            needed.append(item)
    pool = [c for c in candidates if not local_only_candidates or _is_local(c)]
    pool.sort(key=lambda c: str(c.get("id", "")))
    if not needed:
        return {"covered": True, "by": [], "missing": [], "text": TEXT_NOTHING_NEEDED, "action": None}
    wanted = frozenset(needed)
    covering = [c for c in pool if wanted <= _caps(c)]
    if covering:
        by = [{"id": c["id"], "kind": c.get("kind", "model"), "label": c.get("label") or c["id"]} for c in covering]
        text = "You can cover this " + _join([f"with {entry['label']}" for entry in by], "or")
        return {"covered": True, "by": by, "missing": [], "text": text, "action": None}
    if pool:
        closest = min(pool, key=lambda c: (-len(wanted & _caps(c)), str(c.get("id", ""))))
        have = _caps(closest)
        missing = [item for item in needed if item not in have]
    else:
        missing = list(needed)
    text = "You need an agent that can " + _join([_words(item) for item in missing], "and")
    return {"covered": False, "by": [], "missing": missing, "text": text, "action": "connect"}


def area_note(*, sensitivity: str | None, local_only: bool) -> str | None:
    """The line shown on an area that only counts local agents, or ``None``. "Local only" wins over "Sensitive"."""
    if local_only:
        return NOTE_LOCAL_ONLY
    if sensitivity == "sensitive":
        return NOTE_SENSITIVE
    return None


def office_requirements(definition: Any, candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``{office, areas: [{id, requires, covered, by, missing, text, note, action}]}`` for an ``OfficeDefinition``.

    An area is "Local only" when the area or the whole office says ``local_only``, and "Sensitive" when its
    ``sensitivity`` is ``sensitive``; in both cases only local candidates count. ``requires`` informs: nothing routes by it.
    """
    areas = []
    for area in definition.areas:
        local = bool(area.local_only or definition.local_only)
        note = area_note(sensitivity=area.sensitivity, local_only=local)
        requires = sorted(area.requires)
        result = coverage(requires, candidates, local_only_candidates=note is not None)
        areas.append({"id": area.id, "requires": requires, **result, "note": note})
    return {"office": definition.id, "areas": areas}
