# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""S13.B2: decision lifecycle, run audit, and Python/HTTP/CLI parity."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


@pytest.fixture
def services(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    app = ApplicationServices.create(settings, bundles={"team": BUNDLE}, dry_run=True)
    yield app
    app.runs.close()


def proposal(services, **overrides):
    return services.decisions.propose(title="Keep the concise plan", body="Use the short version.",
                                      actor="agent:codex/gpt", **overrides)


def error(call, *args, **kwargs):
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


def test_migration_and_transitions(services):
    with services.system.db.connect() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='decisions'").fetchone()
    first = proposal(services, project="demo", task_id="T1", team_id="alpha")
    assert first["status"] == "proposed" and first["proposed_by"] == "agent:codex/gpt"
    assert not first["active"]
    refused = error(services.decisions.accept, first["id"], actor="agent:codex/gpt")
    assert (refused.code, refused.status, refused.details["reason"]) == ("invalid_state", 409, "confirmation_required")
    accepted = services.decisions.accept(first["id"], actor="user:ana", outcome="go")
    assert accepted["active"] and accepted["valid_from"] and accepted["decided_by"] == "user:ana"
    assert services.decisions.list(active=True)["count"] == 1
    wrong = error(services.decisions.reject, first["id"], actor="user:ana")
    assert wrong.code == "invalid_state" and wrong.details["reason"] == "invalid_transition"
    replacement = services.decisions.supersede(first["id"], actor="user:ana", title="New plan", body="Replace it.")
    assert replacement["status"] == "accepted" and replacement["active"]
    assert services.decisions.get(first["id"])["superseded_by"] == replacement["id"]
    assert services.decisions.get(first["id"])["status"] == "superseded"
    assert [x["id"] for x in services.decisions.list(active=True)["decisions"]] == [replacement["id"]]
    assert error(services.decisions.supersede, first["id"], actor="user:ana", title="No", body="No").code == "invalid_state"


def test_migration_adds_decisions_without_changing_existing_rows(tmp_path):
    source = Path(__file__).parents[1] / "migrations"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for path in source.glob("*.sql"):
        if int(path.stem.split("_", 1)[0]) <= 14:
            shutil.copyfile(path, migrations / path.name)
    db = Database(SimpleNamespace(db_path=tmp_path / "data" / "multiagent.db", migrations_dir=migrations))
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO projects(id,name,created_at) VALUES('p1','old','2026-01-01T00:00:00Z')")
        conn.commit()
    shutil.copyfile(source / "016_decisions.sql", migrations / "016_decisions.sql")
    assert db.migrate() == ["016_decisions.sql"]
    assert db.migrate() == []
    with db.connect() as conn:
        assert conn.execute("SELECT name FROM projects WHERE id='p1'").fetchone()[0] == "old"
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_rejection_expiry_future_validity_and_existing_replacement(services):
    rejected = services.decisions.reject(proposal(services)["id"], actor="user:ana", outcome="too costly")
    assert rejected["status"] == "rejected" and not rejected["active"]
    assert error(services.decisions.accept, rejected["id"], actor="user:ana").details["reason"] == "invalid_transition"
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    expired = services.decisions.accept(proposal(services)["id"], actor="user:ana",
                                        valid_from=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                                        valid_until=past)
    assert expired["status"] == "expired" and not expired["active"]
    already_expired = services.decisions.accept(proposal(services, valid_until=past)["id"], actor="user:ana")
    assert already_expired["status"] == "expired" and not already_expired["active"]
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    scheduled = services.decisions.accept(proposal(services)["id"], actor="user:ana", valid_from=future)
    assert scheduled["status"] == "accepted" and not scheduled["active"]
    old = services.decisions.accept(proposal(services)["id"], actor="user:ana")
    new = proposal(services)
    assert services.decisions.supersede(old["id"], actor="user:ana", replacement_id=new["id"])["id"] == new["id"]
    assert services.decisions.list(active=True)["count"] == 1


def test_invalid_text_actor_and_dates(services):
    for changes in ({"title": "two\nlines"}, {"body": "x" * 2001}, {"actor": "unknown"},
                    {"valid_until": "2026-01-01"}, {"attempt": 0}):
        args = {"title": "t", "body": "b", "actor": "user:ana", **changes}
        assert error(services.decisions.propose, **args).code == "invalid_request"
    item = proposal(services)
    assert error(services.decisions.get, "../x").code == "invalid_request"
    assert error(services.decisions.get, "dec-000000000000").code == "not_found"
    assert error(services.decisions.reject, item["id"], actor="person:ana").details["reason"] == "confirmation_required"


@pytest.mark.parametrize("action,expected,outcome", [("approve", "accepted", "approve"),
                                                       ("request_changes", "rejected", "changes_requested"),
                                                       ("reject", "rejected", "reject")])
def test_run_reviews_automatically_record_decisions(services, action, expected, outcome):
    run = services.runs.create(RunRequest(project_name="Synthetic review", workflow_id="spec_review",
                                          inputs={"spec_text": "Users reset a password by email."}), bundle="team")
    assert services.runs._bundle_runtime("team").worker.process_once()
    waiting = services.runs.get(run["id"])
    if action == "request_changes":
        services.runs.request_changes(run["id"], "Explain the risk", by="user:ana")
    elif action == "reject":
        services.runs.reject(run["id"], "Out of scope", by="user:ana")
    else:
        services.runs.approve(run["id"], by="user:ana")
    entries = services.decisions.list(run_id=run["id"])["decisions"]
    assert len(entries) == 1
    entry = entries[0]
    assert (entry["status"], entry["outcome"], entry["decided_by"]) == (expected, outcome, "user:ana")
    assert (entry["run_id"], entry["step_id"], entry["attempt"]) == (
        run["id"], waiting["waiting_step"], waiting["waiting_attempt"])


def test_failed_audit_leaves_review_and_run_event(services, monkeypatch):
    run = services.runs.create(RunRequest(project_name="Synthetic review", workflow_id="spec_review",
                                          inputs={"spec_text": "Users reset a password by email."}), bundle="team")
    services.runs._bundle_runtime("team").worker.process_once()
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(services.decisions, "record_review", fail)
    assert services.runs.approve(run["id"])["status"] == "queued"
    assert any(e["event_type"] == "decision_record_failed" for e in services.runs.get(run["id"])["events"])


def test_non_content_wait_records_its_reason(services):
    run = services.runs.create(RunRequest(project_name="Synthetic escalation", workflow_id="spec_review",
                                          inputs={"spec_text": "A synthetic specification."}), bundle="team")
    runtime = services.runs._bundle_runtime("team")
    runtime.store.update_run(run["id"], status="waiting_human", waiting_reason="escalation_approval",
                             waiting_step="assess_risks", waiting_attempt=1)
    runtime.store.db.log_event(run["id"], "escalation_requested", {
        "step_id": "assess_risks", "attempt": 1, "from_level": 1, "to_level": 2, "to_model": "mock"})
    assert services.runs.reject(run["id"], "No escalation")["status"] == "failed"
    entry = services.decisions.list(run_id=run["id"])["decisions"][0]
    assert entry["outcome"] == "escalation_approval:reject" and entry["status"] == "rejected"


class _ClientContext:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *exc):
        return False


