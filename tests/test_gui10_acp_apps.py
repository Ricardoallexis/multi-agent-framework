# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ACP-T04: the Codex ACP card of «Apps y agentes» (the five facts, the step that is missing, the install panel, the area consent
and the start of a conversation).

The pure model runs in Node (skipped without it); the components are read as source. No server, no browser, nothing real runs."""
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
        f"import * as acp from {json.dumps((APPS / 'acp-model.js').as_uri())};",
        f"import * as model from {json.dumps((APPS / 'model.js').as_uri())};",
        f"import * as activation from {json.dumps((APPS / 'activation-model.js').as_uri())};",
        f"import dictionaries from {json.dumps((APPS / 'i18n.js').as_uri())};",
    ])
    result = subprocess.run([NODE, *navigator(language), "--input-type=module", "-e", imports + "\n" + body], capture_output=True,
                            text=True, encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


INSTALL = {"package": "@agentclientprotocol/codex-acp", "version": "2.12.0", "license": "Apache-2.0",
           "command": "npm install -g @agentclientprotocol/codex-acp@2.12.0",
           "source": "https://www.npmjs.com/package/@agentclientprotocol/codex-acp"}


def row(**fields):
    base = {"id": "codex_acp", "kind": "executor", "installed": True, "compatible": True, "authenticated": None, "activated": False,
            "selected": False, "selected_transport": "cli", "protocol_version": None, "version": None, "available": False,
            "reason": "disabled", "missing_step": "enable", "install": INSTALL, "enabled": False}
    return {**base, **fields}


# --- the five facts, each apart ----------------------------------------------------------------------------------------

@needs_node
def test_each_fact_is_shown_apart_and_authenticated_null_is_no_data_yet():
    facts = node(f"""console.log(JSON.stringify(acp.acpFacts({json.dumps(row(compatible=False))}).map((fact) => [fact.id, fact.value])));""")
    assert facts == [["installed", "Yes"], ["compatible", "No"], ["authenticated", "No data yet"], ["activated", "No"], ["transport", "CLI"]]
    full = node(f"""console.log(JSON.stringify(acp.acpFacts({json.dumps(row(authenticated=True, activated=True, version="2.12.0", protocol_version=1))})));""")
    assert [fact["id"] for fact in full] == ["installed", "compatible", "authenticated", "activated", "transport", "version", "protocol"]
    assert {fact["id"]: fact["value"] for fact in full} == {"installed": "Yes", "compatible": "Yes", "authenticated": "Yes", "activated": "Yes",
                                                             "transport": "CLI", "version": "2.12.0", "protocol": "1"}


@needs_node
def test_version_and_protocol_show_only_when_the_server_still_holds_them():
    shown = node(f"""console.log(JSON.stringify(acp.acpFacts({json.dumps(row(version=None, protocol_version=None))}).map((fact) => fact.id)));""")
    assert "version" not in shown and "protocol" not in shown


@needs_node
def test_the_card_draws_the_facts_in_place_of_the_generic_rows_and_does_not_keep_a_parallel_state():
    index = (APPS / "index.js").read_text(encoding="utf-8")
    assert "item.acp.facts.map" in index and "<${AcpPanel}" in index
    model = (APPS / "model.js").read_text(encoding="utf-8")
    assert '"codex_acp"' in model and "buildEntry(row" in model        # the same card builder, not a second one
    built = node(f"""
      const view = model.buildAppsView({{ connections: [{json.dumps(row())}] }}, null, "advanced", null);
      console.log(JSON.stringify({{ ids: view.executors.map((item) => item.id), acp: view.executors[0].acp.facts.length, label: view.executors[0].label }}));
    """)
    assert built == {"ids": ["codex_acp"], "acp": 5, "label": "Codex ACP"}


# --- the step that is missing rules ------------------------------------------------------------------------------------

@needs_node
@pytest.mark.parametrize("fields, activation, step, kind", [
    ({"installed": False, "compatible": False, "missing_step": "install", "reason": "not_installed"}, None, "install", "acp_install"),
    ({"compatible": False, "missing_step": "probe", "reason": "acp_probe_required"}, None, "probe", "acp_probe"),
    ({}, None, "enable", "enable_executor"),
    ({"activated": True, "enabled": True, "available": True, "reason": None, "missing_step": None}, None, None, None),
])
def test_the_missing_step_decides_what_the_card_offers(fields, activation, step, kind):
    result = node(f"""
      const source = {json.dumps(row(**fields))};
      const entry = model.buildAppsView({{ connections: [source] }}, null, "advanced", {json.dumps(activation)}).executors[0];
      console.log(JSON.stringify({{ step: entry.acp.step, kind: entry.action ? entry.action.kind : null }}));
    """)
    assert result == {"step": step, "kind": kind}


@needs_node
def test_a_stale_probe_of_an_activated_adapter_asks_for_the_probe_through_the_activations_row():
    result = node(f"""
      console.log(JSON.stringify([acp.acpStep({json.dumps(row(activated=True, missing_step=None))}, {{ probe_required: true }}),
        acp.acpStep({json.dumps(row(activated=True, missing_step=None))}, {{ probe_required: false }})]));
    """)
    assert result == ["probe", None]


@needs_node
def test_activate_waits_for_the_probe_and_comes_back_after_it():
    result = node("""
      const waiting = activation.activationView({ id: "codex_acp" }, { state: "ready", probe_required: true });
      const ready = activation.activationView({ id: "codex_acp" }, { state: "ready", probe_required: false });
      console.log(JSON.stringify({ waiting: waiting.actions, ready: ready.actions, name: activation.executorName("codex_acp") }));
    """)
    assert result == {"waiting": [], "ready": ["activate"], "name": "Codex ACP"}


@needs_node
def test_an_adapter_waiting_for_the_probe_is_not_shown_as_ready_to_activate():
    chip = node(f"""
      const source = {json.dumps(row(compatible=False, missing_step="probe", reason="acp_probe_required"))};
      const entry = model.buildAppsView({{ connections: [source] }}, null, "advanced",
        {{ executors: [{{ id: "codex_acp", state: "ready", probe_required: true }}] }}).executors[0];
      console.log(JSON.stringify(entry.chip.status));
    """)
    assert chip == "waiting"


# --- the install panel: it shows, it never runs ------------------------------------------------------------------------

@needs_node
def test_install_panel_has_command_version_license_and_the_source_as_a_plain_link():
    panel = node(f"console.log(JSON.stringify(acp.installPanel({json.dumps(INSTALL)})));")
    assert panel == {"command": INSTALL["command"], "version": "2.12.0", "license": "Apache-2.0", "source": INSTALL["source"],
                     "sourceHref": INSTALL["source"]}
    unsafe = node("console.log(JSON.stringify(acp.installPanel({ command: 'x', source: 'javascript:alert(1)' })));")
    assert unsafe["sourceHref"] == ""                       # only an https address becomes a link
    assert node("console.log(JSON.stringify(acp.installPanel({})));") is None


def test_install_panel_never_runs_the_command_and_checks_with_the_probe_route():
    source = (APPS / "acp.js").read_text(encoding="utf-8")
    model = (APPS / "acp-model.js").read_text(encoding="utf-8")
    for text in (source, model):
        assert not re.search(r"child_process|\bexec\(|spawn\(|\beval\(|new Function|innerHTML|dangerouslySetInnerHTML", text)
    assert "/setup/console" not in source and "apiPost(ACP_PROBE_PATH, token, {})" in source          # «Comprobar otra vez» is the probe, with {}
    assert 'ACP_PROBE_PATH = `/gui/api/connections/${ACP_ID}/acp-probe`' in model
    assert "navigator.clipboard.writeText(install.command)" in source                                 # copying is the person's act
    assert "ext.apps.acp.install.you_run_it" in source
    assert '<a href=${install.sourceHref}>${install.source}</a>' in source and "_blank" not in source      # an ordinary link
    # the panel opens only from its button and the install button needs no server call
    assert 'onClick=${() => setOpen(true)}' in source


@needs_node
def test_the_install_step_is_advanced_only_in_simple_mode_because_it_holds_a_command_to_copy():
    result = node(f"""
      const source = {json.dumps(row(installed=False, compatible=False, missing_step="install", reason="not_installed"))};
      const simple = model.buildAppsView({{ connections: [source] }}, null, "simple", null).executors[0];
      console.log(JSON.stringify([simple.action.kind, simple.action.text.includes("advanced mode")]));
    """)
    assert result == ["advanced_only", True]


# --- the consent of the area -------------------------------------------------------------------------------------------

@needs_node
def test_consent_is_read_by_bundle_and_saved_without_a_scope():
    result = node("""
      console.log(JSON.stringify({ read: acp.consentReadPath("office__area one"), body: acp.consentBody("office__area", true),
        off: acp.consentBody("office__area", undefined), note: acp.consentNote() }));
    """)
    assert result["read"] == "/gui/api/connections/codex_acp/consent?bundle=office__area%20one"
    assert result["body"] == {"bundle": "office__area", "allow_write": True} and "scope" not in result["body"]
    assert result["off"] == {"bundle": "office__area", "allow_write": False}
    assert result["note"] == "Consent does not activate executors, allow paid usage, or expand the working folder."
    source = (APPS / "acp.js").read_text(encoding="utf-8")
    assert "apiPut(ACP_CONSENT_PATH, token, consentBody(bundle, allowWrite))" in source
    assert not re.search(r"api(?:Put|Post)\([^)]*scope", source)                # the server finds the scope from the package


def test_consent_is_offered_once_and_only_for_an_activated_adapter():
    source = (APPS / "acp.js").read_text(encoding="utf-8")
    assert "${acp.activated && html`<${AreaSection}" in source                  # not activated: no area, no consent, no start
    assert source.count("ext.apps.acp.consent.configure") == 1                   # one button, one place
    assert "consent.configured ? t(\"ext.apps.acp.consent.change\") : t(\"ext.apps.acp.consent.configure\")" in source
    assert 'role="switch"' in source and "ext.apps.acp.consent.allow_write" in source
    # reading the consent happens when the area is chosen, never on every message
    assert "consentReadPath(bundle)" in source and source.count("apiGet(consentReadPath") == 1


@needs_node
def test_consent_failures_are_text_and_the_card_survives_them():
    result = node("""
      console.log(JSON.stringify([acp.consentFailureText({ status: 404 }),
        acp.consentFailureText({ status: 409, details: { reason: "acp_area_required" } }),
        acp.consentFailureText({ status: 500, message: "boom" }), acp.consentFailureText(null),
        acp.consentStateText(acp.consentView({ configured: false })), acp.consentStateText(acp.consentView({ configured: true, allow_write: true })),
        acp.consentStateText(acp.consentView({ configured: true, allow_write: false }))]));
    """)
    assert result[:4] == ["That package no longer exists.", "The server cannot confirm an office area for that package.", "boom",
                          "Could not read or save the consent."]
    assert result[4].startswith("Not configured") and "also write" in result[5] and "not write" in result[6]


@needs_node
def test_the_area_selector_uses_the_offices_the_gui_already_reads():
    source = (APPS / "acp.js").read_text(encoding="utf-8")
    assert 'apiGet("/gui/api/offices", token)' in source and '"/gui/api/offices/" + encodeURIComponent(office.id)' in source
    groups = node("""
      console.log(JSON.stringify(acp.officeChoices([
        { id: "o1", name: "Office", areas: [{ id: "a", name: "Area A", bundle: "o1__a" }, { id: "b" }] }, { id: "o2", areas: [] }, null ])));
    """)
    assert groups == [{"id": "o1", "name": "Office", "areas": [{"bundle": "o1__a", "name": "Area A"}]}]


# --- starting a conversation -------------------------------------------------------------------------------------------

@needs_node
def test_a_conversation_needs_an_activated_compatible_adapter_a_package_and_text():
    result = node(f"""
      const can = (fields, activation) => acp.acpView({json.dumps(row())}, activation).canStart;
      const view = (fields) => acp.acpView(Object.assign({json.dumps(row(activated=True, compatible=True, missing_step=None))}, fields), null);
      console.log(JSON.stringify({{ off: can(), on: view({{}}).canStart, notCompatible: view({{ compatible: false, missing_step: "probe" }}).canStart,
        notReady: acp.notReadyFailure(false).reason, probeFirst: acp.notReadyFailure(true).reason }}));
    """)
    assert result == {"off": False, "on": True, "notCompatible": False,
                      "notReady": "acp_unavailable", "probeFirst": "acp_probe_required"}


@needs_node
@pytest.mark.parametrize("reason", ["acp_unavailable", "acp_activation_required", "acp_probe_required", "acp_area_required",
                                    "acp_session_limit", "executor_paused_changed", "executor_stopped_by_person"])
def test_a_409_reads_acp_unavailable_with_its_reason_and_keeps_cli_as_an_option(reason):
    result = node(f"""console.log(JSON.stringify(acp.startFailure({{ status: 409, details: {{ reason: {json.dumps(reason)} }} }})));""")
    assert result["unavailable"] is True and result["title"] == "ACP unavailable" and result["reason"] == reason and result["text"]
    spanish = node(f"""console.log(JSON.stringify(acp.startFailure({{ status: 409, details: {{ reason: {json.dumps(reason)} }} }})));""", "es-MX")
    assert spanish["title"] == "ACP no disponible" and spanish["text"] != result["text"]


@needs_node
def test_other_failures_are_plain_text_without_the_unavailable_title():
    result = node("""console.log(JSON.stringify([acp.startFailure({ status: 500, message: "boom" }), acp.startFailure(null), acp.startFailure({ status: 404 })]));""")
    assert result[0] == {"unavailable": False, "reason": "failed", "title": "", "text": "boom"}
    assert result[1]["text"] == "Could not start the conversation." and result[2]["reason"] == "not_found"


def test_apps_only_navigates_to_the_start_and_never_posts_or_switches_transport():
    source = (APPS / "acp.js").read_text(encoding="utf-8")
    start = source[source.index("function StartLink"):source.index("// The area is chosen once")]
    assert "apiPost(" not in start and "location" not in start and "<textarea" not in start       # the first message is not written here
    assert "href=${acpStartHref(bundle)}" in start and "ext.apps.acp.start.button" in start
    assert "ACP_START_PATH" not in source and "/gui/api/threads/acp" not in source
    assert "/gui/api/runs" not in source and "threads/runs" not in source and "/setup/console" not in source
    # not ready: the same notice as a 409, with CLI as a visible choice; nothing starts by itself
    assert "notReadyFailure(probeNeeded)" in start and 'href=${NEW_SESSION_HREF}' in start and "ext.apps.acp.choose_cli" in start
    assert 'NEW_SESSION_HREF = "#new"' in (APPS / "acp-model.js").read_text(encoding="utf-8")


def test_the_apps_extension_has_no_input_or_textarea_at_all():
    for path in APPS.glob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert "<input" not in text and "<textarea" not in text, path.name


def test_the_consent_is_a_switch_button_with_its_visible_text():
    source = (APPS / "acp.js").read_text(encoding="utf-8")
    block = source[source.index('type="button" class="apps-acp-switch"'):source.index('<${Help} topic="apps.acp_write"')]
    assert 'role="switch"' in block and 'aria-checked=${allowWrite ? "true" : "false"}' in block and 'type="button"' in block
    assert "setAllowWrite(!allowWrite)" in block and "ext.apps.acp.consent.allow_write" in block
    css = (APPS / "style.css").read_text(encoding="utf-8")
    assert ".apps-acp-switch" in css and "min-height: var(--target)" in css and ".apps-acp-switch:focus-visible" in css


# --- Spanish, help, CSS ------------------------------------------------------------------------------------------------

SPANISH = {
    "ext.apps.acp.field.installed": "Instalado", "ext.apps.acp.field.compatible": "Compatible", "ext.apps.acp.field.authenticated": "Autenticado",
    "ext.apps.acp.field.activated": "Activado", "ext.apps.acp.check_again": "Comprobar otra vez", "ext.apps.acp.probe.button": "Comprobar",
    "ext.apps.acp.install.button": "Instalar", "ext.apps.acp.consent.configure": "Configurar consentimiento del área",
    "ext.apps.acp.consent.allow_write": "Permitir escribir archivos en esta área",
    "ext.apps.acp.consent.note": "El consentimiento no activa ejecutores, no permite uso de pago ni amplía la carpeta de trabajo.",
    "ext.apps.acp.start.button": "Empezar conversación con Codex ACP", "ext.apps.acp.unavailable": "ACP no disponible",
    "ext.apps.acp.choose_cli": "Elegir CLI", "ext.apps.acp.no_data_yet": "Sin dato todavía",
}


@needs_node
def test_new_texts_exist_in_english_and_spanish_with_the_agreed_wording():
    result = node("console.log(JSON.stringify({ en: Object.keys(dictionaries.en), es: dictionaries.es }));")
    keys_en = {key for key in result["en"] if key.startswith("ext.apps.acp.") or key == "ext.apps.reason.acp_probe_required"}
    keys_es = {key for key in result["es"] if key.startswith("ext.apps.acp.") or key == "ext.apps.reason.acp_probe_required"}
    assert keys_en == keys_es and len(keys_en) > 40
    for key, text in SPANISH.items():
        assert result["es"][key] == text, key


HELP_TOPICS = ["apps.acp_install", "apps.acp_probe", "apps.acp_area", "apps.acp_consent", "apps.acp_write",
               "apps.acp_start", "apps.acp_cli"]


def test_every_new_control_has_its_help_in_both_languages():
    help_source = (APPS / "help.js").read_text(encoding="utf-8")
    source = (APPS / "acp.js").read_text(encoding="utf-8")
    spanish = (APPS / "i18n.js").read_text(encoding="utf-8")
    for topic in HELP_TOPICS:
        assert f'"{topic}": {{' in help_source and f'"help.{topic}": {{' in spanish, topic
        assert f'helpFor("{topic}"' in source and f'topic="{topic}"' in source, topic


def test_css_uses_variables_targets_and_forced_colors_without_inline_style():
    css = (APPS / "style.css").read_text(encoding="utf-8")
    block = css[css.index("/* ACP-T04"):]
    assert "var(--target)" in block and "@media (forced-colors: active)" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", block)       # no colours of its own
    for name in ("apps-acp-panel", "apps-acp-install", "apps-acp-check", "apps-acp-area", "apps-acp-start", "apps-acp-unavailable"):
        assert name in block or name in (APPS / "acp.js").read_text(encoding="utf-8"), name
    for path in ("acp.js", "acp-model.js"):
        assert "style=" not in (APPS / path).read_text(encoding="utf-8")
