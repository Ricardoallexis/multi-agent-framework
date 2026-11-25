# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SIM-T03: the thread, the session, «Nueva sesión» and «Apps y agentes» in simple mode. Advanced mode stays as it was.

The pure models and the components that have no hooks (``ThreadCard``, ``ReviewBody``, ``Item``) run in Node (skipped without
it). A small walker reads the Preact tree they return: it collects the text and the links, and expands only ``ReadableValue``.
No server, no browser."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
APPS = STATIC_DIR / "extensions" / "apps"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

WALK = """
const flat = (v) => {
  const out = {text: [], hrefs: []};
  const walk = (n) => {
    if (n === null || n === undefined || n === false || n === true) return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (typeof n !== "object") { out.text.push(String(n)); return; }
    const props = n.props || {};
    if (props.href) out.hrefs.push(props.href);
    if (typeof n.type === "function") { if (["ReadableValue", "Message"].includes(n.type.name)) walk(n.type(props)); return; }
    walk(props.children);
  };
  walk(v);
  return {text: out.text.join(" "), hrefs: out.hrefs};
};
"""


def read(path) -> str:
    return ui_text(path)


def node(imports: dict[str, object], body: str):
    """Run ``body`` with the modules as ``m0``, ``m1``... and the walker ``flat``; the last console line is the JSON result."""
    lines = [f"import * as m{i} from {json.dumps(path.as_uri())};" for i, path in enumerate(imports)]
    result = subprocess.run([NODE, "--input-type=module", "-e", "\n".join(lines) + WALK + body], capture_output=True,
                            text=True, encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


CARDS = JS / "thread-cards.js"
THREAD_MODEL = JS / "thread-model.js"
NS_MODEL = JS / "new-session-model.js"
APPS_MODEL = APPS / "model.js"
ITEMS = JS / "session-items.js"
DETAIL = JS / "session-detail.js"

ARTIFACT = {"data": {"answer": "hola", "score": 3}}
COUNTERS = {"model_calls": 2, "max_model_calls": 9}


@needs_node
def test_raw_output_only_in_advanced():
    result = node({THREAD_MODEL: 0}, """
      console.log(JSON.stringify({simple: m0.showRawOutput("simple"), advanced: m0.showRawOutput("advanced"),
        links: [m0.threadLinksFor({task_href: "#plans"}, "simple"), m0.threadLinksFor({task_href: "#plans"}, "advanced"),
                m0.threadLinksFor({}, "advanced")]}));
    """)
    assert result["simple"] is False and result["advanced"] is True
    assert result["links"] == [{"detail": False, "related": False}, {"detail": True, "related": True},
                               {"detail": True, "related": False}]


@needs_node
def test_review_output_simple_has_no_json_or_calls():
    result = node({CARDS: 0}, """
      const props = {artifact: %s, counters: %s, steps: ["draft"], earlier: null, loading: false, loadError: false,
        showEarlier() {}};
      console.log(JSON.stringify({simple: flat(m0.ReviewBody({...props, mode: "simple"})).text,
                                  advanced: flat(m0.ReviewBody({...props, mode: "advanced"})).text,
                                  fallback: flat(m0.ReviewBody({...props})).text}));
    """ % (json.dumps(ARTIFACT), json.dumps(COUNTERS)))
    simple = result["simple"]
    assert "Salida para revisar" in simple and "answer" in simple and "hola" in simple
    for hidden in ("JSON completo", "Llamadas a modelo", "Ver pasos anteriores"):
        assert hidden not in simple
    for shown in ("JSON completo", "Llamadas a modelo", "Ver pasos anteriores"):
        assert shown in result["advanced"]
    assert result["fallback"] == result["advanced"]   # no mode is advanced


@needs_node
def test_review_output_advanced_lists_the_earlier_steps():
    result = node({CARDS: 0}, """
      const earlier = [{stepId: "draft", attempts: [{attempt: 1, output: {text: "viejo"}}]}];
      console.log(JSON.stringify({simple: flat(m0.ReviewBody({artifact: %s, counters: null, steps: ["draft"], earlier,
          loading: false, loadError: true, showEarlier() {}, mode: "simple"})).text,
        advanced: flat(m0.ReviewBody({artifact: %s, counters: null, steps: ["draft"], earlier, loading: false,
          loadError: true, showEarlier() {}, mode: "advanced"})).text}));
    """ % (json.dumps(ARTIFACT), json.dumps(ARTIFACT)))
    assert "Intento" not in result["simple"] and "draft" not in result["simple"]
    assert "No se pudieron leer" not in result["simple"]
    # the summary is one translated string now ("Intento 1"), not the two pieces the old flattening joined with a space
    assert "Intento 1" in result["advanced"] and "draft" in result["advanced"] and "No se pudieron leer" in result["advanced"]


@needs_node
def test_final_object_output_readable_in_simple():
    result = node({CARDS: 0}, """
      const entry = {kind: "final", seq: 4, text: "Terminó", output: {answer: "hola", items: [{n: 1}]}};
      const card = (mode) => flat(m0.ThreadCard({entry, status: "completed", runId: "run-1", token: "t", reviewView: null,
        busy: false, onAction() {}, mode}));
      console.log(JSON.stringify({simple: card("simple"), advanced: card("advanced")}));
    """)
    simple = result["simple"]["text"]
    assert "answer" in simple and "hola" in simple
    assert "{" not in simple and '"answer"' not in simple
    assert '"answer": "hola"' in result["advanced"]["text"]   # advanced keeps the JSON text


@needs_node
def test_final_text_and_error_do_not_change_in_simple():
    result = node({CARDS: 0}, """
      const card = (entry, mode) => flat(m0.ThreadCard({entry, status: "failed", runId: "r", token: "t", reviewView: null,
        busy: false, onAction() {}, mode})).text;
      console.log(JSON.stringify({text: card({kind: "final", seq: 1, output: "listo"}, "simple"),
                                  error: card({kind: "final", seq: 1, error: "falló"}, "simple")}));
    """)
    assert "listo" in result["text"] and "falló" in result["error"]


@needs_node
def test_thread_card_simple_hides_detail_links_and_step_model():
    result = node({CARDS: 0}, """
      const card = (entry, mode) => flat(m0.ThreadCard({entry, status: "running", runId: "run-1", token: "t",
        reviewView: null, busy: false, onAction() {}, mode}));
      const step = {kind: "step", seq: 2, step_id: "draft", text: "Listo", executor: {model: "gpt-x"}};
      const launched = {kind: "launched", seq: 1, workflow_id: "wf", task_id: "T1", task_href: "#plans/T1",
        team: {team_id: "eq"}, team_href: "#teams/eq"};
      console.log(JSON.stringify({stepSimple: card(step, "simple"), stepAdvanced: card(step, "advanced"),
        launchedSimple: card(launched, "simple"), launchedAdvanced: card(launched, "advanced")}));
    """)
    assert "Ver detalle" not in result["stepSimple"]["text"] and result["stepSimple"]["hrefs"] == []
    assert "draft" in result["stepSimple"]["text"] and "gpt-x" not in result["stepSimple"]["text"]
    assert "Ver detalle" in result["stepAdvanced"]["text"] and result["stepAdvanced"]["hrefs"] == ["#runs/run-1"]
    assert "gpt-x" in result["stepAdvanced"]["text"]
    assert result["launchedSimple"]["hrefs"] == [] and "Ver plan" not in result["launchedSimple"]["text"]
    assert result["launchedAdvanced"]["hrefs"] == ["#runs/run-1", "#plans/T1", "#teams/eq"]


def test_decision_attempt_is_an_advanced_detail():
    # Decision has hooks, so this reads its source: the attempt number is gated by showRawOutput.
    assert 'entry.attempt && showRawOutput(mode) ? " · " + t("thread.reason.attempt"' in read(CARDS)


@needs_node
def test_plus_menu_has_no_form_in_simple():
    result = node({NS_MODEL: 0}, """
      const entry = {id: "wf", bundle: "pack"};
      console.log(JSON.stringify({simple: m0.plusMenuEntries("simple"), advanced: m0.plusMenuEntries("advanced"),
        originSimple: m0.cardOrigin(entry, "simple"), originAdvanced: m0.cardOrigin(entry, "advanced"),
        builtIn: m0.cardOrigin({id: "wf"}, "advanced"), label: m0.originLabel(entry)}));
    """)
    assert result["simple"] == ["executor"] and result["advanced"] == ["executor", "form"]
    assert result["originSimple"] == ""
    assert result["originAdvanced"] == result["label"] == "Bundle «pack»"
    assert result["builtIn"] == "Incluido con el framework"


def test_new_session_draws_the_origin_and_the_menu_from_the_mode():
    source = read(JS / "new-session.js")
    assert "plusMenuEntries(currentMode())" in source and "cardOrigin(entry, currentMode())" in source
    assert 'Usar un ejecutor</button>' in source   # «Usar un ejecutor» stays in both modes
    assert 'plusMenuEntries(currentMode()).includes("form")' in source


def _row(**extra):
    return {"id": "codex_cli", "available": False, **extra}


@needs_node
def test_apps_simple_hides_copy_lines():
    result = node({APPS_MODEL: 0}, """
      const status = {plans: {}};
      const cases = {
        executor: m0.missingAction(%s, "executor", status, "simple"),
        agy: m0.missingAction(%s, "executor", status, "simple"),
        model: m0.missingAction({id: "openai", available: false, missing_step: "enable"}, "model", status, "simple"),
        key: m0.missingAction({id: "gemini", available: false, missing_step: "key"}, "model", status, "simple"),
        invalid: m0.missingAction({id: "gemini", available: false, reason: "key_invalid"}, "model", status, "simple"),
      };
      console.log(JSON.stringify({cases, text: m0.ADVANCED_STEP_TEXT}));
    """ % (json.dumps(_row(missing_step="enable")), json.dumps(_row(id="antigravity_cli", missing_step="enable"))))
    assert result["text"] == "Este paso se hace en modo avanzado"
    for name, action in result["cases"].items():
        if name in ("executor", "agy"):   # EJE-T02: «Activar» is not a file edit, so an executor keeps its button in simple mode
            assert action["kind"] == "enable_executor", name
            assert not {"copyText", "catalogLines", "envLines", "manualText"} & set(action), name
            continue
        assert action["kind"] == "advanced_only", name
        assert action["text"] == result["text"] and action["title"], name
        assert set(action) == {"kind", "title", "text"}, name   # no copyText, catalogLines, envLines, line, setting or detail


@needs_node
def test_apps_simple_keeps_console_without_command():
    result = node({APPS_MODEL: 0}, """
      const status = {plans: {codex_cli: {install: {command: ["npm", "i", "-g", "x"], source: "docs"},
                                          login: {command: ["codex", "login"]}}}};
      const install = {id: "codex_cli", available: false, missing_step: "install"};
      const login = {id: "codex_cli", available: false, missing_step: "session"};
      console.log(JSON.stringify({install: m0.missingAction(install, "executor", status, "simple"),
        login: m0.missingAction(login, "executor", status, "simple"),
        advanced: m0.missingAction(install, "executor", status, "advanced")}));
    """)
    for name in ("install", "login"):
        action = result[name]
        assert action["kind"] == "console" and "commandText" not in action
        assert action["step"] in {"install", "login"} and action["title"] and action["detail"]
    assert result["advanced"]["commandText"] == "npm i -g x"


@needs_node
def test_apps_advanced_unchanged():
    """Without a mode, and with "advanced", missingAction answers exactly what it answered before SIM-T03."""
    result = node({APPS_MODEL: 0}, """
      const status = {plans: {codex_cli: {install: {command: ["npm", "i", "-g", "x"], source: "docs", source_date: "2026-01-01"}}}};
      const rows = [
        [{id: "codex_cli", available: false, missing_step: "enable"}, "executor"],
        [{id: "antigravity_cli", available: false, missing_step: "enable", notice_accepted: false}, "executor"],
        [{id: "openai", available: false, missing_step: "enable"}, "model"],
        [{id: "gemini", available: false, missing_step: "key"}, "model"],
        [{id: "gemini", available: false, reason: "key_invalid"}, "model"],
        [{id: "codex_cli", available: false, missing_step: "install"}, "executor"],
        [{id: "codex_cli", available: false, reason: "limit_reached"}, "executor"],
        [{id: "codex_cli", available: true}, "executor"],
      ];
      console.log(JSON.stringify(rows.map(([row, kind]) => [m0.missingAction(row, kind, status),
        m0.missingAction(row, kind, status, "advanced")])));
    """)
    for default, advanced in result:
        assert default == advanced
    kinds = [pair[0] and pair[0]["kind"] for pair in result]
    assert kinds == ["enable_executor", "enable_executor", "enable_model", "key", "key", "console", "info", None]
    # EJE-T02: an executor has no lines to paste any more (its manual way is one sentence)
    assert not {"catalogLines", "copyText", "envLines"} & set(result[0][0]) and "manualText" in result[0][0]
    assert not {"catalogLines", "copyText", "envLines"} & set(result[1][0])
    assert result[2][0]["copyText"] == "OPENAI_ENABLED=true" and result[3][0]["setting"] == "GEMINI_API_KEY"
    assert result[5][0]["commandText"] == "npm i -g x" and result[5][0]["sourceDate"] == "2026-01-01"


@needs_node
def test_apps_view_passes_the_mode_and_keeps_certificate_and_probe_in_both():
    result = node({APPS_MODEL: 0}, """
      const payload = {connections: [
        {id: "codex_cli", available: false, missing_step: "enable", installed: true},
        {id: "openai", available: false, reason: "tls_certificate", tls_fix: {trust: "system", label: "Usar los certificados de Windows"}},
      ]};
      const brief = (mode) => { const v = m0.buildAppsView(payload, null, mode);
        return {action: v.executors[0].action.kind, canProbe: v.models[0].canProbe, tls: v.models[0].tlsFix}; };
      console.log(JSON.stringify({simple: brief("simple"), advanced: brief("advanced"),
        none: m0.buildAppsView(payload).executors[0].action.kind}));
    """)
    assert result["simple"]["action"] == result["advanced"]["action"] == result["none"] == "enable_executor"   # EJE-T02
    for mode in ("simple", "advanced"):
        assert result[mode]["canProbe"] is True and result[mode]["tls"] == {"trust": "system", "label": "Usar los certificados de Windows"}


def test_apps_view_reads_the_mode_and_draws_the_advanced_step():
    index = read(APPS / "index.js")
    assert "buildAppsView(connections, setupStatus, currentMode(), activations)" in index
    assert 'action.kind === "advanced_only"' in index and 'topic="apps.advanced_step"' in index
    assert '"apps.advanced_step": {' in read(APPS / "help.js")


@needs_node
def test_session_hides_tokens_per_message_in_simple():
    result = node({ITEMS: 0}, """
      const item = {kind: "assistant_message", text: "Hola", usage: {input: 10, output: 5, total: 15}};
      console.log(JSON.stringify({simple: flat(m0.Item({item, mode: "simple"})).text,
        advanced: flat(m0.Item({item, mode: "advanced"})).text, none: flat(m0.Item({item})).text}));
    """)
    assert "Hola" in result["simple"] and "Tokens de este mensaje" not in result["simple"]
    assert "Tokens de este mensaje" in result["advanced"] and result["none"] == result["advanced"]


@needs_node
def test_a_run_event_without_known_text_is_its_type_in_simple():
    result = node({DETAIL: 0}, """
      const event = {id: 7, type: "step_started", ts: "2026-11-21T10:00:00Z", payload: {step_id: "draft", attempt: 2}};
      const known = {id: 8, type: "context_warning", payload: {step_id: "draft", ratio: 0.5}};
      console.log(JSON.stringify({simple: m0.frameworkItem(event, "simple").text, advanced: m0.frameworkItem(event, "advanced").text,
        none: m0.frameworkItem(event).text, known: m0.frameworkItem(known, "simple").text}));
    """)
    assert result["simple"] == "step_started"
    assert result["advanced"] == result["none"] == 'step_started {"step_id":"draft","attempt":2}'
    assert result["known"].startswith("Aviso de contexto")


def test_the_views_read_the_mode_with_currentmode():
    for path in (JS / "thread.js", JS / "session-detail.js", JS / "new-session.js", APPS / "index.js"):
        source = read(path)
        assert "currentMode" in source and "gui-mode.js" in source, path.name
    assert "mode=${currentMode()}" in read(JS / "thread.js")
