# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SKL-T02: external bundle gate with synthetic scans and GUI decisions."""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices, ServiceError
from test_offices_install import ACTOR, write_office


FIXTURE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


@pytest.fixture
def setup(tmp_path):
    bundle = shutil.copytree(FIXTURE, tmp_path / "external")
    marker = tmp_path / "imported"
    with (bundle / "contracts.py").open("a", encoding="utf-8") as stream:
        stream.write(f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n")
    fake_python = tmp_path / "tool" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_bytes(b"fake")
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="",
                        skillspector_python=str(fake_python), gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"team": bundle}, dry_run=True)
    services.bundle_scans.verifier = lambda path: True
    services.bundle_scans.env = {}
    yield services, bundle, marker
    services.bundle_scans.close()
    services.runs.close()


def runner(verdict="SAFE", findings=(), coverage=100):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        report = {"verdict": verdict, "skills": [{"path": ".", "coverage_percent": coverage}],
                  "findings": list(findings)}
        return SimpleNamespace(exit_code=0, stdout_tail=json.dumps(report), stdout_truncated=False, timed_out=False)
    return run, calls


def blocked(services, code):
    with pytest.raises(ServiceError) as error:
        services.definitions.validate_bundle("team")
    assert (error.value.status, error.value.code) == (409, code)
    return error.value.details


def decide(services, details, decision, *, risk_ack=False):
    token = ensure_token(services.settings.gui_token_file)
    headers = {"Authorization": f"Bearer {token}"}
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    with TestClient(app, base_url="http://localhost") as client:
        session = client.post("/gui/api/approvals/session", headers=headers,
                              json={"code": "synthetic-start-code"})
        assert session.status_code == 200, session.text
        path = f"/gui/api/approvals/{details['approval_id']}"
        challenge = client.post(path + "/challenge", headers=headers, json={})
        assert challenge.status_code == 200, challenge.text
        result = client.post(path + "/decision", headers=headers,
                              json={"decision": decision, "code": challenge.json()["code"], "risk_ack": risk_ack})
        assert result.status_code == 200, result.text
        return result.json()


def test_pending_precedes_contract_import_and_safe_loads(setup):
    services, bundle, marker = setup
    (bundle / "requirements-dev.txt").write_text("synthetic==1\n", encoding="utf-8")
    fake, calls = runner()
    services.bundle_scans.runner = fake
    pending = blocked(services, "bundle_scan_pending")
    assert not marker.exists() and not calls
    approval = services.user_approvals.status(pending["approval_id"])
    assert approval["kind"] == "osv_query" and "api.osv.dev" in str(approval["location"])
    assert "requirements-dev.txt" in approval["location"]
    decide(services, pending, "approve")
    assert blocked(services, "bundle_scan_running") == {"bundle": "team"}
    services.bundle_scans.close()
    assert services.bundle_scans.status("team")["state"] == "safe"
    assert services.definitions.validate_bundle("team")["valid"]
    assert marker.exists() and len(calls) == 1
    assert (services.settings.local_dir / "bundle-scans" / f"team-{services.bundle_scans.fingerprint(bundle)}.json").is_file()


def test_run_api_returns_pending_before_contract_import(setup):
    services, _, marker = setup
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post("/api/v1/runs", params={"bundle": "team"}, json={
            "project_name": "Synthetic", "workflow_id": "spec_review",
            "inputs": {"spec_text": "Synthetic input"},
        })
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "bundle_scan_pending"
        assert not marker.exists()


@pytest.mark.parametrize("verdict,needs_click", [("CAUTION", False), ("DO_NOT_INSTALL", True)])
def test_verdicts_and_fingerprint_change(setup, verdict, needs_click):
    services, bundle, _ = setup
    finding = {"level": "high", "rule": "synthetic", "message": "Review this", "file": "SKILL.md"}
    fake, calls = runner(verdict, [finding], coverage=60 if needs_click else 100)
    services.bundle_scans.runner = fake
    decide(services, blocked(services, "bundle_scan_pending"), "approve")
    blocked(services, "bundle_scan_running")
    services.bundle_scans.close()
    if needs_click:
        pending = blocked(services, "bundle_scan_blocked")
        assert pending["findings"][0]["rule"] == "synthetic"
        assert pending["risk_ack_required"] is True
        assert services.bundle_scans.status("team")["recommendation"] == "do_not_install"
        decide(services, pending, "approve", risk_ack=True)
    assert services.definitions.validate_bundle("team")["valid"]
    assert len(calls) == 1
    (bundle / "requirements-dev.txt").write_text("example==1\n", encoding="utf-8")
    assert blocked(services, "bundle_scan_pending")["approval_id"]
    assert len(calls) == 1


