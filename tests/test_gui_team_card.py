# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CHT-T05: the card of a team version (rules, permissions, resolved model and mode per role), its full diff and its chat thread.

Checked over real services in Mock: the version view carries the card, a first version compares against an empty team
(header included), a role without agent or with a model that is not in the catalog says so without failing, the thread
route gives one stop with the decisions the server accepts, and the pure model, the router and the wiring run under Node.
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
from multiagent.services.teams import _role_models, compute_team_diff

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture
def env(tmp_path):
    team = tmp_path / "case"
    shutil.copytree(GENERAL_BUNDLE, team)
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"case": team})
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token, team_dir=team)
    services.runs.close()


def get(env, path):
    return env.client.get("/gui/api/threads" + path, headers={"Authorization": f"Bearer {env.token}"})


def edit_team(env, change):
    path = env.team_dir / "team.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def roles(card):
    return {role["id"]: role for role in card["roles"]}


# --- the service: the card ---------------------------------------------------------------------------------------

def test_the_version_carries_rules_permissions_resolved_model_and_mode_per_role(env):
    edit_team(env, lambda data: (data.update(mode="supervised"),
                                 data["roles"][1].update(permissions=["read", "write"])))
    version = env.services.teams.propose("case", actor="agent:codex/gpt", channel="python")
    card = env.services.teams.get_card(version["id"])["card"]
    assert card["mode"] == "supervised" and card["model_error"] is None
    analyst = roles(card)["analyst"]
    assert analyst["rules"] == ["Ground every point in the provided source text."]
    assert analyst["permissions"] == ["read", "write"] and analyst["purpose"].startswith("Extracts")
    assert [item["agent"] for item in analyst["members"]] == ["document_analyst", "research_analyst"]
    assert analyst["model"]["status"] == "resolved" and analyst["model"]["missing"] == []
    model = analyst["model"]["models"][0]
    assert model["id"] == "local_default" and model["provider"] == "ollama" and model["tier"] == "local"


def test_a_role_held_by_a_person_has_no_agent_and_says_so(env):
    version = env.services.teams.propose("case", actor="agent:codex/gpt", channel="python")
    card = env.services.teams.get_card(version["id"])["card"]
    assert card["mode"] is None                                         # the example team declares no mode
    lead = roles(card)["lead"]
    assert lead["model"] == {"status": "no_agent", "models": [], "missing": []}
    assert lead["members"] == [{"id": "human_lead", "kind": "person", "name": "Human Coordinator", "agent": None}]


def test_a_model_that_left_the_catalog_is_reported_not_raised(env):
    version = env.services.teams.propose("case", actor="agent:codex/gpt", channel="python")
    for path in (env.team_dir / "workflows").glob("*.yaml"):            # the version stores the team, not the catalog
        path.write_text(path.read_text(encoding="utf-8").replace("preferred_model: local_default",
                                                                 "preferred_model: ghost_model"), encoding="utf-8")
    card = env.services.teams.get_card(version["id"])["card"]
    reviewer = roles(card)["reviewer"]["model"]
    assert reviewer == {"status": "not_in_catalog", "models": [], "missing": ["ghost_model"]}


def test_an_unreadable_catalog_leaves_the_roles_without_a_model_and_does_not_fail(env):
    version = env.services.teams.propose("case", actor="agent:codex/gpt", channel="python")
    (env.team_dir / "catalog" / "models.yaml").write_text("models: [unclosed", encoding="utf-8")
    card = env.services.teams.get_card(version["id"])["card"]
    assert card["model_error"] and roles(card)["analyst"]["model"]["status"] == "unavailable"
    assert roles(card)["lead"]["model"]["status"] == "no_agent"


def test_role_models_names_each_reason():
    step = lambda agent, preferred, fallback=None, role=None, function=None: SimpleNamespace(   # noqa: E731
        agent=agent, preferred_model=preferred, fallback_model=fallback, role=role, function=function)
    catalog = SimpleNamespace(agents={"a": {}, "b": {}}, models={"m1": SimpleNamespace(
        id="m1", provider="p", model="x", tier="local", host="local")})
    role = {"id": "r"}
    assert _role_models(role, [], [step("a", "m1")], catalog)["status"] == "no_agent"
    assert _role_models(role, ["a"], [step("b", "m1")], catalog)["status"] == "no_step"
    assert _role_models(role, ["ghost"], [step("b", "m1")], catalog)["status"] == "agent_not_in_catalog"
    assert _role_models(role, ["a"], [step("a", "m1", "m2")], catalog)["status"] == "partial"
    assert _role_models(role, ["a"], [step("a", "m2")], catalog)["status"] == "not_in_catalog"
    assert _role_models(role, ["a"], [step("a", "m1")], catalog)["status"] == "resolved"
    assert _role_models(role, ["a"], [step(None, "m1", role="r")], catalog)["status"] == "resolved"   # a role step
    assert _role_models(role, ["a"], [step(None, None, function="f")], catalog)["status"] == "no_step"


