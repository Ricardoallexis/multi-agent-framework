// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The screen «Decisiones» (CNT-T06): the tasks that wait for a usage limit, each with its decision card. It stands where the
// «Qué tienes que hacer» list will take them when the project view arrives; it reads GET /gui/api/continuity/decisions.
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import { DecisionCard } from "./card.js";
import { usePolled } from "./poll.js";

export function Cards({ token, cards, reload }) {
  return html`<ul class="continuity-list">
    ${cards.map((card) => html`<li key=${card.task_id}><${DecisionCard} token=${token} card=${card} onChanged=${reload} onStale=${reload} /></li>`)}
  </ul>`;
}

export function ContinuityView({ token }) {
  const { loading, data, error, reload, refreshing } = usePolled("/gui/api/continuity/decisions", token);
  const cards = data && Array.isArray(data.decisions) ? data.decisions : [];
  const pending = cards.filter((card) => card.decision === "pending");
  // One list, the tasks that still wait for a decision first: a card keeps its place in the list (and its focus) when it is decided.
  const ordered = [...pending, ...cards.filter((card) => card.decision !== "pending")];
  return html`<section class="continuity-view" aria-label=${t("ext.continuity.navigation.decisions")}>
    <div class="continuity-toolbar">
      <span class="continuity-option"><button type="button" class="btn secondary continuity-btn" disabled=${refreshing}
        ...${helpFor("continuity.refresh")} onClick=${reload}>${refreshing ? t("ext.continuity.refreshing") : t("ext.continuity.refresh")}</button>
        <${Help} topic="continuity.refresh" compact=${true} /></span>
      <p class="continuity-count muted" role="status">${loading ? t("ext.continuity.searching_tasks")
        : pending.length === 0 ? t("ext.continuity.pending_count.zero")
        : pending.length === 1 ? t("ext.continuity.pending_count.one")
          : t("ext.continuity.pending_count.many", { count: pending.length })}</p>
    </div>
    ${error && html`<p class="message error" role="alert">${t("ext.continuity.list.read_error", {
      latest: data ? t("ext.continuity.latest_response") : "", message: error.message,
    })}</p>`}
    ${!loading && cards.length === 0 && !error && html`<p class="muted">${t("ext.continuity.list.empty")}</p>`}
    ${ordered.length > 0 && html`<${Cards} token=${token} cards=${ordered} reload=${reload} />`}
  </section>`;
}
