// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Quick guide and shortcuts» (GUI4-T06): what the guide says and how it is asked to open. Pure functions: no DOM, no Preact,
// no storage. quick-guide.js draws it. Only keys that exist in the application are listed: F1 (shell-state.js isHelpKey), Ctrl+B
// (isSidebarKey), Ctrl+K (command-palette-model.js isPaletteKey) and Esc, which closes a dialog. Texts are computed when drawn.
import { t } from "./i18n.js";

export const QUICK_GUIDE_EVENT = "multiagent:quick-guide";

// The shortcuts, in the order they are listed. `keys` are the key caps as written; `text` is the sentence beside them.
export function shortcutRows() {
  return [
    { id: "help", keys: ["F1"], text: t("quick_guide.key_help") },
    { id: "sidebar", keys: ["Ctrl", "B"], text: t("quick_guide.key_sidebar") },
    { id: "search", keys: ["Ctrl", "K"], text: t("quick_guide.key_search") },
    { id: "close", keys: ["Esc"], text: t("quick_guide.key_close") },
  ];
}

// The explanation blocks above and below the shortcuts.
export function guideBlocks() {
  return {
    intro: [t("quick_guide.help_what"), t("quick_guide.help_hints")],
    again: t("quick_guide.help_again"),
  };
}

// Ask the guide to open. The «Ayuda» rows of the footer and of «Más» call this; the one QuickGuide mounted in the shell listens.
export function requestQuickGuide(target = globalThis.window) {
  if (!target || typeof target.dispatchEvent !== "function" || typeof globalThis.CustomEvent !== "function") return false;
  target.dispatchEvent(new globalThis.CustomEvent(QUICK_GUIDE_EVENT));
  return true;
}

// Listen for the request; returns the function that stops listening.
export function onQuickGuide(listener, target = globalThis.window) {
  if (!target || typeof target.addEventListener !== "function") return () => {};
  target.addEventListener(QUICK_GUIDE_EVENT, listener);
  return () => target.removeEventListener(QUICK_GUIDE_EVENT, listener);
}
