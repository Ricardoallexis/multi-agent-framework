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
      <span id=${id + "-hint"} class="hint-text" hidden=${compact && !open}>${hint || data.hint}</span>
      <button type="button" class="help-toggle" aria-expanded=${open ? "true" : "false"} aria-controls=${id + "-more"}
        aria-label=${t("shell.more_information", { title: data.title })} title=${t("shell.what_is_this")} onClick=${() => setOpen(!open)}>?</button>
    </div>
    <div id=${id + "-more"} class="help-more" hidden=${!open}>${sentences.join(" ")}</div>
  </div>`;
}
