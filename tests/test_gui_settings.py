# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The simple or advanced mode on the server: gui-settings.json, ``mode`` in the session and advanced_mode_required (SIM-T01)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.services.errors import ServiceError
from multiagent.services.gui_settings import GUI_SETTINGS_FILE, GUI_SETTINGS_SCHEMA, GuiSettingsServices, load_file


def make_settings(tmp_path):
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token")


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    services = ApplicationServices.create(settings, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token, settings=settings,
                              auth={"Authorization": f"Bearer {token}"}, file=settings.local_dir / GUI_SETTINGS_FILE)
    services.runs.close()


@pytest.fixture
def service(tmp_path):
    return GuiSettingsServices(SimpleNamespace(settings=make_settings(tmp_path)))


def test_missing_file_reads_simple(service):
    assert service.read()["mode"] == "simple" and service.read()["schema"] == GUI_SETTINGS_SCHEMA
    assert not (service.services.settings.local_dir / GUI_SETTINGS_FILE).exists()
    assert load_file(service.services.settings.local_dir) == {}


def test_corrupt_file_reads_simple(service):
    folder = service.services.settings.local_dir
    folder.mkdir(parents=True)
    (folder / GUI_SETTINGS_FILE).write_text("{no json", encoding="utf-8")
    assert service.mode() == "simple" and load_file(folder) == {}
    assert (folder / GUI_SETTINGS_FILE).read_text(encoding="utf-8") == "{no json"  # not rewritten by a read
    (folder / GUI_SETTINGS_FILE).write_text(json.dumps({"mode": "expert", "color": "x"}), encoding="utf-8")
    assert service.mode() == "simple" and "color" not in service.read()


def test_set_mode_persists_atomically(service):
    folder = service.services.settings.local_dir
    answer = service.set_mode("advanced", actor="user:ana", channel="gui")
    assert answer["mode"] == "advanced" and answer["updated_by"] == "user:ana@gui" and answer["updated_at"]
    other = GuiSettingsServices(service.services)
    assert other.mode() == "advanced" and other.value("mode") == "advanced"
    assert json.loads((folder / GUI_SETTINGS_FILE).read_text(encoding="utf-8"))["schema"] == GUI_SETTINGS_SCHEMA
    assert [path.name for path in folder.iterdir()] == [GUI_SETTINGS_FILE]  # no .tmp left behind
    assert service.set_mode("simple")["mode"] == "simple"


def test_unknown_key_or_value_rejected(service):
    for call in (lambda: service.set_value("color", "x"), lambda: service.set_mode("expert")):
        with pytest.raises(ServiceError) as caught:
            call()
        assert caught.value.code == "invalid_request" and caught.value.status == 422
    assert not (service.services.settings.local_dir / GUI_SETTINGS_FILE).exists()


def test_closed_list_grows_with_one_entry(service, monkeypatch):
    from multiagent.services import gui_settings
    monkeypatch.setitem(gui_settings.ALLOWED_VALUES, "models", ("mock", "real"))
    assert "models" not in service.read()  # an unwritten key does not appear
    service.set_value("models", "real")
    assert service.read()["models"] == "real" and service.value("models") == "real" and service.mode() == "simple"
    assert load_file(service.services.settings.local_dir)["models"] == "real"


def test_session_reports_mode(env):
    assert env.client.get("/gui/api/session", headers=env.auth).json()["mode"] == "simple"
    answer = env.client.post("/gui/api/settings/mode", json={"mode": "advanced"}, headers=env.auth)
    assert answer.status_code == 200 and answer.json()["mode"] == "advanced"
    session = env.client.get("/gui/api/session", headers=env.auth).json()
    assert session["mode"] == "advanced" and set(session) == {"mock", "version", "mode"}
    assert env.client.get("/gui/api/settings", headers=env.auth).json()["mode"] == "advanced"
    bad = env.client.post("/gui/api/settings/mode", json={"mode": "expert"}, headers=env.auth)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_request"


def test_mode_route_needs_token_and_json(env):
    assert env.client.post("/gui/api/settings/mode", json={"mode": "advanced"}).status_code == 401
    assert env.client.get("/gui/api/settings").status_code == 401
    plain = env.client.post("/gui/api/settings/mode", content='{"mode": "advanced"}',
                            headers={**env.auth, "Content-Type": "text/plain"})
    assert plain.status_code == 415
    assert not env.file.exists()


def test_require_advanced_mode(env):
    gui = env.services.gui_settings
    with pytest.raises(ServiceError) as caught:
        gui.require_advanced_mode("install_code")
    assert caught.value.code == "invalid_state" and caught.value.status == 409
    assert caught.value.details == {"reason": "advanced_mode_required", "action": "install_code"}
    gui.set_mode("advanced")
    assert gui.require_advanced_mode("install_code") is None


def test_api_v1_unchanged_by_mode(env):
    v1 = {"Host": "localhost"}
    simple = env.client.get("/api/v1/workflows", headers=v1)
    env.services.gui_settings.set_mode("advanced")
    advanced = env.client.get("/api/v1/workflows", headers=v1)
    assert simple.status_code == advanced.status_code == 200 and simple.json() == advanced.json()
