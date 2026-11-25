# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFI-T05: allow-listed office exports and manifest-checked imports."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.offices.export import build_export
from multiagent.offices.schema import OfficeDefinition
from multiagent.services import ApplicationServices, ServiceError
from multiagent.teams.digest import team_sha256
from multiagent.teams.schema import Team

FIXTURE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
ACTOR = "user:local"
TEAM = {
    "schema_version": 1, "id": "area_team", "description": "A synthetic team.",
    "roles": [{"id": "analyst", "purpose": "Turns a specification into requirements.", "rules": ["Quote the source."],
               "skills": [], "contracts": ["ResearchOutput"], "capabilities": ["doc.read"], "permissions": []}],
    "members": [{"id": "lead", "kind": "person", "name": "Lead"},
                {"id": "analyst_agent", "kind": "agent", "agent": "requirements_analyst"}],
    "assignments": [{"role": "analyst", "member": "analyst_agent"}],
    "relationships": [],
}


def _settings(root: Path) -> Settings:
    return Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False,
                    gui_token_file=root.parent / "gui-token")


@pytest.fixture
def world(tmp_path):
    opened: list[ApplicationServices] = []

    def start(root: Path | None = None) -> ApplicationServices:
        services = ApplicationServices.create(_settings(root or tmp_path / "local"), dry_run=True)
        opened.append(services)
        return services

    yield start, tmp_path
    for services in opened:
        services.bundle_scans.close()
        services.runs.close()


def make_area(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination)
    (destination / "contracts.py").unlink()
    (destination / "workflows" / "spec_review.yaml").write_text(
        "id: spec_review\nmax_iterations: 1\nsteps:\n"
        "  - id: extract_requirements\n    agent: requirements_analyst\n    prompt_id: spec/extract_requirements\n"
        "    prompt_version: 1\n    skills: []\n    contract: ResearchOutput\n    preferred_model: local_default\n"
        "    fallback_model: null\n    requires_web: false\n    when: null\n    checkpoint_after: false\n",
        encoding="utf-8")
    (destination / "team.yaml").write_text(yaml.safe_dump(TEAM, sort_keys=False), encoding="utf-8")
    (destination / "README.md").write_text("Synthetic area.\n", encoding="utf-8")
    return destination


