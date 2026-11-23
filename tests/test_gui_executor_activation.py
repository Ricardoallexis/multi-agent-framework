# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EJE-T02: «Activar» in «Apps y agentes»: the five states, the fixed note, the flow of calls and the texts.

The pure modules run in Node (skipped without it), with a fake ``post`` that notes the routes. Nothing here presses the
button of the note, starts a GUI or a browser, or calls a real CLI: ``activateExecutor`` only talks to the fake."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from multiagent.services.executor_activations import STATES as SERVER_STATES

APPS = STATIC_DIR / "extensions" / "apps"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def node(body: str):
    """Run ``body`` with the three modules as ``model``, ``act`` and ``flow``; the last console line is the JSON result."""
    head = (
        f'import * as model from {json.dumps((APPS / "activation-model.js").as_uri())};\n'
        f'import * as flow from {json.dumps((APPS / "activation.js").as_uri())};\n'
        f'import * as apps from {json.dumps((APPS / "model.js").as_uri())};\n'
    )
    run = subprocess.run([NODE, "--input-type=module", "-e", head + body], capture_output=True, text=True,
                         encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


# A fake ``post``: notes every route, answers from ``answers`` (a value, or an Error-like object to throw).
FAKE = """
const calls = [];
const fail = (status, code, details) => Object.assign(new Error(code), {status, code, details, message: code});
const makePost = (answers) => async (path, body) => {
  calls.push({path, body});
  const key = Object.keys(answers).find((k) => path.endsWith(k));
  const answer = key === undefined ? {} : answers[key];
  if (answer instanceof Error) throw answer;
  return answer;
};
const REQUEST = {approval_id: "ap1", sha256: "h1", note_id: "n", text: "Vas a activar Codex.\\nGasta tu cuota.", note: "x", fingerprint: {}};
const OK = {"/activation": REQUEST, "/challenge": {code: "c1", expires_in: 120, sha256: "h1"}, "/decision": {state: "approved"},
           "/activate": {id: "codex_cli", state: "active"}};
"""


@needs_node
def test_five_states():
    result = node("""
      const row = {id: "codex_cli"};
      const out = {};
      for (const state of model.STATES) out[state] = model.activationView(row, {id: "codex_cli", state, activated_at: "2026-11-22T03:03:00+00:00",
        paused: state === "paused" ? {reason: "changed", changes: [{field: "major", from: "2", to: "3"}], at: "x"} : null});
      console.log(JSON.stringify({states: model.STATES, labels: model.STATE_LABELS, out}));
    """)
    assert result["states"] == list(SERVER_STATES)       # the five states of the server, in its order
    assert result["labels"] == {"not_installed": "No instalado", "no_session": "Instalado, sin sesión",
                                "ready": "Listo para activar", "active": "Activo", "paused": "En pausa"}
    for state, view in result["out"].items():
        assert view["state"] == state and view["label"] == result["labels"][state]
    assert result["out"]["ready"]["actions"] == ["activate"]
    assert result["out"]["active"]["actions"] == ["deactivate"]
    assert result["out"]["paused"]["actions"] == ["review"]
    assert result["out"]["no_session"]["actions"] == ["login"]
    assert result["out"]["not_installed"]["actions"] == []
    assert result["out"]["active"]["detail"] == "Activado por ti el 2026-11-22 03:03"
    assert result["out"]["paused"]["detail"] == "Cambió la versión 2 → 3"


@needs_node
def test_chip_of_an_executor_card_is_its_state():
    result = node("""
      const payload = {connections: [{id: "codex_cli", available: true, installed: true},
                                     {id: "claude_cli", available: false, missing_step: "enable", installed: true}]};
      const activations = {executors: [{id: "codex_cli", state: "active", activated_at: "2026-11-22T03:03:00+00:00", paused: null},
                                       {id: "claude_cli", state: "ready", paused: null}]};
      const view = apps.buildAppsView(payload, null, "simple", activations);
      const plain = apps.buildAppsView(payload, null, "simple");
      console.log(JSON.stringify({chips: view.executors.map((e) => e.chip), stop: view.canStopAll,
        plain: plain.executors.map((e) => e.chip), plainStop: plain.canStopAll}));
    """)
    # the executors come in the fixed order claude_cli, codex_cli
    assert result["chips"] == [{"status": "ready", "label": "Listo para activar"}, {"status": "active", "label": "Activo"}]
    # without the answer of the server an executor enabled by hand stays «Listo»
    assert result["plain"] == [{"status": "waiting", "label": "Falta un paso"}, {"status": "ok", "label": "Listo"}]
    assert result["stop"] is True and result["plainStop"] is False


@needs_node
def test_activate_flow_order():
    result = node(FAKE + """
      const done = await flow.activateExecutor({token: "t", executorId: "codex_cli", post: makePost(OK)});
      console.log(JSON.stringify({done, calls}));
    """)
    assert result["done"]["ok"] is True
    assert [call["path"] for call in result["calls"]] == [
        "/gui/api/executors/codex_cli/activation",
        "/gui/api/approvals/ap1/challenge",
        "/gui/api/approvals/ap1/decision",
        "/gui/api/executors/codex_cli/activate",
    ]
    assert result["calls"][2]["body"] == {"code": "c1", "decision": "approve"}
    assert result["calls"][3]["body"] == {"approval_id": "ap1"}
    # the approvals session is taken when the page loads (js/auth.js); this flow never repeats it
    assert not any(call["path"].endswith("/approvals/session") for call in result["calls"])


@needs_node
def test_note_request_then_confirm_does_not_ask_twice():
    result = node(FAKE + """
      const first = await flow.requestActivation({executorId: "codex_cli", post: makePost(OK)});
      const afterNote = calls.length;
      const done = await flow.activateExecutor({token: "t", executorId: "codex_cli", post: makePost(OK), request: first.request});
      console.log(JSON.stringify({first: first.ok, afterNote, paths: calls.map((c) => c.path), done: done.ok}));
    """)
    assert result["first"] is True and result["afterNote"] == 1 and result["done"] is True
    assert result["paths"].count("/gui/api/executors/codex_cli/activation") == 1       # no second request after the note
    assert result["paths"][1:] == ["/gui/api/approvals/ap1/challenge", "/gui/api/approvals/ap1/decision",
                                   "/gui/api/executors/codex_cli/activate"]


@needs_node
def test_expired_code_is_retryable():
    result = node(FAKE + """
      const expired = fail(409, "invalid_state", {reason: "user_click_required", cause: "invalid_or_expired_code"});
      const done = await flow.activateExecutor({token: "t", executorId: "codex_cli", post: makePost({...OK, "/decision": expired})});
      console.log(JSON.stringify({done, paths: calls.map((c) => c.path)}));
    """)
    assert result["done"]["ok"] is False and result["done"]["retryable"] is True
    assert "venció" in result["done"]["message"] and "Activar" in result["done"]["message"]
    assert not any(path.endswith("/activate") for path in result["paths"])


@needs_node
def test_changed_application_between_note_and_click_starts_over():
    result = node(FAKE + """
      const changed = fail(409, "invalid_state", {reason: "executor_paused_changed"});
      const done = await flow.activateExecutor({token: "t", executorId: "codex_cli", post: makePost({...OK, "/activate": changed})});
      const session = fail(409, "invalid_state", {reason: "user_click_required", cause: "missing_or_invalid_session"});
      const noSession = await flow.activateExecutor({token: "t", executorId: "codex_cli", post: makePost({...OK, "/challenge": session})});
      const none = await flow.requestActivation({executorId: "codex_cli",
        post: makePost({"/activation": fail(409, "invalid_state", {reason: "not_installed"})})});
      console.log(JSON.stringify({done, noSession, none}));
    """)
    assert result["done"]["ok"] is False and result["done"]["restart"] is True and "Vuelve a empezar" in result["done"]["message"]
    assert result["noSession"]["ok"] is False and result["noSession"]["retryable"] is False
    assert "aprobaciones" in result["noSession"]["message"]
    assert result["none"]["ok"] is False and "no está instalado" in result["none"]["message"]


@needs_node
def test_note_from_server_as_text():
    result = node("""
      const note = model.activationNote({approval_id: "a", text: "Vas a activar <b>Codex</b>.\\nGasta tu cuota."}, "codex_cli");
      console.log(JSON.stringify(note));
    """)
    assert result["title"] == "Activar Codex" and result["confirmLabel"] == "Activar Codex"
    assert result["text"] == "Vas a activar <b>Codex</b>.\nGasta tu cuota."          # kept as the server sent it: text, not markup
    assert result["lines"] == ["Vas a activar <b>Codex</b>.", "Gasta tu cuota."]
    source = (APPS / "index.js").read_text(encoding="utf-8")
    for name in ("activation.js", "activation-model.js", "index.js"):
        text = (APPS / name).read_text(encoding="utf-8")
        assert "innerHTML" not in text and "dangerouslySetInnerHTML" not in text and "style=" not in text, name
    assert "note.lines.map" in source and "${note.title}" in source      # the note is drawn as text nodes


@needs_node
def test_agy_note_name_and_other_names():
    result = node("""
      console.log(JSON.stringify(["codex_cli", "claude_cli", "copilot_cli", "antigravity_cli"].map((id) => model.activationNote({text: "t"}, id).confirmLabel)));
    """)
    assert result == ["Activar Codex", "Activar Claude", "Activar Copilot", "Activar Antigravity (agy)"]


@needs_node
def test_no_enable_lines_for_executors():
    result = node("""
      const status = {plans: {}};
      const rows = [{id: "codex_cli", available: false, missing_step: "enable"},
                    {id: "antigravity_cli", available: false, missing_step: "enable", notice_accepted: false}];
      const out = {};
      for (const mode of ["simple", "advanced"]) out[mode] = rows.map((row) => apps.missingAction(row, "executor", status, mode));
      console.log(JSON.stringify(out));
    """)
    for mode in ("simple", "advanced"):
        for action in result[mode]:
            assert action["kind"] == "enable_executor" and action["title"] == "Listo para activar"
            for key in ("catalogLines", "copyText", "envLines", "envFile", "catalogFile"):
                assert key not in action, (mode, key)
    assert result["advanced"][0]["manualText"] == "También puedes encenderlo a mano en catalog/models.yaml"
    assert "manualText" not in result["simple"][0]
    # the models keep what SIM-T03 gave them
    model_result = node("""
      console.log(JSON.stringify(apps.missingAction({id: "openai", available: false, missing_step: "enable"}, "model", {}, "advanced")));
    """)
    assert model_result["copyText"] == "OPENAI_ENABLED=true"


@needs_node
def test_paused_changes_text():
    result = node("""
      console.log(JSON.stringify([
        model.changesText([{field: "major", from: "2", to: "3"}]),
        model.changesText([{field: "path", from: "a", to: "b"}]),
        model.changesText([{field: "account", from: "a@x", to: "b@x"}, {field: "major", from: "1", to: "2"}]),
        model.changesText([]), model.changesText(null),
        model.activationView({id: "codex_cli"}, {state: "paused", paused: {reason: "stopped_by_person", changes: [], at: "x"}, changes: []}).detail,
      ]));
    """)
    assert result[0] == "Cambió la versión 2 → 3"
    assert result[1] == "Cambió la ruta de la aplicación"
    assert result[2] == "Cambió la cuenta · Cambió la versión 1 → 2"
    assert result[3] == "" and result[4] == ""
    assert result[5] == 'En pausa: la apagaste con "Apagar todos"'


@needs_node
def test_stop_all_confirms():
    result = node(FAKE + """
      const answers = {"/stop-all": {stopped_tasks: ["t1"], paused: ["codex_cli"], kill_switch: "written"}};
      const asked = [];
      const no = await flow.stopAllExecutors({token: "t", post: makePost(answers), confirm: async (text) => { asked.push(text); return false; }});
      const noCallback = await flow.stopAllExecutors({token: "t", post: makePost(answers)});
      const callsBefore = calls.length;
      const yes = await flow.stopAllExecutors({token: "t", post: makePost(answers), confirm: async (text) => { asked.push(text); return true; }});
      console.log(JSON.stringify({no, noCallback, callsBefore, yes, asked, calls, confirm: model.STOP_ALL_CONFIRM}));
    """)
    assert result["confirm"] == "Detiene lo que esté corriendo y pone todos en pausa"
    assert result["asked"] == [result["confirm"]] * 2
    assert result["no"]["cancelled"] is True and result["noCallback"]["cancelled"] is True
    assert result["callsBefore"] == 0                                              # nothing reached the route without a yes
    assert result["yes"]["ok"] is True and "1 tarea" in result["yes"]["message"]
    assert [call["path"] for call in result["calls"]] == ["/gui/api/executors/stop-all"]


@needs_node
def test_stop_all_text_covers_the_kill_switch_answers():
    result = node("""
      const base = {stopped_tasks: [], paused: ["codex_cli", "claude_cli"]};
      console.log(JSON.stringify(["written", "foreign", "failed"].map((k) => model.stopAllText({...base, kill_switch: k}))));
    """)
    assert "2 ejecutores quedaron en pausa" in result[0] and "agy" not in result[0]
    assert "no lo puso la app" in result[1] and "No se pudo escribir" in result[2]


@needs_node
def test_deactivate_is_one_call_without_a_note():
    result = node(FAKE + """
      const done = await flow.deactivateExecutor({token: "t", executorId: "claude_cli", post: makePost({"/deactivate": {id: "claude_cli", state: "ready"}})});
      const again = await flow.deactivateExecutor({token: "t", executorId: "claude_cli",
        post: makePost({"/deactivate": fail(409, "invalid_state", {reason: "not_active"})})});
      console.log(JSON.stringify({done, again, paths: calls.map((c) => c.path)}));
    """)
    assert result["paths"] == ["/gui/api/executors/claude_cli/deactivate", "/gui/api/executors/claude_cli/deactivate"]
    assert result["done"]["ok"] is True and result["again"]["ok"] is False and result["again"]["refresh"] is True


@needs_node
def test_foreign_kill_switch_line():
    result = node("""
      const row = {id: "antigravity_cli"};
      const ready = model.activationView(row, {id: "antigravity_cli", state: "ready", paused: null, kill_switch_foreign: true});
      const paused = model.activationView(row, {id: "antigravity_cli", state: "paused", kill_switch_foreign: true,
        paused: {reason: "changed", changes: [{field: "major", from: "1", to: "2"}], at: "x"}});
      const normal = model.activationView(row, {id: "antigravity_cli", state: "ready", paused: null, kill_switch_foreign: false});
      console.log(JSON.stringify({ready, paused, normal}));
    """)
    line = "agy está apagado por un archivo que no puso la app; quítalo tú para activarlo"
    assert result["ready"]["detail"] == line and result["ready"]["actions"] == []
    assert result["paused"]["actions"] == [] and result["paused"]["detail"].endswith(line)
    assert result["normal"]["actions"] == ["activate"]


def test_activation_does_not_import_approvals_extension():
    for name in ("activation.js", "activation-model.js"):
        source = (APPS / name).read_text(encoding="utf-8")
        imports = re.findall(r'^\s*import\b[^;]*?from\s+"([^"]+)"', source, re.M)
        assert all("approvals" not in path for path in imports), (name, imports)
        assert "extensions/approvals" not in source.replace("// ", "").split("export", 1)[1], name
    # it calls the routes of the approvals by its own paths
    flow = (APPS / "activation.js").read_text(encoding="utf-8")
    assert "/approvals/" in flow and "challenge" in flow and "decision" in flow


def test_no_route_that_activates_outside_the_gui():
    """Rule 1: nothing but the browser routes can activate. The extension only calls ``/gui/api``."""
    flow = (APPS / "activation.js").read_text(encoding="utf-8")
    assert "/api/v1" not in flow and "const GUI = \"/gui/api\"" in flow
