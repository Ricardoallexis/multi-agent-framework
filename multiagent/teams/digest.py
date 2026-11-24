# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The digest of a team definition (OFI-T03).

The digest is taken over the canonical model of the team, never over the bytes of ``team.yaml``: two files that
say the same thing have the same digest. ``TeamServices.propose`` stores it with each team version, and an office
may declare it as ``team_sha256`` so an area is installed only if its team is the one the author wrote.
"""
from __future__ import annotations

from typing import Any

from ..external_output import canonical_json, sha256


def team_canonical_bytes(team: Any) -> bytes:
    """The canonical JSON of a validated ``Team``: sorted keys, compact separators, UTF-8."""
    return canonical_json(team.model_dump(mode="json"))


def team_sha256(team: Any) -> str:
    """Hex SHA-256 of ``team_canonical_bytes(team)``."""
    return sha256(team_canonical_bytes(team))
