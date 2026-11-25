# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Connection checks use synthetic facts and never invoke generation."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.cli import build_parser
from multiagent.config import Settings
from multiagent.gui.connections_api import add_connection_routes
from multiagent.gui.security import require_token
from multiagent.gui.token import ensure_token
from multiagent.routing import ExecutorDescriptor
from multiagent.services import ApplicationServices
from multiagent.services.connections import ConnectionServices, IDS


def fixture(*, missing=None, signed_out=None, enabled=True, notice=True, quota=0, health=None, key=True,
            acp_installed=False, acp_probe=None):
    missing, signed_out = set(missing or ()), set(signed_out or ())
    def detector(**kwargs):
        assert kwargs["include_antigravity"] is True
        return {"contexts": {"packaged_process": {"apps": [
            {"app": app, "detection": "not_detected" if app in missing else "detected", "version": "1.2.3",
             "session": {"state": "signed_out" if app in signed_out else "signed_in"}}
            for app in ("claude", "codex", "copilot", "agy")]}}}
    def forbidden(*args, **kwargs):
        raise AssertionError("generation was called")
    descriptor = ExecutorDescriptor(executor_id="codex_cli", surface="codex_cli",
                                    transports=("cli",), enabled=enabled)
    registry = SimpleNamespace(
        get=lambda identifier: descriptor if identifier == "codex_cli" else None,
        snapshot=lambda: {"codex_cli": descriptor},
    )
    settings = SimpleNamespace(gemini_api_key="SYNTHETIC_SECRET" if key else "",
                               openai_api_key="SYNTHETIC_SECRET" if key else "", openai_enabled=True,
                               antigravity_notice_accepted="agy-third-party-notice/1" if notice else "")
    executor = SimpleNamespace(status=lambda: {"executors": [
        {"executor_id": identifier, "enabled": enabled, "pause_reason": None}
        for identifier in IDS if identifier.endswith("_cli")]}, registry=registry, run_task=forbidden)
    usage = SimpleNamespace(plans=lambda: {"apps": [
        {"app": app, "status": "ok", "windows": [{"window": "weekly", "used_percent": quota,
                                                    "resets_at": "2026-12-01T00:00:00Z"}]}
        for app in ("claude", "codex", "copilot", "antigravity")]})
    adapter = SimpleNamespace(health=lambda: health or {"available": True, "version": "0.1"}, generate=forbidden,
                              generate_structured=forbidden)
    services = SimpleNamespace(settings=settings, executors=executor, usage=usage,
                               system=SimpleNamespace(adapters={"ollama": adapter}))
    return ConnectionServices(services, detector=detector,
                              clock=lambda: datetime(2026, 11, 18, tzinfo=timezone.utc),
                              acp_resolver=lambda cwd: r"C:\synthetic\codex-acp.exe" if acp_installed else None,
                              acp_probe=acp_probe or (lambda *args, **kwargs: {"status": "no_response"}))


def test_ready_missing_install_session_and_cache():
    service = fixture(missing={"codex"}, signed_out={"claude"})
    rows = {row["id"]: row for row in service.list()["connections"]}
    assert len(rows) == 8 and rows["ollama"]["available"] is True
    assert rows["codex_cli"]["missing_step"] == "install"
    assert rows["codex_acp"]["missing_step"] == "install"
    assert rows["claude_cli"]["missing_step"] == "session"
    assert rows["copilot_cli"]["session"] == "unknown"
    assert rows["copilot_cli"]["available"] is False
    assert rows["antigravity_cli"]["session"] == "signed_in"
    assert service.list()["connections"][0]["checked_at"] == "2026-11-18T00:00:00+00:00"


def test_codex_and_codex_acp_detection_are_independent():
    acp_only = {row["id"]: row for row in fixture(missing={"codex"}, acp_installed=True).list()["connections"]}
    assert acp_only["codex_cli"]["installed"] is False
    assert acp_only["codex_acp"]["installed"] is True
    assert acp_only["codex_acp"]["enabled"] is False
    assert acp_only["codex_acp"]["available"] is False
    assert acp_only["codex_acp"]["reason"] == "disabled"

    cli_only = {row["id"]: row for row in fixture().list()["connections"]}
    assert cli_only["codex_cli"]["installed"] is True
    assert cli_only["codex_acp"]["installed"] is False


def test_acp_probe_is_separate_from_activation_and_registry_snapshot():
    def fake_probe(executable, *, cwd):
        assert executable == r"C:\synthetic\codex-acp.exe"
        return {"status": "compatible", "protocol_version": 1,
                "capabilities": {"loadSession": True}, "auth_methods": []}

    service = fixture(acp_installed=True, acp_probe=fake_probe)
    registry = service._services.executors.registry
    before = registry.snapshot()
    before_status = service._services.executors.status()["executors"]
    row = service.probe_acp("codex_acp")

    assert row["enabled"] is False and row["session"] == "not_applicable"
    assert row["available"] is False
    assert row["acp_probe"]["status"] == "compatible"
    descriptor = row["acp_probe"]["descriptor"]
    assert descriptor["transports"] == ["cli", "acp"]
    assert descriptor["transport_evidence"]["values"] == ["acp"]
    assert descriptor["enabled"] is before["codex_cli"].enabled
    assert descriptor["mode"] == before["codex_cli"].mode
    assert registry.snapshot() == before
    assert service._services.executors.status()["executors"] == before_status
    assert "stdout" not in row["acp_probe"] and "stderr" not in row["acp_probe"]


