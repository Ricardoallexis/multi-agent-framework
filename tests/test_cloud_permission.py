# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SEN-T02: saved office cloud permission, automatic routing and one-use grants."""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.contracts import SocialPostRequest
from multiagent.errors import ProviderUnavailable
from multiagent.gui.token import ensure_token
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError
from test_offices_install import ACTOR, invoke_cli, write_office


FIXTURE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
INPUTS = {"spec_text": "A synthetic specification."}


class CountingAdapter(LLMAdapter):
    def __init__(self, provider: str, *, fail: bool = False):
        self.provider = provider
        self.fail = fail
        self.calls: list[str] = []

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
        self.calls.append(spec.id)
        if self.fail:
            raise ProviderUnavailable("synthetic provider unavailable")
        payload = ({"requirements": [{"id": "R1", "text": "A synthetic requirement.", "priority": "must"}]}
                   if "requirements" in output_schema.model_fields      # the spec_review fixture
                   else {"objective": "A synthetic objective.", "key_findings": ["A synthetic finding."]})  # office areas
        parsed = output_schema.model_validate(payload)
        return LLMResponse(parsed, parsed.model_dump_json(), self.provider, spec.model, tokens_in=1, tokens_out=1)

    def health(self):
        return {"provider": self.provider, "available": True}


def configure_bundle(folder: Path, *, preferred: str = "local_default", fallback: str | None = "cloud_fast") -> None:
    workflow_path = folder / "workflows" / "spec_review.yaml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    step = workflow["steps"][0]
    workflow["steps"] = [step]
    step["preferred_model"] = preferred
    step["fallback_model"] = fallback
    workflow_path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")

    models_path = folder / "catalog" / "models.yaml"
    catalog = yaml.safe_load(models_path.read_text(encoding="utf-8"))
    local = catalog["models"]["local_default"]
    catalog["models"]["cloud_fast"] = {
        **local, "provider": "gemini", "model": "synthetic-cloud", "tier": "cloud",
        "capabilities": ["structured_output"], "permissions": ["provider:cloud"],
        "permissions_required": ["provider:cloud"], "allowed_data_classes": ["non_sensitive"],
    }
    if preferred == "local_other":
        catalog["models"]["local_other"] = {**local, "model": "synthetic-local"}
    models_path.write_text(yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8")


def configured_bundle(tmp_path: Path, *, preferred: str = "local_default",
                      fallback: str | None = "cloud_fast") -> Path:
    folder = tmp_path / "team"
    shutil.copytree(FIXTURE, folder)
    configure_bundle(folder, preferred=preferred, fallback=fallback)
    return folder


@pytest.fixture
def service_factory(tmp_path):
    opened: list[ApplicationServices] = []

    def build(*, bundle: Path | None = None, mock_mode: bool = False, dry_run: bool = False,
              local_fails: bool = False):
        local = CountingAdapter("ollama", fail=local_fails)
        cloud = CountingAdapter("gemini")
        settings = Settings(
            _env_file=None, local_dir=tmp_path / f"local-{len(opened)}", worker_enabled=False,
            mock_mode=mock_mode, bundle_scan="off", gemini_api_key="", openai_api_key="",
            openai_enabled=False, gui_token_file=tmp_path / f"gui-token-{len(opened)}",
        )
        services = ApplicationServices.create(
            settings, bundles={"team": bundle} if bundle is not None else None,
            dry_run=dry_run, adapter_overrides={"ollama": local, "gemini": cloud},
        )
        opened.append(services)
        return services, local, cloud

    yield build
    for services in opened:
        services.bundle_scans.close()
        services.runs.close()


def request(**fields) -> RunRequest:
    return RunRequest(workflow_id="spec_review", project_name="Cloud permission",
                      inputs=INPUTS, **fields)


def process(services: ApplicationServices, run: dict, bundle: str | None = None) -> dict:
    if bundle is None:
        assert services.system.worker.process_once()
    else:
        runtime = services.runs._bundle_runtime(bundle)
        assert runtime is not None and runtime.worker.process_once()
    return services.runs.get(run["id"])


def office_with_bundle(tmp_path: Path, *, areas: tuple[str, ...] = ("notices",),
                       overrides: dict | None = None, preferred: str = "local_default",
                       fallback: str | None = "cloud_fast") -> Path:
    levels = {area: {"sensitivity": "normal"} for area in areas}      # «Normal» unless the test says otherwise
    root = write_office(tmp_path / "office", office_id="demo", areas=areas, overrides={**levels, **(overrides or {})})
    for area in areas:
        configure_bundle(root / "areas" / area, preferred=preferred, fallback=fallback)
    return root


def approve_permission(services: ApplicationServices, approval_id: str) -> None:
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    headers = {"Authorization": f"Bearer {ensure_token(services.settings.gui_token_file)}"}
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/gui/api/approvals/session", headers=headers,
                           json={"code": "synthetic-start-code"}).status_code == 200
        challenge = client.post(f"/gui/api/approvals/{approval_id}/challenge", headers=headers, json={})
        assert challenge.status_code == 200
        decided = client.post(f"/gui/api/approvals/{approval_id}/decision", headers=headers,
                              json={"decision": "approve", "code": challenge.json()["code"]})
        assert decided.status_code == 200, decided.text


