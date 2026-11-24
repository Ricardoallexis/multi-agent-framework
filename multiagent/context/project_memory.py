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

An untrusted entry that contradicts one the person confirmed (MEM-T02: same project and kind, the same title once
normalized, a different body) is not chosen at all and is counted apart in the note. Two untrusted entries that
clash are both quoted: only what a person wrote is protected.
"""
from __future__ import annotations

import hashlib
import unicodedata
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


def title_key(title: str) -> str:
    """A title as the contradiction check compares it: NFKD without marks, ``casefold`` and single spaces."""
    plain = "".join(char for char in unicodedata.normalize("NFKD", str(title)) if not unicodedata.combining(char))
    return " ".join(plain.casefold().split())


def contradicts(entry: Mapping[str, Any], confirmed: Mapping[str, Any]) -> bool:
    """Whether the untrusted ``entry`` contradicts the person's ``confirmed`` one: same project and kind, same
    normalized title and a different body (the same body is a repeat, not a clash)."""
    return (entry["trust"] != "trusted" and confirmed["trust"] == "trusted" and entry["id"] != confirmed["id"]
            and entry.get("project_id") == confirmed.get("project_id") and entry["kind"] == confirmed["kind"]
            and title_key(entry["title"]) == title_key(confirmed["title"])
            and str(entry["body"]).strip() != str(confirmed["body"]).strip())


def contradicted_by(entry: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The confirmed entries of ``entries`` that the untrusted ``entry`` contradicts (empty for a trusted one)."""
    return [other for other in entries if contradicts(entry, other)]


class OmittedCount(int):
    """How many entries were left out, as the plain integer the engine records, that also knows how many of them
    were left out for contradicting an entry the person confirmed (``conflicts``; the rest fell to the size cap)."""

    conflicts: int

    def __new__(cls, total: int, conflicts: int = 0):
        value = super().__new__(cls, total)
        value.conflicts = conflicts
        return value


@dataclass(frozen=True)
class MemorySelection:
    entries: tuple[Mapping[str, Any], ...]
    omitted: int            # an ``OmittedCount``: the size cap plus the contradictions
    chars: int


def select_entries(entries: Sequence[Mapping[str, Any]], setting: MemorySetting) -> MemorySelection:
    """The entries that fit ``setting``: trusted first, then confidence, then newest; whole entries only.

    An untrusted entry that contradicts a confirmed one is never chosen, however much room there is."""
    asked = [item for item in entries if item["kind"] in setting.kinds]
    clashing = [item for item in asked if contradicted_by(item, asked)]
    wanted = [item for item in asked if not any(item is other for other in clashing)]
    ranked = sorted(range(len(wanted)), key=lambda index: (
        wanted[index]["trust"] != "trusted", -_RANK.get(wanted[index]["confidence"], 0), -index))
    chosen: list[Mapping[str, Any]] = []
    used = 0
    for index in ranked:
        size = len(entry_text(wanted[index]))
        if used + size <= setting.max_chars:
            chosen.append(wanted[index])
            used += size
    return MemorySelection(tuple(chosen), OmittedCount(len(asked) - len(chosen), len(clashing)), used)


def render_entry(entry: Mapping[str, Any], text: str | None = None) -> str:
    """One entry quoted as untrusted data (``text`` replaces the entry's own when it was screened)."""
    return wrap_untrusted(entry_text(entry) if text is None else text, entry_provenance(entry))


def omitted_note(omitted: int) -> str:
    """The note after the entries: how many were left out by the size cap and how many for contradicting an entry
    the person confirmed (an ``OmittedCount`` tells them apart; a plain integer is all size cap)."""
    clashes = min(int(getattr(omitted, "conflicts", 0)), int(omitted))
    capped = int(omitted) - clashes
    notes = []
    if capped:
        notes.append(f"({capped} more project memory entries left out by the size cap.)")
    if clashes:
        notes.append("(1 project memory entry left out: it contradicts an entry the person confirmed.)" if clashes == 1
                     else f"({clashes} project memory entries left out: they contradict entries the person confirmed.)")
    return " ".join(notes)
