# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PLN-T04: the plan proposals view of the GUI (``/gui/api/plan-proposals`` and ``static/js/plans.js``).

The router is checked over real services in Mock: token protection on every route, listing with status filter, reading
one proposal, editing tasks, reassigning a task, approving and rejecting with the actor (``user:operator``) and channel
(``gui``) fixed by the server, revision conflicts (409), existing-task conflicts (409), and closed-proposal conflicts.
The view is checked statically and, when Node is installed, via ``node --check`` and pure Node rendering checks.
"""
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
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from gui_text import ui_text
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture
def env(tmp_path):
    general = tmp_path / "general"
    shutil.copytree(GENERAL_BUNDLE, general)
    settings = Settings(
        _env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
        execution_backend="mock", mock_mode=True, gemini_api_key="",
        openai_api_key="", openai_enabled=False,
        gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(settings, bundles={"general": general})
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def call(env, method, path, **kwargs):
    return env.client.request(
        method, "/gui/api/plan-proposals" + path,
        headers={"Authorization": f"Bearer {env.token}"}, **kwargs,
    )


def create_proposal(env, *, group_id="launch", goal="Launch product"):
    doc = env.services.planning.propose("manual", {
        "group_id": group_id,
        "goal": goal,
        "tasks": [
            {"id": "technical", "title": "Technical review", "workflow": "social_post",
             "depends_on": [], "objective": "Check the design", "inputs": {"topic": "Launch"}},
            {"id": "commercial", "title": "Commercial review", "workflow": "social_post",
             "depends_on": ["technical"]},
        ],
    })
    return env.services.plan_proposals.save(doc, actor="agent:planner", channel="python", note="initial")


# --- the router --------------------------------------------------------------------------------------------------

def test_every_route_asks_for_the_gui_token(env):
    routes = [
        ("GET", ""),
        ("GET", "/plp-000000000000"),
        ("POST", "/plp-000000000000/edit"),
        ("POST", "/plp-000000000000/reassign"),
        ("POST", "/plp-000000000000/approve"),
        ("POST", "/plp-000000000000/reject"),
    ]
    for method, path in routes:
        assert env.client.request(method, "/gui/api/plan-proposals" + path, json={}).status_code == 401, path
        wrong = env.client.request(
            method, "/gui/api/plan-proposals" + path, json={},
            headers={"Authorization": "Bearer wrong"},
        )
        assert wrong.status_code == 401, path


def test_list_filter_and_get_proposal(env):
    assert call(env, "GET", "").json() == {"proposals": [], "count": 0}
    first = create_proposal(env, group_id="alpha", goal="First goal")
    second = create_proposal(env, group_id="beta", goal="Second goal")
    env.services.plan_proposals.reject(second["id"], actor="user:ana", channel="python", note="not now")

    all_items = call(env, "GET", "")
    assert all_items.status_code == 200
    assert all_items.json()["count"] == 2

    only_proposed = call(env, "GET", "", params={"status": "proposed"}).json()
    assert [item["id"] for item in only_proposed["proposals"]] == [first["id"]]

    only_rejected = call(env, "GET", "", params={"status": "rejected"}).json()
    assert [item["id"] for item in only_rejected["proposals"]] == [second["id"]]

    bad_status = call(env, "GET", "", params={"status": "bogus"})
    assert bad_status.status_code == 422 and bad_status.json()["detail"]["code"] == "invalid_request"

    detail = call(env, "GET", f"/{first['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == first["id"]
    assert [t["id"] for t in detail.json()["proposal"]["tasks"]] == ["technical", "commercial"]

    missing = call(env, "GET", "/plp-000000000000")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "not_found"


def test_edit_reassign_and_approve_fix_actor_and_channel(env):
    saved = create_proposal(env)
    tasks = [dict(task) for task in saved["proposal"]["tasks"]]
    tasks[0]["title"] = "Technical approval"

    edited = call(env, "POST", f"/{saved['id']}/edit", json={
        "tasks": tasks,
        "expected_revision": 1,
        "note": "clearer title",
        "actor": "user:mallory",
        "channel": "cli",
    })
    assert edited.status_code == 200, edited.text
    edited_body = edited.json()
    assert edited_body["revision"] == 2
    assert edited_body["proposal"]["tasks"][0]["title"] == "Technical approval"
    assert edited_body["history"][-1]["actor"] == "user:operator"
    assert edited_body["history"][-1]["channel"] == "gui"
    assert edited_body["history"][-1]["note"] == "clearer title"

    reassigned = call(env, "POST", f"/{saved['id']}/reassign", json={
        "task_id": "technical",
        "expected_revision": 2,
        "preferred": "agent:codex",
        "workflow": "summarize_document",
        "bundle": "general",
        "note": "use bundle workflow",
        "actor": "user:mallory",
        "channel": "api",
    })
    assert reassigned.status_code == 200, reassigned.text
    reassigned_body = reassigned.json()
    assert reassigned_body["revision"] == 3
    tech = reassigned_body["proposal"]["tasks"][0]
    assert tech["preferred"] == "agent:codex"
    assert tech["workflow"] == "summarize_document"
    assert tech["bundle"] == "general"
    assert reassigned_body["history"][-1]["actor"] == "user:operator"
    assert reassigned_body["history"][-1]["channel"] == "gui"

    approved = call(env, "POST", f"/{saved['id']}/approve", json={
        "expected_revision": 3,
        "note": "ship it",
        "actor": "agent:planner",
        "channel": "cli",
    })
    assert approved.status_code == 200, approved.text
    approved_body = approved.json()
    assert approved_body["status"] == "approved"
    assert approved_body["decided_by"] == "user:operator"
    assert approved_body["decided_channel"] == "gui"
    assert approved_body["plan_status"]["status"] == "running"
    assert env.services.tasks.get_task("technical")["preferred"] == "agent:codex"


def test_revision_conflict_and_closed_transition_and_existing_task_conflict(env):
    saved = create_proposal(env)
    tasks = [dict(task) for task in saved["proposal"]["tasks"]]
    tasks[0]["title"] = "Updated once"
    assert call(env, "POST", f"/{saved['id']}/edit", json={"tasks": tasks, "expected_revision": 1}).status_code == 200

    stale_edit = call(env, "POST", f"/{saved['id']}/edit", json={"tasks": tasks, "expected_revision": 1})
    assert stale_edit.status_code == 409
    assert stale_edit.json()["detail"]["code"] == "invalid_state"
    assert stale_edit.json()["detail"]["details"]["reason"] == "stale_revision"

    stale_reassign = call(env, "POST", f"/{saved['id']}/reassign", json={
        "task_id": "technical", "expected_revision": 1, "preferred": "agent:codex",
    })
    assert stale_reassign.status_code == 409
    assert stale_reassign.json()["detail"]["code"] == "invalid_state"
    assert stale_reassign.json()["detail"]["details"]["reason"] == "stale_revision"

    stale_approve = call(env, "POST", f"/{saved['id']}/approve", json={"expected_revision": 1})
    assert stale_approve.status_code == 409
    assert stale_approve.json()["detail"]["code"] == "invalid_state"
    assert stale_approve.json()["detail"]["details"]["reason"] == "stale_revision"
    assert call(env, "GET", f"/{saved['id']}").json()["revision"] == 2

    rejected = call(env, "POST", f"/{saved['id']}/reject", json={"note": "hold off", "actor": "user:mallory"})
    assert rejected.status_code == 200
    rejected_body = rejected.json()
    assert rejected_body["status"] == "rejected"
    assert rejected_body["decided_by"] == "user:operator"
    assert rejected_body["decided_channel"] == "gui"
    assert rejected_body["history"][-1]["note"] == "hold off"

    for action, body in (
        ("edit", {"tasks": tasks, "expected_revision": 2}),
        ("reassign", {"task_id": "technical", "expected_revision": 2, "preferred": "agent:codex"}),
        ("approve", {"expected_revision": 2}),
        ("reject", {}),
    ):
        closed = call(env, "POST", f"/{saved['id']}/{action}", json=body)
        assert closed.status_code == 409 and closed.json()["detail"]["code"] == "invalid_state", action

    second = create_proposal(env)
    env.services.tasks.create_group("launch", "Launch", actor="user:ana")
    env.services.tasks.create_task("launch", "technical", "Existing", actor="user:ana")
    duplicate = call(env, "POST", f"/{second['id']}/approve", json={"expected_revision": 1})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "invalid_state"
    assert duplicate.json()["detail"]["details"]["reason"] == "task_exists"


# --- the view, as text -------------------------------------------------------------------------------------------

def test_the_view_is_wired_into_routes_sidebar_and_help():
    view = ui_text((JS / "plans.js"))
    main = ui_text(JS / "main.js")
    route = ui_text((JS / "route.js"))
    extensions = ui_text((JS / "extensions.js"))
    help_text = ui_text((JS / "help-text.js"))

    assert 'import { Plans } from "./plans.js"' in main and '{ id: "plans", label: "Planes", icon: "file", mode: "advanced" }' in main
    assert re.search(r"<\$\{Plans\}[^>]*route=\$\{route\}", main)
    assert '"plans"' in route and '"plans"' in extensions

    for expected in ("/gui/api/plan-proposals", "expected_revision", "onReload", "<${Confirm}", "409"):
        assert expected in view, expected

    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "dangerouslySetInnerHTML", "__html", "eval(", "style=", "document.write"):
        assert bad not in view, bad

    # the page never picks actor or channel: the server fixes user:operator and gui
    assert "user:operator" not in view and '"gui"' not in view
    assert not re.search(r"\b(actor|channel|decided_by)\s*:", view)

    topics = set(re.findall(r'topic="(plans\.\w+)"', view) + re.findall(r'helpFor\("(plans\.\w+)"', view))
    for required_topic in ("plans.section", "plans.list", "plans.detail", "plans.edit", "plans.reassign", "plans.approve", "plans.reject", "plans.note"):
        assert required_topic in topics, required_topic
    for topic in topics:
        assert f'"{topic}":' in help_text, topic


@needs_node
def test_every_touched_script_passes_node_check():
    for name in ("plans.js", "main.js", "route.js", "extensions.js", "help-text.js"):
        result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr)


# --- the view, run (needs Node) ----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("plans-static")
    shutil.copytree(JS, root / "js")
    shutil.copytree(STATIC_DIR / "i18n", root / "i18n")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    return root


def run_node(root, body):
    script = root / "plans-view.mjs"
    script.write_text(body, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


WALKER = r"""
const expand = (v) => {
  if (v == null || typeof v === "boolean" || typeof v === "string" || typeof v === "number") return v;
  if (Array.isArray(v)) return v.map(expand);
  if (v.type?.name === "Help") return "";
  if (typeof v.type === "function") return expand(v.type(v.props || {}));
  return { type: v.type, props: { ...v.props, children: expand(v.props?.children) } };
};
const text = (v) => v == null || typeof v === "boolean" ? "" : Array.isArray(v) ? v.map(text).join(" ")
  : typeof v === "object" ? text(v.props?.children) : String(v);
