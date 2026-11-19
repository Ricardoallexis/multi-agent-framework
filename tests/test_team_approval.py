# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""S6.A3 / EQP-T03: versioned team approvals across Python, HTTP and CLI."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
SPEC_REVIEW_BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


def _settings(local_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        local_dir=local_dir,
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )


def _copy_general(tmp_path: Path, name: str = "general") -> Path:
    dest = tmp_path / name
    shutil.copytree(GENERAL_BUNDLE, dest)
    return dest


def _services(tmp_path: Path, *, bundles: dict[str, Path] | None = None) -> ApplicationServices:
    return ApplicationServices.create(_settings(tmp_path / "local"), bundles=bundles or {})


def _error(call, *args, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


def _read_team_yaml(bundle_dir: Path) -> dict:
    return yaml.safe_load((bundle_dir / "team.yaml").read_text(encoding="utf-8"))


def _write_team_yaml(bundle_dir: Path, data: dict) -> None:
    (bundle_dir / "team.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


class _ClientContext:
    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, *exc) -> bool:
        return False


def _invoke_cli(monkeypatch, capsys, client: TestClient, args: list[str]) -> dict:
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    cli.main(args)
    return json.loads(capsys.readouterr().out)


def test_python_lifecycle_draft_submit_approve_reject_and_history(tmp_path: Path):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundles={"case": bundle_dir})
    try:
        assert services.teams.approved("case") is None
        assert services.teams.list("case") == {"bundle": "case", "versions": [], "count": 0}

        draft = services.teams.propose("case", actor="agent:codex/gpt", channel="python", draft=True)
        assert draft["status"] == "draft"
        assert draft["version"] == 1
        assert draft["team_id"] == "general_team"
        assert draft["created_by"] == "agent:codex/gpt"
        assert draft["created_channel"] == "python"
        assert draft["active"] is False
        assert draft["diff"]["base_version_id"] is None
        assert len(draft["diff"]["roles"]["added"]) == 4

        # Cannot approve or reject a draft directly.
        assert _error(services.teams.approve, draft["id"], actor="user:ana", channel="python").code == "invalid_state"
        assert _error(services.teams.reject, draft["id"], actor="user:ana", channel="python").code == "invalid_state"

        submitted = services.teams.submit(draft["id"], "agent:codex/gpt")
        assert submitted["status"] == "proposed"
        assert submitted["proposed_by"] == "agent:codex/gpt"

        # Cannot submit twice.
        assert _error(services.teams.submit, draft["id"], "agent:codex/gpt").code == "invalid_state"

        # Reject v1.
        rejected = services.teams.reject(draft["id"], actor="user:ana", channel="python", note="Needs review")
        assert rejected["status"] == "rejected"
        assert rejected["decided_by"] == "user:ana"
        assert rejected["decided_channel"] == "python"
        assert rejected["note"] == "Needs review"
        assert rejected["decided_at"]
        assert services.teams.approved("case") is None

        # Propose and approve v2 -> becomes active.
        v2 = services.teams.propose("case", actor="user:ana", channel="python")
        assert v2["version"] == 2 and v2["status"] == "proposed"
        approved_v2 = services.teams.approve(v2["id"], actor="user:ana", channel="python", note="Ship it")
        assert approved_v2["status"] == "approved"
        assert approved_v2["active"] is True
        assert approved_v2["note"] == "Ship it"
        assert approved_v2["decided_by"] == "user:ana"
        assert approved_v2["diff"]["base_version_id"] is None
        assert services.teams.approved("case")["id"] == approved_v2["id"]

        # Modify team, propose v3, approve v3 -> v2 stays approved as history, v3 becomes active.
        data = _read_team_yaml(bundle_dir)
        data["description"] = "Updated general team."
        _write_team_yaml(bundle_dir, data)
        v3 = services.teams.propose("case", actor="agent:codex/gpt", channel="python")
        assert v3["version"] == 3
        approved_v3 = services.teams.approve(v3["id"], actor="user:bob", channel="python", note="v3 ok")
        assert approved_v3["active"] is True
        assert approved_v3["diff"]["base_version_id"] == approved_v2["id"]
        assert approved_v3["diff"]["team_fields"]["description"] == {
            "before": data["description"].replace("Updated general team.",
                                                  "Example team definition for the general bundle combining bundle agents and a human lead."),
            "after": "Updated general team.",
        }
        assert services.teams.approved("case")["id"] == approved_v3["id"]

        v2_after = services.teams.get(v2["id"])
        assert v2_after["status"] == "approved"
        assert v2_after["active"] is False

        listed = services.teams.list("case")
        assert [item["version"] for item in listed["versions"]] == [1, 2, 3]
        assert [item["status"] for item in listed["versions"]] == ["rejected", "approved", "approved"]
    finally:
        services.runs.close()


