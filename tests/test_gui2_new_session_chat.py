# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI2-T03: «Nueva sesión» as a guided chat (new-session.js, new-session-model.js).

Static checks over the client files and the stylesheet (nothing is injected as HTML, nothing runs without the summary's
«Lanzar», help for every control, keyboard, 44 px targets, 375 px), Node tests of the pure conversation model (matching,
commands, questions, the reducer, the catalog reader; skipped when Node is not installed), and a contract test that
feeds the real inputs endpoint into the model and posts what it builds to the real create-run API (no browser)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from test_gui_help import HELP, VIEW_FILES, read as read_js
from test_gui_sessions_view import FORBIDDEN as BASE_FORBIDDEN

JS = STATIC_DIR / "js"
CSS = STATIC_DIR / "new-session.css"
NODE = shutil.which("node")
BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
CHAT_FILES = ["new-session.js", "new-session-model.js"]
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def source(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def css_text() -> str:
    return re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)


def run_node(script: str, data: dict | None = None) -> dict:
    """Run a module script that can ``import`` the chat model (``MODEL``) and read ``DATA``; it prints one JSON object."""
    prelude = (f'import * as m from {json.dumps((JS / "new-session-model.js").as_uri())};\n'
               "const DATA = JSON.parse(process.env.DATA || 'null');\n"
               "globalThis.fetch = () => { throw new Error('the model must not touch the network'); };\n")
    done = subprocess.run([NODE, "--input-type=module", "-e", prelude + script], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env={**os.environ, "DATA": json.dumps(data)})
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# CUO-T03: a limited provider returns to word matching with a concrete notice.
def test_new_session_limit_keeps_keyword_fallback_and_observed_time(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from multiagent.errors import QuotaExceeded
    from test_gui2_new_session_model import Scripted, make

    retry_at = datetime(2026, 11, 20, 18, tzinfo=timezone.utc)
    made = make(tmp_path, Scripted(error=QuotaExceeded("synthetic quota", retry_at=retry_at)),
                gui_new_session_model="cloud_fast")
    monkeypatch.setattr(made.connections, "list", lambda: {"connections": [
        {"id": "ollama", "kind": "model", "available": False, "enabled": True},
        {"id": "gemini", "kind": "model", "available": True, "enabled": True},
        {"id": "claude_cli", "kind": "executor", "available": True, "enabled": True},
    ]})
    try:
        result = made.new_session.interpret("review a spec", confirm_cloud=True)
        assert result["source"] == "keywords" and result["reason"] == "limit_reached"
        assert result["proposal"] is None
        assert result["limit"]["resets_at"] == retry_at.isoformat()
        assert result["limit"]["suggestions"] == ["claude_cli"]
    finally:
        made.runs.close()


# --- the files ---------------------------------------------------------------------------------------------------

def test_chat_files_exist_and_the_page_links_the_stylesheet_after_app_css():
    for name in CHAT_FILES:
        assert (JS / name).is_file(), name
    assert CSS.is_file()
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert html.index("/gui/static/app.css") < html.index("/gui/static/new-session.css")
    assert re.findall(r"<script[^>]*>", html) == ['<script type="module" src="/gui/static/js/main.js">']
    assert "style=" not in html and "<style" not in html


@pytest.mark.parametrize("name", CHAT_FILES + ["create-run.js", "runs.js", "main.js"])
def test_chat_code_has_no_html_injection_eval_inline_handlers_or_remote_code(name):
    text = source(name)
    for pattern, label in BASE_FORBIDDEN:
        assert not re.search(pattern, text), f"{name}: {label}"
    assert "__html" not in text and "style=" not in text


def test_the_stylesheet_follows_the_csp_the_theme_and_the_touch_rules():
    sheet = CSS.read_text(encoding="utf-8")
    assert "style=" not in sheet and "@import" not in sheet and "url(" not in sheet      # CSP: same origin only
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css_text())          # colours come from the app.css variables, so dark mode follows
    text = css_text()
    for selector in (".ns-chip", ".ns-card", ".ns-composer textarea"):
        assert re.search(re.escape(selector) + r"\s*\{[^}]*min-height:\s*var\(--target\)", text), selector
    assert "minmax(min(100%," in text                                  # the cards fit a 375 px screen
    assert re.search(r"@media \(max-width: 599\.98px\)", text)
    assert "position: sticky" in text                                  # the writing box stays in reach
    assert "overflow-wrap: anywhere" in text and "white-space: pre-wrap" in text


