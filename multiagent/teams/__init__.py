# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Team definition models and loaders (S6.A1)."""
from __future__ import annotations

from .schema import (
    Assignment,
    Member,
    MemberKind,
    Relationship,
    RelationshipType,
    Role,
    Team,
    TeamDefinitionError,
    TeamIssue,
    load_team,
    parse_team_document,
    validate_team,
)

__all__ = [
    "Assignment",
    "Member",
    "MemberKind",
    "Relationship",
    "RelationshipType",
    "Role",
    "Team",
    "TeamDefinitionError",
    "TeamIssue",
    "load_team",
    "parse_team_document",
    "validate_team",
]
