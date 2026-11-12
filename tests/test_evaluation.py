from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from multiagent.config import Settings
from multiagent.evaluation import EvalSet, load_eval_set, run_eval_set
from multiagent.evaluation.cases import CheckSpec
from multiagent.evaluation.checks import evaluate_check
from multiagent.services import ApplicationServices

EXAMPLE = Path(__file__).parent / "evals" / "example.yaml"


@pytest.fixture
def services(tmp_path):
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )
    return ApplicationServices.create(settings, dry_run=True)


def test_loads_valid_set_and_rejects_unknown_check(tmp_path):
    loaded = load_eval_set(EXAMPLE)
    assert loaded.name == "synthetic-social-post"
    assert len(loaded.cases) == 3

    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["cases"][0]["checks"][0]["type"] = "unknown"
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError, match="Unknown check type"):
        load_eval_set(invalid)


def test_rejects_invalid_yaml_and_duplicate_case_ids(tmp_path):
    invalid_yaml = tmp_path / "malformed.yaml"
    invalid_yaml.write_text("cases: [", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid evaluation YAML"):
        load_eval_set(invalid_yaml)

    duplicate = {
        "name": "invalid",
        "version": "1",
        "cases": [
            {"id": "same", "workflow": "social_post"},
            {"id": "same", "workflow": "social_post"},
        ],
    }
    with pytest.raises(ValidationError, match="unique"):
        EvalSet.model_validate(duplicate)


@pytest.mark.parametrize(
    ("config", "artifact", "passed"),
    [
        ({"type": "contract_valid"}, {"kind": "ContentOutput", "data": {"title": "Hi", "hook": "A useful hook", "body": "A" * 30}}, True),
        ({"type": "contract_valid"}, {"kind": "ContentOutput", "data": {"title": ""}}, False),
        ({"type": "field_present", "field": "title"}, {"data": {"title": "A title"}}, True),
        ({"type": "field_present", "field": "missing"}, {"data": {"title": "A title"}}, False),
        ({"type": "equals", "field": "title", "expected": "Hello"}, {"data": {"title": "Hello"}}, True),
        ({"type": "equals", "field": "title", "expected": "No"}, {"data": {"title": "Hello"}}, False),
        ({"type": "contains", "field": "body", "expected": "garden"}, {"data": {"body": "A garden grows"}}, True),
        ({"type": "contains", "field": "body", "expected": "garden"}, {"data": {"body": "A park grows"}}, False),
        ({"type": "not_contains", "field": "body", "expected": "secret"}, {"data": {"body": "A public message"}}, True),
        ({"type": "not_contains", "field": "body", "expected": "secret"}, {"data": {"body": "A secret message"}}, False),
        ({"type": "number_between", "field": "count", "min": 2, "max": 5}, {"data": {"count": 3}}, True),
        ({"type": "number_between", "field": "count", "min": 2, "max": 5}, {"data": {"count": 6}}, False),
        ({"type": "max_length", "field": "title", "max_length": 5}, {"data": {"title": "Short"}}, True),
        ({"type": "max_length", "field": "title", "max_length": 4}, {"data": {"title": "Longer"}}, False),
    ],
)
def test_check_types_pass_and_fail(config, artifact, passed):
    result = evaluate_check(CheckSpec.model_validate(config), artifact)
    assert result.passed is passed
    assert result.detail


def test_unknown_check_type_fails_model_validation():
    with pytest.raises(ValidationError, match="Unknown check type"):
        CheckSpec.model_validate({"type": "provider_call"})


def test_example_report_is_reproducible_and_has_expected_failure(services):
    assert services.system.engine.router.dry_run
    eval_set = load_eval_set(EXAMPLE)
    first = run_eval_set(services, eval_set)
    second = run_eval_set(services, eval_set)

    assert first.score == second.score
    assert first.model_dump(exclude={"meta"}) == second.model_dump(exclude={"meta"})
    assert first.score < 1.0
    assert first.cases[0].passed and first.cases[1].passed
    assert not first.cases[2].passed
    assert [check.passed for check in first.cases[2].checks] == [True, True, False]
    assert first.meta["run_ids"] != second.meta["run_ids"]


def test_runner_requires_mock_mode(services):
    services.system.engine.router.dry_run = False
    with pytest.raises(ValueError, match="requires Mock mode"):
        run_eval_set(services, load_eval_set(EXAMPLE))


def test_eval_case_failure_lowers_report_score(services):
    eval_set = EvalSet.model_validate({
        "name": "single-failure",
        "version": "1",
        "cases": [{
            "id": "failed-check",
            "workflow": "social_post",
            "inputs": {
                "topic": "Public libraries",
                "platform": "LinkedIn",
                "audience": "Readers",
                "instructions": "Be clear.",
                "objective": "Encourage visits to public libraries.",
            },
            "checks": [
                {"type": "contract_valid"},
                {"type": "contains", "field": "title", "expected": "__not_present__"},
            ],
        }],
    })
    report = run_eval_set(services, eval_set)
    assert report.score == 0.5
    assert report.cases[0].score == 0.5
