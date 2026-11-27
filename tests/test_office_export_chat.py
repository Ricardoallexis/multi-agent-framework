# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Conversation-assisted office export with synthetic installed copies."""
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.contracts import OfficeExportProposal
from multiagent.document_trust import FIXED_CLAUSE
from multiagent.errors import QuotaExceeded
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.new_session import _BudgetCut, _Refused
from test_office_export import world, write_office


def _sample():
    return {"summary": "A synthetic office for review.", "keep": ["areas/reception/README.md"],
            "omit": [".env", "areas/reception/samples/example.txt"], "questions": ["Share the examples?"]}


def _world(world, monkeypatch):
    start, tmp = world
    source = write_office(tmp / "source")
    (source / "areas" / "reception" / "samples").mkdir(exist_ok=True)
    (source / "areas" / "reception" / "samples" / "example.txt").write_text("Example", encoding="utf-8")
    services = start()
    services.offices.import_folder(source, actor="user:local")
    package = services.installed._folder_of(services.installed._read()["demo__reception"])
    # These private neighbors may exist in a copy even if the installer did not copy them.
    for name in (".env", "runs/a.txt", "conversations/a.txt", "inputs/a.txt",
                 "logs/a.txt", ".local/a.txt", "file_tools/a.txt", "state.db"):
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE_NEIGHBOR", encoding="utf-8")
    return services, package


@pytest.fixture
def real_world(tmp_path):
    opened = []

    def start(*, model="local_default", adapter=None, file_tools_root=None, egress_default="off"):
        settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                            mock_mode=False, bundle_scan="off", gui_token_file=tmp_path / "gui-token",
                            gui_new_session_model=model, file_tools_root=file_tools_root,
                            egress_default=egress_default, gemini_api_key="", openai_api_key="",
                            openai_enabled=False)
        provider = "ollama" if model == "local_default" else "gemini"
        services = ApplicationServices.create(
            settings, dry_run=False,
            adapter_overrides={provider: adapter or FakeAdapter(sample_outputs={"OfficeExportProposal": _sample()})})
        opened.append(services)
        source = write_office(tmp_path / "source")
        services.offices.import_folder(source, actor="user:local")
        package = services.installed._folder_of(services.installed._read()["demo__reception"])
        return services, source, package

    yield start
    for services in opened:
        services.bundle_scans.close()
        services.runs.close()


def _client(services):
    token = ensure_token(services.settings.gui_token_file)
    return TestClient(create_app(services=services), base_url="http://localhost"), {
        "Authorization": f"Bearer {token}"}


def test_real_model_path_drafts_and_screens_configured_tools_root(real_world, monkeypatch):
    services, _, package = real_world(file_tools_root=Path("unused"))
    custom = package / "custom_private" / "nested" / "note.txt"
    custom.parent.mkdir(parents=True)
    custom.write_text("PRIVATE_NEIGHBOR", encoding="utf-8")
    object.__setattr__(services.settings, "file_tools_root", custom.parent.parent)
    prompts = []
    adapter = services.system.engine.router.adapters["ollama"]
    original = adapter.generate_structured

    def capture(**kwargs):
        prompts.append(kwargs["prompt"])
        return original(**kwargs)

    monkeypatch.setattr(adapter, "generate_structured", capture)
    drafted = services.office_package_chat.draft("demo", "Share this office")
    assert drafted["source"] == "model"
    assert prompts and prompts[0].startswith(FIXED_CLAUSE)
    assert "PRIVATE_NEIGHBOR" not in prompts[0]
    assert all("custom_private" not in path for path in drafted["included"] + drafted["excluded"])
    result = services.office_package_chat.download("demo", version=drafted["version"])
    with zipfile.ZipFile(io.BytesIO(result["data"])) as archive:
        assert all("custom_private" not in path for path in archive.namelist())


def test_real_model_path_limit_reached_has_notice(real_world, monkeypatch):
    services, _, _ = real_world()
    adapter = services.system.engine.router.adapters["ollama"]

    def limited(**kwargs):
        raise QuotaExceeded("Synthetic limit")

    monkeypatch.setattr(adapter, "generate_structured", limited)
    result = services.office_package_chat.draft("demo", "Share this office")
    assert result["reason"] == "limit_reached"
    assert result["limit"]["code"] == "limit_reached"
    assert result["limit"]["target"] == "local_default"


