# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFI-T03: install, remove and reimport an office of level 1 (services, API and CLI), with synthetic folders."""
from __future__ import annotations

import base64
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
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.offices import (OFFICE_FILE_MAX_BYTES, OFFICE_MAX_AREAS, OFFICES_FILE, OFFICES_SCHEMA)
from multiagent.teams.digest import team_sha256

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


def make_area(destination: Path, *, marker: str = "", team: bool = True, broken: bool = False) -> Path:
    """An area of data only: the fixture without ``contracts.py`` and with one step on a built-in contract."""
    shutil.copytree(FIXTURE, destination)
    (destination / "contracts.py").unlink()
    (destination / "workflows" / "spec_review.yaml").write_text(
        "id: broken\nsteps: [" if broken else
        "id: spec_review\nmax_iterations: 1\nsteps:\n"
        "  - id: extract_requirements\n    agent: requirements_analyst\n    prompt_id: spec/extract_requirements\n"
        "    prompt_version: 1\n    skills: []\n    contract: ResearchOutput\n    preferred_model: local_default\n"
        "    fallback_model: null\n    requires_web: false\n    when: null\n    checkpoint_after: false\n",
        encoding="utf-8")
    if marker:
        prompt = destination / "prompts" / "spec" / "extract_requirements.v1.md"
        prompt.write_text(prompt.read_text(encoding="utf-8") + f"\n<!-- {marker} -->\n", encoding="utf-8")
    if team:
        (destination / "team.yaml").write_text(yaml.safe_dump(TEAM, sort_keys=False), encoding="utf-8")
    return destination


