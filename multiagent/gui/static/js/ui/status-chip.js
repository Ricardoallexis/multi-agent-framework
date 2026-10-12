// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// StatusChip (GUI2-T11): a status shown as text, tone and (for dots) shape, never by colour alone.
//
//   <${StatusChip} status="running" labels="run" />                      pill with the label ("En ejecución")
//   <${StatusChip} status="working" labels="dot" variant="dot" detail="En ejecución" />
//                                                                         only a dot; the words are for screen readers and the tooltip
//   <${StatusChip} status="idle" labels=${myMap} variant="inline" />     small mark + coloured text, for item lines
//
//   status   the key to look up (a run status, a session status, ...); a key the map does not know shows its own name
//   labels   a label map (status -> { text, tone, explanation }) or the NAME of a registered one: "run" (default), "step",
//            "session", "subagent", "dot", or whatever registerLabelSet() added (labels.js; it also tells how the label map
//            of PRY-T02 plugs in)
//   variant  "chip" (default) | "inline" | "dot"
//   detail   extra words after the label, in parentheses, lower case (dot variant: "Trabajando (en ejecución)")
//   className / any other attribute (role, aria-live, id) goes to the outer element
// The tone becomes the class tone-<tone>; the colours live in app.css. Everything is rendered as text.
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { labelEntry, resolveLabels, statusText } from "./labels.js";

export function StatusChip({ status, labels = "run", variant = "chip", detail = "", className = "", ...rest }) {
  const entry = labelEntry(resolveLabels(labels), status);
  const text = statusText(entry, detail);
  const tone = "tone-" + entry.tone;
  const extra = className ? " " + className : "";
  const pulse = entry.pulse ? " pulse" : "";
  if (variant === "dot") {
    return html`<span class=${"status-dot " + tone + extra} title=${text} data-status=${entry.key} ...${rest}>
      <span class=${"dot" + pulse} data-shape=${entry.shape} aria-hidden="true"></span>
      <span class="sr-only">Estado: ${text}</span>
    </span>`;
  }
  if (variant === "inline") {
    return html`<span class=${"status-inline " + tone + extra} title=${entry.explanation || null} data-status=${entry.key} ...${rest}>
      <span class=${"mark" + pulse} data-shape=${entry.shape} aria-hidden="true"></span>
      <span class="status-text">${text}</span>
    </span>`;
  }
  return html`<span class=${"badge status-chip " + tone + extra} title=${entry.explanation || null} data-status=${entry.key} ...${rest}>${text}</span>`;
}
