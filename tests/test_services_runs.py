# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Run services of the facade: lifecycle, bundle runtimes and structured errors (G4a-T03)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.contracts import ExecutionMode, HumanStepSubmission
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                    gemini_api_key="", openai_api_key="", openai_enabled=False)


@pytest.fixture
def services(settings: Settings) -> ApplicationServices:
    services = ApplicationServices.create(settings, bundles={"team": BUNDLE}, dry_run=True)
    yield services
    services.runs.close()


def request(**overrides) -> RunRequest:
    return RunRequest(**{
        "project_name": "Synthetic review", "workflow_id": "spec_review",
        "inputs": {"spec_text": "Users can reset a password by email."},
        **overrides,
    })


def process(services: ApplicationServices) -> None:
    assert services.runs._bundle_runtime("team").worker.process_once()


def service_error(call, *args, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


def test_bundle_run_goes_through_both_reviews_to_completion(services):
    run = services.runs.create(request(), bundle="team")
    assert (run["status"], run["bundle"], run["workflow_id"]) == ("queued", "team", "spec_review")

    process(services)
    waiting = services.runs.get(run["id"])
    assert (waiting["status"], waiting["waiting_step"], waiting["bundle"]) == ("waiting_human", "assess_risks", "team")
    assert services.runs.approve(run["id"])["status"] == "queued"

    process(services)
    completed = services.runs.approve(run["id"])
    assert (completed["status"], completed["bundle"]) == ("completed", "team")
    artifacts = services.runs.artifacts(run["id"])
    assert len(artifacts) == 3
    assert json.loads(json.dumps(completed, default=str))["id"] == run["id"]


def test_bundle_runs_use_their_own_store(services):
    run = services.runs.create(request(), bundle="team")
    with pytest.raises(KeyError):
        services.system.store.get_run(run["id"])
    listed = services.runs.list()
    assert [(item["id"], item["bundle"]) for item in listed] == [(run["id"], "team")]


def test_review_decisions_follow_run_service_rules(services):
    run = services.runs.create(request(), bundle="team")
    error = service_error(services.runs.approve, run["id"])
    assert (error.code, error.status) == ("invalid_state", 409)

    process(services)
    error = service_error(services.runs.request_changes, run["id"], "   ")
    assert (error.code, error.status) == ("invalid_operation", 409)
    assert services.runs.request_changes(run["id"], "Explain the highest risk")["status"] == "queued"
    process(services)
    assert services.runs.regenerate(run["id"])["status"] == "queued"
    process(services)
    rejected = services.runs.reject(run["id"], "Out of scope")
    assert rejected["status"] == "rejected"


def test_cancel_and_idempotency(services):
    first = services.runs.create(request(idempotency_key="same-request"), bundle="team")
    assert services.runs.create(request(idempotency_key="same-request"), bundle="team")["id"] == first["id"]
    error = service_error(services.runs.create, request(idempotency_key="same-request", inputs={"spec_text": "Different"}), bundle="team")
    assert (error.code, error.status) == ("idempotency_conflict", 409)
    assert services.runs.cancel(first["id"])["status"] == "cancelled"


def test_human_guided_steps(services, settings):
    samples = load_bundle(BUNDLE, settings).sample_outputs
    run = services.runs.create(request(execution_mode=ExecutionMode.HUMAN_GUIDED), bundle="team")
    error = service_error(services.runs.human_next, run["id"])
    assert (error.code, error.status) == ("human_step_unavailable", 409)

    process(services)
    pending = services.runs.human_next(run["id"])
    assert (pending["step_id"], pending["bundle"]) == ("extract_requirements", "team")
    error = service_error(services.runs.human_submit, run["id"],
                          HumanStepSubmission(raw_response="not json", provider="synthetic_human"))
    assert (error.code, error.status) == ("human_submission_invalid", 422)

    state = services.runs.human_submit(run["id"], HumanStepSubmission(
        raw_response=json.dumps(samples[pending["expected_contract"]]), provider="synthetic_human"))
    assert state["bundle"] == "team" and state["current_step"] == 1


def test_unknown_run_and_bundle_are_not_found(services):
    for call in (services.runs.get, services.runs.approve, services.runs.artifacts, services.runs.human_next):
        error = service_error(call, "missing-run")
        assert (error.code, error.status) == ("not_found", 404)
    assert service_error(services.runs.create, request(), bundle="other").code == "not_found"


def test_unknown_workflow_is_not_found(services):
    error = service_error(services.runs.create, request())  # spec_review is not a built-in workflow
    assert error.code == "not_found"
    error = service_error(services.runs.create, request(workflow_id="missing"), bundle="team")
    assert error.code == "not_found"