def write_office(root: Path, *, office_id: str = "demo", areas: tuple[str, ...] = ("reception",)) -> Path:
    root.mkdir(parents=True)
    for area in areas:
        make_area(root / "areas" / area)
    document = {"schema": "maf.office/1", "id": office_id, "name": f"{office_id.title()} office",
                "areas": [{"id": area, "name": area.title(), "path": f"areas/{area}"} for area in areas]}
    (root / "office.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return root


def _definition(area: str = "reception", office_id: str = "demo") -> OfficeDefinition:
    return OfficeDefinition.model_validate({
        "schema": "maf.office/1", "id": office_id, "name": f"{office_id.title()} office",
        "areas": [{"id": area, "name": area.title(), "path": f"areas/{area}"}],
    })


def _pure_files(area: str = "reception") -> dict[str, bytes]:
    prefix = f"areas/{area}/"
    return {
        prefix + "team.yaml": yaml.safe_dump(TEAM, sort_keys=False).encode("utf-8"),
        prefix + "workflows/review.yaml": b"id: review\nsteps: []\n",
        prefix + "prompts/review.md": b"Review this request.",
        prefix + "catalog/agents.yaml": b"agents: {}\n",
    }


def _zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def _zip_with(data: bytes, *, changed: tuple[str, bytes] | None = None, extra: tuple[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    with _zip(data) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for path in source.namelist():
            if changed and path == changed[0]:
                target.writestr(path, changed[1])
            else:
                target.writestr(path, source.read(path))
        if extra:
            target.writestr(*extra)
    return output.getvalue()


class _ClientContext:
    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


def test_export_contains_only_the_allow_list():
    files = _pure_files()
    files.update({
        "areas/reception/.env": b"IGNORED",
        "areas/reception/runs.db": b"IGNORED",
        "areas/reception/catalog/models.yaml": b"models: {}\n",
        "areas/reception/.local/x.txt": b"IGNORED",
        "areas/reception/functions.py": b"def run(): pass\n",
        "areas/reception/prompts/note.py": b"ignored code\n",
    })
    data, manifest = build_export(files, _definition())
    with _zip(data) as archive:
        assert set(archive.namelist()) == {
            "office.yaml", "manifest.json", "areas/reception/team.yaml",
            "areas/reception/workflows/review.yaml", "areas/reception/prompts/review.md",
            "areas/reception/catalog/agents.yaml",
        }
    assert any(item["path"].endswith("prompts/note.py") for item in manifest["warnings"])


def test_include_code_adds_only_contracts_and_functions():
    files = _pure_files()
    files.update({"areas/reception/contracts.py": b"CONTRACT = True\n",
                  "areas/reception/functions.py": b"def run(): return True\n",
                  "areas/reception/prompts/note.py": b"not portable\n"})
    data, _ = build_export(files, _definition(), include_code=True)
    with _zip(data) as archive:
        names = set(archive.namelist())
    assert "areas/reception/contracts.py" in names
    assert "areas/reception/functions.py" in names
    assert "areas/reception/prompts/note.py" not in names


def test_manifest_lists_every_file():
    data, manifest = build_export(_pure_files(), _definition())
    with _zip(data) as archive:
        names = set(archive.namelist()) - {"manifest.json"}
        assert {item["path"] for item in manifest["files"]} == names
        for item in manifest["files"]:
            content = archive.read(item["path"])
            assert item["sha256"] == hashlib.sha256(content).hexdigest()
            assert item["size"] == len(content)
    assert manifest["schema"] == "maf.office-export/1"
    team = Team.model_validate(TEAM, context={"skip_team_semantics": True})
    assert manifest["areas"][0]["team_sha256"] == team_sha256(team)


def test_exported_office_yaml_carries_team_sha256():
    data, manifest = build_export(_pure_files(), _definition())
    with _zip(data) as archive:
        exported = yaml.safe_load(archive.read("office.yaml"))
    assert exported["areas"][0]["path"] == "areas/reception"
    assert exported["areas"][0]["team_sha256"] == manifest["areas"][0]["team_sha256"]


def test_secret_blocks_export(world):
    start, tmp = world
    source = write_office(tmp / "secret-office")
    secret = "-----BEGIN PRIVATE KEY-----\nMIIEowIBAAKCAQEA0123456789\n-----END PRIVATE KEY-----"
    (source / "areas" / "reception" / "prompts" / "review.md").write_text(
        f"First line.\nSecond line.\n{secret}\n", encoding="utf-8")
    services = start()
    services.offices.import_folder(source, actor=ACTOR)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post("/api/v1/offices/demo/export", json={})
    assert response.status_code == 422
    assert response.json()["detail"]["details"]["reason"] == "export_secret_found"
    assert response.json()["detail"]["details"]["found"] == [
        {"path": "areas/reception/prompts/review.md", "line": 3, "type": "private_key"}
    ]
    assert secret not in response.text


def test_models_catalog_warning():
    files = _pure_files()
    files["areas/reception/catalog/models.yaml"] = b"models:\n  area_only: {}\n"
    files["areas/reception/workflows/review.yaml"] = (
        b"id: review\nsteps:\n  - id: draft\n    preferred_model: area_only\n")
    data, manifest = build_export(files, _definition())
    assert manifest["warnings"][0] == {
        "code": "export_model_not_portable", "path": "areas/reception/workflows/review.yaml",
        "workflow": "review", "step": "draft",
    }
    with _zip(data) as archive:
        assert archive.testzip() is None


def test_round_trip(world):
    start, tmp = world
    source = start(tmp_path_local(tmp, "source"))
    destination = start(tmp_path_local(tmp, "destination"))
    source.offices.import_folder(write_office(tmp / "roundtrip"), actor=ACTOR)
    exported = source.offices.export("demo")
    imported = destination.offices.import_zip(exported["data"], actor=ACTOR)
    assert [area["id"] for area in imported["office"]["areas"]] == ["reception"]
    repeated = destination.offices.export("demo")
    assert repeated["manifest"]["files"] == exported["manifest"]["files"]
    source_entry = source.installed._read()["demo__reception"]
    destination_entry = destination.installed._read()["demo__reception"]
    original = source.installed._folder_of(source_entry)
    copied = destination.installed._folder_of(destination_entry)
    from multiagent.bundles import load_bundle

    assert team_sha256(load_bundle(original, source.settings).team) == team_sha256(
        load_bundle(copied, destination.settings).team)


def test_export_reads_installed_copies(world):
    start, tmp = world
    services = start()
    source = write_office(tmp / "copy-source")
    services.offices.import_folder(source, actor=ACTOR)
    original_readme = (source / "areas" / "reception" / "README.md").read_bytes()
    (source / "areas" / "reception" / "README.md").write_text("Changed at the source.\n", encoding="utf-8")
    exported = services.offices.export("demo")
    with _zip(exported["data"]) as archive:
        assert archive.read("areas/reception/README.md") == original_readme


def tmp_path_local(tmp_path: Path, name: str) -> Path:
    return tmp_path / f"{name}-local"


def test_area_export_into_another_office(world):
    start, tmp = world
    services = start()
    services.offices.import_folder(write_office(tmp / "demo", areas=("notices",)), actor=ACTOR)
    services.offices.import_folder(write_office(tmp / "other", office_id="other", areas=("purchasing",)), actor=ACTOR)
    exported = services.offices.export("demo", area="notices")
    assert exported["manifest"]["kind"] == "area"
    with _zip(exported["data"]) as archive:
        definition = yaml.safe_load(archive.read("office.yaml"))
    assert len(definition["areas"]) == 1
    assert "relations" not in definition and "orchestrator" not in definition
    added = services.offices.import_zip(exported["data"], actor=ACTOR, into="other")
    assert [area["id"] for area in added["office"]["areas"]] == ["purchasing", "notices"]
    registered = services.installed._read()
    demo_folder = services.installed._folder_of(registered["demo__notices"])
    other_folder = services.installed._folder_of(registered["other__notices"])
    assert demo_folder != other_folder and demo_folder.is_dir() and other_folder.is_dir()


def test_import_rejects_unsafe_entries(world):
    start, tmp = world
    services = start()
    entries = [("../x", False), ("/abs", False), ("a\\b", False), ("link", True)]
    for name, symlink in entries:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            if symlink:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (0o120777 << 16)
                archive.writestr(info, "outside")
            elif name == "a\\b":
                info = zipfile.ZipInfo("placeholder")
                info.filename = name
                archive.writestr(info, b"unsafe")
            else:
                archive.writestr(name, b"unsafe")
        with pytest.raises(ServiceError) as caught:
            services.offices.import_zip(buffer.getvalue(), actor=ACTOR)
        assert caught.value.details["reason"] == "zip_unsafe_entry"
    staging = services.settings.data_dir / "installed" / ".staging"
    assert not staging.exists() or not list(staging.iterdir())
    assert not (tmp / "abs").exists()


def test_manifest_mismatch(world):
    start, tmp = world
    source = start(tmp_path_local(tmp, "manifest-source"))
    destination = start(tmp_path_local(tmp, "manifest-destination"))
    source.offices.import_folder(write_office(tmp / "manifest-office"), actor=ACTOR)
    exported = source.offices.export("demo")["data"]
    with _zip(exported) as archive:
        changed_path = next(name for name in archive.namelist() if name.endswith("README.md"))
    changed = _zip_with(exported, changed=(changed_path, b"changed\n"))
    with pytest.raises(ServiceError) as caught:
        destination.offices.import_zip(changed, actor=ACTOR)
    assert caught.value.details == {"reason": "office_manifest_mismatch", "path": changed_path}

    extra = _zip_with(exported, extra=("areas/reception/extra.txt", b"unlisted"))
    with pytest.raises(ServiceError) as caught:
        destination.offices.import_zip(extra, actor=ACTOR)
    assert caught.value.details == {"reason": "office_manifest_mismatch", "path": "areas/reception/extra.txt"}


def test_export_route_and_cli_parity(world, monkeypatch, capsys):
    start, tmp = world
    services = start()
    services.offices.import_folder(write_office(tmp / "parity-office"), actor=ACTOR)
    monkeypatch.setattr("multiagent.offices.export.utcnow", lambda: "2026-11-24T00:00:00+00:00")
    output = tmp / "cli-export.zip"
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post("/api/v1/offices/demo/export", json={})
        assert response.status_code == 200
        route_archive = base64.b64decode(response.json()["zip_base64"], validate=True)
        with zipfile.ZipFile(io.BytesIO(route_archive)) as archive:
            assert json.loads(archive.read("manifest.json")) == response.json()["manifest"]
        monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
        cli.main(["office", "export", "demo", "--output", str(output)])
        cli_result = json.loads(capsys.readouterr().out)
        assert cli_result["manifest"] == response.json()["manifest"]
        with zipfile.ZipFile(output) as archive:
            assert json.loads(archive.read("manifest.json")) == response.json()["manifest"]
        original = output.read_bytes()

        with pytest.raises(SystemExit) as caught:
            cli.main(["office", "export", "demo", "--output", str(output)])
        assert caught.value.code == 1
        capsys.readouterr()
        assert output.read_bytes() == original
