# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""INI-T02: the five steps of «Inicio» and their times, on the server.

``LOCAL_DIR`` is in ``tmp_path``. No real CLI, no network and nobody presses anything: the connection rows come from a
double, the activations from a double of ``active_ids``, and the runs are Mock runs of the example package.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices
from multiagent.services.first_run import (FIRST_RUN_FILE, FIRST_RUN_SCHEMA, START_FILE, STEP_TITLES, STEPS,
                                           FirstRunServices)

ACTOR = "user:test"
NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
CONNECTION_ROW = {"id": "claude_cli", "kind": "executor", "available": True, "reason": None}


def make_settings(tmp_path, *, mock=True) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=mock,
                    gemini_api_key="", openai_api_key="", openai_enabled=False,
                    gui_token_file=tmp_path / "gui-token")


class FakeDetector:
    """The shape of ``setup.detect.detect`` with no app found. It never runs anything."""

    def __call__(self, **kwargs):
        return {"contexts": {"packaged_process": {"state": "measured", "apps": []}}}


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    services = ApplicationServices.create(settings, dry_run=True, adapter_overrides={"ollama": FakeAdapter()})
    services.connections.detector = FakeDetector()
    # The real list() reads the home of whoever runs the tests (session logs, usage files): never in a test.
    services.connections.list = lambda: {"connections": []}
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(services=services, settings=settings, client=client, tmp=tmp_path,
                              headers={"Authorization": f"Bearer {token}"}, first_run=services.first_run)
    services.runs.close()


def install_example(env):
    return env.services.installed.install_shipped("general", actor=ACTOR)


def queue_run(env):
    return env.services.runs.create(RunRequest(workflow_id="ask_once", project_name="Prueba",
                                               inputs={"task": "Explica la idea."}), bundle="general")["id"]


def complete_run(env):
    run_id = queue_run(env)
    assert env.services.runs._bundle_runtime("general").worker.process_once()
    assert env.services.runs.get(run_id)["status"] == "completed"
    return run_id


def done(status):
    return {step["id"]: step["done"] for step in status["steps"]}


def test_fresh_install_all_pending(env):
    status = env.first_run.status()
    assert STEP_TITLES["install"] == "Install an office"
    assert status["schema"] == FIRST_RUN_SCHEMA == "maf.first-run/1"
    assert [step["id"] for step in status["steps"]] == list(STEPS)
    assert [step["title"] for step in status["steps"]] == [STEP_TITLES[step] for step in STEPS]
    assert status["pending"] == list(STEPS) and status["all_done"] is False
    assert status["offer_real"] is False and status["mock"] is True and status["times"] == []
    assert all(step["done_at"] is None for step in status["steps"])
    assert status["install_started_at"]


def test_install_step_from_registry(env):
    assert done(env.first_run.status())["install"] is False
    install_example(env)
    status = env.first_run.status()
    assert done(status)["install"] is True
    step = status["steps"][0]
    assert step["done_at"] and [item["id"] for item in status["times"]] == ["install"]
    assert "install" not in status["pending"]


def test_install_step_from_office_registry(env):
    assert done(env.first_run.status())["install"] is False
    env.services.offices.install_example(actor=ACTOR, channel="test")
    status = env.first_run.status()
    assert done(status)["install"] is True
    assert status["steps"][0]["done_at"]
    assert "install" not in status["pending"]


def test_try_mock_needs_completed_run_with_document(env):
    install_example(env)
    queue_run(env)                                           # queued: no result yet, so it does not count
    status = env.first_run.status()
    assert done(status)["try_mock"] is False and status["offer_real"] is False
    assert env.services.runs._bundle_runtime("general").worker.process_once()
    status = env.first_run.status()
    assert done(status)["try_mock"] is True and status["offer_real"] is True


def test_real_run_step_ignores_runs_from_a_mock_process(env):
    install_example(env)
    complete_run(env)
    status = env.first_run.status()
    assert done(status)["try_mock"] is True and done(status)["real_run"] is False
    assert status["mock"] is True and status["models"] == "mock"


def test_runs_before_process_start_do_not_count(env):
    install_example(env)
    complete_run(env)
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    after_restart = FirstRunServices(env.services, started_at=later)
    status = after_restart.status()
    assert done(status)["try_mock"] is False and status["offer_real"] is False
    earlier = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert done(FirstRunServices(env.services, started_at=earlier).status())["try_mock"] is True


