// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Contextual help (GUI-T12). <Help topic="..."> shows a one-line hint and a "?" button that opens a short explanation
// (collapsed by default). The control it explains points at the hint with helpFor(topic), which gives it
// aria-describedby; the button has aria-expanded and aria-controls. Everything is plain text; the texts live in
// help-text.js (an extension keeps its own in extensions/<name>/help.js and registers them, see extensions.js).
// A control that deliberately has no help says why with data-help-exempt (see tests/test_gui_help.py).
import { html, useState } from "../vendor/htm-preact-standalone.module.js";
import { registeredHelp } from "./extensions.js";
import { localizedHelp } from "./help-text.js";
import { t } from "./i18n.js";

const slug = (text) => String(text).replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
const baseId = (topic, scope) => "help-" + slug(topic) + (scope ? "-" + slug(scope) : "");

// id of the hint element of a topic (a repeated control passes a scope to keep the ids unique).
export const helpHintId = (topic, scope) => baseId(topic, scope) + "-hint";

// Attributes for the control a help entry explains: it is described by the hint (plus any other ids it already had).
export function helpFor(topic, { also = "", scope = "" } = {}) {
  return { "aria-describedby": [helpHintId(topic, scope), also].filter(Boolean).join(" ") };
}

// topic: key of HELP; entry: a full { title, hint, more } for helps built from data (workflow inputs, execution mode);
// hint: replaces the visible line; compact: only the "?" shows until it is opened (small buttons);
// scope: makes the ids unique when the same topic appears more than once on the page;
// always: the block stays visible when help mode is off (GUI3-T02), for the help of a screen title that does not follow the <h2>.
export function Help({ topic, entry = null, hint = null, compact = false, scope = "", always = false }) {
  const [open, setOpen] = useState(false);
  const data = entry || localizedHelp(topic) || registeredHelp(topic);
  if (!data) return null;
  const id = baseId(topic, scope);
  const sentences = Array.isArray(data.more) ? data.more : [data.more];
  const close = (event) => {
    if (event.key !== "Escape" || !open) return;
    event.preventDefault(); // the session screen also goes back with Esc: this one only closes the help
    setOpen(false);
    const toggle = event.currentTarget.querySelector(".help-toggle");
    if (toggle) toggle.focus();
  };
  return html`<div class=${"help-block" + (always ? " help-always" : "") + (compact ? " compact" : "") + (open ? " open" : "")} onKeyDown=${close}>
    <div class="hint-row">
      <span id=${id + "-hint"} class="hint-text" data-hint-title=${data.title} hidden=${compact && !open}>${hint || data.hint}</span>
      <button type="button" class="help-toggle" aria-expanded=${open ? "true" : "false"} aria-controls=${id + "-more"}
        aria-label=${t("shell.more_information", { title: data.title })} title=${t("shell.what_is_this")} onClick=${() => setOpen(!open)}>?</button>
    </div>
    <div id=${id + "-more"} class="help-more" hidden=${!open}>${sentences.join(" ")}</div>
  </div>`;
}

// --- Hover hint (GUI4-T12) ------------------------------------------------------------------------------------------
// A document-level delegate (installed when this module loads, so main.js does not change): when the pointer rests on a control
// that helpFor() describes, or the control gets keyboard focus, or a finger holds it, a small hint (the topic title and its one-line
// hint) shows next to it. It does not depend on help mode. Esc closes it; the pointer can move onto it; touching it does not close it.
// The control keeps its aria-describedby; the pop is aria-hidden because that same text is already the control's description.
// The side is a class (hint-bottom, hint-top, hint-right, hint-left); the coordinates go through CSSOM (the CSP only blocks the inline attribute).
const HINT_ID = "hover-hint";
const HOVER_DELAY = 400;
const LEAVE_DELAY = 150;
const HOLD_DELAY = 500;
const GAP = 8;

// The hint element a control is described by (the first id of aria-describedby that is a help hint), or null.
export function hintSourceFor(control, doc = document) {
  const ids = String(control && control.getAttribute ? control.getAttribute("aria-describedby") || "" : "").split(/\s+/).filter(Boolean);
  for (const id of ids) {
    if (!id.endsWith("-hint")) continue;
    const source = doc.getElementById(id);
    if (source && source.classList && source.classList.contains("hint-text")) return source;
  }
  return null;
}

// Pure placement: where a pop of size {width,height} goes next to the control rect, inside the viewport {width,height}.
export function placeHint(rect, size, viewport, gap = GAP) {
  const fitsBelow = rect.bottom + gap + size.height <= viewport.height;
  const fitsAbove = rect.top - gap - size.height >= 0;
  const fitsRight = rect.right + gap + size.width <= viewport.width;
  const fitsLeft = rect.left - gap - size.width >= 0;
  const side = fitsBelow ? "bottom" : fitsAbove ? "top" : fitsRight ? "right" : fitsLeft ? "left" : "bottom";
  const clamp = (value, max) => Math.max(0, Math.min(value, Math.max(0, max)));
  let left;
  let top;
  if (side === "bottom" || side === "top") {
    left = clamp(rect.left + (rect.right - rect.left) / 2 - size.width / 2, viewport.width - size.width);
    top = clamp(side === "bottom" ? rect.bottom + gap : rect.top - gap - size.height, viewport.height - size.height);
  } else {
    left = clamp(side === "right" ? rect.right + gap : rect.left - gap - size.width, viewport.width - size.width);
    top = clamp(rect.top + (rect.bottom - rect.top) / 2 - size.height / 2, viewport.height - size.height);
  }
  return { side, left: Math.round(left), top: Math.round(top) };
}