# --- the service: the first version against nothing --------------------------------------------------------------

def test_a_first_version_compares_against_an_empty_team_and_its_mode_counts():
    bare = {"schema_version": 1, "id": "tiny", "description": "A team with nothing in it.", "mode": "supervised",
            "roles": [], "members": [], "assignments": [], "relationships": []}
    diff = compute_team_diff(None, bare)
    assert diff["has_changes"] is True and diff["base_version"] is None
    assert set(diff["team_fields"]) == {"mode"}                         # id, description and schema_version are not "changes"
    assert diff["team_fields"]["mode"] == {"before": None, "after": "supervised"}


def test_the_first_version_in_the_service_has_a_full_diff_with_rules_and_permissions(env):
    edit_team(env, lambda data: data.update(mode="autonomous"))
    version = env.services.teams.propose("case", actor="agent:codex/gpt", channel="python")
    for diff in (version["diff"], env.services.teams.get(version["id"])["diff"]):
        assert diff["has_changes"] and diff["team_fields"]["mode"]["after"] == "autonomous"
        added = {item["id"]: item for item in diff["roles"]["added"]}
        assert set(added) == {"lead", "analyst", "writer", "reviewer"}
        assert added["lead"]["rules"] == ["Approve outputs only after quality review passes."]
        assert len(diff["members"]["added"]) == 6 and len(diff["relationships"]["added"]) == 5
    env.services.teams.approve(version["id"], actor="user:ana", channel="python")
    again = env.services.teams.get(version["id"])["diff"]               # approved first version: still against nothing
    assert again["base_version"] is None and again["has_changes"] and len(again["roles"]["added"]) == 4


# --- the router ----------------------------------------------------------------------------------------------------

def test_the_teams_route_returns_the_card_and_the_list_keeps_it_out(env):
    version = env.services.teams.propose("case", actor="agent:codex/gpt", channel="python")
    headers = {"Authorization": f"Bearer {env.token}"}
    body = env.client.get(f"/gui/api/teams/versions/{version['id']}", headers=headers).json()
    assert set(body["card"]) == {"mode", "roles", "model_error"} and len(body["card"]["roles"]) == 4
    entry = env.client.get("/gui/api/teams", headers=headers).json()["teams"][0]
    assert "card" not in entry["pending"]


def test_the_team_thread_is_one_stop_with_the_decisions_the_server_accepts(env):
    version = env.services.teams.propose("case", actor="agent:codex/gpt", channel="python")
    page = get(env, f"/teams/{version['id']}").json()
    assert page["version_id"] == version["id"] and page["status"] == "proposed" and page["cursor"] == 1
    (entry,) = page["entries"]
    assert entry["kind"] == "team_version" and entry["seq"] == 1 and entry["version"]["bundle"] == "case"
    assert entry["decision"] == {"type": "team_approval", "step_id": "", "attempt": None, "actions": ["approve", "reject"]}
    assert len(entry["card"]["roles"]) == 4 and entry["diff"]["has_changes"]
    assert "team" not in entry and "team" not in entry["version"]
    env.services.teams.approve(version["id"], actor="user:ana", channel="python")
    done = get(env, f"/teams/{version['id']}").json()
    assert done["status"] == "approved" and done["entries"][0]["decision"]["actions"] == []


def test_the_team_thread_asks_for_the_token_and_knows_missing_and_bad_ids(env):
    assert env.client.get("/gui/api/threads/teams/tmv-000000000000").status_code == 401
    assert get(env, "/teams/tmv-000000000000").status_code == 404
    assert get(env, "/teams/not-an-id").status_code == 422 or get(env, "/teams/not-an-id").status_code == 400


