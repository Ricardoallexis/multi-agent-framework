// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// ResizeHandle (GUI4-T03, B1): the separator between a side pane and the content. A drag changes the width, arrows, Home and End
// move it from the keyboard, a double click puts the default back. The component only reports pixels; the caller decides what a
// width means (shell.js applies it through the CSSOM, never through a style attribute).
//   kind      "side" or "panel": the pane it belongs to, for the label and the CSS
//   width     the current width in px, min/max the range (aria-valuenow, aria-valuemin, aria-valuemax)
//   edge      "right" when the pane is on the left of the content (the arrows move its right edge), "left" for the panel
//   measure   (event) -> the width in px that the pointer asks for
//   onPreview (px) while dragging, onCommit(px) when the pointer is released or a key is pressed, onReset() on a double click
//   keyWidth  (current, key) -> the width a key asks for
import { html, useRef } from "../../vendor/htm-preact-standalone.module.js";
import { t } from "../i18n.js";

export function ResizeHandle({ kind, width, min, max, edge, measure, keyWidth, onPreview, onCommit, onReset }) {
  const drag = useRef(null);
  const onPointerDown = (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    drag.current = { id: event.pointerId, px: width };
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch (_) { /* no capture: the move still arrives while over it */ }
    event.preventDefault();
  };
  const onPointerMove = (event) => {
    if (!drag.current || drag.current.id !== event.pointerId) return;
    drag.current.px = measure(event);
    onPreview(drag.current.px);
  };
  const finish = (event) => {
    if (!drag.current || drag.current.id !== event.pointerId) return;
    const px = drag.current.px;
    drag.current = null;
    onCommit(px);
  };
  const onKeyDown = (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = keyWidth(width, event.key, edge);
    if (next !== width) onCommit(next);
  };
  return html`<div class=${"resize-handle resize-" + kind} role="separator" aria-orientation="vertical" tabindex="0"
    aria-label=${t(kind === "side" ? "shell.resize_sidebar" : "shell.resize_panel")}
    aria-valuemin=${min} aria-valuemax=${max} aria-valuenow=${width}
    onPointerDown=${onPointerDown} onPointerMove=${onPointerMove} onPointerUp=${finish} onPointerCancel=${finish}
    onKeyDown=${onKeyDown} onDblClick=${onReset}></div>`;
}
