# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PAQ-T01: installed packages (copy with a fingerprint, registry, start, API and CLI), with synthetic folders."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.installed import BUSY_STATUSES, CODE_FREE_SUFFIXES, REGISTRY_SCHEMA, package_level

FIXTURE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
ACTOR = "user:local"


def make_data_bundle(destination: Path, *, marker: str = "") -> Path:
    """A bundle with files of data only: the fixture without ``contracts.py`` and one step on a built-in contract."""
    shutil.copytree(FIXTURE, destination)
    (destination / "contracts.py").unlink()
    (destination / "workflows" / "spec_review.yaml").write_text(
        "id: spec_review\nmax_iterations: 1\nsteps:\n"
        "  - id: extract_requirements\n    agent: requirements_analyst\n    prompt_id: spec/extract_requirements\n"
        "    prompt_version: 1\n    skills: []\n    contract: ResearchOutput\n    preferred_model: local_default\n"
        "    fallback_model: null\n    requires_web: false\n    when: null\n    checkpoint_after: false\n"
        + (f"# {marker}\n" if marker else ""), encoding="utf-8")
    return destination


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


def test_install_copies_and_survives_restart(world):
    start, tmp = world
    services = start()
    origin = make_data_bundle(tmp / "origin")
    entry = services.installed.install_folder("demo", origin, actor=ACTOR, source_label="synthetic")
    assert (entry["name"], entry["level"], entry["version"], entry["state"], entry["reason"]) == (
        "demo", "data", 1, "ok", None)
    assert entry["source"] == "copy" and entry["trust_functions"] is False and entry["channel"] == "python"
    assert "path" not in entry and str(tmp) not in json.dumps(entry)
    assert "demo" in services.bundle_names()
    registry = json.loads((installed_dir(services) / "registry.json").read_text(encoding="utf-8"))
    assert registry["schema"] == REGISTRY_SCHEMA and registry["bundles"]["demo"]["path"] == "demo/v1"
    again = start()  # a second facade on the same data_dir, no --bundle
    assert "demo" in again.bundle_names()
    assert again.installed.get("demo")["fingerprint"] == entry["fingerprint"]
    assert again.definitions.validate_bundle("demo")["valid"] is True


def test_copy_runs_not_the_origin(world):
    start, tmp = world
    services = start()
    origin = make_data_bundle(tmp / "origin")
    services.installed.install_folder("demo", origin, actor=ACTOR)
    (origin / "workflows" / "spec_review.yaml").write_text("id: broken\nsteps: [", encoding="utf-8")
    (origin / "extra.md").write_text("added later", encoding="utf-8")
    assert services.definitions.validate_bundle("demo")["workflows"] == ["spec_review"]
    again = start()
    assert again.installed.get("demo")["state"] == "ok"
    assert again.definitions.validate_bundle("demo")["valid"] is True
    assert not (services.bundle_path("demo") / "extra.md").exists()


def test_changed_copy_does_not_load(world):
    start, tmp = world
    services = start()
    services.installed.install_folder("demo", make_data_bundle(tmp / "a"), actor=ACTOR)
    services.installed.install_folder("other", make_data_bundle(tmp / "b"), actor=ACTOR)
    workflow = installed_dir(services) / "demo" / "v1" / "workflows" / "spec_review.yaml"
    workflow.write_text(workflow.read_text(encoding="utf-8") + "# edited by hand\n", encoding="utf-8")
    again = start()
    entry = again.installed.get("demo")
    assert (entry["state"], entry["reason"]) == ("changed", "installed_bundle_changed")
    assert "demo" not in again.bundle_names() and "other" in again.bundle_names()
    with pytest.raises(ServiceError) as blocked:  # using it says why, instead of «not found»
        again.bundle_path("demo")
    assert (blocked.value.status, blocked.value.details["reason"]) == (409, "installed_bundle_changed")
    assert again.installed.get("other")["state"] == "ok"
    shutil.rmtree(installed_dir(services) / "other" / "v1")
    missing = start()
    assert missing.installed.get("other")["state"] == "missing"
    assert missing.installed.get("other")["reason"] == "installed_bundle_changed"
    assert "other" not in missing.bundle_names()


