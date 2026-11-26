// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Which cards of the «Contexto» panel are drawn (GUI3-T04). Pure functions (no DOM, no Preact, no fetch), so they can be
// checked on their own; right-panel.js applies them and shell-state.js remembers the modes.
//
// A card has a mode: "auto" (drawn only when it has content), "always" (drawn even when empty, with its «Sin …» text) or
// "never" (not drawn). Something urgent is drawn in every mode: the card says so through onContent({ has, urgent }), because
// the shared code cannot name extensions. A card that never reports counts as «with content», so the extensions that
// existed before this panel keep showing.
import { sourcesCount, sourcesModel, tokensModel, changesModel } from "./panel-model.js";

export const CARD_MODES = ["auto", "always", "never"];
export const DEFAULT_MODE = "auto";

export function normalizeMode(value, fallback = DEFAULT_MODE) {
  return CARD_MODES.includes(value) ? value : fallback;
}

// A stored object of { card name: mode } reduced to the entries that are valid; anything else is dropped.
export function normalizeModes(raw) {
  const modes = {};
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return modes;
  for (const [name, mode] of Object.entries(raw)) {
    if (/^[a-z][a-z0-9_]{0,39}$/.test(name) && CARD_MODES.includes(mode)) modes[name] = mode;
  }
  return modes;
}

// The mode of a card: what the person chose, else what the card registered, else "auto".
export function modeOf(card, modes) {
  const own = modes && Object.prototype.hasOwnProperty.call(modes, card.name) ? modes[card.name] : null;
  return normalizeMode(own, normalizeMode(card.defaultMode));
}

// What a card reported, completed: not reporting means content; urgent implies content.
export function contentOf(reported) {
  const urgent = Boolean(reported) && reported.urgent === true;
  return { has: urgent || !reported || reported.has !== false, urgent };
}

export function sameContent(a, b) {
  const left = contentOf(a);
  const right = contentOf(b);
  return left.has === right.has && left.urgent === right.urgent;
}

export function cardShown(mode, reported) {
  const content = contentOf(reported);
  if (content.urgent || mode === "always") return true;
  if (mode === "never") return false;
  return content.has;
}

// { name: true|false } for every card, from the modes and what each card reported.
export function shownCards(cards, modes, contents) {
  const shown = {};
  for (const card of cards) shown[card.name] = cardShown(modeOf(card, modes), contents[card.name]);
  return shown;
}

export function groupShown(shown, names) {
  return names.some((name) => Boolean(shown && shown[name]));
}

// The dot beside «Contexto»: some card that is drawn has something to show (an empty card drawn by «Siempre» does not count).
export function anyContent(cards, modes, contents) {
  const shown = shownCards(cards, modes, contents);
  return cards.some((card) => shown[card.name] && contentOf(contents[card.name]).has);
}

// Content of the four own cards from the data of the open session or run. Nothing is known until the data is ready.
// «Subagentes» is not here: its panel reports through onContent, because it follows its own stream.
export function ownContents(subject, data) {
  const none = { changes: { has: false }, tokens: { has: false }, sources: { has: false } };
  if (!subject || !data || data.status !== "ready") return none;
  const changes = changesModel(subject, data);
  const tokens = tokensModel(subject, data);
  return {
    changes: { has: changes.files.length > 0 },
    tokens: { has: Boolean(tokens.total || tokens.workers.tokens || tokens.orchestrator.tokens) || tokens.cost.state !== "unknown" },
    sources: { has: sourcesCount(sourcesModel(subject, data)) > 0 },
  };
}
