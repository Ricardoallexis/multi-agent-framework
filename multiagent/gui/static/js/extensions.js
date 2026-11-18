// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// GUI extension points (FLU-T04). A task that adds a view, a right-panel slot or help texts creates ONE folder,
// static/extensions/<name>/, and edits no shared file (index.html, main.js, route.js, help-text.js, right-panel.js):
//   index.js    the entry module; calls the register functions below when it is loaded
//   style.css   optional stylesheet; the page adds it as a <link> (nothing to add to index.html)
//   help.js     optional help texts: `export const HELP = { ... }` as strict JSON, keys "<name>.*" (see help-text.js)
//   others      helpers of the extension (models, components); only index.js and style.css are loaded by the page
// <name> is lowercase letters and "_". The server lists the folders (GET /gui/api/extensions, multiagent/gui/extensions.py);
// main.js calls loadExtensions() before it draws and waits at most LOAD_BUDGET_MS for it: a manifest that hangs or fails, or an
// extension that is slow or broken, never keeps the core screens from drawing. Whatever has not finished by then is listed as
// failed (extensionFailures(), the notice in main.js) and, if it finishes later, it registers late: subscribeRegistry() tells
// the page, which draws again.
//
//   registerView({ id, label, component, order, owner })   a screen: a button in the sidebar and the route "#<id>" or "#<id>/<item>"
//                                                   (component props: token, status, mock, route = { section, id })
//   registerSlot(name, component, { title, topic, order, owner })   a section of the right panel (also exported by panel-slots.js);
//                                                   props: token, route, sessionId; topic is a help key (optional)
//   registerUsageSection({ id, label, component, order, owner })   a tab of the «Uso» screen after Planes, Global and API y presupuesto
//                                                   (CNT-T06; props: token); the ids of the built-in tabs are reserved
//   registerHelp(entries, owner)                    help entries { title, hint, more } for <Help topic=...> and helpFor()
// owner is the extension folder id. A failed import removes only that owner's registrations; calls without owner stay registered.
//
// This file imports nothing, so it loads anywhere (the page, Node tests). The one dynamic import() of the GUI is here:
// its path is a constant plus an extension id that matched NAME, never a URL taken from elsewhere (see importEntry).

export const RESERVED_SECTIONS = ["sessions", "runs", "usage", "evaluation", "tree", "map", "new"];   // the routes of the built-in screens
export const RESERVED_SLOTS = ["changes", "subagents", "tokens", "sources"];            // the sections of the built-in panel
export const RESERVED_USAGE_SECTIONS = ["plans", "global", "costs"];                    // the tabs of the built-in «Uso» screen (usage.js)
export const LOAD_BUDGET_MS = 3000;   // the most the first render waits for the manifest, the modules and the sheets together
const ID = /^[a-z][a-z0-9_]{0,39}$/;
const NAME = /^[a-z][a-z_]{0,39}$/;
const TOPIC = /^[a-z_]+(?:\.[a-z_]+)+$/;
const ENTRY_URL = /^\/gui\/static\/extensions\/([a-z][a-z_]{0,39})\/index\.js$/;
const STYLE_URL = /^\/gui\/static\/extensions\/([a-z][a-z_]{0,39})\/style\.css$/;
const STYLE_WAIT_MS = 2500;   // a sheet that neither loads nor fails stops being waited for (below LOAD_BUDGET_MS)

const views = new Map();
const slots = new Map();
const usageSections = new Map();
const helps = new Map();
const ownership = new Map(); // owner -> registrations made by that extension, across all registries
const failures = new Map();   // extension id -> why it is not loaded (the manifest itself is "extensions")
const listeners = new Set();
let version = 0;

// The page draws again when something is registered or fails late: it subscribes with subscribeRegistry (main.js, route.js).
function changed() {
  version += 1;
  for (const listener of [...listeners]) {
    try { listener(); } catch (_) { /* a listener must not stop the others */ }
  }
}

