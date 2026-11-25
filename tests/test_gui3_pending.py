# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI3-T05: «Pendientes» with tabs: the pure model in Node, no server."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from gui_text import ui_text

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(source: str) -> dict:
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
                            cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


REGISTER = """
  import { registerView, registeredViews } from %(extensions)s;
  const component = () => null;
  const add = (id, label, order, extra = {}) => registerView({ id, owner: id, label, component, order, group: "pending", ...extra });
  add("task_waiting", "Esperan tu decision", 55, { pendingType: "task" });
  add("document_alerts", "Alertas de documentos", 50);
  add("continuity", "Decisiones", 50, { pendingType: "continuity" });
  add("approvals", "Aprobaciones", 45, { pendingType: "approval" });
  registerView({ id: "apps", owner: "apps", label: "Apps", component, group: "primary" });
  const views = registeredViews();
"""


def header() -> str:
    return REGISTER % {"extensions": json.dumps((JS / "extensions.js").as_uri())}


@needs_node
def test_tabs_come_from_what_is_registered_with_a_counter_only_where_the_api_gives_one():
    model = json.dumps((JS / "pending-model.js").as_uri())
    out = run_node(header() + f"""
      import {{ pendingTabs }} from {model};
      const items = [{{ type: "approval" }}, {{ type: "approval" }}, {{ type: "run" }}, {{ type: "task" }}];
      const unknown = pendingTabs(views, items, false);
      console.log(JSON.stringify({{
        tabs: pendingTabs(views, items).map((tab) => [tab.id, tab.label, tab.count ?? null]),
        before: unknown.map((tab) => tab.count ?? null),
        empty: pendingTabs(views, []).map((tab) => tab.count ?? null),
        none: pendingTabs([], []).map((tab) => tab.id),
      }}));
    """)
    # «Todo» first, then the registered views in their order (45, 50, 50 by name, 55); «apps» is not a pending view
    assert out["tabs"] == [["all", "Todo", 4], ["approvals", "Aprobaciones", 2], ["continuity", "Decisiones", 0],
                           ["document_alerts", "Alertas de documentos", None], ["task_waiting", "Esperan tu decision", 1]]
    assert out["before"] == [None] * 5            # no number is claimed before the first answer
    assert out["empty"] == [0, 0, 0, None, 0]
    assert out["none"] == ["all"]


@needs_node
def test_each_tab_has_its_hash_with_and_without_an_id_and_old_addresses_select_their_tab():
    model = json.dumps((JS / "pending-model.js").as_uri())
    route = json.dumps((JS / "route.js").as_uri())
    out = run_node(header() + f"""
      import {{ pendingTabs, routeTab, tabHash, tabRoute }} from {model};
      import {{ parseRoute }} from {route};
      const tabs = pendingTabs(views, []);
      const pick = (hash) => routeTab(parseRoute(hash), tabs);
      console.log(JSON.stringify({{
        hashes: tabs.map((tab) => tabHash(tab.id)),
        withId: tabHash("approvals", "a/b c"), allIgnoresId: tabHash("all", "x"),
        routes: tabs.map((tab) => tabRoute(tab.id)),
        selected: ["#pending", "#approvals", "#approvals/abc", "#continuity", "#document_alerts", "#task_waiting", "#runs", "#apps"].map(pick),
        id: parseRoute("#approvals/a%2Fb").id,
        reserved: parseRoute("#pending"),
      }}));
    """)
    assert out["hashes"] == ["#pending", "#approvals", "#continuity", "#document_alerts", "#task_waiting"]
    assert out["withId"] == "#approvals/a%2Fb%20c" and out["allIgnoresId"] == "#pending"
    assert out["routes"] == [["pending", None], ["approvals", None], ["continuity", None], ["document_alerts", None],
                             ["task_waiting", None]]
    assert out["selected"] == ["all", "approvals", "approvals", "continuity", "document_alerts", "task_waiting", None, None]
    assert out["id"] == "a/b"                                   # #approvals/<id> still carries the id of the card
    assert out["reserved"] == {"section": "pending", "id": None}


@needs_node
def test_a_pending_view_without_pending_type_has_no_counter():
    model = json.dumps((JS / "pending-model.js").as_uri())
    ext = json.dumps((JS / "extensions.js").as_uri())
    out = run_node(f"""
      import {{ registerView, registeredViews }} from {ext};
      import {{ pendingTabs }} from {model};
      registerView({{ id: "plain", owner: "plain", label: "Plain", component: () => null, group: "pending" }});
      console.log(JSON.stringify(pendingTabs(registeredViews(), [{{ type: "approval" }}]).map((tab) => [tab.id, tab.count ?? null])));
    """)
    assert out == [["all", 1], ["plain", None]]


