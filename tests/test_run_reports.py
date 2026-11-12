"""Run reports expose structured evidence without reproducing private inputs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices
from multiagent.services.reports import build_run_report, render_run_report_markdown


def test_report_filters_payloads_and_explicitly_includes_raw():
    marker = "PRIVATE_SYNTHETIC_PROMPT_DO_NOT_EXPORT"
    run = {"id": "run-1", "status": "failed", "workflow_id": "demo", "request": {"prompt": marker},
           "events": [
               {"seq": 1, "ts": "2026-01-01", "event_type": "step_started",
                "payload": {"step_id": "draft", "prompt": marker}},
               {"seq": 2, "ts": "2026-01-01", "event_type": "routing_decision",
                "payload": {"step_id": "draft", "selected_model": "fixture", "prompt": marker}},
               {"seq": 3, "ts": "2026-01-01", "event_type": "model_attempt",
                "payload": {"step_id": "draft", "status": "failed", "error_type": "FixtureError", "error": marker}},
               {"seq": 4, "ts": "2026-01-01", "event_type": "call_paused",
                "payload": {"step_id": "draft", "waiting_reason": "content_review", "request": {"text": marker}}},
               {"seq": 5, "ts": "2026-01-01", "event_type": "workflow_failed",
                "payload": {"type": "FixtureError", "error": marker}},
           ]}
    usage = {"records": [{"model": "fixture", "tokens_in": 4, "prompt": marker}],
             "totals": {"tokens_in": 4}, "cost_is_estimate": True}
    safe = build_run_report(run, usage)
    assert marker not in json.dumps(safe)
    assert marker not in render_run_report_markdown(safe)
    assert len(safe["steps"]) == len(safe["attempts"]) == len(safe["routing_decisions"]) == 1
    assert len(safe["human_waits"]) == len(safe["errors"]) == 1
    assert safe["usage"]["records"][0]["tokens_in"] == 4
    raw = build_run_report(run, usage, include_raw=True)
    assert marker in json.dumps(raw)


def test_mock_run_report_matches_python_and_http(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False)
    bundle = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
    services = ApplicationServices.create(settings, bundles={"team": bundle})
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post("/api/v1/runs", params={"bundle": "team"}, json={
            "project_name": "Synthetic", "workflow_id": "spec_review",
            "inputs": {"spec_text": "Synthetic private fixture"},
        })
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        assert services.runs._bundle_runtime("team").worker.process_once()
        expected = services.runs.report(run_id)
        assert expected["steps"] and expected["routing_decisions"] and expected["attempts"]
        assert "Synthetic private fixture" not in json.dumps(expected)
        actual = client.get(f"/api/v1/runs/{run_id}/report")
        assert actual.status_code == 200 and actual.json() == expected
        markdown = client.get(f"/api/v1/runs/{run_id}/report", params={"format": "markdown"})
        assert markdown.status_code == 200 and markdown.text == services.runs.report(run_id, format="markdown")
        assert client.get(f"/api/v1/runs/{run_id}/report", params={"format": "bad"}).status_code == 422
        assert client.get("/api/v1/runs/missing/report").status_code == 404
    services.runs.close()
