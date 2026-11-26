# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T13: the conversation list, mode defaults and simple run actions."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from session_fixtures import CLAUDE_ID, make_services, make_stores, write_claude
from tests.test_gui9_launch import DOM_PRELUDE

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
SPANISH_NAVIGATOR = [
    "--import",
    "data:text/javascript,Object.defineProperty(globalThis,'navigator',{value:{language:'es-MX'},configurable:true})",
]


def run_node(source: str) -> dict:
    result = subprocess.run(
        [NODE, *SPANISH_NAVIGATOR, "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=STATIC_DIR,
        timeout=30,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@needs_node
def test_initial_route_preserves_shared_addresses_and_groups_conversations_by_date():
    route = json.dumps((JS / "route.js").as_uri())
    model = json.dumps((JS / "command-palette-model.js").as_uri())
    result = run_node(f"""
      import {{ initialSection, parseRoute }} from {route};
      import {{ groupRecentEntries }} from {model};
      const now = new Date(2026, 10, 25, 12).getTime();
      const day = (date) => new Date(2026, 10, date, 9).toISOString();
      const grouped = groupRecentEntries([
        {{ id: "old", updatedAt: day(1), attention: false }},
        {{ id: "week", updatedAt: day(20), attention: false }},
        {{ id: "yesterday", updatedAt: day(24), attention: false }},
        {{ id: "today", updatedAt: day(25), attention: false }},
      ], now);
      console.log(JSON.stringify({{
        initial: [initialSection("simple"), initialSection("simple", true), initialSection("advanced")],
        routes: [parseRoute("#sessions"), parseRoute("#runs/run-1"), parseRoute("#new/run/run-1")],
        groups: grouped.map((item) => [item.id, item.subgroup]),
      }}));
    """)
    assert result["initial"] == ["new", "start", "sessions"]
    assert result["routes"] == [
        {"section": "sessions", "id": None},
        {"section": "runs", "id": "run-1"},
        {"section": "new", "id": "run-1"},
    ]
    assert result["groups"] == [
        ["today", "today"],
        ["yesterday", "yesterday"],
        ["week", "week"],
        ["old", "older"],
    ]


@needs_node
def test_sessions_component_renders_search_filters_mode_labels_and_folder_fallback():
    preact = json.dumps((STATIC_DIR / "vendor" / "htm-preact-standalone.module.js").as_uri())
    sessions_module = json.dumps((JS / "sessions.js").as_uri())
    i18n = json.dumps((JS / "i18n.js").as_uri())
    result = run_node(DOM_PRELUDE + f"""
      const {{ render, html }} = await import({preact});
      const {{ Sessions }} = await import({sessions_module});
      const {{ t }} = await import({i18n});
      document.addEventListener = () => {{}};
      document.removeEventListener = () => {{}};
      const calls = [];
      const now = new Date().toISOString();
      const base = {{ surface: "framework", status: "completed", models: [], tokens: null,
        child_count: 0, parent_id: null, created_at: now, updated_at: now }};
      const source = [
        {{ ...base, id: "framework:run-1", native_id: "run-1", source: "framework",
          title: "Only framework", cwd: "/workspace/run" }},
        {{ ...base, id: "framework:run-2", native_id: "run-2", source: "framework",
          title: "Other framework", cwd: null }},
      ];
      const reply = (payload) => ({{ ok: true, status: 200, json: async () => payload }});
      globalThis.fetch = async (path) => {{
        const url = new URL(String(path), "http://localhost");
        calls.push(url.href);
        const text = (url.searchParams.get("text") || "").toLowerCase();
        const sessions = source.filter((item) => !text || JSON.stringify(item).toLowerCase().includes(text));
        return reply({{ sessions, next_cursor: null, hint: null, warnings: [] }});
      }};
      const root = document.createElement("div");
      const draw = (mode) => render(html`<${{Sessions}} token="fake" route=${{ {{ id: null }} }}
        active=${{true}} mode=${{mode}} />`, root);
      draw("simple");
      await waitUntil(() => root.textContent.includes("Only framework"), 5000, "simple sessions");
      const input = allElements(root).find((node) => node.id === "sf-text");
      const filters = allElements(root).find((node) => node.localName === "details");
      const simpleTitle = allElements(root).find((node) => node.id === "sessions-title").textContent;
      const simpleDateHeading = allElements(root).find((node) => node.localName === "h4")?.textContent;
      const simpleLink = allElements(root).find((node) => node.getAttribute("data-sid") === "framework:run-1");
      const cwdInSimpleRow = simpleLink.textContent.includes("/workspace/run");
      const noFolderText = t("sessions.no_folder_run");
      const noFolderInRow = allElements(root).find((node) => node.getAttribute("data-sid") === "framework:run-2")
        .textContent.includes(noFolderText);
      draw("advanced");
      const advancedTitle = allElements(root).find((node) => node.id === "sessions-title").textContent;
      const advancedRow = allElements(root).find((node) => node.getAttribute("data-sid") === "framework:run-1");
      const cwdInAdvancedRow = advancedRow.textContent.includes("/workspace/run");
      const advancedNoFolder = allElements(root).find((node) => node.getAttribute("data-sid") === "framework:run-2")
        .textContent.includes(noFolderText);
      input.value = "Only framework";
      input.emit("input", {{ target: input, currentTarget: input }});
      await waitUntil(() => calls.some((value) => new URL(value).searchParams.get("text") === "Only framework"),
        5000, "debounced live search");
      await waitUntil(() => !root.textContent.includes("Other framework"), 5000, "filtered list");
      console.log(JSON.stringify({{
        oneSearch: allElements(root).filter((node) => node.id === "sf-text").length === 1,
        filtersCollapsed: Boolean(filters) && !filters.hasAttribute("open"),
        labelsChange: simpleTitle === t("sessions.all_conversations") && advancedTitle === t("sessions.all_sessions"),
        simpleDateHeading: simpleDateHeading === t("sessions.today"),
        simpleThreadLink: simpleLink.getAttribute("href") === "#new/run/run-1",
        cwdInSimpleRow, cwdInAdvancedRow, noFolderInRow, advancedNoFolder,
        liveSearch: !root.textContent.includes("Other framework"),
      }}));
      process.exit(0);
    """)
    assert result == {
        "oneSearch": True,
        "filtersCollapsed": True,
        "labelsChange": True,
        "simpleDateHeading": True,
        "simpleThreadLink": True,
        "cwdInSimpleRow": True,
        "cwdInAdvancedRow": True,
        "noFolderInRow": True,
        "advancedNoFolder": True,
        "liveSearch": True,
    }


@needs_node
def test_session_detail_hides_advanced_links_in_simple_mode():
    preact = json.dumps((STATIC_DIR / "vendor" / "htm-preact-standalone.module.js").as_uri())
    detail_module = json.dumps((JS / "session-detail.js").as_uri())
    gui_mode = json.dumps((JS / "gui-mode.js").as_uri())
    result = run_node(DOM_PRELUDE + f"""
      const {{ setCurrentMode }} = await import({gui_mode});
      setCurrentMode("simple");
      const {{ render, html }} = await import({preact});
      const {{ SessionDetail }} = await import({detail_module});
      document.addEventListener = () => {{}};
      document.removeEventListener = () => {{}};
      const summary = {{
        id: "framework:run-1", source: "framework", native_id: "run-1", title: "Run one",
        surface: "framework", status: "completed", cwd: null, parent_id: null, models: [],
        tokens: null, child_count: 0, created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        open_in_app: {{ kind: "url", value: "/gui/#run=run-1", needs_confirmation: false }},
      }};
      const reply = (payload, status = 200) => ({{ ok: status === 200, status, json: async () => payload }});
      globalThis.fetch = async (path) => {{
        const value = String(path);
        if (value.includes("/items?")) return reply({{ items: [], next_cursor: null, done: true, warnings: [] }});
        if (value.startsWith("/gui/api/sessions/")) return reply(summary);
        if (value.startsWith("/gui/api/stream")) return reply({{}}, 404);
        return reply({{}});
      }};
      const root = document.createElement("div");
      const draw = (mode) => render(html`<${{SessionDetail}} token="fake" sessionId="framework:run-1"
        onBack=${{() => {{}}}} mode=${{mode}} />`, root);
      draw("simple");
      await waitUntil(() => root.textContent.includes("Run one"), 5000, "simple session detail");
      const simpleLinks = allElements(root).filter((node) => node.localName === "a")
        .map((node) => node.getAttribute("href"));
      draw("advanced");
      await waitUntil(() => allElements(root).some((node) => node.localName === "a"
        && (node.getAttribute("href") || "").includes("#runs/run-1")), 5000, "advanced run link");
      const advancedLinks = allElements(root).filter((node) => node.localName === "a")
        .map((node) => node.getAttribute("href"));
      console.log(JSON.stringify({{
        simpleHidesRunAndTree: !simpleLinks.some((href) => href && (href.includes("#runs/") || href.includes("#tree/"))),
        advancedShowsRunAndTree: advancedLinks.some((href) => href && href.includes("#runs/run-1"))
          && advancedLinks.some((href) => href && href.includes("#tree/")),
      }}));
      process.exit(0);
    """)
    assert result == {"simpleHidesRunAndTree": True, "advancedShowsRunAndTree": True}


@needs_node
def test_manual_action_form_is_available_and_submits_in_simple_run_thread():
    preact = json.dumps((STATIC_DIR / "vendor" / "htm-preact-standalone.module.js").as_uri())
    thread_module = json.dumps((JS / "thread.js").as_uri())
    gui_mode = json.dumps((JS / "gui-mode.js").as_uri())
    result = run_node(DOM_PRELUDE + f"""
      const {{ setCurrentMode }} = await import({gui_mode});
      setCurrentMode("simple");
      const {{ render, html }} = await import({preact});
      const {{ RunThread }} = await import({thread_module});
      document.hidden = false;
      document.scrollingElement = {{ scrollTop: 0, clientHeight: 100, scrollHeight: 100 }};
      document.addEventListener = () => {{}};
      document.removeEventListener = () => {{}};
      const calls = [];
      const review = {{
        id: "run-1", status: "waiting_human", waiting: {{ reason: "execute_step", step_id: "answer", attempt: 1 }},
        pending_human: {{ step_id: "answer", attempt: 1 }}, terminal: false, cancel_requested: false,
        counters: {{}},
      }};
      const pending = {{
        step_id: "answer", attempt: 1, expected_contract: "plain text", prompt_id: "prompt-1",
        prompt_version: 1, prompt: "Please provide the answer.",
      }};
      const reply = (payload) => ({{ ok: true, status: 200, json: async () => payload }});
      globalThis.fetch = async (path, options = {{}}) => {{
        const value = String(path);
        const method = options.method || "GET";
        const body = options.body ? JSON.parse(options.body) : null;
        calls.push({{ path: value, method, body }});
        if (value.startsWith("/gui/api/threads/runs/")) return reply({{ status: "waiting_human", cursor: 1, entries: [] }});
        if (value === "/gui/api/runs/run-1") return reply(review);
        if (value.endsWith("/human-next")) return reply(pending);
        if (value.endsWith("/human-submit")) return reply(review);
        if (value === "/gui/api/inbox/policy") return reply({{ enabled: false }});
        return reply({{}});
      }};
      const root = document.createElement("div");
      render(html`<${{RunThread}} token="fake" runId="run-1" />`, root);
      await waitUntil(() => allElements(root).some((node) => node.id === "human-response"),
        5000, "manual response form");
      const response = allElements(root).find((node) => node.id === "human-response");
      response.value = "Manual answer";
      response.emit("input", {{ target: response, currentTarget: response }});
      await new Promise((resolve) => setTimeout(resolve, 10));
      let form = response.parentNode;
      while (form && form.localName !== "form") form = form.parentNode;
      if (!form) throw new Error("Manual response form was not rendered");
      form.emit("submit", {{ target: form, currentTarget: form }});
      await waitUntil(() => calls.some((call) => call.method === "POST"
        && call.path.endsWith("/human-submit")), 5000, "manual response submission");
      const submission = calls.find((call) => call.method === "POST" && call.path.endsWith("/human-submit"));
      console.log(JSON.stringify({{ formVisible: true, responseSent: submission.body.raw_response === "Manual answer" }}));
      process.exit(0);
    """)
    assert result == {"formVisible": True, "responseSent": True}


def test_gui_sessions_http_endpoint_accepts_text_query(tmp_path):
    stores = make_stores(tmp_path)
    services = make_services(stores)
    write_claude(stores)
    token = ensure_token(services.settings.gui_token_file)
    try:
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            response = client.get(
                "/gui/api/sessions", params={"text": "CLAUDE work"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200, response.text
        assert [summary["id"] for summary in response.json()["sessions"]] == [f"claude:{CLAUDE_ID}"]
    finally:
        services.runs.close()
