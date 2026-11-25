// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import "./i18n.js";
import { t } from "../../js/i18n.js";
// Pure view-model of «Activar» for the executor cards of «Apps y agentes» (EJE-T02): the five states, the fixed note and the
// texts of the answers. No DOM and no network. Everything here is plain text; the note of the server is never HTML.

export const STATES = ["not_installed", "no_session", "ready", "active", "paused"];

const STATE_KEYS = {
  not_installed: "ext.apps.activation.state.not_installed",
  no_session: "ext.apps.activation.state.no_session",
  ready: "ext.apps.activation.state.ready",
  active: "ext.apps.activation.state.active",
  paused: "ext.apps.activation.state.paused",
};
export const STATE_LABELS = Object.defineProperties({}, Object.fromEntries(
  Object.entries(STATE_KEYS).map(([state, key]) => [state, { enumerable: true, get: () => t(key) }]),
));

// The names the server uses in the note (``services/executor_activations.py``).
export const EXECUTOR_NAMES = {
  codex_cli: "Codex",
  claude_cli: "Claude",
  copilot_cli: "Copilot",
  antigravity_cli: "Antigravity (agy)",
};

export const STOP_ALL_CONFIRM = t("ext.apps.activation.stop_all.confirm");
export const stopAllConfirmText = () => t("ext.apps.activation.stop_all.confirm");
const stopAllPausedText = () => t("ext.apps.activation.stop_all.paused");
const foreignKillSwitchText = () => t("ext.apps.activation.foreign_switch");
export const manualEnableText = () => t("ext.apps.activation.manual_enable");

export function executorName(executorId) {
  return EXECUTOR_NAMES[executorId] || String(executorId || t("ext.apps.activation.executor"));
}

const FIELD_TEXT = {
  path: () => t("ext.apps.activation.change.path"),
  major: (change) => t("ext.apps.activation.change.major", { from: change.from, to: change.to }),
  account: () => t("ext.apps.activation.change.account"),
};

// «Cambió la versión 2 → 3»; several changes are joined with « · ».
export function changesText(changes) {
  const list = Array.isArray(changes) ? changes : [];
  const parts = [];
  for (const change of list) {
    if (!change || typeof change !== "object") continue;
    const make = FIELD_TEXT[change.field];
    if (make) parts.push(make(change));
  }
  return parts.join(" · ");
}

// «2026-11-22T03:03:00+00:00» -> «2026-11-22 03:03»: no locale and no timezone maths in a pure module.
function shortDate(value) {
  const match = typeof value === "string" ? /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(value) : null;
  return match ? `${match[1]} ${match[2]}` : null;
}

// The state of an executor when the server did not answer «activations»: what the connection row already says.
function stateFromConnection(row) {
  if (!row) return "not_installed";
  if (row.installed === false || row.reason === "not_installed" || row.missing_step === "install") return "not_installed";
  if (row.missing_step === "session" || row.session === "signed_out") return "no_session";
  return "ready";
}

// -> {state, label, actions, detail}. ``row`` is the connection row, ``activation`` the row of
// GET /gui/api/executors/activations (or null). ``actions`` are among "activate", "deactivate", "review" and "login".
export function activationView(row, activation) {
  const known = activation && STATES.includes(activation.state);
  const state = known ? activation.state : stateFromConnection(row);
  const label = t(STATE_KEYS[state]);
  const foreign = Boolean(activation && activation.kill_switch_foreign);
  let actions = [];
  let detail = "";
  if (state === "no_session") {
    actions = ["login"];
  } else if (state === "ready") {
    actions = ["activate"];
  } else if (state === "active") {
    actions = ["deactivate"];
    const when = shortDate(activation && activation.activated_at);
    detail = when ? t("ext.apps.activation.active_at", { when }) : t("ext.apps.activation.active");
  } else if (state === "paused") {
    actions = ["review"];
    const paused = activation && activation.paused;
    if (paused && paused.reason === "stopped_by_person") {
      detail = stopAllPausedText();
    } else {
      const changes = changesText((paused && paused.changes) || (activation && activation.changes));
      detail = changes || t("ext.apps.activation.paused_changed");
    }
  }
  if (foreign && (state === "ready" || state === "paused")) {
    // A switch a person made by hand: «Activar» would not start anything, so it is not offered.
    actions = actions.filter((action) => action !== "activate" && action !== "review");
    detail = detail ? `${detail}. ${foreignKillSwitchText()}` : foreignKillSwitchText();
  }
  return { state, label, actions, detail, foreignKillSwitch: foreign };
}

// The fixed note of the server, as text, and the button that accepts it: «Activar Codex».
export function activationNote(payload, executorId) {
  const name = executorName(executorId);
  const raw = payload && typeof payload === "object" ? (payload.text ?? payload.note) : "";
  const text = typeof raw === "string" ? raw : "";
  const label = t("ext.apps.activation.activate_name", { name });
  return { title: label, text, lines: text.split("\n").filter((line) => line.trim() !== ""), confirmLabel: label };
}

export function stopAllText(result) {
  const stopped = Array.isArray(result?.stopped_tasks) ? result.stopped_tasks.length : 0;
  const paused = Array.isArray(result?.paused) ? result.paused.length : 0;
  const parts = [
    stopped === 1 ? t("ext.apps.activation.stop_all.stopped_one") : t("ext.apps.activation.stop_all.stopped_many", { count: stopped }),
    paused === 1 ? t("ext.apps.activation.stop_all.paused_one") : t("ext.apps.activation.stop_all.paused_many", { count: paused }),
  ];
  let text = parts.join("; ") + ".";
  if (result?.kill_switch === "foreign") {
    text += " " + t("ext.apps.activation.stop_all.foreign_switch");
  } else if (result?.kill_switch === "failed") {
    text += " " + t("ext.apps.activation.stop_all.write_failed");
  }
  return text;
}
