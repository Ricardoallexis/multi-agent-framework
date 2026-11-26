# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""USO-T07: the pace sentence, the tabs in both modes, the tab addresses, and the budget and continuity forms."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
POLICY = STATIC_DIR / "extensions" / "continuity" / "policy.js"
NODE = shutil.which("node")


def source(path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("usage10-static")
    shutil.copytree(JS, root / "js")
    shutil.copytree(STATIC_DIR / "i18n", root / "i18n")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    shutil.copytree(STATIC_DIR / "extensions", root / "extensions")
    (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    return root


def run_node(root, body):
    script = root / "usage10.mjs"
    script.write_text(body, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_the_sentence_has_three_cases_and_no_data(node_root):
    out = run_node(node_root, r"""
      import { setLanguage } from "./js/i18n.js";
      import { paceOf, paceText, usagePace } from "./js/usage-model.js";
      const window = (extra) => ({ window: "weekly", state: "current", resets_at: "2026-10-18T12:00:00Z", ...extra });
      const app = (name, windows, extra = {}) => ({ app: name, status: "ok", windows, source: { kind: "x" }, ...extra });
      const plans = { apps: [
        app("codex", [window({ used_percent: 40, elapsed_percent: 60 })]),
        app("claude", [window({ used_percent: 70, elapsed_percent: 30 })]),
        app("copilot", [window({ window: "monthly", used_percent: 95, elapsed_percent: 90 })], { credits: { used: 95, cap: 100 } }),
      ] };
      const nodata = [
        app("codex", []), app("codex", [window({ used_percent: 40, elapsed_percent: null })]),
        app("claude", [window({ used_percent: 40, elapsed_percent: 60 })], { source: null }),
        app("copilot", [window({ window: "monthly", used_percent: 40, elapsed_percent: 60 })], { credits: { used: 1, cap: null } }),
        app("codex", [window({ used_percent: null, elapsed_percent: 60 })]),
        app("codex", [window({ used_percent: 60, elapsed_percent: 60, state: "window_reset" })]),
      ].map(paceOf);
      const result = {};
      for (const language of ["es", "en"]) {
        setLanguage(language);
        result[language] = usagePace(plans).map((item) => [item.app, item.kind, paceText(item)]);
        result[language + "_nodata"] = nodata.map((item) => [item.kind, paceText(item)]);
      }
      result.limit = (() => { setLanguage("es"); return paceText(paceOf(app("codex", [window({ used_percent: 100, state: "limit_reached", elapsed_percent: 10, resets_at: null })]))); })();
      result.disabled = usagePace({ apps: [{ app: "codex", status: "disabled", windows: [] }] }).length;
      console.log(JSON.stringify(result));
    """)
    codex, claude, copilot = out["es"]
    assert codex[:2] == ["codex", "ok"] and codex[2].startswith("Vas bien: llevas el 40 % del plan y ha pasado el 60 % de la semana")
    assert claude[:2] == ["claude", "fast"] and claude[2] == "Vas rápido: a este ritmo el plan se acaba antes de renovarse."
    assert copilot[:2] == ["copilot", "low"] and copilot[2].startswith("Queda poco: se renueva el ")
    assert out["en"][0][2].startswith("On track: you have used 40 % of the plan and 60 % of the week has passed")
    assert out["en"][1][2].startswith("Going fast") and out["en"][2][2].startswith("Little left: it renews on")
    assert all(kind == "no_data" for kind, _ in out["es_nodata"]) and all(text == "sin dato" for _, text in out["es_nodata"])
    assert all(text == "no data" for _, text in out["en_nodata"])
    assert out["limit"] == "Queda poco: el plan está cerca de su límite."     # a limit with no reset time still says so
    assert out["disabled"] == 0                                                  # an app without a plan has no sentence


@needs_node
def test_tab_addresses_and_the_open_tab_in_both_modes(node_root):
    out = run_node(node_root, r"""
      import { openTab, usageHref, usageTabFromHash } from "./js/usage-model.js";
      import { parseRoute } from "./js/route.js";
      const ids = ["plans", "global", "costs", "continuity"];
      console.log(JSON.stringify({
        hashes: ["#usage", "#usage/plans", "#usage/global", "#usage/costs", "#usage/continuity", "#usage/", "#sessions/plans", "#usage/a/b", ""].map(usageTabFromHash),
        route: parseRoute("#usage/continuity"), plain: parseRoute("#usage"),
        hrefs: ids.map(usageHref), plainHref: usageHref(""),
        advanced: ["", "plans", "costs", "continuity", "nope"].map((tab) => openTab("advanced", tab, ids)),
        simple: ["", "plans", "costs", "continuity", "nope"].map((tab) => openTab("simple", tab, ids)),
      }));
    """)
    assert out["hashes"] == ["", "plans", "global", "costs", "continuity", "", "", "", ""]
    assert out["route"]["section"] == "usage" and out["route"]["id"] == "continuity" and out["plain"] == {"section": "usage", "id": None}
    assert out["hrefs"] == ["#usage/plans", "#usage/global", "#usage/costs", "#usage/continuity"] and out["plainHref"] == "#usage"
    assert out["advanced"] == ["plans", "plans", "costs", "continuity", "plans"]       # #usage alone opens the usual tab
    assert out["simple"] == ["", "plans", "costs", "continuity", ""]                     # folded until one is asked for


def test_both_modes_keep_every_tab_and_drive_the_address():
    usage = source(JS / "usage.js")
    assert 'tabs=${tabs}' in usage and 'class="usage-folds"' in usage and 'aria-expanded=' in usage
    assert 'mode === "advanced"' in usage and "onModeChange" in usage
    assert usage.count('navigate("usage"') == 2 and "usageTabFromHash" in usage
    for part in ("<${Plans}", "<${Global}", "<${Costs}", "extraSection.component", "<${PaceSentences}", "<${BudgetSettings}"):
        assert part in usage, part
    # nothing was taken out of the old tabs
    for part in ("PlanCard", "TokenRows", "CostGroup", "usage.budget_notice", "usage.cost_evidence", "usage.plan_notes"):
        assert part in usage, part


@needs_node
def test_budget_form_rules(node_root):
    out = run_node(node_root, r"""
      import { budgetChanged, budgetPayload, budgetRows, budgetValid, parseAmount, sourceText } from "./js/usage-model.js";
      import { setLanguage } from "./js/i18n.js";
      const data = { source: "gui", known_providers: ["openai", "gemini", "ollama"],
        providers: { openai: { amount: 20, mode: "warn" }, gemini: { amount: 0, mode: "cut" } } };
      const rows = budgetRows(data);
      const edited = (name, change) => rows.map((row) => row.provider === name ? { ...row, ...change } : row);
      const environment = budgetRows({ source: "environment", known_providers: ["openai"], providers: {}, budgets: [{ id: "e" }] });
      setLanguage("es"); const es = ["gui", "environment", "default", "other"].map(sourceText);
      setLanguage("en"); const en = ["gui", "environment", "default"].map(sourceText);
      console.log(JSON.stringify({
        rows, payload: budgetPayload(rows), same: budgetChanged(rows, budgetRows(data)),
        amountChange: budgetChanged(edited("openai", { amount: "25" }), rows), modeChange: budgetChanged(edited("ollama", { mode: "cut" }), rows),
        clear: budgetPayload(edited("openai", { amount: "" })).providers.openai,
        zero: budgetPayload(edited("openai", { amount: "0" })).providers.openai, comma: parseAmount("20,5"),
        bad: ["-1", "abc", "1e3", "20.1234567", "1000000001", "NaN", "Infinity", "--"].map((text) => parseAmount(text).valid),
        valid: budgetValid(rows), invalid: budgetValid(edited("openai", { amount: "-3" })),
        untouchedEnvironment: budgetChanged(environment, environment), es, en,
      }));
    """)
    assert [row["provider"] for row in out["rows"]] == ["gemini", "ollama", "openai"]
    assert out["payload"]["providers"] == {"gemini": {"amount": 0, "mode": "cut"}, "ollama": {"amount": None, "mode": "warn"},
                                           "openai": {"amount": 20, "mode": "warn"}}
    assert out["same"] is False and out["untouchedEnvironment"] is False         # the form does not send without changes
    assert out["amountChange"] is True and out["modeChange"] is True
    assert out["clear"] == {"amount": None, "mode": "warn"}                      # empty is no budget
    assert out["zero"] == {"amount": 0, "mode": "warn"}                          # 0 is do not spend
    assert out["comma"] == {"valid": True, "value": 20.5}
    assert out["bad"] == [False] * 8 and out["valid"] is True and out["invalid"] is False
    assert out["es"] == ["guardado en esta GUI", "definido por el entorno", "valor predeterminado", "valor predeterminado"]
    assert out["en"] == ["saved in this GUI", "set by the environment", "default value"]


@needs_node
def test_continuity_form_rules_and_no_automatic_switch(node_root):
    out = run_node(node_root, r"""
      import { CONTINUITY_APPS, CONTINUITY_HOURS, CONTINUITY_MODES, continuityChanged, continuityDraft, continuityPayload } from "./js/usage-model.js";
      const data = { source: "gui", mode: "semi_auto", wait_threshold_hours: 24, automatic: false, max_handoffs: 1,
        candidates: { claude_cli: "never", codex_cli: "ask", copilot_cli: "no_ask" } };
      const draft = continuityDraft(data);
      console.log(JSON.stringify({
        modes: CONTINUITY_MODES, hours: CONTINUITY_HOURS, apps: CONTINUITY_APPS, draft, payloadKeys: Object.keys(continuityPayload(draft)).sort(),
        same: continuityChanged(draft, continuityDraft(data)),
        hours_changed: continuityChanged({ ...draft, wait_threshold_hours: 3 }, draft),
        app_changed: continuityChanged({ ...draft, candidates: { ...draft.candidates, codex_cli: "never" } }, draft),
        mode_changed: continuityChanged({ ...draft, mode: "manual" }, draft),
        fallback: continuityDraft({ mode: "auto", wait_threshold_hours: 7, candidates: { claude_cli: "x" } }),
      }));
    """)
    assert out["modes"] == ["manual", "semi_auto"] and out["hours"] == [1, 3, 5, 24]
    assert out["apps"] == ["claude_cli", "codex_cli", "copilot_cli"]
    assert out["draft"] == {"mode": "semi_auto", "wait_threshold_hours": 24,
                            "candidates": {"claude_cli": "never", "codex_cli": "ask", "copilot_cli": "no_ask"}}
    assert out["payloadKeys"] == ["candidates", "mode", "wait_threshold_hours"]       # nothing else travels
    assert out["same"] is False and out["hours_changed"] and out["app_changed"] and out["mode_changed"]
    assert out["fallback"]["mode"] == "manual" and out["fallback"]["wait_threshold_hours"] == 5      # the safest values
    policy = source(POLICY)
    # USO-T08 owns the automatic switch: no checkbox, no toggle and no "automatic" value in this section
    assert "checkbox" not in policy and 'role="switch"' not in policy and "automatic" not in policy.replace("fixed_", "")
    assert "auto" not in out["modes"]


def test_the_three_fixed_rules_are_a_note_without_a_control():
    policy = source(POLICY)
    block = policy[policy.index('class="continuity-fixed-rules'):policy.index("</form>")]
    for key in ("fixed_title", "fixed_handoffs", "fixed_running", "fixed_paid", "not_released"):
        assert f"ext.continuity.settings.{key}" in block, key
    assert not re.search(r"<(input|select|button|textarea)", block)
    assert policy.count("<select") == 3 and policy.count('type="submit"') == 1          # mode, hours, one per app (a map); one save


def test_forms_send_with_put_and_keep_the_csp_rules():
    api = source(JS / "api.js")
    assert 'export const apiPut = (path, token, body) => request("PUT", path, token, body);' in api
    usage, policy, model = source(JS / "usage.js"), source(POLICY), source(JS / "usage-model.js")
    assert 'apiPut("/gui/api/settings/budget"' in usage and 'apiPut("/gui/api/settings/continuity"' in policy
    for name, text in (("usage.js", usage), ("policy.js", policy), ("usage-model.js", model)):
        assert " style=" not in text and "innerHTML" not in text and "dangerouslySetInnerHTML" not in text, name
        assert "onSubmit=" in text or name == "usage-model.js"
    # a form that matches the server does not send
    assert usage.count("!changed || !valid || saving") >= 2 and "if (!changed || saving) return;" in policy


NEW_USAGE_KEYS = [
    "usage.pace.title", "usage.pace.ok", "usage.pace.fast", "usage.pace.low", "usage.pace.low_no_date", "usage.pace.no_data",
    "usage.pace.period.five_hour", "usage.pace.period.week", "usage.pace.period.month", "usage.pace.period.other",
    "usage.see_detail", "usage.hide_detail", "usage.source.gui", "usage.source.environment", "usage.source.default",
    "usage.budget.title", "usage.budget.source", "usage.budget.mode.warn", "usage.budget.mode.cut", "usage.budget.save",
    "usage.budget.no_changes", "usage.budget.saved", "usage.budget.amount_note", "usage.budget.mode_note",
]


@needs_node
def test_new_texts_exist_in_english_and_spanish_and_the_old_spanish_is_unchanged(node_root):
    out = run_node(node_root, r"""
      import "./extensions/continuity/i18n.js";
      import { registerTranslations, setLanguage, t } from "./js/i18n.js";
      const keys = %s;
      const ext = ["title", "source", "mode_label", "hours_label", "destinations", "save", "no_changes", "saved", "fixed_title",
        "fixed_handoffs", "fixed_running", "fixed_paid", "not_released", "source.gui", "source.environment", "source.default"]
        .map((name) => "ext.continuity.settings." + name);
      const read = (language) => { setLanguage(language); return [...keys, ...ext].map((key) => t(key, { count: 1, used: 1, elapsed: 1, period: "p", when: "w", source: "s" })); };
      const en = read("en"), es = read("es");
      setLanguage("es");
      console.log(JSON.stringify({ en, es, plans: t("usage.plans_tab"), costs: t("usage.costs_heading"), mode: t("ext.continuity.mode.manual.explanation") }));
    """ % json.dumps(NEW_USAGE_KEYS))
    assert len(out["en"]) == len(out["es"]) == len(NEW_USAGE_KEYS) + 16
    assert all(isinstance(text, str) and text and not text.startswith(("usage.", "ext.")) for text in out["en"] + out["es"])
    assert out["en"] != out["es"]
    assert (out["plans"], out["costs"]) == ("Planes", "API y presupuesto")
    assert out["mode"] == "Siempre te pregunta antes de esperar o de pasar la tarea a otro agente."


def test_the_old_policy_note_no_longer_says_it_cannot_be_changed():
    extension = source(STATIC_DIR / "extensions" / "continuity" / "i18n.js")
    assert "They cannot be changed here yet" not in extension and "todavía no se pueden cambiar desde aquí" not in extension
    en, es = source(STATIC_DIR / "i18n" / "en" / "usage.js"), source(STATIC_DIR / "i18n" / "es" / "usage.js")
    assert "does not yet provide a budget" not in en and "todavía no expone un presupuesto" not in es


def test_no_div_uses_the_bare_notice_style_and_the_budget_help_points_above_the_form():
    # ".notice" alone is the full-screen «no token» box (margin 15vh); only p.notice and .notice.check are compact
    for path in (JS / "usage.js", POLICY):
        assert not re.search(r'<div[^>]*class="[^"]*\bnotice\b', source(path)), path.name
    usage = source(JS / "usage.js")
    assert usage.count('helpFor("usage.costs", { scope: "budget" })') == 3 and 'helpFor("usage.costs")' not in usage
    assert '<${Help} topic="usage.costs" scope="budget" />' in usage
    # «no changes to save» only while the form is valid, so it never sits beside an error
    assert '${!changed && valid && html`<span class="muted small">${t("usage.budget.no_changes")}</span>`}' in usage


@needs_node
def test_an_invalid_amount_is_not_reported_as_no_changes(node_root):
    out = run_node(node_root, r"""
      import { budgetChanged, budgetRows, budgetValid } from "./js/usage-model.js";
      const rows = budgetRows({ source: "gui", known_providers: ["openai"], providers: {} });
      const typed = rows.map((row) => ({ ...row, amount: "abc" }));
      console.log(JSON.stringify({ changed: budgetChanged(typed, rows), valid: budgetValid(typed) }));
    """)
    assert out == {"changed": False, "valid": False}      # unchanged yet invalid: the text «no changes» must stay hidden


def test_the_help_of_the_usage_screen_matches_what_it_now_does():
    lists = source(JS / "help-text-lists.js")
    extension_help = source(STATIC_DIR / "extensions" / "continuity" / "help.js")
    extension_i18n = source(STATIC_DIR / "extensions" / "continuity" / "i18n.js")
    es = source(STATIC_DIR / "i18n" / "es" / "usage.js")
    assert "this screen only displays them" not in extension_help and "esta pantalla solo los muestra" not in extension_i18n
    assert "In the advanced mode, use the arrow keys" in lists and "En el modo avanzado, usa las flechas" in es
    assert "monthly budget for each API provider" in lists and "presupuesto mensual por proveedor de API" in es
