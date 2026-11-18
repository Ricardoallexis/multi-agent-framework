# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Step attempt history, comparison, API/CLI parity, GUI routes and review reports."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.contracts import ExecutionMode, HumanStepSubmission
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


class _ClientContext:
    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


def _make_services(tmp_path: Path) -> ApplicationServices:
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        mock_mode=True,
        max_llm_calls=8,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
        gui_token_file=tmp_path / "gui-token",
    )
    return ApplicationServices.create(settings, bundles={"team": BUNDLE}, dry_run=True)


def _process_team(services: ApplicationServices) -> None:
    runtime = services.runs._bundle_runtime("team")
    assert runtime is not None and runtime.worker.process_once()


def _create_two_attempt_checkpoint_run(services: ApplicationServices) -> tuple[str, str]:
    run = services.runs.create(
        RunRequest(
            project_name="Synthetic history",
            workflow_id="spec_review",
            inputs={"spec_text": "Users can reset a password by email."},
        ),
        bundle="team",
    )
    run_id = run["id"]
    _process_team(services)  # runs extract_requirements (attempt 1) + assess_risks (attempt 1) -> checkpoint
    waiting = services.runs.get(run_id)
    assert (waiting["status"], waiting["waiting_step"]) == ("waiting_human", "assess_risks")

    feedback = "Add rate-limiting mitigation to the highest risk"
    services.runs.request_changes(run_id, feedback)
    _process_team(services)  # runs assess_risks (attempt 2) -> checkpoint

    runtime = services.runs._bundle_runtime("team")
    assert runtime is not None
    # Distinguish attempt 2 output deterministically while keeping valid schema shape.
    with runtime.store.db.tx() as conn:
        row = conn.execute(
            "SELECT output_json FROM run_steps WHERE run_id=? AND step_id=? AND attempt=2",
            (run_id, "assess_risks"),
        ).fetchone()
        updated = json.loads(row["output_json"])
        updated["risks"][0]["risk"] = "Enforce per-IP rate limiting\nand token expiry."
        conn.execute(
            "UPDATE run_steps SET output_json=? WHERE run_id=? AND step_id=? AND attempt=2",
            (json.dumps(updated), run_id, "assess_risks"),
        )
    services.runs.approve(run_id)
    return run_id, feedback


def test_checkpoint_revision_records_two_attempts_feedback_review_and_report(tmp_path):
    services = _make_services(tmp_path)
    try:
        run_id, feedback = _create_two_attempt_checkpoint_run(services)

        history = services.runs.step_attempts(run_id, "assess_risks")
        assert history["run_id"] == run_id and history["step_id"] == "assess_risks"
        assert history["bundle"] == "team"
        assert len(history["attempts"]) == 2

        first, second = history["attempts"]
        assert (first["attempt"], first["status"]) == (1, "completed")
        assert first["producer"]["kind"] == "model" and first["producer"]["model"]
        assert first["input"] == {"revision_feedback": "", "has_previous_output": False}
        assert first["markdown"] and "# assess_risks" in first["markdown"]
        assert first["review"]["status"] == "changes_requested"
        assert first["review"]["actor"] == "operator"
        assert first["review"]["reviewed_at"]
        assert first["review"]["feedback"] == feedback
        assert first["tokens"] is not None
        assert first["tokens"]["tokens_in"] == 10 and first["tokens"]["tokens_out"] == 20
        assert first["tokens"]["tokens_total"] == 30

        assert (second["attempt"], second["status"]) == (2, "completed")
        assert second["input"] == {"revision_feedback": feedback, "has_previous_output": True}
        assert second["review"]["status"] == "approved"
        assert second["review"]["actor"] == "operator"
        assert second["review"]["reviewed_at"]
        assert second["approved"] is True

        comparison = services.runs.compare_attempts(run_id, "assess_risks", 1, 2)
        assert comparison["a"]["attempt"] == 1 and comparison["b"]["attempt"] == 2
        assert comparison["changes"] == [
            {
                "path": "risks[0].risk",
                "change": "changed",
                "before": first["output"]["risks"][0]["risk"],
                "after": "Enforce per-IP rate limiting\nand token expiry.",
                "diff": comparison["changes"][0]["diff"],
            }
        ]
        assert "--- before\n+++ after\n@@" in comparison["changes"][0]["diff"]

        report = services.runs.report(run_id)
        assert [item["event_type"] for item in report["reviews"]] == [
            "checkpoint",
            "human_revision",
            "checkpoint",
            "human_approved",
        ]
        assert report["reviews"][1]["step_id"] == "assess_risks"
        assert report["reviews"][1]["attempt"] == 1
        assert report["reviews"][1]["actor"] == "operator"
        assert report["reviews"][1]["timestamp"]
        assert feedback not in json.dumps(report)  # untrusted feedback stays out unless include_raw=True
        markdown = services.runs.report(run_id, format="markdown")
        assert "## Reviews (4)" in markdown
    finally:
        services.runs.close()


