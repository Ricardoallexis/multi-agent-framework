// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Inicio» (INI-T03), pure part: the rows of the steps that are still pending, with their actions, the hours as text to copy and
// the fixed texts. No DOM and no network, so Node tests it. The server decides which steps are done (GET /gui/api/start, INI-T02):
// a step that is done never comes back, so nothing here remembers anything. Every title and hour is plain text.
import { officeHref } from "./route.js";

export const POLL_MS = 4000;   // the same pace as the threads
export const REAL_MODELS_NOTE = "Los modelos reales pueden costar dinero o usar tu cuota. Puedes volver a Mock aquí mismo";
export const TRY_MOCK_NOTE = "Cuando termine, verás el documento en el hilo";
export const CHECKING_TEXT = "Comprobando…";
export const START_PATH = "/gui/api/start";
export const MODELS_PATH = "/gui/api/settings/models";
export const OFFICES_PATH = "/gui/api/offices";
export const EXAMPLE_OFFICE_ID = "cafeteria";
export const EXAMPLE_PATH = "/gui/api/offices/example";

const link = (label, href) => ({ kind: "link", label, href });
const post = (label, path, body = {}) => ({ kind: "post", label, path, body });

// What each pending step offers. «Conectar» and «Activar» only point at «Apps y agentes»: the activation flow lives there.
function actionsFor(id, status) {
  switch (id) {
    case "install":
      return [...(status.example_available ? [post("Instalar la oficina de ejemplo", EXAMPLE_PATH)] : []),
              link("Importar una oficina", "#offices")];
    case "try_mock":
      return [link("Probar en Mock", status.example_installed
        ? officeHref(EXAMPLE_OFFICE_ID, "menu")
        : "#new")];
    case "connect":
      return [link("Conectar un agente", "#apps")];
    case "activate":
      return [link("Activar un agente", "#apps")];
    case "real_run":
      return [...(status.offer_real ? [{ ...post("Pasar a modelos reales", MODELS_PATH, { models: "real" }), note: REAL_MODELS_NOTE, confirm: true }] : []),
              link("Correr con tus datos", "#new")];
    default:
      return [];
  }
}

// The pending steps in the order of the server: [{ id, title, note?, actions: [{ kind: "post" | "link", label, href?, path?, body?, note?, confirm? }] }].
// `status` is the GET /gui/api/start answer, with the example-office fields added by the view.
export function startRows(status) {
  if (!status || !Array.isArray(status.steps)) return [];
  const pending = Array.isArray(status.pending) ? new Set(status.pending) : null;
  return status.steps
    .filter((step) => step && !step.done && (!pending || pending.has(step.id)))
    .map((step) => {
      const row = { id: step.id, title: String(step.title || ""), actions: actionsFor(step.id, status) };
      if (step.id === "try_mock") row.note = TRY_MOCK_NOTE;
      return row;
    });
}

// «Inicio» goes in the sidebar while a step is missing; an answer that is not there yet leaves it in «Más».
export function inStartSidebar(status) {
  return Boolean(status) && status.all_done !== true && Array.isArray(status.pending) && status.pending.length > 0;
}

// The hours exactly as the server wrote them (`times_text`), to show and to copy.
export function timesText(status) {
  return status && typeof status.times_text === "string" ? status.times_text : "";
}

// «Copiar»: writes timesText(status) and nothing else. `writeText` is navigator.clipboard.writeText.
export async function copyTimes(status, writeText) {
  const text = timesText(status);
  if (!text || typeof writeText !== "function") return false;
  await writeText(text);
  return true;
}

// «Volver a Mock»: shown when the saved setting is «real», the way back that REAL_MODELS_NOTE promises.
export function backToMockAction(status) {
  return status && status.models === "real" ? post("Volver a Mock", MODELS_PATH, { models: "mock" }) : null;
}
