# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CHT-T06: the thread of a plan proposal (``GET /gui/api/threads/plans/{id}``, ``#new/plan/<id>``) and its entries.

Checked over real services in Mock (nothing calls a model): the open proposal with the inputs of each task and the
revision a decision sends, the message and the disabled approval when a bundle with roles lacks an approved team (and where
it leads), the approved plan with one message per task and its run, a stale revision answered with 409 by the existing
route, and the pure model, the router and the wiring under Node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
LONG_TEXT = "A synthetic paragraph about the launch. " * 20


@pytest.fixture
def env(tmp_path):
    bundle_dir = tmp_path / "general"
    shutil.copytree(GENERAL_BUNDLE, bundle_dir)
    path = bundle_dir / "workflows" / "summarize_document.yaml"      # the same setup as the team check of ARR-T01
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    workflow["steps"][0]["role"] = "analyst"
    workflow["steps"][1]["role"] = "writer"
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, execution_backend="mock",
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"general": bundle_dir})
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def headers(env):
    return {"Authorization": f"Bearer {env.token}"}


def thread(env, proposal_id):
    return env.client.get(f"/gui/api/threads/plans/{proposal_id}", headers=headers(env))


def save(env, workflow, *, inputs=None):
    task = {"id": "review", "title": "Review the document", "workflow": workflow, "bundle": "general", "depends_on": []}
    if inputs is not None:
        task["inputs"] = inputs
    proposal = env.services.planning.propose("manual", {"group_id": "launch", "goal": "Launch product", "tasks": [task]})
    return env.services.plan_proposals.save(proposal, actor="agent:planner", channel="python")


def decide(env, proposal_id, action, body):
    return env.client.post(f"/gui/api/plan-proposals/{proposal_id}/{action}", json=body, headers=headers(env))


# --- the service and the route -----------------------------------------------------------------------------------

def test_an_open_proposal_is_one_stop_with_the_inputs_of_each_task_and_its_revision(env):
    saved = save(env, "review_checklist", inputs={"checklist": LONG_TEXT, "owner": "Ana"})
    page = thread(env, saved["id"]).json()
    assert page["proposal_id"] == saved["id"] and page["status"] == "proposed" and page["revision"] == 1
    (entry,) = page["entries"]
    assert entry["kind"] == "plan" and entry["seq"] == 1 and page["cursor"] == 1
    assert entry["proposal"]["goal"] == "Launch product" and entry["proposal"]["revision"] == 1
    (task,) = entry["tasks"]
    assert task["id"] == "review" and task["inputs"] == {"checklist": LONG_TEXT, "owner": "Ana"}
    assert entry["decision"] == {"type": "plan_approval", "step_id": "", "attempt": None, "revision": 1,
                                 "actions": ["approve", "reject"], "approve_blocked": None}


def test_a_task_without_inputs_shows_an_empty_object(env):
    saved = save(env, "review_checklist")
    assert thread(env, saved["id"]).json()["entries"][0]["tasks"][0]["inputs"] == {}


def test_a_bundle_with_roles_and_no_approved_team_says_so_and_blocks_approving(env):
    saved = save(env, "summarize_document", inputs={"document_text": "A synthetic document."})
    page = thread(env, saved["id"]).json()
    plan, missing = page["entries"]
    blockers = [{"bundle": "general", "roles": ["analyst", "writer"], "team_version_id": None}]
    assert plan["decision"]["actions"] == ["approve", "reject"]          # reject stays possible
    assert plan["decision"]["approve_blocked"] == {"reason": "team_not_approved", "blockers": blockers}
    assert missing["kind"] == "team_missing" and missing["seq"] == 2
    assert (missing["bundle"], missing["roles"], missing["team_version_id"]) == ("general", ["analyst", "writer"], None)
    # the route that decides keeps refusing it, with the same reason the thread shows
    refused = decide(env, saved["id"], "approve", {"expected_revision": 1})
    assert refused.status_code == 409 and refused.json()["detail"]["details"]["reason"] == "team_not_approved"


