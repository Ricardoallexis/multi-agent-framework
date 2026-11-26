# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T04: the new-session composer, recent cards, and automatic effort preferences."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "multiagent" / "gui" / "static" / "js"
CSS = ROOT / "multiagent" / "gui" / "static" / "new-session.css"
HELP_EN = JS / "help-text-shell.js"
HELP_ES = ROOT / "multiagent" / "gui" / "static" / "i18n" / "es" / "shell.js"
NEW_SESSION_EN = ROOT / "multiagent" / "gui" / "static" / "i18n" / "en" / "new-session.js"
NEW_SESSION_ES = ROOT / "multiagent" / "gui" / "static" / "i18n" / "es" / "new-session.js"
NODE = shutil.which("node")


def test_home_composer_recent_cards_and_responsive_fit():
    view = (JS / "new-session.js").read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert 't("new_session.home_title")' in view
    assert '<${RecentConversations} recent=${recentRuns} />' in view
    assert 'href=${threadHref(run.id)}' in view and 't("new_session.continue_conversation")' in view
    assert 't("new_session.where")' in view and 't("new_session.test_run_no_cost")' in view
    assert 't("new_session.start_with_office")' in view and "onPick=${typing}" in view
    assert 't("new_session.no_model_connect")' in view and 't("new_session.message_stays_local")' in view
    assert 't("new_session.return_at", { time: resetTime })' in view
    assert 't("new_session.keep_writing")' in view and 't("new_session.apps_agents")' in view
    assert 'aria-label=${t("new_session.send")}' in view and "hidden=${conv.phase === \"confirm\"}" in view
    assert "GUI4-T04" in css
    assert ".ns-recent-card:nth-child(n + 3) { display: none; }" in css
    assert ".ns-recent-card:nth-child(n + 2) { display: none; }" in css
    assert "@media (max-height: 820px)" in css and "@media (max-height: 620px)" in css
    assert ".ns-recent-grid { display: none; }" in css
    assert "style=" not in view


