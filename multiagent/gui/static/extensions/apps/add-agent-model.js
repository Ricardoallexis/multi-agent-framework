// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure view-model of «Add an agent» in «Apps y agentes» (EXA-T06): the form body, what the server refused and why, and the card of
// each agent of the person. No DOM and no network. The name, the program, the arguments and what the agent announced come from the
// server or from the agent: everything here is plain text, never HTML, and nothing here runs, checks or activates anything.
import "./i18n.js";
import { t } from "../../js/i18n.js";
import { changesText } from "./activation-model.js";

export const AGENTS_PATH = "/gui/api/acp-agents";
export const agentPath = (id, action = "") => `${AGENTS_PATH}/${encodeURIComponent(id)}${action ? "/" + action : ""}`;
export const CUSTOMIZE_HREF = "#customize";

const text = (value) => (typeof value === "string" ? value.trim() : "");
const details = (failure) => (failure && typeof failure.details === "object" && failure.details ? failure.details : {});
const sameList = (a, b) => a.length === b.length && a.every((item, index) => item === b[index]);

// --- the form ---------------------------------------------------------------------------------------------------------------
// One argument per line. The empty lines at the end are not arguments; an empty line in the middle is sent and the server says which one.
export function parseArgs(value) {
  const lines = String(value ?? "").split(/\r\n|\r|\n/);
  while (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}
export const argsText = (args) => (Array.isArray(args) ? args.filter((item) => typeof item === "string").join("\n") : "");

export const emptyForm = () => ({ name: "", executable: "", args: "" });
export const formOf = (agent) => ({ name: agent.name, executable: agent.executable, args: argsText(agent.args) });
export const canSave = (form) => Boolean(form) && text(form.name) !== "" && text(form.executable) !== "";

// POST /gui/api/acp-agents: the id is optional and the server makes it from the name, so none is sent.
export const addBody = (form) => ({ name: text(form.name), executable: text(form.executable), args: parseArgs(form.args) });

// PUT /gui/api/acp-agents/{id}: only what changed. `{}` means there is nothing to save.
export function editBody(agent, form) {
  const body = {};
  if (text(form.name) !== agent.name) body.name = text(form.name);
  if (text(form.executable) !== agent.executable) body.executable = text(form.executable);
  const args = parseArgs(form.args);
  if (!sameList(args, agent.args)) body.args = args;
  return body;
}

// What saving will do, said before it is saved: a change of the program or its arguments pauses the activation and clears the check;
// a change of the name alone touches neither, and the text does not say otherwise.
export function editNotice(agent, form) {
  const body = editBody(agent, form);
  if ("executable" in body || "args" in body) return { kind: "pauses", text: t("ext.apps.agents.edit.pauses") };
  if ("name" in body) return { kind: "name_only", text: t("ext.apps.agents.edit.name_only") };
  return { kind: "none", text: "" };
}

// --- what the server refused --------------------------------------------------------------------------------------------------
// `details.reason` is the code the server gives; each one has its sentence. An unknown code is never a blank: it says the code.
const REASON_KEYS = {
  executable_not_absolute: "executable_not_absolute",
  executable_has_wildcard: "executable_has_wildcard",
  executable_has_shell_characters: "executable_has_shell_characters",
  executable_is_shell_script: "executable_is_shell_script",
  executable_is_shell: "executable_is_shell",
  executable_not_found: "executable_not_found",
  executable_in_writable_folder: "executable_in_writable_folder",
  argument_has_placeholder: "argument_has_placeholder",
  argument_has_shell_syntax: "argument_has_shell_syntax",
  argument_invalid: "argument_invalid",
  argument_in_writable_folder: "argument_in_writable_folder",
  argument_relative_path: "argument_relative_path",
  too_many_arguments: "too_many_arguments",
  name_invalid: "name_invalid",
  invalid_agent_id: "agent_id",
  agent_id_reserved: "agent_id",
  agent_exists: "agent_id",
  too_many_agents: "too_many_agents",
};
export const REASONS = Object.keys(REASON_KEYS);

// «Argument 2: …» when the server names the argument (`details.argument`, counted from 1).
export function reasonText(reason, argument = null) {
  const key = REASON_KEYS[reason];
  if (!key) return "";
  const base = t("ext.apps.agents.reason." + key);
  return Number.isInteger(argument) && argument > 0 ? t("ext.apps.agents.argument_n", { position: String(argument), text: base }) : base;
}

export const isAgentGone = (failure) => Boolean(failure) && failure.status === 404 && details(failure).reason === "acp_agent_not_found";

export function failureText(failure) {
  if (isAgentGone(failure)) return t("ext.apps.agents.gone");
  const reason = text(details(failure).reason);
  const known = reasonText(reason, details(failure).argument);
  if (known) return known;
  if (reason) return t("ext.apps.agents.reason.unknown", { code: reason });
  return (failure && failure.message) || t("ext.apps.agents.failed");
}

// --- the card of an agent ----------------------------------------------------------------------------------------------------
export const STATES = ["disabled", "active", "paused"];
const CHECK_STATUSES = ["compatible", "incompatible", "not_installed", "no_response", "invalid_response"];
const show = (value) => (typeof value === "string" || typeof value === "number" ? String(value) : "?");

// «Changed: the arguments · the program path. Review it again.» The file of the program has its own sentence (pause `file`).
export function agentChangesText(changes) {
  const list = Array.isArray(changes) ? changes.filter((change) => change && typeof change === "object") : [];
  const parts = [];
  let file = false;
  for (const change of list) {
    if (change.field === "file") file = true;
    else if (change.field === "args") parts.push(t("ext.apps.agents.change.args"));
    else if (change.field === "version") parts.push(t("ext.apps.agents.change.version", { from: show(change.from), to: show(change.to) }));
    else {
      const known = changesText([change]);
      if (known) parts.push(known);
    }
  }
  return [parts.length ? t("ext.apps.agents.changed", { changes: parts.join(" · ") }) : "", file ? t("ext.apps.agents.changed_file") : ""]
    .filter(Boolean).join(" ");
}

const clip = (value, limit = 80) => (value.length > limit ? value.slice(0, limit) + "…" : value);

// What the last check found, from the bounded evidence of the server. What the agent announced is read as plain strings only.
function checkFacts(row) {
  const probe = row.probe && typeof row.probe === "object" ? row.probe : null;
  if (!probe) return [{ id: "check", label: t("ext.apps.agents.fact.check"), value: t("ext.apps.agents.check.none") }];
  const status = CHECK_STATUSES.includes(probe.status) ? t("ext.apps.agents.check." + probe.status) : show(probe.status);
  const facts = [{ id: "check", label: t("ext.apps.agents.fact.check"), value: status }];
  if (probe.protocol_version !== null && probe.protocol_version !== undefined && probe.protocol_version !== "") {
    facts.push({ id: "protocol", label: t("ext.apps.agents.fact.protocol"), value: clip(show(probe.protocol_version)) });
  }
  const info = probe.agent_info && typeof probe.agent_info === "object" ? probe.agent_info : null;
  const announced = info ? [text(info.name) || text(info.title), text(info.version)].filter(Boolean).join(" ") : "";
  if (announced) facts.push({ id: "announces", label: t("ext.apps.agents.fact.announces"), value: clip(announced) });
  return facts;
}

// One row of GET /gui/api/acp-agents, as the card draws it. `actions` are among check, activate, deactivate, edit and remove;
// «Start a conversation» is `canStart`, and only an active, valid and checked agent has it.
export function agentView(row) {
  const source = row && typeof row === "object" ? row : {};
  const id = text(source.id);
  const valid = source.valid !== false;
  const state = STATES.includes(source.state) ? source.state : "disabled";
  const executable = typeof source.executable === "string" ? source.executable : "";
  const args = Array.isArray(source.args) ? source.args.filter((item) => typeof item === "string") : [];
  const missingProgram = valid && source.installed === false;
  const probeRequired = valid && !missingProgram && source.probe_required === true;
  const paused = source.paused && typeof source.paused === "object" ? source.paused : null;
  const lines = [];
  if (!valid) {
    const why = reasonText(text(source.reason)) || (text(source.reason) ? t("ext.apps.agents.reason.unknown", { code: text(source.reason) }) : t("ext.apps.agents.failed"));
    lines.push({ id: "invalid", tone: "error", text: t("ext.apps.agents.invalid", { reason: why.replace(/\.$/, "") }) });
  }
  if (missingProgram) lines.push({ id: "missing", tone: "error", text: t("ext.apps.agents.missing") });
  if (valid && state === "paused") {
    const changed = paused && paused.reason === "stopped_by_person" ? t("ext.apps.activation.stop_all.paused")
      : agentChangesText((paused && paused.changes) || source.changes);
    lines.push({ id: "paused", tone: "note", text: changed || t("ext.apps.activation.paused_changed") });
  }
  if (probeRequired && state !== "active") lines.push({ id: "check_first", tone: "note", text: t("ext.apps.agents.check_first") });
  const actions = [];
  if (valid && !missingProgram) actions.push("check");
  if (valid && !missingProgram && state !== "active" && !probeRequired) actions.push("activate");
  if (valid && state === "active") actions.push("deactivate");
  actions.push("edit", "remove");
  return {
    id,
    name: text(source.name) || id,
    executable,
    args,
    valid,
    state: valid ? state : "invalid",
    stateLabel: t("ext.apps.agents.state." + (valid ? state : "invalid")),
    facts: valid ? checkFacts(source) : [],
    lines,
    actions,
    activateReview: state === "paused",
    canStart: valid && state === "active" && !missingProgram && source.probed === true && source.probe_required !== true,
    scopeLine: t("ext.apps.agents.scope"),
  };
}

// GET /gui/api/acp-agents -> the cards. A row without an id is dropped.
export function agentRows(payload) {
  const list = payload && Array.isArray(payload.agents) ? payload.agents : [];
  return list.filter((row) => row && typeof row.id === "string" && row.id).map(agentView);
}

// A probe answers with the updated row: the list keeps its place.
export function replaceAgent(views, row) {
  if (!row || typeof row.id !== "string") return views;
  return views.map((view) => (view.id === row.id ? agentView(row) : view));
}

// --- the fixed texts ----------------------------------------------------------------------------------------------------------
export const fixedNote = () => t("ext.apps.agents.note");
export const removeConfirmText = () => t("ext.apps.agents.remove.confirm");
export const advancedOnlyText = () => t("ext.apps.agents.advanced_only");
