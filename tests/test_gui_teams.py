# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EQP-T05: the teams view of the GUI (``/gui/api/teams`` and ``static/js/teams.js``).

The router is checked over real services in Mock: an empty list, a bundle with an approved team and a pending proposal,
the org chart, approve and reject with the actor fixed by the router (whatever the body says), ``invalid_state`` when a
proposal is decided twice, ``not_found``, and that every route asks for the GUI token. The view is checked as text and,
when Node is installed, its pure parts are run with data of the router's shape.
"""
from __future__ import annotations

import json
import re
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
from gui_text import ui_text
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def make_env(tmp_path, bundles):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles=bundles)
    return services, ensure_token(settings.gui_token_file)


@pytest.fixture
def empty(tmp_path):
    services, token = make_env(tmp_path, {})
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


@pytest.fixture
def env(tmp_path):
    team = tmp_path / "case"
    shutil.copytree(GENERAL_BUNDLE, team)
    plain = tmp_path / "plain"
    shutil.copytree(GENERAL_BUNDLE, plain)
    (plain / "team.yaml").unlink()                      # a bundle without a team is not listed
    services, token = make_env(tmp_path, {"case": team, "plain": plain})
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token, team_dir=team)
    services.runs.close()


def call(env, method, path, **kwargs):
    return env.client.request(method, "/gui/api/teams" + path, headers={"Authorization": f"Bearer {env.token}"}, **kwargs)


def approved_and_pending(env):
    """v1 approved, then v2 (a new role) proposed and left pending. Returns (v1, v2)."""
    v1 = env.services.teams.propose("case", actor="agent:codex/gpt", channel="python")
    env.services.teams.approve(v1["id"], actor="user:ana", channel="python")
    path = env.team_dir / "team.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["roles"].append({"id": "auditor", "purpose": "Audits.", "rules": []})
    data["relationships"].append({"type": "reports_to", "from": "auditor", "to": "lead"})
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    v2 = env.services.teams.propose("case", actor="agent:codex/gpt", channel="python")
    return v1, v2


# --- the router --------------------------------------------------------------------------------------------------

def test_every_route_asks_for_the_gui_token(env):
    routes = [("GET", ""), ("GET", "/case/versions"), ("GET", "/versions/tmv-000000000000"), ("GET", "/case/org-chart"),
              ("POST", "/versions/tmv-000000000000/approve"), ("POST", "/versions/tmv-000000000000/reject")]
    for method, path in routes:
        assert env.client.request(method, "/gui/api/teams" + path, json={}).status_code == 401, path
        wrong = env.client.request(method, "/gui/api/teams" + path, json={}, headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401, path


def test_no_bundles_means_an_empty_list(empty):
    answer = call(empty, "GET", "")
    assert answer.status_code == 200 and answer.json() == {"teams": [], "count": 0}


def test_a_bundle_with_a_team_but_no_versions_is_listed_without_versions(env):
    body = call(env, "GET", "").json()
    assert body == {"teams": [{"bundle": "case", "approved": None, "pending": None}], "count": 1}   # "plain" has no team.yaml
    assert call(env, "GET", "/case/versions").json() == {"bundle": "case", "versions": [], "count": 0}


def test_the_list_gives_the_approved_version_and_the_pending_proposal_without_the_team_body(env):
    v1, v2 = approved_and_pending(env)
    entry = call(env, "GET", "").json()["teams"][0]
    assert entry["approved"]["id"] == v1["id"] and entry["approved"]["status"] == "approved" and entry["approved"]["active"] is True
    assert entry["pending"]["id"] == v2["id"] and entry["pending"]["status"] == "proposed"
    assert "team" not in entry["approved"] and "team" not in entry["pending"]
    versions = call(env, "GET", "/case/versions").json()
    assert [v["version"] for v in versions["versions"]] == [1, 2] and all("team" not in v for v in versions["versions"])
    proposed = call(env, "GET", "/case/versions", params={"status": "proposed"}).json()["versions"]
    assert [v["id"] for v in proposed] == [v2["id"]]


def test_one_version_carries_its_diff_against_the_approved_one(env):
    v1, v2 = approved_and_pending(env)
    body = call(env, "GET", f"/versions/{v2['id']}").json()
    assert body["id"] == v2["id"] and body["diff"]["base_version_id"] == v1["id"] and body["diff"]["has_changes"] is True
    assert [role["id"] for role in body["diff"]["roles"]["added"]] == ["auditor"]


def test_the_org_chart_is_the_service_chart_and_marks_the_pending_position(env):
    v1, v2 = approved_and_pending(env)
    body = call(env, "GET", "/case/org-chart").json()
    assert body == env.services.teams.org_chart("case")
    assert body["pending_version_id"] == v2["id"] and body["mermaid"].startswith("flowchart TD")
    by_id = {position["role_id"]: position for position in body["positions"]}
    assert by_id["auditor"]["status"] == "pending"
    other = call(env, "GET", "/case/org-chart", params={"version_id": v1["id"]}).json()
    assert other["version_id"] == v1["id"] and "auditor" not in {p["role_id"] for p in other["positions"]}


def test_approve_fixes_the_actor_and_the_channel_whatever_the_body_says(env):
    _, v2 = approved_and_pending(env)
    answer = call(env, "POST", f"/versions/{v2['id']}/approve",
                  json={"note": "Looks right", "actor": "user:mallory", "channel": "cli", "decided_by": "user:mallory"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["status"] == "approved" and body["active"] is True and body["note"] == "Looks right"
    assert body["decided_by"] == "user:operator" and body["decided_channel"] == "gui"
    entry = call(env, "GET", "").json()["teams"][0]
    assert entry["approved"]["id"] == v2["id"] and entry["pending"] is None


def test_reject_fixes_the_actor_and_the_channel_and_keeps_the_approved_version(env):
    v1, v2 = approved_and_pending(env)
    answer = call(env, "POST", f"/versions/{v2['id']}/reject", json={"actor": "user:mallory"})   # no note: it is optional
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["status"] == "rejected" and body["decided_by"] == "user:operator" and body["decided_channel"] == "gui"
    assert body["note"] == ""
    assert call(env, "GET", "").json()["teams"][0]["approved"]["id"] == v1["id"]


def test_deciding_twice_is_invalid_state_and_the_first_decision_stands(env):
    _, v2 = approved_and_pending(env)
    assert call(env, "POST", f"/versions/{v2['id']}/reject", json={}).status_code == 200
    for action in ("approve", "reject"):
        again = call(env, "POST", f"/versions/{v2['id']}/{action}", json={})
        assert again.status_code == 409 and again.json()["detail"]["code"] == "invalid_state", action
    assert call(env, "GET", f"/versions/{v2['id']}").json()["status"] == "rejected"


def test_missing_things_are_not_found_and_bad_ids_are_invalid(env):
    assert call(env, "GET", "/versions/tmv-000000000000").json()["detail"]["code"] == "not_found"
    assert call(env, "GET", "/nobody/versions").status_code == 404
    assert call(env, "GET", "/nobody/org-chart").status_code == 404
    assert call(env, "GET", "/versions/not-an-id").status_code == 422
    for action in ("approve", "reject"):
        assert call(env, "POST", f"/versions/tmv-000000000000/{action}", json={}).status_code == 404, action


# --- the view, as text -------------------------------------------------------------------------------------------

def test_the_view_is_wired_into_the_routes_the_menu_and_the_help():
    view = ui_text((JS / "teams.js"))
    main = ui_text(JS / "main.js")
    assert 'import { Teams } from "./teams.js"' in main and '{ id: "teams", label: "Equipos", icon: "person", mode: "advanced" }' in main
    assert re.search(r"<\$\{Teams\}[^>]*route=\$\{route\}", main)
    assert '"teams"' in ui_text((JS / "route.js"))
    assert '"teams"' in ui_text((JS / "extensions.js"))
    for path in ("/gui/api/teams", "/org-chart", "/versions", '"/" + action'):
        assert path in view, path
    for bad in ("innerHTML", "dangerouslySetInnerHTML", "__html", "eval(", "style="):
        assert bad not in view, bad
    # the page never names the actor or the channel: the router does
    assert "user:operator" not in view and '"gui"' not in view and "{ note:" in view
    assert not re.search(r"\b(actor|channel|decided_by)\s*:", view)
    help_text = ui_text((JS / "help-text.js"))
    for topic in set(re.findall(r'topic="(teams\.\w+)"', view) + re.findall(r'helpFor\("(teams\.\w+)"', view)):
        assert f'"{topic}":' in help_text, topic
    # both decisions go through the shared confirmation, and the Mermaid text has its own Copy button
    assert view.count("<${Confirm}") == 1 and 'confirm === "approve"' in view and 'confirm === "reject"' in view
    assert ">Copiar</button>" in view and '<ul class="team-tree"' in view


@needs_node
def test_every_script_passes_node_check():
    for name in ("teams.js", "main.js", "route.js", "extensions.js", "help-text.js"):
        result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr)


# --- the view, run (needs Node) ----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("teams-static")
    shutil.copytree(JS, root / "js")
    shutil.copytree(STATIC_DIR / "i18n", root / "i18n")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    return root


def run_node(root, body):
    script = root / "teams-view.mjs"
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
"""