def test_the_chat_replaces_the_form_at_new_and_the_form_stays_available():
    main = source("main.js")
    assert 'import { NewSession } from "./new-session.js"' in main
    # The team and plan threads (CHT-T05, CHT-T06) take "#new/team/<id>" and "#new/plan/<id>"; every other "#new" is the chat.
    assert re.search(r'\$\{creating (?:&& route\.kind !== "\w+" )*&& html`<\$\{NewSession\} token=\$\{token\} mock=\$\{mock\} />`\}', main)
    assert "interpret" not in main                                     # the shell does not call the interpreter: new-session.js does
    assert "<${Runs}" in main and "startCreating" not in main + source("runs.js")
    chat = source("new-session.js")
    assert 'import { CreateRun, PreviewPanel } from "./create-run.js"' in chat
    assert re.search(r"<\$\{CreateRun\} token=\$\{token\} mock=\$\{mock\} initial=\$\{formInitial\}", chat)
    runs = source("runs.js")
    assert "<${CreateRun}" in runs and "Nueva ejecución" in runs        # the classic way from «Ejecuciones» is untouched
    form = source("create-run.js")
    assert "export function CreateRun({ token, mock, onCreated, onClose, initial = null })" in form
    assert "export function PreviewPanel" in form
    assert 'if (!seed.current) setWorkflowId("")' in form              # without a seed the form clears the workflow as before


def test_the_model_is_pure_and_matching_is_deterministic_with_no_dead_hooks():
    model = source("new-session-model.js")
    imports = re.findall(r'^import\b.*$', model, re.M)
    assert all(re.match(r'^import\s+\{[^}]+\}\s+from\s+"\./quota-text\.js";?$', line) for line in imports), imports
    quota_text = source("quota-text.js")
    assert not re.search(r"^import\b", quota_text, re.M)
    for forbidden in ("fetch(", "apiPost", "apiGet", "document.", "window.", "localStorage"):
        assert forbidden not in model, forbidden
        assert forbidden not in quota_text, forbidden
    chat = source("new-session.js")
    for text in (model, chat):                                         # no unwired hook (GUI2-T17 wired the interpretation: test_gui2_new_session_model.py)
        assert "hinted" not in text and "NOT wired" not in text


def test_messages_are_rendered_as_text_and_the_log_is_announced():
    chat = source("new-session.js")
    assert "${message.text}" in chat and 'role="log"' in chat and 'aria-label="Conversación"' in chat
    assert "dangerouslySetInnerHTML" not in chat and "innerHTML" not in chat
    # every other piece of data the screen shows goes through ${...} as text too
    assert chat.count("${cardDescription(entry)}") == 1 and "${question.description}" in chat and ": row.value}" in chat


# --- nothing runs without the confirmation -----------------------------------------------------------------------------

def test_only_the_launch_button_of_the_summary_creates_a_run():
    chat = source("new-session.js")
    posts = re.findall(r'apiPost\("(/gui/api/[a-z/-]+)"', chat)
    assert sorted(posts) == ["/gui/api/new-session/interpret", "/gui/api/runs", "/gui/api/runs/preview"]     # the first one only proposes
    start = chat.index("async function launch()")
    end = chat.index("function openForm()")
    body = chat[start:end]
    assert 'apiPost("/gui/api/runs", token' in body and "idempotency_key" in body
    assert 'state.phase !== "confirm"' in body and "(!mock && !confirmReal)" in body       # only from the summary, and the real-model check
    refs = re.findall(r"\blaunch\b(?!\w)", re.sub(r'"[^"\n]*"', "", chat))             # the function, outside of strings
    assert re.findall(r"onLaunch=\$\{launch\}", chat) == ["onLaunch=${launch}"]         # one button is wired to it...
    assert not re.search(r"launch\(\)", chat.replace("async function launch()", ""))   # ...and nothing else calls it
    assert refs.count("launch") >= 2
    assert "Lanzar" in chat and "Editar" in chat and "Ver como formulario" in chat
    # the summary card gets the focus, never its «Lanzar» button: a held Intro key must not launch
    assert "confirmBox.current.focus()" in chat and ".focus()" in chat
    assert not re.search(r"launch\w*Ref|launchButton", chat)


def test_the_real_model_checkbox_is_kept_as_in_the_form():
    chat = source("new-session.js")
    assert "Entiendo que el servidor no está en modo Mock" in chat
    assert 'blockers.push("Confirma que entiendes que el servidor no está en modo Mock.")' in chat
    assert 'disabled=${busy || blockers.length > 0}' in chat


# --- keyboard, focus, help ---------------------------------------------------------------------------------------------

def test_composer_keyboard_focus_and_labels():
    chat = source("new-session.js")
    keydown = chat[chat.index("function SessionBox"):chat.index("// SES-T03:")]
    assert 'event.key === "Enter"' in keydown and "!event.shiftKey" in keydown and "!event.isComposing" in keydown
    assert "event.preventDefault()" in keydown and "!event.repeat" in keydown        # Intro sends; held key and IME do not
    assert re.search(r'<label for="ns-composer-input">', chat) and 'id="ns-composer-input"' in chat
    assert '<form class="ns-composer"' in chat and 'type="submit"' in chat
    assert "(pointer: fine)" in chat                                   # no soft keyboard forced open on a touch screen
    assert 'role="alert"' in chat and 'aria-invalid=${error ? "true" : null}' in chat
    assert "ns-composer-error" in chat and "ns-q-desc" in chat         # the box is described by the question and the error
    assert chat.count('aria-live="polite"') >= 1 and 'role="group" aria-label="Opciones de inicio"' in chat
    assert 'tabindex="-1" ref=${boxRef}' in chat
    assert "scrollIntoView" in chat


