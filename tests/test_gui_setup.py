# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""INS-T05: «Apps y agentes» (#apps) extension, read-only INS-T03 template status, and INS-T04 console route."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.gui import STATIC_DIR
from multiagent.gui.security import CSP, require_json, require_token
from multiagent.gui.setup_api import add_setup_routes, permission_status
from multiagent.gui.token import ensure_token
from multiagent.services.errors import ServiceError
from multiagent.setup import guided

EXT_APPS = STATIC_DIR / "extensions" / "apps"
NODE = shutil.which("node")


def _build_client(tmp_path: Path, *, allow_launch: bool = True, policy=None, setup_guide=None,
                  setup_launcher=None, setup_detector=None, setup_system: str | None = "Windows",
                  project_root: Path | None = None):
    token_file = tmp_path / "gui-token"
    token_file.write_text("synthetic-token-with-enough-length", encoding="utf-8")
    settings = SimpleNamespace(gui_allow_launch=allow_launch)
    services = SimpleNamespace(
        settings=settings,
        executors=SimpleNamespace(document_policy=policy),
        setup_guide=setup_guide,
        setup_launcher=setup_launcher,
        setup_detector=setup_detector,
        setup_system=setup_system,
        setup_project_root=project_root,
    )
    app = FastAPI()
    app.state.gui_token_file = token_file

    # create_app() answers every ServiceError with its status; this bare app needs the same handler.
    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.to_payload()})

    router = APIRouter(prefix="/gui/api", dependencies=[Depends(require_token), Depends(require_json)])
    add_setup_routes(router, services)
    app.include_router(router)
    headers = {"Authorization": "Bearer synthetic-token-with-enough-length", "Content-Type": "application/json"}
    return TestClient(app), headers


def test_setup_routes_require_token_and_never_accept_commands(tmp_path):
    client, headers = _build_client(tmp_path, setup_guide=lambda *a, **kw: pytest.fail("must not launch"))
    assert client.get("/gui/api/setup/status").status_code == 401
    assert client.post("/gui/api/setup/console", json={"id": "claude_cli", "step": "install"}).status_code == 401

    bad_command = client.post("/gui/api/setup/console", headers=headers,
                              json={"id": "claude_cli", "step": "install", "command": ["whoami"]})
    assert bad_command.status_code == 422

    bad_tool = client.post("/gui/api/setup/console", headers=headers, json={"id": "gemini", "step": "install"})
    assert bad_tool.status_code == 400

    bad_agy_install = client.post("/gui/api/setup/console", headers=headers,
                                  json={"id": "antigravity_cli", "step": "install"})
    assert bad_agy_install.status_code == 400


def test_permission_status_is_read_only_and_reflects_templates(tmp_path):
    (tmp_path / "docs" / "permissions").mkdir(parents=True)
    (tmp_path / "docs" / "permissions" / "claude-settings.template.json").write_text("{}", encoding="utf-8")
    (tmp_path / "docs" / "permissions" / "codex-config.template.toml").write_text("", encoding="utf-8")

    inactive = permission_status(tmp_path)
    assert inactive["claude_cli"]["template_present"] is True
    assert inactive["claude_cli"]["active"] is False
    assert inactive["codex_cli"]["active"] is False
    assert inactive["copilot_cli"]["mode"] == "per_run"
    assert inactive["antigravity_cli"]["mode"] == "supervised_modes"

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".claude" / "settings.local.json").write_text('{"secret": "SYNTHETIC"}', encoding="utf-8")
    active = permission_status(tmp_path)
    assert active["claude_cli"]["active"] is True
    assert active["claude_cli"]["local_present"] is True
    assert "SYNTHETIC" not in json.dumps(active)


class _ControlledConsole:
    """Fake launcher whose console «closes» only when the test says so."""

    def __init__(self, exit_code: int = 0):
        self.exit_code = exit_code
        self.started = threading.Event()
        self.release = threading.Event()
        self.launched = []

    def launcher(self, argv, **kwargs):
        self.launched.append((argv, kwargs))
        outer = self

        class Child:
            def wait(self):
                outer.started.set()
                assert outer.release.wait(timeout=5)
                return outer.exit_code

        return Child()

    @staticmethod
    def detector(**kwargs):
        return {"contexts": {kwargs["context"]: {"state": "measured", "apps": []}}}


