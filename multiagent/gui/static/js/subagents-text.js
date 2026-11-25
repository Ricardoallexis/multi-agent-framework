// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Texts and formatters of the subagents panel. The server sends codes and facts; the wording lives here.
import { relativeTime, sourceLabel, warningText } from "./session-text.js";
import { t, currentLanguage, onLanguageChange } from "./i18n.js";
import { labelEntry, labelSet, legacyInfo } from "./ui/labels.js";

export const PAGE_SIZE = 10; // children shown per tab before Show more

export const GROUPS = [
  { id: "active", get label() { return t("subagents.active"); }, get empty() { return t("subagents.active_empty"); } },
  { id: "done", get label() { return t("subagents.done"); }, get empty() { return t("subagents.done_empty"); } },
];

// Same split as the server (sessions/children.py): active = running, testing, idle; the rest is finished.
const ACTIVE = ["running", "testing", "idle"];
export const groupOf = (status) => (ACTIVE.includes(status) ? "active" : "done");

const NOTES = { waiting_human: "subagents.waiting_human" };

// {label, tone, help} of a child status (the words are in ui/labels.js, set "subagent"); a note can replace the label.
export function statusInfo(status, note) {
  const base = legacyInfo("subagent", status);
  return NOTES[note] ? { ...base, label: t(NOTES[note]) } : base;
}

// What <StatusChip labels=...> gets for a child: the shared "subagent" set, or a one-status map when a note renames the label.
export function childLabels(child) {
  if (!NOTES[child.note]) return "subagent";
  const entry = labelEntry(labelSet("subagent"), child.status);
  return { [child.status]: { ...entry, text: t(NOTES[child.note]), explanation: entry.explanation } };
}

const KIND_NAME = { subagent: "subagents.subagent", thread: "subagents.thread", step: "subagents.step" };

function shortId(child) {
  const tail = String(child.id).split(/[:#]/).pop() || "";
  return tail.replace(/^agent-/, "").slice(0, 8);
}

// What names the child: the description the app gave it, else its type or nickname, else a generic name.
export function childTitle(child) {
  if (child.label) return child.label;
  if (child.name) return child.name;
  return (KIND_NAME[child.kind] ? t(KIND_NAME[child.kind]) : t("subagents.agent")) + " " + shortId(child);
}

// Two letters for the avatar: the initials of the title, or the app's own when there is nothing to take them from.
export function initials(child) {
  const words = String(childTitle(child)).split(/[\s\-_·:]+/).filter((word) => /^[\p{L}\p{N}]/u.test(word));
  const letters = words.slice(0, 2).map((word) => word.charAt(0).toUpperCase()).join("");
  return letters || sourceLabel(child.source).slice(0, 2);
}

const number = (value, digits) => value.toLocaleString(currentLanguage(), { maximumFractionDigits: digits });

// "1,2 M tokens"; null (not reported) is never shown as 0.
export function compactTokens(tokens) {
  if (!tokens) return t("subagents.unknown_tokens");
  const known = (value) => typeof value === "number" && Number.isFinite(value);
  let total = null;
  if (known(tokens.total)) total = tokens.total;
  else if (known(tokens.input) || known(tokens.output)) total = (known(tokens.input) ? tokens.input : 0) + (known(tokens.output) ? tokens.output : 0);
  if (total === null) return t("subagents.unknown_tokens");
  if (total >= 1e6) return number(total / 1e6, 1) + " M tokens";
  if (total >= 1e3) return number(total / 1e3, 1) + " k tokens";
  return number(total, 0) + " tokens";
}

// Second line: the type (when the title is the description), the model and the level. The panel draws the level as a
// LevelBadge (ui/level-badge.js), so it asks for withLevel = false; the text form stays for anything that has no badge.
export function detailText(child, withLevel = true) {
  const parts = [];
  if (child.label && child.name && child.name !== child.label) parts.push(child.name);
  parts.push(child.model || t("subagents.no_model"));
  if (withLevel && child.level) parts.push(t("subagents.level") + child.level);
  return parts.join(" · ");
}

// Third line, after the status label: when it last did something and how many tokens it used.
export function activityText(child, now = Date.now()) {
  const when = child.last_activity_at ? t("subagents.activity") + relativeTime(child.last_activity_at, now) : t("subagents.no_time");
  return when + " · " + compactTokens(child.tokens);
}

export function countsText(counts) {
  const active = counts ? counts.active : 0;
  const done = counts ? counts.done : 0;
  return active + (active === 1 ? t("subagents.active_one") : t("subagents.active_many")) + t("subagents.and") + done + (done === 1 ? t("subagents.done_one") : t("subagents.done_many"));
}

export let NO_CONVERSATION = t("subagents.no_conversation");
onLanguageChange(() => { NO_CONVERSATION = t("subagents.no_conversation"); });

// Reader warnings: the ones of this panel first, the generic ones of the sessions view otherwise.
export function childrenWarning(warning) {
  const text = String(warning);
  if (text.startsWith("children_truncated")) return t("subagents.truncated");
  return warningText(text);
}