def test_every_chat_control_is_covered_by_the_help_rules_and_its_topics_exist():
    assert "new-session.js" in VIEW_FILES                               # test_gui_help checks helpFor on every control
    chat = source("new-session.js")
    used = set(re.findall(r'topic="(chat\.[a-z_]+)"', chat)) | set(re.findall(r'helpFor\("(chat\.[a-z_]+)"', chat))
    assert used == {key for key in HELP if key.startswith("chat.")}, used ^ {k for k in HELP if k.startswith("chat.")}
    assert {"create.mode", "create.sensitive", "create.confirm_real"} <= set(re.findall(r'"(create\.[a-z_]+)"', chat))
    for key in sorted(used):
        assert re.search(rf'topic="{re.escape(key)}"', chat), key        # every topic that guides a control also draws its hint
    assert "data-help-exempt" not in chat                               # no control of the chat is exempt
    assert "Por ahora abre el formulario" not in read_js("help-text.js")          # «Nueva sesión» no longer opens the form
    assert "Lanzar" in " ".join(HELP["shell.new_session"]["more"])


# --- the pure model (Node) -----------------------------------------------------------------------------------------------

CATALOG = [
    {"id": "summarize_document", "bundle": "general", "description": "Resume un documento en lenguaje claro para un público "
     "concreto; sirve para actas, informes, correos largos o artículos.", "described": True},
    {"id": "review_checklist", "bundle": "general", "description": "Revisa una especificación u otro documento contra una "
     "lista de criterios y emite un veredicto con los problemas y las sugerencias.", "described": True},
    {"id": "research_options", "bundle": "general", "description": "Investiga una pregunta con el contexto que aportes, compara "
     "las opciones posibles y propone una recomendación razonada.", "described": True},
    {"id": "social_post", "bundle": None, "description": "Redacta una publicación para una red social a partir de un tema, "
     "una audiencia y unas instrucciones.", "described": True},
    {"id": "plan_event", "bundle": "synthetic", "description": "Organiza una reunión o un evento pequeño: define objetivo, "
     "fechas, invitados y tareas pendientes.", "described": True},
]


@needs_node
def test_matching_is_deterministic_on_names_and_descriptions_and_ignores_accents():
    out = run_node("""
      const run = (q, c = DATA.catalog) => m.matchWorkflows(q, c).map((x) => ({ id: x.entry.id, matched: x.matched }));
      const tie = [{ id: "b_flow", bundle: null, description: "informe" }, { id: "a_flow", bundle: null, description: "informe" },
                   { id: "a_flow", bundle: "x", description: "informe" }];
      console.log(JSON.stringify({
        summary: run("quiero un resumen del acta de la reunión"), review: run("revisar un documento"),
        compare: run("comparar opciones"), social: run("Redacta un post para redes sociales"),
        accents: [run("REUNIÓN"), run("reunion")], none: run("xyzzy"), fillers: run("para el de la quiero necesito"),
        empty: run(""), half: run("sum"), again: [run("documento"), run("documento")],
        ties: m.matchWorkflows("informe", tie).map((x) => x.entry.bundle + "|" + x.entry.id),
        limited: m.matchWorkflows("documento", DATA.catalog, { limit: 1 }).length,
        words: m.keywords("¿Qué Reunión organizo, de la dirección?"),
        origins: DATA.catalog.map(m.originLabel),
      }));
    """, {"catalog": CATALOG})
    assert out["summary"][0] == {"id": "summarize_document", "matched": ["resumen", "acta"]}   # name and description; filler words ignored
    assert [item["id"] for item in out["summary"]] == ["summarize_document", "plan_event"]
    assert out["review"][0]["id"] == "review_checklist" and out["review"][0]["matched"] == ["revisar", "documento"]
    assert out["compare"][0]["id"] == "research_options"
    assert out["social"][0]["id"] == "social_post" and "post" in out["social"][0]["matched"]
    assert out["accents"][0] == out["accents"][1] and out["accents"][0][0]["id"] == "plan_event"
    assert out["none"] == [] and out["fillers"] == [] and out["empty"] == []
    assert out["half"][0]["id"] == "summarize_document"                                          # a name typed halfway
    assert out["again"][0] == out["again"][1]                                                    # same input, same answer
    assert out["ties"] == ["null|a_flow", "null|b_flow", "x|a_flow"]                              # ties: built-in first, then by id
    assert out["limited"] == 1
    assert out["words"] == ["reunion", "organizo", "direccion"]
    assert out["origins"] == ["Bundle «general»"] * 3 + ["Incluido con el framework", "Bundle «synthetic»"]