def test_rejected_osv_requires_bundle_load_click(setup):
    services, _, marker = setup
    fake, calls = runner()
    services.bundle_scans.runner = fake
    decide(services, blocked(services, "bundle_scan_pending"), "reject")
    pending = blocked(services, "bundle_scan_blocked")
    assert services.bundle_scans.status("team")["reason"] == "osv_rejected"
    assert not marker.exists() and not calls
    decide(services, pending, "approve", risk_ack=True)
    assert services.definitions.validate_bundle("team")["valid"]


def test_load_ack_is_required_before_spending_code(setup):
    services, _, marker = setup
    decide(services, blocked(services, "bundle_scan_pending"), "reject")
    pending = blocked(services, "bundle_scan_blocked")
    assert pending["risk_ack_required"] is True
    token = ensure_token(services.settings.gui_token_file)
    headers = {"Authorization": f"Bearer {token}"}
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/gui/api/approvals/session", headers=headers,
                           json={"code": "synthetic-start-code"}).status_code == 200
        path = f"/gui/api/approvals/{pending['approval_id']}"
        code = client.post(path + "/challenge", headers=headers, json={}).json()["code"]
        rejected = client.post(path + "/decision", headers=headers,
                               json={"decision": "approve", "code": code})
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "risk_ack_required"
        assert services.user_approvals.status(pending["approval_id"])["state"] == "pending"
        assert not marker.exists()
        accepted = client.post(path + "/decision", headers=headers,
                               json={"decision": "approve", "code": code, "risk_ack": True})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["risk_ack"] is True
    assert services.definitions.validate_bundle("team")["valid"]
    assert services.bundle_scans.status("team")["load_approved"] is True


def test_rejecting_bundle_load_needs_no_ack(setup):
    services, _, marker = setup
    decide(services, blocked(services, "bundle_scan_pending"), "reject")
    pending = blocked(services, "bundle_scan_blocked")
    rejected = decide(services, pending, "reject")
    assert rejected["state"] == "rejected"
    assert blocked(services, "bundle_scan_blocked")["approval_id"] == pending["approval_id"]
    assert not marker.exists()


def test_old_cache_without_risk_ack_asks_again_once(setup):
    services, bundle, _ = setup
    fingerprint = services.bundle_scans.fingerprint(bundle)
    services.bundle_scans._write_cache("team", fingerprint, {
        "fingerprint": fingerprint, "scan": {"state": "not_analyzed", "reason": "osv_rejected"},
        "load_approved": True,
    })
    old = services.user_approvals.request(
        kind="bundle_load", destination={"bundle": "team", "kind": "bundle_load"},
        location={"bundle": "team", "fingerprint": fingerprint}, sha256=fingerprint)
    with services.tasks.store.transaction() as conn:
        services.tasks.store.append_event(conn, "user_approval.decided", actor="user:gui",
                                          ref=old["id"], payload={"decision": "approved"})
    assert services.bundle_scans.status("team")["load_approved"] is False
    first = blocked(services, "bundle_scan_blocked")
    second = blocked(services, "bundle_scan_blocked")
    assert first["approval_id"] == second["approval_id"]
    assert first["approval_id"] != old["id"]
    assert first["risk_ack_required"] is True
    decide(services, first, "approve", risk_ack=True)
    assert services.definitions.validate_bundle("team")["valid"]


def test_cache_survives_new_facade(setup):
    services, bundle, _ = setup
    fake, calls = runner()
    services.bundle_scans.runner = fake
    decide(services, blocked(services, "bundle_scan_pending"), "approve")
    blocked(services, "bundle_scan_running")
    services.bundle_scans.close()
    another = ApplicationServices.create(services.settings, bundles={"team": bundle}, dry_run=True)
    try:
        assert another.definitions.validate_bundle("team")["valid"]
        assert len(calls) == 1
    finally:
        another.bundle_scans.close()
        another.runs.close()


def test_simultaneous_loads_share_one_worker(setup):
    services, _, _ = setup
    entered, release = threading.Event(), threading.Event()
    fake, calls = runner()
    def waiting(argv, **kwargs):
        entered.set()
        assert release.wait(5)
        return fake(argv, **kwargs)
    services.bundle_scans.runner = waiting
    decide(services, blocked(services, "bundle_scan_pending"), "approve")
    try:
        blocked(services, "bundle_scan_running")
        assert entered.wait(5)
        blocked(services, "bundle_scan_running")
        assert not calls
    finally:
        release.set()
        services.bundle_scans.close()
    assert len(calls) == 1


