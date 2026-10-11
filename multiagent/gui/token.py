# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Local GUI access token.

The token is a random 256-bit URL-safe string kept in a file that only the
current user should read. On POSIX the file is created with mode 0600. On
Windows ``os.open`` cannot express an ACL, so the file relies on the user
profile ACL (the profile folder is private to that user by default); keep the
file inside the profile and do not move it to a shared folder.

The launcher enforces that: ``check_token_location`` refuses a token file that is not under the user's
home folder (on any system) unless ``gui_token_outside_home`` (environment ``GUI_TOKEN_OUTSIDE_HOME=true``)
is set, which says the owner knows that folder is private to them.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

MIN_TOKEN_LENGTH = 22  # 128 bits in base64url


def _valid(value: str) -> bool:
    return len(value) >= MIN_TOKEN_LENGTH and value.isascii() and value.isprintable() and " " not in value


class TokenLocationError(RuntimeError):
    """The configured token file is not in a private place (see ``check_token_location``)."""


def check_token_location(path: Path, *, allow_outside_home: bool = False) -> None:
    """Refuse a token file outside the user's home folder unless the owner opted in.

    Outside the profile the folder's ACL may let other accounts read the token (on Windows ``os.open``
    cannot restrict it), and anyone who reads it can use the GUI API.
    """
    if allow_outside_home:
        return
    try:
        inside = Path(path).resolve().is_relative_to(Path.home().resolve())
    except (OSError, RuntimeError, ValueError):  # no usable home folder: treat as outside
        inside = False
    if not inside:
        raise TokenLocationError(
            f"The GUI token file {path} is outside your home folder, where other users may read it. "
            "Keep it under your home folder, or set GUI_TOKEN_OUTSIDE_HOME=true if that folder is private to you.")


def read_token(path: Path) -> str | None:
    """Return the token stored in ``path`` or ``None`` if missing or malformed."""
    try:
        value = Path(path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value if _valid(value) else None


def ensure_token(path: Path) -> str:
    """Return the stored token, creating the file with a fresh one if needed."""
    path = Path(path)
    existing = read_token(path)
    if existing:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(token + "\n")
    try:
        os.chmod(path, 0o600)  # on Windows this only touches the read-only bit
    except OSError:
        pass
    return token