@needs_node
def test_one_poll_serves_every_reader_and_follows_the_visibility():
    model = json.dumps((JS / "pending-model.js").as_uri())
    out = run_node(f"""
      import {{ createPendingStore }} from {model};
      let loads = 0, hidden = false, change = null, nextId = 0;
      const timers = new Map();
      const news = [];
      const store = createPendingStore({{
        load: async () => {{ loads += 1; return {{ answer: {{ items: [{{ id: "i" + loads, since: "2026-11-18T10:00:00Z" }}] }},
          background: {{ items: [] }}, policy: {{ enabled: true, reason: "" }} }}; }},
        setTimer: (run, ms) => {{ const id = ++nextId; timers.set(id, [run, ms]); return id; }},
        clearTimer: (id) => {{ timers.delete(id); }},
        isHidden: () => hidden,
        watchVisibility: (onChange) => {{ change = onChange; return () => {{ change = null; }}; }},
        onNew: (items, seen) => news.push([items.map((i) => i.id), seen]),
      }});
      const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
      const seen = [];
      const first = store.subscribe((state) => seen.push("a" + state.items.length));
      const second = store.subscribe((state) => seen.push("b" + state.items.length));   // the second reader starts no poll
      await tick();
      const afterStart = {{ loads, timers: [...timers.values()].map((t) => t[1]), loading: store.get().loading }};
      [...timers.values()][0][0]();                                                      // one tick: one load for both readers
      await tick();
      const afterTick = loads;
      hidden = true; change(); await tick();                                             // hidden: slower, and no refresh
      const hiddenState = {{ loads, timers: [...timers.values()].map((t) => t[1]) }};
      hidden = false; change(); await tick();                                            // visible again: refresh now, 5 s
      const visibleState = {{ loads, timers: [...timers.values()].map((t) => t[1]) }};
      first(); const stillOn = timers.size;
      second(); const stopped = {{ timers: timers.size, watching: change === null }};
      console.log(JSON.stringify({{ afterStart, afterTick, hiddenState, visibleState, stillOn, stopped, news, seen: seen.length > 0 }}));
    """)
    assert out["afterStart"] == {"loads": 1, "timers": [5000], "loading": False}
    assert out["afterTick"] == 2
    assert out["hiddenState"] == {"loads": 2, "timers": [30000]}
    assert out["visibleState"] == {"loads": 3, "timers": [5000]}
    assert out["stillOn"] == 1 and out["stopped"] == {"timers": 0, "watching": True}
    assert out["news"] == [[["i2"], ["i1"]], [["i3"], ["i2"]]] and out["seen"]


@needs_node
def test_a_failed_poll_keeps_the_last_items_and_reports_the_error():
    model = json.dumps((JS / "pending-model.js").as_uri())
    out = run_node(f"""
      import {{ createPendingStore }} from {model};
      let fail = false;
      const store = createPendingStore({{
        load: async () => {{ if (fail) throw new Error("down"); return {{ answer: {{ items: [{{ id: "x" }}] }}, background: {{ items: [] }}, policy: {{}} }}; }},
        setTimer: () => 1, clearTimer: () => {{}}, isHidden: () => false,
      }});
      const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
      const off = store.subscribe(() => {{}});
      await tick(); fail = true; await store.refresh();
      const state = store.get();
      off();
      console.log(JSON.stringify({{ items: state.items.length, error: state.error.message, loading: state.loading }}));
    """)
    assert out == {"items": 1, "error": "down", "loading": False}


def test_the_three_counted_extensions_declare_their_type_and_nothing_else_changed():
    expected = {"approvals": "approval", "continuity": "continuity", "task_waiting": "task"}
    for name, kind in expected.items():
        source = (STATIC_DIR / "extensions" / name / "index.js").read_text(encoding="utf-8")
        assert f'pendingType: "{kind}"' in source and 'group: "pending"' in source, name
    alerts = (STATIC_DIR / "extensions" / "document_alerts" / "index.js").read_text(encoding="utf-8")
    assert "pendingType" not in alerts                    # the API does not count document alerts


def test_the_screen_uses_tabs_with_label_and_help_and_the_row_is_a_link_to_the_same_poll():
    pending = ui_text((JS / "pending.js"))
    main = ui_text(JS / "main.js")
    assert re.search(r'<\$\{Tabs\} id="pending" label="Pendientes"', pending) and 'helpFor("pending.tabs")' in pending
    assert 'topic="pending.tabs"' in pending
    # the row and the «Todo» tab read the one store; neither polls by itself
    assert pending.count("createPendingStore(") == 1 and pending.count("setInterval(") == 1
    assert pending.count("usePendingInbox()") >= 3
    # the title of the screen is one <h2>, and the views of the group no longer draw their own help above the tabs
    assert 'inPending ? "Pendientes"' in main and "!creating && !tree && !inPending" in main
    assert "<${PendingScreen}" in main and "!inPending && section === view.id" in main
    # the help of the view goes above its panel, visible with help mode off
    assert "topic=${entry.topic} always=${true}" in pending


def test_the_pending_views_leave_more_for_the_tabs():
    sidebar = ui_text((JS / "sidebar.js"))
    model = ui_text((JS / "sidebar-model.js"))
    assert "navigation.pending.some(" in sidebar and 'section.group === "pending"' in model


@needs_node
def test_touched_scripts_pass_node_check():
    for name in ("pending.js", "pending-model.js", "route.js", "extensions.js", "main.js", "sidebar.js", "sidebar-model.js",
                 "help-text.js"):
        result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr)
    for name in ("approvals", "continuity", "task_waiting"):
        path = STATIC_DIR / "extensions" / name / "index.js"
        result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr)
