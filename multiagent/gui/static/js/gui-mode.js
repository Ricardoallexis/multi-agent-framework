// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// SIM-T02: the simple or advanced mode of the GUI, as the server reports it (GET /gui/api/session, SIM-T01). The mode belongs to
// the installation, so nothing is remembered in the browser: this module holds the value of the page and tells its listeners
// when it changes. A view is shown in simple mode only when it says `mode: "simple"`; a view with no mode is advanced (L4).
import { apiPost } from "./api.js";
import { html } from "../vendor/htm-preact-standalone.module.js";
import { t } from "./i18n.js";

export const MODES = ["simple", "advanced"];
export const DEFAULT_MODE = "simple";

// Resolve the text at render time to avoid calling i18n during the module import cycle.
function advancedOnlyText() {
  return t("shell.advanced_only");
}

export const ADVANCED_ONLY_TEXT = html`<${advancedOnlyText} />`;

let current = DEFAULT_MODE;
const listeners = new Set();

export function currentMode() { return current; }

// A value that is not one of MODES changes nothing; the listeners hear only a real change.
export function setCurrentMode(mode) {
  if (!MODES.includes(mode) || mode === current) return current;
  current = mode;
  for (const listener of [...listeners]) {
    try { listener(current); } catch (_) { /* a listener must not stop the others */ }
  }
  return current;
}

export function onModeChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// Advanced shows everything; simple shows only what is declared simple.
export function visibleIn(entry, mode = "advanced") {
  return mode !== "simple" || Boolean(entry) && entry.mode === "simple";
}

// POST /gui/api/settings/mode, then the page follows the mode the server saved.
export async function saveMode(token, mode, post = apiPost) {
  if (!MODES.includes(mode)) throw new Error("saveMode: mode must be \"simple\" or \"advanced\"");
  const answer = await post("/gui/api/settings/mode", token, { mode });
  setCurrentMode(answer && MODES.includes(answer.mode) ? answer.mode : mode);
  return answer;
}
