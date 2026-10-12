// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Logic of the right-panel sections «Cambios», «Tokens» and «Fuentes» (GUI2-T05). Pure functions (no DOM, no Preact,
// no fetch) so they can be checked on their own; panel-data.js loads the data and panel-changes/tokens/sources.js draw it.
//
// Everything comes from what the token-protected /gui/api routes already give:
//   a session of an app  GET /sessions/{id} (summary, tokens), /items (tool calls with the files they name),
//                        /children (the workers and their tokens);
//   a framework run      GET /runs/{id} (steps, artifacts, events) and /runs/{id}/usage (telemetry, cost, price evidence).
// Message text is never read here: only tool names, paths and counts.
import { relativePath } from "./panel-text.js";

export const PAGE_ITEMS = 200;       // items per request, like the session screen
export const PAGES_PER_TICK = 10;    // pages read in one round, so a long session fills in little by little
export const MAX_ITEMS = 20000;      // items analysed per session
export const MAX_PATHS = 300;        // distinct paths kept per list
export const SHOWN = 8;              // rows shown before «Ver más»

const KNOWN = (value) => typeof value === "number" && Number.isFinite(value);
const COUNT = (value) => (Number.isInteger(value) && value >= 0 ? value : null);
const TEXT = (value) => (typeof value === "string" && value.trim() ? value.trim() : null);

// --- what the panel is about ---------------------------------------------------------------------------------------

const RUN_ID = /^[A-Za-z0-9_-]{1,64}$/;
const FRAMEWORK_SESSION = /^framework:([A-Za-z0-9_-]{1,64})$/;

// The session or run open in the centre, or null. `route` is the hash route, `sessionId` the one the shell worked out
// (only for #sessions/<id>). A run (#runs/<id>, or the session "framework:<id>") is read through the run routes.
export function subjectOf(route, sessionId) {
  if (route && route.section === "runs" && route.id) {
    const id = String(route.id);
    return RUN_ID.test(id) ? { kind: "run", runId: id, sessionId: "framework:" + id, key: "run:" + id } : null;
  }
  if (typeof sessionId === "string" && sessionId) {
    const run = FRAMEWORK_SESSION.exec(sessionId);
    if (run) return { kind: "run", runId: run[1], sessionId, key: "run:" + run[1] };
    return { kind: "session", runId: null, sessionId, key: "session:" + sessionId };
  }
  return null;
}

// --- tools ---------------------------------------------------------------------------------------------------------

const WRITE_WORDS = /write|edit|patch|create|replace|insert|delete|rename|save|update/;
const READ_WORDS = /read|view|grep|glob|search|lookup|list|find|notebook/;
const READ_NAMES = new Set(["cat", "ls", "open", "tree", "head", "tail"]);

