# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""External output previews, receipts, backend parity and public doors."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.external_output import canonical_json, sha256
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError
from test_gui_runs import CONTENT, POST_INPUTS

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


@pytest.fixture(params=["native", "mock"])
def services(tmp_path, request):
    settings = Settings(_env_file=None, local_dir=tmp_path / request.param,
                        execution_backend=request.param, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    result = ApplicationServices.create(settings, dry_run=True)
    yield result
    result.runs.close()


def waiting_run(services):
    run = services.runs.create(RunRequest(
        project_name="External fixture", workflow_id="social_post",
        inputs=POST_INPUTS, execution_mode="human_guided"))
    assert services.system.worker.process_once()
    state = services.runs.get(run["id"])
    assert state["waiting_reason"] == "execute_step"
    return run["id"]


def evidence(name="trace.txt", content=b"fixture log\n"):
    return [{"name": name, "content_base64": base64.b64encode(content).decode("ascii")}]


def error_code(call, *args, **kwargs):
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value.code


def test_waiting_preview_is_read_only_and_confirmation_registers_hashes(services):
    run_id = waiting_run(services)
    before = services.runs.get(run_id)
    preview = services.runs.external_output_preview(
        run_id, CONTENT, evidence=evidence(), actor="user:fixture", channel="test")
    after = services.runs.get(run_id)
    assert (after["status"], after["events"], after["artifacts"]) == (
        before["status"], before["events"], before["artifacts"])
    assert preview["output"] == CONTENT
    assert preview["files"][0]["sha256"] == sha256(b"fixture log\n")
    confirmed = services.runs.external_output_confirm(run_id, preview["id"], actor="user:fixture", channel="test")
    assert confirmed["waiting_reason"] == "review"
    assert services.system.store.list_step_attempts(run_id, confirmed["waiting_step"])[0]["model_id"] == "external:output"
    artifacts = confirmed["artifacts"]
    manifest = next(a for a in artifacts if a["kind"] == "external_manifest")
    receipt = next(a for a in artifacts if a["kind"] == "external_receipt")
    file = next(a for a in artifacts if a["kind"] == "external_evidence")
    assert manifest["data"]["output_sha256"] == sha256(canonical_json(CONTENT))
    assert manifest["data"]["files"][0]["sha256"] == file["data"]["sha256"]
    assert file["data"]["sha256"] == sha256((services.settings.data_dir / file["path"]).read_bytes())
    assert receipt["data"]["manifest_sha256"] == sha256(
        (services.settings.data_dir / manifest["path"]).read_bytes())
    assert receipt["data"]["file_sha256"][preview["files"][0]["file"]] == file["data"]["sha256"]
    assert error_code(services.runs.external_output_confirm, run_id, preview["id"],
                      actor="user:fixture") == "invalid_state"


def test_review_creates_new_attempt_and_preserves_review(services):
    run_id = waiting_run(services)
    first = services.runs.external_output_preview(run_id, CONTENT)
    services.runs.external_output_confirm(run_id, first["id"], actor="user:fixture")
    before = services.runs.get(run_id)
    review = services.runs.external_output_preview(run_id, CONTENT)
    assert review["attempt"] == before["waiting_attempt"]
    confirmed = services.runs.external_output_confirm(run_id, review["id"], actor="user:fixture")
    assert confirmed["waiting_reason"] == "review"
    assert confirmed["waiting_attempt"] == before["waiting_attempt"] + 1
    attempts = services.system.store.list_step_attempts(run_id, confirmed["waiting_step"])
    assert [row["attempt"] for row in attempts] == [1, 2]
    assert services.runs.approve(run_id)["status"] in {"queued", "completed"}


def test_stale_actor_and_bad_evidence(services):
    run_id = waiting_run(services)
    preview = services.runs.external_output_preview(run_id, CONTENT)
    assert error_code(services.runs.external_output_confirm, run_id, preview["id"],
                      actor="service:robot") == "invalid_request"
    services.runs._external_now = lambda: preview["expires_at"]
    assert error_code(services.runs.external_output_confirm, run_id, preview["id"],
                      actor="user:fixture") == "invalid_state"
    services.runs._external_now = lambda: preview["expires_at"] - 1
    assert error_code(services.runs.external_output_preview, run_id, CONTENT,
                      evidence=evidence("../trace.txt")) == "evidence_rejected"
    assert error_code(services.runs.external_output_preview, run_id, CONTENT,
                      evidence=evidence("code.exe")) == "evidence_rejected"
    assert error_code(services.runs.external_output_preview, run_id, CONTENT,
                      evidence=evidence("capture.png", b"wrong")) == "evidence_rejected"
    assert error_code(services.runs.external_output_preview, run_id, {}) == "human_submission_invalid"
    services.settings.external_evidence_max_file_bytes = 2
    assert error_code(services.runs.external_output_preview, run_id, CONTENT,
                      evidence=evidence()) == "evidence_rejected"
    services.settings.external_evidence_max_file_bytes = 10_000_000
    services.settings.external_evidence_max_total_bytes = 5
    assert error_code(services.runs.external_output_preview, run_id, CONTENT,
                      evidence=evidence("a.txt", b"abc") + evidence("b.txt", b"def")) == "evidence_rejected"
    services.settings.external_evidence_max_total_bytes = 25_000_000
    services.system.store.update_run(run_id, waiting_attempt=99)
    assert error_code(services.runs.external_output_confirm, run_id, preview["id"],
                      actor="user:fixture") == "invalid_state"


def test_api_and_cli(services, monkeypatch, capsys, tmp_path):
    run_id = waiting_run(services)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        class ClientContext:
            def __enter__(self):
                return client
            def __exit__(self, *_):
                return False
        monkeypatch.setattr(cli, "api_client", ClientContext)
        output = tmp_path / "output.json"
        output.write_text(json.dumps(CONTENT), encoding="utf-8")
        log = tmp_path / "trace.txt"
        log.write_text("fixture log\n", encoding="utf-8")
        cli.main(["external-output", "preview", run_id, "--output", str(output),
                  "--evidence", str(log)])
        preview = json.loads(capsys.readouterr().out)
        assert preview["files"][0]["name"] == "trace.txt"
        cli.main(["external-output", "confirm", run_id, preview["id"]])
        assert json.loads(capsys.readouterr().out)["waiting_reason"] == "review"
        bad = client.post(f"/api/v1/runs/{run_id}/external-output/confirm",
                          json={"preview_id": preview["id"], "actor": "user:fixture"})
        assert bad.status_code == 409 and bad.json()["detail"]["code"] == "invalid_state"


def test_native_bundle_review_of_model_step(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "bundle",
                        worker_enabled=False, mock_mode=True, gemini_api_key="",
                        openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings, bundles={"team": BUNDLE}, dry_run=True)
    try:
        run = services.runs.create(RunRequest(
            project_name="Review fixture", workflow_id="spec_review",
            inputs={"spec_text": "Users can reset a password by email."}), bundle="team")
        runtime = services.runs._bundle_runtime("team")
        assert runtime.worker.process_once()
        before = services.runs.get(run["id"])
        assert before["waiting_reason"] == "review"
        old = runtime.store.review_artifact(run["id"], step_id=before["waiting_step"],
                                            attempt=before["waiting_attempt"])
        output = json.loads(old["data_json"])
        preview = services.runs.external_output_preview(run["id"], output)
        after = services.runs.external_output_confirm(run["id"], preview["id"], actor="user:fixture")
        assert after["waiting_reason"] == "review"
        assert after["waiting_attempt"] == before["waiting_attempt"] + 1
        assert len(runtime.store.list_step_attempts(run["id"], before["waiting_step"])) == 2
    finally:
        services.runs.close()


def test_api_preview_and_confirm(services):
    run_id = waiting_run(services)
    url = f"/api/v1/runs/{run_id}/external-output"
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        prepared = client.post(url + "/preview", json={
            "output": CONTENT, "evidence": evidence(), "actor": "user:fixture", "channel": "api"})
        assert prepared.status_code == 200, prepared.text
        preview = prepared.json()
        assert preview["step_id"] == "create" and preview["files"][0]["kind"] == "text/plain"
        confirmed = client.post(url + "/confirm", json={
            "preview_id": preview["id"], "actor": "user:fixture", "channel": "api"})
        assert confirmed.status_code == 200, confirmed.text
        assert any(item["kind"] == "external_receipt" for item in confirmed.json()["artifacts"])


def test_cli_uses_api_double(monkeypatch, capsys, tmp_path):
    calls = []
    def fake_api(method, path, *, json_body):
        calls.append((method, path, json_body))
        return {"id": "preview-fixture"} if path.endswith("/preview") else {"status": "waiting_human"}
    monkeypatch.setattr(cli, "_api_json", fake_api)
    output = tmp_path / "output.json"
    output.write_text(json.dumps(CONTENT), encoding="utf-8")
    log = tmp_path / "trace.txt"
    log.write_bytes(b"fixture log\n")
    cli.main(["external-output", "preview", "run-fixture", "--output", str(output),
              "--evidence", str(log)])
    cli.main(["external-output", "confirm", "run-fixture", "preview-fixture"])
    assert [path for _, path, _ in calls] == [
        "/api/v1/runs/run-fixture/external-output/preview",
        "/api/v1/runs/run-fixture/external-output/confirm"]
    assert calls[0][2]["output"] == CONTENT
    assert base64.b64decode(calls[0][2]["evidence"][0]["content_base64"]) == b"fixture log\n"
    assert calls[1][2]["actor"].startswith("user:")