@pytest.fixture()
def console(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setattr(guided.launch_safety, "launch_environment", lambda: {"SAFE": "1"})
    monkeypatch.setattr(guided, "_console_state", None)
    fake = _ControlledConsole()
    yield fake
    fake.release.set()  # never leave the waiting thread alive
    thread = guided._console_thread
    if thread is not None:
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_console_post_answers_202_open_then_get_reports_closed_with_recheck(tmp_path, console):
    client, headers = _build_client(
        tmp_path, policy=object(),  # proves active document_policy receives the confirmed GUI approval
        setup_launcher=console.launcher, setup_detector=console.detector, setup_system="Windows",
    )
    assert client.get("/gui/api/setup/console").status_code == 401
    assert client.get("/gui/api/setup/console", headers=headers).json() == {"state": "none"}

    resp = client.post("/gui/api/setup/console", headers=headers, json={"id": "codex_cli", "step": "login"})
    # The console is still open (release not set): the request did not wait for it.
    assert resp.status_code == 202
    body = resp.json()
    assert body["ok"] is True and body["state"] == "open" and body["console_id"]
    assert body["app"] == "codex" and body["step"] == "login"
    assert body["plan"]["command"] == ["codex", "login"]
    assert body["console_exit_code"] is None and body["recheck"] is None
    assert console.started.wait(timeout=5)

    opened = client.get("/gui/api/setup/console", headers=headers).json()
    assert opened["state"] == "open" and opened["console_id"] == body["console_id"]

    second = client.post("/gui/api/setup/console", headers=headers, json={"id": "claude_cli", "step": "install"})
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "console_already_open"

    console.exit_code = 3
    console.release.set()
    guided._console_thread.join(timeout=5)
    closed = client.get("/gui/api/setup/console", headers=headers).json()
    assert closed["state"] == "closed" and closed["console_id"] == body["console_id"]
    assert closed["console_exit_code"] == 3
    assert closed["recheck"]["contexts"]["packaged_process"]["state"] == "measured"

    assert len(console.launched) == 1
    assert console.launched[0][0][1:] == ["-m", "multiagent.setup.guided", "--console", "codex", "login"]
    assert console.launched[0][1]["creationflags"] == guided.CREATE_NEW_CONSOLE
    evidence = list((tmp_path / "local" / "setup-evidence").glob("*.jsonl"))
    assert len(evidence) == 1
    records = [json.loads(line) for line in evidence[0].read_text(encoding="utf-8").splitlines()]
    assert records[0]["before_action"]["command"] == ["codex", "login"]
    assert records[1]["console_exit_code"] == 3

    # The lock is free again once the waiting thread ends.
    again = client.post("/gui/api/setup/console", headers=headers, json={"id": "codex_cli", "step": "login"})
    assert again.status_code == 202


def test_console_launch_failure_answers_409_and_get_says_failed(tmp_path, console):
    def broken(argv, **kwargs):
        raise OSError("synthetic launch failure")

    client, headers = _build_client(tmp_path, setup_launcher=broken, setup_detector=console.detector,
                                    setup_system="Windows")
    resp = client.post("/gui/api/setup/console", headers=headers, json={"id": "codex_cli", "step": "login"})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "launch_failed"
    state = client.get("/gui/api/setup/console", headers=headers).json()
    assert state["state"] == "failed" and state["reason"] == "launch_failed"
    assert guided._console_lock.acquire(blocking=False)  # a failed launch must not keep the lock
    guided._console_lock.release()


def test_console_recheck_failure_reports_failed_and_frees_the_lock(tmp_path, console):
    def broken_detector(**kwargs):
        raise RuntimeError("synthetic detector failure")

    client, headers = _build_client(tmp_path, setup_launcher=console.launcher, setup_detector=broken_detector,
                                    setup_system="Windows")
    resp = client.post("/gui/api/setup/console", headers=headers, json={"id": "codex_cli", "step": "login"})
    assert resp.status_code == 202
    console.release.set()
    guided._console_thread.join(timeout=5)
    state = client.get("/gui/api/setup/console", headers=headers).json()
    assert state["state"] == "failed" and state["reason"] == "RuntimeError"
    assert guided._console_lock.acquire(blocking=False)
    guided._console_lock.release()


def test_console_route_respects_allow_launch_and_non_windows_and_agy_info(tmp_path):
    disabled_client, headers = _build_client(
        tmp_path, allow_launch=False, setup_guide=lambda *a, **kw: pytest.fail("must not launch"),
    )
    assert disabled_client.get("/gui/api/setup/status", headers=headers).json()["allow_launch"] is False
    denied = disabled_client.post("/gui/api/setup/console", headers=headers,
                                  json={"id": "claude_cli", "step": "install"})
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "launch_disabled"

    linux_client, headers = _build_client(
        tmp_path, setup_launcher=lambda *a, **kw: pytest.fail("must not open console"), setup_system="Linux",
    )
    assert linux_client.get("/gui/api/setup/status", headers=headers).json()["console_supported"] is False
    unsupported = linux_client.post("/gui/api/setup/console", headers=headers,
                                    json={"id": "copilot_cli", "step": "install"})
    assert unsupported.status_code == 409
    assert unsupported.json()["detail"]["code"] == "launch_unsupported"

    agy = linux_client.post("/gui/api/setup/console", headers=headers,
                            json={"id": "antigravity_cli", "step": "login"})
    assert agy.status_code == 200  # nothing to open, so nothing to wait for
    assert "Inicia sesión tú en agy" in agy.json()["plan"]["message"]


def test_apps_extension_is_discovered_and_served_on_real_app(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gui_token_file=tmp_path / "gui-token", gui_heartbeat_seconds=0.05, gui_poll_seconds=0.02,
                        gui_stream_max_seconds=0.4)
    database = Database(settings)
    database.migrate()
    token = ensure_token(settings.gui_token_file)
    services = SimpleNamespace(
        system=SimpleNamespace(settings=settings, worker=Mock(), store=Store(database)),
        runs=Mock(),
        definitions=Mock(),
    )
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        auth = {"Authorization": f"Bearer {token}"}
        manifest = client.get("/gui/api/extensions", headers=auth).json()["extensions"]
        entry = next(item for item in manifest if item["id"] == "apps")
        assert entry["script"] == "/gui/static/extensions/apps/index.js"
        assert entry["style"] == "/gui/static/extensions/apps/style.css"

        for rel in ("index.js", "model.js", "help.js", "style.css"):
            resp = client.get(f"/gui/static/extensions/apps/{rel}")
            assert resp.status_code == 200
            assert resp.headers["content-security-policy"] == CSP

        status_resp = client.get("/gui/api/setup/status", headers=auth)
        assert status_resp.status_code == 200
        assert "permissions" in status_resp.json() and "plans" in status_resp.json()


def _run_node(script: str, env_data: dict | None = None) -> dict:
    env = {**os.environ}
    if env_data is not None:
        env["DATA"] = json.dumps(env_data)
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, env=env)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_apps_pure_model_shows_only_the_missing_step_and_never_uses_network():
    data = {
        "connections": [
            {"id": "claude_cli", "kind": "executor", "installed": False, "version": None, "session": "unknown",
             "enabled": False, "available": False, "reason": "not_installed", "missing_step": "install",
             "checked_at": "2026-11-19T01:00:00Z"},
            {"id": "codex_cli", "kind": "executor", "installed": True, "version": "1.0.0", "session": "signed_out",
             "enabled": True, "available": False, "reason": "session_missing", "missing_step": "session",
             "checked_at": "2026-11-19T01:00:00Z"},
            {"id": "copilot_cli", "kind": "executor", "installed": True, "version": "0.2.0", "session": "unknown",
             "enabled": False, "available": False, "reason": "disabled", "missing_step": "enable",
             "checked_at": "2026-11-19T01:00:00Z"},
            {"id": "antigravity_cli", "kind": "executor", "installed": True, "version": "1.5.10", "session": "unknown",
             "enabled": False, "notice_accepted": False, "available": False, "reason": "notice_not_accepted",
             "missing_step": "enable", "checked_at": "2026-11-19T01:00:00Z"},
            {"id": "gemini", "kind": "model", "key_configured": False, "key_setting": "GEMINI_API_KEY",
             "key_file": ".local/.env", "enabled": True, "available": False, "reason": "key_missing",
             "missing_step": "key", "checked_at": "2026-11-19T01:00:00Z"},
            {"id": "openai", "kind": "model", "key_configured": True, "key_setting": "OPENAI_API_KEY",
             "key_file": ".local/.env", "enabled": False, "available": False, "reason": "disabled",
             "missing_step": "enable", "checked_at": "2026-11-19T01:00:00Z"},
            {"id": "ollama", "kind": "model", "reachable": True, "version": "0.5.0", "enabled": True,
             "available": True, "reason": None, "missing_step": None, "checked_at": "2026-11-19T01:00:00Z"},
        ],
        "setupStatus": {
            "allow_launch": True,
            "console_supported": True,
            "permissions": {
                "claude_cli": {"mode": "template", "template_path": "docs/permissions/claude-settings.template.json",
                               "active_path": ".claude/settings.json", "active": False, "guide": "docs/PERMISSIONS.md"},
                "codex_cli": {"mode": "template", "template_path": "docs/permissions/codex-config.template.toml",
                              "active_path": ".codex/config.toml", "active": True, "guide": "docs/PERMISSIONS.md"},
                "copilot_cli": {"mode": "per_run", "guide": "docs/PERMISSIONS.md"},
                "antigravity_cli": {"mode": "supervised_modes", "guide": "docs/EXECUTORS.md"},
            },
            "plans": {
                "claude_cli": {"install": {"command": ["winget", "install", "Anthropic.ClaudeCode"],
                                           "source": "https://example.invalid", "source_date": "2026-11-19"}},
                "codex_cli": {"login": {"command": ["codex", "login"],
                                        "source": "https://example.invalid", "source_date": "2026-11-19"}},
            },
        },
    }
    script = f"""
      import {{ buildAppsView }} from {json.dumps((EXT_APPS / "model.js").as_uri())};
      globalThis.fetch = () => {{ throw new Error("pure model must not use network"); }};
      const payload = JSON.parse(process.env.DATA);
      console.log(JSON.stringify(buildAppsView(payload, payload.setupStatus)));
    """
    view = _run_node(script, data)
    execs = {item["id"]: item for item in view["executors"]}
    models = {item["id"]: item for item in view["models"]}

    assert list(execs) == ["claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"]
    assert list(models) == ["gemini", "openai", "ollama"]

    assert execs["claude_cli"]["action"]["kind"] == "console"
    assert execs["claude_cli"]["action"]["step"] == "install"
    assert execs["claude_cli"]["action"]["commandText"] == "winget install Anthropic.ClaudeCode"
    assert "Inactiva" in execs["claude_cli"]["permissions"]

    assert execs["codex_cli"]["action"]["kind"] == "console"
    assert execs["codex_cli"]["action"]["step"] == "login"
    assert "Activa en .codex/config.toml" in execs["codex_cli"]["permissions"]

    assert execs["copilot_cli"]["action"]["kind"] == "enable_executor"
    assert "enabled: true" in execs["copilot_cli"]["action"]["catalogLines"]
    assert execs["copilot_cli"]["action"]["envLines"] is None

    assert execs["antigravity_cli"]["action"]["kind"] == "enable_executor"
    assert "enabled: true" in execs["antigravity_cli"]["action"]["catalogLines"]
    assert execs["antigravity_cli"]["action"]["envLines"] == "ANTIGRAVITY_NOTICE_ACCEPTED=agy-third-party-notice/1"

    assert models["gemini"]["action"] == {
        "kind": "key",
        "title": "Falta configurar la clave",
        "setting": "GEMINI_API_KEY",
        "file": ".local/.env",
        "detail": "Añade GEMINI_API_KEY en .local/.env, reinicia la aplicación y pulsa «Volver a comprobar». Nunca escribas la clave aquí.",
    }
    assert models["openai"]["action"]["kind"] == "enable_model"
    assert models["openai"]["action"]["line"] == "OPENAI_ENABLED=true"
    assert models["ollama"]["action"] is None and models["ollama"]["available"] is True

    # GUI3-T08: pending-step rows start open; ready rows start folded with status in words.
    assert execs["claude_cli"]["defaultOpen"] is True and execs["claude_cli"]["missingLine"] == "Falta instalarlo"
    assert execs["codex_cli"]["defaultOpen"] is True and execs["codex_cli"]["missingLine"] == "Falta iniciar sesión"
    assert models["gemini"]["defaultOpen"] is True and models["gemini"]["missingLine"] == "Falta configurar la clave"
    assert models["ollama"]["defaultOpen"] is False and models["ollama"]["missingLine"] is None
    assert models["ollama"]["chip"]["label"] == "Listo"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_apps_pure_model_handles_agy_uninstalled_and_ollama_tls():
    data = {
        "connections": [
            {"id": "antigravity_cli", "kind": "executor", "installed": False, "missing_step": "install",
             "available": False, "reason": "not_installed"},
            {"id": "ollama", "kind": "model", "reachable": False, "available": False, "reason": "tls_certificate",
             "diagnosis": "The TLS certificate could not be verified; check the local trust chain."},
        ],
    }
    script = f"""
      import {{ buildAppsView }} from {json.dumps((EXT_APPS / "model.js").as_uri())};
      const payload = JSON.parse(process.env.DATA);
      console.log(JSON.stringify(buildAppsView(payload, null)));
    """
    view = _run_node(script, data)
    agy = view["executors"][0]
    ollama = view["models"][0]
    assert agy["action"]["kind"] == "info"
    assert ollama["action"]["kind"] == "info"
    assert "No se pudo verificar el certificado TLS" in ollama["action"]["detail"]
    assert "TLS certificate" not in ollama["action"]["detail"]


