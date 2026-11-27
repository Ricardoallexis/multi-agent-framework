# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EXA-T06: «Add an agent» of «Apps y agentes» and the ACP thread that names the agent.

The pure models and the form run in Node (skipped without it); the components that need a browser are read as source. No server, no
browser, no executable: every program, path and name below is made up."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from multiagent.services import acp_agents as agents_module

APPS = STATIC_DIR / "extensions" / "apps"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

NOTE_EN = ("Adding an agent does not turn it on. Whoever wrote the program decides what it does. ACP is a protocol, not isolation. "
           "The framework only lets it read and write what the area allows.")
NOTE_ES = ("Añadir un agente no lo activa. Quien escribió el programa decide lo que hace. ACP no es un aislamiento. "
           "El framework solo le deja leer y escribir lo que el área permite.")
REMOVE_EN = ("Remove this agent? Its open conversations will close and its activation will be deleted. "
             "Saved history stays until you delete it.")
REMOVE_ES = ("¿Quitar este agente? Se cerrarán sus conversaciones abiertas y se borrará su activación. "
             "El historial guardado se queda hasta que lo borres.")
PAUSES_EN = "Changing the program or its arguments pauses the activation and clears the check."
PAUSES_ES = "Cambiar el programa o sus argumentos pausa la activación y borra la comprobación."
SCOPE_EN = "This agent serves conversations in the app only, not workflow steps."
SCOPE_ES = "Este agente sirve para conversaciones en la app, no para pasos de flujos."


