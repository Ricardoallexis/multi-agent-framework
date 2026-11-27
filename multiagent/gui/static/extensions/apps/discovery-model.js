// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure view-model of «Search for apps on this computer» in «Apps y agentes» (EXA-T05). No DOM and no network. Until the person
// answers «Search once», the server measures nothing and its rows say `consent_required`; this module only turns that answer and
// the fixed presets into texts and buttons. Everything from the server is plain text; no command here is ever run by the page.
import "./i18n.js";
import { t } from "../../js/i18n.js";
import { fmtDate } from "../../js/text.js";

export const DISCOVERY_PATH = "/gui/api/connections/discovery";
export const CONSENT_REQUIRED = "consent_required";

const PRESET_LABELS = {
  claude: "Claude Code",
  codex: "Codex CLI",
  copilot: "GitHub Copilot CLI",
  agy: "Antigravity (agy)",
};
const APP_ORDER = ["claude", "codex", "copilot", "agy"];

// A row nobody measured: its reason says so, or it is an activated executor that stays available without a measurement.
export const isUnsearched = (row) => Boolean(row) && row.reason === CONSENT_REQUIRED;
export const wasNotMeasured = (row) => Boolean(row) && (row.reason === CONSENT_REQUIRED || row.discovery === CONSENT_REQUIRED);

export const answerBody = (answer) => ({ answer: answer === "accepted" ? "accepted" : "declined" });

const text = (value) => (typeof value === "string" ? value.trim() : "");

function readLines(reads) {
  const source = reads && typeof reads === "object" ? reads : {};
  return APP_ORDER.filter((app) => Array.isArray(source[app]) && source[app].length > 0)
    .flatMap((app) => source[app].filter(Array.isArray).map((args) => `${app} ${args.map(String).join(" ")}`));
}

// The official install commands of the presets (the same list as the install console). agy has none: it is information only.
export function presetLines(presets, advanced) {
  return (Array.isArray(presets) ? presets : []).filter((item) => item && typeof item.app === "string").map((item) => {
    const name = PRESET_LABELS[item.app] || item.app;
    const command = Array.isArray(item.install?.command) ? item.install.command.map(String).join(" ") : "";
    if (!command) return { app: item.app, name, installable: false, command: null, source: null, sourceDate: null, text: t("ext.apps.discovery.preset_none", { name }) };
    return {
      app: item.app,
      name,
      installable: true,
      text: advanced
        ? t("ext.apps.discovery.preset_command", { name, command })
        : name,
      command: advanced ? command : null,
      source: advanced ? text(item.install.source) : null,
      sourceDate: advanced ? text(item.install.source_date) : null,
    };
  });
}

// What the card draws. `discovery` is GET /gui/api/connections/discovery (or the `discovery` of the PUT answer).
export function discoveryView(discovery, mode = "advanced") {
  if (!discovery || typeof discovery !== "object") return null;
  const advanced = mode !== "simple";
  const answer = discovery.answer === "accepted" || discovery.answer === "declined" ? discovery.answer : null;
  const summary = answer === "accepted" ? t("ext.apps.discovery.summary_accepted")
    : answer === "declined" ? t("ext.apps.discovery.summary_declined")
      : t("ext.apps.discovery.summary");
  const when = text(discovery.at) ? fmtDate(discovery.at) : "";
  const buttons = answer === "accepted"
    ? [{ id: "withdraw", answer: "declined", label: t("ext.apps.discovery.withdraw"), primary: false }]
    : [{ id: "accept", answer: "accepted", label: t("ext.apps.discovery.accept"), primary: true },
      ...(answer === null ? [{ id: "decline", answer: "declined", label: t("ext.apps.discovery.decline"), primary: false }] : [])];
  const lines = [];
  if (advanced) {
    const reads = readLines(discovery.reads);
    if (reads.length) lines.push({ kind: "reads", title: t("ext.apps.discovery.reads_intro"), commands: reads });
    lines.push({ kind: "stored", text: t("ext.apps.discovery.stored", { file: text(discovery.stored_in) || "gui-settings.json" }) });
  }
  return {
    state: answer || "unanswered",
    answer,
    title: t("ext.apps.discovery.title"),
    summary,
    lines,
    note: t("ext.apps.discovery.note"),                       // fixed: what the consent never releases
    answered: answer && when ? t(answer === "accepted" ? "ext.apps.discovery.accepted_at" : "ext.apps.discovery.declined_at", { when }) : null,
    buttons,
    presetsTitle: advanced ? t("ext.apps.discovery.presets_title") : null,
    presetsNote: t("ext.apps.discovery.presets_note"),
    presets: presetLines(discovery.presets, advanced),
  };
}
