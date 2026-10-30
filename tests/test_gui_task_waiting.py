# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CNT-T16: read-only task waits through FLU-T04, using synthetic tasks only."""
import json
import re
import subprocess

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui import STATIC_DIR
from multiagent.gui.security import CSP
from test_gui_extensions import check_extension_code, check_extension_styles
from test_gui_help import NODE, check_extensions
from test_task_waiting import services  # shared synthetic fixture, no application accounts or CLIs

JS = STATIC_DIR / "js" / "task-waiting.js"
EXT = STATIC_DIR / "extensions" / "task_waiting"


def node(source):
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
                            encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_extension_is_discovered_served_and_reading_never_resolves_waits(services):
    tasks = services.tasks
    a = tasks.set_wait("A", who="user", question="Synthetic decision?", reference="synthetic:question",
                       actor="agent:a", expected_revision=tasks.get_task("A")["revision"])
    b = tasks.set_wait("B", who="agent", target="agent:b", question="Synthetic review?",
                       actor="agent:a", expected_revision=tasks.get_task("B")["revision"])
    before_events = tasks.events(task_id="A")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        # The existing GUI manifest is protected; static assets and CNT-T15's local API have their existing policy.
        assert client.get("/gui/api/extensions").status_code == 401
        for asset in ("extensions/task_waiting/index.js", "extensions/task_waiting/help.js",
                      "extensions/task_waiting/style.css", "js/task-waiting.js"):
            response = client.get("/gui/static/" + asset)
            assert response.status_code == 200
            assert response.headers["content-security-policy"] == CSP
        response = client.get("/api/v1/tasks?waiting_for=all")
        assert response.status_code == 200
        assert {t["id"] for t in response.json()} == {"A", "B"}
        assert client.get("/api/v1/tasks?waiting_for=user").json() == [a]
    assert tasks.get_task("A") == a and tasks.get_task("B") == b
    assert tasks.events(task_id="A") == before_events


def test_extension_has_help_scoped_theme_styles_and_no_mutations():
    check_extensions(STATIC_DIR)
    check_extension_code(STATIC_DIR)
    check_extension_styles(STATIC_DIR)
    entry = (EXT / "index.js").read_text(encoding="utf-8")
    assert re.search(r'registerView\(\{ id: "task_waiting"', entry)
    assert 'registerSlot("task_waiting"' in entry
    source = entry + JS.read_text(encoding="utf-8")
    for forbidden in ("apiPost", "apiPut", "apiDelete", "/waiting/decision", "/waiting/clear", "innerHTML", "href=", "onSubmit"):
        assert forbidden not in source
    assert "aria-live=\"polite\"" in entry and "role=\"alert\"" in entry
    assert "task.label?.text || task.state" in entry
    assert "white-space: pre-wrap" in (EXT / "style.css").read_text(encoding="utf-8")