def test_the_missing_team_message_leads_to_the_proposed_version_and_clears_once_approved(env):
    saved = save(env, "summarize_document", inputs={"document_text": "A synthetic document."})
    version = env.services.teams.propose("general", actor="user:ana", channel="python")
    plan, missing = thread(env, saved["id"]).json()["entries"]
    assert missing["team_version_id"] == version["id"]
    assert plan["decision"]["approve_blocked"]["blockers"][0]["team_version_id"] == version["id"]
    env.services.teams.approve(version["id"], actor="user:ana", channel="python")
    (only,) = thread(env, saved["id"]).json()["entries"]
    assert only["kind"] == "plan" and only["decision"]["approve_blocked"] is None


def test_an_approved_plan_has_one_message_per_task_with_its_run(env):
    saved = save(env, "review_checklist", inputs={"checklist": "Check it."})
    answer = decide(env, saved["id"], "approve", {"expected_revision": 1})
    assert answer.status_code == 200 and answer.json()["decided_by"] == "user:operator"
    page = thread(env, saved["id"]).json()
    plan, task = page["entries"]
    assert page["status"] == "approved" and plan["decision"]["actions"] == [] and plan["decision"]["approve_blocked"] is None
    assert task["kind"] == "task_run" and task["task_id"] == "review" and task["title"] == "Review the document"
    assert task["run_id"] and task["run_status"]
    assert env.services.runs.get(task["run_id"])["task_id"] == "review"   # the run the message opens exists and is this task's


def test_a_rejected_plan_offers_nothing_more(env):
    saved = save(env, "review_checklist")
    assert decide(env, saved["id"], "reject", {"note": "not now"}).status_code == 200
    page = thread(env, saved["id"]).json()
    assert page["status"] == "rejected" and [item["kind"] for item in page["entries"]] == ["plan"]
    assert page["entries"][0]["decision"]["actions"] == []


def test_a_stale_revision_is_a_409_and_the_thread_then_sends_the_new_one(env):
    saved = save(env, "review_checklist", inputs={"checklist": "Check it."})
    before = thread(env, saved["id"]).json()["entries"][0]["decision"]["revision"]
    edited = env.client.post(f"/gui/api/plan-proposals/{saved['id']}/edit", headers=headers(env), json={
        "expected_revision": before, "tasks": [{"id": "review", "title": "Review it again", "workflow": "review_checklist",
                                                 "bundle": "general", "depends_on": []}]})
    assert edited.status_code == 200 and edited.json()["revision"] == before + 1
    stale = decide(env, saved["id"], "approve", {"expected_revision": before})
    assert stale.status_code == 409
    assert stale.json()["detail"]["details"]["reason"] == "stale_revision"
    assert stale.json()["detail"]["details"]["revision"] == before + 1
    assert env.services.plan_proposals.get(saved["id"])["status"] == "proposed"
    current = thread(env, saved["id"]).json()["entries"][0]
    assert current["decision"]["revision"] == before + 1 and current["tasks"][0]["title"] == "Review it again"
    assert decide(env, saved["id"], "approve", {"expected_revision": before + 1}).status_code == 200


def test_the_thread_asks_for_the_token_and_knows_a_missing_proposal(env):
    assert env.client.get("/gui/api/threads/plans/plp-000000000000").status_code == 401
    assert thread(env, "plp-000000000000").status_code == 404


def test_the_pending_inbox_opens_a_plan_in_its_thread(env):
    saved = save(env, "review_checklist")
    items = env.client.get("/gui/api/pending", headers=headers(env)).json()["items"]
    assert [item["href"] for item in items if item["type"] == "plan"] == ["#new/plan/" + saved["id"]]


def append_plan_event(env, proposal_id, event, **payload):
    store = env.services.tasks.store
    with store.transaction() as conn:
        store.append_event(conn, event, actor="system:worker", group_id="launch", task_id="review",
                           payload={"proposal_id": proposal_id, "run_id": "run-1", **payload})


