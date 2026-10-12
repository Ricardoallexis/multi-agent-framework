// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Progress (GUI2-T11): a progress bar that says its value in words, not only in length.
//
//   <${Progress} label="Pasos hechos" value=${3} max=${5} />            "3 de 5" and a bar at 60 %
//   <${Progress} label="Contexto usado" value=${63} valueText="63 % del contexto" tone="warning" />   max defaults to 100
//   <${Progress} label="Cargando" />                                      no value: indeterminate (no aria-valuenow)
//
//   label      what is being measured; it is the accessible name of the bar and is also shown (hideLabel: only for screen readers)
//   value/max  the measure; min defaults to 0 and max to 100; a value outside the range is clamped; max <= min is indeterminate
//   valueText  the words ("3 de 5 pasos"); default: "N de M", or "N %" on a 0..100 scale
//   tone       a tone of labels.js for the fill (default "active")
// The bar is role="progressbar" with aria-valuemin, aria-valuemax, aria-valuenow, aria-valuetext and aria-label. The fill is an
// SVG rect (a CSS style attribute would break the CSP), so the length is data, not style.
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { normalizeTone } from "./labels.js";

const finite = (value) => typeof value === "number" && Number.isFinite(value);

// The numbers behind a bar: { determinate, min, max, now, percent, valueText }.
export function progressInfo({ value = null, max = 100, min = 0, valueText = "" } = {}) {
  const low = finite(min) ? min : 0;
  const high = finite(max) ? max : 100;
  if (!finite(value) || high <= low) {
    return { determinate: false, min: low, max: high, now: null, percent: null, valueText: valueText || "En curso" };
  }
  const now = Math.min(Math.max(value, low), high);
  const percent = Math.round(((now - low) / (high - low)) * 100);
  const scale = low === 0 && high === 100;
  const text = valueText || (scale ? percent + " %" : now + " de " + high);
  return { determinate: true, min: low, max: high, now, percent, valueText: text };
}

export function Progress({ label, value = null, max = 100, min = 0, valueText = "", tone = "active", hideLabel = false, className = "", ...rest }) {
  const info = progressInfo({ value, max, min, valueText });
  const aria = { "aria-valuemin": info.min, "aria-valuemax": info.max, "aria-valuetext": info.valueText };
  if (info.determinate) aria["aria-valuenow"] = info.now;
  return html`<div class=${"progress tone-" + normalizeTone(tone) + (info.determinate ? "" : " indeterminate") + (className ? " " + className : "")} ...${rest}>
    <div class="progress-head" aria-hidden="true">
      <span class=${"progress-label" + (hideLabel ? " sr-only" : "")}>${label}</span>
      <span class="progress-text">${info.valueText}</span>
    </div>
    <div class="progress-track" role="progressbar" aria-label=${label} ...${aria}>
      <svg class="progress-svg" viewBox="0 0 100 1" preserveAspectRatio="none" focusable="false" aria-hidden="true">
        <rect class="progress-fill" x="0" y="0" width=${info.determinate ? info.percent : 100} height="1"></rect>
      </svg>
    </div>
  </div>`;
}
