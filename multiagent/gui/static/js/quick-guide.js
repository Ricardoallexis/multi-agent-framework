// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Quick guide and shortcuts» (GUI4-T06): one native <dialog> (showModal) that explains the pop-up help and lists the shortcuts
// that exist. showModal keeps the focus inside while it is open; closing it (Esc, «Close», the backdrop) gives the focus back to
// the control that opened it. It draws no state of its own beyond open/closed: help mode itself is changed by F1, the «Ayuda»
// row or «Personalizar».
import { html, useEffect, useLayoutEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { guideBlocks, onQuickGuide, shortcutRows } from "./quick-guide-model.js";
import { t } from "./i18n.js";

// The opener may have been hidden while the guide was open (the bar closed, the drawer left): then the focus goes to the
// button that shows the bar again, or to the main content.
function hidden(node) {
  if (node.closest && node.closest("[inert]")) return true;
  try { if (window.getComputedStyle(node).visibility === "hidden") return true; } catch (_) { /* no CSSOM: assume visible */ }
  return typeof node.getClientRects === "function" && node.getClientRects().length === 0;
}
function fallbackFocus() {
  const target = document.querySelector(".edge-side") || document.getElementById("main-content");
  if (target && typeof target.focus === "function") target.focus();
}

export function QuickGuide() {
  const [open, setOpen] = useState(false);
  const dialog = useRef(null);
  const closeButton = useRef(null);
  const before = useRef(null);   // the control that asked for the guide, to return the focus to it

  useEffect(() => onQuickGuide(() => { before.current = document.activeElement; setOpen(true); }), []);

  // Before paint, so the focus is already inside when the dialog appears.
  useLayoutEffect(() => {
    const box = dialog.current;
    if (!box) return;
    if (open && !box.open) {
      if (typeof box.showModal === "function") box.showModal(); else box.setAttribute("open", "");
      if (closeButton.current) closeButton.current.focus();
    } else if (!open && box.open) {
      box.close();
    }
  }, [open]);

  // The dialog closes itself on Esc and on box.close(): the state and the focus follow.
  const closed = () => {
    setOpen(false);
    const back = before.current;
    before.current = null;
    if (back && typeof back.focus === "function" && document.contains(back) && !hidden(back)) back.focus();
    else fallbackFocus();
  };
  const latest = useRef(closed);
  latest.current = closed;
  useEffect(() => {
    const box = dialog.current;
    const onClosed = () => latest.current();
    box.addEventListener("close", onClosed);
    return () => box.removeEventListener("close", onClosed);
  }, []);

  const onKeyDown = (event) => {
    if (event.key === "Escape" && !event.isComposing && !event.defaultPrevented) {
      event.preventDefault();   // the session screen also goes back with Esc: this one only closes the guide
      dialog.current.close();
    }
  };

  const blocks = open ? guideBlocks() : null;
  return html`<dialog ref=${dialog} class="quick-guide" aria-labelledby="quick-guide-title" onKeyDown=${onKeyDown}
    onClick=${(event) => { if (event.target === dialog.current) dialog.current.close(); }}>
    ${open && html`<div class="quick-guide-box">
      <h2 id="quick-guide-title" class="quick-guide-title">${t("quick_guide.title")}</h2>
      <section aria-labelledby="quick-guide-help">
        <h3 id="quick-guide-help" class="quick-guide-heading">${t("quick_guide.help_heading")}</h3>
        ${blocks.intro.map((text) => html`<p key=${text}>${text}</p>`)}
      </section>
      <section aria-labelledby="quick-guide-keys">
        <h3 id="quick-guide-keys" class="quick-guide-heading">${t("quick_guide.keys_heading")}</h3>
        <dl class="quick-guide-keys">
          ${shortcutRows().map((row) => html`<div key=${row.id} class="quick-guide-row">
            <dt>${row.keys.map((key, at) => html`<span key=${key}>${at > 0 && html`<span aria-hidden="true"> + </span>`}<kbd>${key}</kbd></span>`)}</dt>
            <dd>${row.text}</dd>
          </div>`)}
        </dl>
      </section>
      <section aria-labelledby="quick-guide-again">
        <h3 id="quick-guide-again" class="quick-guide-heading">${t("quick_guide.again_heading")}</h3>
        <p>${blocks.again}</p>
      </section>
      <div class="quick-guide-actions">
        ${/* data-help-exempt: this file is not in VIEW_FILES of test_gui_help.py; if it is added there, count ("quick-guide.js", "confirm"): 1 */ null}
        <button type="button" class="btn secondary" ref=${closeButton} data-help-exempt="confirm" onClick=${() => dialog.current.close()}>${t("quick_guide.close")}</button>
      </div>
    </div>`}
  </dialog>`;
}
