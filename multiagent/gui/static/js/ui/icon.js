// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Icon: small decorative SVG marks for navigation and controls.
// Usage: <${Icon} name="folder" />
//   name   one of ICON_NAMES; an unknown name shows a neutral dot.
// Icons never supply an accessible name: the surrounding control keeps its text.
// The stroke, size and focus target come from app.css and the surrounding control.
// No image files, downloaded assets or inline styles are used.
// Paths use one 24 by 24 coordinate system and rounded joins.
// The unknown-name fallback is intentionally quiet and decorative.
import { html } from "../../vendor/htm-preact-standalone.module.js";

const PATHS = {
  more: "M5 12h14 M12 5v14",
  search: "M20 20l-4.3-4.3 M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13",
  inbox: "M4 5h16v12l-3 3H7l-3-3V5 M4 14h5l1.5 2h3L15 14h5",
  folder: "M3 6h7l2 2h9v11H3V6",
  apps: "M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z",
  dots: "M5 12h.01 M12 12h.01 M19 12h.01",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  panel: "M4 4h16v16H4z M15 4v16",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  help: "M9 9a3 3 0 1 1 4 2.8c-.7.4-1 1-1 2.2 M12 18h.01",
  settings: "M12 3v2 M12 19v2 M3 12h2 M19 12h2 M5.6 5.6 7 7 M17 17l1.4 1.4 M18.4 5.6 17 7 M7 17l-1.4 1.4 M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8",
  send: "M3 11l18-8-8 18-2-8-8-2 M11 13l10-10",
  shield: "M12 2l8 4v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6z M9 12l2 2 4-4",
  close: "M5 5l14 14 M19 5L5 19",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2",
  file: "M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6",
  person: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M4 21c0-4 3-7 8-7s8 3 8 7",
  link: "M10 14l4-4 M8 16H6a4 4 0 0 1 0-8h4 M14 8h4a4 4 0 0 1 0 8h-4",
  copy: "M8 8h12v13H8z M5 17H4V4h12v1",
  refresh: "M20 8a8 8 0 0 0-14-2L4 8 M4 4v4h4 M4 16a8 8 0 0 0 14 2l2-2 M20 20v-4h-4",
  run: "M6 4l13 8-13 8z",
  chart: "M4 20V12 M10 20V5 M16 20v-8 M22 20V8",
  check: "M4 12l5 5L20 6",
  map: "M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3z M9 3v15 M15 6v15",
  building: "M3 21h18 M5 21V7l7-4 7 4v14 M9 21v-6h6v6 M9 9h.01 M15 9h.01 M9 12h.01 M15 12h.01",
};
export const ICON_NAMES = Object.freeze(Object.keys(PATHS));

export function Icon({ name }) {
  return html`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    ${PATHS[name] ? html`<path d=${PATHS[name]} />` : html`<circle cx="12" cy="12" r="2" />`}
  </svg>`;
}