def test_real_model_path_secret_review_and_approved_retry(real_world, monkeypatch):
    import multiagent.secret_gate as gate
    services, _, _ = real_world(model="cloud_fast", egress_default="off")
    monkeypatch.setattr(services.offices, "privacy", lambda _: {
        "local_only": False, "areas": [{"data_class": "non_sensitive", "cloud_effective": True}]})
    monkeypatch.setattr(gate, "scan_text", lambda value: [(0, 12, "secret_key")])
    held = services.office_package_chat.draft("demo", "Share this office", confirm_cloud=True)
    assert held["reason"] == "secret_review_required"
    assert held["approval_id"]
    services.user_approvals.decide(held["approval_id"], "approve")
    retried = services.office_package_chat.draft(
        "demo", "Share this office", confirm_cloud=True, approval_id=held["approval_id"])
    assert retried["source"] == "model"
    assert services.user_approvals.status(held["approval_id"])["state"] == "consumed"


@pytest.mark.parametrize("route", ["preview", "download"])
def test_stale_installed_version_after_real_draft(real_world, route):
    services, source, _ = real_world()
    drafted = services.office_package_chat.draft("demo", "Share this office")
    (source / "areas" / "reception" / "README.md").write_text("New version.", encoding="utf-8")
    services.offices.reimport_folder("demo", source, actor="user:local")
    call = getattr(services.office_package_chat, route)
    with pytest.raises(ServiceError) as error:
        call("demo", version=drafted["version"])
    assert error.value.status == 409
    assert error.value.details["reason"] == "office_export_stale"


def test_remote_model_requires_confirmation_and_permission(real_world, monkeypatch):
    services, _, _ = real_world(model="cloud_fast")
    monkeypatch.setattr(services.offices, "privacy", lambda _: {
        "local_only": False, "areas": [{"data_class": "non_sensitive", "cloud_effective": True}]})
    confirmation = services.office_package_chat.draft("demo", "Share this office")
    assert confirmation["reason"] == "cloud_confirmation_required"
    assert confirmation["model"] == "cloud_fast"
    monkeypatch.setattr(services.offices, "privacy", lambda _: {
        "local_only": False, "areas": [{"data_class": "non_sensitive", "cloud_effective": False}]})
    refused = services.office_package_chat.draft("demo", "Share this office", confirm_cloud=True)
    assert refused["reason"] == "cloud_not_allowed"
    assert refused["source"] == "deterministic"


def test_all_code_routes_need_advanced_mode_and_advanced_download_works(real_world):
    services, _, package = real_world()
    (package / "functions.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    client, headers = _client(services)
    with client:
        for route, body in (("draft", {"text": "Share this office", "include_code": True}),
                            ("preview", {"version": 1, "include_code": True}),
                            ("download", {"version": 1, "include_code": True, "accept_code_risk": True})):
            response = client.post(f"/gui/api/offices/demo/export/{route}", headers=headers, json=body)
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["details"]["reason"] == "advanced_mode_required"
        services.gui_settings.set_mode("advanced")
        draft = client.post("/gui/api/offices/demo/export/draft", headers=headers,
                            json={"text": "Share this office", "include_code": True})
        assert draft.status_code == 200, draft.text
        downloaded = client.post("/gui/api/offices/demo/export/download", headers=headers,
                                 json={"version": draft.json()["version"], "include_code": True,
                                       "accept_code_risk": True})
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.json()["zip_base64"]
        import base64
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(downloaded.json()["zip_base64"]))) as archive:
            assert "areas/reception/functions.py" in archive.namelist()


def test_empty_text_and_local_models_use_area_id(real_world):
    services, _, package = real_world()
    with pytest.raises(ServiceError) as error:
        services.office_package_chat.draft("demo", "   ")
    assert error.value.message == "text must not be empty"
    source = services.offices.export("demo", _source=True)
    area = source["definition"].areas[0]
    source["definition"] = source["definition"].model_copy(update={
        "areas": [area.model_copy(update={"path": "custom/location"})]})
    source["files"]["custom/location/catalog/models.yaml"] = b"models:\n  sample_local: {}\n"
    assert "reception: sample_local" in services.office_package_chat._local_models(source)


