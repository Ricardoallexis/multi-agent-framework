# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Where the session stores live and which folders may be shown (contract section 6)."""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SOURCES = ("claude", "codex", "copilot", "antigravity")


def _default_claude_desktop_dir() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    pattern = os.path.join(glob.escape(local), "Packages", "Claude_*", "LocalCache", "Roaming", "Claude",
                           "claude-code-sessions")
    for match in sorted(glob.glob(pattern)):
        return Path(match)
    return None


@dataclass(frozen=True)
class SessionsConfig:
    """Paths are passed in; ``defaults()`` resolves them from the user's home / %LOCALAPPDATA%.

    ``allowed_roots`` empty means nothing is listed (contract principle 3).
    """

    allowed_roots: tuple[str, ...] = ()
    sources: tuple[str, ...] = DEFAULT_SOURCES
    claude_home: Path | None = None
    codex_home: Path | None = None
    copilot_home: Path | None = None
    claude_desktop_dir: Path | None = None
    antigravity_home: Path | None = None  # ~/.gemini/antigravity-cli (agy)

    @classmethod
    def defaults(cls, allowed_roots: tuple[str, ...] | list[str] = (),
                 sources: tuple[str, ...] | list[str] = DEFAULT_SOURCES, **paths: Path | None) -> SessionsConfig:
        home = Path.home()
        values = {"claude_home": home / ".claude", "codex_home": home / ".codex",
                  "copilot_home": home / ".copilot", "antigravity_home": home / ".gemini" / "antigravity-cli",
                  "claude_desktop_dir": _default_claude_desktop_dir()}
        values.update({k: (Path(v) if v is not None else None) for k, v in paths.items() if k in values})
        return cls(allowed_roots=tuple(str(r) for r in allowed_roots), sources=tuple(sources), **values)
