# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""MVP-T01: install the example office only on request, and only from a source checkout."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import multiagent.services.offices as office_services
from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

PATH = "/gui/api/offices"


@pytest.fixture
def gui(tmp_path):
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
        gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(settings, dry_run=True, adapter_overrides={"ollama": FakeAdapter()})
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        yield SimpleNamespace(client=client, services=services, auth={"Authorization": f"Bearer {token}"})
    services.bundle_scans.close()
    services.runs.close()


def test_install_example_copies_the_cafeteria(gui):
    assert gui.services.offices.example_available() is True
    result = gui.services.offices.install_example()
    assert result["office"]["id"] == "cafeteria" and result["office"]["state"] == "ok"
    assert {area["id"] for area in result["office"]["areas"]} == {"menu", "compras", "avisos"}
    assert all(area["package_state"] == "ok" for area in result["office"]["areas"])

    bundles = {item["name"]: item for item in gui.services.installed.list()}
    assert set(bundles) == {"cafeteria__menu", "cafeteria__compras", "cafeteria__avisos"}
    assert all(item["state"] == "ok" and item["source"] == "office" and item["office"] == "cafeteria"
               for item in bundles.values())
    assert {item["bundle"]: item["status"] for item in result["teams"]} == {
        "cafeteria__menu": "proposed",
        "cafeteria__compras": "proposed",
        "cafeteria__avisos": "proposed",
    }
    assert all(gui.services.teams.approved(name) is None for name in bundles)
    source = (office_services.PROJECT_ROOT / office_services.SHIPPED_OFFICES["cafeteria"]).resolve()
    assert all(gui.services._bundles[name].resolve() != source / "areas" / name.split("__", 1)[1]
               for name in bundles)


def test_example_only_in_a_checkout(gui, monkeypatch):
    monkeypatch.setattr(office_services, "IN_CHECKOUT", False)
    assert gui.services.offices.example_available() is False
    listing = gui.client.get(PATH, headers=gui.auth)
    assert listing.status_code == 200 and listing.json()["example_available"] is False
    response = gui.client.post(PATH + "/example", json={}, headers=gui.auth)
    assert (response.status_code, response.json()["detail"]["details"]["reason"]) == (409, "example_not_available")
    assert gui.services.offices.list() == [] and gui.services.installed.list() == []


def test_example_twice_reports_existing_office(gui):
    created = gui.client.post(PATH + "/example", json={}, headers=gui.auth)
    assert created.status_code == 201
    assert (created.json()["office"]["id"], created.json()["office"]["installed_by"],
            created.json()["office"]["channel"]) == ("cafeteria", "user:gui", "gui")
    repeated = gui.client.post(PATH + "/example", json={}, headers=gui.auth)
    assert (repeated.status_code, repeated.json()["detail"]["details"]["reason"]) == (409, "office_exists")
    listing = gui.client.get(PATH, headers=gui.auth)
    assert listing.status_code == 200
    assert listing.json()["example_available"] is False
    assert [office["id"] for office in listing.json()["offices"]] == ["cafeteria"]
