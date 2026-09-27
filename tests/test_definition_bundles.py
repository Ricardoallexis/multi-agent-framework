"""Definition bundles: a non-marketing workflow validated against its own resources."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from multiagent.bundles import load_bundle
from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.contracts import OUTPUT_SCHEMAS
from multiagent.prompts import PromptManager
from multiagent.workflow_validation import WorkflowDefinitionError, validate_workflow_definition

SPEC_REVIEW = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
PACKAGE = Path(__file__).resolve().parents[1] / "multiagent"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", gemini_api_key="", openai_api_key="")


@pytest.fixture
def bundle_copy(tmp_path: Path) -> Path:
    target = tmp_path / "spec_review"
    shutil.copytree(SPEC_REVIEW, target)
    return target


def edit_workflow(bundle: Path, index: int, **changes) -> None:
    path = bundle / "workflows" / "spec_review.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["steps"][index].update(changes)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def load_error(bundle: Path, settings: Settings) -> WorkflowDefinitionError:
    with pytest.raises(WorkflowDefinitionError) as info:
        load_bundle(bundle, settings).load_workflow("spec_review")
    return info.value


def test_spec_review_loads_with_its_own_steps_and_contracts(settings):
    bundle = load_bundle(SPEC_REVIEW, settings)
    definition = bundle.load_workflow("spec_review")
    assert [step.id for step in definition.steps] == ["extract_requirements", "assess_risks", "draft_test_plan"]
    assert [step.checkpoint_after for step in definition.steps] == [False, True, True]
    assert {step.contract for step in definition.steps} == {"RequirementsList", "RiskAssessment", "TestPlan"}
    assert set(bundle.catalog.agents) == {"requirements_analyst", "risk_reviewer", "test_planner"}


def test_bundle_contracts_extend_the_bundled_ones_without_changing_them(settings):
    bundle = load_bundle(SPEC_REVIEW, settings)
    assert set(OUTPUT_SCHEMAS) < set(bundle.output_schemas)
    assert "RequirementsList" not in OUTPUT_SCHEMAS


def test_every_bundle_contract_has_a_valid_sample_output(settings):
    bundle = load_bundle(SPEC_REVIEW, settings)
    assert set(bundle.sample_outputs) == {"RequirementsList", "RiskAssessment", "TestPlan"}
    for contract, sample in bundle.sample_outputs.items():
        bundle.output_schemas[contract].model_validate(sample)


def test_bundle_prompts_render_the_generic_step_placeholders(settings):
    bundle = load_bundle(SPEC_REVIEW, settings)
    text = bundle.prompts.load("spec/draft_test_plan", 1).text
    assert "{extract_requirements_output}" in text and "{assess_risks_output}" in text


def test_prompt_lookup_follows_the_given_prompt_manager(settings):
    bundle = load_bundle(SPEC_REVIEW, settings)
    definition = bundle.workflows.load("spec_review")
    common = {"catalog": bundle.catalog, "settings": settings, "output_schemas": bundle.output_schemas}
    validate_workflow_definition(definition, prompts=bundle.prompts, **common)
    with pytest.raises(WorkflowDefinitionError) as info:
        validate_workflow_definition(definition, **common)
    assert {issue.code for issue in info.value.issues} == {"prompt_not_found"}


def test_default_resources_are_unchanged(settings):
    assert Catalog(settings).catalog_dir == settings.catalog_dir
    prompts = PromptManager(settings)
    assert (prompts.prompts_dir, prompts.skills_dir) == (settings.prompts_dir, settings.skills_dir)


@pytest.mark.parametrize("index,field,value,code", [
    (0, "contract", "Minutes", "unknown_contract"),
    (1, "agent", "copywriter", "unknown_agent"),
    (2, "prompt_version", 2, "prompt_not_found"),
    (0, "preferred_model", "cloud_fast", "unknown_model"),
])
def test_broken_references_fail_before_any_run(settings, bundle_copy, index, field, value, code):
    edit_workflow(bundle_copy, index, **{field: value})
    assert {issue.code for issue in load_error(bundle_copy, settings).issues} == {code}


def test_bundle_without_workflows_is_rejected(settings, tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(WorkflowDefinitionError) as info:
        load_bundle(tmp_path / "empty", settings)
    assert info.value.issues[0].code == "bundle_invalid"


@pytest.mark.parametrize("source,code", [
    ("raise RuntimeError('broken')", "bundle_contracts_invalid"),
    ("OUTPUT_SCHEMAS = {'Minutes': dict}", "bundle_contracts_invalid"),
    ("from multiagent.contracts import ContentOutput\nOUTPUT_SCHEMAS = {'ContentOutput': ContentOutput}",
     "contract_conflict"),
])
def test_invalid_bundle_contracts_are_rejected(settings, bundle_copy, source, code):
    (bundle_copy / "contracts.py").write_text(source, encoding="utf-8")
    with pytest.raises(WorkflowDefinitionError) as info:
        load_bundle(bundle_copy, settings)
    assert info.value.issues[0].code == code


def test_sample_output_must_satisfy_its_contract(settings, bundle_copy):
    path = bundle_copy / "contracts.py"
    path.write_text(path.read_text(encoding="utf-8") + '\nSAMPLE_OUTPUTS["TestPlan"] = {"cases": []}\n', encoding="utf-8")
    with pytest.raises(WorkflowDefinitionError) as info:
        load_bundle(bundle_copy, settings)
    assert info.value.issues[0].code == "sample_output_invalid"


def test_the_package_holds_nothing_from_the_spec_review_domain():
    names = ("spec_review", "extract_requirements", "assess_risks", "draft_test_plan",
             "RequirementsList", "RiskAssessment", "TestPlan", "requirements_analyst")
    for source in PACKAGE.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert not [name for name in names if name in text], source
