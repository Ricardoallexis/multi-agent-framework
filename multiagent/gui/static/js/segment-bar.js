// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// SegmentBar (AVZ-T03): a bar made of equal pieces, one per item, that says its state in words and not only in colour.
//
//   <${SegmentBar} segments=${[{ id: "T01", state: "done", tone: "success", text: "Done" }, ...]}
//     summary="7 of 12 tasks done, 2 waiting for review" className="project-segbar" />
//
//   segments   the pieces in order: { id, state, tone, text }. `state` goes to data-state (a status key such as "done"),
//              `tone` becomes the class tone-<tone> (the colours live in app.css through --tone), `text` is the state in words
//   summary    the sentence for a screen reader («7 de 12 hechas, 2 en revisión»). The bar is one image with that name; the
//              pieces are hidden from assistive technology because the sentence already says all of them
//   className  extra classes for the bar. The sheet that styles the bar belongs to the view that uses it (it has to draw
//              `.segment-bar` as a flex row and `[data-state]` pieces with `flex: 1`); no size or colour is set from here (CSP)
// Every piece has the same width: there is no size to weigh them by. A piece that has a state "todo" is drawn as an outline by the
// sheet, so the difference between finished and not started is not only a colour. Everything is rendered as text.
import { html } from "../vendor/htm-preact-standalone.module.js";

// How many pieces carry each state, in the order they first appear: [{ state, text, count }].
export function segmentCounts(segments) {
  const found = new Map();
  for (const segment of Array.isArray(segments) ? segments : []) {
    const state = String(segment?.state || "");
    const entry = found.get(state) || { state, text: String(segment?.text || state), count: 0 };
    entry.count += 1;
    found.set(state, entry);
  }
  return [...found.values()];
}

// A plain sentence for when the caller gives none: «2 Done, 1 Blocked» (the words are the segments' own).
export function defaultSummary(segments) {
  return segmentCounts(segments).map(({ text, count }) => count + " " + text).join(", ");
}

export function SegmentBar({ segments, summary = "", className = "", ...rest }) {
  const pieces = Array.isArray(segments) ? segments : [];
  const extra = className ? " " + className : "";
  return html`<div class=${"segment-bar" + extra} role="img" aria-label=${summary || defaultSummary(pieces)} ...${rest}>
    ${pieces.map((piece, index) => html`<span key=${piece?.id ?? index} class=${"segment tone-" + String(piece?.tone || "neutral")}
      data-state=${String(piece?.state || "")} aria-hidden="true"></span>`)}
  </div>`;
}
