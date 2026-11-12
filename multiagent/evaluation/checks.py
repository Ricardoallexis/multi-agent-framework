"""Deterministic evaluation checks."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..contracts import OUTPUT_SCHEMAS
from .cases import CheckResult, CheckSpec


def _field_value(data: Any, path: str | None) -> tuple[bool, Any]:
    if not path:
        return False, None
    value = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def evaluate_check(spec: CheckSpec, artifact: dict[str, Any] | None) -> CheckResult:
    """Evaluate one check against an artifact, returning a stable explanation."""
    data = artifact.get("data") if artifact else None
    check_type = spec.type
    passed = False
    detail: str

    if check_type == "contract_valid":
        kind = artifact.get("kind") if artifact else None
        contract = OUTPUT_SCHEMAS.get(kind) if isinstance(kind, str) else None
        if contract is None or not isinstance(data, dict):
            detail = "No known output contract or structured artifact is available"
        else:
            try:
                contract.model_validate(data)
            except ValidationError as exc:
                detail = f"{kind} contract validation failed: {exc}"
            else:
                passed = True
                detail = f"{kind} satisfies its output contract"
    elif check_type == "field_present":
        found, _ = _field_value(data, spec.field)
        passed = found
        detail = f"Field {spec.field!r} is {'present' if found else 'missing'}"
    else:
        found, actual = _field_value(data, spec.field) if spec.field else (True, data)
        if not found:
            detail = f"Field {spec.field!r} is missing"
        elif check_type == "equals":
            passed = actual == spec.expected
            detail = f"Value {'equals' if passed else 'does not equal'} expected value"
        elif check_type == "contains":
            try:
                passed = spec.expected in actual
            except TypeError:
                passed = False
            detail = f"Value {'contains' if passed else 'does not contain'} expected value"
        elif check_type == "not_contains":
            try:
                passed = spec.expected not in actual
            except TypeError:
                passed = False
            detail = f"Value {'does not contain' if passed else 'contains'} forbidden value"
        elif check_type == "number_between":
            if isinstance(actual, (int, float)) and not isinstance(actual, bool):
                passed = (spec.min is None or actual >= spec.min) and (spec.max is None or actual <= spec.max)
            detail = f"Number is {'within' if passed else 'outside'} the configured bounds"
        elif check_type == "max_length":
            try:
                length = len(actual)
            except TypeError:
                length = -1
            limit = spec.max_length if spec.max_length is not None else -1
            passed = 0 <= length <= limit
            detail = f"Length {length if length >= 0 else 'is unavailable'}; maximum is {limit}"
        else:
            raise ValueError(f"Unknown check type {check_type!r}")

    return CheckResult(name=check_type, passed=passed, detail=detail)
