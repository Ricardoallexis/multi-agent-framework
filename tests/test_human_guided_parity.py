# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The three public doors share one human-guided validation and wait state."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from multiagent import cli
from test_gui_runs import CONTENT, env, get, guided_run, post


class _ClientContext:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *_):
        return False


def invoke(monkeypatch, capsys, client, args):
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    try:
        cli.main(args)
    except SystemExit as exc:
        assert exc.code == 1
    output = capsys.readouterr()
    return output.out or output.err


def test_three_doors_check_and_submit_without_check_side_effects(env, monkeypatch, capsys, tmp_path):
    run_id = guided_run(env)
    api = f"/api/v1/runs/{run_id}"
    gui = f"/runs/{run_id}"
    next_api = env.client.get(api + "/human-next").json()
    assert next_api == get(env, gui + "/human-next").json()
    assert next_api["expected_schema"]["type"] == "object"
    assert next_api["request_path"] and not Path(next_api["request_path"]).is_absolute()
    assert (env.services.settings.data_dir / next_api["request_path"]).is_file()
    out = tmp_path / "prompt.txt"
    invoke(monkeypatch, capsys, env.client, ["human-next", run_id, "--out", str(out)])
    assert out.read_text(encoding="utf-8") == next_api["prompt"]

    before = env.services.runs.get(run_id)["events"]
    raw_before = list(env.services.settings.runs_dir.rglob("*_HUMAN_RAW*"))
    bad_api = env.client.post(api + "/human-check", json={"raw_response": "{}"}).json()
    bad_gui = post(env, gui + "/human-check", {"raw_response": "{}"}).json()
    bad_cli = json.loads(invoke(monkeypatch, capsys, env.client,
                                ["human-check", run_id, "--file", str(_response_file(tmp_path, "{}"))]))
    assert bad_api == bad_gui == bad_cli
    assert bad_api["ok"] is False and bad_api["problems"]
    assert all(problem["path"] != "$" for problem in bad_api["problems"])

    response = json.dumps(CONTENT)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(response))
    good_api = env.client.post(api + "/human-check", json={"raw_response": response}).json()
    good_gui = post(env, gui + "/human-check", {"raw_response": response}).json()
    good_cli = json.loads(invoke(monkeypatch, capsys, env.client, ["human-check", run_id, "--file", "-"]))
    assert good_api == good_gui == good_cli == {"ok": True, "contract": next_api["expected_contract"], "problems": []}
    assert env.services.runs.get(run_id)["events"] == before
    assert list(env.services.settings.runs_dir.rglob("*_HUMAN_RAW*")) == raw_before

    body = {"raw_response": response, "provider": "fixture", "model": "synthetic", "prompt_used": "Edited prompt", "notes": "Checked"}
    assert env.client.post(api + "/human-submit", json=body).json()["waiting_reason"] == "review"
    stale_api = env.client.post(api + "/human-submit", json=body)
    stale_gui = post(env, gui + "/human-submit", body)
    assert stale_api.status_code == stale_gui.status_code == 409
    assert stale_api.json()["detail"] == stale_gui.json()["detail"]
    assert stale_api.json()["detail"]["code"] == "invalid_state"
    assert stale_api.json()["detail"]["details"] == {"reason": "not_waiting"}
    stale_cli = invoke(monkeypatch, capsys, env.client,
                       ["human-submit", run_id, "--file", str(_response_file(tmp_path, response))])
    assert "HTTP 409: invalid_state:" in stale_cli and '"reason": "not_waiting"' in stale_cli


def _response_file(tmp_path, text):
    path = tmp_path / "response.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_stdin_gui_submit_and_api_research_default(env, monkeypatch, capsys):
    response = json.dumps(CONTENT)
    gui_id = guided_run(env)
    body = {"raw_response": response, "provider": "fixture", "prompt_used": "Changed", "notes": "Synthetic"}
    assert post(env, f"/runs/{gui_id}/human-submit", body).status_code == 200
    with env.services.system.store.db.connect() as conn:
        saved = conn.execute("SELECT provider, model, prompt_used, notes FROM human_step_submissions WHERE run_id=?",
                             (gui_id,)).fetchone()
    assert tuple(saved) == ("fixture", "", "Changed", "Synthetic")
    cli_id = guided_run(env)
    shown_prompt = env.client.get(f"/api/v1/runs/{cli_id}/human-next").json()["prompt"]
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(response))
    state = json.loads(invoke(monkeypatch, capsys, env.client,
                              ["human-submit", cli_id, "--file", "-", "--provider", "fixture"]))
    assert state["waiting_reason"] == "review"
    with env.services.system.store.db.connect() as conn:
        saved_prompt = conn.execute("SELECT prompt_used FROM human_step_submissions WHERE run_id=?",
                                    (cli_id,)).fetchone()[0]
    assert saved_prompt == shown_prompt
    research_id = guided_run(env)
    submitted = env.client.post(f"/api/v1/runs/{research_id}/research-submit", json={"raw_response": response})
    assert submitted.status_code == 200
    event = [e for e in submitted.json()["events"] if e["event_type"] == "human_step_submitted"][-1]
    assert event["payload"]["provider"] == "human_web"
    alias_id = guided_run(env)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(response))
    assert json.loads(invoke(monkeypatch, capsys, env.client,
                             ["research-submit", alias_id, "--file", "-"]))["waiting_reason"] == "review"


@pytest.mark.parametrize("stale", ["cancelled", "completed", "wrong_attempt"])
def test_stale_wait_has_same_error_at_every_door(env, monkeypatch, capsys, tmp_path, stale):
    run_id = guided_run(env)
    response = json.dumps(CONTENT)
    if stale == "cancelled":
        assert env.client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 200
    elif stale == "completed":
        assert env.client.post(f"/api/v1/runs/{run_id}/human-submit",
                               json={"raw_response": response}).status_code == 200
        assert env.client.post(f"/api/v1/runs/{run_id}/approve").json()["status"] == "completed"
    else:
        env.services.system.store.update_run(run_id, waiting_attempt=999)
    body = {"raw_response": response}
    api = env.client.post(f"/api/v1/runs/{run_id}/human-submit", json=body)
    gui = post(env, f"/runs/{run_id}/human-submit", body)
    assert api.status_code == gui.status_code == 409
    assert api.json()["detail"] == gui.json()["detail"]
    assert api.json()["detail"]["details"] == {"reason": "not_waiting"}
    cli_error = invoke(monkeypatch, capsys, env.client,
                       ["human-submit", run_id, "--file", str(_response_file(tmp_path, response))])
    assert "HTTP 409: invalid_state:" in cli_error and '"reason": "not_waiting"' in cli_error


def test_check_reports_unsupported_backend(env, monkeypatch):
    run_id = guided_run(env)
    monkeypatch.setattr(env.services.system.run_service.engine, "check_human_step", None)
    response = env.client.post(f"/api/v1/runs/{run_id}/human-check", json={"raw_response": "{}"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_state"
    assert response.json()["detail"]["details"] == {"reason": "unsupported"}
