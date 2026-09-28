# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Structured errors of the application services.

Every interface built on the services (Python, HTTP, CLI and later UI and MCP)
reports failures the same way: a stable ``code``, a human ``message``, an
HTTP-like ``status`` and JSON-ready ``details``. Unexpected exceptions are not
translated, so real bugs still surface as such.
"""
from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

from pydantic import ValidationError

from ..errors import (
    BudgetExceeded,
    HumanSubmissionError,
    IdempotencyConflict,
    InvalidStateTransition,
    MultiAgentError,
)
from ..workflow_validation import WorkflowDefinitionError

# Stable error codes. Interfaces may rely on them; add new ones, never rename.
NOT_FOUND = "not_found"                          # 404: run, workflow, agent or bundle does not exist
INVALID_REQUEST = "invalid_request"              # 422: request data fails validation
INVALID_DEFINITION = "invalid_definition"        # 422: workflow or bundle definition is invalid
INVALID_STATE = "invalid_state"                  # 409: operation not allowed in the run's current state
INVALID_OPERATION = "invalid_operation"          # 409: operation arguments do not fit the run
BUDGET_EXCEEDED = "budget_exceeded"              # 409: run budget does not allow the operation
IDEMPOTENCY_CONFLICT = "idempotency_conflict"    # 409: idempotency key reused with different data
HUMAN_STEP_UNAVAILABLE = "human_step_unavailable"    # 409: run has no pending human step
HUMAN_SUBMISSION_INVALID = "human_submission_invalid"  # 422: human/external response rejected
OPERATION_FAILED = "operation_failed"            # 409: other framework error while handling the request


class ServiceError(Exception):
    """Public, interface-independent error raised by the application services."""

    def __init__(self, code: str, message: str, *, status: int = 400,
                 details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        """JSON-ready form; never contains local file paths or secrets."""
        return {"code": self.code, "message": self.message, "details": self.details}

    def __repr__(self) -> str:
        return f"ServiceError({self.code!r}, {self.message!r}, status={self.status})"


def translate(exc: BaseException) -> ServiceError | None:
    """Return the ``ServiceError`` for a known framework exception, else ``None``."""
    if isinstance(exc, ServiceError):
        return exc
    if isinstance(exc, KeyError):
        message = str(exc.args[0]) if exc.args else "Not found"
        return ServiceError(NOT_FOUND, message, status=404)
    if isinstance(exc, WorkflowDefinitionError):
        payload = exc.to_payload()
        return ServiceError(INVALID_DEFINITION, payload["error"], status=422, details=payload)
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        # pydantic gives ``loc`` as a tuple; lists keep the payload identical after a JSON round trip.
        return ServiceError(INVALID_REQUEST, "Invalid request", status=422,
                            details={"errors": [{**error, "loc": list(error["loc"])} for error in errors]})
    if isinstance(exc, InvalidStateTransition):
        return ServiceError(INVALID_STATE, str(exc), status=409)
    if isinstance(exc, BudgetExceeded):
        return ServiceError(BUDGET_EXCEEDED, str(exc), status=409)
    if isinstance(exc, IdempotencyConflict):
        return ServiceError(IDEMPOTENCY_CONFLICT, str(exc), status=409)
    if isinstance(exc, HumanSubmissionError):
        return ServiceError(HUMAN_SUBMISSION_INVALID, str(exc), status=422)
    if isinstance(exc, MultiAgentError):
        return ServiceError(OPERATION_FAILED, str(exc), status=409)
    if isinstance(exc, ValueError):
        # Checked last: WorkflowDefinitionError and ValidationError are ValueErrors too.
        return ServiceError(INVALID_OPERATION, str(exc), status=409)
    return None


F = TypeVar("F", bound=Callable[..., Any])


def service_errors(func: F) -> F:
    """Decorator for service methods: known exceptions leave as ``ServiceError``."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except ServiceError:
            raise
        except Exception as exc:
            error = translate(exc)
            if error is None:
                raise
            raise error from exc

    return wrapper  # type: ignore[return-value]
