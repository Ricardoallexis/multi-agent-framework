// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Tabs (GUI2-T11): the WAI-ARIA tabs pattern, with the keyboard that goes with it.
//
//   <${Tabs} id="sub" label="Estado de los subagentes" selected=${tab} onSelect=${setTab}
//       tabs=${[{ id: "active", label: "Activos", count: 3 }, { id: "done", label: "Terminados", count: 1 }]}
//       after=${html`<${Help} topic="subagents.tabs" compact=${true} />`} ...${helpFor("subagents.tabs")}>
//     ...the content of the selected tab...
//   <//>
//
//   id         prefix of the element ids: tab "<id>-tab-<tabId>", panel "<id>-panel" (panelId overrides it)
//   label      accessible name of the tab list
//   tabs       [{ id, label, count? }]; count (a number) shows as "Label · count"
//   selected   id of the selected tab; onSelect(tabId) is called on click and on the arrow keys, Home and End
//   orientation "horizontal" (default: Left/Right) or "vertical" (Up/Down); Home and End work in both
//   after      a node drawn next to the tab list (the "?" help button of the tabs)
//   panelProps extra attributes of the panel (aria-busy, ...); className goes to the outer element
//   children   the panel content; the panel is role="tabpanel", labelled by its tab and focusable (tabindex 0)
//   (other props)  the aria-describedby that help.js gives with helpFor goes to every tab button
// Markup: role="tablist" > role="tab" with aria-selected, aria-controls and a roving tabindex (only the selected tab is in
// the Tab order; the arrows move between tabs and select them, as the old subagents tabs did). Plain text only.
import { html } from "../../vendor/htm-preact-standalone.module.js";

export const tabDomIds = (id, tabId, panelId = "") => ({ tab: id + "-tab-" + tabId, panel: panelId || id + "-panel" });

// Which tab a key moves to, or -1 when the key is not a tab key. Wraps around at both ends.
export function nextTabIndex(key, current, count, { orientation = "horizontal" } = {}) {
  if (!(count > 0)) return -1;
  const vertical = orientation === "vertical";
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  if (key === (vertical ? "ArrowDown" : "ArrowRight")) return (current + 1) % count;
  if (key === (vertical ? "ArrowUp" : "ArrowLeft")) return (current - 1 + count) % count;
  return -1;
}

export const tabText = (tab) => tab.label + (typeof tab.count === "number" ? " · " + tab.count : "");

export function Tabs({ id, label, tabs, selected, onSelect, orientation = "horizontal", after = null, panelProps = {},
  panelId = "", className = "", children, ...describe }) {
  const vertical = orientation === "vertical";
  const current = tabs.some((tab) => tab.id === selected) ? selected : tabs.length ? tabs[0].id : null;
  const panel = tabDomIds(id, current, panelId);

  const onKeyDown = (event) => {
    if (event.ctrlKey || event.altKey || event.metaKey || event.shiftKey || event.isComposing) return;
    const buttons = Array.from(event.currentTarget.querySelectorAll('[role="tab"]'));
    const at = buttons.indexOf(event.target.closest ? event.target.closest('[role="tab"]') : event.target);
    if (at < 0) return;
    const next = nextTabIndex(event.key, at, tabs.length, { orientation });
    if (next < 0) return;
    event.preventDefault();
    onSelect(tabs[next].id);
    if (buttons[next]) buttons[next].focus();
  };

  return html`<div class=${"tabs-wrap" + (className ? " " + className : "")}>
    <div class="tabs-row">
      <div class="tabs" role="tablist" aria-label=${label} aria-orientation=${vertical ? "vertical" : null} onKeyDown=${onKeyDown}>
        ${tabs.map((tab) => html`<button key=${tab.id} type="button" role="tab" class="tab" data-help-exempt="component"
          id=${tabDomIds(id, tab.id).tab} aria-selected=${tab.id === current ? "true" : "false"} aria-controls=${panel.panel}
          tabindex=${tab.id === current ? 0 : -1} ...${describe} onClick=${() => onSelect(tab.id)}>${tabText(tab)}</button>`)}
      </div>
      ${after}
    </div>
    <div id=${panel.panel} class="tab-panel" role="tabpanel" aria-labelledby=${current === null ? null : panel.tab} tabindex="0" ...${panelProps}>${children}</div>
  </div>`;
}