def test_deciding_from_the_card_uses_the_existing_routes_and_fixes_actor_and_channel(env):
    version = env.services.teams.propose("case", actor="agent:codex/gpt", channel="python")
    answer = env.client.post(f"/gui/api/teams/versions/{version['id']}/approve", json={"note": "ok", "actor": "user:x"},
                             headers={"Authorization": f"Bearer {env.token}"})
    assert answer.status_code == 200
    assert answer.json()["decided_by"] == "user:operator" and answer.json()["decided_channel"] == "gui"


# --- the pure model, the route and the wiring (Node) ------------------------------------------------------------------

@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("team-card-static")
    shutil.copytree(JS, root / "js")
    shutil.copytree(STATIC_DIR / "i18n", root / "i18n")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    return root


def run_node(root, body):
    script = root / "card.mjs"
    script.write_text('import { setLanguage } from "./js/i18n.js";\nsetLanguage("es");\n' + body, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_every_script_passes_node_check():
    for name in ("team-card-model.js", "team-thread.js", "team-card.js", "thread-cards.js", "teams.js", "main.js", "route.js", "help-text.js"):
        result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr)


@needs_node
def test_the_route_reads_a_team_version_and_leaves_run_threads_alone(node_root):
    out = run_node(node_root, r"""
      import { parseRoute, teamThreadHref } from "./js/route.js";
      console.log(JSON.stringify({
        team: parseRoute("#new/team/tmv-0123456789ab"), encoded: parseRoute("#new/team/a%20b"),
        run: parseRoute("#new/run/run-1"), bare: parseRoute("#new"), teams: parseRoute("#teams/general"),
        href: teamThreadHref("tmv-0123456789ab")}));
    """)
    assert out["team"] == {"section": "new", "id": "tmv-0123456789ab", "kind": "team"}
    assert out["encoded"]["id"] == "a b"
    assert out["run"] == {"section": "new", "id": "run-1"} and out["bare"] == {"section": "new", "id": None}
    assert out["teams"] == {"section": "teams", "id": "general"}
    assert out["href"] == "#new/team/tmv-0123456789ab"


@needs_node
def test_the_card_model_writes_every_part_as_text(node_root):
    out = run_node(node_root, r"""
      import * as m from "./js/team-card-model.js";
      const model = { id: "local_default", provider: "ollama", model: "qwen3:8b", tier: "local" };
      const card = { mode: "supervised", roles: [
        { id: "analyst", purpose: "Reads.", rules: ["Cite.", "Be brief."], permissions: ["read", "external"],
          members: [{ id: "a1", kind: "agent", agent: "doc" }, { id: "h", kind: "person", name: "Ana" }],
          model: { status: "resolved", models: [model], missing: [] } },
        { id: "lead", rules: [], permissions: [], members: [], model: { status: "no_agent", models: [], missing: [] } },
        { id: "gone", rules: [], permissions: [], members: [{ id: "x", kind: "agent", agent: "doc" }],
          model: { status: "not_in_catalog", models: [], missing: ["ghost"] } }] };
      const rows = m.cardRoles(card);
      const diff = { base_version: null, team_fields: { mode: { before: null, after: "supervised" } },
        roles: { added: [{ id: "analyst", rules: ["Cite."], permissions: ["read"] }], removed: [], changed: [] },
        members: { added: [{ id: "a1", kind: "agent", agent: "doc" }], removed: [], changed: [] },
        assignments: { added: [{ role: "analyst", member: "a1" }], removed: [], changed: [] },
        relationships: { added: [{ type: "reports_to", from: "analyst", to: "lead" }], removed: [], changed: [] } };
      console.log(JSON.stringify({
        modes: [m.modeText("supervised"), m.modeText("autonomous"), m.modeText(null)],
        analyst: rows[0], lead: rows[1], gone: rows[2],
        diff: m.diffLines(diff), nothing: m.diffLines({ team_fields: {}, roles: {}, base_version: 2 }),
        none: m.diffLines(null),
        decide: [m.decisionActions({ decision: { actions: ["approve", "changes", "reject"] } }),
                 m.decisionActions({ decision: { actions: [] } }), m.canDecide(null)]}));
    """)
    assert out["modes"] == ["Supervisado", "Autónomo", "Sin modo declarado"]
    rows = {row["key"]: row["text"] for row in out["analyst"]["rows"]}
    assert rows["rules"] == "Cite.; Be brief." and rows["permissions"] == "leer, salir a servicios externos"
    assert rows["model"] == "qwen3:8b (local_default, ollama, local)"
    assert rows["members"] == "a1 (agente doc), Ana (persona)"
    assert out["analyst"]["notes"] == [] and out["analyst"]["modelStatus"] == "resolved"
    lead = {row["key"]: row["text"] for row in out["lead"]["rows"]}
    assert lead["rules"] == "Sin reglas declaradas" and lead["permissions"] == "Ninguno" and lead["members"] == "Nadie"
    assert lead["model"] == "Sin modelo resuelto" and "Sin agente" in out["lead"]["notes"][0]
    assert out["gone"]["modelStatus"] == "not_in_catalog" and any("ghost" in note for note in out["gone"]["notes"])
    diff = out["diff"]
    assert diff["empty"] is False and "no hay versión aprobada" in diff["base"].lower()
    assert diff["header"] == ["Modo: (vacío) → supervised"]
    sections = {part["section"]: part["buckets"][0]["lines"] for part in diff["sections"]}
    assert sections["roles"] == ["analyst (1 regla; permisos: leer)"] and sections["members"] == ["a1 (agente doc)"]
    assert sections["assignments"] == ["a1 en analyst"] and sections["relationships"] == ["analyst reporta a lead"]
    assert out["nothing"]["empty"] is True and out["none"]["empty"] is True
    assert out["decide"] == [["approve", "reject"], [], False]


