# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI2-T08: the agent tree view. Static checks of the client code and the stylesheet (route, roles, keyboard, help,
no HTML injection, polling and stream clean-up), the pure model run under Node when it is installed, and server checks
that the tree reads only fields the real answers have. Synthetic data only: no real folders and no model calls."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui import STATIC_DIR
from multiagent.gui.security import CSP
from multiagent.gui.token import ensure_token
from multiagent.sessions.children import ChildAgent, ChildStatus
from session_fixtures import CLAUDE_ID, jsonl, make_services, make_stores, write_claude
from test_gui_sessions_view import FORBIDDEN

JS = STATIC_DIR / "js"
CSS = STATIC_DIR / "agent-tree.css"
NODE = shutil.which("node")
TREE_FILES = ["agent-tree.js", "agent-tree-model.js"]
TOPICS = ["open", "view", "back", "filter", "node", "open_child", "log"]


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


@pytest.fixture
def world(tmp_path):
    stores = make_stores(tmp_path)
    services = make_services(stores)
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, stores=stores, auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


# --- the route and where it is linked from -----------------------------------------------------------------------

def test_route_is_registered_linked_from_the_session_header_and_mounted_by_main():
    route, main, detail = read("route.js"), read("main.js"), read("session-detail.js")
    assert re.search(r'SECTION_IDS = \[[^\]]*"tree"', route)
    assert 'routeHref("tree", summary.id)' in detail and "Árbol de agentes" in detail
    assert 'import { AgentTree } from "./agent-tree.js"' in main
    assert "<${AgentTree} key=${route.id" in main            # one tree per session: no state of another one is kept
    # the tree is a view of the session: the sidebar and the right panel keep treating it as that session
    assert 'context = tree ? { section: "sessions", id: route.id } : route' in main
    assert "<${RightPanel} token=${token} route=${context} />" in main
    # the list of sessions stays mounted (its filters and pages survive the trip), as before this task
    assert 'active=${section === "sessions"}' in main
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/gui/static/agent-tree.css">' in html


def run_node(script: str):
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_route_parses_the_tree_hash():
    script = """
      import { parseRoute, routeHref } from %s;
      console.log(JSON.stringify([parseRoute("#tree/claude%%3Aabc"), parseRoute("#tree"), routeHref("tree", "codex:1")]));
    """ % json.dumps((JS / "route.js").as_uri())
    out = run_node(script)
    assert out[0] == {"section": "tree", "id": "claude:abc"}
    assert out[1] == {"section": "tree", "id": None}
    assert out[2] == "#tree/codex%3A1"


# --- accessibility, keyboard, help, safe rendering -----------------------------------------------------------------

def test_tree_markup_has_roles_a_roving_tab_stop_and_the_keyboard():
    view, model = read("agent-tree.js"), read("agent-tree-model.js")
    assert 'role="tree"' in view and 'role="treeitem"' in view and 'role="group"' in view
    assert "aria-selected" in view and "aria-level" in view and "aria-label=${nodeAriaLabel(view)}" in view
    assert "tabindex=${tabStop ? 0 : -1}" in view and "data-node-id" in view      # one tab stop (roving)
    assert "aria-labelledby" in view and "aria-busy" in view and 'role="alert"' in view and 'role="status"' in view
    for key in ('"Enter"', '" "', '"Escape"'):
        assert key in view, key
    for key in ('"ArrowDown"', '"ArrowUp"', '"Home"', '"End"'):
        assert key in model, key
    assert "preventDefault" in view
    # a node chosen on a narrow screen brings its detail (stacked below the tree) into view
    assert "scrollIntoView({ block: \"nearest\" })" in view and "reveal.current = true" in view
    # the level and the status are written, never colour only; the dots are decoration
    assert 'aria-hidden="true"' in view and "view.level || view.levelText" in view and "status.label" in view
    # the tree item itself is the focusable element: no control nested inside it
    item = view[view.index("function NodeItem"):view.index("function Detail")]
    assert "<button" not in item and "<a " not in item and "<input" not in item


def test_help_topics_exist_and_the_view_uses_them():
    text, view = read("help-text.js"), read("agent-tree.js")
    for topic in TOPICS:
        assert f'"agent_tree.{topic}": {{' in text, topic
        assert f'topic="agent_tree.{topic}"' in view + read("session-detail.js"), topic
    for control in ("back", "filter", "node", "open_child"):
        assert f'helpFor("agent_tree.{control}")' in view, control
    assert 'helpFor("agent_tree.open")' in read("session-detail.js")