def test_download_normalizes_export_secret_error(real_world, monkeypatch):
    services, _, _ = real_world()
    version = services.offices.export("demo", _source=True)["version"]
    original = services.offices.export

    def blocked(office_id, **kwargs):
        if kwargs.get("_source"):
            return original(office_id, **kwargs)
        raise ServiceError("invalid_request", "Synthetic secret", status=422,
                           details={"reason": "export_secret_found", "found": [{"path": "office.yaml",
                           "line": 1, "type": "secret_key"}]})

    monkeypatch.setattr(services.offices, "export", blocked)
    with pytest.raises(ServiceError) as error:
        services.office_package_chat.download("demo", version=version)
    assert error.value.details == {"reason": "office_export_check_failed",
                                   "found": [{"path": "office.yaml", "line": 1, "type": "secret_key"}],
                                   "unverified": []}


@pytest.mark.parametrize("failure,reason", [(_BudgetCut, "budget_cut"), (_Refused, "refused")])
def test_model_guard_stop_has_its_own_reason(real_world, monkeypatch, failure, reason):
    services, _, _ = real_world()

    def stopped(*args, **kwargs):
        raise failure("Synthetic stop")

    monkeypatch.setattr(services.new_session, "_ask", stopped)
    assert services.office_package_chat.draft("demo", "Share this office") == {"reason": reason}


def test_draft_uses_screened_allow_list_and_model_suggestions_cannot_add_paths(world, monkeypatch):
    services, package = _world(world, monkeypatch)
    (package / "README.md").write_text("Contact contact@example.test", encoding="utf-8")
    prompts = []
    fake = FakeAdapter(sample_outputs={"OfficeExportProposal": _sample()})

    def ask(prompt, **kwargs):
        prompts.append(prompt)
        return fake.generate_structured(spec=services.system.catalog.model("local_default"), prompt=prompt,
                                        output_schema=OfficeExportProposal).parsed

    monkeypatch.setattr(services.new_session, "_ask", ask)
    drafted = services.office_package_chat.draft("demo", "Share this office")
    assert drafted["source"] == "model"
    assert ".env" not in drafted["suggested_omit"]
    assert "contact@example.test" not in prompts[0]
    assert "PRIVATE_NEIGHBOR" not in prompts[0]
    assert "[REDACTED:email:1]" in prompts[0]
    with pytest.raises(ServiceError) as error:
        services.office_package_chat.preview("demo", version=drafted["version"], omit=[".env"])
    assert error.value.details["reason"] == "export_path_not_portable"


def test_preview_and_download_rebuild_then_screen_the_package(world, monkeypatch):
    services, package = _world(world, monkeypatch)
    fake = FakeAdapter(sample_outputs={"OfficeExportProposal": _sample()})
    monkeypatch.setattr(services.new_session, "_ask", lambda prompt, **kw: fake.generate_structured(
        spec=services.system.catalog.model("local_default"), prompt=prompt,
        output_schema=OfficeExportProposal).parsed)
    version = services.office_package_chat.draft("demo", "Share it")["version"]
    omit = ["areas/reception/samples/example.txt"]
    preview = services.office_package_chat.preview("demo", version=version, omit=omit)
    assert not preview["blocked"]
    result = services.office_package_chat.download("demo", version=version, omit=omit)
    with zipfile.ZipFile(io.BytesIO(result["data"])) as archive:
        names = archive.namelist()
        assert "areas/reception/samples/example.txt" not in names
        assert not any(name.endswith((".db", ".env")) for name in names)
        assert "PRIVATE_NEIGHBOR" not in str([archive.read(name) for name in names])
    (package / "README.md").write_text("contact@example.test", encoding="utf-8")
    blocked = services.office_package_chat.preview("demo", version=version)
    assert blocked["blocked"]
    with pytest.raises(ServiceError) as error:
        services.office_package_chat.download("demo", version=version)
    assert error.value.details["reason"] == "office_export_check_failed"