@needs_node
def test_the_tree_nests_reports_and_the_forest_keeps_every_root(node_root):
    out = run_node(node_root, r"""
      import { buildTree, membersText, describeItem } from "./js/teams.js";
      const pos = (role_id, reports, reports_to = null) => ({ role_id, purpose: "p", members: [], reports_to, reports, status: "vacant", pending_removal: false });
      const tree = buildTree({ roots: ["a"], positions: [pos("a", ["b", "c"]), pos("b", ["d"], "a"), pos("c", [], "a"), pos("d", [], "b")] });
      const forest = buildTree({ roots: ["a", "z"], positions: [pos("a", []), pos("z", [])] });
      const cycle = buildTree({ roots: [], positions: [pos("x", ["y"], "y"), pos("y", ["x"], "x")] });
      const shape = (nodes) => nodes.map((n) => [n.position.role_id, shape(n.children)]);
      console.log(JSON.stringify({ tree: shape(tree), forest: shape(forest), cycle: shape(cycle),
        members: membersText([{ id: "m1", kind: "human", name: "Ana" }, { id: "m2", kind: "agent", name: null }]), none: membersText([]),
        assignment: describeItem("assignments", { role: "lead", member: "ana" }),
        link: describeItem("relationships", { type: "reviews", from: "a", to: "b" }) }));
    """)
    assert out["tree"] == [["a", [["b", [["d", []]]], ["c", []]]]]
    assert out["forest"] == [["a", []], ["z", []]]
    assert out["cycle"] == [["x", [["y", []]]]]          # each position is drawn once: no loop
    assert out["members"] == "Ana (human), m2 (agent)" and out["none"] == "Nadie"
    assert out["assignment"] == "ana en lead" and out["link"] == "a revisa a b"


