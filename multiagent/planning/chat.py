# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Parts of a free-text request for «Nueva sesión»: where one request becomes a plan of several workflows."""
from __future__ import annotations

import re

PLAN_SEPARATORS = ("y luego", "y después", "después", "luego")
# Longest first, so "y luego" is one separator and not "y" plus "luego".
_SPLIT = re.compile(r"\b(?:" + "|".join(re.escape(item) for item in sorted(PLAN_SEPARATORS, key=len, reverse=True)) + r")\b",
                    re.IGNORECASE)
_EDGES = " \t\r\n,;"


def split_request(text: str) -> list[str]:
    """The parts of ``text`` between the plan separators, without commas or spaces at the edges and without empty parts."""
    parts = (part.strip(_EDGES) for part in _SPLIT.split(str(text or "")))
    return [part for part in parts if part]
