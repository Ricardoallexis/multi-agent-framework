# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EJC-T02: new-session confirmation, project input, office options and local-only defaults."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from tests.gui_text import ui_text

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
SPANISH_NAVIGATOR = [
    "--import",
    "data:text/javascript,Object.defineProperty(globalThis,'navigator',{value:{language:'es-MX'},configurable:true})",
]
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str) -> dict:
    result = subprocess.run(
        [NODE, *SPANISH_NAVIGATOR, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


DOM_PRELUDE = r"""
class FakeNode {
  constructor(name, nodeType = 1, ownerDocument = null) {
    this.nodeType = nodeType;
    this.localName = name;
    this.nodeName = nodeType === 3 ? "#text" : String(name).toUpperCase();
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.childNodes = [];
    this._attributes = new Map();
    this._events = new Map();
    this.style = { cssText: "" };
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.type = "";
    this.id = "";
    this.data = "";
    this.onclick = this.oninput = this.onsubmit = this.onkeydown = this.onchange = this.onblur = this.onfocus = null;
  }
  get firstChild() { return this.childNodes[0] || null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    const siblings = this.parentNode.childNodes;
    return siblings[siblings.indexOf(this) + 1] || null;
  }
  get attributes() { return [...this._attributes].map(([name, value]) => ({ name, value })); }
  get textContent() { return this.nodeType === 3 ? this.data : this.childNodes.map((child) => child.textContent).join(""); }
  set textContent(value) {
    if (this.nodeType === 3) { this.data = String(value); return; }
    this.childNodes.splice(0).forEach((child) => { child.parentNode = null; });
    if (String(value)) this.appendChild(this.ownerDocument.createTextNode(String(value)));
  }
  get innerHTML() { return this.textContent; }
  set innerHTML(value) { this.textContent = value; }
  setAttribute(name, value) { this._attributes.set(name, String(value)); }
  getAttribute(name) { return this._attributes.has(name) ? this._attributes.get(name) : null; }
  hasAttribute(name) { return this._attributes.has(name); }
  removeAttribute(name) { this._attributes.delete(name); }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    this.childNodes.push(child); child.parentNode = this; return child;
  }
  insertBefore(child, reference) {
    if (!reference) return this.appendChild(child);
    if (child.parentNode) child.parentNode.removeChild(child);
    const index = this.childNodes.indexOf(reference);
    if (index < 0) return this.appendChild(child);
    this.childNodes.splice(index, 0, child); child.parentNode = this; return child;
  }
  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) this.childNodes.splice(index, 1);
    child.parentNode = null; return child;
  }
  addEventListener(type, callback, capture = false) { this._events.set(type + Boolean(capture), callback); }
  removeEventListener(type, _callback, capture = false) { this._events.delete(type + Boolean(capture)); }
  dispatchEvent(event) {
    event.target ||= this;
    let node = this;
    do {
      event.currentTarget = node;
      node._events.get(event.type + "false")?.call(node, event);
      node = event.bubbles ? node.parentNode : null;
    } while (node);
    return !event.defaultPrevented;
  }
  emit(type, extra = {}) {
    const event = { type, bubbles: false, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
    this.dispatchEvent(event); return event;
  }
  focus() {}
  scrollIntoView() {}
}
class FakeDocument {
  constructor() {
    this.documentElement = { lang: "" };
    this.body = this.createElement("body");
  }
  createElement(name) { return new FakeNode(name, 1, this); }
  createElementNS(_namespace, name) { return this.createElement(name); }
  createTextNode(value) { const node = new FakeNode("#text", 3, this); node.data = String(value); return node; }
}
const windowListeners = new Map();
const location = {
  pathname: "/", search: "", _hash: "#new/office/demo/orders",
  get hash() { return this._hash; },
  set hash(value) {
    this._hash = value;
    const event = { type: "hashchange", target: this, currentTarget: null };
    for (const listener of windowListeners.get("hashchange") || []) listener(event);
  },
};
const storage = { getItem() { return null; }, setItem() {}, removeItem() {} };
globalThis.document = new FakeDocument();
globalThis.location = location;
globalThis.window = {
  location, localStorage: storage, sessionStorage: storage,
  addEventListener(type, callback) {
    if (!windowListeners.has(type)) windowListeners.set(type, new Set());
    windowListeners.get(type).add(callback);
  },
  removeEventListener(type, callback) { windowListeners.get(type)?.delete(callback); },
  dispatchEvent(event) { for (const listener of windowListeners.get(event.type) || []) listener(event); },
  matchMedia() { return { matches: true, addEventListener() {}, removeEventListener() {} }; },
};
globalThis.history = { replaceState() {} };
const allElements = (root) => {
  const found = [];
  const visit = (node) => { for (const child of node.childNodes || []) { found.push(child); visit(child); } };
  visit(root); return found;
};
const hasClass = (node, name) => (node.getAttribute?.("class") || "").split(/\s+/).includes(name);
const waitUntil = async (predicate, timeout = 5000, label = "UI state") => {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (predicate()) return; await new Promise((resolve) => setTimeout(resolve, 10)); }
  throw new Error("Timed out waiting for " + label);
};
"""


@needs_node
def test_project_question_and_existing_project_build_the_same_run_body():
    model = json.dumps((JS / "new-session-model.js").as_uri())
    out = run_node(f"""
      const m = await import({model});
      const workflow = {{ id: "review", bundle: null }};
      const spec = {{ inputs: [{{ name: "request", label: "Request", required: true, type: "text" }}] }};
      const start = (project) => m.reduce(m.createConversation(), {{
        type: "pick", workflow, spec, project,
      }});
      let existing = start("Existing project");
      const firstProgress = m.progress(existing);
      existing = m.reduce(existing, {{ type: "send", text: "Review this" }});
      const projectQuestion = m.currentQuestion(existing);
      existing = m.reduce(existing, {{ type: "send", text: "Existing project" }});
      let fresh = start("");
      fresh = m.reduce(fresh, {{ type: "send", text: "Review this" }});
      fresh = m.reduce(fresh, {{ type: "send", text: "Existing project" }});
      console.log(JSON.stringify({{
        firstProgress, projectSource: projectQuestion.source,
        existingBody: m.runBody(existing), freshBody: m.runBody(fresh),
        summary: m.answerRows(existing), readyMessage: existing.messages.at(-1).text,
      }}));
    """)
    assert out["firstProgress"] == {"n": 1, "total": 2}
    assert out["projectSource"] == "project"
    assert out["existingBody"] == out["freshBody"]
    assert out["existingBody"]["project_name"] == "Existing project"
    assert out["readyMessage"] == "Listo para iniciar: review."
    assert all(row["value"] for row in out["summary"])


@needs_node
def test_effective_privacy_and_local_model_require_both_permissions():
    model = json.dumps((JS / "new-session-model.js").as_uri())
    out = run_node(f"""
      const m = await import({model});
      const offices = [{{ id: "demo" }}, {{ id: "demo__long" }}];
      const privacy = {{ areas: [
        {{ id: "normal", data_class: "non_sensitive", cloud_effective: true }},
        {{ id: "denied", data_class: "non_sensitive", cloud_effective: false }},
        {{ id: "sensitive", data_class: "sensitive", cloud_effective: true }},
      ] }};
      const connections = {{ connections: [{{ id: "ollama", enabled: true, available: true }}] }};
      const routing = {{
        catalog_models: [{{ id: "local-1", provider: "ollama" }}],
        steps: [{{ model_options: ["local-1"] }}],
      }};
      console.log(JSON.stringify({{
        bundleArea: m.officeAreaForBundle("demo__long__area", offices),
        normal: m.officeAreaAllowsCloud(privacy, "normal"),
        deniedRequiresLocal: m.officeAreaRequiresLocal(privacy, "denied"),
        sensitiveRequiresLocal: m.officeAreaRequiresLocal(privacy, "sensitive"),
        localAvailable: m.hasAvailableLocalModel(connections, routing),
        unavailableWithoutConnection: m.hasAvailableLocalModel({{ connections: [] }}, routing),
      }}));
    """)
    assert out == {
        "bundleArea": {"officeId": "demo__long", "areaId": "area"},
        "normal": True,
        "deniedRequiresLocal": True,
        "sensitiveRequiresLocal": True,
        "localAvailable": True,
        "unavailableWithoutConnection": False,
    }


@needs_node
def test_office_workflow_confirmation_uses_its_privacy_and_waits_for_launch():
    preact = json.dumps((STATIC_DIR / "vendor" / "htm-preact-standalone.module.js").as_uri())
    new_session = json.dumps((JS / "new-session.js").as_uri())
    script = DOM_PRELUDE + r"""
      const { render, html } = await import(__PREACT__);
      const { NewSession } = await import(__NEW_SESSION__);
      const calls = [];
      let releasePreview;
      let releaseOfficeList;
      let rejectConnections;
      const routing = {
        catalog_models: [
          { id: "local-model", provider: "ollama" },
          { id: "cloud-model", provider: "openai" },
        ],
        steps: [{
          step_id: "answer", status: "selected", profile_id: "default", model: "cloud-model",
          model_options: ["local-model", "cloud-model"], mode_options: ["supervised", "automatic"],
          profile_options: ["default"], level_options: ["standard"], effort_options: [],
        }],
      };
      const previewResult = { routing, steps: [], missing_inputs: [], empty_optional_inputs: [] };
      const reply = (payload) => ({ ok: true, status: 200, json: async () => payload });
      globalThis.fetch = async (path, options = {}) => {
        const method = options.method || "GET";
        const body = options.body ? JSON.parse(options.body) : null;
        calls.push({ method, path: String(path), body });
        if (method === "POST" && path === "/gui/api/runs/preview") {
          return new Promise((resolve) => { releasePreview = () => resolve(reply(previewResult)); });
        }
        if (method === "POST" && path === "/gui/api/runs") return reply({ id: "run-created" });
        if (path === "/gui/api/offices") return new Promise((resolve) => {
          releaseOfficeList = () => resolve(reply({ offices: [{ id: "demo", name: "Demo Office" }] }));
        });
        if (path === "/gui/api/bundles") return reply([
          { name: "demo__orders" }, { name: "demo__inventory" },
        ]);
        if (path === "/gui/api/offices/demo") return reply({
          id: "demo", areas: [{ id: "orders", name: "Orders" }, { id: "inventory", name: "Inventory" }],
          file_tools_configured: true,
        });
        if (path === "/gui/api/offices/demo/scene") return reply({
          office: { id: "demo" }, areas: [{ data_class: "restricted" }, { data_class: "non_sensitive" }],
        });
        if (path === "/gui/api/offices/demo/privacy") return reply({ areas: [
          { id: "orders", data_class: "non_sensitive", cloud_effective: true },
          { id: "inventory", data_class: "restricted", cloud_effective: true },
        ] });
        if (path === "/gui/api/connections") return new Promise((_resolve, reject) => {
          rejectConnections = () => reject(new Error("connections unavailable"));
        });
        if (path === "/gui/api/runs?limit=50") return reply([]);
        if (path.startsWith("/gui/api/workflows?bundle=")) {
          const bundle = new URLSearchParams(path.split("?")[1]).get("bundle");
          const area = bundle.split("__")[1];
          return reply([{ id: "review_" + area, bundle, label: "Review " + area }]);
        }
        if (path.includes("/gui/api/workflows/review_inventory/inputs")) {
          return reply({ description: "Inventory review", inputs: [
            { name: "task", label: "Task", required: true, type: "text" },
          ], steps: [] });
        }
        if (path.includes("/gui/api/workflows/review_orders/inputs")) {
          return reply({ description: "Orders review", inputs: [
            { name: "task", label: "Task", required: true, type: "text" },
          ], steps: [] });
        }
        throw new Error("Unexpected fake API request: " + method + " " + path);
      };
      location._hash = "#new";
      const root = document.createElement("div");
      render(html`<${NewSession} token="fake-token" mock=${false} />`, root);
      await waitUntil(() => Boolean(rejectConnections), 5000, "connection request");
      const button = (needle) => allElements(root).find((node) =>
        node.localName === "button" && node.textContent.toLowerCase().includes(needle.toLowerCase()));
      const submitComposer = async (value) => {
        const textarea = allElements(root).find((node) => node.id === "ns-composer-input");
        if (!textarea) throw new Error("Expected an active composer");
        textarea.value = value;
        textarea.emit("input", { target: textarea, currentTarget: textarea });
        await new Promise((resolve) => setTimeout(resolve, 10));
        const form = allElements(root).find((node) => hasClass(node, "ns-composer"));
        form.emit("submit", { target: form, currentTarget: form });
      };
      await waitUntil(() => Boolean(allElements(root).find((node) => node.id === "ns-composer-input")), 5000, "first workflow question");
      await waitUntil(() => root.textContent.includes("2 workflows disponibles."), 5000, "office catalog completion");
      button("Trabajar en un workflow").emit("click", { target: button("Trabajar en un workflow") });
      await waitUntil(() => Boolean(button("review_inventory")), 5000, "inventory workflow card");
      button("review_inventory").emit("click", { target: button("review_inventory") });
      await waitUntil(() => root.textContent.includes("Pregunta 1 de 2: Task"), 5000, "first workflow question");
      await submitComposer("Check stock");
      await waitUntil(() => root.textContent.includes("Pregunta 2 de 2: Proyecto"), 5000, "project question");
      await submitComposer("Stock audit");
      await waitUntil(() => Boolean(releasePreview), 5000, "preview request");
      const localOnlyCheckbox = allElements(root).filter((node) => node.localName === "input" && node.type === "checkbox")[0];
      const modeSelectWhileOfficeListLoads = allElements(root).find((node) => node.id === "ns-mode");
      const cloudModeHiddenWhileOfficeListLoads = !allElements(modeSelectWhileOfficeListLoads)
        .some((node) => node.localName === "option" && node.value === "cloud");
      const localOnlyLockedWhileOfficeListLoads = localOnlyCheckbox.checked && localOnlyCheckbox.disabled;
      releaseOfficeList();
      await waitUntil(() => root.textContent.includes("Esta área guarda sus datos solo en este equipo."), 5000, "bundle privacy load");
      // GUI4-T04 keeps the composer mounted but hidden while confirming, so the picker keeps its state
      const hasComposerWhileConfirming = allElements(root).some((node) => hasClass(node, "ns-composer") && !node.hidden);
      const inputs = allElements(root).filter((node) => node.localName === "input" && node.type === "checkbox");
      const localOnly = inputs[0];
      const modeSelect = allElements(root).find((node) => node.id === "ns-mode");
      const modeOptions = allElements(modeSelect).filter((node) => node.localName === "option").map((node) => node.value);
      const pendingText = root.textContent;
      const previewCall = calls.find((call) => call.method === "POST" && call.path === "/gui/api/runs/preview");
      const createdBeforeConfirm = calls.some((call) => call.method === "POST" && call.path === "/gui/api/runs");
      releasePreview();
      await waitUntil(() => allElements(root).some((node) => node.getAttribute("aria-busy") === "false"), 5000, "preview completion");
      const noLocalModelNoticeWhileConnectionsLoad = !root.textContent.includes("No hay un modelo local disponible.");
      rejectConnections();
      await new Promise((resolve) => setTimeout(resolve, 10));
      const noLocalModelNoticeAfterConnectionsFail = !root.textContent.includes("No hay un modelo local disponible.");
      const launchReadyVisibleAfterPreview = root.textContent.includes("Todo listo: nada se ejecuta hasta que pulses «Lanzar».");
      const confirmReal = allElements(root).filter((node) => node.localName === "input" && node.type === "checkbox")[1];
      confirmReal.checked = true;
      confirmReal.emit("change", { target: confirmReal, currentTarget: confirmReal });
      await waitUntil(() => allElements(root).some((node) =>
        node.localName === "button" && node.textContent.trim() === "Lanzar" && !node.disabled), 5000, "enabled launch action");
      // Keep this test scoped to launch; the run-thread view starts its own polling lifecycle.
      windowListeners.get("hashchange")?.clear();
      const launch = button("Lanzar");
      launch.emit("click", { target: launch });
      await waitUntil(() => calls.some((call) => call.method === "POST" && call.path === "/gui/api/runs"), 5000, "run creation");
      const createCall = calls.find((call) => call.method === "POST" && call.path === "/gui/api/runs");
      console.log(JSON.stringify({
        composerHidden: !hasComposerWhileConfirming,
        localOnlyCheckedAndLocked: localOnly.checked && localOnly.disabled,
        cloudModeHidden: !modeOptions.includes("cloud"),
        localOnlyReasonVisible: pendingText.includes("Esta área guarda sus datos solo en este equipo."),
        warningVisibleWhileBlocked: pendingText.includes("Todo listo: nada se ejecuta hasta que pulses «Lanzar»."),
        warningVisibleAfterPreview: launchReadyVisibleAfterPreview,
        officePrivacyRequested: calls.some((call) => call.path === "/gui/api/offices/demo/privacy"),
        localOnlyLockedWhileOfficeListLoads, cloudModeHiddenWhileOfficeListLoads,
        noLocalModelNoticeWhileConnectionsLoad, noLocalModelNoticeAfterConnectionsFail,
        createdBeforeConfirm, previewBody: previewCall.body, createBody: createCall.body,
        originText: pendingText.includes("Origen del workflow") && pendingText.includes("Bundle «demo__inventory»"),
      }));
    """.replace("__PREACT__", preact).replace("__NEW_SESSION__", new_session)
    out = run_node(script)
    assert out["composerHidden"] is True
    assert out["localOnlyCheckedAndLocked"] is True
    assert out["cloudModeHidden"] is True
    assert out["localOnlyReasonVisible"] is True
    assert out["warningVisibleWhileBlocked"] is True
    assert out["warningVisibleAfterPreview"] is True
    assert out["officePrivacyRequested"] is True
    assert out["localOnlyLockedWhileOfficeListLoads"] is True
    assert out["cloudModeHiddenWhileOfficeListLoads"] is True
    assert out["noLocalModelNoticeWhileConnectionsLoad"] is True
    assert out["noLocalModelNoticeAfterConnectionsFail"] is True
    assert out["createdBeforeConfirm"] is False
    assert out["originText"] is True
    preview = out["previewBody"]
    create = out["createBody"]
    assert preview["bundle"] == create["bundle"] == "demo__inventory"
    assert preview["workflow_id"] == create["workflow_id"] == "review_inventory"
    assert preview["inputs"] == create["inputs"] == {"task": "Check stock"}
    assert preview["execution_mode"] == create["execution_mode"] == "auto"
    assert preview["sensitive"] == create["sensitive"] is True
    assert preview["routing_preferences"] == create["routing_preferences"] == {}
    assert create["project_name"] == "Stock audit"


@needs_node
def test_new_session_omits_offices_context_while_office_list_is_loading():
    preact = json.dumps((STATIC_DIR / "vendor" / "htm-preact-standalone.module.js").as_uri())
    new_session = json.dumps((JS / "new-session.js").as_uri())
    gui_mode = json.dumps((JS / "gui-mode.js").as_uri())
    script = DOM_PRELUDE + r"""
      const { setCurrentMode } = await import(__GUI_MODE__);
      setCurrentMode("advanced");
      const { render, html } = await import(__PREACT__);
      const { NewSession } = await import(__NEW_SESSION__);
      const officeRequests = [];
      let releaseNewSessionOffices;
      const reply = (payload) => ({ ok: true, status: 200, json: async () => payload });
      globalThis.fetch = async (path) => {
        if (path === "/gui/api/offices") {
          officeRequests.push(path);
          if (officeRequests.length === 1) return new Promise((resolve) => { releaseNewSessionOffices = resolve; });
          return reply({ offices: [{ id: "demo" }] });
        }
        if (path === "/gui/api/bundles") return reply([{ name: "general" }]);
        if (path.startsWith("/gui/api/workflows")) return reply([]);
        if (path === "/gui/api/connections") return reply({ connections: [] });
        if (path === "/gui/api/runs?limit=50") return reply([]);
        throw new Error("Unexpected fake API request: " + path);
      };
      location._hash = "#new";
      const root = document.createElement("div");
      render(html`<${NewSession} token="fake" mock=${true} />`, root);
      await waitUntil(() => Boolean(releaseNewSessionOffices), 5000, "NewSession office-list request");
      const plus = allElements(root).find((node) => hasClass(node, "ns-plus"));
      plus.emit("click", { target: plus });
      await waitUntil(() => allElements(root).some((node) =>
        node.localName === "button" && node.textContent.includes("Ver como formulario")), 5000, "form action in menu");
      const formAction = allElements(root).find((node) =>
        node.localName === "button" && node.textContent.includes("Ver como formulario"));
      formAction.emit("click", { target: formAction });
      await waitUntil(() => officeRequests.length === 2, 5000, "CreateRun office-list request");
      const formVisible = root.textContent.includes("Nueva ejecución");
      releaseNewSessionOffices(reply({ offices: [{ id: "demo" }] }));
      await new Promise((resolve) => setTimeout(resolve, 10));
      console.log(JSON.stringify({ officeRequestCount: officeRequests.length, formVisible }));
    """.replace("__PREACT__", preact).replace("__NEW_SESSION__", new_session).replace("__GUI_MODE__", gui_mode)
    out = run_node(script)
    assert out == {"officeRequestCount": 2, "formVisible": True}


@needs_node
def test_create_run_only_fetches_offices_when_initial_context_is_missing():
    preact = json.dumps((STATIC_DIR / "vendor" / "htm-preact-standalone.module.js").as_uri())
    create_run = json.dumps((JS / "create-run.js").as_uri())
    script = DOM_PRELUDE + r"""
      const { render, html } = await import(__PREACT__);
      const { CreateRun } = await import(__CREATE_RUN__);
      const requests = [];
      const officeRequests = [];
      globalThis.fetch = async (path, options = {}) => {
        requests.push(path);
        if (path === "/gui/api/offices") officeRequests.push(options.method || "GET");
        return { ok: true, status: 200, json: async () => {
          if (path === "/gui/api/offices") return { offices: [{ id: "demo" }] };
          if (path === "/gui/api/bundles") return [];
          if (path === "/gui/api/workflows") return [];
          return {};
        } };
      };
      const root = document.createElement("div");
      render(html`<${CreateRun} token="fake" mock=${true}
        initial=${{ offices: [{ id: "demo" }], officeId: "demo", privacy: { areas: [] }, connections: { connections: [] } }} />`, root);
      await waitUntil(() => requests.includes("/gui/api/bundles") && requests.includes("/gui/api/workflows"));
      const withContext = officeRequests.length;
      render(null, root);
      render(html`<${CreateRun} token="fake" mock=${true}
        initial=${{ officeId: "demo", privacy: { areas: [] }, connections: { connections: [] } }} />`, root);
      await waitUntil(() => officeRequests.length === 1);
      console.log(JSON.stringify({ withContext, withoutContext: officeRequests.length }));
    """.replace("__PREACT__", preact).replace("__CREATE_RUN__", create_run)
    out = run_node(script)
    assert out == {"withContext": 0, "withoutContext": 1}


def test_run_api_still_forces_sensitive_for_sensitive_office_areas(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
        gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(settings, bundles={"team": BUNDLE}, dry_run=True)
    original_area_class = services.offices.area_class
    original_office_of = services.offices.office_of
    monkeypatch.setattr(services.offices, "area_class", lambda bundle: "sensitive" if bundle == "team" else original_area_class(bundle))
    monkeypatch.setattr(services.offices, "office_of", lambda bundle: ("demo", "area") if bundle == "team" else original_office_of(bundle))
    observed_preview_sensitivity = []
    original_preview = services.definitions.preview_prompts

    def capture_preview(*args, **kwargs):
        observed_preview_sensitivity.append(kwargs.get("sensitive"))
        return original_preview(*args, **kwargs)

    monkeypatch.setattr(services.definitions, "preview_prompts", capture_preview)
    observed_create_sensitivity = []
    original_apply_area_class = services.runs._apply_area_class

    def capture_create(request, bundle):
        adjusted = original_apply_area_class(request, bundle)
        observed_create_sensitivity.append(adjusted.sensitive)
        return adjusted

    monkeypatch.setattr(services.runs, "_apply_area_class", capture_create)
    token = ensure_token(settings.gui_token_file)
    try:
        with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
            headers = {"Authorization": f"Bearer {token}"}
            preview = client.post("/gui/api/runs/preview", headers=headers, json={
                "workflow_id": "spec_review", "bundle": "team", "inputs": {"spec_text": "Review"},
                "execution_mode": "auto", "sensitive": False,
            })
            assert preview.status_code == 200, preview.text
            assert observed_preview_sensitivity and all(observed_preview_sensitivity)
            created = client.post("/gui/api/runs", headers=headers, json={
                "workflow_id": "spec_review", "bundle": "team", "project_name": "Privacy check",
                "inputs": {"spec_text": "Review"}, "execution_mode": "auto", "sensitive": False,
            })
            assert created.status_code == 202, created.text
            assert observed_create_sensitivity == [True]
    finally:
        services.runs.close()
