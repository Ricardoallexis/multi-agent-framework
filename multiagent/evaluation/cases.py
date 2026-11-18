"""Evaluation set and report models."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ..contracts import StrictModel

CHECK_TYPES = {
    "contract_valid",
    "field_present",
    "equals",
    "contains",
    "not_contains",
    "number_between",
    "max_length",
}


class CheckSpec(StrictModel):
    """One deterministic assertion against a step output or final artifact."""

    type: str
    step: str | None = None
    field: str | None = None
    expected: Any = None
    min: float | None = None
    max: float | None = None
    max_length: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_check(self) -> CheckSpec:
        if self.type not in CHECK_TYPES:
            raise ValueError(f"Unknown check type {self.type!r}")
        if self.type == "field_present" and not self.field:
            raise ValueError("field_present requires 'field'")
        if self.type in {"equals", "contains", "not_contains"} and "expected" not in self.model_fields_set:
            raise ValueError(f"{self.type} requires 'expected'")
        if self.type == "number_between":
            if self.min is None and self.max is None:
                raise ValueError("number_between requires 'min' or 'max'")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError("number_between 'min' must not exceed 'max'")
        if self.type == "max_length" and self.max_length is None:
            raise ValueError("max_length requires 'max_length'")
        return self


class EvalCase(StrictModel):
    """One workflow input set and its assertions."""

    id: str = Field(min_length=1)
    workflow: str | None = Field(default=None, min_length=1)
    inputs: dict[str, str] = Field(default_factory=dict)
    checks: list[CheckSpec] = Field(default_factory=list)


class EvalTarget(StrictModel):
    """Definition evaluated by this set."""

    kind: Literal["workflow", "agent", "prompt"]
    id: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def prompt_needs_version(self) -> EvalTarget:
        if self.kind == "prompt" and self.version is None:
            raise ValueError("Prompt target requires version")
        if self.kind != "prompt" and self.version is not None:
            raise ValueError("Only prompt targets have a version")
        return self


class EvalSet(StrictModel):
    """Versioned collection of evaluation cases."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    target: EvalTarget | None = None
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_case_ids(self) -> EvalSet:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Evaluation case ids must be unique")
        if self.target is None and any(case.workflow is None for case in self.cases):
            raise ValueError("Cases without a target require workflow")
        if self.target is not None and self.target.kind == "workflow":
            if any(case.workflow not in (None, self.target.id) for case in self.cases):
                raise ValueError("Case workflow must match target.id")
        return self


class CheckResult(StrictModel):
    """Outcome and explanation for one check."""

    name: str
    passed: bool
    detail: str


class CaseResult(StrictModel):
    """Score and check outcomes for one case."""

    id: str
    passed: bool
    score: float
    checks: list[CheckResult]


class EvalReport(StrictModel):
    """Aggregate evaluation results; volatile identifiers live only in metadata."""

    name: str
    version: str
    cases: list[CaseResult]
    score: float
    meta: dict[str, Any] = Field(default_factory=dict)