def invoke(monkeypatch, capsys, client, args):
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    cli.main(args)
    return json.loads(capsys.readouterr().out)


def test_python_http_cli_parity(services, monkeypatch, capsys):
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        created = invoke(monkeypatch, capsys, client, ["decision", "propose", "--title", "Plan", "--text", "Do it",
                                                      "--actor", "agent:codex", "--project", "demo"])
        assert created == services.decisions.get(created["id"])
        assert client.get(f"/api/v1/decisions/{created['id']}").json() == created
        assert invoke(monkeypatch, capsys, client, ["decision", "list", "--project", "demo"]) == \
            client.get("/api/v1/decisions", params={"project": "demo"}).json() == services.decisions.list(project="demo")
        refused = client.post(f"/api/v1/decisions/{created['id']}/accept", json={"actor": "agent:codex"})
        assert refused.status_code == 409 and refused.json()["detail"]["details"]["reason"] == "confirmation_required"
        with pytest.raises(SystemExit) as exit_status:
            cli.main(["decision", "accept", created["id"], "--actor", "agent:codex"])
        cli_error = capsys.readouterr().err
        assert exit_status.value.code == 1 and "invalid_state" in cli_error and "confirmation_required" in cli_error
        accepted = invoke(monkeypatch, capsys, client, ["decision", "accept", created["id"], "--actor", "user:ana"])
        assert accepted == services.decisions.get(created["id"])
        assert invoke(monkeypatch, capsys, client, ["decision", "list", "--active"])["count"] == 1
        new = client.post(f"/api/v1/decisions/{created['id']}/supersede", json={
            "actor": "user:ana", "title": "New plan", "body": "Replace it"})
        assert new.status_code == 200 and new.json()["active"]
        assert invoke(monkeypatch, capsys, client, ["decision", "show", created["id"]])["status"] == "superseded"
        to_reject = invoke(monkeypatch, capsys, client, ["decision", "propose", "--title", "No",
                                                        "--text", "Skip it", "--actor", "user:ana"])
        rejected = invoke(monkeypatch, capsys, client, ["decision", "reject", to_reject["id"],
                                                        "--actor", "user:ana", "--outcome", "too costly"])
        assert rejected == services.decisions.get(to_reject["id"])
        to_replace = invoke(monkeypatch, capsys, client, ["decision", "propose", "--title", "Third",
                                                         "--text", "Try this", "--actor", "user:ana"])
        replacement = invoke(monkeypatch, capsys, client, ["decision", "supersede", new.json()["id"],
                                                           "--actor", "user:ana", "--replacement-id", to_replace["id"]])
        assert replacement == client.get(f"/api/v1/decisions/{to_replace['id']}").json()
