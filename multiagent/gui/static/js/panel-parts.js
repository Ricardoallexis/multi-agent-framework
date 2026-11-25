// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Small pieces shared by the Changes, Tokens, and Sources sections (GUI2-T05): an empty state, the
// loading line, the failure box with Retry, the Show more toggle and the +/- counts of a file. Everything is
// plain text through htm/Preact (no HTML is ever built from data). Kept local and small on purpose: a later change adopts
// the shared components of GUI2-T11 (StatusChip, Progress, Tabs, LevelBadge) in their place.
import { html } from "../vendor/htm-preact-standalone.module.js";
import { Help, helpFor } from "./help.js";
import { t } from "./i18n.js";
import { fmtInt } from "./panel-text.js";
import { errorText } from "./text.js";

export function Empty({ children }) {
  return html`<p class="muted small ctx-note">${children}</p>`;
}

export function Loading({ children }) {
  return html`<p class="muted small ctx-note" role="status" aria-busy="true">${children}</p>`;
}

// `scope` keeps the ids of the help unique (one retry per section). `stale` means the data on screen is older than
// the failure: the box is then a note under the data and not the whole content.
export function Failure({ error, scope, what, onRetry, stale = false }) {
  return html`<div class=${"message error ctx-error" + (stale ? " stale" : "")} role="alert">
    ${stale ? t("panel.update_failed") : t("panel.read_failed")}${what}: ${errorText(error)}
    <button type="button" class="btn secondary small" ...${helpFor("panel.retry", { scope })} onClick=${onRetry}>${t("panel.retry")}</button>
    <${Help} topic="panel.retry" scope=${scope} compact=${true} />
  </div>`;
}

// "Show 5 more" / "Show less" under a list that shows its first rows.
export function MoreToggle({ scope, total, shown, expanded, onToggle }) {
  if (total <= shown) return null;
  return html`<div class="ctx-more-row">
    <button type="button" class="ctx-more" aria-expanded=${expanded ? "true" : "false"} ...${helpFor("panel.more", { scope })}
      onClick=${onToggle}>${expanded ? t("panel.show_less") : t("panel.show_prefix") + (total - shown) + t("panel.show_more")}</button>
    <${Help} topic="panel.more" scope=${scope} compact=${true} />
  </div>`;
}

// «+12 −4»; a side the source did not count is left out (never shown as 0). Colour is not the only cue: the sign is text.
export function Delta({ file }) {
  const plus = file.additions;
  const minus = file.deletions;
  if (plus === null && minus === null) return null;
  return html`<span class="ctx-delta">
    ${plus !== null && html`<span class="ctx-plus"><span aria-hidden="true">+</span>${fmtInt(plus)}<span class="sr-only">${t("panel.added_lines")}</span></span>`}
    ${minus !== null && html`<span class="ctx-minus"><span aria-hidden="true">−</span>${fmtInt(minus)}<span class="sr-only">${t("panel.removed_lines")}</span></span>`}
  </span>`;
}
