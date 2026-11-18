"""Domain-independent requests for the runtime's Python interface."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, field_serializer

from .contracts import ExecutionMode, StrictModel


class FunctionStepRequestError(ValueError):
    """A run cannot route or change the execution mode of a function step."""

    code = "function_step_option_invalid"

    def __init__(self, step_id: str):
        self.step_id = step_id
        super().__init__(f"Step {step_id}: function steps do not accept routing preferences or execution modes")


def validate_function_step_options(request, definition) -> None:
    """Reject provider options for deterministic steps before a run is stored."""
    for step in definition.steps:
        if step.function is not None and (step.id in request.step_modes or step.id in request.routing_preferences):
            raise FunctionStepRequestError(step.id)


class RoutingPreference(StrictModel):
    """Run-scoped ECO choices; policy and budgets remain authoritative."""
    mode: Literal["supervised", "automatic", "single"] | None = None
    profile_id: str | None = Field(default=None, min_length=1, max_length=120)
    level: Literal["T1", "T2", "T3", "T4"] | None = None
    model: str | None = Field(default=None, min_length=1, max_length=120)
    effort: Literal["none", "low", "medium", "high", "max"] | None = None


class RunRequest(StrictModel):
    workflow_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
    project_name: str = Field(min_length=2, max_length=120)
    inputs: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str = Field(default="", max_length=120)
    execution_mode: ExecutionMode = ExecutionMode.AUTO
    step_modes: dict[str, ExecutionMode] = Field(default_factory=dict)
    sensitive: bool = False
    routing_preferences: dict[str, RoutingPreference] = Field(default_factory=dict, exclude_if=lambda values: not values)
    # Optional link to a task (PRY): checked against the system database when the run is created and never changed.
    task_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

    @field_serializer("routing_preferences")
    def serialize_preferences(self, values):
        return {key: choice.model_dump(exclude_unset=True) for key, choice in values.items()}

    @field_validator("inputs")
    @classmethod
    def validate_input_names(cls, values: dict[str, str]) -> dict[str, str]:
        for key in values:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
                raise ValueError("Input names must be template identifiers")
            if key in {"previous_output", "revision_feedback"} or key.endswith("_output"):
                raise ValueError(f"Input name {key!r} is reserved for runtime context")
        return values