def test_done_step_stays_done(env):
    install_example(env)
    complete_run(env)
    first = env.first_run.status()
    assert done(first)["try_mock"] is True
    saved = json.loads((env.settings.local_dir / FIRST_RUN_FILE).read_text(encoding="utf-8"))
    assert saved["schema"] == FIRST_RUN_SCHEMA and saved["steps"]["try_mock"]["done_at"]
    assert not list(env.settings.local_dir.glob("*.tmp"))
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    again = FirstRunServices(env.services, started_at=later).status()      # no run counts for this process now
    assert done(again)["try_mock"] is True
    assert {item["id"]: item["done_at"] for item in again["times"]}["try_mock"] == \
        {item["id"]: item["done_at"] for item in first["times"]}["try_mock"]


def test_a_broken_file_reads_as_empty(env):
    (env.settings.local_dir / FIRST_RUN_FILE).write_text("{not json", encoding="utf-8")
    assert env.first_run.status()["pending"] == list(STEPS)


def test_connect_ignores_mock_double(env):
    # connections.list() has no row for Mock itself: the stand-in is a provider whose adapter is the FakeAdapter double.
    assert isinstance(env.services.system.adapters["ollama"], FakeAdapter)
    env.services.connections.list = lambda: {"connections": [{"id": "ollama", "kind": "model", "available": True,
                                                                "reason": None}]}
    assert done(env.first_run.status())["connect"] is False
    env.services.connections.list = lambda: {"connections": [
        {"id": "ollama", "kind": "model", "available": True, "reason": None}, dict(CONNECTION_ROW)]}   # + a fake CLI
    assert done(env.first_run.status())["connect"] is True


def test_connect_needs_an_available_row(env):
    env.services.connections.list = lambda: {"connections": [{**CONNECTION_ROW, "available": False,
                                                                "reason": "session_missing"}]}
    assert done(env.first_run.status())["connect"] is False


def test_activate_from_active_ids(env):
    assert done(env.first_run.status())["activate"] is False
    env.services.executor_activations.active_ids = lambda: ["codex_cli"]               # the double of the activation
    assert done(env.first_run.status())["activate"] is True


def test_times_from_launcher_start(env):
    install_example(env)
    folder = env.settings.local_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / START_FILE).write_text((NOW - timedelta(minutes=3)).isoformat(timespec="seconds"), encoding="utf-8-sig")
    first_run = FirstRunServices(env.services, clock=lambda: NOW)
    status = first_run.status()
    assert status["install_started_at"].startswith("2026-11-22T11:57:00")
    assert status["times"][0]["id"] == "install" and status["times"][0]["minutes"] == 3
    text = first_run.times_text()
    assert "Install an office" in text and "3 min" in text and "pending" in text
    assert STEP_TITLES["real_run"] in text


def test_clock_starts_at_first_status_without_launcher_file(env):
    first_run = FirstRunServices(env.services, clock=lambda: NOW)
    assert first_run.status()["install_started_at"].startswith("2026-11-22T12:00:00")
    later = FirstRunServices(env.services, clock=lambda: NOW + timedelta(minutes=9))
    assert later.status()["install_started_at"].startswith("2026-11-22T12:00:00")      # it stays


def test_set_models_route(env):
    response = env.client.post("/gui/api/settings/models", headers=env.headers, json={"models": "real"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"models": "real", "restart_required": True, "text": "Close this window and reopen the Iniciar launcher"}
    assert env.services.gui_settings.value("models") == "real"
    assert env.client.post("/gui/api/settings/models", headers=env.headers, json={"models": "otro"}).status_code == 422
    back = env.client.post("/gui/api/settings/models", headers=env.headers, json={"models": "mock"})
    assert back.json()["models"] == "mock" and env.services.gui_settings.value("models") == "mock"
    assert env.client.post("/gui/api/settings/models", json={"models": "real"}).status_code in (401, 403)


def test_start_route(env):
    install_example(env)
    response = env.client.get("/gui/api/start", headers=env.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == FIRST_RUN_SCHEMA and body["pending"][0] == "try_mock"
    assert "Install an office" in body["times_text"]
    assert env.client.get("/gui/api/start").status_code in (401, 403)


def test_not_in_api_v1(env):
    paths = [getattr(route, "path", "") for route in env.client.app.routes]
    assert not [path for path in paths if path.startswith("/api/v1") and ("first-run" in path or "start" in path)]
    for path in ("/api/v1/start", "/api/v1/first-run", "/api/v1/primer-recorrido"):
        assert env.client.get(path).status_code in (401, 404, 405)


def test_a_run_of_the_same_second_before_the_start_does_not_count(env):
    install_example(env)
    complete_run(env)
    restarted = FirstRunServices(env.services)             # a new process right after: started_at keeps its fractions
    assert done(restarted.status())["try_mock"] is False
