# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""RWD-T03: read-only pending inbox API and shell client."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")


@pytest.fixture
def env(tmp_path):
    bundle = tmp_path / "general"
    shutil.copytree(GENERAL_BUNDLE, bundle)
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"general": bundle}, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def get(env):
    return env.client.get("/gui/api/pending", headers={"Authorization": f"Bearer {env.token}"})


def test_pending_includes_user_approval(env):
    approval = env.services.user_approvals.request(
        kind="secret_send", destination={"provider": "fake", "model": "mock"},
        location={"run": "synthetic", "step": "create"}, secret_types=["token"],
        masked_sample="ab…yz", sha256="a" * 64)
    body = get(env).json()
    item = next(item for item in body["items"] if item["id"] == "approval:" + approval["id"])
    assert item["href"] == "#approvals/" + approval["id"]
    assert body["count"] == len(body["items"])


def waiting_run(env):
    response = env.client.post("/gui/api/runs", headers={"Authorization": f"Bearer {env.token}"}, json={
        "workflow_id": "social_post", "project_name": "Synthetic review",
        "inputs": {"topic": "Synthetic", "platform": "Test", "audience": "Test", "instructions": "No external calls"},
        "execution_mode": "auto",
    })
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    env.services.system.store.update_run(run_id, status="waiting_human", waiting_reason="review",
                                         waiting_step="create", waiting_attempt=1)
    return run_id


def create_plan(env):
    document = env.services.planning.propose("manual", {
        "group_id": "pending-plan",
        "goal": "Synthetic proposed plan",
        "tasks": [{"id": "review", "title": "Synthetic review", "workflow": "social_post",
                   "depends_on": [], "objective": "Review a synthetic item"}],
    })
    return env.services.plan_proposals.save(document, actor="agent:synthetic", channel="test")


def test_endpoint_lists_each_pending_kind_with_a_local_link_and_does_not_mutate(env, monkeypatch):
    run_id = waiting_run(env)
    team = env.services.teams.propose("general", actor="agent:synthetic", channel="test")
    proposal = create_plan(env)
    env.services.tasks.create_group("pending-human", "Pending human task", actor="user:operator")
    task = env.services.tasks.create_task("pending-human", "question", "Synthetic task question", actor="user:operator")
    task = env.services.tasks.set_wait("question", who="user", question="Choose a synthetic option",
                                       actor="agent:synthetic", expected_revision=task["revision"])
    monkeypatch.setattr("multiagent.gui.pending_api.build_cards", lambda _services: [{
        "task_id": "continuity-question", "title": "Synthetic continuity task", "reason": "credits_exhausted",
        "since": "2026-11-18T12:00:00Z", "decision": "pending",
    }])

    before = {
        "run": env.services.runs.get(run_id),
        "team": env.services.teams.get(team["id"]),
        "plan": env.services.plan_proposals.get(proposal["id"]),
        "task": env.services.tasks.get_task(task["id"]),
    }
    response = get(env)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "count"} and body["count"] == len(body["items"]) == 5
    items = {item["type"]: item for item in body["items"]}
    assert set(items) == {"run", "team", "plan", "continuity", "task"}
    assert items["run"]["id"] == "run:" + run_id and items["run"]["reason"] == "review"
    assert items["run"]["href"] == "#new/run/" + run_id
    # The plan opens its detail view until CHT-T06 adds #new/plan/<id>; the team opens its card thread (CHT-T05).
    assert items["team"]["id"] == "team:" + team["id"] and items["team"]["href"] == "#new/team/" + team["id"]
    assert items["plan"]["id"] == "plan:" + proposal["id"] and items["plan"]["href"] == "#new/plan/" + proposal["id"]
    assert items["continuity"]["href"] == "#continuity"
    assert items["task"]["href"] == "#task_waiting" and items["task"]["reason"] == "Choose a synthetic option"
    assert all({"id", "type", "title", "reason", "since", "href"} == set(item) for item in body["items"])
    assert env.services.runs.get(run_id) == before["run"]
    assert env.services.teams.get(team["id"]) == before["team"]
    assert env.services.plan_proposals.get(proposal["id"]) == before["plan"]
    assert env.services.tasks.get_task(task["id"]) == before["task"]


