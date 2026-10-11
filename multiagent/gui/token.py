# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Local GUI access token.

The token is a random 256-bit URL-safe string kept in a file that only the
current user should read. On POSIX the file is created with mode 0600. On
Windows ``os.open`` cannot express an ACL, so the file relies on the user
profile ACL (the profile folder is private to that user by default); keep the
file inside the profile and do not move it to a shared folder.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

MIN_TOKEN_LENGTH = 22  # 128 bits in base64url


def _valid(value: str) -> bool:
    return len(value) >= MIN_TOKEN_LENGTH and value.isascii() and value.isprintable() and " " not in value


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
