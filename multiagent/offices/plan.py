# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Office-specific planning helpers."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from typing import Literal

from .schema import OFFICE_ID_PATTERN, OfficeDefinition

_OFFICE_ID = re.compile(OFFICE_ID_PATTERN + r"\Z")


def office_group_id(office_id: str) -> str:
    """Return the stable plan group id for an office."""
    if not isinstance(office_id, str) or _OFFICE_ID.fullmatch(office_id) is None:
        raise ValueError("Invalid office id")
    suffix = hashlib.sha256(office_id.encode("utf-8")).hexdigest()[:8]
    return f"office-{office_id}-{suffix}"


def is_office_group(group_id: str, office_id: str) -> bool:
    """Whether ``group_id`` belongs to exactly ``office_id``."""
    if not isinstance(group_id, str) or not isinstance(office_id, str):
        return False
    if _OFFICE_ID.fullmatch(office_id) is None:
        return False
    return re.fullmatch(rf"office-{re.escape(office_id)}-[0-9a-f]{{8}}", group_id) is not None


def default_plan_template(
    definition: OfficeDefinition,
    text: str,
    first_text_input: Callable[[str, str], str | None],
) -> dict | None:
    """Translate the office default plan into the deterministic planner template."""
    if not definition.orchestrator.default_plan:
        return None

    areas = {area.id: area for area in definition.areas}
    stages = []
    for item in definition.orchestrator.default_plan:
        area = areas[item.area]
        bundle = f"{definition.id}__{area.id}"
        input_name = first_text_input(item.workflow, bundle)
        stages.append({
            "id": item.id or item.area,
            "title": area.name,
            "depends_on": list(item.after),
            "workflow": item.workflow,
            "bundle": bundle,
            "inputs": {input_name: text} if input_name else {},
        })
    return {"prefix": definition.id, "stages": stages}


def office_privacy(classes: Iterable[str | None]) -> Literal["normal", "protected", "unset"]:
    """Summarize area classes for the office's outbound-model decision."""
    values = tuple(classes)
    if any(value in {"sensitive", "restricted"} for value in values):
        return "protected"
    if any(value is None for value in values):
        return "unset"
    if any(value != "non_sensitive" for value in values):
        raise ValueError("Unknown office data class")
    return "normal"