// What a tool call does to the files it names: write (changes them), read (consults them), fetch (a web page),
// search (a web search) or other. Only the tool name decides; a tool the panel does not know is "other" and counts nowhere.
export function toolClass(name) {
  const key = String(name || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  if (!key) return "other";
  if (key.includes("websearch")) return "search";
  if (/fetch|browse|openurl/.test(key)) return "fetch";
  const reads = READ_WORDS.test(key) || READ_NAMES.has(key);
  const writes = WRITE_WORDS.test(key);
  if (writes) return "write";
  return reads ? "read" : "other";
}

// --- paths and urls ------------------------------------------------------------------------------------------------

// Same file whatever the separators; a Windows drive letter compares without case.
export function pathKey(path) {
  let key = String(path).trim().replace(/\\/g, "/");
  if (/^[A-Za-z]:\//.test(key)) key = key.toLowerCase();
  return key.replace(/\/+$/, "") || key;
}

// scheme + host + path of a web address. The query and the fragment can carry tokens, so they are dropped.
export function cleanUrl(raw) {
  const text = String(raw || "").trim().replace(/[,;]+$/, "");
  if (!/^https?:\/\//i.test(text)) return null;
  try {
    const url = new URL(text);
    const path = url.pathname === "/" ? "" : url.pathname;
    return (url.protocol + "//" + url.host + path).slice(0, 200);
  } catch (_) {
    return null;
  }
}

// The tool summary the readers give is «key=value, key=value»: the address of a fetch is its «url=» value.
export function urlOfSummary(summary) {
  const found = /(?:^|[\s,])url=(\S+)/.exec(String(summary || ""));
  return found ? cleanUrl(found[1]) : null;
}

// --- the digest of a session's items -------------------------------------------------------------------------------

export function newDigest() {
  return { items: 0, tools: 0, edits: new Map(), reads: new Map(), urls: new Map(), searches: 0, attachments: 0, more: false, seq: 0 };
}

// Entry of `map` for `path`, moved to the end (the most recently touched last). A new path past MAX_PATHS is not kept.
function touch(digest, map, path, make) {
  const key = pathKey(path);
  if (!key) return null;
  let entry = map.get(key);
  if (entry === undefined) {
    if (map.size >= MAX_PATHS) { digest.more = true; return null; }
    entry = make(String(path).trim().slice(0, 300));
  } else {
    map.delete(key);
  }
  entry.seq = ++digest.seq;
  map.set(key, entry);
  return entry;
}

// Optional per-file counts a reader may attach to a tool call: item.changes = [{ path, additions, deletions }].
// The readers do not send it yet; when they do, the panel shows the counts with no change here.
function statsOf(item) {
  const stats = new Map();
  for (const change of Array.isArray(item.changes) ? item.changes : []) {
    const path = change && TEXT(change.path);
    if (path) stats.set(pathKey(path), { path, additions: COUNT(change.additions), deletions: COUNT(change.deletions) });
  }
  return stats;
}

function addToolCall(digest, item) {
  const tool = item.tool && typeof item.tool === "object" ? item.tool : {};
  const kind = toolClass(tool.name);
  digest.tools += 1;
  if (kind === "search") { digest.searches += 1; return; }
  if (kind === "fetch") {
    const url = urlOfSummary(tool.summary);
    if (url) {
      const entry = touch(digest, digest.urls, url, (value) => ({ url: value, count: 0, seq: 0 }));
      if (entry) entry.count += 1;
    }
    return;
  }
  const files = (Array.isArray(item.files) ? item.files : []).filter((file) => typeof file === "string" && file.trim());
  if (kind === "write") {
    const stats = statsOf(item);
    const paths = [...new Set(files.map((file) => file.trim()).concat([...stats.values()].map((stat) => stat.path)))];
    for (const path of paths) {
      const entry = touch(digest, digest.edits, path, (value) => ({ path: value, edits: 0, additions: null, deletions: null, tools: new Set(), seq: 0 }));
      if (!entry) continue;
      entry.edits += 1;
      if (tool.name) entry.tools.add(String(tool.name).slice(0, 40));
      const stat = stats.get(pathKey(path));
      if (stat && stat.additions !== null) entry.additions = (entry.additions || 0) + stat.additions;
      if (stat && stat.deletions !== null) entry.deletions = (entry.deletions || 0) + stat.deletions;
    }
  } else if (kind === "read") {
    for (const path of files) {
      const entry = touch(digest, digest.reads, path, (value) => ({ path: value, count: 0, seq: 0 }));
      if (entry) entry.count += 1;
    }
  }
}

// Adds a page of items to the digest (in place). Only tool calls and the markers of attachments are looked at.
export function addItems(digest, items) {
  for (const item of Array.isArray(items) ? items : []) {
    digest.items += 1;
    if (!item || typeof item !== "object") continue;
    if (item.kind === "tool_call") addToolCall(digest, item);
    else if ((item.kind === "user_message" || item.kind === "assistant_message") && /^\[(image|document)\]$/.test(item.text || "")) {
      digest.attachments += 1;
    }
  }
  return digest;
}

// Plain data of the digest for the views, the most recently touched first.
export function snapshotDigest(digest) {
  const newest = (map) => [...map.values()].reverse();
  return {
    items: digest.items,
    tools: digest.tools,
    edits: newest(digest.edits).map((entry) => ({ ...entry, tools: [...entry.tools] })),
    reads: newest(digest.reads).map((entry) => ({ ...entry })),
    urls: newest(digest.urls).map((entry) => ({ ...entry })),
    searches: digest.searches,
    attachments: digest.attachments,
    more: digest.more,
  };
}

// --- Cambios -------------------------------------------------------------------------------------------------------

// Where a file list of an artifact may be: result.files, changed_files or files_changed, as strings or
// { path | file | filename | name, additions | added | insertions, deletions | removed }. A convention a preset can follow.
const FILE_LISTS = ["files", "changed_files", "files_changed"];

function artifactFile(entry) {
  if (typeof entry === "string") return TEXT(entry) ? { path: entry.trim().slice(0, 300), additions: null, deletions: null } : null;
  if (!entry || typeof entry !== "object") return null;
  const path = TEXT(entry.path) || TEXT(entry.file) || TEXT(entry.filename) || TEXT(entry.name);
  if (!path) return null;
  const pick = (...names) => { for (const name of names) { const value = COUNT(entry[name]); if (value !== null) return value; } return null; };
  return { path: path.slice(0, 300), additions: pick("additions", "added", "insertions"), deletions: pick("deletions", "removed") };
}

export function artifactFiles(data) {
  const files = [];
  if (!data || typeof data !== "object") return files;
  for (const name of FILE_LISTS) {
    if (!Array.isArray(data[name])) continue;
    for (const entry of data[name].slice(0, 200)) {
      const file = artifactFile(entry);
      if (file) files.push(file);
    }
  }
  return files;
}

function totalsOf(files) {
  let additions = 0;
  let deletions = 0;
  let counted = 0;
  for (const file of files) {
    if (file.additions === null && file.deletions === null) continue;
    counted += 1;
    additions += file.additions || 0;
    deletions += file.deletions || 0;
  }
  return { files: files.length, edits: files.reduce((sum, file) => sum + file.edits, 0), additions, deletions, counted };
}

// {files, totals, artifacts, more}: the files changed by the session (tool calls that write) or by the run (files its
// artifacts list). `artifacts` is how many results the run has, to say so when none of them lists a file.
export function changesModel(subject, data) {
  if (subject.kind === "run") {
    const found = new Map();
    const artifacts = data.run && Array.isArray(data.run.artifacts) ? data.run.artifacts : [];
    for (const artifact of artifacts) {
      for (const file of artifactFiles(artifact.data)) {
        const key = pathKey(file.path);
        const before = found.get(key);
        found.set(key, { path: file.path, edits: (before ? before.edits : 0) + 1, additions: file.additions, deletions: file.deletions, tools: [] });
      }
    }
    const files = [...found.values()].reverse();
    return { files, totals: totalsOf(files), artifacts: artifacts.length, more: false };
  }
  const files = data.view ? data.view.edits : [];
  return { files, totals: totalsOf(files), artifacts: 0, more: Boolean(data.view && data.view.more) };
}

// --- Fuentes -------------------------------------------------------------------------------------------------------

// Session: the folder, the files it consulted, the web pages it fetched, searches and attachments.
// Run: the workflow, the prompts and contracts its steps use, and the names of its inputs (never their values).
export function sourcesModel(subject, data) {
  if (subject.kind === "run") {
    const run = data.run || {};
    const events = Array.isArray(run.events) ? run.events : [];
    const prompts = new Map();
    for (const event of events) {
      const payload = event && event.type === "step_started" && event.payload && typeof event.payload === "object" ? event.payload : null;
      if (!payload || !TEXT(payload.prompt_id)) continue;
      const version = Number.isInteger(payload.prompt_version) ? payload.prompt_version : null;
      prompts.set(payload.prompt_id + "@" + version, { id: payload.prompt_id, version });
    }
    const contracts = [...new Set((Array.isArray(run.steps) ? run.steps : []).map((step) => TEXT(step && step.contract)).filter(Boolean))];
    return {
      kind: "run",
      workflow: TEXT(run.workflow_id), bundle: TEXT(run.bundle),
      prompts: [...prompts.values()], contracts,
      inputs: Object.keys(run.inputs && typeof run.inputs === "object" ? run.inputs : {}),
      partial: KNOWN(run.events_total) && run.events_total > events.length,
    };
  }
  const view = data.view || { reads: [], urls: [], searches: 0, attachments: 0, more: false };
  const folder = data.summary && TEXT(data.summary.cwd);
  return {
    kind: "session", folder,
    files: view.reads.map((entry) => ({ ...entry, shown: relativePath(entry.path, folder) })),
    urls: view.urls, searches: view.searches, attachments: view.attachments, more: view.more,
  };
}

export function sourcesCount(model) {
  if (model.kind === "run") return (model.workflow ? 1 : 0) + model.prompts.length + model.contracts.length + model.inputs.length;
  return (model.folder ? 1 : 0) + model.files.length + model.urls.length + model.searches + model.attachments;
}

// --- Tokens --------------------------------------------------------------------------------------------------------

const TOKEN_KEYS = ["input", "output", "cache_read", "cache_write", "total"];
const hasTokens = (tokens) => Boolean(tokens) && typeof tokens === "object" && TOKEN_KEYS.some((key) => KNOWN(tokens[key]));

// Sum of the token counts that were reported. A kind nobody reported stays null (never 0); `unreported` counts the
// entries that gave nothing at all.
export function sumTokens(list) {
  const sum = { input: null, output: null, cache_read: null, cache_write: null, total: null, reported: 0, unreported: 0 };
  for (const tokens of list) {
    if (!hasTokens(tokens)) { sum.unreported += 1; continue; }
    sum.reported += 1;
    for (const key of TOKEN_KEYS) if (KNOWN(tokens[key])) sum[key] = (sum[key] || 0) + tokens[key];
  }
  return sum;
}

const orNull = (sum) => (sum.reported > 0 ? sum : null);

function group(children) {
  const groups = new Map();
  for (const child of children) {
    const model = TEXT(child.model) || "modelo sin indicar";
    const key = model + "|" + (child.level || "");
    if (!groups.has(key)) groups.set(key, { model, level: child.level || null, children: [] });
    groups.get(key).children.push(child);
  }
  return [...groups.values()].map((entry) => {
    const sum = sumTokens(entry.children.map((child) => child.tokens));
    return { model: entry.model, level: entry.level, count: entry.children.length, tokens: orNull(sum), unreported: sum.unreported };
  });
}

// Orchestrator against workers for a session of an app. The session itself is the orchestrator (its own tokens); the
// workers are its subagents and threads (/children). When the open session is itself a subagent it has no workers.
export function sessionTokens(summary, children) {
  const list = children && Array.isArray(children.children) ? children.children : null;
  const sub = Boolean(summary && summary.parent_id);
  const main = hasTokens(summary && summary.tokens) ? summary.tokens : null;
  const workers = list === null ? null : sumTokens(list.map((child) => child.tokens));
  const all = sumTokens([main].concat(list === null ? [] : list.map((child) => child.tokens)));
  return {
    kind: "session",
    orchestrator: { label: sub ? "Esta conversación (subagente)" : "Orquestador (la sesión)", models: summary ? summary.models || [] : [], tokens: main },
    workers: { label: "Trabajadores", count: list === null ? null : list.length, tokens: workers ? orNull(workers) : null,
      unreported: workers ? workers.unreported : 0, readable: list !== null },
    total: orNull(all), incomplete: all.unreported > 0 || list === null,
    byModel: list === null ? [] : group(list),
    cost: { state: "unknown", amount: null, currency: "USD", unknownCalls: 0, other: [], evidence: [], reason: "app" },
  };
}

// Evidence of the price used, once per model (the latest call that has it).
export function priceEvidence(records) {
  const byModel = new Map();
  for (const record of Array.isArray(records) ? records : []) {
    const basis = record && record.cost_basis;
    if (!basis || typeof basis !== "object" || !basis.input || !basis.output) continue;
    const model = TEXT(record.model) || TEXT(basis.model_id) || "modelo sin indicar";
    const before = byModel.get(model);
    byModel.set(model, { model, currency: TEXT(basis.currency) || "USD", input: basis.input, output: basis.output,
      calls: (before ? before.calls : 0) + 1, pricedAt: TEXT(basis.priced_at) });
  }
  return [...byModel.values()];
}

// The cost of a run from its telemetry totals: unknown (never 0) when no call has a price; partial when some do not.
export function costOf(totals, records) {
  const usd = totals && KNOWN(totals.estimated_cost_usd) ? totals.estimated_cost_usd : null;
  const unknownCalls = totals && KNOWN(totals.cost_unknown_records) ? totals.cost_unknown_records : 0;
  const other = Object.entries((totals && totals.cost_other_currencies) || {})
    .filter(([, amount]) => KNOWN(amount)).map(([currency, amount]) => ({ currency, amount }));
  const calls = totals && KNOWN(totals.records) ? totals.records : 0;
  let state = "unknown";
  if (usd !== null || other.length > 0) state = unknownCalls > 0 ? "partial" : "known";   // currencies are kept apart, never summed
  return { state, amount: usd, currency: "USD", unknownCalls, other, evidence: priceEvidence(records), reason: calls === 0 ? "none" : "run" };
}

// Orchestrator against workers for a framework run. The workflow engine only coordinates and uses no model; every
// provider call belongs to a step, so the telemetry is the workers' work.
export function runTokens(usage) {
  const totals = usage && usage.totals ? usage.totals : null;
  const run = totals && totals.run ? totals.run : null;
  const sum = run ? { input: KNOWN(run.tokens_in) ? run.tokens_in : null, output: KNOWN(run.tokens_out) ? run.tokens_out : null,
    cache_read: null, cache_write: null, total: null, reported: 0, unreported: 0 } : null;
  const reported = Boolean(sum) && (sum.input !== null || sum.output !== null);
  const byModel = (totals && Array.isArray(totals.by_model) ? totals.by_model : []).map((entry) => ({
    model: TEXT(entry.model) || "modelo sin indicar", level: null, count: KNOWN(entry.records) ? entry.records : 0,
    tokens: KNOWN(entry.tokens_in) || KNOWN(entry.tokens_out)
      ? { input: KNOWN(entry.tokens_in) ? entry.tokens_in : null, output: KNOWN(entry.tokens_out) ? entry.tokens_out : null } : null,
    unreported: KNOWN(entry.records_without_usage) ? entry.records_without_usage : 0,
  }));
  const calls = run && KNOWN(run.records) ? run.records : 0;
  return {
    kind: "run",
    orchestrator: { label: "Orquestador (el motor del workflow)", models: [], tokens: null, noModel: true },
    workers: { label: "Trabajadores (los pasos)", count: calls, tokens: reported ? sum : null,
      unreported: run && KNOWN(run.records_without_usage) ? run.records_without_usage : 0, readable: usage !== null, failed: run && KNOWN(run.failed) ? run.failed : 0 },
    total: reported ? sum : null, incomplete: Boolean(run) && KNOWN(run.records_without_usage) && run.records_without_usage > 0,
    byModel,
    cost: costOf(run, usage && usage.records),
  };
}

export function tokensModel(subject, data) {
  return subject.kind === "run" ? runTokens(data.usage) : sessionTokens(data.summary, data.children);
}