def grant_permission(services: ApplicationServices, area: str | None = None) -> dict:
    approval = services.offices.request_cloud_permission("demo", area)
    approve_permission(services, approval["approval_id"])
    return services.offices.grant_cloud_permission(
        "demo", area, approval_id=approval["approval_id"], actor=ACTOR)


def test_default_auto_never_calls_the_cloud(service_factory, tmp_path):
    services, local, cloud = service_factory(
        bundle=configured_bundle(tmp_path), local_fails=True)
    run = services.runs.create(request(), bundle="team")

    assert run["request"]["step_modes"] == {"extract_requirements": "local"}
    finished = process(services, run, "team")
    assert finished["status"] == "failed"
    assert local.calls == ["local_default"] and cloud.calls == []


def test_office_permission_is_inherited_and_area_wins(service_factory, tmp_path):
    services, _, _ = service_factory()
    root = office_with_bundle(tmp_path, areas=("notices", "alerts"))
    services.offices.import_folder(root, actor=ACTOR)

    grant_permission(services)
    services.offices.raise_level("demo", None, local_only=True, actor=ACTOR)
    shown = services.offices.privacy("demo")
    assert shown["cloud"]["allowed"] is True and shown["cloud"]["source"] == "person"
    areas = {item["id"]: item for item in shown["areas"]}
    assert areas["notices"]["cloud_effective"] is True and areas["notices"]["cloud"]["allowed"] is False
    assert services.offices.cloud_permission_for("demo__notices", "notices") is True

    services.offices.raise_level("demo", "notices", cloud_allowed=False, actor=ACTOR)
    shown = services.offices.privacy("demo")
    areas = {item["id"]: item for item in shown["areas"]}
    assert areas["notices"]["cloud_effective"] is False and areas["alerts"]["cloud_effective"] is True
    services.offices.raise_level("demo", None, cloud_allowed=False, actor=ACTOR)
    assert services.offices.cloud_permission_for("demo__alertas") is False
    grant = grant_permission(services, "notices")
    services.offices.raise_level("demo", "notices", sensitivity="sensitive", actor=ACTOR)
    areas = {item["id"]: item for item in services.offices.privacy("demo")["areas"]}
    assert areas["notices"]["cloud_effective"] is True
    assert areas["notices"]["cloud"] == {
        "allowed": True, "set_by": ACTOR, "set_at": grant["cloud_permission"]["set_at"], "source": "person"}
    assert areas["alerts"]["cloud_effective"] is False


def test_reimport_keeps_the_person_permission(service_factory, tmp_path):
    services, _, _ = service_factory()
    root = office_with_bundle(tmp_path)
    services.offices.import_folder(root, actor=ACTOR)
    grant_permission(services, "notices")

    services.offices.reimport_folder("demo", root, actor=ACTOR)

    privacy = services.offices.privacy("demo")
    area = privacy["areas"][0]
    assert privacy["cloud"]["allowed"] is False
    assert area["cloud"]["allowed"] is True and area["cloud"]["set_by"] == ACTOR
    assert area["cloud"]["set_at"] and area["cloud"]["source"] == "person"
    assert area["cloud_effective"] is True
    entry = json.loads((services.settings.data_dir / "installed" / "offices.json").read_text(encoding="utf-8"))
    assert "cloud_allowed" not in entry["offices"]["demo"]["definition"]["areas"][0]