@pytest.mark.parametrize("name", TREE_FILES)
def test_tree_code_has_no_html_injection_eval_inline_handlers_or_remote_code(name):
    text = read(name)
    for pattern, label in FORBIDDEN:
        assert not re.search(pattern, text), f"{name}: {label}"
    assert "__html" not in text and "style=" not in text and "document.body" not in text


def test_stylesheet_has_focus_touch_targets_reduced_motion_dark_mode_and_no_inline_style_escape():
    sheet = CSS.read_text(encoding="utf-8")
    for forbidden in ("@import", "url(", "style=", "http://", "https://", "!important", "position: fixed"):
        assert forbidden not in sheet, forbidden
    assert ".at-node:focus-visible" in sheet and "outline: 2px solid var(--accent)" in sheet
    assert "min-height: var(--target)" in sheet                      # nodes and the filter keep a 44 px target
    assert "prefers-reduced-motion" in sheet and "prefers-color-scheme: dark" in sheet
    for level in ("T1", "T2", "T3", "T4"):
        assert f".lv-{level}" in sheet and f"--lv-{level}-bg" in sheet
    assert "position: sticky" in re.search(r"\.at-detail \{([^}]*)\}", sheet)[1]    # the detail stays in sight beside a long tree
    # the «En espera» state is a static ring (it never animates)
    ring = re.search(r"\.at-dot\.tone-hold \{([^}]*)\}", sheet)[1]
    assert "animation" not in ring and "border" in ring
    # 375 px: the columns wrap instead of overflowing and long text breaks
    assert "flex-wrap: wrap" in sheet and "overflow-wrap: anywhere" in sheet and "min-width: 0" in sheet


# --- polling and stream clean-up -----------------------------------------------------------------------------------

def test_view_follows_the_children_stream_with_a_ten_second_fallback_and_cleans_up():
    view = read("agent-tree.js")
    assert 'import { followChildren } from "./subagents-panel.js"' in view
    assert re.search(r"TICK_MS = 10000;", view) and "setInterval(" in view
    assert "return () => { alive.current = false; stop(); clearInterval(timer); };" in view
    assert "const mine = ++ticket.current;" in view and "mine !== ticket.current" in view     # no stale answer wins
    assert "event.version !== version.current" in view                                         # no refetch for the same state
    assert "!connected.current || ticks % CONNECTED_EVERY === 0" in view                       # poll only when needed
    assert "requestAnimationFrame" not in view and "setTimeout(" not in view                   # nothing left running


def test_new_files_are_served_with_the_csp(world):
    for name in TREE_FILES:
        response = world.client.get(f"/gui/static/js/{name}")
        assert response.status_code == 200 and "javascript" in response.headers["content-type"], name
        assert response.headers["content-security-policy"] == CSP
    sheet = world.client.get("/gui/static/agent-tree.css")
    assert sheet.status_code == 200 and ".at-node" in sheet.text and sheet.headers["content-security-policy"] == CSP


# --- the fields the tree reads exist in the API answers ----------------------------------------------------------------

def test_fields_read_by_the_tree_exist_in_the_children_and_session_answers(world):
    keys = set(ChildAgent(id="x", source="claude", kind="subagent", name=None, label=None, model=None, level=None,
                          status=ChildStatus.RUNNING, started_at=None, last_activity_at=None, tokens=None,
                          link_id=None).to_dict())
    code = read("agent-tree-model.js") + read("agent-tree.js")
    used = set(re.findall(r"\bchild\.([a-z_]+)", code))
    # reset_at is optional: no reader gives it yet and the tree is silent without it (see the model tests)
    assert used - {"reset_at"} <= keys, used - keys

    write_claude(world.stores)
    real = datetime.now(UTC)
    when = (real - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = world.stores.claude / "projects" / "proj" / CLAUDE_ID / "subagents" / "agent-zz.jsonl"
    jsonl(path, [{"type": "user", "timestamp": when, "message": {"role": "user", "content": "hola"}}])
    path.with_suffix(".meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Mira el árbol"}),
                                              encoding="utf-8")
    base = f"/gui/api/sessions/claude:{CLAUDE_ID}"
    answer = world.client.get(base + "/children", headers=world.auth).json()
    summary = world.client.get(base, headers=world.auth).json()
    child = answer["children"][0]
    assert (child["name"], child["label"], child["status"]) == ("Explore", "Mira el árbol", "running")
    assert used - {"reset_at"} <= set(child) and {"version", "warnings"} <= set(answer)
    # mode and profile are not part of the summary yet: the tree shows them only when they appear
    optional = {"mode", "profile"}
    wanted = set(re.findall(r"\bsummary\.([a-z_]+)", code))
    assert wanted - optional <= set(summary), wanted - set(summary)


