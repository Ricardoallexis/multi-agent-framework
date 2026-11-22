// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// PRV-T01: the label of this instance (the INSTANCE_LABEL setting, as GET /gui/api/session reports it). Plain data and pure
// functions with no DOM, so a Node test can run it. The label is plain text: shell.js draws it as a text child, and
// pageTitle() is the only thing that builds the text of the tab title (pending.js is the only place that writes it).

export const INSTANCE_LABEL_MAX = 40;
export const TITLE = "Multi-Agent Framework";
export const SHORT_TITLE = "Multiagent";

let current = "";
const listeners = new Set();

export function instanceLabel() { return current; }

// A value that is not a string, or is longer than the limit, or has a control character, is treated as no label.
export function setInstanceLabel(label) {
  const next = typeof label === "string" && label.length <= INSTANCE_LABEL_MAX && !/[\u0000-\u001f\u007f-\u009f]/.test(label) ? label : "";
  if (next === current) return current;
  current = next;
  for (const listener of [...listeners]) {
    try { listener(current); } catch (_) { /* a listener must not stop the others */ }
  }
  return current;
}

export function onInstanceLabelChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// «Multi-Agent Framework» or «(n) Multiagent» as before; with a label it goes in front: «<label> · Multi-Agent Framework».
export function pageTitle(count = 0) {
  const prefix = current ? current + " · " : "";
  return count ? `(${count}) ${prefix}${SHORT_TITLE}` : `${prefix}${TITLE}`;
}