def test_run_without_office_needs_confirm_cloud(service_factory, tmp_path):
    services, local, cloud = service_factory(bundle=configured_bundle(tmp_path), local_fails=True)
    local_run = services.runs.create(request(), bundle="team")
    assert local_run["request"]["confirm_cloud"] is False
    assert process(services, local_run, "team")["status"] == "failed"
    assert cloud.calls == []

    confirmed = services.runs.create(request(confirm_cloud=True), bundle="team")
    finished = process(services, confirmed, "team")
    assert finished["status"] == "completed" and cloud.calls == ["cloud_fast"]
    next_run = services.runs.create(request(), bundle="team")
    assert next_run["request"]["confirm_cloud"] is False
    assert not [event for event in services.runs.get(next_run["id"])["events"]
                if event["event_type"] == "cloud_permission_applied"]


def test_cloud_preferred_step_is_rejected_before_saving(service_factory, tmp_path):
    services, _, cloud = service_factory(
        bundle=configured_bundle(tmp_path, preferred="cloud_fast", fallback=None))

    with pytest.raises(ServiceError) as caught:
        services.runs.create(request(), bundle="team")

    error = caught.value
    assert (error.code, error.status, error.details) == (
        "cloud_permission_required", 422, {"step": "extract_requirements", "model": "cloud_fast"})
    assert services.runs.list(limit=10) == [] and cloud.calls == []


def test_other_local_preferred_switches_to_local_default(service_factory, tmp_path):
    services, local, cloud = service_factory(
        bundle=configured_bundle(tmp_path, preferred="local_other"), local_fails=False)
    run = services.runs.create(request(), bundle="team")

    assert run["request"]["step_modes"] == {"extract_requirements": "local"}
    assert process(services, run, "team")["status"] == "completed"
    assert local.calls == ["local_default"] and cloud.calls == []


def test_permission_reaches_a_profiled_step(service_factory, tmp_path):
    services, _, cloud = service_factory(local_fails=True)
    root = office_with_bundle(tmp_path, preferred="local_default", fallback=None)
    services.offices.import_folder(root, actor=ACTOR)
    runtime = services.runs._bundle_runtime("demo__notices")
    assert runtime is not None
    runtime.service.engine.catalog.routing_assignments["spec_review"] = {"extract_requirements": "cloud-only"}
    runtime.service.engine.catalog.routing_profiles["cloud-only"] = {
        "enabled": True, "candidate_model_ids": ["cloud_fast"], "max_candidates": 1,
        "max_provider_invocations": 1, "max_retries_per_candidate": 0,
        "required_capabilities": ["structured_output"], "required_roles": [],
        "required_permissions": [], "allowed_permissions": ["provider:cloud"],
        "allowed_data_classes": ["non_sensitive"], "execution_modes": ["auto"],
        "require_health": False, "economic_selection": False,
    }
    models = runtime.service.engine.catalog.models      # the profile's only candidate can do the step's role
    models["cloud_fast"] = dataclasses.replace(models["cloud_fast"], roles=frozenset({"requirements_analyst"}))

    denied = services.runs.create(request(), bundle="demo__notices")
    process(services, denied, "demo__notices")
    assert denied["request"]["confirm_cloud"] is False and cloud.calls == []

    grant_permission(services, "notices")
    allowed = services.runs.create(request(), bundle="demo__notices")
    finished = process(services, allowed, "demo__notices")
    events = [event for event in finished["events"] if event["event_type"] == "cloud_permission_applied"]
    assert finished["status"] == "completed" and cloud.calls == ["cloud_fast"]
    assert allowed["request"]["confirm_cloud"] is True and len(events) == 1
    assert {key: events[0]["payload"][key] for key in ("office", "area", "scope", "set_by", "set_at")} == {
        "office": "demo", "area": "notices", "scope": "area", "set_by": ACTOR,
        "set_at": services.offices.privacy("demo")["areas"][0]["cloud"]["set_at"],
    }