@needs_node
def test_commands_starters_and_card_texts():
    out = run_node("""
      console.log(JSON.stringify({
        commands: m.COMMANDS, starters: m.STARTERS,
        parsed: ["/workflow", "/workflow  resumen del acta ", "/Workflows", "/continuar", "/ayuda", "/AYUDA ahora",
                 "/home/ana/doc.txt", "/otro", "workflow", "  /ayuda", "/continuarya", ""].map(m.parseCommand),
        cards: [m.cardDescription({ description: "Hace algo." }), m.cardDescription({ description: "", described: false }),
                m.cardDescription({ description: "", described: true }), m.cardDescription({ description: "", described: true, unreadable: true })],
      }));
    """)
    assert out["commands"] == ["workflow", "continuar", "ayuda"]
    assert [item["label"] for item in out["starters"]] == ["Trabajar en un workflow", "Continuar una ejecución", "Ayuda"]
    assert [item["id"] for item in out["starters"]] == out["commands"]          # a starter is the same as its command
    assert out["parsed"] == [
        {"name": "workflow", "rest": ""}, {"name": "workflow", "rest": "resumen del acta"}, {"name": "workflow", "rest": ""},
        {"name": "continuar", "rest": ""}, {"name": "ayuda", "rest": ""}, {"name": "ayuda", "rest": "ahora"},
        None, None, None, {"name": "ayuda", "rest": ""}, None, None]                # a path is an answer, not a command
    assert out["cards"][0] == "Hace algo." and "Leyendo" in out["cards"][1]
    assert "no trae descripción" in out["cards"][2] and "No se pudo leer" in out["cards"][3]


SPEC = {
    "description": "Un workflow de prueba.",
    "inputs": [
        {"name": "question", "label": "Pregunta", "description": "La pregunta.", "example": "¿Qué CRM conviene?", "type": "long_text",
         "choices": [], "required": True, "source": "declared", "context": False, "used_in": ["a"], "prompt_line": "Q: {question}"},
        {"name": "background", "label": "Contexto", "description": "Datos que ya tienes.", "example": "Dos zonas horarias", "type": "text",
         "choices": [], "required": False, "source": "declared", "context": False, "used_in": ["a"], "prompt_line": None},
        {"name": "level", "label": "Nivel", "description": "Cuánto detalle.", "example": "media", "type": "choice",
         "choices": ["breve", "media"], "required": False, "source": "declared", "context": False, "used_in": ["a"], "prompt_line": None},
        {"name": "brand_context", "label": "Brand context", "description": None, "example": None, "type": "text", "choices": [],
         "required": False, "source": "derived", "context": True, "used_in": ["a"], "prompt_line": "Brand: {brand_context}"},
        {"name": "tone", "label": "Tone", "description": None, "example": None, "type": "text", "choices": [],
         "required": True, "source": "derived", "context": False, "used_in": ["a", "b"], "prompt_line": "Tone: {tone}"},
    ],
}


@needs_node
def test_questions_follow_the_declared_inputs_and_the_project_comes_last():
    out = run_node("""
      const questions = m.buildQuestions(DATA.spec);
      const by = Object.fromEntries(questions.map((q) => [q.key, q]));
      console.log(JSON.stringify({
        keys: questions.map((q) => q.key), kinds: questions.map((q) => q.kind), required: questions.map((q) => q.required),
        tone: by.tone, level: by.level, project: by["@project"], noSpec: m.buildQuestions(null).map((q) => q.key),
        checks: [
          m.validateAnswer(by.question, "   "), m.validateAnswer(by.question, "  texto  "), m.validateAnswer(by.background, ""),
          m.validateAnswer(by.level, "MEDIA"), m.validateAnswer(by.level, "enorme"), m.validateAnswer(by.level, ""),
          m.validateAnswer(by["@project"], "a"), m.validateAnswer(by["@project"], " Informe "), m.validateAnswer(by["@project"], "x".repeat(121)),
          m.validateAnswer(by["@project"], "x".repeat(120)),
        ],
      }));
    """, {"spec": SPEC})
    assert out["keys"] == ["question", "background", "level", "tone", "@project"]      # brand_context (optional context) is not asked
    assert out["kinds"] == ["long_text", "text", "choice", "text", "text"]
    assert out["required"] == [True, False, False, True, True]
    assert "El workflow no describe esta entrada." in out["tone"]["description"] and "Tone: {tone}" in out["tone"]["description"]
    assert out["tone"]["example"] == "" and out["level"]["example"] == "" and out["level"]["choices"] == ["breve", "media"]
    assert out["project"]["required"] is True and out["project"]["source"] == "project"
    assert out["noSpec"] == ["@project"]
    required, trimmed, optional, choice, bad_choice, choice_empty, short, project, long, edge = out["checks"]
    assert "obligatoria" in required["error"] and trimmed == {"value": "texto"} and optional == {"value": ""}
    assert choice == {"value": "media"} and "breve, media" in bad_choice["error"] and choice_empty == {"value": ""}
    assert "al menos 2" in short["error"] and project == {"value": "Informe"} and "120" in long["error"] and edge == {"value": "x" * 120}