def test_name_clash_stops_startup(world):
    start, tmp = world
    services = start()
    services.installed.install_folder("demo", make_data_bundle(tmp / "a"), actor=ACTOR)
    elsewhere = make_data_bundle(tmp / "elsewhere")
    with pytest.raises(ValueError, match="^bundle name in use: demo$"):
        start({"demo": elsewhere})
    # The same name with the same resolved folder replaces nothing and does not stop the start.
    same = start({"demo": services.bundle_path("demo")})
    assert same.bundle_names() == ["demo"]
    with pytest.raises(ValueError, match="^bundle name in use: a$"):
        cli._parse_ui_bundles(["a=x", "a=y"])
    one, two = tmp / "one", tmp / "two"
    one.mkdir(); two.mkdir()
    with pytest.raises(ValueError, match="^bundle name in use: a$"):
        cli._parse_ui_bundles([f"a={one}", f"a={two}"])
    assert cli._parse_ui_bundles([f"a={one}", f"a={one}"]) == {"a": one}
    # Installing a name that a --bundle already holds is refused too.
    with pytest.raises(ServiceError) as error:
        same.installed.install_folder("demo", elsewhere, actor=ACTOR)
    assert (error.value.status, error.value.details["reason"]) == (409, "bundle_name_in_use")


def test_package_level(tmp_path):
    data = tmp_path / "data"
    (data / "sub").mkdir(parents=True)
    (data / "a.yaml").write_text("x: 1", encoding="utf-8")
    (data / "sub" / "b.md").write_text("b", encoding="utf-8")
    assert package_level(data) == "data"
    assert set(CODE_FREE_SUFFIXES) == {".yaml", ".yml", ".md", ".txt", ".json", ".csv"}
    (data / "sub" / "pycache_ok").mkdir()
    (data / "sub" / "contracts.py").write_text("x = 1", encoding="utf-8")
    assert package_level(data) == "code"
    (data / "sub" / "contracts.py").unlink()
    (data / "LICENSE").write_text("no suffix", encoding="utf-8")
    assert package_level(data) == "code"


def test_links_are_not_copied(world):
    start, tmp = world
    services = start()
    origin = make_data_bundle(tmp / "origin")
    outside = tmp / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    try:
        os.symlink(outside, origin / "link.md")
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let the test create a symbolic link")
    with pytest.raises(ServiceError) as error:
        services.installed.install_folder("demo", origin, actor=ACTOR)
    assert (error.value.status, error.value.code) == (422, "invalid_request")
    assert services.installed.list() == [] and "demo" not in services.bundle_names()
    assert not list((installed_dir(services) / ".staging").glob("*"))


def test_invalid_data_package_is_not_registered(world):
    start, tmp = world
    services = start()
    origin = make_data_bundle(tmp / "origin")
    (origin / "workflows" / "spec_review.yaml").write_text("id: spec_review\nsteps: []\n", encoding="utf-8")
    with pytest.raises(ServiceError) as error:
        services.installed.install_folder("demo", origin, actor=ACTOR)
    assert error.value.code == "invalid_definition"
    assert services.installed.list() == [] and not (installed_dir(services) / "demo").exists()
    with pytest.raises(ServiceError) as bad:
        services.installed.install_folder("bad name!", origin, actor=ACTOR)
    assert (bad.value.status, bad.value.code) == (422, "invalid_request")
    with pytest.raises(ServiceError) as other:
        services.installed.install_folder("demo", origin, actor="agent:codex")
    assert other.value.details["reason"] == "confirmation_required"