export function installHoverHint(doc = document) {
  const win = doc.defaultView;
  if (!win || doc.__hoverHintInstalled) return;
  doc.__hoverHintInstalled = true;
  let pop = null;
  let showTimer = 0;
  let hideTimer = 0;
  let holdTimer = 0;
  let current = null;
  let overPop = false;

  const popup = () => {
    if (pop && pop.isConnected) return pop;
    pop = doc.createElement("div");
    pop.id = HINT_ID;
    pop.className = "hover-hint";
    pop.setAttribute("role", "tooltip");
    pop.setAttribute("aria-hidden", "true");
    pop.hidden = true;
    if (typeof pop.showPopover === "function") pop.setAttribute("popover", "manual"); // top layer: above a modal <dialog>
    pop.addEventListener("pointerenter", () => { overPop = true; win.clearTimeout(hideTimer); });
    pop.addEventListener("pointerleave", () => { overPop = false; scheduleHide(); });
    doc.body.appendChild(pop);
    return pop;
  };
  const controlOf = (node) => {
    const control = node && node.closest ? node.closest("[aria-describedby]") : null;
    return control && hintSourceFor(control, doc) ? control : null;
  };
  const hide = () => {
    win.clearTimeout(showTimer);
    win.clearTimeout(hideTimer);
    win.clearTimeout(holdTimer);
    current = null;
    overPop = false;
    if (pop) {
      pop.hidden = true;
      if (pop.hasAttribute && pop.hasAttribute("popover") && typeof pop.hidePopover === "function") {
        try { pop.hidePopover(); } catch (error) { /* it was not open */ }
      }
    }
  };
  const hideIfOrphan = () => { if (pop && !pop.hidden && current && !current.isConnected) hide(); };
  const show = (control) => {
    const source = hintSourceFor(control, doc);
    if (!source) return;
    win.clearTimeout(hideTimer);
    const text = (source.textContent || "").trim();
    const title = source.getAttribute("data-hint-title") || "";
    if (!text && !title) return;
    const el = popup();
    el.textContent = "";
    if (title) {
      const heading = doc.createElement("strong");
      heading.className = "hover-hint-title";
      heading.textContent = title;
      el.appendChild(heading);
    }
    if (text) {
      const line = doc.createElement("span");
      line.className = "hover-hint-text";
      line.textContent = text;
      el.appendChild(line);
    }
    current = control;
    el.hidden = false;
    if (el.hasAttribute && el.hasAttribute("popover") && typeof el.showPopover === "function") {
      try { if (!el.matches(":popover-open")) el.showPopover(); } catch (error) { /* the pop still shows without the top layer */ }
    }
    const rect = control.getBoundingClientRect();
    const box = el.getBoundingClientRect();
    const where = placeHint(rect, { width: box.width, height: box.height }, { width: win.innerWidth, height: win.innerHeight });
    el.classList.remove("hint-top", "hint-bottom", "hint-left", "hint-right");
    el.classList.add("hint-" + where.side);
    el.style.setProperty("left", where.left + "px");
    el.style.setProperty("top", where.top + "px");
  };
  function scheduleHide() {
    win.clearTimeout(showTimer);
    win.clearTimeout(hideTimer);
    hideTimer = win.setTimeout(() => { if (!overPop) hide(); }, LEAVE_DELAY);
  }

  doc.addEventListener("pointerover", (event) => {
    if (event.pointerType === "touch") return;
    const control = controlOf(event.target);
    if (!control) return;
    if (control === current) { win.clearTimeout(hideTimer); return; }
    win.clearTimeout(showTimer);
    showTimer = win.setTimeout(() => show(control), HOVER_DELAY);
  });
  doc.addEventListener("pointerout", (event) => {
    if (event.pointerType === "touch") return;
    const control = controlOf(event.target);
    if (!control) return;
    if (event.relatedTarget && control.contains && control.contains(event.relatedTarget)) return;
    scheduleHide();
  });
  doc.addEventListener("focusin", (event) => {
    const control = controlOf(event.target);
    if (!control) return;
    let visible = true;
    try { visible = control.matches(":focus-visible"); } catch (error) { visible = true; }
    if (visible) show(control);
  });
  doc.addEventListener("focusout", (event) => { if (controlOf(event.target)) scheduleHide(); });
  doc.addEventListener("pointerdown", (event) => {
    if (pop && !pop.hidden) {
      // a touch (or click) outside the hint and outside its control closes it; touching the hint itself does not
      const inside = (pop.contains && pop.contains(event.target)) || (current && current.contains && current.contains(event.target));
      if (!inside) hide();
    }
    if (event.pointerType !== "touch") return;
    const control = controlOf(event.target);
    if (!control) return;
    win.clearTimeout(holdTimer);
    holdTimer = win.setTimeout(() => show(control), HOLD_DELAY);
  });
  for (const type of ["pointerup", "pointercancel"]) doc.addEventListener(type, () => win.clearTimeout(holdTimer));
  doc.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.isComposing || !pop || pop.hidden) return;
    event.preventDefault();
    // Registered when this module loads, before the shell adds its own capture listener on the document, so stopping the
    // other listeners of the same phase keeps Esc from also closing the drawer or panel behind: it only closes the hint.
    event.stopImmediatePropagation();
    hide();
  }, true);
  win.addEventListener("hashchange", hide);
  if (typeof win.MutationObserver === "function" && doc.body) new win.MutationObserver(hideIfOrphan).observe(doc.body, { childList: true, subtree: true });
}

if (typeof document !== "undefined" && document.body) installHoverHint(document);
else if (typeof document !== "undefined" && typeof document.addEventListener === "function") document.addEventListener("DOMContentLoaded", () => installHoverHint(document), { once: true });
