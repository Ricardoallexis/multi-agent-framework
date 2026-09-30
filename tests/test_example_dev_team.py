# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The software_development example bundle works on its own in a clean environment (FW1-T04)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "teams" / "software_development"
STEPS = [
    "define_scope", "design_solution", "plan_backend", "plan_frontend",
    "plan_tests", "draft_documentation", "review_delivery",
]
REVIEWED_AFTER = ["design_solution", "review_delivery"]
PRIVATE_MARKERS = [
    ".local", "canal-pareja", "canal", "REGLAS", "Flujo", "AGENTES", "Claude", "Codex", "Copilot",
    "C:\\", "/home/", "/Users/", "AppData", "api_key", "API_KEY", "password=",
]


@pytest.fixture
def bundle_dir(request, tmp_path: Path) -> Path:
    """The example in the repository, or a lone copy of it in an empty folder."""
    if request.param == "repository":
        return EXAMPLE
    target = tmp_path / "isolated" / "software_development"
    shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


in_both_places = pytest.mark.parametrize("bundle_dir", ["repository", "isolated copy"], indirect=True)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    # One model call per step: the default budget (4) is smaller than the workflow.
    values = {"max_llm_calls": len(STEPS), **overrides}
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                    gemini_api_key="", openai_api_key="", openai_enabled=False, **values)


@in_both_places
def test_bundle_loads_and_validates(bundle_dir, tmp_path):
    bundle = load_bundle(bundle_dir, make_settings(tmp_path))
    definition = bundle.load_workflow("software_development")
    assert [step.id for step in definition.steps] == STEPS
    assert [step.id for step in definition.steps if step.checkpoint_after] == REVIEWED_AFTER
    assert {step.agent for step in definition.steps} <= set(bundle.catalog.agents)
    assert {step.contract for step in definition.steps} == set(bundle.sample_outputs)
    for contract, sample in bundle.sample_outputs.items():
        bundle.output_schemas[contract].model_validate(sample)


@in_both_places
def test_bundle_runs_end_to_end_in_mock_with_both_reviews(bundle_dir, tmp_path):
    services = ApplicationServices.create(make_settings(tmp_path), bundles={"team": bundle_dir}, dry_run=True)
    try:
        run = services.runs.create(RunRequest(
            project_name="Password reset", workflow_id="software_development",
            inputs={"feature_request": "Let users reset a forgotten password by email."},
        ), bundle="team")
        worker = services.runs._bundle_runtime("team").worker

        assert worker.process_once()
        waiting = services.runs.get(run["id"])
        assert (waiting["status"], waiting["waiting_step"]) == ("waiting_human", "design_solution")
        assert services.runs.approve(run["id"])["status"] == "queued"

        assert worker.process_once()
        waiting = services.runs.get(run["id"])
        assert (waiting["status"], waiting["waiting_step"]) == ("waiting_human", "review_delivery")
        done = services.runs.approve(run["id"])

        assert (done["status"], done["bundle"]) == ("completed", "team")
        events = [event["event_type"] for event in services.runs.events(run["id"])]
        assert "budget_exhausted" not in events
        assert events.count("step_completed") == len(STEPS)
        assert events.count("human_approved") == len(REVIEWED_AFTER)
        assert events[-1] == "workflow_completed"
        artifacts = {artifact["step_id"] for artifact in services.runs.artifacts(run["id"])}
        assert artifacts == set(STEPS)
    finally:
        services.runs.close()


def test_bundle_has_no_private_or_local_references():
    files = [path for path in EXAMPLE.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    assert files, "the example bundle is empty"
    assert {path.suffix for path in files} <= {".py", ".yaml", ".md"}
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert text.isascii(), f"{path.name} is not plain English/ASCII text"
        found = [marker for marker in PRIVATE_MARKERS if marker in text]
        assert not found, f"{path.relative_to(EXAMPLE)} mentions private or local details: {found}"


def test_core_package_stays_free_of_the_example_domain():
    for path in (ROOT / "multiagent").rglob("*.py"):
        assert "software_development" not in path.read_text(encoding="utf-8"), path.name