def test_code_package_installs_without_importing_and_keeps_the_scan_gate(world):
    start, tmp = world
    services = start()
    origin = tmp / "code"
    shutil.copytree(FIXTURE, origin)
    marker = tmp / "imported"
    with (origin / "contracts.py").open("a", encoding="utf-8") as stream:
        stream.write(f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n")
    entry = services.installed.install_folder("code", origin, actor=ACTOR)
    assert entry["level"] == "code" and not marker.exists()  # installing never runs the package
    assert services.bundle_scans._external(services.bundle_path("code")) is True
    assert services.bundle_scans.status("code")["fingerprint"] == entry["fingerprint"]


def test_remove_keeps_data(world):
    start, tmp = world
    services = start()
    services.installed.install_folder("demo", make_data_bundle(tmp / "origin"), actor=ACTOR)
    services.runs._bundle_runtime("demo")  # creates data_dir/bundles/demo/
    kept = services.settings.data_dir / "bundles" / "demo"
    assert kept.is_dir()
    result = services.installed.remove("demo", actor=ACTOR)
    assert result["kept_data"] is True and result["removed"] is True
    assert kept.is_dir() and (installed_dir(services) / "demo" / "v1").is_dir()
    assert "demo" not in services.bundle_names() and services.installed.list() == []
    assert "demo" not in start().bundle_names()
    with pytest.raises(ServiceError) as error:
        services.installed.remove("demo", actor=ACTOR)
    assert (error.value.status, error.value.details["reason"]) == (409, "bundle_not_installed")
    # Installing the name again is a new version, never an overwrite of the kept copy.
    again = services.installed.install_folder("demo", make_data_bundle(tmp / "second"), actor=ACTOR)
    assert again["version"] == 2


def test_reinstall_is_atomic(world):
    start, tmp = world
    services = start()
    services.installed.install_folder("demo", make_data_bundle(tmp / "v1"), actor=ACTOR)
    first = services.runs._bundle_runtime("demo")
    broken = make_data_bundle(tmp / "broken")
    (broken / "workflows" / "spec_review.yaml").write_text("id: spec_review\nsteps: []\n", encoding="utf-8")
    with pytest.raises(ServiceError):
        services.installed.reinstall("demo", broken, actor=ACTOR)
    assert services.installed.get("demo")["version"] == 1
    assert not (installed_dir(services) / "demo" / "v2").exists()
    assert services.runs._bundle_runtimes["demo"] is first  # a failed reinstall changes nothing
    entry = services.installed.reinstall("demo", make_data_bundle(tmp / "good", marker="new"), actor=ACTOR)
    assert entry["version"] == 2 and entry["state"] == "ok"
    assert "demo" not in services.runs._bundle_runtimes  # the cached environment was dropped
    second = services.runs._bundle_runtime("demo")
    assert second is not first and second.worker.on_finished is not None  # the new one keeps the hook
    assert services.bundle_path("demo").name == "v2"
    assert start().installed.get("demo")["version"] == 2
    with pytest.raises(ServiceError) as error:
        services.installed.reinstall("nope", tmp / "good", actor=ACTOR)
    assert error.value.details["reason"] == "bundle_not_installed"


def test_busy_package_refuses_reinstall_and_remove(world):
    start, tmp = world
    services = start()
    services.installed.install_folder("demo", make_data_bundle(tmp / "origin"), actor=ACTOR)
    run = services.runs.create(RunRequest(project_name="Busy", workflow_id="spec_review",
                                          inputs={"spec_text": "A synthetic specification."}), bundle="demo")
    runtime = services.runs._bundle_runtime("demo")
    with runtime.store.db.tx() as conn:
        conn.execute("UPDATE runs SET status='running' WHERE id=?", (run["id"],))
    assert "running" in BUSY_STATUSES
    other = make_data_bundle(tmp / "other", marker="v2")
    for call in (lambda: services.installed.reinstall("demo", other, actor=ACTOR),
                 lambda: services.installed.remove("demo", actor=ACTOR)):
        with pytest.raises(ServiceError) as error:
            call()
        assert (error.value.status, error.value.code, error.value.details["reason"]) == (409, "invalid_state", "bundle_busy")
    assert services.installed.get("demo")["version"] == 1 and "demo" in services.bundle_names()
    with runtime.store.db.tx() as conn:
        conn.execute("UPDATE runs SET status='completed' WHERE id=?", (run["id"],))
    assert services.installed.remove("demo", actor=ACTOR)["kept_data"] is True


# ---- API and CLI ---------------------------------------------------------------------------------------------------

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


def test_api_and_cli_parity(world, monkeypatch, capsys):
    start, tmp = world
    origin = make_data_bundle(tmp / "origin")
    via_api = start(root=tmp / "api-local")
    via_cli = start(root=tmp / "cli-local")
    with TestClient(create_app(services=via_api), base_url="http://localhost") as http, \
            TestClient(create_app(services=via_cli), base_url="http://localhost") as cli_client:
        created = http.post("/api/v1/installed", json={"name": "demo", "folder": str(origin), "actor": ACTOR,
                                                       "source_label": "synthetic"})
        assert created.status_code == 201, created.text
        output = invoke_cli(monkeypatch, capsys, cli_client, ["bundle", "install", "demo", str(origin)])
        from_cli = json.loads(output)
        expected = {key: value for key, value in created.json().items() if key not in {"installed_at", "source_label",
                                                                                        "channel"}}
        assert {key: value for key, value in from_cli.items()
                if key not in {"installed_at", "source_label", "channel"}} == expected
        assert (created.json()["channel"], from_cli["channel"]) == ("api", "api")
        listing = http.get("/api/v1/installed")
        assert listing.status_code == 200 and [item["name"] for item in listing.json()] == ["demo"]
        assert str(tmp) not in listing.text and "path" not in listing.json()[0]
        assert http.get("/api/v1/installed/demo").json() == listing.json()[0]
        assert json.loads(invoke_cli(monkeypatch, capsys, http, ["bundle", "list"])) == listing.json()
        assert http.get("/api/v1/installed/missing").status_code == 409
        again = http.post("/api/v1/installed", json={"name": "demo", "folder": str(origin), "actor": ACTOR})
        assert (again.status_code, again.json()["detail"]["details"]["reason"]) == (409, "bundle_name_in_use")
        second = make_data_bundle(tmp / "second", marker="v2")
        reinstalled = http.post("/api/v1/installed/demo/reinstall", json={"folder": str(second), "actor": ACTOR})
        assert reinstalled.status_code == 200 and reinstalled.json()["version"] == 2
        relative = invoke_cli(monkeypatch, capsys, cli_client, ["bundle", "reinstall", "demo", str(second)])
        assert json.loads(relative)["version"] == 2
        removed = http.post("/api/v1/installed/demo/remove", json={"actor": ACTOR})
        assert removed.status_code == 200 and removed.json()["kept_data"] is True
        assert json.loads(invoke_cli(monkeypatch, capsys, cli_client, ["bundle", "remove", "demo"]))["kept_data"] is True
        assert http.get("/api/v1/installed").json() == []


def test_api_refuses_trust_functions(world):
    start, tmp = world
    origin = make_data_bundle(tmp / "origin")
    services = start()
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        refused = client.post("/api/v1/installed", json={"name": "demo", "folder": str(origin), "actor": ACTOR,
                                                         "trust_functions": True})
        assert (refused.status_code, refused.json()["detail"]["code"]) == (422, "invalid_request")
        assert services.installed.list() == []
        invalid = client.post("/api/v1/installed", json={"name": "../x", "folder": str(origin), "actor": ACTOR})
        assert (invalid.status_code, invalid.json()["detail"]["code"]) == (422, "invalid_request")


def test_hand_edited_registry_with_a_bad_key_stops_startup(world):
    start, tmp = world
    root = make_settings(tmp / "local").data_dir / "installed"
    root.mkdir(parents=True)
    for key in ("../x", "a b"):
        entry = {"name": key, "path": f"{key}/v1", "fingerprint": "0" * 64}
        (root / "registry.json").write_text(json.dumps({"schema": REGISTRY_SCHEMA, "bundles": {key: entry}}),
                                            encoding="utf-8")
        with pytest.raises(ValueError, match="invalid entry"):
            start()
    (root / "registry.json").write_text(json.dumps({"schema": REGISTRY_SCHEMA, "bundles": {"ok": {"path": "ok/v1"}}}),
                                        encoding="utf-8")
    with pytest.raises(ValueError, match="'ok'"):  # the name inside the entry must match its key
        start()
