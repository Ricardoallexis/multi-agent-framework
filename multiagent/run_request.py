"""Domain-independent requests for the runtime's Python interface."""
from __future__ import annotations

import re

from pydantic import Field, field_validator

from .contracts import ExecutionMode, StrictModel


class RunRequest(StrictModel):
    workflow_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
    project_name: str = Field(min_length=2, max_length=120)
    inputs: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str = Field(default="", max_length=120)
    execution_mode: ExecutionMode = ExecutionMode.AUTO
    step_modes: dict[str, ExecutionMode] = Field(default_factory=dict)
    sensitive: bool = False

    @field_validator("inputs")
    @classmethod
    def validate_input_names(cls, values: dict[str, str]) -> dict[str, str]:
        for key in values:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
                raise ValueError("Input names must be template identifiers")
            if key in {"previous_output", "revision_feedback"} or key.endswith("_output"):
                raise ValueError(f"Input name {key!r} is reserved for runtime context")
        return values