def test_apps_extension_has_no_key_input_and_meets_rwd_rules():
    # «Conectar otro» lives in the core picker (new-session.js): shared code may not import an extension.
    for js_file in EXT_APPS.glob("*.js"):
        src = js_file.read_text(encoding="utf-8")
        assert not re.search(r"<(?:input|textarea)\b", src), f"{js_file.name} must never render an input/textarea for keys"
    css = (EXT_APPS / "style.css").read_text(encoding="utf-8")
    assert ".apps-view" in css and "min-width: 0" in css
    assert "@media (max-width: 899.98px)" in css
    assert "@media (max-width: 599.98px)" in css


def test_new_session_picker_links_to_apps_only_when_that_view_is_loaded():
    # «Conectar otro» (INS-T05) sits under the picker of «Nueva sesión». Shared code may not import an extension, so the
    # core draws the link itself and only while the «apps» view is registered; it only navigates.
    source = (STATIC_DIR / "js" / "new-session.js").read_text(encoding="utf-8")
    assert 'import { isRegisteredView } from "./extensions.js";' in source
    block = re.search(r'\$\{isRegisteredView\("apps"\) && html`(.*?)`\}', source, re.S)
    assert block, "the link must depend on the apps view being registered"
    assert 'href=${routeHref("apps")}' in block.group(1) and "Conectar otro" in block.group(1)
    assert 'helpFor("chat.connect_other")' in block.group(1) and "apiPost" not in block.group(1)
    assert source.index("<${ModelPicker}") < source.index('isRegisteredView("apps")')


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_apps_pure_model_covers_copilot_cache_gemini_states_and_agy_warning():
    data = {
        "connections": [
            {"id": "copilot_cli", "kind": "executor", "installed": True, "version": "0.2.0",
             "session": "signed_in", "verification": "verified", "enabled": True, "available": True,
             "quota": {"status": "ok", "windows": [
                 {"window": "monthly", "group": "Copilot", "used_percent": 35, "resets_at": "2026-12-01T00:00:00Z"},
             ]}},
            {"id": "claude_cli", "kind": "executor", "installed": True, "version": "1.0.0",
             "session": "signed_in", "enabled": True, "available": False, "reason": "quota_paused",
             "limit": {"code": "limit_reached", "resets_at": "2026-11-20T16:45:00Z", "target": "claude_cli", "suggestions": []}},
            {"id": "antigravity_cli", "kind": "executor", "installed": True, "version": "1.5.10",
             "session": "signed_in", "enabled": True, "notice_accepted": True, "available": True,
             "warning": {"reason": "quota_below_launch_floor", "remaining_percent": 12, "resets_at": "2026-11-21T08:00:00Z"}},
            {"id": "gemini", "kind": "model", "key_configured": True, "key_setting": "GEMINI_API_KEY",
             "key_file": ".local/.env", "enabled": True, "available": True, "verification": "unverified"},
        ],
    }
    script = f"""
      import {{ buildAppsView }} from {json.dumps((EXT_APPS / "model.js").as_uri())};
      const payload = JSON.parse(process.env.DATA);
      const first = buildAppsView(payload, null);
      const verified = buildAppsView({{ connections: [{{ ...payload.connections[3], verification: "verified" }}] }}, null);
      const keyInvalid = buildAppsView({{ connections: [{{ ...payload.connections[3], available: false, reason: "key_invalid" }}] }}, null);
      const noAccess = buildAppsView({{ connections: [{{ ...payload.connections[3], available: false, reason: "no_access" }}] }}, null);
      const limited = buildAppsView({{ connections: [{{ ...payload.connections[3], available: false, reason: "limit_reached", resets_at: null }}] }}, null);
      console.log(JSON.stringify({{
        first,
        verified: verified.models[0],
        keyInvalid: keyInvalid.models[0],
        noAccess: noAccess.models[0],
        limited: limited.models[0],
      }}));
    """
    env = {**os.environ, "DATA": json.dumps(data), "TZ": "UTC"}
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, env=env)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)

    execs = {item["id"]: item for item in out["first"]["executors"]}
    assert execs["copilot_cli"]["chip"]["label"] == "Listo"
    assert execs["copilot_cli"]["session"] == "Iniciada"
    assert "35 % usado" in execs["copilot_cli"]["quota"][0]

    assert execs["claude_cli"]["chip"]["label"] == "Límite alcanzado"
    assert execs["claude_cli"]["reasonText"] == "Límite alcanzado · vuelve a las 16:45"
    assert "16:45" in execs["claude_cli"]["action"]["detail"]

    assert execs["antigravity_cli"]["available"] is True
    assert execs["antigravity_cli"]["warning"] == "Quedan 12 %; bajo el piso del 15 %"
    assert execs["antigravity_cli"]["action"] is None

    gem_unverified = out["first"]["models"][0]
    assert gem_unverified["chip"]["label"] == "Sin comprobar"
    assert gem_unverified["available"] is True
    assert gem_unverified["quota"] == ["La API de Gemini no informa la cuota."]

    assert out["verified"]["chip"]["label"] == "Conectado"
    assert out["keyInvalid"]["chip"]["label"] == "Clave inválida"
    assert out["keyInvalid"]["action"]["kind"] == "key"
    assert out["noAccess"]["chip"]["label"] == "Sin acceso"
    assert out["limited"]["chip"]["label"] == "Límite alcanzado"
    assert out["limited"]["reasonText"] == "Límite alcanzado · sin hora conocida"

    for name in ("model.js", "index.js"):
        checked = subprocess.run([NODE, "--check", str(EXT_APPS / name)], capture_output=True, text=True, timeout=60)
        assert checked.returncode == 0, (name, checked.stderr)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_apps_card_shows_the_certificate_button_only_with_tls_fix():
    """ARR3-T07: the server decides (``tls_fix``); the model only reads it, and the view draws the button in either mode."""
    fix = {"trust": "system", "label": "Usar los certificados de Windows"}
    revert = {"trust": "certifi", "label": "Volver a los certificados de siempre"}
    data = {
        "connections": [
            {"id": "openai", "kind": "model", "key_configured": True, "available": False, "reason": "tls_certificate",
             "tls_trust": "certifi", "tls_fix": fix, "tls_revert": None},
            {"id": "gemini", "kind": "model", "key_configured": True, "available": False, "reason": "tls_certificate",
             "tls_trust": "system", "tls_fix": None, "tls_revert": revert},
            {"id": "ollama", "kind": "model", "reachable": True, "available": True, "reason": None,
             "tls_trust": "certifi", "tls_fix": None, "tls_revert": None},
            {"id": "codex_cli", "kind": "executor", "installed": True, "available": True},
        ],
    }
    script = f"""
      import {{ buildAppsView }} from {json.dumps((EXT_APPS / "model.js").as_uri())};
      console.log(JSON.stringify(buildAppsView(JSON.parse(process.env.DATA), null)));
    """
    view = _run_node(script, data)
    by_id = {item["id"]: item for item in view["models"] + view["executors"]}
    assert by_id["openai"]["tlsFix"] == fix and by_id["openai"]["tlsRevert"] is None
    assert by_id["gemini"]["tlsFix"] is None and by_id["gemini"]["tlsRevert"] == revert
    assert by_id["ollama"]["tlsFix"] is None and by_id["ollama"]["tlsRevert"] is None
    assert by_id["codex_cli"]["tlsFix"] is None and by_id["codex_cli"]["canProbe"] is False
    assert by_id["openai"]["canProbe"] is True and by_id["ollama"]["canProbe"] is True
    assert "certificado" in by_id["openai"]["action"]["detail"] and ".pem" in by_id["openai"]["action"]["detail"]
    source = (EXT_APPS / "index.js").read_text(encoding="utf-8")
    assert "item.tlsFix.label" in source and "/tls-trust" in source and "/probe" in source
    assert 'helpFor("apps.tls_fix"' in source and 'helpFor("apps.probe"' in source
