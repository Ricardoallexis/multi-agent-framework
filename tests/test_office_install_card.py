# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFI-T04: one approved office card stages, scans and installs every area."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.offices import OFFICE_NOTICE, STAGE_TTL_SECONDS
from multiagent.gui.token import ensure_token
from test_offices_install import ACTOR, make_settings, write_office


@pytest.fixture
def world(tmp_path):
    opened: list[ApplicationServices] = []

    def start(*, skillspector: bool = False) -> ApplicationServices:
        settings = make_settings(tmp_path / "local")
        if skillspector:
            interpreter = tmp_path / "skillspector" / "Scripts" / "python.exe"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_bytes(b"synthetic interpreter")
            settings = settings.model_copy(update={"skillspector_python": str(interpreter)})
        services = ApplicationServices.create(settings, dry_run=True)
        if skillspector:
            services.bundle_scans.verifier = lambda _path: True
            services.bundle_scans.env = {}
        opened.append(services)
        return services

    yield start, tmp_path
    for services in opened:
        services.bundle_scans.close()
        services.runs.close()


def _decision_response(services: ApplicationServices, approval_id: str, decision: str = "approve", *,
                       risk_ack: bool = False):
    token = ensure_token(services.settings.gui_token_file)
    headers = {"Authorization": f"Bearer {token}"}
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    with TestClient(app, base_url="http://localhost") as client:
        session = client.post("/gui/api/approvals/session", headers=headers,
                              json={"code": "synthetic-start-code"})
        assert session.status_code == 200, session.text
        path = f"/gui/api/approvals/{approval_id}"
        challenge = client.post(path + "/challenge", headers=headers, json={})
        assert challenge.status_code == 200, challenge.text
        response = client.post(path + "/decision", headers=headers,
                               json={"decision": decision, "code": challenge.json()["code"], "risk_ack": risk_ack})
    services.runs.reopen()
    return response


def _decide(services: ApplicationServices, approval_id: str, *, risk_ack: bool = False) -> dict:
    response = _decision_response(services, approval_id, risk_ack=risk_ack)
    assert response.status_code == 200, response.text
    return response.json()


def _install(services: ApplicationServices, card: dict, *, risk_ack: bool = False) -> dict:
    _decide(services, card["approval_id"], risk_ack=risk_ack)
    return services.offices.install_staged(card["stage_id"], approval_id=card["approval_id"])


