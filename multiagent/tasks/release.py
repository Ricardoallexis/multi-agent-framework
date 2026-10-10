# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure ORQ-4 legacy release policy. It never changes native acceptance semantics."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

RELEASE_MODES = ("terminada", "probada", "integrada")
FACT_FIELDS = (
    "delivery_ref", "delivery_sha256", "has_patch", "patchset_sha256", "test_verdict",
    "test_patchset_sha256", "test_ref", "integration_commit", "integration_ref", "updated_by", "updated_at",
)


def empty_facts(task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id, "delivery_ref": "", "delivery_sha256": "", "has_patch": None,
        "patchset_sha256": "", "test_verdict": None, "test_patchset_sha256": "", "test_ref": "",
        "integration_commit": "", "integration_ref": "", "updated_by": "", "updated_at": "",
    }


def facts_view(task_id: str, mode: str | None, row: Mapping[str, Any] | None) -> dict[str, Any]:
    return {**empty_facts(task_id), **(dict(row) if row else {}),
            "task_id": task_id, "legacy_release_mode": mode}


def evaluate(mode: str | None, facts: Mapping[str, Any]) -> tuple[bool, str, list[str]]:
    """Return whether a legacy prerequisite releases, a stable reason, and evidence refs."""
    refs = [ref for ref in (facts.get("delivery_ref"), facts.get("test_ref"), facts.get("integration_ref")) if ref]
    if mode not in RELEASE_MODES:
        return False, "release_mode_unresolved", refs
    # A verified integration is the strongest current evidence and releases in every mode.
    if facts.get("integration_commit") and facts.get("integration_ref"):
        return True, "integrated", refs
    # No patch means the delivery itself completes every legacy threshold.
    if facts.get("delivery_sha256") and facts.get("has_patch") is False:
        return True, "delivered_without_patch", refs
    if not facts.get("delivery_ref") or not facts.get("delivery_sha256"):
        return False, "delivery_missing", refs
    if facts.get("has_patch") is not True:
        return False, "delivery_patch_state_invalid", refs
    if mode == "terminada":
        return True, "delivered", refs
    if mode == "integrada":
        return False, "integration_required", refs
    if facts.get("test_verdict") == "passed":
        if facts.get("test_ref") and facts.get("test_patchset_sha256") == facts.get("patchset_sha256"):
            return True, "tested_current_patchset", refs
        return False, "test_patchset_mismatch", refs
    if facts.get("test_verdict") == "failed":
        return False, "test_failed", refs
    return False, "test_evidence_missing", refs


def validate_union(task_ids: Iterable[str], native: Iterable[Mapping[str, Any]],
                   legacy: Iterable[Mapping[str, Any]]) -> None:
    """Validate ids, duplicate pairs and cycles across both canonical edge tables."""
    from . import graph
    graph.validate_union(task_ids, native, legacy)


def dependent_ids(task_id: str, native: Iterable[Mapping[str, Any]],
                  legacy: Iterable[Mapping[str, Any]]) -> list[str]:
    """Direct dependents across native and legacy edge tables, stable in table order."""
    return list(dict.fromkeys(
        edge["task_id"] for edge in [*native, *legacy] if edge["depends_on_id"] == task_id
    ))