export function registryVersion() { return version; }
export function subscribeRegistry(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function refuse(message) { throw new Error(message); }

const byOrder = (a, b) => (a.order - b.order) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
const orderOf = (value) => (Number.isFinite(value) ? value : 100);
function record(owner, registry, key) {
  if (typeof owner !== "string" || !NAME.test(owner)) {
    console.warn("GUI registration " + key + " has no explicit owner; it cannot be rolled back");
    return;
  }
  if (!ownership.has(owner)) ownership.set(owner, []);
  ownership.get(owner).push([registry, key]);
}

function rollback(owner) {
  const registrations = ownership.get(owner) || [];
  ownership.delete(owner);
  for (const [registry, key] of registrations) registry.delete(key);
  if (registrations.length) changed();
}

export function registerView({ id, label, component, order, owner } = {}) {
  if (typeof id !== "string" || !ID.test(id)) refuse("registerView: id must be lowercase letters, digits and _ (got " + String(id) + ")");
  if (RESERVED_SECTIONS.includes(id)) refuse("registerView: \"" + id + "\" is the route of a built-in screen");
  if (views.has(id)) refuse("registerView: \"" + id + "\" is already registered");
  if (typeof label !== "string" || !label.trim() || label.trim().length > 30) refuse("registerView: label must be 1 to 30 characters");
  if (typeof component !== "function") refuse("registerView: component must be a function");
  views.set(id, { id, key: id, label: label.trim(), component, order: orderOf(order) });
  record(owner, views, id);
  changed();
}

export function registeredViews() { return [...views.values()].sort(byOrder); }
export function isRegisteredView(id) { return views.has(id); }

export function registerSlot(name, component, { title, topic = null, order, owner } = {}) {
  if (typeof name !== "string" || !ID.test(name)) refuse("registerSlot: name must be lowercase letters, digits and _ (got " + String(name) + ")");
  if (RESERVED_SLOTS.includes(name)) refuse("registerSlot: \"" + name + "\" is a section of the built-in panel");
  if (slots.has(name)) refuse("registerSlot: \"" + name + "\" is already registered");
  if (typeof component !== "function") refuse("registerSlot: component must be a function");
  if (typeof title !== "string" || !title.trim() || title.trim().length > 30) refuse("registerSlot: title must be 1 to 30 characters");
  if (topic !== null && (typeof topic !== "string" || !TOPIC.test(topic))) refuse("registerSlot: topic must be a help key like \"name.topic\"");
  slots.set(name, { name, key: name, title: title.trim(), topic, component, order: orderOf(order) });
  record(owner, slots, name);
  changed();
}

export function registeredSlots() { return [...slots.values()].sort(byOrder); }

export function registerUsageSection({ id, label, component, order, owner } = {}) {
  if (typeof id !== "string" || !ID.test(id)) refuse("registerUsageSection: id must be lowercase letters, digits and _ (got " + String(id) + ")");
  if (RESERVED_USAGE_SECTIONS.includes(id)) refuse("registerUsageSection: \"" + id + "\" is a tab of the built-in Uso screen");
  if (usageSections.has(id)) refuse("registerUsageSection: \"" + id + "\" is already registered");
  if (typeof label !== "string" || !label.trim() || label.trim().length > 30) refuse("registerUsageSection: label must be 1 to 30 characters");
  if (typeof component !== "function") refuse("registerUsageSection: component must be a function");
  usageSections.set(id, { id, key: id, label: label.trim(), component, order: orderOf(order) });
  record(owner, usageSections, id);
  changed();
}

export function registeredUsageSections() { return [...usageSections.values()].sort(byOrder); }

export function registerHelp(entries, owner) {
  if (!entries || typeof entries !== "object" || Array.isArray(entries)) refuse("registerHelp: expects an object of entries");
  for (const [topic, entry] of Object.entries(entries)) {
    if (!TOPIC.test(topic)) refuse("registerHelp: bad help key " + topic);
    if (helps.has(topic)) refuse("registerHelp: \"" + topic + "\" is already registered");
    const ok = entry && typeof entry.title === "string" && typeof entry.hint === "string" && Array.isArray(entry.more);
    if (!ok) refuse("registerHelp: \"" + topic + "\" needs { title, hint, more: [..] }");
    helps.set(topic, entry);
    record(owner, helps, topic);
  }
  changed();
}

export function registeredHelp(topic) { return helps.get(topic) || null; }

// The extensions that did not load (and why), as [{ id, message }]; "extensions" is the manifest itself.
export function extensionFailures() { return [...failures].map(([id, message]) => ({ id, message })); }

function noteFailure(id, message) {
  failures.set(id, message);
  changed();
}

function clearFailure(id) {
  if (failures.delete(id)) changed();
}

// Adds one stylesheet and says when it is applied (or failed, or took too long): the first render waits for it, so
// the page does not flash unstyled. A <link> to the same origin is what style-src 'self' allows.
function linkStylesheet(href) {
  return new Promise((resolve) => {
    const link = document.createElement("link");
    let timer = null;
    const done = () => { clearTimeout(timer); resolve(); };
    link.rel = "stylesheet";
    link.href = href;
    link.addEventListener("load", done);
    link.addEventListener("error", done);
    timer = setTimeout(done, STYLE_WAIT_MS);
    document.head.append(link);
  });
}

// The page's one dynamic import. It never takes a URL: the path is a constant plus an id that already matched NAME and whose
// manifest entry equals /gui/static/extensions/<id>/index.js, so nothing but an extension entry module can be imported.
const importEntry = (url, id) => import(`/gui/static/extensions/${id}/index.js`);

const reason = (error) => String((error && error.message) || error);

// Loads the extensions of the manifest { extensions: [{ id, script, style }] }, all at once (the registries order what they
// hold by `order` and name, so the order of loading does not matter). It never throws and it never waits longer than
// timeoutMs: it answers { loaded, failed } ({ id, message } each, in manifest order) with what was done by then, and
// reports as failed what is still pending. Pending work is not cancelled (the manifest request is aborted): if a module
// finishes later it registers late and its failure is cleared. fetchManifest({ signal }), importModule(url, id) and
// addStylesheet(href) are parameters for tests.
export async function loadExtensions({ fetchManifest, importModule = importEntry, addStylesheet = linkStylesheet,
                                       timeoutMs = LOAD_BUDGET_MS } = {}) {
  const outcomes = new Map();    // position in the manifest (-1: the manifest) -> { id, ok, message }, for what finished in time
  const pending = new Map();     // position -> id, for what is still loading
  const sheets = [];
  const controller = typeof AbortController === "function" ? new AbortController() : { signal: undefined, abort() {} };
  let over = false;              // true once the budget is spent: later results are not part of the answer

  const settle = (position, id, ok, message) => {
    pending.delete(position);
    if (ok) clearFailure(id); else noteFailure(id, message);
    if (!over) outcomes.set(position, { id, ok, message });
  };

  const loadOne = async (entry, position) => {
    const id = entry && entry.id;
    let validEntry = false;
    try {
      const script = ENTRY_URL.exec(String(entry && entry.script));
      if (typeof id !== "string" || !NAME.test(id) || !script || script[1] !== id) refuse("bad manifest entry for " + String(id));
      if (entry.style) {
        const style = STYLE_URL.exec(String(entry.style));
        if (!style || style[1] !== id) refuse("bad stylesheet for " + id);
        sheets.push(Promise.resolve(addStylesheet(entry.style)).catch(() => {}));
      }
      validEntry = true;
      await importModule(entry.script, id);
      settle(position, id, true, "");
    } catch (error) {
      if (validEntry) rollback(id);
      settle(position, String(id), false, reason(error));
    }
  };

  const work = (async () => {
    pending.set(-1, "extensions");
    let manifest = [];
    try {
      const body = await fetchManifest({ signal: controller.signal });
      if (!body || !Array.isArray(body.extensions)) refuse("the list of extensions is not valid");
      manifest = body.extensions;
    } catch (error) {
      settle(-1, "extensions", false, reason(error));
      return;
    }
    pending.delete(-1);
    clearFailure("extensions");
    manifest.forEach((entry, position) => pending.set(position, String((entry && entry.id) || position)));
    await Promise.all(manifest.map(loadOne));
    await Promise.all(sheets);
  })().catch((error) => settle(-1, "extensions", false, reason(error)));

  let timer = null;
  const budget = new Promise((resolve) => { timer = setTimeout(() => resolve("late"), timeoutMs); });
  const state = await Promise.race([work.then(() => "done"), budget]);
  clearTimeout(timer);
  if (state === "late") {
    const message = "still loading after " + timeoutMs + " ms";
    for (const [position, id] of [...pending]) {
      outcomes.set(position, { id, ok: false, message });
      noteFailure(id, message);
    }
    over = true;
    controller.abort();
  }
  const ordered = [...outcomes].sort(([a], [b]) => a - b).map(([, outcome]) => outcome);
  const failed = ordered.filter((outcome) => !outcome.ok).map(({ id, message }) => ({ id, message }));
  for (const item of failed) console.warn("GUI extension " + item.id + " did not load: " + item.message);
  return { loaded: ordered.filter((outcome) => outcome.ok).map((outcome) => outcome.id), failed };
}
