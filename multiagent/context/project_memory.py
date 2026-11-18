# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Project memory in the context of a step (S12.A1, CAP-T20): what is asked for, which entries fit and how
they are quoted. Pure: no database, models or events.

A workflow or a step opts in with ``project_memory`` (``true``, ``false`` or ``{max_chars, kinds}``); a step
value wins over the workflow's, and the default is off, so a workflow that does not mention it gets exactly
the prompt it always got. The entries of the project are chosen under a size cap (trusted entries first, then
the more confident, then the newer; an entry that does not fit is left out whole and counted) and each one is
quoted in the untrusted-data envelope of ``document_trust`` with its provenance in the header: they are
data about the project, never instructions, whoever wrote them.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..document_trust import Provenance, SourceKind, derive, wrap_untrusted

KINDS = ("fact", "decision", "convention")
CONFIDENCES = ("low", "medium", "high")
DEFAULT_MAX_CHARS = 4000
MAX_MAX_CHARS = 20000
MIN_MAX_CHARS = 200
SECTION_HEADING = "## Project memory"       # trusted text, outside the envelopes
_RANK = {level: index for index, level in enumerate(CONFIDENCES)}


@dataclass(frozen=True)
class MemorySetting:
    """What a workflow or a step asks for: the size cap (characters of entry text) and the kinds to include."""

    max_chars: int = DEFAULT_MAX_CHARS
    kinds: tuple[str, ...] = KINDS


def setting_issue(value: Any) -> str | None:
    """Why ``value`` is not a valid ``project_memory`` declaration, or ``None`` when it is (or is absent)."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, dict) or set(value) - {"max_chars", "kinds"}:
        return "Expected true, false or {max_chars, kinds}"
    chars = value.get("max_chars", DEFAULT_MAX_CHARS)
    if isinstance(chars, bool) or not isinstance(chars, int) or not MIN_MAX_CHARS <= chars <= MAX_MAX_CHARS:
        return f"max_chars must be an integer from {MIN_MAX_CHARS} to {MAX_MAX_CHARS}"
    kinds = value.get("kinds", list(KINDS))
    if (not isinstance(kinds, list) or not kinds or any(not isinstance(item, str) or item not in KINDS for item in kinds)):
        return f"kinds must be a non-empty list of {', '.join(KINDS)}"
    return None


def resolve_setting(workflow_value: Any, step_value: Any) -> MemorySetting | None:
    """The setting in force for a step, or ``None`` when the memory is off (the default).

    A step value (``True``, ``False`` or a mapping) wins over the workflow's; ``None`` inherits it.
    """
    value = workflow_value if step_value is None else step_value
    if value is None or value is False or setting_issue(value) is not None:
        return None
    if value is True:
        return MemorySetting()
    return MemorySetting(max_chars=int(value.get("max_chars", DEFAULT_MAX_CHARS)),
                         kinds=tuple(value.get("kinds", KINDS)))


def entry_text(entry: Mapping[str, Any]) -> str:
    """The text of an entry as the model reads it: its title and its body."""
    return f"{entry['title']}\n{entry['body']}"


def entry_sha256(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest()


def entry_provenance(entry: Mapping[str, Any]) -> Provenance:
    """Provenance of an entry for the prompt. Always untrusted: the memory is data about the project.

    The author (``user``, ``model_output``...) goes in as the origin, so the header says it (``derived_from``)
    and the label carries the kind, the actor and the confidence. ``Provenance`` never trusts what derives from
    anything, so even an entry the user wrote is quoted and not read as an instruction.
    """
    try:
        origin = Provenance(entry["source_kind"], ref=entry.get("source_ref") or None)
    except ValueError:
        origin = Provenance(SourceKind.UNKNOWN.value)
    try:
        return derive("project_memory", origin, ref=f"memory:{entry['id']}", sha256=entry["sha256"],
                      label=f"{entry['kind']},{entry['source_actor']},confidence:{entry['confidence']}")
    except ValueError:
        return derive("project_memory", origin)


@dataclass(frozen=True)
class MemorySelection:
    entries: tuple[Mapping[str, Any], ...]
    omitted: int
    chars: int


def select_entries(entries: Sequence[Mapping[str, Any]], setting: MemorySetting) -> MemorySelection:
    """The entries that fit ``setting``: trusted first, then confidence, then newest; whole entries only."""
    wanted = [item for item in entries if item["kind"] in setting.kinds]
    ranked = sorted(range(len(wanted)), key=lambda index: (
        wanted[index]["trust"] != "trusted", -_RANK.get(wanted[index]["confidence"], 0), -index))
    chosen: list[Mapping[str, Any]] = []
    used = 0
    for index in ranked:
        size = len(entry_text(wanted[index]))
        if used + size <= setting.max_chars:
            chosen.append(wanted[index])
            used += size
    return MemorySelection(tuple(chosen), len(wanted) - len(chosen), used)


def render_entry(entry: Mapping[str, Any], text: str | None = None) -> str:
    """One entry quoted as untrusted data (``text`` replaces the entry's own when it was screened)."""
    return wrap_untrusted(entry_text(entry) if text is None else text, entry_provenance(entry))


def omitted_note(omitted: int) -> str:
    return f"({omitted} more project memory entries left out by the size cap.)" if omitted else ""
