# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Cloud confirmation is enforced by the run service, including direct callers."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.catalog import Catalog, CatalogError, ModelSpec
from multiagent.config import Settings
from multiagent.contracts import ExecutionMode
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError
from test_offices_install import ACTOR, write_office


FIXTURE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


class CountingCloudAdapter(LLMAdapter):
    provider = "gemini"

    def __init__(self):
        self.calls: list[str] = []

    def generate_structured(
        self, *, spec: ModelSpec, prompt: str, output_schema: type[BaseModel],
        requires_web: bool = False, effort: str | None = None,
    ) -> LLMResponse:
        self.calls.append(spec.id)
        sample = {"requirements": [{"id": "R1", "text": "A synthetic requirement.", "priority": "must"}]}
        parsed = output_schema.model_validate(sample)
        return LLMResponse(parsed, parsed.model_dump_json(), self.provider, spec.model, tokens_in=1, tokens_out=1)

    def health(self) -> dict[str, object]:
        return {"provider": self.provider, "available": True}


@pytest.fixture
def world(tmp_path):
    bundle = tmp_path / "cloud-only"
    shutil.copytree(FIXTURE, bundle)
    models = bundle / "catalog" / "models.yaml"
    text = models.read_text(encoding="utf-8").replace("provider: ollama", "provider: gemini")
    text = text.replace("tier: local", "tier: cloud")
    models.write_text(text, encoding="utf-8")

    cloud = CountingCloudAdapter()
    settings = Settings(
        _env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=False,
        bundle_scan="off", gemini_api_key="", openai_api_key="", openai_enabled=False,
        gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(
        settings, bundles={"team": bundle}, dry_run=False,
        adapter_overrides={"gemini": cloud},
    )
    yield services, cloud, tmp_path
    services.bundle_scans.close()
    services.runs.close()


def request(**fields) -> RunRequest:
    return RunRequest(
        workflow_id="spec_review", project_name="Cloud confirmation",
        inputs={"spec_text": "A synthetic specification."}, **fields,
    )


def assert_cloud_confirmation_error(error: ServiceError) -> None:
    assert (error.code, error.status) == ("cloud_confirmation_required", 422)


def test_services_create_refuses_cloud_mode_without_confirmation(world):
    services, _, _ = world

    with pytest.raises(ServiceError) as caught:
        services.runs.create(request(execution_mode=ExecutionMode.CLOUD), bundle="team")

    assert_cloud_confirmation_error(caught.value)
    assert services.runs.list(limit=10) == []


def test_services_create_refuses_step_cloud_mode_without_confirmation(world):
    services, _, _ = world

    with pytest.raises(ServiceError) as caught:
        services.runs.create(
            request(step_modes={"extract_requirements": ExecutionMode.CLOUD}), bundle="team"
        )

    assert_cloud_confirmation_error(caught.value)
    assert services.runs.list(limit=10) == []


def test_services_create_refuses_cloud_preference_without_confirmation(world):
    services, _, _ = world

    with pytest.raises(ServiceError) as caught:
        services.runs.create(
            request(routing_preferences={"extract_requirements": {"model": "local_default"}}),
            bundle="team",
        )

    assert_cloud_confirmation_error(caught.value)
    assert services.runs.list(limit=10) == []


def test_services_create_with_confirmation_still_works(world):
    services, _, _ = world

    run = services.runs.create(
        request(execution_mode=ExecutionMode.CLOUD, confirm_cloud=True), bundle="team"
    )

    assert run["request"]["confirm_cloud"] is True
    assert services.runs.get(run["id"])["id"] == run["id"]


def test_sensitive_area_confirmation_does_nothing(world):
    services, _, tmp_path = world
    office = write_office(
        tmp_path / "office", office_id="demo", areas=("proposal",),
        overrides={"proposal": {"sensitivity": "sensitive"}},
    )
    services.offices.import_folder(office, actor=ACTOR)

    run = services.runs.create(request(confirm_cloud=True), bundle="demo__proposal")
    saved = services.runs.get(run["id"])

    assert saved["request"]["sensitive"] is True
    assert not saved["request"].get("effective_permissions")
    assert not saved["request"].get("authorized_data_classes")


def test_auto_fixed_cloud_model_with_confirmation_is_saved(world):
    services, cloud, _ = world

    run = services.runs.create(request(confirm_cloud=True), bundle="team")

    assert run["request"]["confirm_cloud"] is True
    assert services.runs.get(run["id"])["id"] == run["id"]
    assert cloud.calls == []


@pytest.mark.parametrize("confirm_cloud", [False, True])
def test_local_mode_rejects_fixed_cloud_model(world, confirm_cloud):
    services, cloud, _ = world

    with pytest.raises(ServiceError) as caught:
        services.runs.create(
            request(execution_mode=ExecutionMode.LOCAL, confirm_cloud=confirm_cloud),
            bundle="team",
        )

    error = caught.value
    assert (error.code, error.status) == ("invalid_definition", 422)
    assert error.details == {
        "reason": "local_model_not_local", "step": "extract_requirements", "model": "local_default",
    }
    assert services.runs.list(limit=10) == [] and cloud.calls == []


def test_sensitive_auto_rejects_fixed_cloud_model(world):
    services, cloud, _ = world

    with pytest.raises(ServiceError) as caught:
        services.runs.create(request(sensitive=True), bundle="team")

    error = caught.value
    assert (error.code, error.status) == ("invalid_definition", 422)
    assert error.details == {
        "reason": "local_model_not_local", "step": "extract_requirements", "model": "local_default",
    }
    assert services.runs.list(limit=10) == [] and cloud.calls == []


def test_local_fixed_model_control_still_creates_run(tmp_path):
    cloud_double = CountingCloudAdapter()
    settings = Settings(
        _env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=False,
        bundle_scan="off", gemini_api_key="", openai_api_key="", openai_enabled=False,
        gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(
        settings, bundles={"team": FIXTURE}, dry_run=False,
        adapter_overrides={"ollama": cloud_double},
    )
    try:
        run = services.runs.create(request(), bundle="team")

        assert run["request"]["confirm_cloud"] is False
        assert services.runs.get(run["id"])["id"] == run["id"]
        assert cloud_double.calls == []
    finally:
        services.bundle_scans.close()
        services.runs.close()


def test_cloud_model_gets_no_call_without_confirmation(world):
    services, cloud, _ = world
    runtime = services.runs._bundle_runtime("team")
    assert runtime is not None
    assert set(runtime.service.engine.catalog.models) == {"local_default"}
    assert runtime.service.engine.catalog.model("local_default").tier == "cloud"

    with pytest.raises(ServiceError) as caught:
        services.runs.create(request(execution_mode=ExecutionMode.CLOUD), bundle="team")

    assert_cloud_confirmation_error(caught.value)
    assert cloud.calls == []
    assert services.runs.list(limit=10) == []


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_catalog_rejects_cloud_provider_declared_local(tmp_path, provider):
    folder = tmp_path / "catalog"
    folder.mkdir()
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (folder / "models.yaml").write_text(
        yaml.safe_dump({"models": {"m": {
            "provider": provider, "model": "synthetic", "tier": "local",
            "structured_output": True, "web": False,
        }}}),
        encoding="utf-8",
    )

    with pytest.raises(CatalogError, match="Model 'm'"):
        Catalog(Settings(_env_file=None, local_dir=tmp_path / "local"), catalog_dir=folder)


def test_catalog_allows_ollama_declared_cloud(tmp_path):
    folder = tmp_path / "catalog"
    folder.mkdir()
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (folder / "models.yaml").write_text(
        yaml.safe_dump({"models": {"m": {
            "provider": "ollama", "model": "synthetic", "tier": "cloud",
            "structured_output": True, "web": False,
        }}}),
        encoding="utf-8",
    )

    catalog = Catalog(Settings(_env_file=None, local_dir=tmp_path / "local"), catalog_dir=folder)

    assert catalog.model("m").provider == "ollama"
    assert catalog.model("m").tier == "cloud"
