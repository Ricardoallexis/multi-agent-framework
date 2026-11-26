# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PAR-T04: the card «Trabajo a la vez» (extension ``concurrency``): pure model, texts, one write, no budget field, links to Usage."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

EXT = STATIC_DIR / "extensions" / "concurrency"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
SPANISH_NAVIGATOR = [
    "--import",
    "data:text/javascript,Object.defineProperty(globalThis,'navigator',{value:{language:'es-MX'},configurable:true})",
]
ENGLISH_NAVIGATOR = [
    "--import",
    "data:text/javascript,Object.defineProperty(globalThis,'navigator',{value:{language:'en-US'},configurable:true})",
]


def run_node(source: str, navigator=SPANISH_NAVIGATOR):
    result = subprocess.run([NODE, *navigator, "--input-type=module", "-e", source], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def uri(name: str) -> str:
    return json.dumps((EXT / name).as_uri())


CONFIG = {
    "mode": "normal", "margin": 10, "margins": {"eco": 30, "normal": 10, "performance": 3, "maximum": 3}, "manual": 256,
    "per_app": {}, "per_model": {}, "allow_t4": False, "duration": None, "expires_at": None, "selection": "auto",
    "limit": 2, "reason": "x", "code": "quota_window", "app": "codex", "window": "five_hour", "mode_reason": "saved_setting",
}


# --- the pure model ---------------------------------------------------------------------------------------------------------

@needs_node
def test_a_draft_that_changed_nothing_sends_nothing_and_each_change_sends_only_itself():
    result = run_node(f"""
      const m = await import({uri("model.js")});
      const config = {json.dumps(CONFIG)};
      const same = m.draftFromConfig(config);
      const out = {{}};
      out.same = m.buildPatch(config, same, {{ advanced: true }});
      out.dirty = m.isDirty(config, same, {{ advanced: true }});
      out.eco = m.buildPatch(config, m.withMode(config, same, "eco"));
      out.margin = m.buildPatch(config, {{ ...same, margin: 20 }});
      out.performance = m.buildPatch(config, m.withMode(config, same, "performance"));
      out.performanceTasks = m.buildPatch(config, {{ ...m.withMode(config, same, "performance"), duration: "tasks" }});
      out.manual = m.buildPatch(config, {{ ...same, selection: "manual", manual: 4, manualText: "4" }});
      out.manualClamped = m.buildPatch(config, {{ ...same, selection: "manual", manual: 99, manualText: "99" }});
      const manualConfig = {{ ...config, selection: "manual", manual: 4 }};
      out.backToAuto = m.buildPatch(manualConfig, m.draftFromConfig({{ ...manualConfig, selection: "auto" }}));
      out.t4Simple = m.buildPatch(config, {{ ...same, allowT4: true }});
      out.t4Advanced = m.buildPatch(config, {{ ...same, allowT4: true }}, {{ advanced: true }});
      out.perApp = m.buildPatch(config, {{ ...same, perApp: m.withLimit(same.perApp, "codex", "3") }}, {{ advanced: true }});
      out.perAppBad = m.withLimit({{}}, "codex", "0");
      out.perAppClear = m.withLimit({{ codex: 3 }}, "codex", "");
      out.perModel = m.addModelLimit({{}}, " qwen3:8b ", "2");
      out.perModelNoId = m.addModelLimit({{ a: 1 }}, "  ", "2");
      console.log(JSON.stringify(out));
    """)
    assert result["same"] == {} and result["dirty"] is False
    assert result["eco"] == {"mode": "eco"}
    assert result["margin"] == {"margin": 20}
    assert result["performance"] == {"mode": "performance", "duration": "next_five_hour"}
    assert result["performanceTasks"] == {"mode": "performance", "duration": "tasks"}
    assert result["manual"] == {"selection": "manual", "manual": 4}
    assert result["manualClamped"] == {"selection": "manual", "manual": 16}
    assert result["backToAuto"] == {"selection": "auto"}
    assert result["t4Simple"] == {}
    assert result["t4Advanced"] == {"allow_t4": True}
    assert result["perApp"] == {"per_app": {"codex": 3}}
    assert result["perAppBad"] == {} and result["perAppClear"] == {}
    assert result["perModel"] == {"qwen3:8b": 2} and result["perModelNoId"] == {"a": 1}


@needs_node
def test_each_mode_brings_its_own_margin_and_a_patch_never_carries_money_or_continuity():
    result = run_node(f"""
      const m = await import({uri("model.js")});
      const config = {json.dumps(CONFIG)};
      const draft = m.draftFromConfig(config);
      const margins = Object.fromEntries(m.MODES.map((mode) => [mode, m.withMode(config, draft, mode).margin]));
      const every = [];
      for (const mode of m.MODES) {{
        for (const selection of ["auto", "manual"]) {{
          every.push(...Object.keys(m.buildPatch(config, {{ ...m.withMode(config, draft, mode), selection, manual: 3, allowT4: true,
            perApp: {{ codex: 2 }}, perModel: {{ x: 1 }}, duration: "tasks", margin: 40 }}, {{ advanced: true }})));
        }}
      }}
      console.log(JSON.stringify({{ margins, keys: [...new Set(every)].sort(), allowed: m.MARGINS, modes: m.MODES }}));
    """)
    assert result["margins"] == {"eco": 30, "normal": 10, "performance": 3, "maximum": 3}
    assert result["allowed"] == [3, 5, 10, 15, 20, 30, 40]
    assert result["keys"] == ["allow_t4", "duration", "manual", "margin", "mode", "per_app", "per_model", "selection"]


@needs_node
def test_fixed_sentences_carry_their_data_and_the_budget_line_is_honest_about_what_it_knows():
    result = run_node(f"""
      import {uri("i18n.js")};
      const m = await import({uri("model.js")});
      const config = {json.dumps(CONFIG)};
      const out = {{}};
      out.eco = m.effectNote({{ selection: "auto", mode: "eco", margin: 3 }});
      out.manual = m.effectNote({{ selection: "manual", manual: 5 }});
      out.now = m.limitNote(config);
      out.nowManual = m.limitNote({{ ...config, selection: "manual", limit: 4 }});
      out.nowMargin = m.limitNote({{ ...config, code: "quota_margin_reached", limit: 0 }});
      out.nowNone = m.limitNote({{ ...config, code: "no_quota_data", limit: 1 }});
      out.warnMax = m.warningNote({{ selection: "auto", mode: "maximum" }}, config);
      out.warnGeneric = m.warningNote({{ selection: "auto", mode: "performance" }}, {{ code: "no_quota_data" }});
      out.warnEco = m.warningNote({{ selection: "auto", mode: "eco" }}, config);
      out.warnManual = m.warningNote({{ selection: "manual", mode: "maximum" }}, config);
      out.expired = m.modeExpired({{ mode_reason: "temporary_mode_expired" }});
      out.budgetMissing = m.budgetLine({{ totals: {{}} }});
      out.budgetNone = m.budgetLine({{ budgets: [] }});
      out.budgetMonth = m.budgetLine({{ budgets: [{{ scope: "month", amount: 50, currency: "USD" }}] }});
      out.levels = m.confirmedLevels({{ model_levels: {{ b: {{ level: "T3", source: "s" }}, a: {{ level: "T1", source: "s" }}, c: {{ level: "T9" }} }} }});
      console.log(JSON.stringify(out));
    """)
    assert result["eco"] == {"key": "ext.concurrency.effect.eco", "params": {"margin": 3}}
    assert result["manual"] == {"key": "ext.concurrency.effect.manual", "params": {"limit": 5}}
    assert result["now"]["key"] == "ext.concurrency.now.quota_window" and result["now"]["params"]["limit"] == 2
    assert result["nowManual"]["key"] == "ext.concurrency.now.manual"
    assert result["nowMargin"]["key"] == "ext.concurrency.now.margin_reached"
    assert result["nowNone"]["key"] == "ext.concurrency.now.no_quota_data"
    assert result["warnMax"]["key"] == "ext.concurrency.warning.maximum.named"
    assert result["warnGeneric"]["key"] == "ext.concurrency.warning.performance.generic"
    assert result["warnEco"] is None and result["warnManual"] is None
    assert result["expired"] is True
    assert result["budgetMissing"]["key"] == "ext.concurrency.budget.unavailable"
    assert result["budgetNone"]["key"] == "ext.concurrency.budget.none"
    assert result["budgetMonth"] == {"key": "ext.concurrency.budget.amount", "params": {"amount": 50, "currency": "USD"}}
    assert [row["model"] for row in result["levels"]] == ["a", "b"]


# --- the texts ---------------------------------------------------------------------------------------------------------------

@needs_node
def test_dictionary_has_english_and_spanish_for_the_same_keys_and_help_in_spanish_only():
    result = run_node(f"""
      import dictionary from {uri("i18n.js")};
      import {{ HELP }} from {uri("help.js")};
      console.log(JSON.stringify({{ en: Object.keys(dictionary.en), es: Object.keys(dictionary.es), topics: Object.keys(HELP) }}));
    """)
    en, es = set(result["en"]), set(result["es"])
    help_keys = {key for key in es if key.startswith("help.")}
    assert not any(key.startswith("help.") for key in en)
    assert es - help_keys == en
    assert all(key.startswith("ext.concurrency.") for key in en)
    assert help_keys == {"help." + topic for topic in result["topics"]}


@needs_node
def test_texts_follow_the_language_and_keep_their_data():
    script = f"""
      import {uri("i18n.js")};
      import {{ t }} from {json.dumps((STATIC_DIR / "js" / "i18n.js").as_uri())};
      const m = await import({uri("model.js")});
      const note = m.effectNote({{ selection: "auto", mode: "eco", margin: 3 }});
      console.log(JSON.stringify({{
        eco: t(note.key, note.params), name: t("ext.concurrency.mode.performance"),
        limit: t("ext.concurrency.limit.link"), budget: t("ext.concurrency.budget.change"),
        now: t("ext.concurrency.now.quota_window", {{ limit: 2, app: "Codex", window: "5-hour", margin: 10 }}),
      }}));
    """
    es = run_node(script)
    assert es["eco"].startswith("Eco (margen 3 %)") and es["name"] == "Rendimiento"
    assert es["limit"] == "Qué pasa al llegar al límite" and es["budget"] == "Cambiar en Uso"
    assert "Como máximo 2 a la vez" in es["now"] and "Codex" in es["now"]
    en = run_node(script, ENGLISH_NAVIGATOR)
    assert en["eco"].startswith("Eco (margin 3 %)") and en["name"] == "Performance"
    assert en["limit"] == "What happens at the limit" and en["budget"] == "Change in Usage"


# --- the rendered card: no budget field, the two links to Usage, nothing written by loading -----------------------------------

def card_module(tmp_path):
    source = (EXT / "index.js").read_text(encoding="utf-8")
    source = re.sub(r"^import\s[^;]*?;[^\r\n]*\r?\n", "", source, flags=re.M)
    source = source.replace("export function ConcurrencyView(", "function ConcurrencyView(")
    js = STATIC_DIR / "js"
    prefix = f"""
import "{(EXT / "i18n.js").as_uri()}";
import {{ html }} from {json.dumps((STATIC_DIR / "vendor" / "htm-preact-standalone.module.js").as_uri())};
import {{ t }} from {json.dumps((js / "i18n.js").as_uri())};
import {{ ApiError, apiGet }} from {json.dumps((js / "api.js").as_uri())};
import {{ authHeaders }} from {json.dumps((js / "auth.js").as_uri())};
import {{ errorText }} from {json.dumps((js / "text.js").as_uri())};
import {{ APPS, DURATIONS, MARGINS, MODES, addModelLimit, budgetLine, buildPatch, confirmedLevels, draftFromConfig, effectNote,
  isDirty, isTemporary, limitNote, manualAboveRange, modeExpired, parseCap, setCap, warningNote, withLimit, withMode,
  withoutKey }} from {uri("model.js")};
const useState = (initial) => globalThis.__hooks.useState(initial);
const useEffect = (fn, deps) => globalThis.__hooks.useEffect(fn, deps);
const registerHelp = () => {{}};
const registerView = () => {{}};
const currentMode = () => globalThis.__mode;
const Help = () => null;
const helpFor = () => ({{}});
const HELP = {{}};
"""
    path = tmp_path / "concurrency-card.mjs"
    path.write_text(prefix + source + "\nexport { ConcurrencyView };\n", encoding="utf-8")
    return path


# A tiny hook runtime for Node (no DOM): state and effects are kept per component position, so the card can be rendered again
# after an event the way a page would. `__seed` presets the state of the view; effects run only when `__effects` is set.
HARNESS = """
const store = new Map(), deps = new Map(), pending = [];
let dirty = false;
globalThis.__path = "r"; globalThis.__i = 0;
globalThis.__hooks = {
  useState(initial) {
    const key = globalThis.__path + "#" + globalThis.__i++;
    if (!store.has(key)) store.set(key, globalThis.__seed && key in globalThis.__seed ? globalThis.__seed[key] : initial);
    return [store.get(key), (value) => { store.set(key, typeof value === "function" ? value(store.get(key)) : value); dirty = true; }];
  },
  useEffect(fn, list) {
    const key = globalThis.__path + "#e" + globalThis.__i++;
    const before = deps.get(key);
    const changed = !before || !list || list.some((item, index) => item !== before[index]);
    deps.set(key, list);
    if (changed && globalThis.__effects) pending.push(fn);
  },
};
const strings = (node) => (node === null || node === undefined || typeof node === "boolean" ? [] : Array.isArray(node) ? node.flatMap(strings)
  : typeof node !== "object" ? [String(node)] : typeof node.type === "function" ? [] : strings((node.props || {}).children));
const flat = (node, out, path, ctx) => {
  if (node === null || node === undefined || typeof node === "boolean") return;
  if (Array.isArray(node)) { node.forEach((child) => flat(child, out, path, ctx)); return; }
  if (typeof node !== "object") { out.texts.push(String(node)); return; }
  const props = node.props || {};
  if (typeof node.type === "function") {
    const own = path + "/" + ctx.n++;
    globalThis.__path = own; globalThis.__i = 0;
    const result = node.type(props);
    flat(result, out, own, { n: 0 });
    return;
  }
  out.tags.push({ tag: node.type, props, label: strings(props.children).join(" ").trim() });
  flat(props.children, out, path, ctx);
};
let view, viewProps;
const render = () => {
  const out = { texts: [], tags: [] };
  globalThis.__path = "r"; globalThis.__i = 0;
  flat(view(viewProps), out, "r", { n: 0 });
  return out;
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async () => {
  let out;
  for (let round = 0; round < 20; round++) {
    dirty = false;
    out = render();
    const effects = pending.splice(0);
    effects.forEach((fn) => fn());
    await tick(); await tick();
    if (!dirty && effects.length === 0) break;
  }
  return out;
};
const plain = (out) => ({ texts: out.texts, tags: out.tags.map((item) => ({ tag: item.tag, label: item.label,
  props: Object.fromEntries(Object.entries(item.props).filter(([key, value]) => typeof value !== "function" && key !== "children")) })) });
const find = (out, test) => out.tags.find(test);
const button = (out, label) => find(out, (item) => item.tag === "button" && item.label === label);
const select = (out, value) => find(out, (item) => item.tag === "select" && item.props.value === value);
const caps = (out) => out.tags.filter((item) => item.tag === "input" && item.props.type === "number" && item.props.max === "256");
const manualBox = (out) => find(out, (item) => item.tag === "input" && item.props.type === "number");
const idBox = (out) => find(out, (item) => item.tag === "input" && item.props.type === "text");
const typeIn = async (tag, value) => { tag.props.onInput({ currentTarget: { value } }); return settle(); };
const press = async (tag) => { tag.props.onClick(); return settle(); };
const choose = async (tag, value) => { tag.props.onChange({ currentTarget: { value } }); return settle(); };
const allText = (out) => out.texts.join(" ") + " " + out.tags.map((item) => item.label).join(" ");
const puts = () => calls.filter((c) => c.method === "PUT").map((c) => c.body);
"""

FAKE_FETCH = """
const calls = [];
let config = JSON.parse(JSON.stringify(globalThis.__config));
globalThis.fetch = async (url, options = {}) => {
  const method = options.method || "GET";
  calls.push({ method, url, body: options.body ? JSON.parse(options.body) : null });
  const reply = (status, payload) => ({ ok: status < 400, status, json: async () => payload });
  if (method === "GET" && url === "/gui/api/settings/concurrency") return globalThis.__failLoad ? reply(500, { detail: { message: "boom" } }) : reply(200, config);
  if (method === "GET" && url === "/gui/api/settings") return reply(200, { model_levels: {} });
  if (method === "GET" && url === "/gui/api/usage/costs") return reply(200, {});
  if (method === "PUT" && url === "/gui/api/settings/concurrency") {
    if (globalThis.__failSave) return reply(422, { detail: { code: "invalid_request", message: "no way" } });
    config = { ...config, ...JSON.parse(options.body) };
    return reply(200, config);
  }
  return reply(404, { detail: "unknown" });
};
"""


def run_card(tmp_path, body: str, *, mode="advanced", config=CONFIG, fail_load=False, fail_save=False):
    """Mount the card with real hooks and a fake fetch, then run ``body`` (JS; ``out`` is the first settled render)."""
    module = card_module(tmp_path)
    return run_node(f"""
      {HARNESS}
      {FAKE_FETCH.replace("globalThis.__config", json.dumps(config))}
      globalThis.__mode = {json.dumps(mode)};
      globalThis.__effects = true;
      globalThis.__failLoad = {json.dumps(fail_load)};
      globalThis.__failSave = {json.dumps(fail_save)};
      globalThis.sessionStorage = {{ getItem: () => "tok", setItem: () => {{}} }};
      const mod = await import({json.dumps(module.as_uri())});
      view = mod.ConcurrencyView; viewProps = {{ token: "tok" }};
      let out = await settle();
      {body}
    """)


def render_card(tmp_path, *, mode: str, config=CONFIG, draft=None):
    """One render with the state of the view preset (no effects, so nothing is read or written)."""
    module = card_module(tmp_path)
    return run_node(f"""
      {HARNESS}
      globalThis.__mode = {json.dumps(mode)};
      globalThis.__effects = false;
      const m = await import({uri("model.js")});
      const draft = {json.dumps(draft)} || m.draftFromConfig({json.dumps(config)});
      globalThis.__seed = {{ "r#0": {json.dumps(config)}, "r#1": draft, "r#2": [{{ model: "qwen3:8b", level: "T1", source: "s" }}],
        "r#3": {{ budgets: [] }}, "r#4": false, "r#5": null, "r#6": false, "r#7": null, "r#8": false }};
      const mod = await import({json.dumps(module.as_uri())});
      view = mod.ConcurrencyView; viewProps = {{ token: "t" }};
      console.log(JSON.stringify(plain(await settle())));
    """)


def labelled(out) -> str:
    return " ".join(out["texts"]) + " " + " ".join(item["label"] for item in out["tags"])


@needs_node
def test_card_has_no_budget_field_and_the_two_links_to_usage_in_advanced(tmp_path):
    out = render_card(tmp_path, mode="advanced")
    links = [item["props"] for item in out["tags"] if item["tag"] == "a"]
    assert [link["href"] for link in links] == ["#usage", "#usage"]
    text = labelled(out)
    assert "Cambiar en Uso" in text and "Qué pasa al llegar al límite" in text
    assert "Sin presupuesto." in text                                        # the read-only line, from a budgets list that is empty
    fields = [item["props"] for item in out["tags"] if item["tag"] in {"input", "select", "textarea"}]
    assert fields and not any("budget" in json.dumps(field).lower() or "presupuesto" in json.dumps(field).lower() for field in fields)
    assert any(field.get("type") == "checkbox" for field in fields)         # «Permitir T4»
    assert "Niveles de modelo que confirmaste" in text and "qwen3:8b: T1" in text


@needs_node
def test_simple_card_has_auto_manual_modes_margin_and_only_the_limit_link(tmp_path):
    out = render_card(tmp_path, mode="simple")
    links = [item["props"]["href"] for item in out["tags"] if item["tag"] == "a"]
    assert links == ["#usage"]
    text = " ".join(out["texts"])
    assert "Margen al renovar" in text and "Normal (margen 10 %)" in text
    assert "Presupuesto" not in text and "Permitir T4" not in text and "Tope por app" not in text
    assert "Como máximo 2 a la vez, fijado por Codex (de 5 horas) con un margen de 10 %." in text
    assert not any(item["tag"] == "input" and item["props"].get("type") == "checkbox" for item in out["tags"])


@needs_node
def test_warning_shows_before_performance_is_saved_and_manual_hides_the_modes(tmp_path):
    draft = {"selection": "auto", "manual": 1, "manualText": "1", "mode": "performance", "margin": 3, "duration": "two_hours",
             "allowT4": False, "perApp": {}, "perModel": {}}
    text = " ".join(render_card(tmp_path, mode="simple", draft=draft)["texts"])
    assert "Rendimiento es temporal y puede agotar la cuota de 5 horas de Codex" in text
    manual = {**draft, "selection": "manual", "manual": 3, "manualText": "3"}
    text = " ".join(render_card(tmp_path, mode="simple", draft=manual)["texts"])
    assert "Manual: como máximo 3 a la vez" in text and "Margen al renovar" not in text and "Rendimiento es temporal" not in text


# --- load() and save() for real, against a fake fetch ----------------------------------------------------------------------------

@needs_node
def test_loading_only_reads_and_save_sends_one_minimal_put(tmp_path):
    result = run_card(tmp_path, """
      const afterLoad = calls.map((c) => c.method + " " + c.url);
      const before = button(out, "Guardar").props.disabled;
      out = await choose(select(out, "normal"), "eco");
      const ready = button(out, "Guardar").props.disabled;
      out = await press(button(out, "Guardar"));
      console.log(JSON.stringify({ afterLoad, before, ready, puts: calls.filter((c) => c.method === "PUT"), total: calls.length,
        saved: allText(out).includes("Guardado."), after: button(out, "Guardar").props.disabled }));
    """)
    assert result["afterLoad"] == ["GET /gui/api/settings/concurrency", "GET /gui/api/settings", "GET /gui/api/usage/costs"]
    assert result["before"] is True and result["ready"] is False
    assert result["puts"] == [{"method": "PUT", "url": "/gui/api/settings/concurrency", "body": {"mode": "eco"}}]
    assert result["total"] == 4 and result["saved"] is True and result["after"] is True


@needs_node
def test_save_error_load_error_and_expired_mode_are_rendered(tmp_path):
    saving = run_card(tmp_path, """
      out = await choose(select(out, "normal"), "eco");
      out = await press(button(out, "Guardar"));
      console.log(JSON.stringify({ text: allText(out), puts: puts().length, calls: calls.map((c) => c.method) }));
    """, fail_save=True, mode="simple")
    assert "No se pudo guardar" in saving["text"] and "invalid_request" in saving["text"] and saving["puts"] == 1
    failing = run_card(tmp_path, "console.log(JSON.stringify({ text: allText(out), calls: calls.map((c) => c.method) }));",
                       fail_load=True, mode="simple")
    assert "No se pudo leer el ajuste actual" in failing["text"] and set(failing["calls"]) == {"GET"}
    expired = run_card(tmp_path, "console.log(JSON.stringify({ text: allText(out) }));", mode="simple",
                       config={**CONFIG, "mode_reason": "temporary_mode_expired"})
    assert "El modo temporal terminó y el ajuste volvió a Normal." in expired["text"]


@needs_node
def test_an_existing_model_cap_row_survives_an_empty_box_and_goes_only_with_remove(tmp_path):
    result = run_card(tmp_path, """
      out = await typeIn(caps(out)[4], "");                         // the row of m1 (after the four app boxes)
      const emptied = { rows: out.tags.filter((i) => i.label === "Quitar").length, value: caps(out)[4].props.value,
        save: button(out, "Guardar").props.disabled };
      out = await typeIn(caps(out)[4], "5");
      const typed = button(out, "Guardar").props.disabled;
      out = await press(button(out, "Guardar"));
      out = await press(button(out, "Quitar"));
      out = await press(button(out, "Guardar"));
      console.log(JSON.stringify({ emptied, typed, puts: puts(), rowsLeft: out.tags.filter((i) => i.label === "Quitar").length }));
    """, config={**CONFIG, "per_model": {"m1": 2}})
    assert result["emptied"] == {"rows": 1, "value": "", "save": True}          # the row stays and the draft keeps the old value
    assert result["typed"] is False
    assert result["puts"] == [{"per_model": {"m1": 5}}, {"per_model": {}}] and result["rowsLeft"] == 0


@needs_node
def test_add_cap_needs_an_id_and_a_valid_cap_and_otherwise_keeps_the_fields(tmp_path):
    result = run_card(tmp_path, """
      const INVALID = "Escribe el id del modelo y un número entero de 1 a 256 como tope.";
      const add = (o) => button(o, "Añadir tope");
      const last = (o) => caps(o)[caps(o).length - 1];
      const states = { start: { disabled: add(out).props.disabled, message: allText(out).includes(INVALID) } };
      out = await typeIn(idBox(out), "m2");
      out = await typeIn(last(out), "0");
      states.zero = { disabled: add(out).props.disabled, message: allText(out).includes(INVALID) };
      out = await press(add(out));       // even if it is pressed, nothing is added and nothing is cleared
      states.afterClick = { id: idBox(out).props.value, cap: last(out).props.value, rows: out.tags.filter((i) => i.label === "Quitar").length,
        save: button(out, "Guardar").props.disabled };
      out = await typeIn(last(out), "7");
      states.valid = { disabled: add(out).props.disabled, message: allText(out).includes(INVALID) };
      out = await press(add(out));
      states.added = { id: idBox(out).props.value, rows: out.tags.filter((i) => i.label === "Quitar").length };
      out = await press(button(out, "Guardar"));
      states.puts = puts();
      console.log(JSON.stringify(states));
    """, config={**CONFIG, "per_model": {"m1": 2}})
    assert result["start"] == {"disabled": True, "message": False}
    assert result["zero"] == {"disabled": True, "message": True}
    assert result["afterClick"] == {"id": "m2", "cap": "0", "rows": 1, "save": True}
    assert result["valid"] == {"disabled": False, "message": False}
    assert result["added"] == {"id": "", "rows": 2}
    assert result["puts"] == [{"per_model": {"m1": 2, "m2": 7}}]


@needs_node
def test_manual_field_keeps_what_is_typed_and_shows_a_cap_set_elsewhere_above_16(tmp_path):
    result = run_card(tmp_path, """
      const first = { value: manualBox(out).props.value, text: allText(out), save: button(out, "Guardar").props.disabled };
      out = await typeIn(manualBox(out), "");
      const empty = { value: manualBox(out).props.value, save: button(out, "Guardar").props.disabled };
      out = await typeIn(manualBox(out), "20");
      const big = { value: manualBox(out).props.value, text: allText(out) };
      out = await press(button(out, "Guardar"));
      console.log(JSON.stringify({ first, empty, big, puts: puts() }));
    """, config={**CONFIG, "selection": "manual", "manual": 256, "limit": 256}, mode="simple")
    assert result["first"]["value"] == "256" and result["first"]["save"] is True
    assert "El límite manual es 256, fijado fuera de esta pantalla y por encima de su rango de 1 a 16." in result["first"]["text"]
    assert "Manual: como máximo 256 a la vez" in result["first"]["text"]
    assert result["empty"] == {"value": "", "save": True}                       # no snap-back, nothing to send
    assert result["big"]["value"] == "20" and "Manual: como máximo 16 a la vez" in result["big"]["text"]
    assert result["puts"] == [{"manual": 16}]                                    # the value the sentence showed


# --- static rules: one write, never on load, no budget, nothing outside the card's files --------------------------------------

def test_the_only_write_is_the_put_of_the_save_button_and_loading_never_writes():
    source = (EXT / "index.js").read_text(encoding="utf-8")
    assert source.count("method: \"PUT\"") == 1 and "method: \"POST\"" not in source
    assert len(re.findall(r"\bfetch\(", source)) == 1
    assert len(re.findall(r"\bputConcurrency\(", source)) == 2            # its definition and the one call
    load = re.search(r"async function load\(\) \{.*?\n  \}\n", source, re.S)[0]
    assert "putConcurrency" not in load and "save(" not in load
    assert re.search(r"useEffect\(\(\) => \{ load\(\); \}, \[token\]\);", source)
    assert not any("save" in body or "putConcurrency" in body for body in re.findall(r"useEffect\(\(\) => \{([^}]*)\}", source))
    save = re.search(r"async function save\(\) \{.*?\n  \}\n", source, re.S)[0]
    assert "buildPatch(" in save and "Object.keys(patch).length === 0" in save


def test_the_card_never_names_a_budget_write_nor_a_setting_of_continuity():
    for name in ("index.js", "model.js"):
        source = (EXT / name).read_text(encoding="utf-8")
        for forbidden in ("usage_budgets", "gui-settings", "budget_cut", "continuity", "settings/model-level", "settings/mode"):
            assert forbidden not in source, f"{name}: {forbidden}"
    index = (EXT / "index.js").read_text(encoding="utf-8")
    assert index.count('href="#usage"') == 2
    assert not re.search(r"<input[^>]*budget", index, re.I)
    assert "/gui/api/usage/costs" in index and "usage.js" not in index


def test_the_apps_screen_only_gets_a_link_to_the_card_and_the_card_is_a_registered_view():
    apps = (STATIC_DIR / "extensions" / "apps" / "index.js").read_text(encoding="utf-8")
    assert apps.count('href="#concurrency"') == 1
    assert "extensions/concurrency" not in apps and "../concurrency" not in apps
    index = (EXT / "index.js").read_text(encoding="utf-8")
    call = re.search(r"registerView\(\{(.*?)\}\);", index, re.S)[1]
    assert 'id: "concurrency"' in call and 'owner: "concurrency"' in call and 'mode: "simple"' in call
    assert (EXT / "style.css").is_file() and "style=" not in index
