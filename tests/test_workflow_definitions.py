"""Workflow definitions must fail before any run work, with stable issue codes."""
from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.contracts import OUTPUT_SCHEMAS
from multiagent.workflow_validation import WorkflowDefinitionError, validate_workflow_definition
from multiagent.workflows import WorkflowCatalog, WorkflowDefinition, WorkflowStep

VALID = {
    "id": "sample",
    "max_iterations": 2,
    "steps": [
        {
            "id": "research",
            "agent": "researcher",
            "prompt_id": "researcher/research",
            "prompt_version": 2,
            "skills": [],
            "contract": "ResearchOutput",
            "preferred_model": "gemini_grounded",
            "fallback_model": None,
            "requires_web": True,
            "when": "requires_web",
            "checkpoint_after": False,
        },
        {
            "id": "create",
            "agent": "creator",
            "prompt_id": "creator/social_post",
            "prompt_version": 2,
            "skills": ["social_media"],
            "contract": "ContentOutput",
            "preferred_model": "local_default",
            "fallback_model": "cloud_fast",
            "requires_web": False,
            "when": None,
            "checkpoint_after": True,
        },
    ],
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", gemini_api_key="", openai_api_key="")


@pytest.fixture
def workflows_dir(tmp_path: Path) -> Path:
    path = tmp_path / "workflows"
    path.mkdir()
    return path


def definition_from(**changes) -> dict:
    raw = copy.deepcopy(VALID)
    raw.update(changes)
    return raw


def with_step(index: int, **changes) -> dict:
    raw = copy.deepcopy(VALID)
    raw["steps"][index].update(changes)
    return raw


def load_text(settings: Settings, workflows_dir: Path, text: str, workflow_id: str = "sample") -> WorkflowDefinition:
    (workflows_dir / f"{workflow_id}.yaml").write_text(text, encoding="utf-8")
    return WorkflowCatalog(settings, workflows_dir=workflows_dir).load(workflow_id)


def load_raw(settings: Settings, workflows_dir: Path, raw) -> WorkflowDefinition:
    return load_text(settings, workflows_dir, yaml.safe_dump(raw, sort_keys=False))


def load_error(settings: Settings, workflows_dir: Path, raw) -> WorkflowDefinitionError:
    with pytest.raises(WorkflowDefinitionError) as info:
        load_raw(settings, workflows_dir, raw)
    return info.value


def validate(definition: WorkflowDefinition, settings: Settings) -> None:
    validate_workflow_definition(
        definition, catalog=Catalog(settings), settings=settings, output_schemas=OUTPUT_SCHEMAS,
    )


def reference_error(settings: Settings, workflows_dir: Path, raw) -> WorkflowDefinitionError:
    definition = load_raw(settings, workflows_dir, raw)
    with pytest.raises(WorkflowDefinitionError) as info:
        validate(definition, settings)
    return info.value


def codes(error: WorkflowDefinitionError) -> set[tuple[str, str]]:
    return {(issue.code, issue.location) for issue in error.issues}


# ---------- valid definitions keep loading ----------

@pytest.mark.parametrize("workflow_id,step_ids", [
    ("social_post", ["research", "create"]),
    ("social_post_full", ["research", "strategy", "create", "design"]),
])
def test_bundled_workflows_load_and_validate(settings, workflow_id, step_ids):
    definition = WorkflowCatalog(settings).load(workflow_id)
    validate(definition, settings)
    assert definition.id == workflow_id
    assert [step.id for step in definition.steps] == step_ids
    assert definition.steps[-1].checkpoint_after is True


def test_valid_custom_definition_loads_from_explicit_directory(settings, workflows_dir):
    definition = load_raw(settings, workflows_dir, VALID)
    validate(definition, settings)
    assert definition.max_iterations == 2
    assert definition.steps[1].skills == ["social_media"]


def test_max_iterations_keeps_its_default(settings, workflows_dir):
    raw = copy.deepcopy(VALID)
    del raw["max_iterations"]
    assert load_raw(settings, workflows_dir, raw).max_iterations == 2


def test_missing_workflow_file_is_still_a_key_error(settings, workflows_dir):
    with pytest.raises(KeyError, match="Workflow not found: absent"):
        WorkflowCatalog(settings, workflows_dir=workflows_dir).load("absent")


# ---------- structural failures at load time ----------

def test_yaml_syntax_error_reports_line(settings, workflows_dir):
    with pytest.raises(WorkflowDefinitionError) as info:
        load_text(settings, workflows_dir, "id: sample\nsteps: [\n  - id: a\n")
    assert codes(info.value) == {("yaml_invalid", "<document>")}
    assert "line" in str(info.value)


def test_duplicate_yaml_key_is_not_silently_overwritten(settings, workflows_dir):
    text = yaml.safe_dump(VALID, sort_keys=False).replace(
        "  contract: ContentOutput\n", "  contract: ContentOutput\n  contract: StrategyOutput\n"
    )
    with pytest.raises(WorkflowDefinitionError) as info:
        load_text(settings, workflows_dir, text)
    assert codes(info.value) == {("yaml_invalid", "<document>")}
    assert "duplicate key 'contract'" in str(info.value)


@pytest.mark.parametrize("raw,expected", [
    ([VALID], {("root_not_mapping", "<root>")}),
    (None, {("root_not_mapping", "<root>")}),
    (definition_from(steps=[]), {("invalid_steps", "steps")}),
    (definition_from(steps={"research": VALID["steps"][0]}), {("invalid_steps", "steps")}),
    (definition_from(steps=["research"]), {("invalid_type", "steps[0]")}),
])
def test_malformed_root_or_step_list(settings, workflows_dir, raw, expected):
    assert codes(load_error(settings, workflows_dir, raw)) == expected


def test_missing_steps_and_unknown_root_field(settings, workflows_dir):
    raw = copy.deepcopy(VALID)
    del raw["steps"]
    raw["max_iteration"] = 3
    assert codes(load_error(settings, workflows_dir, raw)) == {
        ("missing_field", "steps"), ("unknown_field", "max_iteration"),
    }


def test_unknown_field_does_not_hide_value_problems(settings, workflows_dir):
    raw = with_step(0, when="sometimes", checkpoint_after=True)
    raw["max_iteration"] = 3
    raw["steps"][1]["notes"] = "draft"
    assert codes(load_error(settings, workflows_dir, raw)) == {
        ("unknown_field", "max_iteration"),
        ("unknown_field", "steps[1].notes"),
        ("unsupported_condition", "steps[0].when"),
        ("checkpoint_not_terminal", "steps[0].checkpoint_after"),
    }


def test_declared_id_must_match_requested_id(settings, workflows_dir):
    error = load_error(settings, workflows_dir, definition_from(id="other"))
    assert codes(error) == {("workflow_id_mismatch", "id")}
    assert "'other'" in str(error) and "'sample'" in str(error)


@pytest.mark.parametrize("workflow_id", ["../catalog/agents", "..\\x", "a/b", "", " sample"])
def test_unsafe_requested_workflow_id_is_rejected_before_reading(settings, workflows_dir, workflow_id):
    with pytest.raises(WorkflowDefinitionError) as info:
        WorkflowCatalog(settings, workflows_dir=workflows_dir).load(workflow_id)
    assert codes(info.value) == {("invalid_identifier", "<request>")}


def test_empty_and_duplicate_step_ids(settings, workflows_dir):
    raw = copy.deepcopy(VALID)
    raw["steps"][0]["id"] = ""
    raw["steps"].append({**raw["steps"][1], "checkpoint_after": False})
    raw["steps"][1], raw["steps"][2] = raw["steps"][2], raw["steps"][1]
    assert codes(load_error(settings, workflows_dir, raw)) == {
        ("invalid_identifier", "steps[0].id"),
        ("duplicate_step_id", "steps[2].id"),
    }


def test_step_id_must_be_safe_for_artifact_file_names(settings, workflows_dir):
    error = load_error(settings, workflows_dir, with_step(1, id="../create"))
    assert codes(error) == {("invalid_identifier", "steps[1].id")}


def test_step_field_typo_and_missing_field(settings, workflows_dir):
    raw = copy.deepcopy(VALID)
    raw["steps"][1]["checkpoint"] = True
    del raw["steps"][1]["checkpoint_after"]
    assert codes(load_error(settings, workflows_dir, raw)) == {
        ("unknown_field", "steps[1].checkpoint"), ("missing_field", "steps[1].checkpoint_after"),
    }


@pytest.mark.parametrize("when", ["web", "requires-web", "", True])
def test_unsupported_when_condition(settings, workflows_dir, when):
    error = load_error(settings, workflows_dir, with_step(0, when=when))
    assert codes(error) == {("unsupported_condition", "steps[0].when")}
    assert "not_requires_web" in str(error)


def test_not_requires_web_condition_is_supported(settings, workflows_dir):
    definition = load_raw(settings, workflows_dir, with_step(1, when="not_requires_web"))
    assert definition.steps[1].when == "not_requires_web"


@pytest.mark.parametrize("field,value,code", [
    ("requires_web", "yes", "invalid_value"),
    ("requires_web", None, "invalid_value"),
    ("checkpoint_after", "true", "invalid_type"),
    ("prompt_version", 0, "invalid_value"),
    ("prompt_version", True, "invalid_type"),
    ("prompt_version", "2", "invalid_type"),
    ("skills", "social_media", "invalid_type"),
    ("skills", ["social_media", ""], "invalid_reference"),
    ("fallback_model", 7, "invalid_type"),
    ("agent", "", "invalid_value"),
    ("prompt_id", "../README", "invalid_reference"),
    ("prompt_id", "/etc/passwd", "invalid_reference"),
    ("skills", ["production\\visual"], "invalid_reference"),
])
def test_step_values_the_engine_cannot_interpret(settings, workflows_dir, field, value, code):
    error = load_error(settings, workflows_dir, with_step(1, **{field: value}))
    assert codes(error) == {(code, f"steps[1].{field}")}


def test_requires_web_accepts_from_request(settings, workflows_dir):
    assert load_raw(settings, workflows_dir, with_step(1, requires_web="from_request")).steps[1].requires_web == "from_request"


@pytest.mark.parametrize("value,code", [(-1, "invalid_value"), ("2", "invalid_type"), (True, "invalid_type")])
def test_max_iterations_must_be_a_non_negative_integer(settings, workflows_dir, value, code):
    assert codes(load_error(settings, workflows_dir, definition_from(max_iterations=value))) == {(code, "max_iterations")}


def test_intermediate_checkpoint_is_rejected_explicitly(settings, workflows_dir):
    error = load_error(settings, workflows_dir, with_step(0, checkpoint_after=True))
    assert codes(error) == {("checkpoint_not_terminal", "steps[0].checkpoint_after")}
    assert "final step" in str(error)


def test_error_lists_every_problem_with_step_context(settings, workflows_dir):
    raw = with_step(1, when="sometimes", requires_web="maybe")
    error = load_error(settings, workflows_dir, raw)
    assert isinstance(error, ValueError)
    assert error.workflow_id == "sample"
    assert len(error.issues) == 2
    text = str(error)
    assert "steps[1].when (step 'create')" in text
    assert "steps[1].requires_web (step 'create')" in text


# ---------- reference failures before execution ----------

@pytest.mark.parametrize("index,field,value,code,location", [
    (1, "agent", "copywriter", "unknown_agent", "agent"),
    (1, "agent", "analyst", "inactive_agent", "agent"),
    (1, "contract", "PressRelease", "unknown_contract", "contract"),
    (1, "preferred_model", "gpt_unknown", "unknown_model", "preferred_model"),
    (1, "fallback_model", "gpt_unknown", "unknown_model", "fallback_model"),
    (1, "prompt_version", 9, "prompt_not_found", "prompt_id"),
    (1, "prompt_id", "creator/missing", "prompt_not_found", "prompt_id"),
    # Exact case keeps the result identical on case-insensitive (Windows) and Linux file systems.
    (1, "prompt_id", "Creator/Social_Post", "prompt_not_found", "prompt_id"),
    (1, "skills", ["social_media", "missing_skill"], "skill_not_found", "skills"),
    (1, "skills", ["production"], "skill_not_found", "skills"),
    (0, "preferred_model", "local_default", "model_incompatible", "preferred_model"),
])
def test_unresolved_references(settings, workflows_dir, index, field, value, code, location):
    error = reference_error(settings, workflows_dir, with_step(index, **{field: value}))
    assert codes(error) == {(code, f"steps[{index}].{location}")}
    assert repr(value if not isinstance(value, list) else value[-1]) in str(error)


def test_model_without_structured_output_is_incompatible(settings, workflows_dir):
    definition = load_raw(settings, workflows_dir, VALID)
    catalog = Catalog(settings)
    catalog.models["cloud_fast"] = dataclasses.replace(catalog.models["cloud_fast"], structured_output=False)
    with pytest.raises(WorkflowDefinitionError) as info:
        validate_workflow_definition(definition, catalog=catalog, settings=settings, output_schemas=OUTPUT_SCHEMAS)
    assert codes(info.value) == {("model_incompatible", "steps[1].fallback_model")}


def test_python_built_definition_gets_structural_checks_too(settings):
    step = WorkflowStep(**VALID["steps"][1])
    definition = WorkflowDefinition(id="sample", max_iterations=2, steps=[step, step])
    with pytest.raises(WorkflowDefinitionError) as info:
        validate(definition, settings)
    assert ("duplicate_step_id", "steps[1].id") in codes(info.value)
    assert ("checkpoint_not_terminal", "steps[0].checkpoint_after") in codes(info.value)


# ---------- public serialization ----------

def test_issue_dict_uses_the_agreed_keys_and_order(settings, workflows_dir):
    error = load_error(settings, workflows_dir, with_step(1, when="sometimes"))
    issue = error.issues[0]
    assert issue.to_dict() == {
        "code": "unsupported_condition", "location": "steps[1].when", "step_id": "create", "message": issue.message,
    }
    assert list(issue.to_dict()) == ["code", "location", "step_id", "message"]


def test_issue_without_step_serializes_null_step_id(settings, workflows_dir):
    error = load_error(settings, workflows_dir, definition_from(id="other"))
    assert error.issues[0].to_dict()["step_id"] is None


def test_payload_is_json_ready_and_omits_the_local_source_path(settings, workflows_dir):
    error = load_error(settings, workflows_dir, with_step(0, when="sometimes", checkpoint_after=True))
    assert error.source is not None and str(error.source) in str(error)
    payload = error.to_payload()
    assert json.loads(json.dumps(payload)) == payload
    texts = [payload["error"], *(value for issue in payload["issues"] for value in issue.values() if value)]
    assert not any(str(workflows_dir) in text or "sample.yaml" in text for text in texts)
    assert payload == {
        "error": "Invalid workflow definition 'sample': 2 problems",
        "workflow_id": "sample",
        "issues": [issue.to_dict() for issue in error.issues],
    }
