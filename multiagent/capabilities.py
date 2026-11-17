"""Namespaced capability ids shared by tools, agents and executors (S5.C1).

A capability id says what something can do in a form every part of the framework can compare, for example
``file.read`` or ``web.search``. The core fixes only the *shape*; it keeps no closed list of domain capabilities
(presets and adapters declare their own), so the core stays domain neutral.

Pattern (the same one ``ToolSpec.tool_id`` uses): 2 to 4 dot separated segments, each ``[a-z][a-z0-9_]{0,39}``, at most
120 characters in all. No upper case, no spaces, no leading or trailing dot, no ``-``. Examples: ``file.read``,
``web.search``, ``doc.table_extract``. Not valid: ``text_generation`` (one segment), ``File.Read``, ``file..read``.

The functions are pure: they read nothing and register nothing.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

CAPABILITY_PATTERN = r"[a-z][a-z0-9_]{0,39}(?:\.[a-z][a-z0-9_]{0,39}){1,3}"
MAX_CAPABILITY_LENGTH = 120
_CAPABILITY = re.compile(CAPABILITY_PATTERN + r"\Z")


class CapabilityIdError(ValueError):
    """A capability id is outside the pattern. ``code`` is stable: ``invalid_capability_id``."""

    def __init__(self, value: Any) -> None:
        self.code = "invalid_capability_id"
        self.value = value
        super().__init__(f"{self.code}: {value!r}")


def is_valid_capability_id(value: Any) -> bool:
    """True when ``value`` is a string that matches the pattern exactly (no normalization)."""
    return isinstance(value, str) and len(value) <= MAX_CAPABILITY_LENGTH and _CAPABILITY.fullmatch(value) is not None


def validate_capability_id(value: Any) -> str:
    """Return ``value`` unchanged when it matches the pattern, else raise ``CapabilityIdError``."""
    if not is_valid_capability_id(value):
        raise CapabilityIdError(value)
    return value


def normalize_capability_id(value: Any) -> str:
    """For text typed by people or read from configuration: strip surrounding whitespace, lower-case, then validate."""
    if not isinstance(value, str):
        raise CapabilityIdError(value)
    return validate_capability_id(value.strip().lower())


def validate_capability_ids(values: Iterable[Any]) -> frozenset[str]:
    """Validate every id (strict, no normalization) and return them as a frozenset. A bare string is not a set of ids."""
    if isinstance(values, (str, bytes)):
        raise CapabilityIdError(values)
    return frozenset(validate_capability_id(value) for value in values)


def invalid_capability_ids(values: Iterable[Any]) -> list[str]:
    """The ids that do not match the pattern, as sorted text; used where an invalid id only warns."""
    if isinstance(values, (str, bytes)):
        return [str(values)]
    return sorted({str(value) for value in values if not is_valid_capability_id(value)})
