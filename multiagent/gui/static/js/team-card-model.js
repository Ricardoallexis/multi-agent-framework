// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// CHT-T05: pure text of the team card (no DOM, no network). The server sends the version with its `card` (per role: rules,
// permissions, and the model its agent resolves to in the catalog; plus the team mode) and its `diff` against the approved
// version, or against an empty team when there is none. Everything here returns plain text or plain data.

import { t } from "./i18n.js";

export const MODE_TEXT = {
  get supervised() { return t("teams.mode.supervised"); },
  get autonomous() { return t("teams.mode.autonomous"); },
};
export const PERMISSION_TEXT = {
  get read() { return t("teams.permission.read"); },
  get write() { return t("teams.permission.write"); },
  get destructive() { return t("teams.permission.destructive"); },
  get external() { return t("teams.permission.external"); },
};
export const MODEL_STATUS_TEXT = {
  get no_agent() { return t("teams.model.no_agent"); },
  get agent_not_in_catalog() { return t("teams.model.agent_not_in_catalog"); },
  get no_step() { return t("teams.model.no_step"); },
  get not_in_catalog() { return t("teams.model.not_in_catalog"); },
  get partial() { return t("teams.model.partial"); },
  get unavailable() { return t("teams.model.unavailable"); },
};
export const SECTION_TEXT = {
  get roles() { return t("teams.section.roles"); },
  get members() { return t("teams.section.members"); },
  get assignments() { return t("teams.section.assignments"); },
  get relationships() { return t("teams.section.relationships"); },
};
export const LINK_TEXT = {
  get reviews() { return t("teams.link.reviews"); },
  get delegates_to() { return t("teams.link.delegates_to"); },
  get reports_to() { return t("teams.link.reports_to"); },
};
const BUCKET_TEXT = {
  get added() { return t("teams.bucket.added"); },
  get removed() { return t("teams.bucket.removed"); },
  get changed() { return t("teams.bucket.changed"); },
};
const HEADER_TEXT = {
  get id() { return t("teams.header.id"); },
  get description() { return t("teams.header.description"); },
  get schema_version() { return t("teams.header.schema_version"); },
  get mode() { return t("teams.header.mode"); },
};

export const modeText = (mode) => MODE_TEXT[mode] || (mode ? String(mode) : t("teams.mode.none"));
export const permissionText = (permissions) => Array.isArray(permissions) && permissions.length
  ? permissions.map((item) => PERMISSION_TEXT[item] || String(item)).join(", ") : t("teams.permission.none");

// One model as "gemini-x (proveedor, nivel)"; the catalog id is part of it so a person can find it in models.yaml.
export const modelLine = (model) => !model ? "" : String(model.model || model.id) +
  " (" + [model.id, model.provider, model.tier].filter(Boolean).join(", ") + ")";

// What the card says about the model of a role: the resolved models first, then the reason when something is missing.
export function modelSummary(role) {
  const info = (role && role.model) || { status: "unavailable", models: [], missing: [] };
  const lines = (info.models || []).map(modelLine);
  const notes = [];
  if (info.status !== "resolved" && MODEL_STATUS_TEXT[info.status]) notes.push(MODEL_STATUS_TEXT[info.status]);
  if ((info.missing || []).length) notes.push(t("teams.model.missing", { models: info.missing.join(", ") }));
  return { status: info.status || "unavailable", lines, notes, ok: info.status === "resolved" };
}

export const membersLine = (members) => Array.isArray(members) && members.length
  ? members.map((member) => (member.name || member.id) + " (" + (member.kind === "agent"
    ? t("teams.member.agent", { agent: member.agent || "" }) : t("teams.member.person")) + ")").join(", ")
  : t("teams.member.none");

// The rows of one role of the card, as label/value text; an empty list says so instead of leaving a blank.
export function roleRows(role) {
  const list = (items, empty) => Array.isArray(items) && items.length ? items.join("; ") : empty;
  const model = modelSummary(role);
  return {
    id: String(role.id),
    purpose: role.purpose || "",
    rows: [
      { key: "rules", label: t("teams.card.rules"), text: list(role.rules, t("teams.card.no_rules")) },
      { key: "permissions", label: t("teams.card.permissions"), text: permissionText(role.permissions) },
      { key: "model", label: t("teams.card.model"), text: model.lines.length ? model.lines.join("; ") : t("teams.card.no_model") },
      { key: "members", label: t("teams.position.has"), text: membersLine(role.members) },
    ],
    notes: model.notes,
    modelStatus: model.status,
  };
}

export const cardRoles = (card) => Array.isArray(card && card.roles) ? card.roles.map(roleRows) : [];

const fieldValue = (value) => value === null || value === undefined ? t("teams.value.empty") : typeof value === "string" ? value : JSON.stringify(value);
export const fieldChanges = (fields) => Object.keys(fields || {}).map((name) =>
  (HEADER_TEXT[name] || name) + ": " + fieldValue(fields[name].before) + " → " + fieldValue(fields[name].after));

// One line for an added or removed item of a diff section; a role also says its rules and permissions.
export function itemLine(section, item) {
  if (!item) return "";
  if (section === "assignments") return item.member + " " + t("teams.diff.in") + " " + item.role;
  if (section === "relationships") return item.from + " " + (LINK_TEXT[item.type] || item.type) + " " + item.to;
  if (section === "roles") {
    const rules = Array.isArray(item.rules) ? item.rules.length : 0;
    return item.id + " (" + t(rules === 1 ? "teams.item.one_rule" : "teams.item.many_rules", { count: rules })
      + "; " + t("teams.card.permissions").toLowerCase() + ": " + permissionText(item.permissions) + ")";
  }
  if (section === "members") return item.id + " (" + (item.kind === "agent"
    ? t("teams.member.agent", { agent: item.agent || "" }) : t("teams.member.person")) + ")";
  return String(item.id);
}

// The diff as sections of lines: the team header, then roles, members, assignments and relationships.
// A diff against nothing reads as "todo es nuevo"; a diff with no differences says so.
export function diffLines(diff) {
  if (!diff) return { base: "", empty: true, header: [], sections: [] };
  const header = fieldChanges(diff.team_fields);
  const sections = [];
  for (const section of Object.keys(SECTION_TEXT)) {
    const parts = diff[section] || {};
    const buckets = Object.keys(BUCKET_TEXT).filter((bucket) => (parts[bucket] || []).length).map((bucket) => ({
      bucket, label: BUCKET_TEXT[bucket],
      lines: parts[bucket].map((item) => bucket === "changed"
        ? item.id + ": " + fieldChanges(item.fields).join("; ") : itemLine(section, item)),
    }));
    if (buckets.length) sections.push({ section, label: SECTION_TEXT[section], buckets });
  }
  const base = diff.base_version ? t("teams.diff.base_present", { version: diff.base_version })
    : t("teams.diff.base_empty");
  return { base, empty: !header.length && !sections.length, header, sections };
}

export const canDecide = (entry) => Boolean(entry && entry.decision && (entry.decision.actions || []).length);
export const decisionActions = (entry) => canDecide(entry)
  ? entry.decision.actions.filter((action) => action === "approve" || action === "reject") : [];
