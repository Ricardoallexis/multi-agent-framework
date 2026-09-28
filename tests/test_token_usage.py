# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import ResearchMode, RunStatus, SocialPostRequest
from multiagent.errors import ProviderUnavailable
from multiagent.observability.usage import read_usage, summarize_usage


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "multiagent.db",
        runs_dir=tmp_path / "data" / "runs",
        assets_dir=tmp_path / "data" / "assets",
        worker_enabled=False,
        gemini_api_key="",
    )


class AlwaysUnavailable(LLMAdapter):
    provider = "ollama"

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False):
        raise ProviderUnavailable("offline")

    def health(self):
        return {"provider": self.provider, "available": False}


class ZeroUsageAdapter(FakeAdapter):
    provider = "ollama"

    def generate_structured(self, **kwargs) -> LLMResponse:
        response = super().generate_structured(**kwargs)
        response.tokens_in = 0
        response.tokens_out = 0
        return response


class GeminiFakeAdapter(FakeAdapter):
    provider = "gemini"


def run_once(system, request: SocialPostRequest):
    run = system.run_service.create_social_post(request)
    assert system.worker.process_once() is True
    return run["id"], system.run_service.get(run["id"])


def test_execution_creates_provider_independent_usage_record(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run_id, state = run_once(
        system,
        SocialPostRequest(project_name="usage-record", objective="Create an educational post", topic="KNX"),
    )

    records = read_usage(system.store, run_id)
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert len(records) == 1
    assert records[0]["agent_id"] == "creator"
    assert records[0]["attempt"] == 1
    assert records[0]["provider"] == "fake"
    assert records[0]["tokens_total"] == records[0]["tokens_in"] + records[0]["tokens_out"]
    assert records[0]["duration_ms"] == 1


def test_multiple_agents_have_independent_records(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run_id, _ = run_once(
        system,
        SocialPostRequest(
            project_name="usage-agents",
            objective="Create a post with current information",
            topic="Wi-Fi",
            requires_web=True,
            research_mode=ResearchMode.GEMINI_GROUNDED,
        ),
    )

    records = read_usage(system.store, run_id)
    assert {record["agent_id"] for record in records} == {"researcher", "creator"}
    assert [record["attempt"] for record in records] == [1, 1]


def test_failed_provider_attempt_is_recorded_in_common_format(tmp_path):
    system = build_system(
        settings_for(tmp_path),
        adapter_overrides={"ollama": AlwaysUnavailable(), "gemini": GeminiFakeAdapter()},
    )
    run_id, _ = run_once(
        system,
        SocialPostRequest(project_name="usage-failure", objective="Create an educational post", topic="KNX"),
    )

    records = read_usage(system.store, run_id)
    assert [record["success"] for record in records] == [False, True]
    assert records[0]["provider"] == "ollama"
    assert records[0]["error_type"] == "ProviderUnavailable"
    assert records[0]["tokens_in"] is None
    assert records[0]["tokens_out"] is None
    assert records[1]["provider"] == "gemini"
    assert all({"agent_id", "attempt", "duration_ms"} <= record.keys() for record in records)


def test_missing_provider_usage_does_not_break_execution(tmp_path):
    system = build_system(settings_for(tmp_path), adapter_overrides={"ollama": ZeroUsageAdapter()})
    run_id, state = run_once(
        system,
        SocialPostRequest(project_name="usage-missing", objective="Create an educational post", topic="KNX"),
    )

    records = read_usage(system.store, run_id)
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert records[0]["tokens_in"] is None
    assert records[0]["tokens_out"] is None
    assert records[0]["tokens_total"] is None
    assert summarize_usage(records)["run"]["records_without_usage"] == 1


def test_telemetry_write_failure_does_not_stop_step(tmp_path, monkeypatch):
    system = build_system(settings_for(tmp_path), dry_run=True)
    original = system.store.record_telemetry

    def fail_record(**data):
        raise OSError("telemetry unavailable")

    monkeypatch.setattr(system.store, "record_telemetry", fail_record)
    run_id, state = run_once(
        system,
        SocialPostRequest(project_name="usage-isolated", objective="Create an educational post", topic="KNX"),
    )
    monkeypatch.setattr(system.store, "record_telemetry", original)

    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert any(event["event_type"] == "telemetry_failed" for event in state["events"])
    assert run_id


def test_usage_reading_matches_http_facade(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run_id, _ = run_once(
        system,
        SocialPostRequest(project_name="usage-parity", objective="Create an educational post", topic="KNX"),
    )
    expected = read_usage(system.store, run_id)

    with TestClient(create_app(system), base_url="http://localhost") as client:
        response = client.get(f"/api/v1/runs/{run_id}/usage")

    assert response.status_code == 200
    assert response.json()["records"] == expected
    assert response.json()["totals"] == summarize_usage(expected)
