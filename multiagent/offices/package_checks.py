# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic screening of portable office package text."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..egress_guard import findings, redact


@dataclass(frozen=True)
class PackageCheck:
    found: list[dict[str, str | int]]
    redacted: dict[str, str]
    unreadable: list[str]


def excluded_path(path: str) -> bool:
    """Exclude private workspace material regardless of its suffix or location."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    blocked = {"runs", "conversations", "inputs", "logs", ".local", ".env", "file_tools", "file-tools"}
    return (any(part.casefold() in blocked or part.casefold().startswith(".env.") for part in parts)
            or any(part.casefold().endswith((".db", ".sqlite", ".sqlite3", ".log",
                                             "-wal", "-shm", "-journal")) for part in parts))


def in_file_tools_root(path: Path, file_tools_root: Path | None) -> bool:
    """Identify a configured tools directory even when it has a custom name."""
    if file_tools_root is None:
        return False
    try:
        return path.resolve().is_relative_to(file_tools_root.resolve())
    except (OSError, RuntimeError):
        return False


def check_files(files: dict[str, bytes]) -> PackageCheck:
    """Redact recognized secrets and personal data; never attach unreadable files."""
    found: list[dict[str, str | int]] = []
    redacted: dict[str, str] = {}
    unreadable: list[str] = []
    for path, content in sorted(files.items()):
        path_matches = findings(path, include_entropy=True)
        if path_matches:
            found.extend({"path": path, "line": 1, "type": kind} for _, _, kind in path_matches)
            unreadable.append(path)
            continue
        if excluded_path(path) or any(byte < 32 and byte not in (9, 10, 13) for byte in content):
            unreadable.append(path)
            continue
        try:
            source = content.decode("utf-8")
        except UnicodeDecodeError:
            unreadable.append(path)
            continue
        matches = findings(source, include_entropy=True)
        found.extend({"path": path, "line": source.count("\n", 0, start) + 1, "type": kind}
                     for start, _, kind in matches)
        redacted[path] = redact(source, matches)
    return PackageCheck(found, redacted, unreadable)
