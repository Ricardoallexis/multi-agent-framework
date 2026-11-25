# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARR3-T02: the plan draft of «Nueva sesión» in the GUI.

The pure model and the card body run under Node (skipped without it); the flow from the free text to the plan thread runs
over real services in Mock with TestClient. Nothing calls a model and nobody presses «Aprobar»: where a plan is approved,
the test is a double that decides as ``user:operator`` through the same route the thread uses."""
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
TWO_PARTS = "Summarize the document in plain language and then review the specification against the criteria"

DRAFT = {
    "schema": "maf.new-session-plan/1", "goal": "Resume este texto y luego redacta un correo", "group_id": "chat-abc123def456",
    "planner": "deterministic",
    "tasks": [
        {"id": "chat-abc-s1", "title": "Resume este texto", "workflow": "summarize_document", "bundle": "general",
         "inputs": {"document_text": "Un texto de prueba."}, "depends_on": []},
        {"id": "chat-abc-s2", "title": "Redacta un correo", "workflow": "draft_email", "bundle": "general",
         "inputs": {"email_goal": "Redacta un correo", "tone": ["formal", "breve"]}, "depends_on": ["chat-abc-s1"]},
    ],
    "teams": [{"bundle": "general", "roles": ["analyst", "writer"], "action": "propose"}],
    "proposal": {"schema": "multiagent.plan_proposal/1"},
}
CATALOG = [{"id": "summarize_document", "bundle": "general", "description": "Resume un documento."},
           {"id": "draft_email", "bundle": "general", "description": "Redacta un correo."}]


# --- Node helpers ----------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("plan-draft-static")
    shutil.copytree(JS, root / "js")
    shutil.copytree(STATIC_DIR / "i18n", root / "i18n")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    return root


def run_node(root, body):
    script = root / "draft.mjs"
    script.write_text(body, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def rows(root, mode, draft=DRAFT, catalog=CATALOG):
    return run_node(root, f"""
      import {{ planDraftRows }} from "./js/plan-draft-model.js";
      console.log(JSON.stringify(planDraftRows({json.dumps(draft)}, {json.dumps(mode)}, {json.dumps(catalog)})));
    """)


# --- the pure model -----------------------------------------------------------------------------------------------------------

@needs_node
def test_rows_readable_in_simple(node_root):
    first, second = rows(node_root, "simple")
    for row in (first, second):
        assert "workflow" not in row and "bundle" not in row
        assert set(row) == {"id", "title", "workflowText", "inputs", "dependsOnText"}
        assert all(set(item) == {"label", "value"} and isinstance(item["value"], str) for item in row["inputs"])
    assert first["workflowText"] == "Resume un documento." and first["dependsOnText"] == ""
    assert first["inputs"] == [{"label": "document text", "value": "Un texto de prueba."}]
    assert second["dependsOnText"] == "Va después de: Resume este texto"       # titles, not task ids
    assert second["inputs"][1] == {"label": "tone", "value": "formal, breve"}   # a list is text, not JSON
    text = json.dumps([first, second])
    assert "summarize_document" not in text and "draft_email" not in text and "{" not in second["inputs"][1]["value"]


@needs_node
def test_rows_show_ids_in_advanced(node_root):
    first, second = rows(node_root, "advanced")
    assert (first["workflow"], first["bundle"]) == ("summarize_document", "general")
    assert (second["workflow"], second["bundle"]) == ("draft_email", "general")
    assert "summarize_document (general)" in first["workflowText"] and "Resume un documento." in first["workflowText"]


@needs_node
def test_save_body_confirms(node_root):
    result = run_node(node_root, f"""
      import {{ planSaveBody, PLAN_SAVE_LABEL }} from "./js/plan-draft-model.js";
      console.log(JSON.stringify({{ body: planSaveBody({json.dumps(DRAFT)}), label: PLAN_SAVE_LABEL }}));
    """)
    assert result["body"] == {"draft": DRAFT, "confirmed": True} and result["label"] == "Guardar el plan"


@needs_node
def test_team_rows(node_root):
    result = run_node(node_root, f"""
      import {{ teamRows }} from "./js/plan-draft-model.js";
      const none = {{ ...{json.dumps(DRAFT)}, teams: [] }};
      console.log(JSON.stringify({{ some: teamRows({json.dumps(DRAFT)}), none: teamRows(none), missing: teamRows({{}}) }}));
    """)
    assert result["some"] == [{"bundle": "general", "rolesText": "analyst, writer"}]
    assert result["none"] == [] and result["missing"] == []


@needs_node
def test_draft_text_is_plain(node_root):
    evil = "<img src=x onerror=alert(1)>"
    draft = json.loads(json.dumps(DRAFT))
    draft["tasks"][0]["title"] = evil
    draft["tasks"][0]["inputs"]["document_text"] = evil
    result = run_node(node_root, f"""
      import {{ PlanDraftBody }} from "./js/plan-draft.js";
      const texts = [];
      const walk = (n) => {{
        if (n === null || n === undefined || n === false || n === true) return;
        if (Array.isArray(n)) {{ n.forEach(walk); return; }}
        if (typeof n !== "object") {{ texts.push(String(n)); return; }}
        const props = n.props || {{}};
        if (props.dangerouslySetInnerHTML || props.innerHTML) texts.push("HTML-INJECTED");
        walk(props.children);
      }};
      walk(PlanDraftBody({{ draft: {json.dumps(draft)}, mode: "simple", catalog: [] }}));
      console.log(JSON.stringify(texts));
    """)
    assert evil in result and result.count(evil) == 2 and "HTML-INJECTED" not in result
    for name in ("plan-draft.js", "plan-draft-model.js"):
        source = ui_text((JS / name))
        for bad in ("innerHTML", "__html", "dangerouslySetInnerHTML", "style="):
            assert bad not in source, (name, bad)


@needs_node
def test_draft_without_fallback_keeps_the_existing_card_text(node_root):
    result = run_node(node_root, f"""
      import {{ PlanDraftBody }} from "./js/plan-draft.js";
      const texts = [];
      const walk = (n) => {{
        if (n === null || n === undefined || n === false || n === true) return;
        if (Array.isArray(n)) {{ n.forEach(walk); return; }}
        if (typeof n !== "object") {{ texts.push(String(n)); return; }}
        walk(n.props && n.props.children);
      }};
      walk(PlanDraftBody({{ draft: {json.dumps(DRAFT)}, mode: "simple", catalog: {json.dumps(CATALOG)} }}));
      console.log(JSON.stringify(texts));
    """)
    assert "Resume este texto y luego redacta un correo" in result
    assert not any("plan por defecto" in text for text in result)


@needs_node
def test_plan_suggested_answer_is_recognized(node_root):
    result = run_node(node_root, f"""
      import {{ isPlanSuggested }} from "./js/plan-draft-model.js";
      const ok = {{ source: "model", reason: "plan_suggested", plan_draft: {json.dumps(DRAFT)} }};
      console.log(JSON.stringify([isPlanSuggested(ok), isPlanSuggested({{ ...ok, reason: "" }}), isPlanSuggested({{ ...ok, plan_draft: null }}),
        isPlanSuggested(null)]));
    """)
    assert result == [True, False, False, False]


@needs_node
def test_every_script_passes_node_check():
    paths = [
        JS / "route.js", JS / "new-session-model.js", JS / "new-session.js",
        JS / "plan-draft-model.js", JS / "plan-draft.js", JS / "help-text.js",
        STATIC_DIR / "extensions" / "offices" / "index.js", STATIC_DIR / "extensions" / "offices" / "help.js",
    ]
    for path in paths:
        result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (path, result.stderr)


def test_wiring_and_help():
    shell = ui_text((JS / "new-session.js"))
    card = ui_text((JS / "plan-draft.js"))
    help_text = ui_text((JS / "help-text.js"))
    assert 'import { PlanDraftCard } from "./plan-draft.js";' in shell and "isPlanSuggested(interpretation)" in shell
    assert '"chat.plan_draft"' in help_text and '"chat.plan_save"' in help_text
    assert 'topic="chat.plan_draft"' in card and 'helpFor("chat.plan_save")' in card
    assert "/gui/api/new-session/plan/save" in ui_text((JS / "plan-draft-model.js"))
    assert "ARR3-T02" in (STATIC_DIR / "app.css").read_text(encoding="utf-8")


# --- the flow over real services (Mock, TestClient) ----------------------------------------------------------------------

def make_env(tmp_path, bundle):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, execution_backend="mock",
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"general": bundle})
    token = ensure_token(settings.gui_token_file)
    return services, token


@pytest.fixture
def env(tmp_path):
    services, token = make_env(tmp_path, GENERAL_BUNDLE)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


@pytest.fixture
def role_env(tmp_path):
    folder = tmp_path / "role-general"
    shutil.copytree(GENERAL_BUNDLE, folder)
    path = folder / "workflows" / "summarize_document.yaml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    workflow["steps"][0]["role"] = "analyst"
    workflow["steps"][1]["role"] = "writer"
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    services, token = make_env(tmp_path, folder)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


def interpret(env, text=TWO_PARTS):
    answer = env.client.post("/gui/api/new-session/interpret", headers=env.auth, json={"text": text})
    assert answer.status_code == 200
    return answer.json()


@needs_node
def test_save_opens_plan_thread(env, node_root):
    answer = interpret(env)
    assert answer["reason"] == "plan_suggested"
    saved = env.client.post("/gui/api/new-session/plan/save", headers=env.auth,
                            json={"draft": answer["plan_draft"], "confirmed": True})
    assert saved.status_code == 200
    href = saved.json()["href"]
    route = run_node(node_root, f"""
      import {{ parseRoute }} from "./js/route.js";
      console.log(JSON.stringify(parseRoute({json.dumps(href)})));
    """)
    assert route["section"] == "new" and route["kind"] == "plan" and route["id"] == saved.json()["proposal_id"]
    stored = env.services.plan_proposals.get(saved.json()["proposal_id"])
    assert stored["status"] == "proposed"                      # saving approved nothing
    thread = env.client.get(f"/gui/api/threads/plans/{saved.json()['proposal_id']}", headers=env.auth).json()
    assert thread["entries"][0]["decision"]["actions"] == ["approve", "reject"]


def test_c10_regression_in_flow(role_env):
    env = role_env
    answer = interpret(env, TWO_PARTS)
    draft = answer["plan_draft"]
    assert draft["teams"] == [{"bundle": "general", "roles": ["analyst", "writer"], "action": "propose"}]
    saved = env.client.post("/gui/api/new-session/plan/save", headers=env.auth, json={"draft": draft, "confirmed": True}).json()
    assert len(saved["team_versions"]) == 1 and env.services.teams.approved("general") is None
    # a double decides as user:operator through the route of the thread; no person presses «Aprobar» here
    refused = env.client.post(f"/gui/api/plan-proposals/{saved['proposal_id']}/approve", headers=env.auth,
                              json={"expected_revision": saved["revision"]})
    assert refused.status_code == 409
    assert refused.json()["detail"]["details"]["reason"] == "team_not_approved"
    thread = env.client.get(f"/gui/api/threads/plans/{saved['proposal_id']}", headers=env.auth).json()
    plan, missing = thread["entries"]
    assert plan["decision"]["approve_blocked"]["reason"] == "team_not_approved" and missing["kind"] == "team_missing"
    assert env.services.plan_proposals.get(saved["proposal_id"])["status"] == "proposed"