def test_read_only_routes_and_cli(setup, monkeypatch, capsys):
    services, _, _ = setup
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        api = client.get("/api/v1/bundles/team/scan")
        assert api.status_code == 200 and api.json()["state"] == "unscanned"
        assert client.get("/gui/api/bundles/team/scan").status_code == 401
        gui = client.get("/gui/api/bundles/team/scan", headers={"Authorization": f"Bearer {token}"})
        assert gui.status_code == 200 and gui.json() == api.json()
        monkeypatch.setattr(cli, "_api_json", lambda method, path: api.json() if method == "GET" else None)
        cli.main(["bundle-scan", "team"])
        assert json.loads(capsys.readouterr().out) == api.json()
    assert services.user_approvals.pending() == []


def test_missing_tool_loads_with_warning(setup):
    services, _, _ = setup
    services.bundle_scans.env = {}
    services.bundle_scans.verifier = None
    services.settings.skillspector_python = ""
    assert services.definitions.validate_bundle("team")["valid"]
    state = services.bundle_scans.status("team")
    assert state["state"] == "not_installed" and state["warning"]


def test_run_runtime_is_gated_but_missing_store_lookup_is_read_only(setup):
    services, _, marker = setup
    assert services.runs._bundle_runtime("team", create=False) is None
    assert services.user_approvals.pending() == []
    with pytest.raises(ServiceError) as error:
        services.runs._bundle_runtime("team")
    assert error.value.code == "bundle_scan_pending" and not marker.exists()


def test_fingerprint_covers_same_size_content_and_framework_examples_are_exempt(setup):
    services, bundle, _ = setup
    path = bundle / "requirements.txt"
    path.write_text("demo==1\n", encoding="utf-8")
    first = services.bundle_scans.fingerprint(bundle)
    path.write_text("demo==2\n", encoding="utf-8")
    assert services.bundle_scans.fingerprint(bundle) != first
    example = Path(__file__).resolve().parents[1] / "examples" / "bundles" / "general"
    built_in = ApplicationServices.create(services.settings, bundles={"general": example}, dry_run=True)
    try:
        assert built_in.bundle_scans.status("general")["state"] == "exempt"
        assert built_in.user_approvals.pending() == []
    finally:
        built_in.bundle_scans.close()
        built_in.runs.close()


# ---- review retouches (2026-11-20): bytecode, interrupted scans, a tool installed later