def test_acp_initialize_runs_only_for_explicit_post(tmp_path):
    calls = []

    def fake_probe(executable, *, cwd):
        calls.append(executable)
        return {"status": "no_response", "reason": "empty_output"}

    service = fixture(acp_installed=True, acp_probe=fake_probe)
    app = FastAPI()
    app.state.gui_token_file = tmp_path / "gui-token"
    app.state.gui_token_file.write_text("synthetic-token-with-enough-length", encoding="utf-8")
    router = APIRouter(prefix="/gui/api", dependencies=[Depends(require_token)])
    add_connection_routes(router, SimpleNamespace(connections=service))
    app.include_router(router)
    headers = {"Authorization": "Bearer synthetic-token-with-enough-length"}
    with TestClient(app) as client:
        assert client.get("/gui/api/connections", headers=headers).status_code == 200
        assert client.post("/gui/api/connections/codex_acp/check", headers=headers).status_code == 200
        assert client.get("/gui/api/connections/codex_acp/acp-probe", headers=headers).status_code == 405
        assert calls == []
        response = client.post("/gui/api/connections/codex_acp/acp-probe", headers=headers)
        assert response.status_code == 200
        assert response.json()["acp_probe"]["status"] == "no_response"
    assert calls == [r"C:\synthetic\codex-acp.exe"]


def test_key_notice_quota_and_certificate():
    assert fixture(key=False).check("gemini")["missing_step"] == "key"
    text = str(fixture().check("openai"))
    assert "OPENAI_API_KEY" in text and "SYNTHETIC_SECRET" not in text
    assert fixture().check("openai")["available"] is True
    assert fixture().check("gemini")["reachable"] is None
    agy = fixture(notice=False).check("antigravity_cli")
    assert agy["missing_step"] == "enable" and agy["notice_id"] == "agy-third-party-notice/1"
    for used, reason in ((90, "quota_below_launch_floor"), (95, "quota_below_pause_floor")):
        row = fixture(quota=used).check("antigravity_cli")
        assert row["available"] is True and row["reason"] is None
        assert row["warning"] == {"reason": reason, "remaining_percent": 100 - used,
                                  "resets_at": "2026-12-01T00:00:00Z"}
    zero = fixture(quota=100).check("antigravity_cli")
    assert zero["available"] is False and zero["reason"] == "quota_below_pause_floor"
    assert "warning" not in zero
    tls = fixture(health={"available": False, "error": "CERTIFICATE_VERIFY_FAILED: secret path"}).check("ollama")
    assert tls["reason"] == "tls_certificate" and "secret path" not in str(tls)


def test_limited_connection_keeps_reason_and_excludes_unavailable_suggestions():
    service = fixture(quota=100)
    rows = {row["id"]: row for row in service.list()["connections"]}
    claude = rows["claude_cli"]
    assert claude["reason"] == "quota_paused" and claude["available"] is False
    assert claude["limit"]["code"] == "limit_reached"
    assert claude["limit"]["resets_at"] == "2026-12-01T00:00:00Z"
    assert "claude_cli" not in claude["limit"]["suggestions"]
    assert "codex_cli" not in claude["limit"]["suggestions"]


def test_gui_token_and_cli_parser(tmp_path):
    token = tmp_path / "gui-token"
    token.write_text("synthetic-token-with-enough-length", encoding="utf-8")
    service = fixture()
    app = FastAPI()
    app.state.gui_token_file = token
    router = APIRouter(prefix="/gui/api", dependencies=[Depends(require_token)])
    add_connection_routes(router, SimpleNamespace(connections=service))
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/gui/api/connections").status_code == 401
    headers = {"Authorization": "Bearer synthetic-token-with-enough-length"}
    assert len(client.get("/gui/api/connections", headers=headers).json()["connections"]) == 8
    assert client.post("/gui/api/connections/ollama/check", headers=headers).json()["available"] is True
    acp = client.post("/gui/api/connections/codex_acp/acp-probe", headers=headers).json()
    assert acp["acp_probe"]["status"] == "not_installed"
    args = build_parser().parse_args(["connections", "--check", "ollama"])
    assert args.check == "ollama"


def test_connection_routes_are_mounted_on_real_app_with_fakes(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, dry_run=True)
    services.connections = fixture()
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        assert client.get("/gui/api/connections").status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        assert len(client.get("/gui/api/connections", headers=headers).json()["connections"]) == 8
        assert client.get("/api/v1/connections").json()["connections"][0]["id"] == "gemini"
        headers["Content-Type"] = "application/json"
        assert client.post("/gui/api/connections/ollama/check", headers=headers).json()["available"] is True
        assert client.post("/api/v1/connections/wrong/check").status_code == 404


def test_certificate_failure_is_found_in_the_cause_chain_for_ollama():
    """ARR3-T07: ``_tls_failure`` is ``is_tls_failure``: an SDK error that only wraps the ssl error still counts."""
    import ssl

    def boom():
        try:
            raise ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] bad")
        except ssl.SSLCertVerificationError as cause:
            raise RuntimeError("Connection error.") from cause

    service = fixture()
    service._services.system.adapters["ollama"].health = boom
    row = service.check("ollama")
    assert row["reason"] == "tls_certificate" and "TLS certificate" in row["diagnosis"]
    assert row["tls_trust"] == "certifi" and row["tls_fix"]["trust"] == "system"   # the stub has no local_dir: certifi
    assert fixture(health={"available": False, "error": "timed out"}).check("ollama")["reason"] == "unreachable"
