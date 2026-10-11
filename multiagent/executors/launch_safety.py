# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared rules for starting an external program (GUI-T06 ``open_in_app``, ORQ-T47 ``process``).

Three small, dependency-free pieces that every launcher of an agent CLI applies the same way:

- the child environment is a copy of the parent's without ``CLAUDE*`` / ``ANTHROPIC*`` variables (the server may
  itself have been started from an agent session) and with ``NoDefaultCurrentDirectoryInExePath=1``, so Windows
  does not look for ``node``/``cmd`` in the current folder first;
- a program is looked up only in the absolute ``PATH`` entries that are not the process's own current folder, and
  whatever the lookup returns is rejected if it still sits in that folder (Windows puts it first on its own).
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

SCRUBBED_PREFIXES = ("CLAUDE", "ANTHROPIC")
NO_CURRENT_DIR_VARIABLE = "NoDefaultCurrentDirectoryInExePath"


def normal_path(path: str | os.PathLike[str]) -> str:
    """Absolute, case-normalized form used to compare two folders."""
    return os.path.normcase(os.path.abspath(path))


def is_scrubbed_name(name: str) -> bool:
    """True for a variable the child must never inherit (``CLAUDE*`` / ``ANTHROPIC*``, any letter case)."""
    return name.upper().startswith(SCRUBBED_PREFIXES)


def scrubbed_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of the environment (``os.environ`` by default) without any ``CLAUDE*`` or ``ANTHROPIC*`` variable."""
    return {name: value for name, value in (os.environ if source is None else source).items()
            if not is_scrubbed_name(name)}


def launch_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The scrubbed environment plus ``NoDefaultCurrentDirectoryInExePath=1`` (the current folder is not searched)."""
    env = {name: value for name, value in scrubbed_environment(source).items()
           if name.upper() != NO_CURRENT_DIR_VARIABLE.upper()}
    env[NO_CURRENT_DIR_VARIABLE] = "1"
    return env


def find_executable(name: str, *, which: Callable[..., str | None] | None = None) -> str | None:
    """``name`` from the absolute ``PATH`` entries that are not the current folder, or None.

    ``which`` defaults to ``shutil.which`` (looked up at call time).
    """
    which = which or shutil.which
    here = normal_path(os.getcwd())
    entries = [entry.strip().strip('"') for entry in os.environ.get("PATH", "").split(os.pathsep)]
    entries = [entry for entry in entries if entry and os.path.isabs(entry) and normal_path(entry) != here]
    found = which(name, path=os.pathsep.join(entries))
    # Windows puts the current folder first even when it is not asked for: never run what sits there
    if not found or normal_path(os.path.dirname(os.path.abspath(found))) == here:
        return None
    return found


def is_within(path: str | os.PathLike[str], folder: str | os.PathLike[str]) -> bool:
    """True when ``path`` is ``folder`` or lies below it, by its written form or after resolving links."""
    paths = {normal_path(path), normal_path(Path(path).resolve())}
    folders = {normal_path(folder), normal_path(Path(folder).resolve())}
    for candidate in paths:
        for root in folders:
            try:
                if os.path.commonpath([candidate, root]) == root:
                    return True
            except ValueError:  # different drives
                continue
    return False