def write_office(root: Path, *, office_id: str = "demo", areas: tuple[str, ...] = ("recepcion", "propuesta"),
                 plan: list | None = None, relations: list | None = None, overrides: dict | None = None,
                 markers: dict | None = None, broken: tuple[str, ...] = ()) -> Path:
    """An office folder with one area folder each, and its ``office.yaml``. ``overrides``: area id -> extra keys."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for area in areas:
        make_area(root / "areas" / area, marker=(markers or {}).get(area, ""), broken=area in broken)
    document = {"schema": "maf.office/1", "id": office_id, "name": "Demo office",
                "areas": [{"id": area, "name": area.title(), "path": f"areas/{area}", **(overrides or {}).get(area, {})}
                          for area in areas]}
    if plan is not None:
        document["orchestrator"] = {"enabled": True, "default_plan": plan}
    if relations is not None:
        document["relations"] = relations
    (root / "office.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return root


def make_settings(root: Path) -> Settings:
    return Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False,
                    gui_token_file=root.parent / "gui-token")


@pytest.fixture
def world(tmp_path):
    opened: list[ApplicationServices] = []

    def start(bundles=None, root: Path | None = None) -> ApplicationServices:
        services = ApplicationServices.create(make_settings(root or tmp_path / "local"), bundles=bundles, dry_run=True)
        opened.append(services)
        return services

    yield start, tmp_path
    for services in opened:
        services.bundle_scans.close()
        services.runs.close()


def installed_dir(services: ApplicationServices) -> Path:
    return services.settings.data_dir / "installed"


def failure(call, *args, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


def reason(error: ServiceError) -> str:
    return error.details["reason"]


def make_zip(folder: Path, *, extra: dict[str, bytes] | None = None, wrap: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.writestr(wrap + path.relative_to(folder).as_posix(), path.read_bytes())
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def busy(services: ApplicationServices, bundle: str) -> None:
    """A run of the package in ``running``: what ``bundle_busy`` looks at."""
    services.runs.create(RunRequest(project_name="Busy", workflow_id="spec_review",
                                    inputs={"spec_text": "A synthetic specification."}), bundle=bundle)
    runtime = services.runs._bundle_runtime(bundle)
    with runtime.store.db.tx() as conn:
        conn.execute("UPDATE runs SET status='running'")


def test_install_registers_every_area(world):
    start, tmp = world
    services = start()
    result = services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    assert {"demo__recepcion", "demo__propuesta"} <= set(services.bundle_names())
    for area in ("recepcion", "propuesta"):
        entry = services.installed.get(f"demo__{area}")
        assert (entry["source"], entry["office"], entry["level"], entry["version"], entry["state"]) == (
            "office", "demo", "data", 1, "ok")
    office = result["office"]
    assert (office["id"], office["version"], office["level"], office["state"], office["issues"]) == (
        "demo", 1, "data", "ok", [])
    assert [(a["id"], a["bundle"], a["package_state"]) for a in office["areas"]] == [
        ("recepcion", "demo__recepcion", "ok"), ("propuesta", "demo__propuesta", "ok")]
    assert result["changes"] == {"added": ["recepcion", "propuesta"], "changed": [], "removed": [], "unchanged": []}
    assert services.offices.office_of("demo__propuesta") == ("demo", "propuesta")
    assert services.offices.office_of("nothing") is None
    registry = json.loads((installed_dir(services) / OFFICES_FILE).read_text(encoding="utf-8"))
    assert registry["schema"] == OFFICES_SCHEMA and list(registry["offices"]) == ["demo"]
    assert (installed_dir(services) / ".offices" / "demo" / "v1" / "office.yaml").is_file()
    events = [event for event in services.tasks.store.events(limit=100) if event["type"].startswith("office.")]
    assert [(event["type"], event["ref"]) for event in events] == [("office.installed", "demo")]
    assert not (installed_dir(services) / ".staging").exists() or not list((installed_dir(services) / ".staging").iterdir())


def test_install_survives_restart(world):
    start, tmp = world
    services = start()
    first = services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)["office"]
    again = start()
    listed = again.offices.list()
    assert [item["id"] for item in listed] == ["demo"]
    assert (listed[0]["state"], listed[0]["fingerprint"]) == ("ok", first["fingerprint"])
    assert {"demo__recepcion", "demo__propuesta"} <= set(again.bundle_names())
    assert again.definitions.validate_bundle("demo__recepcion")["valid"] is True


def test_install_never_approves_teams(world):
    start, tmp = world
    services = start()
    result = services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    assert [(item["bundle"], item["status"]) for item in result["teams"]] == [
        ("demo__recepcion", "proposed"), ("demo__propuesta", "proposed")]
    for bundle in ("demo__recepcion", "demo__propuesta"):
        versions = services.teams.list(bundle)["versions"]
        assert [(v["status"], v["version"]) for v in versions] == [("proposed", 1)]
        assert services.teams.approved(bundle) is None
    area = services.offices.get("demo")["areas"][0]
    assert area["team"] == {"status": "proposed", "version_id": result["teams"][0]["version_id"]}


def test_office_with_code_is_refused(world):
    start, tmp = world
    services = start()
    root = write_office(tmp / "demo")
    ran = tmp / "ran.txt"
    (root / "areas" / "recepcion" / "contracts.py").write_text(
        f"from pathlib import Path\nPath({str(ran)!r}).write_text('ran')\nOUTPUT_SCHEMAS = {{}}\n", encoding="utf-8")
    error = failure(services.offices.import_folder, root, actor=ACTOR)
    assert (error.status, error.code, reason(error)) == (409, "invalid_state", "office_has_code")
    assert error.details["files"] == ["areas/recepcion/contracts.py"]
    assert error.details["fix"] == "Esta oficina trae código: impórtala desde la ficha de la GUI en modo avanzado"
    assert not ran.exists()                       # nothing of the office was imported or run
    assert services.installed.list() == [] and services.offices.list() == []
    staging = installed_dir(services) / ".staging"
    assert not staging.exists() or not list(staging.iterdir())
    assert not (installed_dir(services) / OFFICES_FILE).exists()
    assert str(tmp) not in json.dumps(error.to_payload())


def test_office_code_files_are_listed_at_most_twenty(world):
    start, tmp = world
    services = start()
    root = write_office(tmp / "demo")
    for index in range(25):
        (root / "areas" / "propuesta" / f"tool{index:02d}.py").write_text("x = 1\n", encoding="utf-8")
    error = failure(services.offices.import_folder, root, actor=ACTOR)
    assert reason(error) == "office_has_code" and len(error.details["files"]) == 20 and error.details["count"] == 25


def test_validation_is_atomic(world):
    start, tmp = world
    services = start()
    root = write_office(tmp / "demo", broken=("propuesta",))
    error = failure(services.offices.import_folder, root, actor=ACTOR)
    assert (error.status, error.code) == (422, "invalid_definition")
    assert services.installed.list() == [] and services.offices.list() == []
    assert not (installed_dir(services) / OFFICES_FILE).exists()
    assert not (installed_dir(services) / "demo__recepcion").exists()
    assert "demo__recepcion" not in services.bundle_names()
    assert services.teams.store.list("demo__recepcion") == []
    staging = installed_dir(services) / ".staging"
    assert not staging.exists() or not list(staging.iterdir())
    assert str(tmp) not in json.dumps(error.to_payload())


def test_a_bad_office_yaml_is_a_definition_error_without_paths(world):
    start, tmp = world
    services = start()
    root = write_office(tmp / "demo")
    (root / "office.yaml").write_text("schema: maf.office/1\nid: Not Valid\nname: x\nareas: []\n", encoding="utf-8")
    error = failure(services.offices.import_folder, root, actor=ACTOR)
    assert (error.status, error.code, reason(error)) == (422, "invalid_definition", "office_invalid")
    assert error.details["issues"] and str(tmp) not in json.dumps(error.to_payload())


def test_apply_batch_is_all_or_nothing(world):
    start, tmp = world
    services = start()
    good, bad = make_area(tmp / "good"), make_area(tmp / "bad", broken=True)
    error = failure(services.installed.apply_batch, [
        {"op": "install", "name": "one", "folder": str(good)}, {"op": "install", "name": "two", "folder": str(bad)}],
        actor=ACTOR)
    assert (error.status, error.code) == (422, "invalid_definition")
    assert services.installed.list() == [] and "one" not in services.bundle_names()
    assert not (installed_dir(services) / "one").exists()          # the copy placed before the failure is gone
    assert not (installed_dir(services) / "registry.json").exists()
    done = services.installed.apply_batch([{"op": "install", "name": "one", "folder": str(good)},
                                           {"op": "install", "name": "two", "folder": str(make_area(tmp / "two"))}],
                                          actor=ACTOR)
    assert [item["name"] for item in done] == ["one", "two"] and {"one", "two"} <= set(services.bundle_names())
    error = failure(services.installed.apply_batch, [{"op": "install", "name": "three", "folder": str(good)},
                                                     {"op": "install", "name": "one", "folder": str(good)}], actor=ACTOR)
    assert reason(error) == "bundle_name_in_use" and "three" not in services.bundle_names()
    error = failure(services.installed.apply_batch, [{"op": "install", "name": "x", "folder": str(good)}],
                    actor=ACTOR, source="office")
    assert error.status == 422                                      # source "office" needs the office id


def test_an_area_is_not_changed_alone(world):
    start, tmp = world
    services = start()
    services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    for call in (lambda: services.installed.remove("demo__recepcion", actor=ACTOR),
                 lambda: services.installed.reinstall("demo__recepcion", tmp / "demo" / "areas" / "recepcion", actor=ACTOR)):
        error = failure(call)
        assert (error.status, reason(error), error.details["office"]) == (409, "bundle_in_office", "demo")
    assert "demo__recepcion" in services.bundle_names()


def test_team_sha256_mismatch(world):
    start, tmp = world
    services = start()
    root = write_office(tmp / "demo", overrides={"recepcion": {"team_sha256": "0" * 64}})
    error = failure(services.offices.import_folder, root, actor=ACTOR)
    assert (error.status, reason(error), error.details["area"]) == (409, "office_team_mismatch", "recepcion")
    assert services.installed.list() == [] and services.offices.list() == []
    digest = team_sha256(load_bundle(root / "areas" / "recepcion", services.settings).team)
    right = write_office(tmp / "right", overrides={"recepcion": {"team_sha256": digest}})
    assert services.offices.import_folder(right, actor=ACTOR)["office"]["id"] == "demo"
    # the digest is of the canonical team, not of the bytes of team.yaml
    team_file = root / "areas" / "recepcion" / "team.yaml"
    team_file.write_text(yaml.safe_dump(TEAM, sort_keys=True, indent=4), encoding="utf-8")
    assert team_sha256(load_bundle(root / "areas" / "recepcion", services.settings).team) == digest


def test_names_in_use(world):
    start, tmp = world
    elsewhere = make_area(tmp / "elsewhere")
    services = start({"demo__recepcion": elsewhere})
    error = failure(services.offices.import_folder, write_office(tmp / "demo"), actor=ACTOR)
    assert (error.status, reason(error)) == (409, "bundle_name_in_use")
    assert services.offices.list() == [] and "demo__propuesta" not in services.bundle_names()
    other = start(root=tmp / "other-local")
    other.offices.import_folder(write_office(tmp / "demo2"), actor=ACTOR)
    error = failure(other.offices.import_folder, write_office(tmp / "demo3"), actor=ACTOR)
    assert (error.status, reason(error), error.details["office"]) == (409, "office_exists", "demo")


def test_remove_keeps_data(world):
    start, tmp = world
    services = start()
    services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    services.runs._bundle_runtime("demo__recepcion")        # creates data_dir/bundles/demo__recepcion/
    kept = services.settings.data_dir / "bundles" / "demo__recepcion"
    assert kept.is_dir()
    result = services.offices.remove("demo", actor=ACTOR)
    assert (result["removed"], result["kept_data"], result["areas"]) == (True, True, ["recepcion", "propuesta"])
    assert kept.is_dir() and (installed_dir(services) / "demo__recepcion" / "v1").is_dir()
    assert (installed_dir(services) / ".offices" / "demo" / "v1" / "office.yaml").is_file()
    assert "demo__recepcion" not in services.bundle_names() and "demo__propuesta" not in services.bundle_names()
    assert services.installed.list() == [] and services.offices.list() == []
    assert start().offices.list() == []
    assert reason(failure(services.offices.remove, "demo", actor=ACTOR)) == "office_not_installed"
    events = [event["type"] for event in services.tasks.store.events(limit=100) if event["type"].startswith("office.")]
    assert events == ["office.installed", "office.removed"]
    again = services.offices.import_folder(write_office(tmp / "demo-again"), actor=ACTOR)["office"]
    assert again["version"] == 2                                   # a new version, never an overwrite of the kept copy
    assert services.installed.get("demo__recepcion")["version"] == 2


def test_remove_is_refused_while_an_area_is_busy(world):
    start, tmp = world
    services = start()
    services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    busy(services, "demo__propuesta")
    error = failure(services.offices.remove, "demo", actor=ACTOR)
    assert (error.status, reason(error)) == (409, "bundle_busy")
    assert [item["id"] for item in services.offices.list()] == ["demo"] and "demo__recepcion" in services.bundle_names()


def test_reimport_shows_only_what_changed(world):
    start, tmp = world
    services = start()
    first = services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    approved = services.teams.approve(first["teams"][1]["version_id"], actor=ACTOR)   # the person, for the test only
    changed = write_office(tmp / "demo-v2", markers={"recepcion": "new wording"})
    result = services.offices.reimport_folder("demo", changed, actor=ACTOR)
    assert result["changes"] == {"added": [], "changed": ["recepcion"], "removed": [], "unchanged": ["propuesta"]}
    assert services.installed.get("demo__recepcion")["version"] == 2
    assert services.installed.get("demo__propuesta")["version"] == 1
    assert services.teams.approved("demo__propuesta")["id"] == approved["id"]
    by_bundle = {item["bundle"]: item for item in result["teams"]}
    assert by_bundle["demo__propuesta"] == {"bundle": "demo__propuesta", "version_id": approved["id"], "status": "approved"}
    assert by_bundle["demo__recepcion"]["status"] == "proposed"
    assert services.teams.list("demo__recepcion")["count"] == 1      # the same team is not proposed twice
    assert result["office"]["version"] == 2
    assert (installed_dir(services) / ".offices" / "demo" / "v2" / "office.yaml").is_file()
    again = services.offices.reimport_folder("demo", changed, actor=ACTOR)   # the same folder: nothing changes
    assert again["changes"] == {"added": [], "changed": [], "removed": [], "unchanged": ["recepcion", "propuesta"]}
    assert again["office"]["version"] == 2 and services.installed.get("demo__recepcion")["version"] == 2
    events = [event["type"] for event in services.tasks.store.events(limit=100) if event["type"].startswith("office.")]
    assert events == ["office.installed", "office.reimported"]
    assert reason(failure(services.offices.reimport_folder, "nothing", changed, actor=ACTOR)) == "office_not_installed"
    other = write_office(tmp / "other", office_id="other")
    assert reason(failure(services.offices.reimport_folder, "demo", other, actor=ACTOR)) == "office_id_mismatch"


def test_reimport_with_a_changed_team_proposes_it_and_keeps_the_approved_one(world):
    start, tmp = world
    services = start()
    first = services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    approved = services.teams.approve(first["teams"][0]["version_id"], actor=ACTOR)
    root = write_office(tmp / "demo-v2")
    (root / "areas" / "recepcion" / "team.yaml").write_text(yaml.safe_dump(
        {**TEAM, "description": "Another description."}, sort_keys=False), encoding="utf-8")
    result = services.offices.reimport_folder("demo", root, actor=ACTOR)
    assert result["changes"]["changed"] == ["recepcion"]
    assert services.teams.approved("demo__recepcion")["id"] == approved["id"]      # the new one waits for a person
    statuses = [(v["version"], v["status"]) for v in services.teams.list("demo__recepcion")["versions"]]
    assert statuses == [(1, "approved"), (2, "proposed")]


def test_reimport_adds_and_removes_areas(world):
    start, tmp = world
    services = start()
    services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    result = services.offices.reimport_folder(
        "demo", write_office(tmp / "demo-v2", areas=("recepcion", "avisos")), actor=ACTOR)
    assert result["changes"] == {"added": ["avisos"], "changed": [], "removed": ["propuesta"], "unchanged": ["recepcion"]}
    assert "demo__avisos" in services.bundle_names() and "demo__propuesta" not in services.bundle_names()
    assert [area["id"] for area in services.offices.get("demo")["areas"]] == ["recepcion", "avisos"]
    assert (installed_dir(services) / "demo__propuesta" / "v1").is_dir()   # the removed area keeps its copy


def test_reimport_refused_while_busy(world):
    start, tmp = world
    services = start()
    services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    busy(services, "demo__recepcion")
    changed = write_office(tmp / "demo-v2", markers={"recepcion": "one", "propuesta": "two"})
    error = failure(services.offices.reimport_folder, "demo", changed, actor=ACTOR)
    assert (error.status, error.code, reason(error)) == (409, "invalid_state", "bundle_busy")
    assert services.installed.get("demo__recepcion")["version"] == 1       # nothing changed, not even the idle area
    assert services.installed.get("demo__propuesta")["version"] == 1
    assert services.offices.get("demo")["version"] == 1
    assert not (installed_dir(services) / "demo__recepcion" / "v2").exists()
    assert not (installed_dir(services) / "demo__propuesta" / "v2").exists()
    assert not (installed_dir(services) / ".offices" / "demo" / "v2").exists()
    assert services.teams.list("demo__propuesta")["count"] == 1


def test_import_one_area_into_an_office(world):
    start, tmp = world
    services = start()
    services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    single = write_office(tmp / "avisos-only", office_id="export", areas=("avisos",), relations=[
        {"from": "avisos", "to": "recepcion", "kind": "consults"}, {"from": "avisos", "to": "ghost", "kind": "hands_off"}])
    result = services.offices.import_folder(single, actor=ACTOR, into="demo")
    assert "demo__avisos" in services.bundle_names() and services.installed.get("demo__avisos")["office"] == "demo"
    assert result["changes"] == {"added": ["avisos"], "changed": [], "removed": [], "unchanged": ["recepcion", "propuesta"]}
    assert [item["code"] for item in result["warnings"]] == ["office_relation_unknown_area"]
    assert "ghost" in result["warnings"][0]["location"]
    office = services.offices.get("demo")
    assert (office["version"], [a["id"] for a in office["areas"]]) == (2, ["recepcion", "propuesta", "avisos"])
    assert office["relations"] == [{"from": "avisos", "to": "recepcion", "kind": "consults"}]
    saved = yaml.safe_load((installed_dir(services) / ".offices" / "demo" / "v2" / "office.yaml").read_text(encoding="utf-8"))
    assert [a["id"] for a in saved["areas"]] == ["recepcion", "propuesta", "avisos"]
    error = failure(services.offices.import_folder, single, actor=ACTOR, into="demo")
    assert (error.status, reason(error), error.details["area"]) == (409, "office_area_exists", "avisos")
    assert reason(failure(services.offices.import_folder, single, actor=ACTOR, into="nothing")) == "office_not_installed"
    two = write_office(tmp / "two-areas", office_id="export", areas=("uno", "dos"))
    assert reason(failure(services.offices.import_folder, two, actor=ACTOR, into="demo")) == "office_single_area_required"


def test_remove_one_area(world):
    start, tmp = world
    services = start()
    plan = [{"area": "recepcion", "workflow": "spec_review"},
            {"area": "propuesta", "workflow": "spec_review", "after": ["recepcion"]}]
    services.offices.import_folder(write_office(tmp / "demo", plan=plan, relations=[
        {"from": "recepcion", "to": "propuesta", "kind": "hands_off"}]), actor=ACTOR)
    result = services.offices.remove_area("demo", "propuesta", actor=ACTOR)
    assert (result["removed"], result["kept_data"], result["area"]) == (True, True, "propuesta")
    assert "demo__recepcion" in services.bundle_names() and "demo__propuesta" not in services.bundle_names()
    codes = sorted(item["code"] for item in result["warnings"])
    assert codes == ["office_plan_item_removed", "office_relation_removed"]
    office = services.offices.get("demo")
    assert [a["id"] for a in office["areas"]] == ["recepcion"] and office["version"] == 2
    assert [item["id"] for item in office["orchestrator"]["default_plan"]] == ["recepcion"] and office["relations"] == []
    saved = yaml.safe_load((installed_dir(services) / ".offices" / "demo" / "v2" / "office.yaml").read_text(encoding="utf-8"))
    assert [item["area"] for item in saved["orchestrator"]["default_plan"]] == ["recepcion"]
    assert "propuesta" not in json.dumps(saved)
    assert (installed_dir(services) / ".offices" / "demo" / "v1" / "office.yaml").read_text(encoding="utf-8").count("propuesta")
    assert reason(failure(services.offices.remove_area, "demo", "propuesta", actor=ACTOR)) == "office_area_not_installed"
    assert reason(failure(services.offices.remove_area, "demo", "recepcion", actor=ACTOR)) == "office_last_area"
    assert start().offices.get("demo")["areas"][0]["id"] == "recepcion"


def test_size_limits(world):
    start, tmp = world
    services = start()
    root = write_office(tmp / "demo")
    (root / "areas" / "recepcion" / "big.md").write_bytes(b"a" * (OFFICE_FILE_MAX_BYTES + 1))
    error = failure(services.offices.import_folder, root, actor=ACTOR)
    assert (error.status, error.code, reason(error)) == (422, "invalid_request", "office_too_large")
    assert error.details["field"] == "file" and error.details["path"] == "areas/recepcion/big.md"
    (root / "areas" / "recepcion" / "big.md").write_bytes(b"a" * OFFICE_FILE_MAX_BYTES)   # exactly the limit passes
    assert services.offices.import_folder(root, actor=ACTOR)["office"]["id"] == "demo"
    names = tuple(f"a{index:02d}" for index in range(OFFICE_MAX_AREAS + 1))
    many = write_office(tmp / "many", office_id="many", areas=names)
    error = failure(services.offices.import_folder, many, actor=ACTOR)
    assert (reason(error), error.details["field"]) == ("office_too_large", "areas")


def test_zip_import_and_unsafe_entries(world):
    start, tmp = world
    services = start()
    root = write_office(tmp / "demo")
    archive = make_zip(root, wrap="demo/")                    # what «Enviar a › Carpeta comprimida» makes
    result = services.offices.import_zip(archive, actor=ACTOR, filename="demo.zip")
    assert result["office"]["id"] == "demo" and "demo__recepcion" in services.bundle_names()
    other = start(root=tmp / "other-local")
    error = failure(other.offices.import_zip, make_zip(root, extra={"../evil.yaml": b"x: 1"}), actor=ACTOR)
    assert (error.status, reason(error)) == (422, "zip_unsafe_entry")
    error = failure(other.offices.import_zip, b"this is not a zip", actor=ACTOR)
    assert (error.status, reason(error)) == (422, "zip_invalid")
    error = failure(other.offices.import_zip, make_zip(root, extra={"areas/recepcion/contracts.py": b"x = 1"}), actor=ACTOR)
    assert (error.status, reason(error), error.details["files"]) == (409, "office_has_code", ["areas/recepcion/contracts.py"])
    assert other.installed.list() == [] and not (installed_dir(other) / OFFICES_FILE).exists()
    staging = installed_dir(other) / ".staging"
    assert not staging.exists() or not list(staging.iterdir())
    changed = write_office(tmp / "demo-v2", markers={"propuesta": "zip"})
    again = services.offices.reimport_zip("demo", make_zip(changed), actor=ACTOR, filename="demo-v2.zip")
    assert again["changes"]["changed"] == ["propuesta"]


def test_a_changed_copy_makes_the_office_degraded(world):
    start, tmp = world
    services = start()
    services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    workflow = installed_dir(services) / "demo__recepcion" / "v1" / "workflows" / "spec_review.yaml"
    workflow.write_text(workflow.read_text(encoding="utf-8") + "# edited by hand\n", encoding="utf-8")
    shutil.rmtree(installed_dir(services) / "demo__propuesta" / "v1")
    office = start().offices.get("demo")
    assert office["state"] == "degraded"
    assert {(issue["code"], issue["area"]) for issue in office["issues"]} == {
        ("area_changed", "recepcion"), ("area_missing", "propuesta")}
    assert [area["package_state"] for area in office["areas"]] == ["changed", "missing"]
    fixed = start().offices.reimport_folder("demo", write_office(tmp / "demo-again"), actor=ACTOR)
    assert fixed["changes"]["changed"] == ["recepcion", "propuesta"] and fixed["office"]["state"] == "ok"


def test_a_hand_edited_offices_file_stops_the_start(world):
    start, tmp = world
    services = start()
    services.offices.import_folder(write_office(tmp / "demo"), actor=ACTOR)
    path = installed_dir(services) / OFFICES_FILE
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="offices registry"):
        start()
    path.write_text(json.dumps({"schema": OFFICES_SCHEMA, "offices": {"Bad Key": {"definition": {}}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid entry"):
        start()


def test_without_offices_nothing_changes(world):
    start, tmp = world
    services = start()
    assert services.offices.list() == [] and services.offices.office_of("demo") is None
    services.installed.apply_batch([{"op": "install", "name": "plain", "folder": str(make_area(tmp / "plain"))}], actor=ACTOR)
    assert not (installed_dir(services) / OFFICES_FILE).exists()
    assert not (installed_dir(services) / ".offices").exists()
    assert services.installed.get("plain")["office"] is None
    assert start().offices.list() == [] and not (installed_dir(services) / OFFICES_FILE).exists()
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        assert client.get("/api/v1/offices").json() == {"offices": []}
        assert client.get("/api/v1/offices/demo").status_code == 409
    assert not (installed_dir(services) / OFFICES_FILE).exists()


class _ClientContext:
    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


def invoke_cli(monkeypatch, capsys, client: TestClient, args: list[str]) -> str:
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    try:
        cli.main(args)
    except SystemExit as exc:
        assert exc.code == 1
    captured = capsys.readouterr()
    return captured.out if captured.out else captured.err


def stable(office: dict) -> dict:
    """An office without what differs between two facades: the time, the channel and the ids of the team versions."""
    office = {key: value for key, value in office.items() if key not in {"installed_at", "channel"}}
    office["areas"] = [{**area, "team": {**area["team"], "version_id": bool(area["team"]["version_id"])}}
                       for area in office["areas"]]
    return office


def test_api_and_cli_parity(world, monkeypatch, capsys):
    start, tmp = world
    root = write_office(tmp / "demo")
    via_api, via_cli, via_zip = start(root=tmp / "api-local"), start(root=tmp / "cli-local"), start(root=tmp / "zip-local")
    archive = tmp / "demo.zip"
    archive.write_bytes(make_zip(root))
    with TestClient(create_app(services=via_api), base_url="http://localhost") as http, \
            TestClient(create_app(services=via_cli), base_url="http://localhost") as cli_client, \
            TestClient(create_app(services=via_zip), base_url="http://localhost") as zip_client:
        created = http.post("/api/v1/offices", json={"folder": str(root), "actor": ACTOR})
        assert created.status_code == 201, created.text
        from_cli = json.loads(invoke_cli(monkeypatch, capsys, cli_client, ["office", "import", str(root)]))
        from_zip = json.loads(invoke_cli(monkeypatch, capsys, zip_client, ["office", "import", str(archive)]))
        assert stable(from_cli["office"]) == stable(created.json()["office"]) == stable(from_zip["office"])
        assert created.json()["changes"] == from_cli["changes"] == from_zip["changes"]
        assert (created.json()["office"]["channel"], from_cli["office"]["channel"]) == ("api", "api")
        assert [item["status"] for item in created.json()["teams"]] == ["proposed", "proposed"]
        listing = http.get("/api/v1/offices")
        assert listing.status_code == 200 and [o["id"] for o in listing.json()["offices"]] == ["demo"]
        assert str(tmp) not in listing.text and str(tmp).replace("\\", "/") not in listing.text
        assert str(tmp) not in created.text and "path" not in listing.json()["offices"][0]["areas"][0]
        assert http.get("/api/v1/offices/demo").json() == listing.json()["offices"][0]
        assert json.loads(invoke_cli(monkeypatch, capsys, http, ["office", "list"])) == listing.json()
        assert json.loads(invoke_cli(monkeypatch, capsys, http, ["office", "show", "demo"])) == listing.json()["offices"][0]
        again = http.post("/api/v1/offices", json={"folder": str(root), "actor": ACTOR})
        assert (again.status_code, again.json()["detail"]["details"]["reason"]) == (409, "office_exists")
        assert http.get("/api/v1/offices/missing").status_code == 409
        # a .zip as base64 in the JSON body, and a reimport that changes one area
        changed = write_office(tmp / "demo-v2", markers={"recepcion": "api"})
        blob = base64.b64encode(make_zip(changed)).decode("ascii")
        bad = http.post("/api/v1/offices/demo/reimport", json={"zip_base64": "***", "actor": ACTOR})
        assert (bad.status_code, bad.json()["detail"]["details"]["reason"]) == (422, "zip_invalid")
        both = http.post("/api/v1/offices/demo/reimport", json={"zip_base64": blob, "folder": str(changed), "actor": ACTOR})
        assert (both.status_code, both.json()["detail"]["details"]["reason"]) == (422, "office_source_required")
        reimported = http.post("/api/v1/offices/demo/reimport", json={"zip_base64": blob, "filename": "x.zip", "actor": ACTOR})
        assert reimported.status_code == 200 and reimported.json()["changes"]["changed"] == ["recepcion"]
        via_folder = json.loads(invoke_cli(monkeypatch, capsys, cli_client, ["office", "reimport", "demo", str(changed)]))
        assert via_folder["changes"] == reimported.json()["changes"]
        single = write_office(tmp / "single", office_id="export", areas=("avisos",))
        added = json.loads(invoke_cli(monkeypatch, capsys, cli_client, ["office", "import", str(single), "--into", "demo"]))
        assert added["changes"]["added"] == ["avisos"]
        removed = http.post("/api/v1/offices/demo/areas/propuesta/remove", json={"actor": ACTOR})
        assert removed.status_code == 200 and removed.json()["kept_data"] is True
        by_cli = json.loads(invoke_cli(monkeypatch, capsys, cli_client, ["office", "remove", "demo", "--area", "propuesta"]))
        assert by_cli["area"] == removed.json()["area"] == "propuesta"
        gone = http.post("/api/v1/offices/demo/remove", json={"actor": ACTOR})
        assert gone.status_code == 200 and gone.json()["kept_data"] is True and http.get("/api/v1/offices").json() == {"offices": []}
        assert json.loads(invoke_cli(monkeypatch, capsys, cli_client, ["office", "remove", "demo"]))["removed"] is True


def test_api_refuses_approval_fields(world):
    start, tmp = world
    services = start()
    root = write_office(tmp / "demo")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        for field, value in (("approve_teams", True), ("trust_functions", True), ("risk_ack", True)):
            response = client.post("/api/v1/offices", json={"folder": str(root), "actor": ACTOR, field: value})
            assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_request", field
            assert field in response.text
        assert services.offices.list() == [] and services.installed.list() == []
        assert client.post("/api/v1/offices", json={"folder": str(root), "actor": ACTOR}).status_code == 201
        for path in ("/api/v1/offices/demo/reimport", "/api/v1/offices/demo/remove", "/api/v1/offices/demo/areas/propuesta/remove"):
            body = {"actor": ACTOR, "approve_teams": True, **({"folder": str(root)} if path.endswith("reimport") else {})}
            assert client.post(path, json=body).status_code == 422, path
        assert services.teams.approved("demo__recepcion") is None and services.teams.approved("demo__propuesta") is None
        actor = client.post("/api/v1/offices/demo/remove", json={"actor": "agent:codex"})
        assert actor.status_code == 409 and actor.json()["detail"]["details"]["reason"] == "confirmation_required"
