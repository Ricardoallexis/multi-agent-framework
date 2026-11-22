// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// ARR3-T02: pure helpers of the plan draft that «Nueva sesión» shows when a request needs more than one workflow
// (interpret answers reason "plan_suggested" with a plan_draft, ARR3-T01). No DOM and no network: every text returned is
// plain text, and the card draws it as text nodes. In simple mode the rows carry descriptions and texts, never workflow ids.
export const PLAN_SAVE_LABEL = "Guardar el plan";
export const PLAN_CANCEL_LABEL = "Cancelar";
export const PLAN_SAVE_PATH = "/gui/api/new-session/plan/save";
export const PLAN_SUGGESTED_REASON = "plan_suggested";
export const PLAN_NOTICE = "Tu pedido necesita varios pasos, así que preparé un plan. Revísalo: no se guarda ni se aprueba nada hasta que lo confirmes.";
export const PLAN_SAVED_NOTE = "Guardar no aprueba el plan: la decisión de aprobarlo o rechazarlo la tomas después, en el hilo del plan.";

const MAX_VALUE_CHARS = 2000;

// Text of any input value: a string as it is, a list or an object as short "name: value" parts (never a JSON document).
function plain(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(plain).filter(Boolean).join(", ");
  if (typeof value === "object") return Object.keys(value).map((key) => key + ": " + plain(value[key])).join("; ");
  return String(value);
}

const humanLabel = (name) => String(name).replace(/[_-]+/g, " ").trim();

// True when the answer of interpret carries a draft the card can show.
export function isPlanSuggested(interpretation) {
  const draft = interpretation && interpretation.plan_draft;
  return Boolean(interpretation && interpretation.reason === PLAN_SUGGESTED_REASON && draft && Array.isArray(draft.tasks) && draft.tasks.length > 0);
}

// The description of a workflow from the catalog of «Nueva sesión» (entries {id, bundle, description}); "" when unknown.
function describe(task, catalog) {
  const list = Array.isArray(catalog) ? catalog : [];
  const entry = list.find((item) => item.id === task.workflow && (item.bundle || null) === (task.bundle || null))
    || list.find((item) => item.id === task.workflow);
  return entry && typeof entry.description === "string" ? entry.description.trim() : "";
}

// planDraftRows(draft, mode, catalog) -> [{id, title, workflowText, inputs: [{label, value}], dependsOnText}].
// Simple: the workflow is its description (or nothing) and the row has no `workflow` or `bundle`. Advanced: the row also
// carries `workflow` and `bundle`, and the texts name them. «Va después de» names the titles of the earlier tasks.
export function planDraftRows(draft, mode = "simple", catalog = []) {
  const tasks = draft && Array.isArray(draft.tasks) ? draft.tasks : [];
  const titles = new Map(tasks.map((task) => [task.id, String(task.title || task.id)]));
  const advanced = mode === "advanced";
  return tasks.map((task) => {
    const description = describe(task, catalog);
    const inputs = Object.keys(task.inputs || {}).map((name) => ({
      label: humanLabel(name), value: plain(task.inputs[name]).slice(0, MAX_VALUE_CHARS),
    }));
    const after = (Array.isArray(task.depends_on) ? task.depends_on : []).map((id) => titles.get(id) || id);
    const row = {
      id: String(task.id),
      title: String(task.title || task.id),
      workflowText: advanced
        ? [description, task.workflow + (task.bundle ? " (" + task.bundle + ")" : "")].filter(Boolean).join(" · ")
        : description,
      inputs,
      dependsOnText: after.length ? "Va después de: " + after.join(", ") : "",
    };
    if (advanced) { row.workflow = task.workflow; row.bundle = task.bundle || null; }
    return row;
  });
}

// teamRows(draft) -> [{bundle, rolesText}]: a row for each bundle whose team version will be proposed with the plan.
export function teamRows(draft) {
  const teams = draft && Array.isArray(draft.teams) ? draft.teams : [];
  return teams.map((team) => ({ bundle: String(team.bundle), rolesText: (Array.isArray(team.roles) ? team.roles : []).join(", ") }));
}

// The body of POST /gui/api/new-session/plan/save: saving needs the confirmation of the person, which pressing the button is.
export const planSaveBody = (draft) => ({ draft, confirmed: true });

// The text of a failed save: the 409 and 422 of the server say what is wrong with the draft.
export function planSaveFailure(error) {
  const details = (error && error.details) || {};
  if (error && error.status === 422) return "El borrador ya no es válido. Vuelve a escribir tu pedido para preparar otro.";
  if (error && error.status === 409 && details.reason === "confirmation_required") return "Falta confirmar el guardado. Vuelve a pulsar «" + PLAN_SAVE_LABEL + "».";
  return (error && error.message) || "No se pudo guardar el plan.";
}
