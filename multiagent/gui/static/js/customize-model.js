// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// What «Personalizar» lists and how a choice changes (GUI3-T07). Pure functions: no DOM, no Preact, no storage. customize.js
// draws them and shell-state.js remembers the result.
import { CARD_MODES, modeOf, normalizeModes } from "./context-model.js";
import { visibleIn } from "./gui-mode.js";
import { t } from "./i18n.js";

export const CUSTOMIZE_SECTION = "customize";
export const LANGUAGE_CHOICES = ["auto", "en", "es"];
export const MODE_LABELS = {
  get auto() { return t("customize.auto"); },
  get always() { return t("customize.always"); },
  get never() { return t("customize.never"); },
};

// The sections a person can pin: the ones that are not already fixed and not this screen. A section is pinned when its id is in `pinned`.
// SIM-T02: in simple mode the advanced sections are not offered; a pin already made stays in `pinned` untouched.
export function pinnableSections(sections, pinned, mode = "advanced") {
  const ids = new Set(Array.isArray(pinned) ? pinned : []);
  const seen = new Set();
  const rows = [];
  for (const section of Array.isArray(sections) ? sections : []) {
    if (!section || typeof section.id !== "string" || seen.has(section.id)) continue;
    seen.add(section.id);
    if (section.group === "primary" || section.id === CUSTOMIZE_SECTION || !visibleIn(section, mode)) continue;
    rows.push({ id: section.id, label: section.label, pinned: ids.has(section.id) });
  }
  return rows;
}

// Pin or release one section; the order of the others is kept, and an id that is not a string changes nothing.
export function togglePinned(pinned, id) {
  const list = (Array.isArray(pinned) ? pinned : []).filter((value) => typeof value === "string");
  if (typeof id !== "string" || !id) return list;
  return list.includes(id) ? list.filter((value) => value !== id) : [...list, id];
}

// The four cards of the panel and the ones extensions registered, each with the mode in effect.
export const OWN_CARDS = [
  { name: "changes", get title() { return t("customize.card_changes"); } },
  { name: "subagents", get title() { return t("customize.card_subagents"); } },
  { name: "tokens", get title() { return t("customize.card_tokens"); } },
  { name: "sources", get title() { return t("customize.card_sources"); } },
];

export function cardRows(extraSlots, modes) {
  const extra = (Array.isArray(extraSlots) ? extraSlots : []).map((slot) => ({ name: slot.name, title: slot.title, defaultMode: slot.defaultMode }));
  return OWN_CARDS.concat(extra).map((card) => ({ name: card.name, title: card.title, mode: modeOf(card, modes) }));
}

// The modes after one card changes; a mode that is not valid changes nothing.
export function withCardMode(modes, name, mode) {
  const current = normalizeModes(modes);
  if (!CARD_MODES.includes(mode) || typeof name !== "string") return current;
  return normalizeModes({ ...current, [name]: mode });
}

/* GUI3-T09 */
// The accent palettes of the «Colores» block. The ids and names live here; the colours live only in app.css (a block per
// palette, `.app[data-accent="<id>"]`), so nothing here repeats a value. «Neutro» is the default and has no block: it is :root.
export const ACCENTS = [
  { id: "neutral", get label() { return t("customize.accent_neutral"); } },
  { id: "azul", get label() { return t("customize.accent_azul"); } },
  { id: "verde", get label() { return t("customize.accent_verde"); } },
  { id: "violeta", get label() { return t("customize.accent_violeta"); } },
];
export const DEFAULT_ACCENT = "neutral";

// A palette id that does not exist (an old or edited stored value) is «Neutro».
export function normalizeAccent(value) {
  return ACCENTS.some((accent) => accent.id === value) ? value : DEFAULT_ACCENT;
}