const hasTag = (v, tag) => {
  if (!v || typeof v !== "object") return false;
  if (Array.isArray(v)) return v.some((x) => hasTag(x, tag));
  if (v.type === tag) return true;
  return hasTag(v.props?.children, tag);
};
"""


@needs_node
def test_pure_helpers_and_text_escaping_in_node(node_root):
    out = run_node(node_root, WALKER + r"""
      import {
        PlanList, TasksTable, HistoryList,
        buildEditedTasks, buildReassignPayload, parseDependencies, formatDependencies, toEditDrafts,
      } from "./js/plans.js";

      const xss = "<script>alert(1)</script>";
      const proposal = {
        id: "plp-1",
        group_id: "launch",
        planner: "manual",
        goal: xss,
        status: "proposed",
        revision: 3,
        created_by: "agent:planner",
        created_at: "2026-11-18T10:00:00+00:00",
        proposal: {
          tasks: [
            { id: "t1", title: "Step 1", objective: xss, depends_on: [], workflow: "social_post", preferred: "agent:codex" },
            { id: "t2", title: "Step 2", depends_on: ["t1"], workflow: "summarize_document", bundle: "general" },
          ],
        },
        history: [
          { id: 1, kind: "edit", actor: "user:operator", channel: "gui", at: "2026-11-18T11:00:00+00:00",
            revision: 3, note: xss, changes: { t1: { before: {}, after: {} } } },
        ],
      };

      const listVNode = expand(PlanList({ data: { proposals: [proposal] } }));
      const emptyVNode = expand(PlanList({ data: { proposals: [] } }));
      const tasksVNode = expand(TasksTable({ tasks: proposal.proposal.tasks }));
      const historyVNode = expand(HistoryList({ history: proposal.history }));

      const drafts = toEditDrafts(proposal.proposal.tasks);
      drafts[1].depends_on_text = "t1, t1, ";
      const edited = buildEditedTasks(drafts);
      const reassign = buildReassignPayload(
        proposal.proposal.tasks[0],
        { preferred: "agent:claude", workflow: "social_post", bundle: "" },
        3,
        "cambio",
      );

      console.log(JSON.stringify({
        listText: text(listVNode),
        emptyText: text(emptyVNode),
        tasksText: text(tasksVNode),
        historyText: text(historyVNode),
        hasScriptTag: hasTag(listVNode, "script") || hasTag(tasksVNode, "script") || hasTag(historyVNode, "script"),
        deps: parseDependencies("a, b, a, "),
        depsFormatted: formatDependencies(["a", "b"]),
        depsEmpty: formatDependencies([]),
        edited,
        reassign,
      }));
    """)
    assert out["hasScriptTag"] is False
    assert "<script>alert(1)</script>" in out["listText"]
    assert "Propuesta" in out["listText"] and "r3" in out["listText"]
    assert "No hay propuestas" in out["emptyText"]
    assert "Step 1" in out["tasksText"] and "agent:codex" in out["tasksText"] and "general" in out["tasksText"]
    assert "Editada" in out["historyText"] and "t1" in out["historyText"]
    assert out["deps"] == ["a", "b"]
    assert out["depsFormatted"] == "a, b" and out["depsEmpty"] == "Ninguna"
    assert out["edited"][1]["depends_on"] == ["t1"]
    assert out["reassign"] == {
        "task_id": "t1", "expected_revision": 3, "note": "cambio",
        "preferred": "agent:claude", "workflow": "social_post",
    }