def test_http_and_cli_gates_with_api_double(tmp_path: Path, monkeypatch, capsys):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundles={"case": bundle_dir})
    try:
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            # HTTP: propose -> list -> show -> approve
            resp = client.post("/api/v1/teams/case/versions", json={"actor": "agent:codex", "draft": False})
            assert resp.status_code == 201
            v1 = resp.json()
            assert v1["status"] == "proposed" and v1["created_channel"] == "api"

            listed = client.get("/api/v1/teams/case/versions")
            assert listed.status_code == 200 and listed.json()["count"] == 1

            shown = client.get(f"/api/v1/teams/versions/{v1['id']}")
            assert shown.status_code == 200 and shown.json() == v1

            approved = client.post(
                f"/api/v1/teams/versions/{v1['id']}/approve",
                json={"actor": "user:ana", "note": "LGTM"},
            )
            assert approved.status_code == 200
            assert approved.json()["status"] == "approved"
            assert client.get("/api/v1/teams/case/approved").json()["id"] == v1["id"]

            # Modify team and test CLI commands through TestClient (api_client double)
            data = _read_team_yaml(bundle_dir)
            data["roles"][0]["permissions"] = ["read"]
            _write_team_yaml(bundle_dir, data)

            cli_draft = _invoke_cli(monkeypatch, capsys, client, ["team", "propose", "case", "--draft"])
            assert cli_draft["status"] == "draft"
            assert cli_draft["created_by"] == "user:local"
            assert cli_draft["created_channel"] == "cli"

            cli_submitted = _invoke_cli(monkeypatch, capsys, client, ["team", "submit", cli_draft["id"]])
            assert cli_submitted["status"] == "proposed"

            cli_shown = _invoke_cli(monkeypatch, capsys, client, ["team", "show", cli_draft["id"]])
            assert cli_shown["diff"]["roles"]["changed"][0]["id"] == "lead"

            cli_rejected = _invoke_cli(
                monkeypatch, capsys, client,
                ["team", "reject", cli_draft["id"], "--note", "Hold on"],
            )
            assert cli_rejected["status"] == "rejected"
            assert cli_rejected["decided_by"] == "user:local"
            assert cli_rejected["decided_channel"] == "cli"
            assert cli_rejected["note"] == "Hold on"

            cli_list = _invoke_cli(monkeypatch, capsys, client, ["team", "list", "case"])
            assert cli_list["count"] == 2

            # Direct _api_json double verification for CLI routing and default actor
            calls: list[tuple[str, str, dict | None, dict | None]] = []

            def fake_api_json(method: str, path: str, *, json_body=None, params=None, **_kwargs):
                calls.append((method, path, json_body, params))
                return {"ok": True}

            monkeypatch.setattr(cli, "_api_json", fake_api_json)
            cli.main(["team", "propose", "case"])
            cli.main(["team", "approve", v1["id"], "--note", "ok"])
            assert calls == [
                ("POST", "/api/v1/teams/case/versions", {"actor": "user:local", "draft": False, "channel": "cli"}, None),
                ("POST", f"/api/v1/teams/versions/{v1['id']}/approve",
                 {"actor": "user:local", "note": "ok", "channel": "cli"}, None),
            ]
    finally:
        services.runs.close()


