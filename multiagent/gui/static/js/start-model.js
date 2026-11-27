// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Inicio» (INI-T03), pure part: the rows of the steps that are still pending, with their actions, the hours as text to copy and
// the fixed texts. No DOM and no network, so Node tests it. The server decides which steps are done (GET /gui/api/start, INI-T02):
// a step that is done never comes back, so nothing here remembers anything. Every title and hour is plain text.
import { officeHref } from "./route.js";
import { t, onLanguageChange } from "./i18n.js";

export const POLL_MS = 4000;   // the same pace as the threads
export let REAL_MODELS_NOTE = t("start.real_models_note");
export let TRY_MOCK_NOTE = t("start.try_mock_note");
export let CHECKING_TEXT = t("start.checking");
onLanguageChange(() => {
  REAL_MODELS_NOTE = t("start.real_models_note");
  TRY_MOCK_NOTE = t("start.try_mock_note");
  CHECKING_TEXT = t("start.checking");
});
export const START_PATH = "/gui/api/start";
export const MODELS_PATH = "/gui/api/settings/models";
export const OFFICES_PATH = "/gui/api/offices";
export const EXAMPLE_OFFICE_ID = "cafe";
export const EXAMPLE_PATH = "/gui/api/offices/example";

const link = (label, href) => ({ kind: "link", label, href });
const post = (label, path, body = {}) => ({ kind: "post", label, path, body });

// What each pending step offers. «Conectar» and «Activar» only point at «Apps y agentes»: the activation flow lives there.
function actionsFor(id, status) {
  switch (id) {
    case "install":
      return [...(status.example_available ? [post(t("start.install_example"), EXAMPLE_PATH)] : []),
              link(t("start.import_office"), "#offices")];
    case "try_mock":
      return [link(t("start.try_mock"), status.example_installed
        ? officeHref(EXAMPLE_OFFICE_ID, "menu")
        : "#new")];
    case "connect":
      return [link(t("start.connect_agent"), "#apps")];
    case "activate":
      return status.steps?.find((step) => step.id === "activate")?.applicable === false
        ? [] : [link(t("start.activate_agent"), "#apps")];
    case "real_run":
      return [...(status.offer_real ? [{ ...post(t("start.real_models"), MODELS_PATH, { models: "real" }), note: REAL_MODELS_NOTE, confirm: true }] : []),
              link(t("start.run_your_data"), "#new")];
    default:
      return [];
  }
}

// The pending steps in the order of the server: [{ id, title, note?, actions: [{ kind: "post" | "link", label, href?, path?, body?, note?, confirm? }] }].
// `status` is the GET /gui/api/start answer, with the example-office fields added by the view.
export function startRows(status) {
  if (!status || !Array.isArray(status.steps)) return [];
  const pending = Array.isArray(status.pending) ? new Set(status.pending) : null;
  // EXA-T05: «Inicio» measures nothing. While the person has not allowed «Search for apps», the steps that need the apps say so.
  const unsearched = status.discovery === null || status.discovery === "declined";
  return status.steps
    .filter((step) => step && !step.done && (step.applicable === false || !pending || pending.has(step.id)))
    .map((step) => {
      const row = { id: step.id, title: String(step.title || ""), actions: actionsFor(step.id, status) };
      if (step.id === "try_mock") row.note = TRY_MOCK_NOTE;
      if (step.id === "connect" && unsearched) row.note = t("start.discovery_note");
      if (step.id === "activate" && step.applicable === false) row.note = unsearched ? t("start.discovery_note") : t("start.not_applicable");
      return row;
    });
}

// Start appears in the sidebar while a step is missing; before a response arrives it stays under More.
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

// Return to Mock is shown when the saved setting is real, as REAL_MODELS_NOTE promises.
export function backToMockAction(status) {
  return status && status.models === "real" ? post(t("start.back_to_mock"), MODELS_PATH, { models: "mock" }) : null;
}