FLOW = """
  const spec = DATA.spec, catalog = DATA.catalog;
  const snap = (s) => JSON.stringify(s);
  let s = m.createConversation();
  const log = {};
  const step = (action) => { const before = snap(s); const next = m.reduce(s, action); log.pure = (log.pure !== false) && snap(s) === before; s = next; return s; };
  const texts = () => s.messages.map((x) => x.role + ": " + x.text);

  log.start = { phase: s.phase, messages: s.messages.length };
  step({ type: "send", text: "/ayuda" });
  log.help = { phase: s.phase, last: s.messages[s.messages.length - 1].text, count: s.messages.length };
  step({ type: "reset" });
  step({ type: "command", name: "workflow", rest: "", echo: "Trabajar en un workflow", catalog });
  log.chip = { phase: s.phase, showAll: s.showAll, n: s.suggestions.length, first: s.messages[0].text, order: s.suggestions.map((x) => x.key) };
  step({ type: "send", text: "quiero un resumen del acta", catalog });
  log.free = { phase: s.phase, source: s.source, showAll: s.showAll, keys: s.suggestions.map((x) => x.key),
               cards: m.suggestedEntries(s, catalog).map((x) => x.entry.id + ":" + x.matched.join("+")) };
  step({ type: "show_all", catalog });
  log.all = { n: s.suggestions.length, showAll: s.showAll };
  step({ type: "send", text: "zzz", catalog: [] });
  log.empty = { phase: s.phase, n: s.suggestions.length, last: s.messages[s.messages.length - 1].text };
  step({ type: "send", text: "qwerty", catalog });
  log.nomatch = { source: s.source, showAll: s.showAll, n: s.suggestions.length };

  step({ type: "pick", workflow: { id: "summarize_document", bundle: "general", description: "d" }, spec });
  log.pick = { phase: s.phase, index: s.index, q: m.currentQuestion(s).key, progress: m.progress(s), last: s.messages[s.messages.length - 1].text, names: s.inputNames };
  const count = s.messages.length;
  step({ type: "send", text: "   " });
  log.emptyRequired = { error: s.error, count: s.messages.length === count, index: s.index };
  step({ type: "typing" });
  log.typing = { error: s.error };
  step({ type: "send", text: "  Elegir un CRM  " });
  log.q1 = { index: s.index, answer: s.answers.question, error: s.error, last: s.messages[s.messages.length - 1].text };
  step({ type: "skip" });
  log.skip = { index: s.index, answer: s.answers.background, echoed: s.messages[s.messages.length - 2].text };
  step({ type: "send", text: "enorme" });
  log.badChoice = { index: s.index, error: s.error };
  step({ type: "back" });
  log.back = { index: s.index, last: s.messages[s.messages.length - 1].text };
  step({ type: "send", text: "" });                 // optional: empty keeps it skipped
  step({ type: "send", text: "MEDIA" });
  log.choice = { answer: s.answers.level, index: s.index };
  const before = snap(s);
  step({ type: "skip" });                            // tone is required: skip does nothing
  log.skipRequired = { same: snap(s) === before, index: s.index };
  step({ type: "send", text: "serio" });
  step({ type: "send", text: "a" });
  log.shortProject = { error: s.error, phase: s.phase };
  step({ type: "send", text: "  Proy  " });
  log.confirm = { phase: s.phase, rows: m.answerRows(s), last: s.messages[s.messages.length - 1].text };
  log.body = m.runBody(s, { mode: "local", sensitive: true });
  log.bodyDefault = m.runBody(s);
  log.seed = m.formSeed(s, { mode: "cloud", sensitive: false });
  log.noSeed = m.formSeed(m.createConversation());
  const rowsBefore = s.messages.length;
  step({ type: "send", text: "lánzalo ya", catalog });
  log.confirmText = { phase: s.phase, added: s.messages.length - rowsBefore, last: s.messages[s.messages.length - 1].text, question: m.currentQuestion(s) };
  step({ type: "edit" });
  log.edit = { phase: s.phase, index: s.index, q: m.currentQuestion(s).key, kept: s.answers, last: s.messages[s.messages.length - 1].text };
  step({ type: "back" });
  log.backAtStart = { index: s.index };
  step({ type: "send", text: "/ayuda" });
  log.helpInAsk = { phase: s.phase, index: s.index, answers: Object.keys(s.answers).length, last: s.messages[s.messages.length - 1].text };
  step({ type: "send", text: "/home/ana/acta.txt" });   // a path is an answer, not a command
  log.path = { answer: s.answers.question, index: s.index };
  step({ type: "send", text: "/workflow", catalog });
  log.restartByCommand = { phase: s.phase, workflow: s.workflow, answers: s.answers, questions: s.questions.length };
  step({ type: "send", text: "/continuar" });
  log.continue = { phase: s.phase };
  step({ type: "launched", runId: "r1" });
  log.launched = { phase: s.phase };
  step({ type: "notice", text: "Aviso." });
  log.notice = s.messages[s.messages.length - 1];
  log.ids = s.messages.map((x) => x.id);
  log.unknown = m.reduce(s, { type: "nope" }) === s;
  console.log(JSON.stringify(log));
"""


