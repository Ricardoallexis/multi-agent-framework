// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Ctrl+K (GUI3-T07): one native <dialog> (showModal) with a search field. It finds screens, offices, areas, projects, recent
// conversations and help titles; Up and Down move, Intro opens, Esc closes, and focus goes back to where it was. It only
// navigates and explains: opening an entry changes the address or shows a help text as plain text, nothing else.
// The entries come from the sidebar (sidebar.js) and the model is command-palette-model.js.
import { html, useEffect, useLayoutEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { groupPaletteResults, KINDS, keyAction, moveIndex, openEntry, searchPalette } from "./command-palette-model.js";
import { Help, helpFor } from "./help.js";
import { t } from "./i18n.js";

// open: whether the dialog is shown; onClose(): the parent forgets it; onGo(route): the parent navigates.
export function CommandPalette({ open, entries, onClose, onGo, areaStatus = "idle" }) {
  const dialog = useRef(null);
  const field = useRef(null);
  const before = useRef(null);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const [detail, setDetail] = useState(null);
  const results = open ? searchPalette(entries, query) : [];
  const groups = open ? groupPaletteResults(results) : [];
  const active = results.length ? Math.min(index, results.length - 1) : -1;

  // Before paint: keys typed right after Ctrl+K or the «Buscar» row already land in the field (a plain effect waits for the
  // next frame, which a hidden tab may delay).
  useLayoutEffect(() => {
    const box = dialog.current;
    if (!box) return;
    if (open && !box.open) {
      before.current = document.activeElement;
      setQuery(""); setIndex(0); setDetail(null);
      if (typeof box.showModal === "function") box.showModal(); else box.setAttribute("open", "");
      if (field.current) field.current.focus();
    } else if (!open && box.open) {
      box.close();
    }
  }, [open]);

  // The dialog closes itself on Esc: the parent and the focus follow.
  const closed = () => {
    onClose();
    const back = before.current;
    before.current = null;
    if (back && typeof back.focus === "function" && document.contains(back)) back.focus();
  };

  const latest = useRef(closed);
  latest.current = closed;
  useEffect(() => {
    const box = dialog.current;
    const onClosed = () => latest.current();
    box.addEventListener("close", onClosed);
    return () => box.removeEventListener("close", onClosed);
  }, []);

  const choose = (entry) => {
    const action = openEntry(entry);
    if (!action) return;
    if (action.show) { setDetail(action.show); return; }
    if (dialog.current && dialog.current.open) dialog.current.close();
    onGo(action.route);
  };

  const onKeyDown = (event) => {
    const action = keyAction(event);
    if (action === "move") {
      event.preventDefault();
      setIndex(moveIndex(active, event.key, results.length));
    } else if (action === "open") {
      event.preventDefault();
      choose(results[active]);
    } else if (event.key === "Escape" && !event.isComposing && !event.defaultPrevented) {
      // One Esc closes, also with text in the field: a search field would spend the first one clearing itself.
      event.preventDefault();
      dialog.current.close();
    }
  };

  const optionId = (position) => "palette-option-" + position;
  return html`<dialog ref=${dialog} class="palette" aria-labelledby="palette-title"
    onClick=${(event) => { if (event.target === dialog.current) dialog.current.close(); }}>
    ${open && html`<div class="palette-box">
      <h2 id="palette-title" class="sr-only">${t("palette.search")}</h2>
      <form role="search" class="palette-form" onSubmit=${(event) => event.preventDefault()}>
        <label for="palette-input">${t("palette.search")}</label>
        <div class="palette-row">
          <input id="palette-input" ref=${field} type="search" maxlength="100" autocomplete="off" role="combobox"
            aria-expanded="true" aria-controls="palette-list" aria-autocomplete="list"
            aria-activedescendant=${active >= 0 ? optionId(active) : null}
            placeholder=${t("palette.placeholder")} ...${helpFor("palette.search")} value=${query}
            onInput=${(event) => { setQuery(event.target.value); setIndex(0); setDetail(null); }} onKeyDown=${onKeyDown} />
          <button type="button" class="btn secondary" ...${helpFor("palette.close")} onClick=${() => dialog.current.close()}>${t("palette.close")}</button>
        </div>
        <${Help} topic="palette.search" compact=${true} />
        <${Help} topic="palette.close" compact=${true} />
      </form>
      ${areaStatus === "loading" && html`<p class="muted small" role="status">${t("palette.loading_areas")}</p>`}
      ${areaStatus === "error" && html`<p class="message error small" role="alert">${t("palette.areas_error")}</p>`}
      <ul id="palette-list" class="palette-list" role="listbox" aria-label=${t("palette.results")}>
        ${groups.map((group) => html`<li key=${group.key} class="palette-group" role=${group.titleKey ? "group" : "presentation"}
          aria-label=${group.titleKey ? (group.subtitleKey ? t(group.titleKey) + ": " + t(group.subtitleKey) : t(group.titleKey)) : null}>
          ${group.titleKey && html`<span aria-hidden="true" class="palette-kind palette-group-title">${t(group.titleKey)}</span>`}
          ${group.subtitleKey && html`<span aria-hidden="true" class="palette-kind palette-subgroup">${t(group.subtitleKey)}</span>`}
          ${group.items.map(({ entry, position }) => html`<div key=${entry.key} id=${optionId(position)} role="option" class="palette-option"
            aria-selected=${position === active ? "true" : "false"} onClick=${() => choose(entry)}>
            <span class="palette-label">${entry.label}</span>
            <span class="palette-kind">${KINDS[entry.kind]}${entry.hint && entry.kind !== "help" ? " · " + entry.hint : ""}</span>
          </div>`)}
        </li>`)}
      </ul>
      <p class="muted small" aria-live="polite">${results.length ? results.length + t(results.length === 1 ? "palette.result_one" : "palette.result_many") : t("palette.no_match", { query: query.trim() })}</p>
      ${detail && html`<div class="palette-detail" role="region" aria-label=${t("palette.explanation")}>
        <p class="palette-detail-title">${detail.title}</p>
        <p>${detail.hint}</p>
        <p class="muted">${detail.more.join(" ")}</p>
      </div>`}
    </div>`}
  </dialog>`;
}