def test_revoking_applies_to_new_runs(service_factory, tmp_path):
    services, _, _ = service_factory(bundle=None, local_fails=True)
    root = office_with_bundle(tmp_path)
    services.offices.import_folder(root, actor=ACTOR)
    assert services.runs._bundle_runtime("demo__notices") is not None
    grant_permission(services)

    first = services.runs.create(request(), bundle="demo__notices")
    first_saved = services.runs.get(first["id"])
    assert first_saved["request"]["confirm_cloud"] is True
    assert [event["event_type"] for event in first_saved["events"]].count("cloud_permission_applied") == 1

    services.offices.raise_level("demo", None, cloud_allowed=False, actor=ACTOR)
    following = services.runs.create(request(), bundle="demo__notices")
    following_saved = services.runs.get(following["id"])
    assert following_saved["request"]["confirm_cloud"] is False
    assert following_saved["request"]["step_modes"] == {"extract_requirements": "local"}
    assert not [event for event in following_saved["events"] if event["event_type"] == "cloud_permission_applied"]


def test_area_without_level_ignores_the_permission(service_factory, tmp_path):
    services, _, cloud = service_factory(local_fails=True)
    root = office_with_bundle(tmp_path, areas=("nueva",), overrides={"nueva": {}})
    services.offices.import_folder(root, actor=ACTOR)
    assert services.runs._bundle_runtime("demo__nueva") is not None
    assert services.offices.area_class("demo__nueva") is None
    grant_permission(services)

    run = services.runs.create(request(), bundle="demo__nueva")
    saved = process(services, run, "demo__nueva")
    assert saved["request"]["confirm_cloud"] is False and cloud.calls == []
    assert saved["request"]["step_modes"] == {"extract_requirements": "local"}
    assert not [event for event in saved["events"] if event["event_type"] == "cloud_permission_applied"]


def test_person_confirmation_has_no_event(service_factory, tmp_path):
    services, _, _ = service_factory(bundle=configured_bundle(tmp_path))
    run = services.runs.create(request(confirm_cloud=True), bundle="team")
    saved = services.runs.get(run["id"])

    assert saved["request"]["confirm_cloud"] is True
    assert not [event for event in saved["events"] if event["event_type"] == "cloud_permission_applied"]


def test_sensitive_and_restricted_win(service_factory, tmp_path):
    services, _, cloud = service_factory()
    root = office_with_bundle(
        tmp_path, areas=("sensible", "restringida"),
        overrides={"sensible": {"sensitivity": "sensitive"}, "restringida": {"local_only": True}},
    )
    services.offices.import_folder(root, actor=ACTOR)
    assert services.runs._bundle_runtime("demo__sensible") is not None
    assert services.runs._bundle_runtime("demo__restringida") is not None
    grant_permission(services)

    sensitive = services.runs.create(request(confirm_cloud=True), bundle="demo__sensible")
    sensitive_saved = process(services, sensitive, "demo__sensible")
    assert sensitive_saved["request"]["sensitive"] is True and cloud.calls == []
    assert not [event for event in sensitive_saved["events"] if event["event_type"] == "cloud_permission_applied"]

    restricted = services.runs.create(request(), bundle="demo__restringida")
    restricted_saved = process(services, restricted, "demo__restringida")
    assert restricted_saved["request"]["sensitive"] is True and cloud.calls == []
    with pytest.raises(ServiceError) as caught:
        services.runs.create(request(confirm_cloud=True), bundle="demo__restringida")
    assert caught.value.details["reason"] == "area_local_only"