@needs_node
def test_the_conversation_flows_from_the_greeting_to_the_summary_without_running_anything():
    log = run_node(FLOW, {"spec": SPEC, "catalog": CATALOG})
    assert log["pure"] is True                                                    # the reducer never mutates the state it receives
    assert log["start"] == {"phase": "welcome", "messages": 0}
    assert log["help"]["phase"] == "welcome" and log["help"]["count"] == 2
    assert "/workflow" in log["help"]["last"] and "/continuar" in log["help"]["last"] and "/ayuda" in log["help"]["last"]
    assert "Nada se ejecuta hasta que pulses «Lanzar»" in log["help"]["last"]
    assert log["chip"]["phase"] == "choose" and log["chip"]["showAll"] is True and log["chip"]["n"] == len(CATALOG)
    assert log["chip"]["first"] == "Trabajar en un workflow"                      # the chip label is what the user "said"
    assert log["chip"]["order"][0] == "|social_post"                              # built-in first, then bundles by name and id
    assert log["free"]["phase"] == "choose" and log["free"]["source"] == "keywords" and log["free"]["showAll"] is False
    assert log["free"]["cards"][0] == "summarize_document:resumen+acta"
    assert log["all"] == {"n": len(CATALOG), "showAll": True}
    assert log["empty"]["n"] == 0 and "No hay workflows disponibles" in log["empty"]["last"]
    assert log["nomatch"] == {"source": "", "showAll": True, "n": len(CATALOG)}   # nothing matched: all of them, and it says so

    assert log["pick"]["phase"] == "ask" and log["pick"]["index"] == 0 and log["pick"]["q"] == "question"
    assert log["pick"]["progress"] == {"n": 1, "total": 5}
    assert log["pick"]["last"] == "Pregunta 1 de 5: Pregunta"
    assert log["pick"]["names"] == ["question", "background", "level", "brand_context", "tone"]
    assert log["emptyRequired"]["count"] is True and "obligatoria" in log["emptyRequired"]["error"] and log["emptyRequired"]["index"] == 0
    assert log["typing"]["error"] == ""
    assert log["q1"] == {"index": 1, "answer": "Elegir un CRM", "error": "", "last": "Pregunta 2 de 5: Contexto (opcional)"}
    assert log["skip"] == {"index": 2, "answer": "", "echoed": "(omitida)"}
    assert log["badChoice"]["index"] == 2 and "breve, media" in log["badChoice"]["error"]
    assert log["back"]["index"] == 1 and "pregunta 2 de 5" in log["back"]["last"]
    assert log["choice"] == {"answer": "media", "index": 3}
    assert log["skipRequired"] == {"same": True, "index": 3}
    assert log["shortProject"]["phase"] == "ask" and "al menos 2" in log["shortProject"]["error"]
    assert log["confirm"]["phase"] == "confirm"
    assert [(r["label"], r["value"], r["empty"]) for r in log["confirm"]["rows"]] == [
        ("Pregunta", "Elegir un CRM", False), ("Contexto", "", True), ("Nivel", "media", False), ("Tone", "serio", False), ("Proyecto", "Proy", False)]
    assert "Nada se ejecuta hasta que pulses «Lanzar»" in log["confirm"]["last"]
    # the body is the one the form sends: every input of the workflow (the unasked context one empty), project trimmed
    assert log["body"] == {"workflow_id": "summarize_document", "bundle": "general", "project_name": "Proy",
                           "inputs": {"question": "Elegir un CRM", "background": "", "level": "media", "brand_context": "", "tone": "serio"},
                           "execution_mode": "local", "sensitive": True}
    assert log["bodyDefault"]["execution_mode"] == "auto" and log["bodyDefault"]["sensitive"] is False
    assert log["seed"] == {"bundle": "general", "workflowId": "summarize_document", "project": "Proy", "mode": "cloud", "sensitive": False,
                           "values": {"question": "Elegir un CRM", "background": "", "level": "media", "brand_context": "", "tone": "serio"}}
    assert log["noSeed"] is None
    assert log["confirmText"]["phase"] == "confirm" and log["confirmText"]["added"] == 2 and log["confirmText"]["question"] is None
    assert "«Lanzar»" in log["confirmText"]["last"] and "«Ver como formulario»" in log["confirmText"]["last"]
    assert log["edit"]["phase"] == "ask" and log["edit"]["index"] == 0 and log["edit"]["q"] == "question"
    assert log["edit"]["kept"]["question"] == "Elegir un CRM" and log["edit"]["kept"]["@project"] == "Proy"
    assert log["backAtStart"]["index"] == 0
    assert log["helpInAsk"] == {"phase": "ask", "index": 0, "answers": 5, "last": log["helpInAsk"]["last"]}
    assert "pregunta 1 de 5: Pregunta" in log["helpInAsk"]["last"] and "Responde en la caja de abajo" in log["helpInAsk"]["last"]
    assert log["path"] == {"answer": "/home/ana/acta.txt", "index": 1}
    assert log["restartByCommand"] == {"phase": "choose", "workflow": None, "answers": {}, "questions": 0}
    assert log["continue"]["phase"] == "continue" and log["launched"]["phase"] == "launched"
    assert log["notice"]["role"] == "assistant" and log["notice"]["text"] == "Aviso."
    assert log["ids"] == sorted(set(log["ids"])) and log["unknown"] is True       # ids are unique and increasing


