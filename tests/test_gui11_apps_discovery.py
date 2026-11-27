# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EXA-T05: the card «Search for apps on this computer» of «Apps y agentes» and the rows that say «Not searched yet».

The pure models run in Node (skipped without it); the components are read as source. No server, no browser, no executable."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

APPS = STATIC_DIR / "extensions" / "apps"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def navigator(language: str) -> list[str]:
    return ["--import", f"data:text/javascript,Object.defineProperty(globalThis,'navigator',{{value:{{language:'{language}'}},configurable:true}})"]


def node(body: str, language: str = "en-US"):
    imports = "\n".join([
        f"import * as discovery from {json.dumps((APPS / 'discovery-model.js').as_uri())};",
        f"import * as model from {json.dumps((APPS / 'model.js').as_uri())};",
        f"import * as start from {json.dumps((STATIC_DIR / 'js' / 'start-model.js').as_uri())};",
        f"import dictionaries from {json.dumps((APPS / 'i18n.js').as_uri())};",
    ])
    result = subprocess.run([NODE, *navigator(language), "--input-type=module", "-e", imports + "\n" + body], capture_output=True,
                            text=True, encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


PRESETS = [
    {"app": "claude", "id": "claude_cli", "install": {"command": ["winget", "install", "Anthropic.ClaudeCode"], "source": "https://example.test/claude", "source_date": "2026-11-19"}},
    {"app": "codex", "id": "codex_cli", "install": {"command": ["npm", "install", "-g", "@openai/codex"], "source": "https://example.test/codex", "source_date": "2026-11-19"}},
    {"app": "copilot", "id": "copilot_cli", "install": {"command": ["winget", "install", "GitHub.Copilot"], "source": "https://example.test/copilot", "source_date": "2026-11-19"}},
    {"app": "agy", "id": "antigravity_cli", "install": None},
]
READS = {"claude": [["--version"], ["auth", "status"]], "codex": [["--version"], ["login", "status"]], "copilot": [["--version"]], "agy": [["--version"]]}


def discovery(**fields):
    base = {"answer": None, "at": None, "by": None, "allowed": False, "releases": ["version", "session_state"],
            "not_released": ["install", "sign_in", "activate", "credentials", "quota"], "reads": READS,
            "stored_in": "gui-settings.json", "presets": PRESETS}
    return {**base, **fields}


def unsearched_row(identifier, **fields):
    base = {"id": identifier, "kind": "executor", "installed": None, "version": None, "session": "unknown", "enabled": False,
            "available": False, "reason": "consent_required", "missing_step": None, "discovery": "consent_required"}
    return {**base, **fields}


# --- the card, in both modes -----------------------------------------------------------------------------------------------

@needs_node
def test_before_the_answer_the_card_offers_search_once_and_not_now():
    view = node(f"console.log(JSON.stringify(discovery.discoveryView({json.dumps(discovery())}, 'simple')));")
    assert view["state"] == "unanswered" and view["title"] == "Search for apps on this computer"
    assert [button["label"] for button in view["buttons"]] == ["Search once", "Not now"]
    assert [button["answer"] for button in view["buttons"]] == ["accepted", "declined"]
    assert "only which apps are installed, their version and whether they are signed in" in view["summary"]


@needs_node
def test_simple_mode_says_it_in_one_sentence_and_shows_no_command():
    view = node(f"console.log(JSON.stringify(discovery.discoveryView({json.dumps(discovery(answer='accepted', at='2026-11-26T20:00:00+00:00'))}, 'simple')));")
    assert view["lines"] == [] and view["presetsTitle"] is None
    assert all(preset["command"] is None for preset in view["presets"])
    assert "winget" not in json.dumps(view) and "npm" not in json.dumps(view)


@needs_node
def test_advanced_mode_adds_the_read_commands_and_where_the_answer_is_saved():
    view = node(f"console.log(JSON.stringify(discovery.discoveryView({json.dumps(discovery())}, 'advanced')));")
    reads = next(line for line in view["lines"] if line["kind"] == "reads")["commands"]
    assert reads == ["claude --version", "claude auth status", "codex --version", "codex login status", "copilot --version", "agy --version"]
    stored = next(line for line in view["lines"] if line["kind"] == "stored")["text"]
    assert "gui-settings.json" in stored and "measured again after a restart" in stored


@needs_node
def test_the_fixed_note_says_what_the_consent_does_not_release_in_every_state():
    for answer in (None, "accepted", "declined"):
        view = node(f"console.log(JSON.stringify(discovery.discoveryView({json.dumps(discovery(answer=answer))}, 'simple')));")
        assert view["note"] == "This does not install apps, sign in, activate any executor, read credentials or spend quota."


@needs_node
def test_after_accepting_the_card_can_withdraw_and_after_declining_it_can_search_again():
    accepted = node(f"console.log(JSON.stringify(discovery.discoveryView({json.dumps(discovery(answer='accepted'))}, 'advanced')));")
    assert [button["label"] for button in accepted["buttons"]] == ["Withdraw consent"] and accepted["buttons"][0]["answer"] == "declined"
    declined = node(f"console.log(JSON.stringify(discovery.discoveryView({json.dumps(discovery(answer='declined'))}, 'advanced')));")
    assert [button["label"] for button in declined["buttons"]] == ["Search once"]
    assert declined["summary"].startswith("Nothing is searched")


@needs_node
def test_the_presets_list_the_official_commands_and_agy_has_no_install():
    view = node(f"console.log(JSON.stringify(discovery.discoveryView({json.dumps(discovery(answer='accepted'))}, 'advanced')));")
    presets = {preset["app"]: preset for preset in view["presets"]}
    assert presets["codex"]["command"] == "npm install -g @openai/codex" and presets["codex"]["source"] == "https://example.test/codex"
    assert presets["codex"]["sourceDate"] == "2026-11-19" and presets["claude"]["command"] == "winget install Anthropic.ClaudeCode"
    assert presets["agy"]["installable"] is False and presets["agy"]["command"] is None and "no install command" in presets["agy"]["text"]


@needs_node
def test_the_answer_body_never_carries_anything_but_the_answer():
    bodies = node("console.log(JSON.stringify([discovery.answerBody('accepted'), discovery.answerBody('declined'), discovery.answerBody('anything else')]));")
    assert bodies == [{"answer": "accepted"}, {"answer": "declined"}, {"answer": "declined"}]


# --- the rows before the answer --------------------------------------------------------------------------------------------

@needs_node
def test_an_unsearched_card_says_not_searched_yet_and_offers_no_step():
    built = node(f"""
      const view = model.buildAppsView({{ connections: [{json.dumps(unsearched_row('claude_cli'))}] }}, null, "advanced", {{ executors: [{{ id: "claude_cli", state: "not_installed", consent_required: true, session: "unknown", paused: null, changes: [] }}] }});
      const item = view.executors[0];
      console.log(JSON.stringify({{ chip: item.chip, installed: item.installed, version: item.version, session: item.session, action: item.action,
        activation: item.activation, reason: item.reasonText, line: item.missingLine, open: item.defaultOpen }}));
    """)
    assert built["chip"] == {"status": "unsearched", "label": "Not searched yet"}
    assert built["installed"] == built["version"] == built["session"] == "Not searched yet"
    assert built["action"] is None and built["activation"] is None and built["reason"] is None and built["line"] is None
    assert built["open"] is False


@needs_node
def test_an_activated_executor_keeps_its_active_chip_without_a_search():
    built = node(f"""
      const row = {json.dumps(unsearched_row('claude_cli', reason=None, available=True, enabled=True))};
      const view = model.buildAppsView({{ connections: [row] }}, null, "advanced", {{ executors: [{{ id: "claude_cli", state: "active", session: "unknown", paused: null, changes: [], activated_at: "2026-11-20T10:00:00+00:00" }}] }});
      const item = view.executors[0];
      console.log(JSON.stringify({{ chip: item.chip.status, installed: item.installed, activation: item.activation && item.activation.state }}));
    """)
    assert built == {"chip": "active", "installed": "Not searched yet", "activation": "active"}


@needs_node
def test_after_the_search_the_cards_are_the_ones_of_today():
    built = node(f"""
      const rows = [{{ id: "codex_cli", kind: "executor", installed: false, available: false, reason: "not_installed", missing_step: "install", enabled: false, session: "unknown" }}];
      const view = model.buildAppsView({{ connections: rows }}, {{ plans: {{ codex_cli: {{ install: {{ command: ["npm", "install", "-g", "@openai/codex"], source: "https://example.test/codex", source_date: "2026-11-19" }} }} }} }}, "advanced", null);
      const item = view.executors[0];
      console.log(JSON.stringify({{ chip: item.chip.status, installed: item.installed, kind: item.action.kind, step: item.action.step, command: item.action.commandText }}));
    """)
    assert built == {"chip": "waiting", "installed": "No", "kind": "console", "step": "install", "command": "npm install -g @openai/codex"}


# --- «Inicio» only points at the card --------------------------------------------------------------------------------------

@needs_node
def test_home_says_apps_were_not_searched_and_never_asks_for_a_search_itself():
    rows = node("""
      const steps = [{ id: "connect", title: "Connect an agent", done: false, applicable: true },
                     { id: "activate", title: "Activate it", done: false, applicable: false }];
      const none = start.startRows({ steps, pending: ["connect"], discovery: null });
      const accepted = start.startRows({ steps, pending: ["connect"], discovery: "accepted" });
      const old = start.startRows({ steps, pending: ["connect"] });
      console.log(JSON.stringify({ none, accepted, old }));
    """)
    note = "Apps have not been searched for yet. Open Apps & agents and choose “Search once”."
    assert [row["note"] for row in rows["none"]] == [note, note]
    assert rows["none"][0]["actions"] == [{"kind": "link", "label": "Connect an agent", "href": "#apps"}]
    assert [row.get("note") for row in rows["accepted"]] == [None, "Not applicable: no CLI executor was detected on this computer."]
    assert [row.get("note") for row in rows["old"]] == [None, "Not applicable: no CLI executor was detected on this computer."]


def test_home_and_the_apps_screen_start_nothing_and_only_a_press_writes_the_answer():
    start = (STATIC_DIR / "js" / "start.js").read_text(encoding="utf-8")
    assert "connections" not in start and "discovery" not in start        # «Inicio» does not call or write it
    index = (APPS / "index.js").read_text(encoding="utf-8")
    card = (APPS / "discovery.js").read_text(encoding="utf-8")
    assert "apiPut(DISCOVERY_PATH" in card and card.count("onClick=${() => send(") == 3
    assert card.count("apiPut(") == 1 and "useEffect" not in card          # nothing writes it on its own
    assert 'apiGet(DISCOVERY_PATH' in index and "apiPut(" not in index.replace("apiPut } from", "")
    assert "apiPost(\"/gui/api/connections" not in index.split("function ToolCard")[0]     # no probe runs on opening the screen


# --- every install asks for its own confirmation ---------------------------------------------------------------------------

def test_each_install_has_its_own_confirmation_and_then_a_separate_sign_in_step():
    index = (APPS / "index.js").read_text(encoding="utf-8")
    console_step = index[index.index("function ConsoleStep"):index.index("function MissingStep")]
    assert 'setPhase("confirm")' in console_step and "data-help-exempt=\"confirm\" disabled=${phase === \"running\"} onClick=${launch}" in console_step
    assert 'apiPost("/gui/api/setup/console"' in console_step and console_step.count("apiPost(") == 1
    assert 'action.step === "install" ? t("ext.apps.install.button"' in console_step
    assert 't("ext.apps.install.sign_in_yourself")' in console_step
    assert "DISCOVERY_PATH" not in console_step                              # the search answer never stands for an install
    assert "showSignInAfterConsole(ranStep, result)" in console_step         # the line follows the launched step and the exit code
    assert "setRanStep(action.step)" in console_step.split("async function launch()")[1].split("try {")[0]
    assert "result && action.step" not in console_step


@needs_node
def test_the_sign_in_line_follows_the_launched_step_and_the_exit_code():
    cases = node('''console.log(JSON.stringify([
        model.showSignInAfterConsole("install", { console_exit_code: 0 }),
        model.showSignInAfterConsole("install", { console_exit_code: 0, recheck: { step: "login" } }),
        model.showSignInAfterConsole("install", { console_exit_code: 1 }),
        model.showSignInAfterConsole("login", { console_exit_code: 0 }),
        model.showSignInAfterConsole("install", null),
        model.showSignInAfterConsole(null, { console_exit_code: 0 }),
    ]))''')
    assert cases == [True, True, False, False, False, False]


def test_search_once_does_not_read_everything_again():
    index = (APPS / "index.js").read_text(encoding="utf-8")
    assert "onAnswered=${loadAll}" not in index and "onAnswered=${handleAnswered}" in index
    handler = index[index.index("async function handleAnswered"):index.index("function handleRowUpdated")]
    assert "saved.discovery" in handler and "saved.connections" in handler
    assert '"/gui/api/connections"' not in handler and "loadAll" not in handler
    for path in ("/gui/api/executors/activations", "/gui/api/setup/status", "/gui/api/composer/options"):
        assert f'apiGet("{path}"' in handler


# --- what stays --------------------------------------------------------------------------------------------------------------

def test_the_controls_of_today_are_still_there():
    index = (APPS / "index.js").read_text(encoding="utf-8")
    for key in ("ext.apps.refresh", "ext.apps.recheck", "ext.apps.activation.stop_all.button", "ext.apps.console.open",
                "ext.apps.activation.activate", "ext.apps.probe"):
        assert key in index, key
    assert 'href="#concurrency"' in index and "<${ActivationPanel}" in index and "<${AcpPanel}" in index and "<${StopAll}" in index


# --- texts in both languages ------------------------------------------------------------------------------------------------

@needs_node
def test_every_new_text_exists_in_english_and_spanish():
    dictionaries = node("console.log(JSON.stringify({ en: Object.keys(dictionaries.en), es: Object.keys(dictionaries.es) }));")
    new = [key for key in dictionaries["en"] if key.startswith("ext.apps.discovery.")] + [
        "ext.apps.chip.unsearched", "ext.apps.reason.consent_required", "ext.apps.install.button", "ext.apps.install.sign_in_yourself"]
    assert len([key for key in new if key.startswith("ext.apps.discovery.")]) >= 20
    for key in new:
        assert key in dictionaries["es"], key
    help_source = (APPS / "help.js").read_text(encoding="utf-8")
    for topic in ("apps.discovery", "apps.discovery_accept", "apps.discovery_decline", "apps.discovery_withdraw"):
        assert f'"{topic}"' in help_source and f"help.{topic}" in dictionaries["es"], topic


@needs_node
def test_the_spanish_card_and_the_home_note_read_in_spanish():
    view = node(f"console.log(JSON.stringify(discovery.discoveryView({json.dumps(discovery())}, 'simple')));", language="es-ES")
    assert view["title"] == "Buscar apps en este equipo" and [button["label"] for button in view["buttons"]] == ["Buscar una vez", "Ahora no"]
    assert view["note"] == "Esto no instala apps, no inicia sesión, no activa ningún ejecutor, no lee credenciales y no gasta cuota."
    rows = node("""
      console.log(JSON.stringify(start.startRows({ steps: [{ id: "connect", title: "Conectar", done: false, applicable: true }], pending: ["connect"], discovery: null })));
    """, language="es-ES")
    assert rows[0]["note"] == "Aún no se han buscado las apps. Abre «Apps y agentes» y elige «Buscar una vez»."
    built = node(f"""
      const view = model.buildAppsView({{ connections: [{json.dumps(unsearched_row('codex_cli'))}] }}, null, "simple");
      console.log(JSON.stringify(view.executors[0].chip));
    """, language="es-ES")
    assert built == {"status": "unsearched", "label": "Sin buscar aún"}


def test_no_text_of_the_card_is_injected_as_html_and_no_style_leaves_the_apps_prefix():
    card = (APPS / "discovery.js").read_text(encoding="utf-8")
    assert "innerHTML" not in card and "dangerouslySetInnerHTML" not in card
    css = (APPS / "style.css").read_text(encoding="utf-8")
    block = css[css.index("EXA-T05"):css.index("ACP-T04: the Codex ACP card")]
    assert re.findall(r"^([.#][\w-]+)", block, re.M) and all(name.startswith(".apps-") for name in re.findall(r"^(\.[\w-]+)", block, re.M))