# --- the pure model, run under Node ----------------------------------------------------------------------------------

MODEL = json.dumps((JS / "agent-tree-model.js").as_uri())
NOW_MS = "Date.parse('2026-10-11T12:00:00Z')"

ROWS = """
  const kids = [
    { id: "a", source: "claude", kind: "subagent", name: "Explore", label: "Revisa la ayuda", model: "claude-sonnet-5-5", level: "T2",
      status: "running", note: null, started_at: "2026-10-11T11:40:00Z", last_activity_at: "2026-10-11T11:59:00Z",
      tokens: { input: 1500, output: 500 }, link_id: "claude:1:agent-a" },
    { id: "b", source: "codex", kind: "thread", name: "Banach", label: "Escribe pruebas", model: "gpt-6-luna", level: "T1",
      status: "testing", note: null, started_at: "2026-10-11T11:30:00Z", last_activity_at: "2026-10-11T11:58:00Z",
      tokens: null, link_id: "codex:2" },
    { id: "c", source: "copilot", kind: "subagent", name: null, label: "Documenta", model: "gpt-6-luna", level: "T1",
      status: "idle", note: null, started_at: "2026-10-11T10:00:00Z", last_activity_at: "2026-10-11T11:20:00Z",
      tokens: null, link_id: null },
    { id: "d", source: "claude", kind: "subagent", name: "General", label: "Terminó", model: "claude-opus-5-5", level: "T3",
      status: "done", note: null, started_at: "2026-10-11T09:00:00Z", last_activity_at: "2026-10-11T09:30:00Z",
      tokens: null, link_id: "claude:1:agent-d" },
    { id: "e", source: "claude", kind: "subagent", name: "Plan", label: "Parado", model: "otro-modelo", level: null,
      status: "stopped", note: null, started_at: "2026-10-11T07:00:00Z", last_activity_at: "2026-10-11T08:00:00Z",
      tokens: null, link_id: null },
    { id: "f", source: "codex", kind: "thread", name: "Curie", label: "Espera el límite", model: "gpt-6-sol", level: "T3",
      status: "idle", note: "waiting_limit", reset_at: "2026-10-11T16:10:00Z", started_at: "2026-10-11T08:00:00Z",
      last_activity_at: "2026-10-11T10:00:00Z", tokens: null, link_id: "codex:3" },
    { id: "g", source: "framework", kind: "step", name: "propose", label: null, model: "claude-opus-5-5", level: "T3",
      status: "idle", note: "waiting_human", started_at: "2026-10-11T11:00:00Z", last_activity_at: "2026-10-11T11:30:00Z",
      tokens: null, link_id: null },
  ];
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_groups_counts_and_the_only_active_filter_agree_with_the_subagents_panel():
    out = run_node(f"""
      import {{ groupByLevel, countStatuses, filterChildren, bucketOf, visibleOrder }} from {MODEL};
      {ROWS}
      const grouped = groupByLevel(kids);
      console.log(JSON.stringify({{
        groups: Object.fromEntries(Object.entries(grouped).map(([k, v]) => [k, v.map((x) => x.id)])),
        counts: countStatuses(kids),
        buckets: Object.fromEntries(kids.map((k) => [k.id, bucketOf(k)])),
        only: filterChildren(kids, true).map((x) => x.id),
        all: filterChildren(kids, false).length,
        order: visibleOrder(grouped),
        empty: [groupByLevel(), countStatuses(), filterChildren()],
      }}));
    """)
    assert out["groups"] == {"T1": ["b", "c"], "T2": ["a"], "T3": ["d", "f", "g"], "T4": [], "unknown": ["e"]}
    # idle counts as active (the server's rule); a child waiting for a limit or a person is counted apart;
    # done and stopped are both «terminados»
    assert out["buckets"] == {"a": "active", "b": "active", "c": "active", "d": "done", "e": "done",
                              "f": "waiting", "g": "waiting"}
    assert out["counts"] == {"active": 3, "done": 2, "waiting": 2}
    assert out["only"] == ["a", "b", "c", "f", "g"] and out["all"] == 7
    assert out["order"] == ["root", "b", "c", "a", "d", "f", "g", "e"]
    assert out["empty"][0] == {"T1": [], "T2": [], "T3": [], "T4": [], "unknown": []}
    assert out["empty"][1] == {"active": 0, "done": 0, "waiting": 0} and out["empty"][2] == []


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_waiting_state_is_static_text_that_is_silent_when_the_data_is_missing():
    out = run_node(f"""
      import {{ waitingText, isWaiting, statusView }} from {MODEL};
      {ROWS}
      const by = Object.fromEntries(kids.map((k) => [k.id, k]));
      console.log(JSON.stringify({{
        limit: waitingText(by.f, "en-GB"),
        noReset: waitingText({{ status: "idle", note: "waiting_limit" }}),
        onlyReset: waitingText({{ status: "idle", reset_at: "2026-10-11T16:10:00Z" }}, "en-GB"),
        badReset: waitingText({{ status: "idle", note: "waiting_limit", reset_at: "no es una fecha" }}),
        none: [waitingText(by.a), waitingText(null), waitingText({{}})],
        finished: waitingText({{ status: "done", note: "waiting_limit", reset_at: "2026-10-11T16:10:00Z" }}),
        waiting: kids.map((k) => isWaiting(k)),
        viewF: statusView(by.f), viewG: statusView(by.g), viewA: statusView(by.a), viewB: statusView(by.b),
        viewE: statusView(by.e),
      }}));
    """)
    assert re.fullmatch(r"En espera · hasta \d{2}:\d{2}", out["limit"])
    assert out["noReset"] == "En espera · hora de restablecimiento no disponible" == out["badReset"]
    assert re.fullmatch(r"En espera · hasta \d{2}:\d{2}", out["onlyReset"])
    assert out["none"] == [None, None, None] and out["finished"] is None
    assert out["waiting"] == [False, False, False, False, False, True, True]
    assert out["viewF"]["tone"] == "hold" and out["viewF"]["label"].startswith("En espera · hasta ")
    assert out["viewG"]["tone"] == "wait" and out["viewG"]["label"] == "Esperando a una persona"
    assert (out["viewA"]["label"], out["viewA"]["tone"]) == ("Trabajando", "run")
    assert (out["viewB"]["label"], out["viewB"]["tone"]) == ("Ejecutando prueba", "test")
    assert (out["viewE"]["label"], out["viewE"]["tone"]) == ("Detenido", "quiet")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_nodes_are_described_in_plain_text_with_time_level_and_links():
    out = run_node(f"""
      import {{ describeNode, describeRoot, elapsedText, durationText, nodeAriaLabel, detailFacts, rootFacts, childLink,
        levelText }} from {MODEL};
      {ROWS}
      const now = {NOW_MS};
      const by = Object.fromEntries(kids.map((k) => [k.id, k]));
      const summary = {{ id: "claude:1", source: "claude", surface: "desktop", title: "Diseño", cwd: "C:\\\\work\\\\proj",
        created_at: "2026-10-11T11:00:00Z", models: ["claude-opus-5-5"], status: "active", child_count: 7,
        tokens: {{ input: 10, output: 5, cache_read: null, cache_write: null, total: null }} }};
      console.log(JSON.stringify({{
        a: describeNode(by.a, now), c: describeNode(by.c, now), g: describeNode(by.g, now),
        root: describeRoot(summary, now), rootNone: describeRoot(null, now),
        elapsed: [elapsedText(by.a, now), elapsedText(by.d, now), elapsedText({{ status: "running" }}, now),
          elapsedText({{ status: "done", started_at: "2026-10-11T09:00:00Z" }}, now)],
        durations: [durationText(0), durationText(59000), durationText(60000), durationText(3600000),
          durationText(3900000), durationText(90000000), durationText(86400000)],
        aria: nodeAriaLabel(describeNode(by.a, now)),
        facts: detailFacts(by.f, now), rootFacts: rootFacts(summary, now), rootFactsNone: rootFacts(null, now),
        links: [childLink(by.a, "claude:1"), childLink(by.c, "claude:1"), childLink(by.g, "framework:run1"),
          childLink(null, "x")],
        levels: ["T1", "T4", null, "T9"].map(levelText),
      }}));
    """)
    a = out["a"]
    assert (a["title"], a["what"], a["level"], a["tier"], a["model"]) == (
        "Explore", "Revisa la ayuda", "T2", "T2", "claude-sonnet-5-5")
    assert a["elapsed"] == "Lleva 20 min" and a["tokens"] == "2 k tokens" and a["status"]["label"] == "Trabajando"
    # no name: a generic title from the kind and the id, and the description stays in «en qué trabaja»
    assert out["c"]["title"].startswith("Subagente ") and out["c"]["what"] == "Documenta"
    assert out["g"]["title"] == "propose" and out["g"]["what"] == "Sin descripción"
    assert out["root"]["title"] == "Diseño" and out["root"]["level"] is None and out["root"]["tier"] == "root"
    assert out["root"]["levelText"] == "Orquestador" and out["root"]["status"]["label"] == "Activa"
    assert out["root"]["elapsed"].startswith("Iniciada ") and out["root"]["tokens"] == "entrada 10 · salida 5"
    assert out["rootNone"]["title"] == "Sesión principal" and out["rootNone"]["elapsed"] == "Inicio no disponible"
    assert out["elapsed"] == ["Lleva 20 min", "Duró 30 min", "Tiempo no disponible", "Duración no disponible"]
    assert out["durations"] == ["menos de 1 min", "menos de 1 min", "1 min", "1 h", "1 h 5 min", "1 d 1 h", "1 d"]
    assert out["aria"].startswith("Explore. nivel T2. Trabajando. claude-sonnet-5-5.")
    facts = dict(out["facts"])
    assert facts["Aplicación"] == "Codex" and facts["Tipo"] == "Hilo" and facts["Nivel"] == "T3 · avanzado"
    assert facts["Estado"].startswith("En espera · hasta ") and facts["Última actividad"] != "No consta"
    assert dict(out["rootFacts"])["Agentes lanzados"] == "7" and dict(out["rootFacts"])["Carpeta"] == "C:\\work\\proj"
    assert out["rootFactsNone"] == [["Estado", "Estado desconocido"]]
    for rows in (out["facts"], out["rootFacts"]):
        assert all(value and "undefined" not in value and "null" not in value for _, value in rows), rows
    assert out["links"][0] == {"section": "sessions", "id": "claude:1:agent-a", "label": "Abrir conversación"}
    assert out["links"][1] is None
    assert out["links"][2] == {"section": "runs", "id": "run1", "label": "Ver la ejecución"} and out["links"][3] is None
    assert out["levels"] == ["T1 · económico", "T4 · máximo", "Sin nivel", "Sin nivel"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_keyboard_movement_and_the_log_of_status_changes():
    out = run_node(f"""
      import {{ moveFocus, snapshot, diffSnapshots, visibleOrder, groupByLevel }} from {MODEL};
      {ROWS}
      const ids = visibleOrder(groupByLevel(kids.slice(0, 3)));
      const before = snapshot(kids.slice(0, 3));
      const moved = kids.slice(0, 3).map((k) => (k.id === "a" ? {{ ...k, status: "done" }} : k));
      moved.push(kids[5]);
      console.log(JSON.stringify({{
        ids,
        moves: [moveFocus(ids, "root", "ArrowDown"), moveFocus(ids, "root", "ArrowUp"),
          moveFocus(ids, ids[ids.length - 1], "ArrowDown"), moveFocus(ids, "b", "Home"), moveFocus(ids, "b", "End"),
          moveFocus(ids, "b", "ArrowRight"), moveFocus(ids, "ghost", "ArrowDown")],
        same: diffSnapshots(before, snapshot(kids.slice(0, 3))),
        changed: diffSnapshots(before, snapshot(moved)),
      }}));
    """)
    assert out["ids"] == ["root", "b", "c", "a"]
    assert out["moves"] == ["b", "root", "a", "root", "a", None, "b"]       # no wrap-around; an unknown id starts at the top
    assert out["same"] == []
    assert out["changed"][0] == "Explore · Trabajando → Terminado"
    assert out["changed"][1].startswith("Curie · aparece · En espera · hasta ")
