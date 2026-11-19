# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI2-T06: composer preview stays read-only; executor launches use a separate approval route."""
from __future__ import annotations

from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices


def make_client(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings)
    token = ensure_token(settings.gui_token_file)
    client = TestClient(create_app(services=services), base_url="http://localhost")
    return services, client, {"Authorization": f"Bearer {token}"}


def test_executor_options_are_only_user_enabled_and_have_explicit_capabilities(tmp_path):
    services, client, auth = make_client(tmp_path)
    try:
        response = client.get("/gui/api/composer/executors", headers=auth)
        assert response.status_code == 200
        assert response.json() == {"executors": []}
    finally:
        client.close()
        services.runs.close()


def test_workflow_routing_preview_reports_persisted_selection_without_creating_a_run(tmp_path):
    services, client, auth = make_client(tmp_path)
    try:
        before = client.get("/gui/api/runs?limit=50", headers=auth).json()
        response = client.post("/gui/api/runs/preview", headers=auth, json={"workflow_id": "social_post", "inputs": {}, "execution_mode": "auto"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == "social_post"
        assert isinstance(body["routing"], dict)
        assert body["routing"]["steps"]
        assert all({"step_id", "step_kind", "profile_id", "mode", "level", "model", "effort", "status",
                    "model_options", "effort_options", "budget_scope"} <= set(step)
                   for step in body["routing"]["steps"])
        assert all(isinstance(step["model_options"], list) and isinstance(step["effort_options"], list)
                   for step in body["routing"]["steps"])
        assert client.get("/gui/api/runs?limit=50", headers=auth).json() == before
    finally:
        client.close()
        services.runs.close()


def test_executor_launch_route_rejects_a_request_without_explicit_confirmation(tmp_path):
    services, client, auth = make_client(tmp_path)
    try:
        before = services.tasks.list_groups()
        response = client.post("/gui/api/executors/launch", headers=auth,
                               json={"task": "Do one task", "executor_id": "codex_cli"})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "confirmation_required"
        assert services.tasks.list_groups() == before
    finally:
        client.close()
        services.runs.close()



class FakeExecutorAdapter:
    adapter_id = "fake"
    version = "test"

    def describe(self, config):
        from multiagent.routing.executor_capabilities import descriptor_from_declaration
        return descriptor_from_declaration("fake_exec", config)

    def build_command(self, request): return []
    def build_resume_command(self, request, session_id): return []
    def render_prompt(self, request): return ""
    def parse_output(self, stdout): return None, None, None
    def classify_failure(self, exit_code, stderr): return None
    def open_target(self, session_id): return None
    def launch(self, request): raise AssertionError("preview must not launch")
    def resume(self, request, session_id): raise AssertionError("preview must not resume")


def test_composer_lists_only_enabled_fake_executors_with_declared_choices(tmp_path):
    from multiagent.executors.registry import ExecutorRegistry
    from multiagent.services import ExecutorServices

    def evidence(values):
        return {"status": "supported", "source": "test fixture", "provenance": "test",
                "scope": "fake_exec", "observed_at": "2026-10-13", "values": values}

    services, client, auth = make_client(tmp_path)
    try:
        config = {"version": 1, "executors": {"fake_exec": {
            "surface": "fake", "adapter_id": "fake", "enabled": True,
            "model_support": evidence(["model-test"]),
            "effort_support": evidence(["low", "medium"]),
        }}}
        services.executors = ExecutorServices(services, registry=ExecutorRegistry(config, [FakeExecutorAdapter()]))
        response = client.get("/gui/api/composer/executors", headers=auth)
        assert response.status_code == 200
        executor = response.json()["executors"][0]
        assert executor["executor_id"] == "fake_exec"
        assert executor["available"] is True
        assert executor["models"] == ["model-test"]
        assert executor["efforts"] == ["low", "medium"]
    finally:
        client.close()
        services.runs.close()
