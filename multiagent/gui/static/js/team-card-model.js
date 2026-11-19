// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// CHT-T05: pure text of the team card (no DOM, no network). The server sends the version with its `card` (per role: rules,
// permissions, and the model its agent resolves to in the catalog; plus the team mode) and its `diff` against the approved
// version, or against an empty team when there is none. Everything here returns plain text or plain data.

export const MODE_TEXT = { supervised: "Supervisado", autonomous: "Autónomo" };
export const PERMISSION_TEXT = { read: "leer", write: "escribir", destructive: "borrar o destruir", external: "salir a servicios externos" };
export const MODEL_STATUS_TEXT = {
  no_agent: "Sin agente: este puesto lo ocupa una persona o nadie, así que no usa un modelo.",
  agent_not_in_catalog: "El agente no está en el catálogo del bundle, así que no se sabe qué modelo usaría.",
  no_step: "Ningún paso de los flujos del bundle usa a este agente, así que no tiene un modelo asignado.",
  not_in_catalog: "Los pasos piden un modelo que no está en el catálogo del bundle.",
  partial: "Algunos modelos de sus pasos no están en el catálogo del bundle.",
  unavailable: "No se pudo leer el catálogo del bundle, así que no se sabe qué modelo usaría.",
};
export const SECTION_TEXT = { roles: "Puestos", members: "Miembros", assignments: "Asignaciones", relationships: "Relaciones" };
export const LINK_TEXT = { reviews: "revisa a", delegates_to: "delega en", reports_to: "reporta a" };
const BUCKET_TEXT = { added: "Añadidos", removed: "Quitados", changed: "Cambiados" };
const HEADER_TEXT = { id: "Identificador", description: "Descripción", schema_version: "Versión del esquema", mode: "Modo" };

export const modeText = (mode) => MODE_TEXT[mode] || (mode ? String(mode) : "Sin modo declarado");
export const permissionText = (permissions) => Array.isArray(permissions) && permissions.length
  ? permissions.map((item) => PERMISSION_TEXT[item] || String(item)).join(", ") : "Ninguno";

// One model as "gemini-x (proveedor, nivel)"; the catalog id is part of it so a person can find it in models.yaml.
export const modelLine = (model) => !model ? "" : String(model.model || model.id) +
  " (" + [model.id, model.provider, model.tier].filter(Boolean).join(", ") + ")";

// What the card says about the model of a role: the resolved models first, then the reason when something is missing.
export function modelSummary(role) {
  const info = (role && role.model) || { status: "unavailable", models: [], missing: [] };
  const lines = (info.models || []).map(modelLine);
  const notes = [];
  if (info.status !== "resolved" && MODEL_STATUS_TEXT[info.status]) notes.push(MODEL_STATUS_TEXT[info.status]);
  if ((info.missing || []).length) notes.push("Falta en el catálogo: " + info.missing.join(", ") + ".");
  return { status: info.status || "unavailable", lines, notes, ok: info.status === "resolved" };
}

export const membersLine = (members) => Array.isArray(members) && members.length
  ? members.map((member) => (member.name || member.id) + " (" + (member.kind === "agent" ? "agente " + (member.agent || "") : "persona") + ")").join(", ")
  : "Nadie";

// The rows of one role of the card, as label/value text; an empty list says so instead of leaving a blank.
export function roleRows(role) {
  const list = (items, empty) => Array.isArray(items) && items.length ? items.join("; ") : empty;
  const model = modelSummary(role);
  return {
    id: String(role.id),
    purpose: role.purpose || "",
    rows: [
      { key: "rules", label: "Reglas", text: list(role.rules, "Sin reglas declaradas") },
      { key: "permissions", label: "Permisos", text: permissionText(role.permissions) },
      { key: "model", label: "Modelo", text: model.lines.length ? model.lines.join("; ") : "Sin modelo resuelto" },
      { key: "members", label: "Ocupa", text: membersLine(role.members) },
    ],
    notes: model.notes,
    modelStatus: model.status,
  };
}

export const cardRoles = (card) => Array.isArray(card && card.roles) ? card.roles.map(roleRows) : [];

const fieldValue = (value) => value === null || value === undefined ? "(vacío)" : typeof value === "string" ? value : JSON.stringify(value);
export const fieldChanges = (fields) => Object.keys(fields || {}).map((name) =>
  (HEADER_TEXT[name] || name) + ": " + fieldValue(fields[name].before) + " → " + fieldValue(fields[name].after));

// One line for an added or removed item of a diff section; a role also says its rules and permissions.
export function itemLine(section, item) {
  if (!item) return "";
  if (section === "assignments") return item.member + " en " + item.role;
  if (section === "relationships") return item.from + " " + (LINK_TEXT[item.type] || item.type) + " " + item.to;
  if (section === "roles") {
    const rules = Array.isArray(item.rules) ? item.rules.length : 0;
    return item.id + " (" + rules + (rules === 1 ? " regla" : " reglas") + "; permisos: " + permissionText(item.permissions) + ")";
  }
  if (section === "members") return item.id + " (" + (item.kind === "agent" ? "agente " + (item.agent || "") : "persona") + ")";
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
  const base = diff.base_version ? "Frente a la versión " + diff.base_version + " aprobada."
    : "No hay versión aprobada con la que comparar: todo lo que sigue es nuevo.";
  return { base, empty: !header.length && !sections.length, header, sections };
}

export const canDecide = (entry) => Boolean(entry && entry.decision && (entry.decision.actions || []).length);
export const decisionActions = (entry) => canDecide(entry)
  ? entry.decision.actions.filter((action) => action === "approve" || action === "reject") : [];