def test_sensitive_office_without_local_model_is_deterministic(world, monkeypatch):
    services, _ = _world(world, monkeypatch)
    from multiagent.contracts import ModelTier
    monkeypatch.setattr(services.new_session, "_model_tier", lambda: ModelTier.CLOUD)
    monkeypatch.setattr(services.offices, "privacy", lambda office_id: {
        "local_only": True, "areas": [{"data_class": "sensitive", "cloud_effective": False}]})
    monkeypatch.setattr(services.new_session, "_ask", lambda *a, **kw: pytest.fail("model called"))
    draft = services.office_package_chat.draft("demo", "Share it", confirm_cloud=True)
    assert draft["source"] == "deterministic"
    assert draft["reason"] == "office_not_normal"


def test_invalid_model_contract_is_rejected(world, monkeypatch):
    services, _ = _world(world, monkeypatch)
    monkeypatch.setattr(services.new_session, "_ask", lambda *a, **kw: {"summary": "x", "unexpected": 1})
    with pytest.raises(ServiceError) as error:
        services.office_package_chat.draft("demo", "Share it")
    assert error.value.details["reason"] == "office_export_invalid_proposal"


def test_direct_export_warns_about_personal_data_without_blocking(world, monkeypatch):
    services, package = _world(world, monkeypatch)
    (package / "README.md").write_text("contact@example.test", encoding="utf-8")
    result = services.offices.export("demo")
    assert result["data"]
    assert any(item["code"] == "export_personal_data_found" for item in result["warnings"])


def test_download_checks_new_archive_even_after_clean_source(world, monkeypatch):
    services, _ = _world(world, monkeypatch)
    source = services.offices.export("demo", _source=True)
    original = services.offices.export

    def changed(office_id, **kwargs):
        if kwargs.get("_source"):
            return source
        result = original(office_id, **kwargs)
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(result["data"])) as old, zipfile.ZipFile(output, "w") as new:
            for path in old.namelist():
                new.writestr(path, b"contact@example.test" if path.endswith("README.md") else old.read(path))
        return {**result, "data": output.getvalue()}

    monkeypatch.setattr(services.offices, "export", changed)
    with pytest.raises(ServiceError) as error:
        services.office_package_chat.download("demo", version=source["version"])
    assert error.value.details["reason"] == "office_export_check_failed"


def test_code_export_needs_explicit_risk_acceptance(world, monkeypatch):
    services, _ = _world(world, monkeypatch)
    version = services.offices.export("demo", _source=True)["version"]
    with pytest.raises(ServiceError) as error:
        services.office_package_chat.download("demo", version=version, include_code=True)
    assert error.value.details["reason"] == "office_export_code_risk_required"


def test_routes_validate_choices_and_only_download_returns_zip(world, monkeypatch):
    services, _ = _world(world, monkeypatch)
    fake = FakeAdapter(sample_outputs={"OfficeExportProposal": _sample()})
    monkeypatch.setattr(services.new_session, "_ask", lambda prompt, **kw: fake.generate_structured(
        spec=services.system.catalog.model("local_default"), prompt=prompt,
        output_schema=OfficeExportProposal).parsed)
    token = ensure_token(services.settings.gui_token_file)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        draft = client.post("/gui/api/offices/demo/export/draft", headers=headers, json={"text": "Share it"})
        assert draft.status_code == 200, draft.text
        version = draft.json()["version"]
        assert "zip_base64" not in draft.json()
        preview = client.post("/gui/api/offices/demo/export/preview", headers=headers, json={"version": version})
        assert preview.status_code == 200, preview.text
        assert "zip_base64" not in preview.json()
        download = client.post("/gui/api/offices/demo/export/download", headers=headers, json={"version": version})
        assert download.status_code == 200, download.text
        assert download.json()["zip_base64"]
        missing = client.post("/gui/api/offices/absent/export/draft", headers=headers, json={"text": "Share it"})
        assert missing.status_code == 404
        bad_version = client.post("/gui/api/offices/demo/export/preview", headers=headers, json={"version": 0})
        assert bad_version.status_code == 422