@needs_node
def test_runs_to_continue_and_the_catalog_reader():
    out = run_node("""
      const runs = [
        { id: "a", status: "completed" }, { id: "b", status: "running" }, { id: "c", status: "waiting_human" },
        { id: "d", status: "interrupted" }, { id: "e", status: "failed" }, { id: "f", status: "paused" }, { id: "g", status: "queued" },
      ];
      const none = Array.from({ length: 9 }, (_, i) => ({ id: "n" + i, status: i % 2 ? "completed" : "cancelled" }));
      const manyOpen = Array.from({ length: 12 }, (_, i) => ({ id: "w" + i, status: "waiting_human" }));
      const calls = [];
      let inFlight = 0, peak = 0;
      const tables = {
        "/gui/api/bundles": [{ name: "general" }, { name: "broken" }],
        "/gui/api/workflows": [{ id: "social_post", bundle: null }],
        "/gui/api/workflows?bundle=general": Array.from({ length: 9 }, (_, i) => ({ id: "w" + i, bundle: "general" })),
      };
      const get = async (path) => {
        calls.push(path); inFlight += 1; peak = Math.max(peak, inFlight);
        await new Promise((resolve) => setTimeout(resolve, 5));
        inFlight -= 1;
        if (path in tables) return tables[path];
        if (path.startsWith("/gui/api/workflows?bundle=broken")) throw new Error("bundle is gone");
        if (path.includes("/w3/inputs")) throw new Error("invalid YAML");
        if (path.includes("/w4/inputs")) return { description: "  Descripción de w4.  " };
        return { description: "" };
      };
      const updates = [];
      const entries = await m.loadCatalog({ get, onUpdate: (list) => updates.push(list.map((e) => e.id + (e.described ? "+" : "-")).join(",")), concurrency: 3 });
      let failure = "";
      try { await m.loadCatalog({ get: async () => { throw new Error("offline"); } }); } catch (error) { failure = error.message; }
      let allBroken = "";
      try { await m.loadCatalog({ get: async (path) => { if (path === "/gui/api/bundles") return []; throw new Error("no list"); } }); } catch (error) { allBroken = error.message; }
      console.log(JSON.stringify({
        picked: [m.pickRuns(runs), m.pickRuns(none), m.pickRuns(manyOpen).runs.length, m.pickRuns(null)],
        order: m.pickRuns(runs).runs.map((r) => r.id),
        ids: entries.map((e) => (e.bundle || "-") + "|" + e.id), w3: entries.find((e) => e.id === "w3"), w4: entries.find((e) => e.id === "w4"),
        described: entries.every((e) => e.described), peak, firstUpdate: updates[0], lastUpdate: updates[updates.length - 1], updates: updates.length,
        inputsCalls: calls.filter((c) => c.endsWith("/inputs") || c.includes("/inputs?")).length, bundleCalls: calls.filter((c) => c.includes("bundle=")).length,
        pathWithBundle: m.inputsPath({ id: "a b", bundle: "x y" }), pathBuiltIn: m.inputsPath({ id: "social_post", bundle: null }),
        failure, allBroken,
      }));
    """)
    assert out["order"] == ["c", "d", "f", "b", "g"]                              # a person is awaited first; finished ones are left out
    resumable, recent, many, empty = out["picked"]
    assert resumable["resumable"] is True and recent["resumable"] is False and len(recent["runs"]) == 5 and many == 8
    assert empty == {"resumable": False, "runs": []}
    assert out["ids"][0] == "-|social_post" and out["ids"][1:] == [f"general|w{i}" for i in range(9)]    # built-in first, a broken bundle tolerated
    assert out["w3"]["unreadable"] is True and out["w3"]["description"] == "" and out["w3"]["described"] is True    # stays in the catalog
    assert out["w4"]["description"] == "Descripción de w4." and out["w4"]["unreadable"] is False
    assert out["described"] is True and out["peak"] <= 3 + 1                    # a few requests at a time (the list calls are done before)
    assert out["firstUpdate"].count("-") == 10 and "+" not in out["firstUpdate"]  # names first...
    assert out["lastUpdate"].count("+") == 10 and out["updates"] == 11             # ...then one update per description
    assert out["inputsCalls"] == 10 and out["bundleCalls"] >= 2
    assert out["pathWithBundle"] == "/gui/api/workflows/a%20b/inputs?bundle=x%20y" and out["pathBuiltIn"] == "/gui/api/workflows/social_post/inputs"
    assert out["failure"] == "offline" and out["allBroken"] == "no list"