@pytest.mark.skipif(not NODE, reason="Node is required")
def test_models_distinguish_user_decisions_from_all_other_waits_and_preserve_lifecycle(services):
    task = services.tasks.set_wait("A", who="user", question="Synthetic?", actor="agent:a",
                                   expected_revision=services.tasks.get_task("A")["revision"])
    assert {"key", "text", "tone"} <= task["label"].keys()
    out = node(f"""
      import {{ waitingTasks, waitingForUser, waitBadge, waitRecipient, readWaiting, WAITING_PATH }} from {json.dumps(JS.as_uri())};
      const kinds = ["user", "agent", "task", "quota", "window"];
      const input = kinds.map((who) => ({{ id: who, state: "running", label: {json.dumps(task['label'], ensure_ascii=False)},
        waiting: {{ who, target: who === "agent" ? "agent:synthetic" : null, question: "Synthetic?" }} }}));
      const original = JSON.stringify(input);
      const tasks = waitingTasks([...input, null, {{ id: "none" }}, {{ id: "bad", waiting: {{who: "unknown"}} }}]);
      const calls = [];
      const read = await readWaiting("synthetic-token", async (...args) => {{ calls.push(args); return input; }});
      const invalid = [];
      for (const value of [null, {{}}, {{tasks: []}}, "invalid"]) {{
        try {{ waitingTasks(value); invalid.push(false); }} catch (_) {{ invalid.push(true); }}
      }}
      console.log(JSON.stringify({{ ids: tasks.map(t => t.id), user: waitingForUser(tasks).map(t => t.id),
        badges: tasks.map(t => waitBadge(t.waiting)), recipient: waitRecipient(tasks[1].waiting),
        same: original === JSON.stringify(input), calls, path: WAITING_PATH, read: read.length, invalid }}));
    """)
    assert out["ids"] == ["user", "agent", "task", "quota", "window"]
    assert out["user"] == ["user"]
    assert out["badges"] == ["Espera tu decisión", "En espera · Agente", "En espera · Tarea",
                             "En espera · Cuota", "En espera · Ventana de integración"]
    assert out["recipient"] == "Agente: agent:synthetic"
    assert out["same"] is True and out["read"] == 5 and all(out["invalid"])
    assert out["calls"] == [["/api/v1/tasks?waiting_for=all", "synthetic-token"]]


@pytest.mark.skipif(not NODE, reason="Node is required")
def test_task_card_keeps_untrusted_text_as_text_and_displays_wait_metadata(services):
    task = services.tasks.set_wait("A", who="quota", question="<script>synthetic</script>",
                                   reference="javascript:synthetic", reset_at="2026-01-02T00:00:00Z", actor="agent:a",
                                   expected_revision=services.tasks.get_task("A")["revision"])
    out = node(f"""
      import {{ WaitingContent }} from {json.dumps((EXT / 'index.js').as_uri())};
      const task = {json.dumps(task, ensure_ascii=False)};
      task.waiting.reset_at = "2026-01-02T00:00:00Z";
      const tree = WaitingContent({{data: {{tasks: [task], loading: false, error: null, reload: () => {{}}}},
        scope: "view", others: true}});
      const tags = [], texts = [];
      function walk(n) {{
        if (typeof n === "string") texts.push(n);
        else if (Array.isArray(n)) n.forEach(walk);
        else if (n && n.props) {{
          if (typeof n.type === "function") {{
            if (n.type.name !== "Help") walk(n.type(n.props));
          }} else {{ tags.push(n.type); walk(n.props.children); }}
        }}
        else if (n && typeof n === "object") throw new TypeError("Object child cannot render");
      }}
      walk(tree);
      console.log(JSON.stringify({{ tags, texts }}));
    """)
    assert "script" not in out["tags"] and "a" not in out["tags"]
    assert "<script>synthetic</script>" in out["texts"]
    assert task["label"]["text"] in out["texts"]
    assert "javascript:synthetic" in out["texts"]
    assert all("T" not in text for text in out["texts"] if "2026" in text)


