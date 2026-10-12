// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// LevelBadge (GUI2-T11): the model level T1..T4 as text, colour and a count of filled marks, never colour alone.
//
//   <${LevelBadge} level="T2" />                  "T2" (screen readers: "Nivel T2, estándar")
//   <${LevelBadge} level=${child.level} showName=${true} />   "T2 estándar"
//   levelInfo("t3")  ->  { id: "T3", rank: 3, name: "avanzado", label: "Nivel T3, avanzado" }   (null when it is not a level)
//
//   level     "T1".."T4" (case and spaces ignored); anything else, or null (a model the catalog has not classified), draws nothing
//   showName  also show the name of the level ("económico", "estándar", "avanzado", "máximo")
//   className / any other attribute goes to the outer element
// The colours are the ones of the design (--lvl-1..4 in app.css): the badge has its own fill, so it reads the same in light and dark.
import { html } from "../../vendor/htm-preact-standalone.module.js";

export const LEVELS = Object.freeze({
  T1: { rank: 1, name: "económico" },
  T2: { rank: 2, name: "estándar" },
  T3: { rank: 3, name: "avanzado" },
  T4: { rank: 4, name: "máximo" },
});

// null when `level` is not T1..T4; otherwise everything a view needs to say about it.
export function levelInfo(level) {
  const match = /^T?([1-4])$/i.exec(String(level === null || level === undefined ? "" : level).trim());
  if (!match) return null;
  const id = "T" + match[1];
  const { rank, name } = LEVELS[id];
  return { id, rank, name, label: "Nivel " + id + ", " + name };
}

export function LevelBadge({ level, showName = false, className = "", ...rest }) {
  const info = levelInfo(level);
  if (!info) return null;
  return html`<span class=${"level level-" + info.rank + (className ? " " + className : "")} data-level=${info.id} title=${info.label} ...${rest}>
    <span class="level-pips" aria-hidden="true">${[1, 2, 3, 4].map((n) => html`<i key=${n} class=${n <= info.rank ? "on" : ""}></i>`)}</span>
    <span class="level-text" aria-hidden="true">${info.id}${showName ? " " + info.name : ""}</span>
    <span class="sr-only">${info.label}</span>
  </span>`;
}