def test_human_guided_step_preserves_prompt_used_and_notes(tmp_path):
    services = _make_services(tmp_path)
    try:
        run = services.runs.create(
            RunRequest(
                project_name="Synthetic human",
                workflow_id="spec_review",
                inputs={"spec_text": "Users receive a verification link."},
                execution_mode=ExecutionMode.HUMAN_GUIDED,
            ),
            bundle="team",
        )
        run_id = run["id"]
        _process_team(services)
        pending = services.runs.human_next(run_id)
        assert pending["step_id"] == "extract_requirements"

        sample_output = load_bundle(BUNDLE, services.settings).sample_outputs[pending["expected_contract"]]
        services.runs.human_submit(
            run_id,
            HumanStepSubmission(
                raw_response=json.dumps(sample_output),
                provider="external-chat",
                model="synthetic-human-model",
                prompt_used="Custom edited prompt used externally",
                notes="Checked edge cases manually",
            ),
        )

        history = services.runs.step_attempts(run_id, "extract_requirements")
        assert len(history["attempts"]) == 1
        attempt = history["attempts"][0]
        assert attempt["producer"]["kind"] == "human"
        assert attempt["producer"]["provider"] == "external-chat"
        assert attempt["producer"]["model"] == "synthetic-human-model"
        assert attempt["producer"]["provenance"] == "human_reported"
        assert attempt["human"] is not None
        assert attempt["human"]["prompt_used"] == "Custom edited prompt used externally"
        assert attempt["human"]["notes"] == "Checked edge cases manually"
        assert attempt["output"] == sample_output
        assert attempt["tokens"] is None
    finally:
        services.runs.close()


def test_report_includes_rejected_review_with_payload_actor(tmp_path):
    services = _make_services(tmp_path)
    try:
        run = services.runs.create(
            RunRequest(
                project_name="Synthetic rejection",
                workflow_id="spec_review",
                inputs={"spec_text": "Users can reset a password."},
            ),
            bundle="team",
        )
        run_id = run["id"]
        _process_team(services)
        services.runs.reject(run_id, "Out of scope")
        # Simulate an explicit payload actor on a subsequent synthetic event to verify payload actor extraction.
        runtime = services.runs._bundle_runtime("team")
        assert runtime is not None
        runtime.store.db.log_event(
            run_id,
            "human_approved",
            {"step_id": "assess_risks", "attempt": 1, "by": "user:ana"},
        )

        attempts = services.runs.step_attempts(run_id, "assess_risks")["attempts"]
        assert attempts[0]["review"]["status"] == "approved"
        assert attempts[0]["review"]["actor"] == "user:ana"

        report = services.runs.report(run_id)
        rejected = next(r for r in report["reviews"] if r["event_type"] == "human_rejected")
        assert (rejected["step_id"], rejected["attempt"], rejected["actor"]) == ("assess_risks", 1, "operator")
        assert "Out of scope" not in json.dumps(report)
        explicit = next(r for r in report["reviews"] if r["event_type"] == "human_approved")
        assert explicit["actor"] == "user:ana"
    finally:
        services.runs.close()


