# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARR-T02: advance a plan by the id of its approved proposal (service, API, CLI and GUI route)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices, ServiceError
from tests.gui_text import ui_text

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture
def env(tmp_path):
    general = tmp_path / "general"
    shutil.copytree(GENERAL_BUNDLE, general)
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        execution_backend="mock", mock_mode=True, gemini_api_key="",
                        openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"general": general})
    services.plan_autoadvance = None      # ARR3-T03: these tests move the plan by hand; the automatic advance has its own file
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token, tmp=tmp_path)
    services.runs.close()


def saved_proposal(services):
    document = services.planning.propose("manual", {"group_id": "launch", "goal": "Launch product", "tasks": [
        {"id": "technical", "title": "Technical review", "workflow": "social_post", "depends_on": [],
         "inputs": {"topic": "Launch"}},
        {"id": "commercial", "title": "Commercial review", "workflow": "social_post",
         "depends_on": ["technical"]}]})
    return services.plan_proposals.save(document, actor="user:ana")


def approved(services):
    saved = saved_proposal(services)
    services.plan_proposals.approve(saved["id"], actor="user:ana", channel="python", expected_revision=1)
    return saved["id"]


def finish_run(services, proposal_id, task_id):
    plan = services.plan_proposals.get(proposal_id)["proposal"]["plan"]
    run_id = next(t["run"]["id"] for t in services.plans.status(plan)["tasks"] if t["id"] == task_id)
    for _ in range(5):
        state = services.runs.get(run_id)["status"]
        if state == "completed":
            return
        if state == "waiting_human":
            services.runs.approve(run_id)
        else:
            assert services.system.worker.process_once()
    assert services.runs.get(run_id)["status"] == "completed"


def by_id(result):
    return {task["id"]: task for task in result["tasks"]}


# --- service ----------------------------------------------------------------------------------------------------

def test_advance_after_a_finished_task_creates_the_next_run(env):
    services = env.services
    proposal_id = approved(services)
    finish_run(services, proposal_id, "technical")
    first = services.plan_proposals.advance(proposal_id, actor="user:ana")
    assert by_id(first)["technical"]["state"] == "submitted"
    assert [item["task_id"] for item in first["advanced"]] == ["technical"]
    services.tasks.transition("technical", "accept", actor="user:ana")
    second = services.plan_proposals.advance(proposal_id, actor="user:ana")
    assert second["proposal_id"] == proposal_id and second["nothing_to_advance"] is False
    assert by_id(second)["commercial"]["run"] is not None
    assert [item["task_id"] for item in second["advanced"]] == ["commercial"]


def test_advance_a_proposal_that_is_not_approved_is_a_conflict(env):
    saved = saved_proposal(env.services)
    with pytest.raises(ServiceError) as caught:
        env.services.plan_proposals.advance(saved["id"], actor="user:ana")
    assert caught.value.code == "invalid_state" and caught.value.details["reason"] == "not_approved"
    with pytest.raises(ServiceError) as missing:
        env.services.plan_proposals.advance("plp-000000000000", actor="user:ana")
    assert missing.value.code == "not_found"


def test_advance_with_nothing_to_advance_answers_empty_without_error(env):
    proposal_id = approved(env.services)   # approving already started the first run
    result = env.services.plan_proposals.advance(proposal_id, actor="user:ana")
    assert result["advanced"] == [] and result["nothing_to_advance"] is True
    assert result["status"] == "running"


# --- API and CLI ------------------------------------------------------------------------------------------------