# --- the real API (no browser) ----------------------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True, max_llm_calls=4,
                        gemini_api_key="", openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"team": BUNDLE}, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, token=token, auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


@needs_node
@pytest.mark.parametrize("workflow_id,bundle", [("social_post", None), ("spec_review", "team")])
def test_what_the_chat_builds_is_accepted_by_the_real_create_run_api(env, workflow_id, bundle):
    params = {"bundle": bundle} if bundle else {}
    listing = env.client.get("/gui/api/workflows", params=params, headers=env.auth).json()
    assert workflow_id in {item["id"] for item in listing} and set(listing[0]) == {"id", "bundle"}   # no description there: the chat reads each one
    spec = env.client.get(f"/gui/api/workflows/{workflow_id}/inputs", params=params, headers=env.auth).json()
    assert {"description", "inputs"} <= set(spec)
    for item in spec["inputs"]:
        assert {"name", "label", "description", "example", "type", "choices", "required", "context", "source", "used_in", "prompt_line"} <= set(item)
    runs_row_keys = {"id", "workflow_id", "project_name", "status", "created_at", "bundle"}
    created = env.client.post("/gui/api/runs", headers=env.auth, json={"workflow_id": workflow_id, "bundle": bundle, "project_name": "Primera",
                                                                         "inputs": {i["name"]: "valor" for i in spec["inputs"] if i["required"]}})
    assert created.status_code == 202, created.text
    assert runs_row_keys <= set(env.client.get("/gui/api/runs?limit=5", headers=env.auth).json()[0])
    before = len(env.client.get("/gui/api/runs?limit=50", headers=env.auth).json())

    out = run_node("""
      let s = m.createConversation();
      s = m.reduce(s, { type: "pick", workflow: { id: DATA.id, bundle: DATA.bundle }, spec: DATA.spec });
      const asked = [];
      while (s.phase === "ask") {
        const q = m.currentQuestion(s);
        asked.push([q.key, q.required]);
        s = m.reduce(s, q.required ? { type: "send", text: q.source === "project" ? "Proyecto del chat" : "respuesta de " + q.key } : { type: "skip" });
      }
      console.log(JSON.stringify({ asked, phase: s.phase, body: m.runBody(s, { mode: "human_guided", sensitive: true }) }));
    """, {"id": workflow_id, "bundle": bundle, "spec": spec})
    assert out["phase"] == "confirm" and out["asked"][-1] == ["@project", True]
    context_only = {i["name"] for i in spec["inputs"] if i["context"] and not i["required"]}
    assert not context_only & {key for key, _ in out["asked"]}                      # optional extra context stays in the form
    body = out["body"]
    assert set(body["inputs"]) == {i["name"] for i in spec["inputs"]}              # every input travels, as in the form
    assert env.client.get("/gui/api/runs?limit=50", headers=env.auth).json().__len__() == before     # building the body created nothing

    preview = env.client.post("/gui/api/runs/preview", headers=env.auth, json={k: body[k] for k in ("workflow_id", "bundle", "inputs", "execution_mode", "sensitive")})
    assert preview.status_code == 200 and preview.json()["missing_inputs"] == []
    posted = env.client.post("/gui/api/runs", headers=env.auth, json={**body, "idempotency_key": "chat-1"})
    assert posted.status_code == 202, posted.text
    view = posted.json()
    assert view["workflow_id"] == workflow_id and view["bundle"] == bundle and view["project_name"] == "Proyecto del chat"
    assert view["execution_mode"] == "human_guided"
    assert env.client.post("/gui/api/runs", headers=env.auth, json={**body, "idempotency_key": "chat-1"}).json()["id"] == view["id"]   # a retry is the same run
    assert len(env.client.get("/gui/api/runs?limit=50", headers=env.auth).json()) == before + 1


def test_the_chat_files_are_served_with_the_csp(env):
    from multiagent.gui.security import CSP

    for path, kind in (("js/new-session.js", "javascript"), ("js/new-session-model.js", "javascript"), ("new-session.css", "css")):
        response = env.client.get(f"/gui/static/{path}")
        assert response.status_code == 200 and kind in response.headers["content-type"], path
        assert response.headers["content-security-policy"] == CSP
    page = env.client.get("/gui/")
    assert "/gui/static/new-session.css" in page.text
