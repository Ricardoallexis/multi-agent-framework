# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Limit notices use observed reset times and eligible connection alternatives."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from multiagent.gui.runs import run_view
from multiagent.services.limits import limit_notice


ROWS = [
    {"id": "gemini", "kind": "model", "available": False, "enabled": True,
     "resets_at": "2026-11-20T18:00:00Z"},
    {"id": "ollama", "kind": "model", "available": True, "enabled": True},
    {"id": "openai", "kind": "model", "available": False, "enabled": True},
    {"id": "claude_cli", "kind": "executor", "available": True, "enabled": True},
    {"id": "codex_cli", "kind": "executor", "available": False, "enabled": True},
]


def services(rows=ROWS):
    connections = SimpleNamespace(list=lambda: {"connections": rows})
    definitions = SimpleNamespace(read_workflow=lambda *_args, **_kwargs: {"steps": []})
    return SimpleNamespace(connections=connections, definitions=definitions,
                           system=SimpleNamespace(catalog=SimpleNamespace(model=lambda *_: SimpleNamespace(provider="gemini"))))


def test_limit_uses_retry_time_first_and_only_eligible_alternatives():
    instant = datetime(2026, 11, 20, 17, 30, tzinfo=timezone.utc)
    notice = limit_notice(services(), "gemini", retry_at=instant)
    assert notice == {"code": "limit_reached", "resets_at": instant.isoformat(), "target": "gemini",
                      "suggestions": ["ollama", "claude_cli"]}


def test_limit_with_no_observed_time_keeps_it_unknown():
    rows = [{**row, "resets_at": None} for row in ROWS]
    assert limit_notice(services(rows), "gemini")["resets_at"] is None


def test_failed_run_shows_final_quota_attempt_and_its_reset():
    run = {"id": "fake-run", "workflow_id": "ask_once", "status": "failed", "request": {},
           "events": [{"seq": 1, "ts": "2026-11-20T17:00:00Z", "event_type": "model_attempt",
                       "payload": {"model": "cloud_fast", "provider": "gemini", "error_type": "QuotaExceeded",
                                   "retry_at": "2026-11-20T18:00:00Z"}}]}
    view = run_view(services(), run)
    assert view["limit"]["code"] == "limit_reached"
    assert view["limit"]["resets_at"] == "2026-11-20T18:00:00Z"
    assert "gemini" not in view["limit"]["suggestions"]


def test_task_manual_continuity_asks_instead_of_handing_off(tmp_path):
    from test_quota_handoff import at, hit_limit, make_world

    world = make_world(tmp_path, continuity_mode="semi_auto")
    task = world.services.tasks.get_task("T1")
    world.services.tasks._edit_continuity("T1", {"mode": "manual"}, actor="test",
                                          expected_revision=task["revision"])
    hit_limit(world, reset=at(8))
    decided = world.services.executors.decide_holds()
    assert decided["delegated"] == []
    assert decided["asked"][0]["why"] == "manual_mode"


def test_unconfirmed_run_keeps_its_old_fingerprint_and_derived_grants_are_not_part_of_it():
    from multiagent.db.store import _canonical_request_fingerprint
    from multiagent.run_request import RunRequest

    plain = RunRequest(workflow_id="w", project_name="Project", inputs={})
    confirmed = RunRequest(workflow_id="w", project_name="Project", inputs={}, confirm_cloud=True)
    assert _canonical_request_fingerprint(plain) != _canonical_request_fingerprint(confirmed)
    stored = confirmed.model_dump(mode="json")
    stored.pop("effective_permissions"), stored.pop("authorized_data_classes")
    assert _canonical_request_fingerprint(RunRequest.model_validate(stored)) == _canonical_request_fingerprint(confirmed)


def test_cli_run_sends_confirm_cloud_only_when_asked(monkeypatch, capsys):
    from multiagent import cli
    sent = []
    monkeypatch.setattr(cli, "_api_json", lambda method, path, **kwargs: sent.append((path, kwargs["json_body"])) or {})
    cli.main(["run", "--project", "synthetic", "--workflow", "ask_once", "--mode", "cloud", "--confirm-cloud"])
    cli.main(["run", "--project", "synthetic", "--workflow", "ask_once"])
    capsys.readouterr()
    assert [path for path, _ in sent] == ["/api/v1/runs", "/api/v1/runs"]
    assert sent[0][1]["confirm_cloud"] is True and "confirm_cloud" not in sent[1][1]
