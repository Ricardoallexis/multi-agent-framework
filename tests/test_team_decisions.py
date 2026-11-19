# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Team version approval and rejection decision-register records (EQP-T06)."""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"


def _services(tmp_path: Path, bundle_dir: Path, *, name: str = "case") -> ApplicationServices:
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )
    return ApplicationServices.create(settings, bundles={name: bundle_dir})


def _copy_general(tmp_path: Path) -> Path:
    dest = tmp_path / "general"
    shutil.copytree(GENERAL_BUNDLE, dest)
    return dest


def test_record_review_accepts_optional_team_id(tmp_path: Path):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundle_dir)
    try:
        without_team = services.decisions.record_review(
            title="Review step",
            body="Looks good.",
            proposed_by="agent:workflow",
            decided_by="user:ana",
            accepted=True,
            outcome="approve",
            run_id="run-1",
            project="demo",
        )
        assert without_team["team_id"] is None

        with_team = services.decisions.record_review(
            title="Team general_team version 1",
            body="bundle: case",
            proposed_by="agent:codex",
            decided_by="user:ana",
            accepted=True,
            outcome="approved",
            team_id="general_team",
            project="case",
        )
        assert with_team["team_id"] == "general_team"
        assert with_team["project"] == "case"
        assert with_team["run_id"] is None
        assert services.decisions.get(with_team["id"])["team_id"] == "general_team"
    finally:
        services.runs.close()


def test_approve_and_reject_record_team_decisions_with_diff_summary(tmp_path: Path):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundle_dir)
    try:
        v1 = services.teams.propose("case", actor="agent:codex/gpt", channel="python")
        approved = services.teams.approve(v1["id"], actor="user:ana", channel="python", note="Ship it")

        dec_id = approved["decision_id"]
        assert isinstance(dec_id, str) and dec_id.startswith("dec-")
        assert "decision_record_error" not in approved

        dec1 = services.decisions.get(dec_id)
        assert dec1["title"] == "Team general_team version 1"
        assert dec1["proposed_by"] == "agent:codex/gpt"
        assert dec1["decided_by"] == "user:ana"
        assert dec1["status"] == "accepted"
        assert dec1["active"] is True
        assert dec1["outcome"] == "approved: Ship it"
        assert dec1["team_id"] == "general_team"
        assert dec1["project"] == "case"
        assert "case" in dec1["body"]
        assert approved["sha256"] in dec1["body"]
        assert "roles: 4 added, 0 removed, 0 changed" in dec1["body"]
        assert "members: 6 added, 0 removed, 0 changed" in dec1["body"]
        assert "assignments: 6 added, 0 removed, 0 changed" in dec1["body"]
        assert "relationships: 5 added, 0 removed, 0 changed" in dec1["body"]

        # Modify team, draft + submit, then reject without note.
        data = yaml.safe_load((bundle_dir / "team.yaml").read_text(encoding="utf-8"))
        data["roles"][0]["permissions"] = ["read"]
        data["members"] = [m for m in data["members"] if m["id"] != "advisor_agent"]
        data["assignments"] = [a for a in data["assignments"] if a["member"] != "advisor_agent"]
        (bundle_dir / "team.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

        v2 = services.teams.propose("case", actor="agent:writer", channel="python", draft=True)
        services.teams.submit(v2["id"], "user:bob")
        rejected = services.teams.reject(v2["id"], actor="user:carol", channel="python")

        rej_dec_id = rejected["decision_id"]
        assert isinstance(rej_dec_id, str) and rej_dec_id.startswith("dec-")
        assert "decision_record_error" not in rejected

        dec2 = services.decisions.get(rej_dec_id)
        assert dec2["title"] == "Team general_team version 2"
        assert dec2["proposed_by"] == "user:bob"
        assert dec2["decided_by"] == "user:carol"
        assert dec2["status"] == "rejected"
        assert dec2["active"] is False
        assert dec2["outcome"] == "rejected"
        assert dec2["team_id"] == "general_team"
        assert dec2["project"] == "case"
        assert rejected["sha256"] in dec2["body"]
        assert "roles: 0 added, 0 removed, 1 changed" in dec2["body"]
        assert "members: 0 added, 1 removed, 0 changed" in dec2["body"]
        assert "assignments: 0 added, 1 removed, 0 changed" in dec2["body"]
        assert "relationships: 0 added, 0 removed, 0 changed" in dec2["body"]
    finally:
        services.runs.close()


def test_decision_record_failure_keeps_approval_and_rejection_valid(tmp_path: Path, monkeypatch):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundle_dir)
    try:
        v1 = services.teams.propose("case", actor="agent:codex", channel="python")

        def fail_runtime(*args, **kwargs):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(services.decisions, "record_review", fail_runtime)
        approved = services.teams.approve(v1["id"], actor="user:ana", channel="python", note="Still valid")
        assert approved["status"] == "approved"
        assert approved["active"] is True
        assert approved["decision_id"] is None
        assert approved["decision_record_error"] == "RuntimeError"
        assert services.teams.approved("case")["id"] == v1["id"]

        data = yaml.safe_load((bundle_dir / "team.yaml").read_text(encoding="utf-8"))
        data["description"] = "Changed team description"
        (bundle_dir / "team.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        v2 = services.teams.propose("case", actor="agent:codex", channel="python")

        def fail_value(*args, **kwargs):
            raise ValueError("synthetic value failure")

        monkeypatch.setattr(services.decisions, "record_review", fail_value)
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            resp = client.post(
                f"/api/v1/teams/versions/{v2['id']}/reject",
                json={"actor": "user:bob", "note": "Not ready"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["decision_id"] is None
        assert body["decision_record_error"] == "ValueError"
        assert services.teams.get(v2["id"])["status"] == "rejected"
    finally:
        services.runs.close()
