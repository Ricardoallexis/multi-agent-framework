# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Additional contract for backend function-step conformance."""
from __future__ import annotations

from multiagent.contracts import StrictModel


class CollectedOutputs(StrictModel):
    steps: dict[str, dict[str, object]]


OUTPUT_SCHEMAS = {"CollectedOutputs": CollectedOutputs}