def test_only_user_actor_decides_and_decided_version_is_immutable(tmp_path: Path):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundles={"case": bundle_dir})
    try:
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            v1 = services.teams.propose("case", actor="agent:codex/gpt", channel="python")

            for bad_actor in ("agent:codex/gpt", "person:ana", "executor:codex_cli", "invalid_actor"):
                err_app = _error(services.teams.approve, v1["id"], actor=bad_actor, channel="python")
                assert (err_app.code, err_app.status) == ("invalid_request", 422)
                err_rej = _error(services.teams.reject, v1["id"], actor=bad_actor, channel="python")
                assert (err_rej.code, err_rej.status) == ("invalid_request", 422)

                http_resp = client.post(
                    f"/api/v1/teams/versions/{v1['id']}/approve",
                    json={"actor": bad_actor},
                )
                assert http_resp.status_code == 422
                assert http_resp.json()["detail"]["code"] == "invalid_request"

            approved = services.teams.approve(v1["id"], actor="user:ana", channel="python", note="Approved")
            assert approved["status"] == "approved"

            # Cannot approve or reject an already-approved version.
            for action in (services.teams.approve, services.teams.reject):
                err = _error(action, v1["id"], actor="user:bob", channel="python", note="Second try")
                assert (err.code, err.status) == ("invalid_state", 409)
            assert client.post(
                f"/api/v1/teams/versions/{v1['id']}/reject",
                json={"actor": "user:bob"},
            ).status_code == 409

            # Propose and reject v2, then verify it cannot be re-decided.
            data = _read_team_yaml(bundle_dir)
            data["description"] = "Second candidate"
            _write_team_yaml(bundle_dir, data)
            v2 = services.teams.propose("case", actor="user:ana", channel="python")
            services.teams.reject(v2["id"], actor="user:ana", channel="python", note="No")
            for action in (services.teams.approve, services.teams.reject):
                err = _error(action, v2["id"], actor="user:ana", channel="python")
                assert (err.code, err.status) == ("invalid_state", 409)
            assert services.teams.get(v1["id"])["note"] == "Approved"
            assert services.teams.get(v2["id"])["note"] == "No"
    finally:
        services.runs.close()


def test_structural_diff_against_approved_version(tmp_path: Path):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundles={"case": bundle_dir})
    try:
        v1 = services.teams.propose("case", actor="user:ana", channel="python")
        services.teams.approve(v1["id"], actor="user:ana", channel="python")

        data = _read_team_yaml(bundle_dir)
        # 1. Add a new role
        data["roles"].append({
            "id": "auditor",
            "purpose": "Audits final deliverables.",
            "rules": ["Verify evidence."],
            "skills": [],
            "contracts": ["ReviewReport"],
            "capabilities": ["doc.review"],
            "permissions": ["read"],
        })
        # 2. Change permissions on an existing role
        data["roles"][0]["permissions"] = ["read"]
        # 3. Remove a member and its assignment
        data["members"] = [m for m in data["members"] if m["id"] != "advisor_agent"]
        data["assignments"] = [a for a in data["assignments"] if a["member"] != "advisor_agent"]
        _write_team_yaml(bundle_dir, data)

        v2 = services.teams.propose("case", actor="agent:codex", channel="python")
        diff = v2["diff"]
        assert diff["base_version_id"] == v1["id"]
        assert diff["base_version"] == 1
        assert diff["has_changes"] is True
        assert [r["id"] for r in diff["roles"]["added"]] == ["auditor"]
        assert diff["roles"]["removed"] == []
        assert diff["roles"]["changed"] == [{
            "id": "lead",
            "fields": {"permissions": {"before": [], "after": ["read"]}},
        }]
        assert [m["id"] for m in diff["members"]["removed"]] == ["advisor_agent"]
        assert diff["members"]["added"] == []
        assert diff["assignments"]["removed"] == [{"role": "writer", "member": "advisor_agent"}]
        assert services.teams.diff(v2["id"]) == diff
        assert services.teams.get(v2["id"])["diff"] == diff

        approved_v2 = services.teams.approve(v2["id"], actor="user:ana", channel="python")
        assert approved_v2["diff"] == diff
        assert services.teams.get(v2["id"])["diff"] == diff
    finally:
        services.runs.close()


