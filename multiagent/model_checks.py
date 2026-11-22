# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Process-local outcome of the last real Gemini request; no credentials are stored."""
from __future__ import annotations

from datetime import datetime
from threading import Lock

_lock = Lock()
_gemini: dict[str, str | None] = {"verification": "unverified", "verified_at": None, "resets_at": None}


def gemini_verification() -> dict[str, str | None]:
    with _lock:
        return dict(_gemini)


def record_gemini_verification(state: str, *, at: datetime, retry_at: datetime | None = None) -> None:
    if state not in {"verified", "key_invalid", "limit_reached", "no_access", "tls_certificate"}:
        raise ValueError("Unknown Gemini verification state")
    with _lock:
        _gemini.update(verification=state,
                       verified_at=at.isoformat() if state == "verified" else None,
                       resets_at=retry_at.isoformat() if state == "limit_reached" and retry_at else None)
