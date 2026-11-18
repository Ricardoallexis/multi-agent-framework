# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""HIL2-T02: external output in the GUI (preview, confirmation, receipt) and its static checks."""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.external_output import sha256
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices
from test_gui_runs import CONTENT, POST_INPUTS


@pytest.fixture(params=["native", "mock"])
def env(tmp_path, request):
    settings = Settings(_env_file=None, local_dir=tmp_path / request.param, execution_backend=request.param,
                        worker_enabled=False, mock_mode=True, gemini_api_key="", openai_api_key="",
                        openai_enabled=False, gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def post(env, path, body):
    return env.client.post("/gui/api" + path, json=body, headers={"Authorization": f"Bearer {env.token}"})


def new_run(env):
    return env.services.runs.create(RunRequest(project_name="GUI external", workflow_id="social_post",
                                               inputs=POST_INPUTS, execution_mode="human_guided"))["id"]


def waiting_run(env):
    run_id = new_run(env)
    assert env.services.system.worker.process_once()
    assert env.services.runs.get(run_id)["waiting_reason"] == "execute_step"
    return run_id


def evidence(name="trace.txt", content=b"fixture log\n"):
    return [{"name": name, "content_base64": base64.b64encode(content).decode("ascii")}]


def test_preview_changes_nothing_and_confirm_returns_the_receipt(env):
    run_id = waiting_run(env)
    before = env.services.runs.get(run_id)
    preview = post(env, f"/runs/{run_id}/external-output/preview", {"output": CONTENT, "evidence": evidence()})
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["output"] == CONTENT and data["files"][0]["sha256"] == sha256(b"fixture log\n")
    assert data["files"][0]["name"] == "trace.txt" and data["expires_at"]
    after = env.services.runs.get(run_id)
    assert (after["status"], after["events"], after["artifacts"]) == (
        before["status"], before["events"], before["artifacts"])
    confirmed = post(env, f"/runs/{run_id}/external-output/confirm", {"preview_id": data["id"]})
    assert confirmed.status_code == 200, confirmed.text
    view = confirmed.json()
    assert view["id"] == run_id and view["waiting"]["reason"] == "review"
    receipt = view["receipt"]
    assert receipt["actor"] == "user:operator" and receipt["channel"] == "gui" and receipt["preview_id"] == data["id"]
    assert receipt["file_sha256"][data["files"][0]["file"]] == sha256(b"fixture log\n")
    stored = next(a for a in env.services.runs.get(run_id)["artifacts"] if a["kind"] == "external_receipt")
    assert stored["data"] == receipt and receipt["manifest_sha256"]
    again = post(env, f"/runs/{run_id}/external-output/confirm", {"preview_id": data["id"]})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "invalid_state"


def test_the_page_cannot_choose_the_actor_or_the_channel(env):
    run_id = waiting_run(env)
    forged = post(env, f"/runs/{run_id}/external-output/preview", {"output": CONTENT, "actor": "user:mallory"})
    assert forged.status_code == 422
    preview = post(env, f"/runs/{run_id}/external-output/preview", {"output": CONTENT}).json()
    for extra in ({"actor": "user:mallory"}, {"channel": "api"}):
        body = {"preview_id": preview["id"], **extra}
        assert post(env, f"/runs/{run_id}/external-output/confirm", body).status_code == 422
    confirmed = post(env, f"/runs/{run_id}/external-output/confirm", {"preview_id": preview["id"]}).json()
    assert (confirmed["receipt"]["actor"], confirmed["receipt"]["channel"]) == ("user:operator", "gui")


def test_errors_keep_their_code_and_reason(env):
    run_id = waiting_run(env)
    path = f"/runs/{run_id}/external-output/preview"
    rejected = post(env, path, {"output": CONTENT, "evidence": evidence("code.exe")})
    assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "evidence_rejected"
    assert rejected.json()["detail"]["details"]["reason"]
    contract = post(env, path, {"output": {}})
    assert contract.status_code == 422 and contract.json()["detail"]["code"] == "human_submission_invalid"
    assert contract.json()["detail"]["details"]["problems"]
    assert post(env, f"/runs/{run_id}/external-output/confirm", {"preview_id": "0" * 32}).status_code == 404
    preview = post(env, path, {"output": CONTENT}).json()
    env.services.runs._external_now = lambda: preview["expires_at"]
    expired = post(env, f"/runs/{run_id}/external-output/confirm", {"preview_id": preview["id"]})
    assert expired.status_code == 409 and expired.json()["detail"]["code"] == "invalid_state"


def test_a_run_that_does_not_wait_answers_409_and_routes_need_the_token(env):
    run_id = new_run(env)
    response = post(env, f"/runs/{run_id}/external-output/preview", {"output": CONTENT})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "invalid_state"
    for action in ("preview", "confirm"):
        assert env.client.post(f"/gui/api/runs/{run_id}/external-output/{action}", json={}).status_code == 401


JS = STATIC_DIR / "js"


def test_the_block_is_wired_and_never_reads_evidence_content_as_text():
    source = (JS / "external-output.js").read_text(encoding="utf-8")
    actions = (JS / "human-actions.js").read_text(encoding="utf-8")
    assert "<${ExternalOutput}" in actions
    assert '"external-output/preview"' in source and '"external-output/confirm"' in source
    assert "<${Confirm}" in source and "details.reason" in source and "details.problems" in source
    assert not re.search(r"\bactor\s*:", source)                  # the page never sends who is acting
    assert not re.search(r"\.text\(\)|readAsText|innerHTML", source)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("name", ["external-output.js", "human-actions.js", "help-text.js"])
def test_node_checks_the_scripts(name):
    result = subprocess.run([shutil.which("node"), "--check", str(JS / name)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