def test_propose_without_changes_or_with_invalid_definition_does_not_save(tmp_path: Path):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundles={"case": bundle_dir})
    try:
        v1 = services.teams.propose("case", actor="user:ana", channel="python")
        services.teams.approve(v1["id"], actor="user:ana", channel="python")

        # Unchanged team.yaml -> invalid_request 422, nothing saved.
        err = _error(services.teams.propose, "case", actor="user:ana", channel="python")
        assert (err.code, err.status) == ("invalid_request", 422)
        assert err.details["reason"] == "no_changes"
        assert services.teams.list("case")["count"] == 1

        # Reordering permissions in YAML normalizes identically -> still no_changes.
        data = _read_team_yaml(bundle_dir)
        data["roles"][0]["permissions"] = ["read"]
        _write_team_yaml(bundle_dir, data)
        v2 = services.teams.propose("case", actor="user:ana", channel="python")
        services.teams.approve(v2["id"], actor="user:ana", channel="python")

        # Invalid contract reference -> invalid_definition 422 with findings, nothing saved.
        bad = _read_team_yaml(bundle_dir)
        bad["roles"][0]["contracts"].append("NonExistentContract")
        _write_team_yaml(bundle_dir, bad)
        err_def = _error(services.teams.propose, "case", actor="user:ana", channel="python")
        assert (err_def.code, err_def.status) == ("invalid_definition", 422)
        assert err_def.details["team"][0]["code"] == "team_unknown_contract"
        assert services.teams.list("case")["count"] == 2
    finally:
        services.runs.close()