@needs_node
def test_the_list_and_the_diff_are_written_out_as_text(node_root):
    out = run_node(node_root, WALKER + r"""
      import { TeamList, DiffView } from "./js/teams.js";
      const approved = { id: "tmv-1", version: 1, status: "approved", decided_at: "2026-11-18T10:00:00+00:00" };
      const pending = { id: "tmv-2", version: 2, status: "proposed", proposed_at: "2026-11-18T11:00:00+00:00" };
      const teams = [{ bundle: "alfa", approved, pending }, { bundle: "beta", approved: null, pending: null }];
      const list = text(expand(TeamList({ data: { teams } })));
      const empty = text(expand(TeamList({ data: { teams: [] } })));
      const diff = { has_changes: true, base_version: 1, team_fields: { description: { before: "a", after: "b" } },
        roles: { added: [{ id: "auditor" }], removed: [], changed: [{ id: "lead", fields: { purpose: { before: "x", after: "y" } } }] },
        members: { added: [], removed: [], changed: [] }, assignments: { added: [{ role: "auditor", member: "ana" }], removed: [], changed: [] },
        relationships: { added: [{ type: "reports_to", from: "auditor", to: "lead" }], removed: [], changed: [] } };
      console.log(JSON.stringify({ list, empty, diff: text(expand(DiffView({ diff }))), none: text(expand(DiffView({ diff: { has_changes: false } }))) }));
    """)
    for word in ("alfa", "v1", "Propuesta", "v2", "beta", "Ninguna"):
        assert word in out["list"], word
    assert "Ningún bundle" in out["empty"]
    for word in ("Datos del equipo", "description", "Puestos", "Añadidos", "auditor", "Cambiados", "lead", "ana en auditor", "auditor reporta a lead"):
        assert word in out["diff"], word
    assert "Sin cambios" in out["none"]