def test_contracts_run_from_source_and_keep_the_fingerprint(setup, tmp_path, monkeypatch):
    import importlib.util
    import py_compile
    import sys

    from multiagent.bundles import load_bundle
    services, bundle, marker = setup
    monkeypatch.setattr(sys, "dont_write_bytecode", False)  # as a GUI started without PYTHONDONTWRITEBYTECODE
    shutil.rmtree(bundle / "__pycache__", ignore_errors=True)  # a copy of the fixture may carry an old one
    before = services.bundle_scans.fingerprint(bundle)
    load_bundle(bundle, services.settings)
    assert marker.exists() and not list(bundle.rglob("*.pyc"))
    assert services.bundle_scans.fingerprint(bundle) == before
    # An unchecked .pyc next to a clean source must never run in its place.
    planted = tmp_path / "planted"
    other = tmp_path / "other.py"
    other.write_text(f"from pathlib import Path\nPath({str(planted)!r}).write_text('ran')\nOUTPUT_SCHEMAS = {{}}\n",
                     encoding="utf-8")
    py_compile.compile(str(other), cfile=importlib.util.cache_from_source(str(bundle / "contracts.py")),
                       doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    load_bundle(bundle, services.settings)
    assert not planted.exists()
    assert services.bundle_scans.fingerprint(bundle) == before


def test_second_load_in_the_same_process_needs_no_new_scan(setup, monkeypatch):
    import sys

    services, _, _ = setup
    monkeypatch.setattr(sys, "dont_write_bytecode", False)  # as a GUI started without PYTHONDONTWRITEBYTECODE
    fake, calls = runner()
    services.bundle_scans.runner = fake
    decide(services, blocked(services, "bundle_scan_pending"), "approve")
    blocked(services, "bundle_scan_running")
    services.bundle_scans.close()
    assert services.definitions.validate_bundle("team")["valid"]
    assert services.definitions.validate_bundle("team")["valid"]
    assert len(calls) == 1 and services.user_approvals.pending() == []


def test_interrupted_scan_asks_again(setup):
    services, bundle, _ = setup
    release = threading.Event()
    fake, _ = runner()

    def waiting(argv, **kwargs):
        assert release.wait(5)
        return fake(argv, **kwargs)
    services.bundle_scans.runner = waiting
    first = blocked(services, "bundle_scan_pending")
    decide(services, first, "approve")
    blocked(services, "bundle_scan_running")
    # A new start that never saw the result: the consumed approval must not leave the bundle stuck.
    restarted = ApplicationServices.create(services.settings, bundles={"team": bundle}, dry_run=True)
    restarted.bundle_scans.verifier = lambda path: True
    restarted.bundle_scans.env = {}
    try:
        with pytest.raises(ServiceError) as error:
            restarted.definitions.validate_bundle("team")
        assert error.value.code == "bundle_scan_pending"
        assert error.value.details["approval_id"] != first["approval_id"]
    finally:
        release.set()
        services.bundle_scans.close()
        restarted.bundle_scans.close()
        restarted.runs.close()


def test_missing_tool_is_not_remembered_once_installed(setup):
    services, bundle, _ = setup
    settings = services.settings.model_copy(update={"skillspector_python": ""})
    missing = ApplicationServices.create(settings, bundles={"team": bundle}, dry_run=True)
    try:
        assert missing.definitions.validate_bundle("team")["valid"]
        assert missing.bundle_scans.status("team")["state"] == "not_installed"
    finally:
        missing.bundle_scans.close()
        missing.runs.close()
    assert not list((services.settings.local_dir / "bundle-scans").glob("*.json"))
    # The fixture's facade has the tool: the same bundle is now scanned, not loaded unscanned.
    blocked(services, "bundle_scan_pending")


# ---- PAQ-T01: an installed copy is always scanned, even when data_dir sits inside the checkout

def test_installed_copy_inside_checkout_is_scanned():
    from multiagent.services.bundle_scans import BundleScanServices

    checkout = Path(__file__).resolve().parents[1]
    data_dir = checkout / "synthetic-data-dir"  # only a path: nothing is created
    scans = BundleScanServices(SimpleNamespace(settings=SimpleNamespace(data_dir=data_dir)))
    assert scans._external(data_dir / "installed" / "demo" / "v1") is True
    assert scans._external(data_dir / "bundles" / "demo") is False  # the rest of the checkout stays exempt
    assert scans._external(checkout / "examples" / "bundles" / "general") is False


def test_incomplete_office_registry_uses_bundle_scan(setup, tmp_path):
    services, _, _ = setup
    services.offices.import_folder(write_office(tmp_path / "office"), actor=ACTOR)
    services.offices._write({})
    fake, _ = runner()
    services.bundle_scans.runner = fake

    with pytest.raises(ServiceError) as error:
        services.definitions.validate_bundle("demo__recepcion")

    assert error.value.code == "bundle_scan_pending"
    approval = services.user_approvals._all()[error.value.details["approval_id"]]
    assert approval["destination"] == {"bundle": "demo__recepcion", "kind": "osv_query"}


def test_office_cache_seeds_each_area(setup, tmp_path):
    services, bundle, _ = setup
    first = shutil.copytree(bundle, tmp_path / "area-one")
    second = shutil.copytree(bundle, tmp_path / "area-two")
    (first / "requirements.txt").write_text("first==1\n", encoding="utf-8")
    (second / "requirements.txt").write_text("second==2\n", encoding="utf-8")
    calls = []

    def fake(argv, **kwargs):
        areas = {first.resolve(): first.name, second.resolve(): second.name}
        area = next(areas[Path(argument).resolve()] for argument in argv if Path(argument).resolve() in areas)
        calls.append(area)
        verdict = "SAFE" if area == "area-one" else "CAUTION"
        report = {"verdict": verdict, "skills": [{"path": ".", "coverage_percent": 100}], "findings": []}
        return SimpleNamespace(exit_code=0, stdout_tail=json.dumps(report), stdout_truncated=False, timed_out=False)

    services.bundle_scans.runner = fake
    folders = {"uno": first, "dos": second}
    office_fingerprint = services.bundle_scans.fingerprint(first)
    pending = services.bundle_scans.scan_office("demo", office_fingerprint, folders)
    assert pending["state"] == "pending" and pending["approval_id"]
    decide(services, {"approval_id": pending["approval_id"]}, "approve")
    assert services.bundle_scans.scan_office("demo", office_fingerprint, folders)["state"] == "running"
    services.bundle_scans.close()

    result = services.bundle_scans.scan_office("demo", office_fingerprint, folders)
    assert result["approval_id"] == pending["approval_id"]
    assert result["state"] == "caution"
    assert {area: row["state"] for area, row in result["areas"].items()} == {"uno": "safe", "dos": "caution"}
    assert calls == ["area-one", "area-two"]
    for area_id, folder in folders.items():
        fingerprint = services.bundle_scans.fingerprint(folder)
        path = services.bundle_scans._cache_path(f"demo__{area_id}", fingerprint)
        cached = json.loads(path.read_text(encoding="utf-8"))
        assert cached["fingerprint"] == fingerprint
        assert cached["scan"]["state"] == result["areas"][area_id]["state"]