# --- the wiring (static) -----------------------------------------------------------------------------------------------

def read(name):
    return ui_text((JS / name))


def test_the_chat_view_the_card_and_equipos_are_wired_together():
    main, thread, cards, teams = read("main.js"), read("team-thread.js"), read("team-card.js"), read("teams.js")
    assert 'route.kind === "team"' in main and "<${TeamThread}" in main and 'import { TeamThread } from "./team-thread.js"' in main
    assert 'route.kind !== "team"' in main and "<${NewSession}" in main
    assert "/gui/api/threads/teams/" in thread and "TeamVersionCard" in thread and "new-session" not in thread
    assert "export function TeamVersionCard" in cards and "export function TeamCardBody" in cards
    assert "TeamCardBody" in teams and "TeamDiffBody" in teams                 # the same card in Equipos
    block = cards.split("CHT-T05: the card of a team version")[1]
    assert block.count("apiPost(") == 1 and "\"/gui/api/teams/versions/\"" in block      # no decision route of its own
    assert "api/threads" not in block


@needs_node
def test_team_version_status_labels_are_shared_and_unknown_codes_are_hidden(node_root):
    out = run_node(node_root, r"""
      import { VERSION_STATUS, versionStatusText } from "./js/ui/labels.js";
      import { TeamVersionCard } from "./js/team-card.js";
      const text = (node) => {
        if (node == null || typeof node === "boolean") return "";
        if (typeof node === "string" || typeof node === "number") return String(node);
        if (Array.isArray(node)) return node.map(text).join("");
        return text(node.props && node.props.children);
      };
      const cardText = (status) => text(TeamVersionCard({
        entry: { text: "Versión", version: { status, bundle: "case" } }, token: "", onDone: () => {}
      }));
      console.log(JSON.stringify({
        labels: [versionStatusText("proposed"), versionStatusText("approved"), versionStatusText("other")],
        keys: Object.keys(VERSION_STATUS),
        cards: [cardText("proposed"), cardText("approved"), cardText("unknown-code")],
      }));
    """)
    assert out["labels"] == ["Propuesta", "Aprobada", "Estado desconocido"]
    assert out["keys"] == ["draft", "proposed", "approved", "rejected"]
    assert "estado Propuesta" in out["cards"][0] and "proposed" not in out["cards"][0]
    assert "estado Aprobada" in out["cards"][1] and "approved" not in out["cards"][1]
    assert "estado Estado desconocido" in out["cards"][2] and "unknown-code" not in out["cards"][2]
    assert "versionStatusText(version.status)" in read("team-card.js")
    assert 'import { VERSION_STATUS, versionStatusText } from "./ui/labels.js"' in read("teams.js")


def test_the_server_fixes_the_actor_so_the_card_never_sends_one():
    block = (JS / "team-card.js").read_text(encoding="utf-8").split("CHT-T05: the card of a team version")[1]
    assert "actor" not in block and "channel" not in block
