# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""One-use GUI approval protocol with a synthetic secret and controlled clock."""
from __future__ import annotations

import hashlib
import contextlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent import cli
from multiagent.config import Settings
from multiagent.contracts import SocialPostRequest
from multiagent.gui.security import APPROVAL_COOKIE, APPROVAL_COOKIE_PATH
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices, ServiceError

SECRET = "synthetic-private-key-DO-NOT-SEND-927361"
DIGEST = hashlib.sha256(SECRET.encode()).hexdigest()
DESTINATION = {"provider": "synthetic", "model": "fake"}


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    clock = [1000.0]
    app.state.approval_sessions.clock = lambda: clock[0]
    with TestClient(app, base_url="http://localhost") as client:
        yield services, client, {"Authorization": f"Bearer {token}"}, clock
    services.runs.close()


def request(services):
    return services.user_approvals.request(kind="secret_send", destination=DESTINATION,
                                          location={"run": "synthetic-run", "step": "create"},
                                          secret_types=["private_key"], masked_sample="synthetic…927361",
                                          sha256=DIGEST)


def post(client, headers, path, body):
    return client.post("/gui/api/approvals" + path, headers=headers, json=body)


def session(client, headers):
    result = post(client, headers, "/session", {"code": "synthetic-start-code"})
    assert result.status_code == 200, result.text
    return result


def test_session_cookie_single_exchange_and_restart(env):
    services, client, headers, _ = env
    first = session(client, headers)
    cookie = first.headers["set-cookie"]
    assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
    assert "path=" + APPROVAL_COOKIE_PATH in cookie.lower()
    again = post(client, headers, "/session", {"code": "synthetic-start-code"})
    assert again.status_code == 409 and "reinicia la GUI" in again.text
    approval = request(services)
    replacement = create_app(services=services)
    replacement.state.approval_sessions.bootstrap_code = "another-start-code"
    with TestClient(replacement, base_url="http://localhost") as restarted:
        restarted.cookies.set(APPROVAL_COOKIE, client.cookies.get(APPROVAL_COOKIE), path=APPROVAL_COOKIE_PATH)
        result = post(restarted, headers, f"/{approval['id']}/challenge", {})
        assert result.status_code == 409
        assert result.json()["detail"]["details"]["reason"] == "user_click_required"


def test_decision_needs_token_cookie_and_fresh_matching_code(env):
    services, client, headers, clock = env
    a = request(services)
    b = request(services)
    assert client.get("/gui/api/approvals", headers=headers).json()["items"][0]["id"] == a["id"]
    session(client, headers)
    path = f"/{a['id']}/decision"
    missing_code = post(client, headers, path, {"decision": "approve"})
    assert missing_code.status_code == 409
    assert missing_code.json()["detail"]["details"]["reason"] == "user_click_required"
    without_token = post(client, {}, path, {"decision": "approve", "code": "x"})
    assert without_token.status_code == 401
    code = post(client, headers, f"/{a['id']}/challenge", {}).json()["code"]
    client.cookies.clear()
    without_cookie = post(client, headers, path, {"decision": "approve", "code": code})
    assert without_cookie.status_code == 409
    assert without_cookie.json()["detail"]["details"]["reason"] == "user_click_required"
    session_cookie = env[1].app.state.approval_sessions._session
    client.cookies.set(APPROVAL_COOKIE, session_cookie, path=APPROVAL_COOKIE_PATH)
    with pytest.raises(HTTPException) as other_digest:
        client.app.state.approval_sessions.verify(a["id"], "0" * 64, session_cookie, code)
    assert other_digest.value.detail["details"]["reason"] == "user_click_required"
    wrong_id = post(client, headers, f"/{b['id']}/decision", {"decision": "approve", "code": code})
    assert wrong_id.status_code == 409
    clock[0] += 120
    expired = post(client, headers, path, {"decision": "approve", "code": code})
    assert expired.status_code == 409
    assert services.user_approvals.status(a["id"])["state"] == "pending"
    fresh = post(client, headers, f"/{a['id']}/challenge", {}).json()["code"]
    decided = post(client, headers, path, {"decision": "approve", "code": fresh})
    assert decided.status_code == 200 and decided.json()["state"] == "approved"
    assert post(client, headers, path, {"decision": "approve", "code": fresh}).status_code == 409
    assert not services.user_approvals.consume(a["id"], sha256="0" * 64, destination=DESTINATION)
    assert not services.user_approvals.consume(a["id"], sha256=DIGEST, destination="other")
    assert services.user_approvals.consume(a["id"], sha256=DIGEST, destination=DESTINATION)
    assert not services.user_approvals.consume(a["id"], sha256=DIGEST, destination=DESTINATION)
    assert services.user_approvals.status(a["id"])["state"] == "consumed"