def _reason(call, *args, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


def test_card_shows_what_the_design_lists(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_office(tmp_path / "demo"))

    assert card["office"] == {"id": "demo", "name": "Demo office", "description": None}
    assert card["level"] == "data"
    assert card["notice"] == OFFICE_NOTICE
    assert [area["id"] for area in card["areas"]] == ["reception", "proposal"]
    for area in card["areas"]:
        assert {"members", "requires", "sensitivity", "declares"} <= set(area)
        assert {"workflows", "contracts", "roles"} == set(area["declares"])
    assert card["code"]["present"] is False
    assert card["scan"]["state"] == "not_installed"
    assert services.user_approvals.status(card["approval_id"])["state"] == "pending"
    assert card["approval_id"] not in {item["id"] for item in services.user_approvals.pending()}


def test_one_record_approves_every_team(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_office(tmp_path / "demo"))
    result = _install(services, card)

    assert result["changes"]["added"] == ["reception", "proposal"]
    for area in ("reception", "proposal"):
        approved = services.teams.approved(f"demo__{area}")
        assert approved is not None
        assert approved["status"] == "approved"
        assert approved["note"] == (
            f"approved when installing office demo {card['fingerprint']} "
            f"(approval {card['approval_id']})"
        )
        assert approved["decided_by"] == "user:operator"
        assert approved["decided_channel"] == "gui"
    approvals = [item for item in services.user_approvals._all().values() if item["kind"] == "office_install"]
    assert len(approvals) == 1
    assert approvals[0]["state"] == "consumed"
    assert all(item["id"] != card["approval_id"] for item in services.user_approvals.pending())


def test_install_needs_an_approved_unused_record(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_office(tmp_path / "demo"))

    error = _reason(services.offices.install_staged, card["stage_id"], approval_id=card["approval_id"])
    assert error.details["reason"] == "office_install_not_approved"
    _decide(services, card["approval_id"])
    services.offices.install_staged(card["stage_id"], approval_id=card["approval_id"])
    again = _reason(services.offices.install_staged, card["stage_id"], approval_id=card["approval_id"])
    assert again.details["reason"] == "office_install_not_approved"


def test_changed_folder_invalidates_the_card(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_office(tmp_path / "demo"))
    staged_area = services.offices._stage_folder(card["stage_id"]) / "source" / "areas" / "reception"
    (staged_area / "prompts" / "spec" / "extract_requirements.v1.md").write_text(
        "Changed after the card.\n", encoding="utf-8")

    error = _reason(services.offices.install_staged, card["stage_id"], approval_id=card["approval_id"])
    assert error.details["reason"] == "office_changed_since_card"


def test_office_scan_asks_one_osv_query_for_all_areas(world):
    start, tmp_path = world
    services = start(skillspector=True)
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        report = {"verdict": "SAFE", "skills": [{"path": ".", "coverage_percent": 100}], "findings": []}
        return SimpleNamespace(exit_code=0, stdout_tail=json.dumps(report), stdout_truncated=False, timed_out=False)

    services.bundle_scans.runner = runner
    folder = write_office(tmp_path / "three", areas=("one", "two", "three"))
    card = services.offices.stage_folder(folder)
    assert card["scan"]["state"] == "pending"
    osv = [item for item in services.user_approvals._all().values() if item["kind"] == "osv_query"]
    assert len(osv) == 1
    assert osv[0]["destination"] == {"office": "demo", "kind": "osv_query"}

    _decide(services, card["scan"]["approval_id"])
    entered, release = threading.Event(), threading.Event()
    base_runner = services.bundle_scans.runner

    def blocked_runner(argv, **kwargs):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("synthetic scan was not released")
        return base_runner(argv, **kwargs)

    services.bundle_scans.runner = blocked_runner
    running = services.offices.card(card["stage_id"])
    assert running["scan"]["state"] == "running"
    try:
        assert entered.wait(5)
        assert services.offices.card(card["stage_id"])["scan"]["state"] == "running"
        assert len([item for item in services.user_approvals._all().values()
                    if item["kind"] == "osv_query"]) == 1
    finally:
        release.set()
    services.bundle_scans.close()
    scanned = services.offices.card(card["stage_id"])
    assert scanned["scan"]["state"] == "safe"
    assert scanned["risk_ack_required"] is True
    assert set(scanned["scan"]["areas"]) == {"one", "two", "three"}
    assert all(area["state"] == "safe" for area in scanned["scan"]["areas"].values())
    assert len(calls) == 3

    _decide(services, card["approval_id"], risk_ack=True)
    services.offices.install_staged(card["stage_id"], approval_id=card["approval_id"])
    before = len(services.user_approvals._all())
    for area in ("one", "two", "three"):
        services.runs.create(RunRequest(project_name=f"Run {area}", workflow_id="spec_review",
                                        inputs={"spec_text": "Synthetic input."}),
                             bundle=f"demo__{area}")
    assert len(services.user_approvals._all()) == before
    assert len([item for item in services.user_approvals._all().values() if item["kind"] == "osv_query"]) == 1


def test_api_installed_areas_share_one_office_query(world):
    start, tmp_path = world
    services = start(skillspector=True)

    def runner(_argv, **_kwargs):
        report = {"verdict": "SAFE", "skills": [{"path": ".", "coverage_percent": 100}], "findings": []}
        return SimpleNamespace(exit_code=0, stdout_tail=json.dumps(report), stdout_truncated=False, timed_out=False)

    services.bundle_scans.runner = runner
    services.offices.import_folder(write_office(tmp_path / "api", areas=("one", "two")), actor=ACTOR)

    first = _reason(services.definitions.validate_bundle, "demo__one")
    assert first.code == "bundle_scan_pending"
    assert first.details["office"] == "demo"
    second = _reason(services.definitions.validate_bundle, "demo__two")
    assert second.code == "bundle_scan_pending"
    assert second.details["approval_id"] == first.details["approval_id"]
    queries = [item for item in services.user_approvals._all().values() if item["kind"] == "osv_query"]
    assert len(queries) == 1
    assert queries[0]["destination"] == {"office": "demo", "kind": "osv_query"}

    _decide(services, first.details["approval_id"])
    entered, release = threading.Event(), threading.Event()
    base_runner = services.bundle_scans.runner

    def blocked_runner(argv, **kwargs):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("synthetic scan was not released")
        return base_runner(argv, **kwargs)

    services.bundle_scans.runner = blocked_runner
    try:
        assert _reason(services.definitions.validate_bundle, "demo__one").code == "bundle_scan_running"
        assert entered.wait(5)
        assert _reason(services.definitions.validate_bundle, "demo__two").code == "bundle_scan_running"
        assert len([item for item in services.user_approvals._all().values() if item["kind"] == "osv_query"]) == 1
    finally:
        release.set()
    services.bundle_scans.close()
    assert services.definitions.validate_bundle("demo__one")["valid"] is True


def test_level_2_needs_advanced_mode_and_risk_ack(world):
    start, tmp_path = world
    services = start()
    folder = write_office(tmp_path / "code")
    (folder / "areas" / "reception" / "functions.py").write_text(
        "from multiagent.functions import register_function\n"
        "def calculate(context, arguments):\n"
        "    return {'steps': {}}\n"
        "register_function('demo.calculate', calculate, description='Synthetic calculation')\n",
        encoding="utf-8")

    simple = _reason(services.offices.stage_folder, folder)
    assert simple.status == 409
    assert simple.details == {"reason": "advanced_mode_required", "action": "install_code"}
    services.gui_settings.set_mode("advanced")
    card = services.offices.stage_folder(folder)
    assert card["level"] == "code" and card["risk_ack_required"] is True

    no_ack = _decision_response(services, card["approval_id"])
    assert no_ack.status_code == 422
    assert no_ack.json()["detail"]["code"] == "risk_ack_required"
    _decide(services, card["approval_id"], risk_ack=True)
    services.offices.install_staged(card["stage_id"], approval_id=card["approval_id"])
    package = services.installed.get("demo__reception")
    assert package["functions"]["trusted"] is True


def test_card_is_not_reachable_from_api_v1(world):
    start, tmp_path = world
    services = start()
    folder = write_office(tmp_path / "code")
    (folder / "areas" / "reception" / "functions.py").write_text("def calculate(context, arguments): return {}\n",
                                                                   encoding="utf-8")

    app = create_app(services=services)
    paths = [getattr(route, "path", "") for route in app.routes]
    office_paths = [path for path in paths if path.startswith("/api/v1/offices")]
    assert office_paths
    assert not any("stage" in path or "install-staged" in path for path in office_paths)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/v1/offices/stage", json={}).status_code in {404, 405}
        assert client.post("/api/v1/offices/install-staged", json={}).status_code in {404, 405}
        response = client.post("/api/v1/offices", json={"folder": str(folder), "actor": ACTOR})
    assert response.status_code == 409
    assert response.json()["detail"]["details"]["reason"] == "office_has_code"


def test_reimport_card_lists_only_changes(world):
    start, tmp_path = world
    services = start()
    _install(services, services.offices.stage_folder(write_office(tmp_path / "original")))
    unchanged_before = services.teams.approved("demo__proposal")["id"]
    changed = write_office(tmp_path / "changed", markers={"reception": "updated"})
    card = services.offices.stage_folder(changed, reimport="demo")

    assert [(area["id"], area["change"]) for area in card["areas"]] == [("reception", "changed")]
    installed = _install(services, card)
    assert installed["changes"]["changed"] == ["reception"]
    assert services.teams.approved("demo__proposal")["id"] == unchanged_before


def test_area_card_enters_an_existing_office(world):
    start, tmp_path = world
    services = start(skillspector=True)

    def runner(_argv, **_kwargs):
        report = {"verdict": "DO_NOT_INSTALL", "skills": [{"path": ".", "coverage_percent": 100}],
                  "findings": []}
        return SimpleNamespace(exit_code=0, stdout_tail=json.dumps(report), stdout_truncated=False, timed_out=False)

    services.bundle_scans.runner = runner
    services.offices.import_folder(write_office(tmp_path / "original"), actor=ACTOR)
    incoming = write_office(tmp_path / "incoming", office_id="extra", areas=("notices",))
    card = services.offices.stage_folder(incoming, into="demo")

    assert card["kind"] == "area"
    assert card["into"] == "demo"
    assert card["office"]["id"] == "demo"
    assert [area["id"] for area in card["areas"]] == ["notices"]
    assert set(card["scan"]["areas"]) == {"reception", "proposal", "notices"}
    _decide(services, card["scan"]["approval_id"])
    assert services.offices.card(card["stage_id"])["scan"]["state"] == "running"
    services.bundle_scans.close()
    scanned = services.offices.card(card["stage_id"])
    assert scanned["scan"]["state"] == "do_not_install"
    assert scanned["risk_ack_required"] is True
    _decide(services, card["approval_id"], risk_ack=True)
    services.offices.install_staged(card["stage_id"], approval_id=card["approval_id"])
    for area in ("reception", "proposal", "notices"):
        bundle = f"demo__{area}"
        fingerprint = services.bundle_scans.fingerprint(services.bundle_path(bundle))
        cached = services.bundle_scans._cached(bundle, fingerprint)
        assert cached is not None
        assert cached["load_approved"] is True and cached["risk_ack"] is True
    assert "demo__notices" in services.bundle_names()
    before = len(services.user_approvals._all())
    for area in ("reception", "proposal", "notices"):
        services.runs.create(RunRequest(project_name=f"Run {area}", workflow_id="spec_review",
                                        inputs={"spec_text": "Synthetic input."}),
                             bundle=f"demo__{area}")
    assert len(services.user_approvals._all()) == before
    assert len([item for item in services.user_approvals._all().values() if item["kind"] == "osv_query"]) == 1


def test_cancel_discards_the_stage(world):
    start, tmp_path = world
    services = start()
    before = services.offices.list()
    card = services.offices.stage_folder(write_office(tmp_path / "demo"))

    services.offices.discard_stage(card["stage_id"])
    approval = services.user_approvals.status(card["approval_id"])
    assert approval["state"] == "rejected"
    assert not services.offices._stage_folder(card["stage_id"]).exists()
    assert services.offices.list() == before


def test_stage_expires(world):
    start, tmp_path = world
    services = start()
    clock = [1000.0]
    services.offices.clock = lambda: clock[0]
    card = services.offices.stage_folder(write_office(tmp_path / "demo"))
    clock[0] += STAGE_TTL_SECONDS + 1

    error = _reason(services.offices.install_staged, card["stage_id"], approval_id=card["approval_id"])
    assert error.details["reason"] == "office_stage_expired"
    assert services.user_approvals.status(card["approval_id"])["state"] == "rejected"
