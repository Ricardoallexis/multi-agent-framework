# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARR-T04: every approval says who gave it and through which door (``by`` and ``channel``)."""
from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

from multiagent import cli
from multiagent.contracts import WaitingReason
from multiagent.document_actions import ActionKind, Approval
from multiagent.document_trust import Provenance, SourceKind
from multiagent.gui.runs import EXTERNAL_ACTOR, _history
from multiagent.tools.approvals import pause_for_result

from test_gui_runs import CONTENT, env, get, guided_run, headers, post  # noqa: F401  (``env`` is a fixture)
from test_tool_approvals import setup


def review_run(env):
    run_id = guided_run(env)
    assert post(env, f"/runs/{run_id}/human-submit", {"raw_response": json.dumps(CONTENT)}).status_code == 200
    return run_id


def approved_events(env, run_id):
    return [e["payload"] for e in env.services.runs.get(run_id)["events"] if e["event_type"] == "human_approved"]


def test_the_gui_records_the_operator_and_the_gui_channel_on_an_approval(env):
    run_id = review_run(env)
    view = post(env, f"/runs/{run_id}/approve").json()
    (payload,) = approved_events(env, run_id)
    assert (payload["by"], payload["channel"]) == (EXTERNAL_ACTOR, "gui")
    entry = [h for h in view["history"] if h["kind"] == "approved"][0]
    assert (entry["actor"], entry["channel"]) == (EXTERNAL_ACTOR, "gui")


def test_the_gui_records_the_actor_on_a_change_request(env):
    run_id = review_run(env)
    view = post(env, f"/runs/{run_id}/changes", {"feedback": "Mas corto"}).json()
    entry = [h for h in view["history"] if h["kind"] == "changes_requested"][0]
    assert (entry["actor"], entry["channel"]) == (EXTERNAL_ACTOR, "gui")


def test_the_api_records_its_channel_on_an_approval(env):
    run_id = review_run(env)
    assert env.client.post(f"/api/v1/runs/{run_id}/approve").status_code == 200
    (payload,) = approved_events(env, run_id)
    assert (payload["by"], payload["channel"]) == ("api", "api")      # nobody authenticates the API: by is the door


def test_a_declared_actor_is_kept_and_an_unusable_one_falls_back_to_the_channel(env):
    run_id = review_run(env)
    env.services.runs.approve(run_id, by="person:ana", channel="api")
    assert [(p["by"], p["channel"]) for p in approved_events(env, run_id)] == [("person:ana", "api")]


def test_the_command_line_approval_through_the_token_route_records_the_gui_actor(env, monkeypatch, capsys):
    run_id = review_run(env)
    monkeypatch.setattr(cli, "api_client", lambda: contextlib.nullcontext(env.client))
    monkeypatch.setattr(cli, "get_settings", lambda: env.services.settings)
    cli._approve_with_token(run_id)
    (payload,) = approved_events(env, run_id)
    assert (payload["by"], payload["channel"]) == (EXTERNAL_ACTOR, "gui")


def test_the_command_line_approval_through_the_api_records_the_api_channel(env, monkeypatch, capsys):
    run_id = review_run(env)
    monkeypatch.setattr(cli, "api_client", lambda: contextlib.nullcontext(env.client))
    cli.cmd_approve(SimpleNamespace(run_id=run_id))
    (payload,) = approved_events(env, run_id)
    assert (payload["by"], payload["channel"]) == ("api", "api")


def test_a_tool_approval_records_by_and_channel(tmp_path):
    store, service, invoker, request, count = setup(tmp_path)
    write = Approval(Provenance(SourceKind.USER, ref="user:1"), frozenset({ActionKind.WRITE}),
                     frozenset({"synthetic.txt"}))
    denied = invoker.invoke(request, grants={"tool:mock.delete"}, evidence=[write])
    assert pause_for_result(denied).waiting_reason == WaitingReason.TOOL_APPROVAL.value
    store.hold_tool_approval(denied.permission_request)
    run_id = request.context.run_id
    service.approve(run_id, by="user:operator", channel="gui", person=True,
                    permission_request_id=denied.permission_request.permission_request_id)
    (event,) = [e for e in store.get_run(run_id)["events"] if e["event_type"] == "tool_approval_approved"]
    assert (event["payload"]["by"], event["payload"]["channel"]) == ("user:operator", "gui")


def test_an_old_event_without_an_actor_reads_as_unknown():
    old = {"seq": 1, "ts": "2026-01-01T00:00:00+00:00", "event_type": "human_approved",
           "payload": {"step_id": "create", "attempt": 1, "artifact_id": 1}}
    older_revision = {"seq": 2, "ts": "2026-01-01T00:00:01+00:00", "event_type": "human_revision",
                      "payload": {"step_id": "create", "attempt": 1, "mode": "revise", "feedback": "x", "by": None}}
    approved, revision = _history([old, older_revision])
    assert (approved["kind"], approved["actor"], approved["channel"]) == ("approved", "unknown", "")
    assert (revision["kind"], revision["actor"]) == ("changes_requested", "unknown")