@pytest.mark.skipif(not NODE, reason="Node is required")
def test_polling_preserves_stale_data_ignores_old_requests_and_stops_after_unmount(services):
    # Hook harness exercises request ordering and cleanup without a browser, using the production hook's source.
    first_task = services.tasks.set_wait("A", who="user", question="Synthetic?", actor="agent:a",
                                         expected_revision=services.tasks.get_task("A")["revision"])
    second_task = services.tasks.set_wait("B", who="user", question="Synthetic next?", actor="agent:a",
                                          expected_revision=services.tasks.get_task("B")["revision"])
    out = node(rf"""
      import {{ readFileSync }} from "node:fs";
      let state, cleanup, tick, visible = "visible", request = [];
      globalThis.document = {{ get visibilityState() {{return visible;}}, querySelector: () => ({{dataset: {{panel: "open"}}}}) }};
      globalThis.setInterval = (fn) => {{ tick = fn; return 1; }};
      let cleared = false;
      globalThis.clearInterval = () => {{ cleared = true; }};
      globalThis.hooks = {{
        useState: (initial) => {{ state = initial; return [state, next => {{ state = typeof next === "function" ? next(state) : next; }}]; }},
        useRef: () => ({{current: null}}), useEffect: (fn) => {{ cleanup = fn(); }},
        apiGet: () => new Promise((resolve, reject) => request.push({{resolve, reject}}))
      }};
      const source = readFileSync({json.dumps(str(JS))}, "utf8")
        .replace(/^import .*;$/gm, "") + "\n";
      const mod = await import("data:text/javascript;base64," + Buffer.from(
        "const {{useState,useRef,useEffect,apiGet}} = globalThis.hooks;\n" + source).toString("base64"));
      const ui = mod.useTaskWaiting("synthetic");
      request[0].resolve([{json.dumps(first_task, ensure_ascii=False)}]); await new Promise(resolve => setImmediate(resolve));
      const first = state.tasks.length;
      const old = ui.reload(); const newer = ui.reload();
      request[2].resolve([{json.dumps(second_task, ensure_ascii=False)}]); await newer;
      request[1].resolve([]); await old;
      const latest = state.tasks[0].id;
      const failed = ui.reload(); request[3].reject(new Error("synthetic offline")); await failed;
      const stale = {{ id: state.tasks[0].id, error: state.error.message, loading: state.loading }};
      visible = "hidden"; tick(); const hiddenCalls = request.length;
      const late = ui.reload(); cleanup(); request[4].resolve([]); await late;
      const retained = state.tasks[0].id;
      console.log(JSON.stringify({{first, latest, stale, hiddenCalls, retained, cleared}}));
    """)
    assert out == {"first": 1, "latest": "B", "stale": {"id": "B", "error": "synthetic offline", "loading": False},
                   "hiddenCalls": 4, "retained": "B", "cleared": True}


@pytest.mark.skipif(not NODE, reason="Node is required")
def test_view_and_panel_share_one_poll_and_folded_panel_does_not_fetch():
    out = node(rf"""
      import {{ readFileSync }} from "node:fs";
      let app = {{dataset: {{panel: "closed"}}}}, requests = [], tick, observer;
      globalThis.document = {{visibilityState: "visible", querySelector: () => app}};
      globalThis.MutationObserver = class {{constructor(fn) {{observer = fn;}} observe() {{}} disconnect() {{}}}};
      globalThis.setInterval = fn => (tick = fn, 1);
      globalThis.clearInterval = () => {{tick = null;}};
      globalThis.hooks = {{
        useState: initial => [initial, () => {{}}], useRef: () => ({{current: null}}),
        useEffect: fn => {{ (globalThis.cleanups ||= []).push(fn()); }},
        apiGet: async () => {{requests.push(1); return [];}}
      }};
      const source = readFileSync({json.dumps(str(JS))}, "utf8").replace(/^import .*;$/gm, "") + "\n";
      const mod = await import("data:text/javascript;base64," + Buffer.from(
        "const {{useState,useRef,useEffect,apiGet}} = globalThis.hooks;\n" + source).toString("base64"));
      mod.useTaskWaiting("synthetic", "panel");
      const folded = requests.length;
      app.dataset.panel = "open"; observer(); await new Promise(resolve => setImmediate(resolve));
      mod.useTaskWaiting("synthetic", "view");
      const shared = requests.length;
      tick(); await new Promise(resolve => setImmediate(resolve));
      const polled = requests.length;
      globalThis.cleanups[1]();
      app.dataset.panel = "closed"; tick(); await new Promise(resolve => setImmediate(resolve));
      const closed = requests.length;
      globalThis.cleanups[0]();
      console.log(JSON.stringify({{folded, shared, polled, closed, timerStopped: tick === null}}));
    """)
    assert out == {"folded": 0, "shared": 1, "polled": 2, "closed": 2, "timerStopped": True}