def test_the_thread_shows_what_the_plan_did_by_itself_and_offers_the_advance_after_a_failure(env):
    """ARR3-T03: plan_event entries from the team events, and ``can_advance`` only after a plan.advance_failed."""
    saved = save(env, "review_checklist", inputs={"checklist": "Check it."})
    assert thread(env, saved["id"]).json()["can_advance"] is False
    decide(env, saved["id"], "approve", {"expected_revision": 1})
    assert thread(env, saved["id"]).json()["can_advance"] is False
    append_plan_event(env, saved["id"], "plan.advanced", advanced=[{"task_id": "review", "state": "running", "run_id": "r2"}])
    page = thread(env, saved["id"]).json()
    events = [entry for entry in page["entries"] if entry["kind"] == "plan_event"]
    assert [entry["event"] for entry in events] == ["plan.advanced"] and events[0]["text"].startswith("The plan advanced: started review.")
    assert page["can_advance"] is False and events[0]["ts"]
    append_plan_event(env, saved["id"], "plan.advance_failed", reason="invalid_state: busy")
    page = thread(env, saved["id"]).json()
    events = [entry for entry in page["entries"] if entry["kind"] == "plan_event"]
    assert [entry["event"] for entry in events] == ["plan.advanced", "plan.advance_failed"]
    assert events[1]["text"] == "Could not advance the plan: invalid_state: busy." and page["can_advance"] is True
    assert page["cursor"] == page["entries"][-1]["seq"]
    append_plan_event(env, saved["id"], "plan.advanced", advanced=[])       # a later advance clears the offer
    assert thread(env, saved["id"]).json()["can_advance"] is False


def test_the_thread_view_has_the_plan_events_the_advance_and_its_help():
    view = ui_text((JS / "plan-thread.js"))
    help_text = ui_text((JS / "help-text.js"))
    for expected in ('entry.kind === "plan_event"', "Avanzar el plan", '"/advance"', 'topic="plan.auto_advance"',
                     'helpFor("plan.auto_advance")', "setInterval(load, 4000)", "clearInterval(timer)", "<${Confirm}", "Sí, avanzar"):
        assert expected in view, expected
    assert '"plan.auto_advance"' in help_text
    for bad in ("innerHTML", "__html", "style="):
        assert bad not in view, bad


# --- the pure model, the route and the wiring (Node) -----------------------------------------------------------------

@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("plan-thread-static")
    shutil.copytree(JS, root / "js")
    shutil.copytree(STATIC_DIR / "i18n", root / "i18n")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    return root


def run_node(root, body):
    script = root / "plan.mjs"
    script.write_text(body, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_every_script_passes_node_check():
    for name in ("plan-card-model.js", "plan-thread.js", "plans.js", "thread-cards.js", "main.js", "route.js", "help-text.js"):
        result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr)


@needs_node
def test_the_route_reads_a_plan_proposal_and_leaves_the_other_threads_alone(node_root):
    out = run_node(node_root, r"""
      import { parseRoute, planThreadHref } from "./js/route.js";
      console.log(JSON.stringify({
        plan: parseRoute("#new/plan/plp-0123456789ab"), encoded: parseRoute("#new/plan/a%20b"),
        team: parseRoute("#new/team/tmv-1"), run: parseRoute("#new/run/run-1"), bare: parseRoute("#new"),
        plans: parseRoute("#plans/plp-1"), href: planThreadHref("plp-0123456789ab")}));
    """)
    assert out["plan"] == {"section": "new", "id": "plp-0123456789ab", "kind": "plan"} and out["encoded"]["id"] == "a b"
    assert out["team"]["kind"] == "team" and out["run"] == {"section": "new", "id": "run-1"}
    assert out["bare"] == {"section": "new", "id": None} and out["plans"] == {"section": "plans", "id": "plp-1"}
    assert out["href"] == "#new/plan/plp-0123456789ab"


