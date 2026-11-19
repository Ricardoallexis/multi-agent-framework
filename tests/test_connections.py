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
from multiagent.services import ApplicationServices
from multiagent.services.connections import ConnectionServices, IDS


def fixture(*, missing=None, signed_out=None, enabled=True, notice=True, quota=0, health=None, key=True):
    missing, signed_out = set(missing or ()), set(signed_out or ())
    def detector(**kwargs):
        assert kwargs["include_antigravity"] is True
        return {"contexts": {"packaged_process": {"apps": [
            {"app": app, "detection": "not_detected" if app in missing else "detected", "version": "1.2.3",
             "session": {"state": "signed_out" if app in signed_out else "signed_in"}}
            for app in ("claude", "codex", "copilot", "agy")]}}}
    def forbidden(*args, **kwargs):
        raise AssertionError("generation was called")
    settings = SimpleNamespace(gemini_api_key="SYNTHETIC_SECRET" if key else "",
                               openai_api_key="SYNTHETIC_SECRET" if key else "", openai_enabled=True,
                               antigravity_notice_accepted="agy-third-party-notice/1" if notice else "")
    executor = SimpleNamespace(status=lambda: {"executors": [
        {"executor_id": identifier, "enabled": enabled, "pause_reason": None}
        for identifier in IDS if identifier.endswith("_cli")]}, run_task=forbidden)
    usage = SimpleNamespace(plans=lambda: {"apps": [
        {"app": app, "status": "ok", "windows": [{"window": "weekly", "used_percent": quota,
                                                    "resets_at": "2026-12-01T00:00:00Z"}]}
        for app in ("claude", "codex", "copilot", "antigravity")]})
    adapter = SimpleNamespace(health=lambda: health or {"available": True, "version": "0.1"}, generate=forbidden,
                              generate_structured=forbidden)
    services = SimpleNamespace(settings=settings, executors=executor, usage=usage,
                               system=SimpleNamespace(adapters={"ollama": adapter}))
    return ConnectionServices(services, detector=detector,
                              clock=lambda: datetime(2026, 11, 18, tzinfo=timezone.utc))


def test_ready_missing_install_session_and_cache():
    service = fixture(missing={"codex"}, signed_out={"claude"})
    rows = {row["id"]: row for row in service.list()["connections"]}
    assert len(rows) == 7 and rows["ollama"]["available"] is True
    assert rows["codex_cli"]["missing_step"] == "install"
    assert rows["claude_cli"]["missing_step"] == "session"
    assert rows["copilot_cli"]["session"] == "unknown"
    assert rows["copilot_cli"]["available"] is False
    assert rows["antigravity_cli"]["session"] == "signed_in"
    assert service.list()["connections"][0]["checked_at"] == "2026-11-18T00:00:00+00:00"


def test_key_notice_quota_and_certificate():
    assert fixture(key=False).check("gemini")["missing_step"] == "key"
    text = str(fixture().check("openai"))
    assert "OPENAI_API_KEY" in text and "SYNTHETIC_SECRET" not in text
    assert fixture().check("openai")["available"] is True
    assert fixture().check("gemini")["reachable"] is None
    agy = fixture(notice=False).check("antigravity_cli")
    assert agy["missing_step"] == "enable" and agy["notice_id"] == "agy-third-party-notice/1"
    assert fixture(quota=90).check("antigravity_cli")["reason"] == "quota_below_launch_floor"
    assert fixture(quota=95).check("antigravity_cli")["reason"] == "quota_below_pause_floor"
    tls = fixture(health={"available": False, "error": "CERTIFICATE_VERIFY_FAILED: secret path"}).check("ollama")
    assert tls["reason"] == "tls_certificate" and "secret path" not in str(tls)


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
    assert len(client.get("/gui/api/connections", headers=headers).json()["connections"]) == 7
    assert client.post("/gui/api/connections/ollama/check", headers=headers).json()["available"] is True
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
        assert len(client.get("/gui/api/connections", headers=headers).json()["connections"]) == 7
        assert client.get("/api/v1/connections").json()["connections"][0]["id"] == "gemini"
        headers["Content-Type"] = "application/json"
        assert client.post("/gui/api/connections/ollama/check", headers=headers).json()["available"] is True
        assert client.post("/api/v1/connections/wrong/check").status_code == 404