def test_endpoint_requires_the_gui_token_and_drops_a_run_that_stops_waiting(env):
    run_id = waiting_run(env)
    assert env.client.get("/gui/api/pending").status_code == 401
    assert env.client.get("/gui/api/pending", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert any(item["id"] == "run:" + run_id for item in get(env).json()["items"])

    env.services.system.store.update_run(run_id, status="completed")
    response = get(env)
    assert response.status_code == 200
    assert all(item["id"] != "run:" + run_id for item in response.json()["items"])


def test_shell_wires_help_and_requests_notifications_only_from_an_opt_in_click():
    pending = (JS / "pending.js").read_text(encoding="utf-8")
    model = (JS / "pending-model.js").read_text(encoding="utf-8")
    shell = (JS / "shell.js").read_text(encoding="utf-8")
    sidebar = (JS / "sidebar.js").read_text(encoding="utf-8")
    help_text = (JS / "help-text.js").read_text(encoding="utf-8")
    style = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    main = (JS / "main.js").read_text(encoding="utf-8")
    assert 'import { PendingLink } from "./pending.js"' in sidebar and "<${PendingLink} current=" in sidebar
    assert 'import { PendingScreen } from "./pending.js"' in main and "<${PendingScreen}" in main
    assert "side-pending" in sidebar and "side-footer" in shell
    assert 'apiGet(PENDING_PATH, token)' in pending and 'const PENDING_PATH = "/gui/api/pending"' in pending
    assert 'plans|approvals)' in pending and 'approval: "Aprobación"' in pending
    assert "fetch(" not in pending and not re.search(r"https?://", pending)
    assert all(value not in model for value in ("document", "window", "fetch("))
    handler = pending.split("const onNotificationsClick = async () => {", 1)[1].split("\n  };", 1)[0]
    assert "window.Notification.requestPermission()" in handler
    assert pending.count("requestPermission") == 1 and 'onClick=${onNotificationsClick}' in pending
    # GUI3-T05: the list is the «Todo» tab of #pending; the toggle button and its "Cerrar" are gone, the row is a link
    for topic in ("pending.toggle", "pending.open", "pending.refresh", "pending.notifications", "pending.tabs"):
        assert f'"{topic}":' in help_text
        assert f'"{topic}"' in pending
    assert '"pending.close":' not in help_text and "pending-popover" not in pending and "aria-expanded" not in pending
    assert 'href=${"#" + PENDING_SECTION}' in pending and 'aria-current=${current ? "page" : null}' in pending
    assert '<${Icon} name="inbox" />Pendientes' in pending
    # one title per screen: the title is the <h2> of main.js, the tab panel has no second one
    assert "<h2" not in pending
    # the title count stays in the page title
    assert "document.title = count ? `(${count}) Multiagent` : \"Multi-Agent Framework\"" in pending
    assert "/* RWD-T03 */" in style and "/* GUI3-T05 */" in style and ".pending-popover" not in style
    assert set(re.findall(r"@media\s*\(max-width:\s*([\d.]+px)\s*\)", style)) <= {"899.98px", "599.98px"}


@pytest.mark.skipif(NODE is None, reason="Node is required")
def test_pending_model_counts_orders_and_only_selects_unseen_items_with_permission():
    script = f"""
      import {{ countPending, newlyPending, pendingIds, sortPending }} from {json.dumps((JS / "pending-model.js").as_uri())};
      const items = [
        {{id: "new", since: "2026-11-18T11:00:00Z"}},
        {{id: "old", since: "2026-11-18T10:00:00Z"}},
        {{id: "newest", since: "2026-11-18T12:00:00Z"}},
      ];
      const ordered = sortPending(items);
      console.log(JSON.stringify({{
        count: countPending(items), order: ordered.map(item => item.id), ids: pendingIds(ordered),
        fresh: newlyPending(ordered, ["old", "new"], true, "granted").map(item => item.id),
        seen: newlyPending(ordered, ["old", "new", "newest"], true, "granted").length,
        denied: newlyPending(ordered, ["old", "new"], true, "denied").length,
        default: newlyPending(ordered, ["old", "new"], true, "default").length,
        optedOut: newlyPending(ordered, ["old", "new"], false, "granted").length,
      }}));
    """
    result = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "count": 3, "order": ["old", "new", "newest"], "ids": ["old", "new", "newest"],
        "fresh": ["newest"], "seen": 0, "denied": 0, "default": 0, "optedOut": 0,
    }


@pytest.mark.skipif(NODE is None, reason="Node is required")
def test_all_touched_scripts_pass_node_check():
    for name in ("pending-model.js", "pending.js", "shell.js", "help-text.js"):
        result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr)