@needs_node
def test_the_plan_model_writes_every_part_as_text(node_root):
    out = run_node(node_root, r"""
      import * as m from "./js/plan-card-model.js";
      const blocked = { decision: { revision: 3, actions: ["approve", "reject"], approve_blocked: { reason: "team_not_approved",
        blockers: [{ bundle: "general", roles: ["analyst"], team_version_id: null }] } } };
      console.log(JSON.stringify({
        pairs: m.inputPairs({ a: "x", b: 2, c: null }), none: [m.inputsValue({}), m.inputsValue(null), m.inputsValue([1])],
        value: m.inputsValue({ a: "x" }),
        revision: [m.decisionRevision(blocked), m.decisionRevision({ proposal: { revision: 5 } }), m.decisionRevision({})],
        actions: [m.decisionActions(blocked), m.decisionActions({ decision: { actions: [] } })],
        block: [m.approveBlock(blocked), m.approveBlock({ decision: { approve_blocked: null } })],
        version: m.missingTeam({ bundle: "general", roles: ["analyst", "writer"], team_version_id: "tmv-1" }),
        teams: m.missingTeam({ bundle: "my bundle", roles: [], team_version_id: null }),
        noBundle: m.missingTeam({ bundle: null, roles: ["r"] }),
        run: m.taskRunLine({ task_id: "t1", title: "Review", state: "running", run_id: "run 1", run_status: "queued" }),
        norun: m.taskRunLine({ task_id: "t2", title: "t2", state: "blocked", run_id: null }),
        stale: m.decisionFailure({ status: 409, code: "invalid_state", message: "Stale", details: { reason: "stale_revision", revision: 4 } }),
        team: m.decisionFailure({ status: 409, details: { reason: "team_not_approved" } }),
        other: m.decisionFailure({ status: 500, code: "boom", message: "Broke", details: {} }),
        gone: m.decisionFailure({ status: 404, message: "Missing" })}));
    """)
    assert out["pairs"] == [["a", "x"], ["b", "2"], ["c", ""]] and out["none"] == [None, None, None]
    assert out["value"] == {"a": "x"} and out["revision"] == [3, 5, None]
    assert out["actions"] == [["approve", "reject"], []]
    assert out["block"][0] == "No se puede aprobar: falta el equipo aprobado de «general»." and out["block"][1] == ""
    assert out["version"]["href"] == "#new/team/tmv-1" and out["version"]["kind"] == "version"
    assert "«general»" in out["version"]["text"] and "analyst, writer" in out["version"]["text"]
    assert out["teams"]["href"] == "#teams/my%20bundle" and out["teams"]["kind"] == "teams"
    assert out["noBundle"]["href"] == "#teams"
    assert out["run"]["href"] == "#new/run/run%201" and "(en cola)" in out["run"]["runText"] and "En marcha" in out["run"]["text"]
    assert out["norun"]["href"] == "" and out["norun"]["runText"] == "Todavía no tiene run."
    assert out["stale"]["reload"] is True and "revisión 4" in out["stale"]["text"]
    assert out["team"]["reload"] is True and out["other"] == {"text": "Broke (boom)", "reload": False}
    assert out["gone"]["reload"] is True


def test_the_view_is_wired_into_the_page_without_touching_the_new_session():
    main, plans, view = [ui_text((JS / name)) for name in ("main.js", "plans.js", "plan-thread.js")]
    assert 'import { PlanThread } from "./plan-thread.js";' in main and "CHT-T06" in main
    assert 'route.kind === "plan"' in main and 'route.kind !== "plan"' in main
    assert 'import { TaskInputs } from "./plan-thread.js";' in plans and "<${TaskInputs}" in plans
    assert "/gui/api/threads/plans/" in view and "/gui/api/plan-proposals/" in view
    assert "expected_revision: revision" in view and "approveBlock(entry)" in view
    assert "dangerouslySetInnerHTML" not in view and "localStorage" not in view
    assert "export function ReadableValue" in ui_text((JS / "thread-cards.js"))