def test_runs_bind_only_current_approved_team_version(tmp_path: Path):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundles={"case": bundle_dir, "plain": SPEC_REVIEW_BUNDLE})
    try:
        # 1. Bundle without team.yaml -> no team_version_bound event.
        plain_run = services.runs.create(
            RunRequest(project_name="Plain", workflow_id="spec_review",
                       inputs={"spec_text": "Users can reset passwords."}),
            bundle="plain",
        )
        assert not any(e["event_type"] == "team_version_bound" for e in plain_run["events"])

        # 2. Bundle with team.yaml proposed but not approved -> no team_version_bound event.
        v1 = services.teams.propose("case", actor="agent:codex", channel="python")
        unapproved_run = services.runs.create(
            RunRequest(project_name="NoTeamYet", workflow_id="summarize_document",
                       inputs={"document_text": "Some document text."}),
            bundle="case",
        )
        assert not any(e["event_type"] == "team_version_bound" for e in unapproved_run["events"])

        # 3. Approve v1 -> new run binds v1 (id, version, sha256).
        approved_v1 = services.teams.approve(v1["id"], actor="user:ana", channel="python")
        bound_run = services.runs.create(
            RunRequest(project_name="BoundV1", workflow_id="summarize_document",
                       inputs={"document_text": "Some document text."},
                       idempotency_key="team-bind-1"),
            bundle="case",
        )
        bound_events = [e for e in bound_run["events"] if e["event_type"] == "team_version_bound"]
        assert len(bound_events) == 1
        assert bound_events[0]["payload"]["version_id"] == approved_v1["id"]
        assert bound_events[0]["payload"]["id"] == approved_v1["id"]
        assert bound_events[0]["payload"]["version"] == 1
        assert bound_events[0]["payload"]["sha256"] == approved_v1["sha256"]

        # Idempotent replay does not duplicate team_version_bound.
        replay = services.runs.create(
            RunRequest(project_name="BoundV1", workflow_id="summarize_document",
                       inputs={"document_text": "Some document text."},
                       idempotency_key="team-bind-1"),
            bundle="case",
        )
        assert len([e for e in replay["events"] if e["event_type"] == "team_version_bound"]) == 1

        # 4. Change team.yaml and propose v2 (unapproved) -> approved() stays v1, existing run unchanged, new run binds v1.
        data = _read_team_yaml(bundle_dir)
        data["description"] = "Changed on disk"
        _write_team_yaml(bundle_dir, data)
        v2 = services.teams.propose("case", actor="agent:codex", channel="python")
        assert services.teams.approved("case")["id"] == approved_v1["id"]

        during_v2_proposal = services.runs.create(
            RunRequest(project_name="StillV1", workflow_id="summarize_document",
                       inputs={"document_text": "Another document."}),
            bundle="case",
        )
        ev_v2_prop = [e for e in during_v2_proposal["events"] if e["event_type"] == "team_version_bound"]
        assert len(ev_v2_prop) == 1
        assert ev_v2_prop[0]["payload"]["version_id"] == approved_v1["id"]

        # Approve v2 -> earlier run remains bound to v1, subsequent run binds v2.
        approved_v2 = services.teams.approve(v2["id"], actor="user:ana", channel="python")
        after_v2 = services.runs.create(
            RunRequest(project_name="NowV2", workflow_id="summarize_document",
                       inputs={"document_text": "Third document."}),
            bundle="case",
        )
        ev_v2 = [e for e in after_v2["events"] if e["event_type"] == "team_version_bound"]
        assert ev_v2[0]["payload"]["version_id"] == approved_v2["id"]
        assert ev_v2[0]["payload"]["version"] == 2
        assert ev_v2[0]["payload"]["sha256"] == approved_v2["sha256"]

        earlier = services.runs.get(bound_run["id"])
        earlier_ev = [e for e in earlier["events"] if e["event_type"] == "team_version_bound"]
        assert earlier_ev[0]["payload"]["version_id"] == approved_v1["id"]
    finally:
        services.runs.close()


def test_migration_015_applies_on_database_already_at_017(tmp_path: Path):
    source = ROOT / "migrations"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for path in source.glob("*.sql"):
        number = int(path.stem.split("_", 1)[0])
        if number != 15 and number <= 17:
            shutil.copyfile(path, migrations / path.name)

    db = Database(SimpleNamespace(db_path=tmp_path / "data" / "multiagent.db", migrations_dir=migrations))
    db.migrate()
    assert db.schema_version() == 17
    with db.connect() as conn:
        conn.execute("INSERT INTO projects(id,name,created_at) VALUES('p1','existing','2026-01-01T00:00:00Z')")
        conn.execute(
            "INSERT INTO decisions(id,title,body,proposed_by,status,created_at) "
            "VALUES('dec-000000000001','T','B','user:ana','proposed','2026-01-01T00:00:00Z')"
        )
        conn.commit()

    shutil.copyfile(source / "015_teams.sql", migrations / "015_teams.sql")
    assert db.migrate() == ["015_teams.sql"]
    assert db.migrate() == []
    assert db.schema_version() == 17
    with db.connect() as conn:
        assert conn.execute("SELECT name FROM projects WHERE id='p1'").fetchone()[0] == "existing"
        assert conn.execute("SELECT title FROM decisions WHERE id='dec-000000000001'").fetchone()[0] == "T"
        assert conn.execute("SELECT COUNT(*) FROM team_versions").fetchone()[0] == 0
        applied = [row[0] for row in conn.execute("SELECT version FROM schema_version ORDER BY version")]
        assert 15 in applied and 16 in applied and 17 in applied