def source(path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def node(body: str, language: str = "en"):
    base = lambda *parts: json.dumps(STATIC_DIR.joinpath(*parts).as_uri())   # noqa: E731
    script = "\n".join([
        f"const ui = await import({base('js', 'i18n.js')});",
        f"const model = await import({base('extensions', 'apps', 'add-agent-model.js')});",
        f"const component = await import({base('extensions', 'apps', 'add-agent.js')});",
        f"const activation = await import({base('extensions', 'apps', 'activation-model.js')});",
        f"const thread = await import({base('js', 'thread-model.js')});",
        f"const route = await import({base('js', 'route.js')});",
        f"ui.setLanguage({json.dumps(language)});",
        "globalThis.fetch = () => { throw new Error('nothing here may touch the network'); };",
        "function walk(node, out) {",
        "  if (node == null || typeof node === 'boolean') return;",
        "  if (Array.isArray(node)) { node.forEach((item) => walk(item, out)); return; }",
        "  if (typeof node === 'string' || typeof node === 'number') { out.text.push(String(node)); return; }",
        "  out.types.push(typeof node.type === 'function' ? node.type.name : node.type);",
        "  if (node.props) { if (node.props.href) out.hrefs.push(node.props.href); walk(node.props.children, out); }",
        "}",
        "const render = (vnode) => { const out = { text: [], types: [], hrefs: [] }; walk(vnode, out); return out; };",
        body,
    ])
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8",
                         cwd=STATIC_DIR, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def row(**fields):
    """The row GET /gui/api/acp-agents gives for a new agent (INFORME.md, section 2)."""
    base = {"id": "my-agent", "name": "My agent", "executable": "C:\\tools\\agent.exe", "args": ["--acp", "--stdio"],
            "state": "disabled", "installed": True, "probed": False, "probe": None, "probe_required": True,
            "activated_at": None, "activated_by": None, "paused": None, "changes": [], "scope": "acp_conversations",
            "valid": True, "reason": None}
    return {**base, **fields}


def view(language="en", **fields):
    return node(f"console.log(JSON.stringify(model.agentView({json.dumps(row(**fields))})));", language)


# --- the form only exists in advanced mode ----------------------------------------------------------------------------------

@needs_node
def test_simple_mode_has_a_line_and_a_link_but_no_form():
    for language, line, link in (("en", "Advanced option. Turn on advanced mode in Customize to add your own agent.", "Open Customize"),
                                 ("es", "Opción avanzada. Activa el modo avanzado en Personalizar para añadir tu propio agente.",
                                  "Abrir Personalizar")):
        out = node("console.log(JSON.stringify(render(component.AgentsSection({ mode: 'simple', token: 't' }))));", language)
        assert line in out["text"] and link in out["text"] and out["hrefs"] == ["#customize"]
        assert "AdvancedAgents" not in out["types"] and not {"form", "input", "textarea", "button"} & set(out["types"])


@needs_node
def test_advanced_mode_mounts_the_form_and_the_list():
    out = node("console.log(JSON.stringify(render(component.AgentsSection({ mode: 'advanced', token: 't' }))));")
    assert "AdvancedAgents" in out["types"] and out["hrefs"] == []
    source_text = source(APPS / "add-agent.js")
    advanced = source_text[source_text.index("function AdvancedAgents"):source_text.index("// In simple mode there is no form")]
    assert "<${AddAgentForm}" in advanced and "useAgents(token)" in advanced
    assert "const advanced = mode !== \"simple\";" in source_text   # the criterion every other view of Apps uses (index.js reads currentMode())
    assert "<${AgentsSection} mode=${mode} token=${token} />" in source(APPS / "index.js")


@needs_node
def test_the_form_asks_name_program_and_arguments_and_always_shows_the_fixed_note():
    for language, note, labels in (("en", NOTE_EN, ["Name", "Program (full path)", "Arguments (one per line)"]),
                                   ("es", NOTE_ES, ["Nombre", "Programa (ruta completa)", "Argumentos (uno por línea)"])):
        out = node("""
          console.log(JSON.stringify({ note: model.fixedNote(), exported: typeof component.AgentForm }));
        """, language)
        assert out["note"] == note
    # The note is drawn by the form itself, so it is under the fields of the add form and of the edit form, whatever their state.
    text = source(APPS / "add-agent.js")
    form = text[text.index("function AgentForm"):text.index("// «Start a conversation»")]
    assert "${fixedNote()}" in form and form.index("apps-agent-field") < form.index("${fixedNote()}")
    assert form.count('t("ext.apps.agents.field.') == 3
    for key in ("name", "program", "args"):
        assert f'ext.apps.agents.field.{key}' in form


@needs_node
def test_add_sends_name_program_and_arguments_and_nothing_else():
    out = node("""
      console.log(JSON.stringify({
        body: model.addBody({ name: '  My agent ', executable: ' C:\\\\tools\\\\agent.exe ', args: '--acp\\n--stdio\\n\\n\\n' }),
        crlf: model.parseArgs('a\\r\\nb\\r\\n'),
        middle: model.parseArgs('a\\n\\nb\\n'),
        none: model.parseArgs(''),
        can: [model.canSave({ name: '', executable: 'x' }), model.canSave({ name: 'a', executable: ' ' }), model.canSave({ name: 'a', executable: 'x' })],
      }));
    """)
    assert out["body"] == {"name": "My agent", "executable": "C:\\tools\\agent.exe", "args": ["--acp", "--stdio"]}
    assert out["crlf"] == ["a", "b"] and out["middle"] == ["a", "", "b"] and out["none"] == []   # an empty line in the middle is for the server to refuse
    assert out["can"] == [False, False, True]
    text = source(APPS / "add-agent.js")
    assert text.count("apiPost(AGENTS_PATH, token, addBody(form))") == 1 and "id:" not in text[text.index("function AddAgentForm"):]


# --- the list: states, buttons and the fixed lines --------------------------------------------------------------------------

@needs_node
def test_a_new_agent_reads_disabled_and_must_be_checked_before_it_can_be_activated():
    for language, label, scope, first in (("en", "Disabled", SCOPE_EN, "Check this agent before activating it."),
                                          ("es", "Deshabilitado", SCOPE_ES, "Comprueba este agente antes de activarlo.")):
        card = view(language)
        assert card["state"] == "disabled" and card["stateLabel"] == label and card["scopeLine"] == scope
        assert card["actions"] == ["check", "edit", "remove"] and card["canStart"] is False
        assert [line["text"] for line in card["lines"]] == [first]
    checked = view(probed=True, probe_required=False, probe={"status": "compatible", "protocol_version": 1,
                                                             "agent_info": {"name": "Made-up agent", "version": "0.1"}})
    assert checked["actions"] == ["check", "activate", "edit", "remove"] and checked["canStart"] is False and checked["lines"] == []
    assert {"id": "announces", "label": "Announces", "value": "Made-up agent 0.1"} in checked["facts"]
    assert {"id": "check", "label": "Last check", "value": "Compatible"} in checked["facts"]
    assert view(installed=False)["actions"] == ["edit", "remove"]   # the program is gone: nothing can be checked or activated


@needs_node
def test_only_an_active_valid_and_checked_agent_can_start_a_conversation():
    base = {"state": "active", "probed": True, "probe_required": False, "activated_at": "2026-11-27T09:00:00+00:00"}
    ok = view(**base)
    assert ok["canStart"] is True and ok["stateLabel"] == "Active" and ok["actions"] == ["check", "deactivate", "edit", "remove"]
    assert view(**{**base, "probed": False})["canStart"] is False
    assert view(**{**base, "probe_required": True})["canStart"] is False
    assert view(**{**base, "state": "disabled"})["canStart"] is False
    assert view(**{**base, "state": "paused"})["canStart"] is False
    assert view(**{**base, "valid": False, "reason": "executable_is_shell"})["canStart"] is False
    assert view(**{**base, "installed": False})["canStart"] is False


@needs_node
def test_an_invalid_entry_says_why_and_offers_no_check_activate_or_start():
    for language, expected in (
            ("en", "This agent's declaration is not valid: The path cannot contain wildcards. Edit or remove it."),
            ("es", "La declaración de este agente no es válida: La ruta no puede llevar comodines. Edítala o quítala.")):
        card = view(language, valid=False, reason="executable_has_wildcard", state="active", probed=True, probe_required=False)
        assert card["actions"] == ["edit", "remove"] and card["canStart"] is False and card["stateLabel"] in ("Not valid", "No válido")
        assert [line["text"] for line in card["lines"]] == [expected] and card["state"] == "invalid" and card["facts"] == []
    unknown = view(valid=False, reason="a_new_reason")
    assert "a_new_reason" in unknown["lines"][0]["text"] and unknown["actions"] == ["edit", "remove"]


@needs_node
def test_a_paused_agent_says_what_changed_including_the_file():
    paused = view(state="paused", paused={"reason": "changed"}, changes=[
        {"field": "args", "from": ["--a"], "to": ["--b"]}, {"field": "version", "from": "1.0", "to": "2.0"}, {"field": "path"}])
    text = paused["lines"][0]["text"]
    assert paused["stateLabel"] == "Paused: review it again" and paused["activateReview"] is True
    assert text == "Changed: the arguments · the version it announces, from 1.0 to 2.0 · The app path changed. Review it again."
    file_only = view(state="paused", paused={"reason": "changed", "changes": [{"field": "file", "from": {"size": 1}, "to": {"size": 2}}]})
    assert file_only["lines"][0]["text"] == "Changed: the program file was replaced or modified. Review it again."
    spanish = view("es", state="paused", paused={"reason": "changed"}, changes=[{"field": "file"}])
    assert spanish["stateLabel"] == "En pausa: revísalo de nuevo"
    assert spanish["lines"][0]["text"] == "Cambió: el archivo del programa se reemplazó o se modificó. Revísalo de nuevo."
    assert "activate" not in paused["actions"]   # its probe was cleared by the change: check first
    after_check = view(state="paused", probe_required=False, changes=[{"field": "args"}])
    assert "activate" in after_check["actions"]


@needs_node
def test_the_name_program_and_arguments_are_text_whatever_they_hold():
    hostile = "<img src=x onerror=alert(1)>"
    out = node(f"""
      const view = model.agentView({json.dumps(row(name=hostile, executable="C:\\tools\\<b>.exe", args=[hostile, "{task}"],
                                                  probe={"status": "compatible", "agent_info": {"name": hostile, "version": "<i>1</i>"}}))});
      console.log(JSON.stringify({{ view, rows: model.agentRows({{ agents: [{{ id: 'a', name: '{hostile}' }}, {{ name: 'no id' }}, null] }}).length }}));
    """)
    assert out["view"]["name"] == hostile and out["view"]["args"] == [hostile, "{task}"] and out["rows"] == 1
    assert {"id": "announces", "label": "Announces", "value": hostile + " <i>1</i>"} in out["view"]["facts"]
    for name in ("add-agent.js", "add-agent-model.js"):
        text = source(APPS / name)
        assert not re.search(r"innerHTML|dangerouslySetInnerHTML|insertAdjacentHTML|outerHTML|document\.write|eval\(|new Function", text), name
        assert "style=" not in text, name


@needs_node
def test_the_list_keeps_its_place_when_a_check_answers():
    out = node(f"""
      const list = model.agentRows({{ agents: [{json.dumps(row(id="first-agent", name="First"))}, {json.dumps(row(id="second-agent", name="Second"))}] }});
      const next = model.replaceAgent(list, {json.dumps(row(id="second-agent", name="Second", probed=True, probe_required=False))});
      console.log(JSON.stringify({{ ids: next.map((item) => item.id), actions: next.map((item) => item.actions) }}));
    """)
    assert out["ids"] == ["first-agent", "second-agent"]
    assert out["actions"][0] == ["check", "edit", "remove"] and out["actions"][1] == ["check", "activate", "edit", "remove"]


# --- nothing acts by itself ---------------------------------------------------------------------------------------------------

def effects(text: str) -> list[str]:
    """The bodies of every useEffect of a module."""
    return [text[found.start():text.index("}, [", found.start())] for found in re.finditer(r"useEffect\(", text)]


def test_opening_the_screen_only_reads_the_list():
    text = source(APPS / "add-agent.js")
    assert not re.search(r"setInterval|setTimeout|requestAnimationFrame", text)
    for body in effects(text):
        assert not re.search(r"apiPost|apiPut|probe\(|requestActivation|activateExecutor|deactivateExecutor|location\.", body), body[:80]
    assert text.count("apiGet(AGENTS_PATH, token)") == 1   # the one read of the list (and the areas of useAreas, when the person opens the picker)
    # A call leaves only from a function the person's press runs; each handler is bound to one button, and the start picker reads the
    # areas only once its panel is open.
    for call in ("apiPost(AGENTS_PATH", "apiPut(agentPath", 'apiPost(agentPath(agent.id, "remove")', "requestActivation(", "activateExecutor(",
                 "deactivateExecutor("):
        assert call in text
    assert "onClick=${probe.probe}" in text and "onClick=${openNote}" in text and "onClick=${turnOff}" in text
    assert "useAreas(token, true)" in text and 'panel === "start" && html`<${StartPicker}' in text
    # no card, in any state, is drawn with a probe, an activation or a conversation already running
    index = source(APPS / "index.js")
    assert "AgentsSection" in index and index.count("add-agent.js") == 1


def test_the_conversation_never_approves_and_activation_uses_todays_flow():
    text = source(APPS / "add-agent.js")
    assert "approvals/" not in text and "decision" not in text and "challenge" not in text   # approving stays in activation.js
    assert "import { activateExecutor, deactivateExecutor, requestActivation } from \"./activation.js\"" in text
    assert "executorId: agent.id" in text
    thread = source(JS / "thread.js")
    assert "approve" not in thread[thread.index("export function AcpThread"):]


@needs_node
def test_the_activation_notice_and_answers_say_the_name_of_an_own_agent():
    out = node("""
      const before = activation.executorName('my-agent');
      activation.rememberExecutorName('my-agent', ' My agent ');
      activation.rememberExecutorName('codex_acp', 'Impostor');
      activation.rememberExecutorName('', 'x');
      console.log(JSON.stringify({ before, after: activation.executorName('my-agent'), codex: activation.executorName('codex_acp'),
        note: activation.activationNote({ approval_id: 'a1', text: 'One line.\\nTwo.' }, 'my-agent').confirmLabel }));
    """)
    assert out == {"before": "my-agent", "after": "My agent", "codex": "Codex ACP", "note": "Activate My agent"}


def test_remove_asks_first_and_edit_says_what_saving_does():
    text = source(APPS / "add-agent.js")
    # the button of the card only opens the confirmation; the deletion is bound to the confirm button inside it
    assert 'onClick=${() => { setProblem(null); setMessage(null); setPanel("remove"); }}' in text
    confirm = text[text.index('panel === "remove" &&'):text.index('panel === "start" &&')]
    assert "removeConfirmText()" in confirm and "onClick=${remove}" in confirm and text.count("onClick=${remove}") == 1
    assert 'apiPost(agentPath(agent.id, "remove"), token, {})' in text


@needs_node
def test_removing_and_editing_texts_in_both_languages():
    for language, remove, pauses, name_only in (
            ("en", REMOVE_EN, PAUSES_EN, "Changing only the name keeps the activation and the check."),
            ("es", REMOVE_ES, PAUSES_ES, "Cambiar solo el nombre conserva la activación y la comprobación.")):
        out = node("""
          const agent = model.agentView(%s);
          const form = model.formOf(agent);
          console.log(JSON.stringify({
            remove: model.removeConfirmText(),
            unchanged: model.editNotice(agent, form),
            name: model.editNotice(agent, { ...form, name: 'Other name' }),
            program: model.editNotice(agent, { ...form, executable: 'C:\\\\tools\\\\other.exe' }),
            args: model.editNotice(agent, { ...form, args: '--acp' }),
            bodyName: model.editBody(agent, { ...form, name: 'Other name' }),
            bodyArgs: model.editBody(agent, { ...form, args: '--acp\\n\\n' }),
            bodyNone: model.editBody(agent, form),
          }));
        """ % json.dumps(row(state="active")), language)
        assert out["remove"] == remove
        assert out["unchanged"] == {"kind": "none", "text": ""}
        assert out["name"] == {"kind": "name_only", "text": name_only} and "paus" not in out["name"]["text"].lower()
        assert out["program"] == {"kind": "pauses", "text": pauses} and out["args"] == {"kind": "pauses", "text": pauses}
        assert out["bodyName"] == {"name": "Other name"} and out["bodyArgs"] == {"args": ["--acp"]} and out["bodyNone"] == {}


@needs_node
def test_the_edit_form_shows_the_warning_before_saving_and_the_fixed_note():
    out = node("""
      const agent = model.agentView(%s);
      const form = model.formOf(agent);
      const draw = (notice) => render(component.AgentForm({ scope: 'edit-my-agent', form, setForm() {}, busy: false, error: null,
        onSubmit() {}, onCancel() {}, editing: true, notice }));
      console.log(JSON.stringify({ pauses: draw(model.editNotice(agent, { ...form, args: '--x' })).text,
        name: draw(model.editNotice(agent, { ...form, name: 'N' })).text }));
    """ % json.dumps(row()))
    assert PAUSES_EN in out["pauses"] and NOTE_EN in out["pauses"] and "Save" in out["pauses"]
    assert PAUSES_EN not in out["name"] and NOTE_EN in out["name"]


# --- what the server refused ----------------------------------------------------------------------------------------------------

def server_reasons() -> set[str]:
    return set(re.findall(r'_reject\("([a-z_]+)"', source(__import__("pathlib").Path(agents_module.__file__))))


@needs_node
def test_every_rejection_of_the_server_has_its_text_in_both_languages():
    assert "executable_is_shell" in server_reasons() and "argument_relative_path" in server_reasons()
    spanish_only_check = {}
    for language in ("en", "es"):
        out = node("""
          const texts = {};
          for (const reason of model.REASONS) texts[reason] = model.failureText({ status: 422, details: { reason } });
          console.log(JSON.stringify(texts));
        """, language)
        spanish_only_check[language] = out
        for reason in server_reasons() - {"person_required"}:   # person_required cannot come from the GUI (the routes fix the actor)
            assert reason in out, f"no text for {reason}"
            assert out[reason] and "code:" not in out[reason] and out[reason] != reason
    assert all(spanish_only_check["es"][reason] != spanish_only_check["en"][reason] for reason in spanish_only_check["en"])


@needs_node
def test_rejection_texts_are_the_ones_of_the_report_and_say_the_argument():
    out = node("""
      console.log(JSON.stringify({
        absolute: model.failureText({ status: 422, details: { reason: 'executable_not_absolute' } }),
        shell: model.failureText({ status: 422, details: { reason: 'executable_is_shell' } }),
        relative: model.failureText({ status: 422, details: { reason: 'argument_relative_path', argument: 3 } }),
        shared: ['invalid_agent_id', 'agent_id_reserved', 'agent_exists'].map((reason) => model.failureText({ status: 409, details: { reason } })),
        plain: model.failureText({ status: 422, details: { reason: 'too_many_arguments' } }),
      }));
    """)
    assert out["absolute"] == "Use the full path of the program, for example C:\\tools\\agent.exe."
    assert out["shell"].startswith("A command interpreter (such as cmd, PowerShell, bash or sh) cannot be an agent")
    assert out["relative"] == "Argument 3: An argument is a relative path: use an absolute path."
    assert out["plain"] == "At most 16 arguments."
    assert len(set(out["shared"])) == 1 and out["shared"][0].startswith("That name gives an id that is invalid, reserved or already used.")
    spanish = node("""
      console.log(JSON.stringify(model.failureText({ status: 422, details: { reason: 'argument_has_shell_syntax', argument: 2 } })));
    """, "es")
    assert spanish == "Argumento 2: Un argumento tiene sintaxis de shell (; & |)."


@needs_node
def test_an_unknown_reason_is_a_generic_message_with_its_code_never_blank():
    out = node("""
      console.log(JSON.stringify([
        model.failureText({ status: 422, details: { reason: 'something_new' }, message: 'x' }),
        model.failureText({ status: 500, details: {}, message: '' }),
        model.failureText({ status: 422, details: { errors: [{ loc: ['body', 'name'] }] }, message: 'Invalid request' }),
        model.failureText({ status: 404, details: { reason: 'acp_agent_not_found' } }),
        model.failureText(null),
      ]));
    """)
    assert out[0] == "The server refused this (code: something_new)."
    assert out[1] == "The request failed." and out[2] == "Invalid request" and out[3] == "That agent no longer exists."
    assert all(item.strip() for item in out)
    spanish = node("console.log(JSON.stringify(model.failureText({ status: 422, details: { reason: 'something_new' } })));", "es")
    assert spanish == "El servidor lo rechazó (código: something_new)."


# --- the thread names the agent ----------------------------------------------------------------------------------------------------

@needs_node
def test_the_route_carries_only_the_agent_id_and_codex_routes_stay_as_they_were():
    out = node("""
      console.log(JSON.stringify({
        codex: route.parseRoute('#new/acp/abc123'), codexStart: route.parseRoute('#new/acp-new/office__area'),
        own: route.parseRoute('#new/acp/abc123/agent/my-agent'), ownStart: route.parseRoute('#new/acp-new/office__area/agent/my-agent'),
        hrefs: [route.acpThreadHref('abc123'), route.acpThreadHref('abc123', 'my-agent'), route.acpStartHref('office__area', 'my-agent'),
                route.acpStartHref('a b')],
      }));
    """)
    assert out["codex"] == {"section": "new", "id": "abc123", "kind": "acp", "agent": None}
    assert out["codexStart"] == {"section": "new", "id": "office__area", "kind": "acp-new", "agent": None}
    assert out["own"] == {"section": "new", "id": "abc123", "kind": "acp", "agent": "my-agent"}
    assert out["ownStart"] == {"section": "new", "id": "office__area", "kind": "acp-new", "agent": "my-agent"}
    assert out["hrefs"] == ["#new/acp/abc123", "#new/acp/abc123/agent/my-agent", "#new/acp-new/office__area/agent/my-agent", "#new/acp-new/a%20b"]
    session = source(JS / "new-session.js")
    assert 'bundle=${runId} agent=${route.agent || ""} />' in session and 'threadId=${runId} agent=${route.agent || ""} />' in session


@needs_node
def test_the_title_of_the_thread_is_the_name_the_server_gives_and_codex_keeps_its_text():
    for language, codex, named, generic, messages in (
            ("en", "Codex ACP chat", "Made-up agent chat", "ACP chat", "Made-up agent messages"),
            ("es", "Conversación con Codex ACP", "Conversación con Made-up agent", "Conversación ACP", "Mensajes de Made-up agent")):
        out = node("""
          const list = { agents: [{ id: 'my-agent', name: ' Made-up agent ' }, { id: 'other', name: 7 }] };
          const title = (who) => thread.acpText('thread.acp.title', who);
          console.log(JSON.stringify({
            codex: title(thread.acpWho('', null)), codexLoaded: title(thread.acpWho('', list)),
            named: title(thread.acpWho('my-agent', list)), loading: title(thread.acpWho('my-agent', null)),
            gone: title(thread.acpWho('removed-agent', list)), nameless: title(thread.acpWho('other', list)),
            aria: thread.acpText('thread.acp.messages.aria', thread.acpWho('my-agent', list)),
            codexAria: thread.acpText('thread.acp.messages.aria', thread.acpWho('', null)),
            running: [thread.acpText('thread.acp.running', thread.acpWho('', null)), thread.acpText('thread.acp.running', thread.acpWho('my-agent', list)),
                      thread.acpText('thread.acp.running', thread.acpWho('gone', list))],
            start: [thread.acpText('thread.acp.start.title', thread.acpWho('', null)), thread.acpText('thread.acp.start.title', thread.acpWho('my-agent', list)),
                    thread.acpText('thread.acp.start.title', thread.acpWho('gone', null))],
            hostile: thread.acpWho('x', { agents: [{ id: 'x', name: '<b>x</b>' }] }),
          }));
        """, language)
        assert out["codex"] == out["codexLoaded"] == codex
        assert out["named"] == named and out["aria"] == messages
        assert out["loading"] == out["gone"] == out["nameless"] == generic    # an id that is not in the list is never Codex, never the route's text
        assert out["codexAria"] == ("Codex ACP messages" if language == "en" else "Mensajes de Codex ACP")
        assert out["running"][0] == ("Codex ACP is working on this turn." if language == "en" else "Codex ACP está trabajando en este turno.")
        assert "Codex" not in out["running"][1] and "Codex" not in out["running"][2] and "Codex" not in out["start"][1] + out["start"][2]
        assert out["start"][0] == ("New Codex ACP conversation" if language == "en" else "Nueva conversación con Codex ACP")
        assert out["hostile"] == {"mode": "named", "name": "<b>x</b>"}   # a name is text: the template draws it as text
    text = source(JS / "thread.js")
    acp = text[text.index("export function AcpThread"):]
    assert "agents" in acp and "${agent}" not in acp and "agent}</" not in acp   # the route's id is never printed
    assert text.count("acpWho(agent, agents)") == 2 or text.count("acpWho(agent, agents)") == 1
    assert "ACP_AGENTS_PATH" in text


@needs_node
def test_the_start_of_a_conversation_sends_the_agent_and_its_reasons_name_nobody():
    for language in ("en", "es"):
        out = node("""
          const failure = (reason, status = 409, own = false) => thread.acpStartFailure({ status, details: { reason } }, own);
          const texts = {};
          for (const reason of ['acp_activation_required', 'executor_paused_changed', 'executor_stopped_by_person', 'acp_unavailable',
                                'acp_probe_required', 'acp_area_required', 'acp_session_limit']) {
            const codex = failure(reason), own = failure(reason, 409, true);
            texts[reason] = { codex: ui.t(codex.textKey), own: ui.t(own.textKey), codexKey: codex.textKey, ownKey: own.textKey };
          }
          console.log(JSON.stringify({
            texts,
            body: thread.acpStartBody('office__area', ' hello ', 'my-agent'), plain: thread.acpStartBody('office__area', 'hello'),
            empty: thread.acpStartBody('office__area', '  ', 'my-agent'),
            gone: thread.acpStartFailure({ status: 404, details: { reason: 'acp_agent_not_found' } }, true),
            gonePackage: thread.acpStartFailure({ status: 404, details: {} }, true),
          }));
        """, language)
        assert out["body"] == {"bundle": "office__area", "text": "hello", "agent": "my-agent"}
        assert out["plain"] == {"bundle": "office__area", "text": "hello"} and out["empty"] is None
        assert out["gone"]["textKey"] == "thread.acp.start.agent_not_found" and out["gonePackage"]["textKey"] == "thread.acp.start.not_found"
        for reason, item in out["texts"].items():
            if reason in ("acp_activation_required", "executor_paused_changed", "executor_stopped_by_person"):
                assert "Codex" in item["codex"] and "Codex" not in item["own"], reason     # Codex keeps its text; an own agent is never called Codex
                assert item["ownKey"].startswith("thread.acp.reason_agent.")
            else:
                assert item["codex"] == item["own"] and item["ownKey"] == item["codexKey"], reason


def test_the_existing_spanish_of_the_thread_did_not_change():
    es = source(STATIC_DIR / "i18n" / "es" / "thread.js")
    for line in ('"thread.acp.title": "Conversación con Codex ACP"', '"thread.acp.messages.aria": "Mensajes de Codex ACP"',
                 '"thread.acp.running": "Codex ACP está trabajando en este turno."', '"thread.acp.kind.message": "Codex"',
                 '"thread.acp.start.title": "Nueva conversación con Codex ACP"',
                 '"thread.acp.reason.acp_activation_required": "Codex ACP no está activado."',
                 '"thread.acp.reason.executor_paused_changed": "Codex ACP cambió y está en pausa: actívalo otra vez."',
                 '"thread.acp.reason.executor_stopped_by_person": "Codex ACP está en pausa porque apagaste todos los ejecutores."'):
        assert line in es, line
    en = source(STATIC_DIR / "i18n" / "en" / "thread.js")
    for line in ('"thread.acp.title": "Codex ACP chat"', '"thread.acp.reason.acp_activation_required": "Codex ACP is not activated."',
                 '"thread.acp.start.title": "New Codex ACP conversation"'):
        assert line in en, line


# --- the texts, in both languages ---------------------------------------------------------------------------------------------------

def dictionary_keys(path, language: str) -> dict[str, str]:
    text = source(path)
    if path.name == "i18n.js":
        text = re.search(r"^const " + language + r" = (\{.*?^\});$", text, re.S | re.M)[1]
    return dict(re.findall(r'^\s*"([^"]+)": ("(?:\\.|[^"\\])*"),?$', text, re.M))


def test_every_text_the_card_and_the_thread_use_exists_in_english_and_spanish():
    en, es = dictionary_keys(APPS / "i18n.js", "en"), dictionary_keys(APPS / "i18n.js", "es")
    used = set()
    for name in ("add-agent.js", "add-agent-model.js"):
        used |= {key for key in re.findall(r'\bt\("(ext\.apps\.[a-z_.]+)"', source(APPS / name)) if not key.endswith(".")}
    used |= {f"ext.apps.agents.reason.{key}" for key in ("executable_not_absolute", "executable_has_wildcard", "executable_has_shell_characters",
             "executable_is_shell_script", "executable_is_shell", "executable_not_found", "executable_in_writable_folder",
             "argument_has_placeholder", "argument_has_shell_syntax", "argument_invalid", "argument_in_writable_folder",
             "argument_relative_path", "too_many_arguments", "name_invalid", "agent_id", "too_many_agents")}
    used |= {f"ext.apps.agents.state.{key}" for key in ("disabled", "active", "paused", "invalid")}
    used |= {f"ext.apps.agents.chip.{key}" for key in ("disabled", "active", "paused", "invalid")}
    used |= {f"ext.apps.agents.check.{key}" for key in ("none", "compatible", "incompatible", "not_installed", "no_response", "invalid_response")}
    assert len(used) > 60
    for key in sorted(used):
        assert key in en and key in es, key
        assert json.loads(en[key]).strip() and json.loads(es[key]).strip(), key
    mine = [key for key in en if key.startswith("ext.apps.agents.")]
    assert sorted(mine) == sorted(key for key in es if key.startswith("ext.apps.agents."))
    assert set(mine) <= used, sorted(set(mine) - used)   # no text nobody uses
    same = [key for key in mine if en[key] == es[key]]
    assert same == ["ext.apps.agents.check.compatible"], same
    thread_en = dictionary_keys(STATIC_DIR / "i18n" / "en" / "thread.js", "en")
    thread_es = dictionary_keys(STATIC_DIR / "i18n" / "es" / "thread.js", "es")
    new = [key for key in thread_en if key.startswith("thread.acp.") and re.search(r"_(named|generic)\"?$|reason_agent|agent_not_found", key)]
    assert len(new) == 13 and set(new) == {key for key in thread_es if key.startswith("thread.acp.") and re.search(r"_(named|generic)$|reason_agent|agent_not_found", key)}
    for key in new:
        assert thread_en[key] != thread_es[key], key


def help_topics(text: str) -> set[str]:
    return set(re.findall(r'^\s*"(apps\.agent[a-z_]*)": \{', text, re.M))


def test_help_topics_for_the_new_controls_exist_in_both_languages_and_are_used():
    topics = {"apps.agents", "apps.agent_name", "apps.agent_program", "apps.agent_args", "apps.agent_add", "apps.agent_save",
              "apps.agent_check", "apps.agent_edit", "apps.agent_remove", "apps.agent_note", "apps.agent_start", "apps.agent_area"}
    assert help_topics(source(APPS / "help.js")) == topics
    spanish = source(APPS / "i18n.js")
    assert {f"apps.{name[len('help.apps.'):]}" for name in re.findall(r'"(help\.apps\.agent[a-z_]*)": \{', spanish)} == topics
    used = set(re.findall(r'topic="(apps\.agent[a-z_]*)"', source(APPS / "add-agent.js")))
    assert used == topics
    # the controls of today keep their own topics: activation, deactivation and the notice are reused, never copied
    text = source(APPS / "add-agent.js")
    assert 'helpFor("apps.activate"' in text and 'helpFor("apps.deactivate"' in text


# --- the styles and the Codex card ------------------------------------------------------------------------------------------------------

def test_the_styles_are_scoped_break_long_paths_and_use_the_shared_phone_cut():
    css = source(APPS / "style.css")
    new = css[css.index("EXA-T06"):]
    for selector in re.findall(r"^([.#@][^{]*?)\s*\{", new, re.M):
        assert selector.startswith((".apps-agent", "@media")), selector
    assert "@media (max-width: 599.98px)" in new and "forced-colors: active" in new
    assert re.search(r"\.apps-agent-program code,\s*\.apps-agent-args code \{[^}]*overflow-wrap: anywhere[^}]*word-break: break-all", new)
    assert re.search(r"\.apps-agent-field input,\s*\.apps-agent-field textarea \{[^}]*max-width: 100%[^}]*min-width: 0", new)
    phone = new[new.index("599.98px"):new.index("forced-colors")]
    assert "flex-basis: 100%" in phone and "width: 100%" in phone
    assert "overflow-x" not in new and not re.search(r"(?<![-\w])width:\s*\d+px", new) and not re.search(r"#[0-9a-fA-F]{3,8}\b", new)
    assert "style=" not in css
    assert not re.search(r"\.apps-agent[^{]*\{[^}]*(?:min-)?width:\s*\d+(?:px|rem)", new) or "min-width: 0" in new


def test_the_codex_card_and_the_rest_of_apps_stay_as_they_are():
    acp = source(APPS / "acp.js")
    assert "export function AcpPanel(" in acp and "path = ACP_PROBE_PATH, id = ACP_ID" in acp
    assert "useProbe({ token, onRowUpdated, onChanged })" in acp and "export function useAreas(" in acp
    index = source(APPS / "index.js")
    for kept in ("<${DiscoveryCard}", "<${StopAll}", "<${AcpPanel}", "<${ActivationPanel}", "import { AcpPanel } from \"./acp.js\"",
                 "executors/activations", "DISCOVERY_PATH"):
        assert kept in index, kept
    assert index.count("AgentsSection") == 2   # the import and the one use


# --- ronda 4: the label of the messages, the help phrases and the route with a literal slash ----------------------------------------

@needs_node
def test_route_keeps_the_session_when_the_agent_part_has_a_slash_or_is_empty():
    out = node("""
      console.log(JSON.stringify(['#new/acp/sid1/agent/a/b', '#new/acp/sid1/agent/', '#new/acp-new/pkg/agent/a/b', '#new/acp-new/pkg/agent/',
        '#new/acp/sid1/agent/my-agent', '#new/acp/sid1', '#new/acp-new/pkg', '#new/acp/sid1/agent/a%2Fb'].map((hash) => route.parseRoute(hash))));
    """)
    ids = [(item["id"], item["agent"]) for item in out]
    assert ids == [("sid1", "a/b"), ("sid1", None), ("pkg", "a/b"), ("pkg", None), ("sid1", "my-agent"), ("sid1", None), ("pkg", None),
                   ("sid1", "a/b")]
    assert [item["kind"] for item in out] == ["acp", "acp", "acp-new", "acp-new", "acp", "acp", "acp-new", "acp"]


def test_the_message_label_names_the_own_agent_and_codex_keeps_its_label():
    cards, thread = source(JS / "thread-cards.js"), source(JS / "thread.js")
    assert 'export function AcpCard({ item, agentLabel = "" })' in cards
    assert 'agentLabel && view.kind === "message" ? agentLabel : t("thread.acp.kind." + view.kind)' in cards
    assert "agentLabel=${agentLabel}" in thread
    assert 'who.mode === "named" ? who.name : who.mode === "generic" ? t("thread.acp.kind.message_generic") : ""' in thread   # Codex: empty
    en, es = source(STATIC_DIR / "i18n" / "en" / "thread.js"), source(STATIC_DIR / "i18n" / "es" / "thread.js")
    assert '"thread.acp.kind.message": "Codex"' in en and '"thread.acp.kind.message": "Codex"' in es
    assert '"thread.acp.kind.message_generic": "Agent"' in en and '"thread.acp.kind.message_generic": "Agente"' in es


def test_the_thread_help_phrases_are_true_for_codex_and_for_an_own_agent():
    runs_en, runs_es = source(JS / "help-text-runs.js"), source(STATIC_DIR / "i18n" / "es" / "runs.js")
    thread_topics = runs_en[runs_en.index('"acp_thread.section"'):runs_en.index('"acp_thread.cli"')]
    thread_topics_es = runs_es[runs_es.index('"help.acp_thread.section"'):runs_es.index('"help.acp_thread.cli"')]
    assert "Codex" not in thread_topics and "Codex" not in thread_topics_es
    for phrase in ("ACP agent conversation", "Message for the ACP agent", "ask the ACP agent.", "conversation with the ACP agent and send"):
        assert phrase in thread_topics, phrase
    for phrase in ("Conversación con el agente ACP", "Mensaje para el agente ACP", "pedirle al agente ACP.", "conversación en vivo con el agente ACP y envía"):
        assert phrase in thread_topics_es, phrase
    assert runs_en.count('"acp_thread.section"') == 1 and runs_en.count('"acp_thread.start"') == 1   # no duplicate topics per agent