def test_help_and_new_session_text_uses_system_catalog_wording():
    for path in (HELP_EN, HELP_ES, NEW_SESSION_EN, NEW_SESSION_ES):
        text = path.read_text(encoding="utf-8").lower()
        assert "general bundle" not in text
        assert "paquete general" not in text


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_limit_banner_fallback_focuses_composer_and_has_one_help_topic():
    source = (JS / "new-session.js").read_text(encoding="utf-8")
    component = re.search(r"function LimitBanner\(\{[^\n]*\}\) \{[\s\S]*?^\}", source, re.M)
    assert component is not None
    script = f"""
      import {{ html }} from {json.dumps((ROOT / "multiagent" / "gui" / "static" / "vendor" / "htm-preact-standalone.module.js").as_uri())};
      const source = {json.dumps(component.group(0))};
      const Help = () => null;
      const LimitBanner = new Function("html", "t", "buildLimitNotice", "limitTimeText", "helpFor", "routeHref", "Help",
        source + "; return LimitBanner;")(html, (key) => ({{
          "new_session.limit_reached": "Limit reached",
          "new_session.limit_text_stays": "Text stays here",
          "new_session.return_when_reset": "Return when the limit resets",
          "new_session.keep_writing": "Keep writing",
          "new_session.apps_agents": "Apps and agents",
        }}[key] || key), (notice) => notice, () => null, () => ({{}}), () => "/apps", Help);
      const tree = LimitBanner({{notice:{{text:"Limit reached", suggestions:[]}}, connections:{{}}, busy:false}});
      const nodes = [];
      const walk = (node) => {{
        if (Array.isArray(node)) return node.forEach(walk);
        if (!node || typeof node !== "object") return;
        nodes.push(node);
        walk(node.props?.children);
      }};
      const text = (node) => node == null || typeof node === "boolean" ? ""
        : Array.isArray(node) ? node.map(text).join("")
        : typeof node === "object" ? text(node.props?.children) : String(node);
      walk(tree);
      const keep = nodes.find((node) => node.type === "button" && text(node).includes("Keep writing"));
      let focused = 0;
      globalThis.document = {{getElementById(id) {{
        if (id !== "ns-composer-input") throw new Error("Unexpected focus target: " + id);
        return {{focus() {{ focused += 1; }}}};
      }}}};
      keep.props.onClick();
      console.log(JSON.stringify({{
        button: Boolean(keep),
        focused,
        limitHelpCount: nodes.filter((node) => node.type === Help && node.props.topic === "chat.limit_notice").length,
      }}));
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"button": True, "focused": 1, "limitHelpCount": 1}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mock_test_run_notice_and_preview_preferences_do_not_retrigger_effect():
    source = (JS / "new-session.js").read_text(encoding="utf-8")
    where_start = source.index('<div class="ns-where"')
    where = source[where_start:source.index("</div>`}", where_start)]
    notice = re.search(r"\$\{(mock && html`<>.*?new_session\.test_run_no_cost.*?</>`)\}", where, re.S)
    assert notice is not None
    script = f"""
      import {{ html }} from {json.dumps((ROOT / "multiagent" / "gui" / "static" / "vendor" / "htm-preact-standalone.module.js").as_uri())};
      const noCostNotice = new Function("mock", "html", "t",
        "return " + {json.dumps(notice.group(1))} + ";");
      const shown = noCostNotice(true, html, (key) => key === "new_session.test_run_no_cost" ? "No-cost test" : key);
      const hidden = noCostNotice(false, html, (key) => key === "new_session.test_run_no_cost" ? "No-cost test" : key);
      const nodes = [];
      const walk = (node) => {{
        if (Array.isArray(node)) return node.forEach(walk);
        if (!node || typeof node !== "object") return;
        nodes.push(node);
        walk(node.props?.children);
      }};
      const text = (node) => node == null || typeof node === "boolean" ? ""
        : Array.isArray(node) ? node.map(text).join("")
        : typeof node === "object" ? text(node.props?.children) : String(node);
      walk(shown);
      console.log(JSON.stringify({{
        shown: nodes.some((node) => node.type === "span" && node.props.class === "ns-where-separator")
          && nodes.some((node) => node.type === "span" && text(node) === "No-cost test"),
        hidden: hidden === false,
      }}));
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"shown": True, "hidden": True}
    effect_start = source.index("// The summary:")
    effect = source[effect_start:source.index("const currentOfficeDetail", effect_start)]
    dependencies = re.search(r"\}, \[(.*?)\]\);", effect, re.S)
    assert dependencies is not None
    assert "automaticPreferences" not in dependencies.group(1)
    assert "token" in dependencies.group(1)
    effect_script = f"""
      const effectSource = {json.dumps(effect)};
      const runPreview = async (effort) => {{
        const key = "bundle|wf";
        const routing = {{steps:[{{step_id:"answer",status:"selected",effort_options:["medium","high","max"]}}]}};
        const automaticRouting = {{current:{{workflowKey:key,routing}}}};
        const automaticPreferences = {{current:{{workflowKey:key,effort:"medium",values:{{}}}}}};
        const conv = {{phase:"confirm",workflow:{{id:"wf",bundle:"bundle"}},answers:{{}},inputNames:[]}};
        let callback, dependencies;
        const calls = [];
        const setup = new Function("useEffect","conv","mode","sensitiveSelection","currentWorkflowKey",
          "automaticModel","automaticEffort","automaticRouting","automaticPreferences","previewWorkflowKey",
          "preview","routingOverrides","token","ticket","runBody","automaticEffortPreferences",
          "mergeRoutingPreferences","setPreview","setPreviewWorkflowKey","setPreviewError","setPreviewing",
          "apiPost","asError", effectSource);
        setup((fn,deps) => {{ callback=fn; dependencies=deps; }}, conv, "auto", false, key, true, effort,
          automaticRouting, automaticPreferences, "", null, {{}}, "fake-token", {{current:0}},
          (state) => ({{workflow_id:state.workflow.id,bundle:state.workflow.bundle,inputs:{{}}}}),
          (current, value) => Object.fromEntries((current?.steps || [])
            .filter((step) => step.step_id && step.status !== "function" && step.effort_options.includes(value))
            .map((step) => [step.step_id, {{effort:value}}])),
          (automatic, overrides) => ({{...automatic,...overrides}}), () => {{}}, () => {{}}, () => {{}}, () => {{}},
          async (path, token, body) => {{
            calls.push({{path,token,body}});
            return {{routing}};
          }}, (failure) => failure);
        callback();
        await new Promise((resolve) => setTimeout(resolve, 0));
        return {{calls, dependencies}};
      }};
      const confirm = await runPreview("high");
      const changedEffort = await runPreview("max");
      console.log(JSON.stringify({{
        confirmCalls: confirm.calls.length,
        confirmPreferences: confirm.calls[0]?.body.routing_preferences,
        changedEffortCalls: changedEffort.calls.length,
        changedEffortPreferences: changedEffort.calls[0]?.body.routing_preferences,
        dependencyCount: confirm.dependencies.length,
      }}));
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", effect_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "confirmCalls": 1,
        "confirmPreferences": {"answer": {"effort": "high"}},
        "changedEffortCalls": 1,
        "changedEffortPreferences": {"answer": {"effort": "max"}},
        "dependencyCount": 9,
    }


def test_gui4_t04_mode_selector_and_single_model_labels_remain_accessible():
    source = (JS / "new-session.js").read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    block = css.split("/* GUI4-T04 */", 1)[1]
    assert 'class="ns-box-select ns-mode-select"' in source
    assert ".ns-box-controls #ns-mode-select { max-width: 11rem; }" in block
    assert ".ns-box-select.ns-mode-select > label, .ns-picker-slot .ns-model-select > label" in block


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_auto_effort_uses_existing_step_preferences_and_option_reason_precedence():
    script = f"""
      Object.defineProperty(globalThis, "navigator", {{value:{{language:"es-MX"}}, configurable:true}});
      const model = await import({json.dumps((JS / "new-session-model.js").as_uri())});
      const picker = await import({json.dumps((JS / "model-picker-model.js").as_uri())});
      const routing = {{steps:[
        {{step_id:"answer",status:"catalog_default",effort_options:["medium","high","max"]}},
        {{step_id:"function",status:"function",effort_options:["medium","high","max"]}},
        {{step_id:"unsupported",status:"catalog_default",effort_options:["low"]}},
      ]}};
      const connections = {{connections:[
        {{id:"gemini",enabled:true,available:false,reason:"quota_paused"}},
        {{id:"openai",enabled:true,available:true}},
      ]}};
      const reasons = picker.pickerGroups(connections, {{options:[
        {{id:"gemini",kind:"cloud",selectable:false,reason:"no_access"}},
        {{id:"openai",kind:"cloud",selectable:false,reason:"no_access"}},
      ]}});
      const localOnly = picker.pickerGroups(connections, {{options:[
        {{id:"gemini",kind:"cloud",selectable:false,reason:"local_only"}},
      ]}});
      console.log(JSON.stringify({{
        medium:model.automaticEffortPreferences(routing,"medium"),
        high:model.automaticEffortPreferences(routing,"high"),
        max:model.automaticEffortPreferences(routing,"max"),
        invalid:model.automaticEffortPreferences(routing,"low"),
        preserved:reasons.cloud.find((item)=>item.id==="gemini").reason,
        supplied:reasons.cloud.find((item)=>item.id==="openai").reason,
        local:localOnly.cloud.find((item)=>item.id==="gemini").reason,
      }}));
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["medium"] == {"answer": {"effort": "medium"}}
    assert output["high"] == {"answer": {"effort": "high"}}
    assert output["max"] == {"answer": {"effort": "max"}}
    assert output["invalid"] == {}
    assert output["preserved"].startswith("Límite alcanzado")
    assert output["supplied"] == "Sin acceso."
    assert output["local"] == "No disponible en un área solo local."