def test_rejection_and_events_never_store_secret(env):
    services, client, headers, _ = env
    approval = request(services)
    session(client, headers)
    code = post(client, headers, f"/{approval['id']}/challenge", {}).json()["code"]
    result = post(client, headers, f"/{approval['id']}/decision", {"decision": "reject", "code": code})
    assert result.status_code == 200 and result.json()["state"] == "rejected"
    assert not services.user_approvals.consume(approval["id"], sha256=DIGEST, destination=DESTINATION)
    events = services.tasks.store.events(limit=500)
    assert SECRET not in str(events)
    assert {e["type"] for e in events if e["ref"] == approval["id"]} == {
        "user_approval.requested", "user_approval.decided"}


def test_bundle_load_service_requires_ack_without_writing_event(env):
    services, _, _, _ = env
    approval = services.user_approvals.request(
        kind="bundle_load", destination={"bundle": "synthetic", "kind": "bundle_load"},
        location={"bundle": "synthetic"}, sha256=DIGEST)
    with pytest.raises(ServiceError) as error:
        services.user_approvals.decide(approval["id"], "approve")
    assert (error.value.status, error.value.code) == (422, "risk_ack_required")
    assert services.user_approvals.status(approval["id"])["state"] == "pending"
    assert [e["type"] for e in services.tasks.store.events(limit=500) if e["ref"] == approval["id"]] == [
        "user_approval.requested"]
    decided = services.user_approvals.decide(approval["id"], "approve", risk_ack=True)
    assert decided["risk_ack"] is True
    assert services.user_approvals.status(approval["id"])["risk_ack"] is True


def test_osv_query_does_not_need_ack(env):
    services, _, _, _ = env
    approval = services.user_approvals.request(
        kind="osv_query", destination={"bundle": "synthetic", "kind": "osv_query"},
        location="synthetic manifests", sha256=DIGEST)
    assert services.user_approvals.decide(approval["id"], "approve")["state"] == "approved"


def test_run_approval_channels_require_click_but_rejection_works(env, monkeypatch):
    services, client, headers, _ = env
    created = services.system.run_service.create_run(SocialPostRequest(
        project_name="Synthetic", objective="Synthetic approval", topic="Synthetic topic",
        use_brand_context=False, pipeline_mode="full"))
    run_id = created["id"]
    services.system.store.update_run(run_id, status="waiting_human", waiting_reason="secret_review",
                                     waiting_step="create", waiting_attempt=1)
    api = client.post(f"/api/v1/runs/{run_id}/approve")
    gui = client.post(f"/gui/api/runs/{run_id}/approve", headers=headers, json={})
    for result in (api, gui):
        assert result.status_code == 409, result.text
        assert result.json()["detail"]["details"]["reason"] == "user_click_required"
        assert "Aprobaciones" in result.json()["detail"]["details"]["where"]
    monkeypatch.setattr(cli, "api_client", lambda: contextlib.nullcontext(client))
    monkeypatch.setattr(cli, "get_settings", lambda: services.settings)
    with pytest.raises(RuntimeError, match="user_click_required"):
        cli.cmd_approve(SimpleNamespace(run_id=run_id))
    with pytest.raises(RuntimeError, match="user_click_required"):
        cli._approve_with_token(run_id)
    assert services.system.store.get_run(run_id)["status"] == "waiting_human"
    rejected = client.post(f"/api/v1/runs/{run_id}/reject", json={"feedback": "No permitir"})
    assert rejected.status_code == 200, rejected.text
    assert services.system.store.get_run(run_id)["status"] == "rejected"