def test_granting_needs_the_one_use_code(service_factory, tmp_path, monkeypatch, capsys):
    services, _, _ = service_factory()
    root = office_with_bundle(tmp_path)
    services.offices.import_folder(root, actor=ACTOR)
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    headers = {"Authorization": f"Bearer {ensure_token(services.settings.gui_token_file)}"}

    with TestClient(app, base_url="http://localhost") as client:
        requested = client.post("/api/v1/offices/demo/privacy/cloud-request", json={"area": "notices"})
        assert requested.status_code == 200
        approval = requested.json()
        assert set(approval) == {"approval_id", "sha256"}
        pending = services.user_approvals.peek(approval["approval_id"])
        assert pending is not None and pending["kind"] == "office_privacy_change" and pending["state"] == "pending"
        assert approval["sha256"] == pending["sha256"]
        with pytest.raises(ServiceError) as caught:
            services.offices.grant_cloud_permission(
                "demo", "notices", approval_id=approval["approval_id"], actor=ACTOR)
        assert (caught.value.status, caught.value.details["reason"]) == (
            409, "office_privacy_change_not_approved")

        assert client.post("/gui/api/approvals/session", headers=headers,
                           json={"code": "synthetic-start-code"}).status_code == 200
        challenge = client.post(
            f"/gui/api/approvals/{approval['approval_id']}/challenge", headers=headers, json={})
        assert challenge.status_code == 200
        decided = client.post(f"/gui/api/approvals/{approval['approval_id']}/decision", headers=headers,
                              json={"decision": "approve", "code": challenge.json()["code"]})
        assert decided.status_code == 200
        granted = services.offices.grant_cloud_permission(
            "demo", "notices", approval_id=approval["approval_id"], actor=ACTOR)
        assert granted["areas"][0]["cloud_effective"] is True
        with pytest.raises(ServiceError) as spent:
            services.offices.grant_cloud_permission(
                "demo", "notices", approval_id=approval["approval_id"], actor=ACTOR)
        assert spent.value.details["reason"] == "office_privacy_change_not_approved"

        routes = {route.path for route in client.app.routes}
        assert "/api/v1/offices/{office_id}/privacy/cloud-request" in routes
        assert not any(path.startswith("/api/v1/offices/") and path.endswith("/privacy/cloud") for path in routes)
        invoked = invoke_cli(monkeypatch, capsys, client,
                             ["office", "privacy", "demo", "--area", "notices", "--no-cloud"])
        assert json.loads(invoked)["areas"][0]["cloud_effective"] is False
        requested_cli = json.loads(invoke_cli(
            monkeypatch, capsys, client, ["office", "privacy", "demo", "--request-cloud"]))
        assert requested_cli["approval_id"] and requested_cli["sha256"]
        assert "Privacy" in requested_cli["message"]
        revoked = client.post("/api/v1/offices/demo/privacy/raise",
                              json={"cloud_allowed": False, "actor": ACTOR})
        assert revoked.status_code == 200 and revoked.json()["cloud"]["allowed"] is False


@pytest.mark.parametrize(("mock_mode", "dry_run"), [(True, False), (False, True)])
def test_mock_changes_nothing(service_factory, tmp_path, mock_mode, dry_run):
    services, _, cloud = service_factory(
        bundle=configured_bundle(tmp_path, preferred="cloud_fast", fallback=None),
        mock_mode=mock_mode, dry_run=dry_run,
    )

    run = services.runs.create(request(), bundle="team")

    assert run["request"]["confirm_cloud"] is False and run["request"]["step_modes"] == {}
    assert cloud.calls == []


def test_social_post_request_confirm_cloud(service_factory, tmp_path):
    services, _, _ = service_factory(bundle=configured_bundle(tmp_path))
    local = SocialPostRequest(
        project_name="social local", objective="Create an educational post", topic="KNX")
    created = services.runs.create(local)
    assert created["request"]["confirm_cloud"] is False
    assert created["request"]["step_modes"] == {"create": "local"}

    web = SocialPostRequest(
        project_name="social web", objective="Create an educational post", topic="KNX",
        requires_web=True)
    with pytest.raises(ServiceError) as caught:
        services.runs.create(web)
    assert (caught.value.code, caught.value.status, caught.value.details) == (
        "cloud_permission_required", 422, {"step": "research", "model": "gemini_grounded"})
    assert [item["id"] for item in services.runs.list(limit=10)] == [created["id"]]

    confirmed = services.runs.create(web.model_copy(update={"confirm_cloud": True}))
    assert confirmed["request"]["confirm_cloud"] is True
