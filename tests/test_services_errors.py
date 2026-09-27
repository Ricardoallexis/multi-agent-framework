# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Application services contract: structured errors and bundle registry (G4a-T01)."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from multiagent.errors import (
    BudgetExceeded,
    HumanSubmissionError,
    IdempotencyConflict,
    InvalidStateTransition,
    ProviderUnavailable,
)
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError, service_errors, translate
from multiagent.workflow_validation import WorkflowDefinitionError, WorkflowIssue


@pytest.mark.parametrize(("exc", "code", "status"), [
    (KeyError("Run not found: r1"), "not_found", 404),
    (InvalidStateTransition("not waiting"), "invalid_state", 409),
    (BudgetExceeded("no budget"), "budget_exceeded", 409),
    (IdempotencyConflict("key reused"), "idempotency_conflict", 409),
    (HumanSubmissionError("bad response"), "human_submission_invalid", 422),
    (ProviderUnavailable("offline"), "operation_failed", 409),
    (ValueError("Feedback is required"), "invalid_operation", 409),
])
def test_known_exceptions_translate_to_service_errors(exc, code, status):
    error = translate(exc)
    assert (error.code, error.status) == (code, status)
    assert error.message == str(exc.args[0])


def test_definition_error_keeps_issues_without_paths():
    exc = WorkflowDefinitionError("demo", [WorkflowIssue("bundle_invalid", "workflows", "no workflows")])
    error = translate(exc)
    assert (error.code, error.status) == ("invalid_definition", 422)
    assert error.details["workflow_id"] == "demo"
    assert error.details["issues"][0]["code"] == "bundle_invalid"


def test_request_validation_error_is_json_ready():
    with pytest.raises(ValidationError) as caught:
        RunRequest(workflow_id="bad id!", project_name="x")
    error = translate(caught.value)
    assert (error.code, error.status) == ("invalid_request", 422)
    payload = error.to_payload()
    assert json.loads(json.dumps(payload)) == payload
    assert set(payload) == {"code", "message", "details"}


def test_unknown_exceptions_are_not_translated():
    assert translate(RuntimeError("bug")) is None

    @service_errors
    def broken():
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        broken()


def test_decorator_raises_service_error_with_cause():
    @service_errors
    def missing():
        raise KeyError("Run not found: r1")

    with pytest.raises(ServiceError) as caught:
        missing()
    assert caught.value.code == "not_found"
    assert isinstance(caught.value.__cause__, KeyError)


def test_bundles_are_registered_by_name_and_paths_stay_private(tmp_path):
    services = ApplicationServices(object(), bundles={"spec_review": tmp_path})
    assert services.bundle_names() == ["spec_review"]
    assert services.bundle_path("spec_review") == tmp_path.resolve()
    with pytest.raises(ServiceError) as caught:
        services.bundle_path("other")
    assert caught.value.code == "not_found"
    assert str(tmp_path) not in json.dumps(caught.value.to_payload())


def test_bundle_names_are_validated(tmp_path):
    with pytest.raises(ValueError):
        ApplicationServices(object(), bundles={"../escape": tmp_path})