def test_service_errors_404_and_422(tmp_path):
    services = _make_services(tmp_path)
    try:
        run_id, _ = _create_two_attempt_checkpoint_run(services)
        with pytest.raises(ServiceError) as missing_run:
            services.runs.step_attempts("missing-run", "assess_risks")
        assert (missing_run.value.code, missing_run.value.status) == ("not_found", 404)

        with pytest.raises(ServiceError) as missing_step:
            services.runs.step_attempts(run_id, "no_such_step")
        assert (missing_step.value.code, missing_step.value.status) == ("not_found", 404)

        with pytest.raises(ServiceError) as missing_attempt:
            services.runs.compare_attempts(run_id, "assess_risks", 1, 99)
        assert (missing_attempt.value.code, missing_attempt.value.status) == ("not_found", 404)

        with pytest.raises(ServiceError) as bad_attempt:
            services.runs.compare_attempts(run_id, "assess_risks", 0, 1)
        assert (bad_attempt.value.code, bad_attempt.value.status) == ("invalid_request", 422)
    finally:
        services.runs.close()


def test_api_cli_parity_errors_and_gui_token_rules(monkeypatch, capsys, tmp_path):
    services = _make_services(tmp_path)
    token = ensure_token(services.settings.gui_token_file)
    auth = {"Authorization": f"Bearer {token}"}
    try:
        run_id, _ = _create_two_attempt_checkpoint_run(services)
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            api_list = client.get(f"/api/v1/runs/{run_id}/steps/assess_risks/attempts")
            assert api_list.status_code == 200
            api_cmp = client.get(
                f"/api/v1/runs/{run_id}/steps/assess_risks/attempts/compare",
                params={"a": 1, "b": 2},
            )
            assert api_cmp.status_code == 200

            # CLI parity
            monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
            cli.main(["attempts", run_id, "assess_risks"])
            cli_list_out = capsys.readouterr().out
            assert json.loads(cli_list_out) == api_list.json()

            cli.main(["attempts", run_id, "assess_risks", "--compare", "1", "2"])
            cli_cmp_out = capsys.readouterr().out
            assert json.loads(cli_cmp_out) == api_cmp.json()

            # 404 and 400 checks on /api/v1
            assert client.get("/api/v1/runs/missing/steps/assess_risks/attempts").status_code == 404
            assert client.get(f"/api/v1/runs/{run_id}/steps/missing/attempts").status_code == 404
            assert client.get(
                f"/api/v1/runs/{run_id}/steps/assess_risks/attempts/compare",
                params={"a": 1, "b": 9},
            ).status_code == 404
            assert client.get(
                f"/api/v1/runs/{run_id}/steps/assess_risks/attempts",
                headers={"Host": "evil.example"},
            ).status_code == 400

            # GUI routes with and without token + host check
            gui_list_path = f"/gui/api/runs/{run_id}/steps/assess_risks/attempts"
            gui_cmp_path = f"/gui/api/runs/{run_id}/steps/assess_risks/attempts/compare?a=1&b=2"
            assert client.get(gui_list_path).status_code == 401
            assert client.get(gui_cmp_path).status_code == 401
            assert client.get(gui_list_path, headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert client.get(gui_list_path, headers={**auth, "Host": "evil.example"}).status_code == 400

            gui_list = client.get(gui_list_path, headers=auth)
            gui_cmp = client.get(gui_cmp_path, headers=auth)
            assert gui_list.status_code == 200 and gui_list.json() == api_list.json()
            assert gui_cmp.status_code == 200 and gui_cmp.json() == api_cmp.json()
    finally:
        services.runs.close()


def test_gui_run_detail_groups_by_step_and_uses_api_helper():
    source = (STATIC_DIR / "js" / "run-detail.js").read_text(encoding="utf-8")
    assert "groupArtifactsByStep" in source
    assert "Comparar" in source
    assert "/attempts/compare" in source
    assert "apiGet" in source
    assert not re.search(r"\bfetch\s*\(", source)