def test_api_and_cli_with_proposal_or_file_give_the_same_plan(env, monkeypatch, capsys):
    proposal_id = approved(env.services)
    plan = env.services.plan_proposals.get(proposal_id)["proposal"]["plan"]
    path = env.tmp / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    class ClientContext:
        def __enter__(self):
            return env.client

        def __exit__(self, *_args):
            return False
    monkeypatch.setattr(cli, "api_client", ClientContext)

    cli.main(["plan", "advance", str(path), "--actor", "user:ana"])
    by_file = json.loads(capsys.readouterr().out)
    cli.main(["plan", "advance", "--proposal", proposal_id, "--actor", "user:ana"])
    by_proposal = json.loads(capsys.readouterr().out)
    for key in ("group_id", "status", "tasks"):
        assert by_proposal[key] == by_file[key], key
    assert by_proposal["proposal_id"] == proposal_id and by_proposal["nothing_to_advance"] is True

    response = env.client.post(f"/api/v1/plan-proposals/{proposal_id}/advance",
                               json={"actor": "user:ana", "channel": "api"})
    assert response.status_code == 200 and response.json()["tasks"] == by_file["tasks"]
    missing = env.client.post("/api/v1/plan-proposals/plp-000000000000/advance", json={"actor": "user:ana"})
    assert missing.status_code == 404

    for argv in (["plan", "advance", str(path), "--proposal", proposal_id, "--actor", "user:ana"],
                 ["plan", "advance", "--actor", "user:ana"]):
        with pytest.raises(SystemExit):   # a file and --proposal together, or neither
            cli.main(argv)


# --- the GUI route ----------------------------------------------------------------------------------------------

def gui(env, proposal_id, **kwargs):
    return env.client.post(f"/gui/api/plan-proposals/{proposal_id}/advance",
                           headers={"Authorization": f"Bearer {env.token}"}, **kwargs)


def test_gui_route_needs_the_token_and_fixes_the_actor(env):
    proposal_id = approved(env.services)
    assert env.client.post(f"/gui/api/plan-proposals/{proposal_id}/advance").status_code == 401
    wrong = env.client.post(f"/gui/api/plan-proposals/{proposal_id}/advance",
                            headers={"Authorization": "Bearer wrong"})
    assert wrong.status_code == 401
    seen = {}
    real = env.services.plan_proposals.advance

    def spy(pid, **kwargs):
        seen.update(kwargs)
        return real(pid, **kwargs)
    env.services.plan_proposals.advance = spy
    ok = gui(env, proposal_id, json={"actor": "user:mallory", "channel": "cli"})
    assert ok.status_code == 200, ok.text
    assert seen == {"actor": "user:operator", "channel": "gui"}
    assert ok.json()["nothing_to_advance"] is True


def test_gui_route_conflicts_on_an_unapproved_proposal(env):
    saved = saved_proposal(env.services)
    response = gui(env, saved["id"], json={})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "invalid_state"


# --- the view ---------------------------------------------------------------------------------------------------

def test_the_advance_button_is_wired_and_helped():
    view = (JS / "plans.js").read_text(encoding="utf-8")
    help_text = ui_text(JS / "help-text.js")
    for expected in ('"/advance"', "Avanzar plan", "<${Confirm}", 'topic="plans.advance"', 'helpFor("plans.advance")'):
        assert expected in view, expected
    assert '"plans.advance":' in help_text
    for bad in ("innerHTML", "dangerouslySetInnerHTML", "__html", "eval(", "style="):
        assert bad not in view, bad
    assert "user:operator" not in view and not re.search(r"\b(actor|channel)\s*:", view)


@needs_node
def test_advance_summary_and_node_check():
    for name in ("plans.js", "help-text.js"):
        checked = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert checked.returncode == 0, (name, checked.stderr)
    source = (JS / "plans.js").read_text(encoding="utf-8")
    body = re.search(r"export function advanceSummary\(result\) \{.*?\n\}\n", source, re.S).group(0)
    script = body.replace("export ", "") + (
        "console.log(JSON.stringify([advanceSummary(null), advanceSummary({advanced: []}),"
        " advanceSummary({advanced: [{task_id: 'a'}, {task_id: 'b'}]})]));")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["No había nada que avanzar.", "No había nada que avanzar.",
                                      "Plan avanzado: a, b."]
