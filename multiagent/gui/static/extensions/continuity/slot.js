// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The section «Continuidad» of the right panel (CNT-T06): the decision card of the task held on the session that is open, or a line
// saying that nothing waits. It reads GET /gui/api/continuity/session/<id>.
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { useContentReport } from "../../js/context-report.js";
import { t } from "../../js/i18n.js";
import { DecisionCard } from "./card.js";
import { handoffNoticeText } from "./model.js";
import { usePolled } from "./poll.js";

export function ContinuitySlot({ token, sessionId, onContent }) {
  const path = sessionId ? "/gui/api/continuity/session/" + encodeURIComponent(sessionId) : null;
  const { loading, data, error, reload } = usePolled(path, token);
  const held = Boolean(data && data.held && data.card);
  useContentReport(onContent, held, held);   // GUI3-T04: a task held on a usage limit is urgent, so it shows in every mode
  if (!sessionId) return html`<p class="muted small slot-empty">${t("ext.continuity.slot.open_session")}</p>`;
  if (loading) return html`<p class="muted small" role="status">${t("ext.continuity.searching")}</p>`;
  if (!data) return html`<p class="message error small" role="alert">${t("ext.continuity.slot.read_error", {
    message: error ? ": " + error.message : ".",
  })}</p>`;
  if (!data.held || !data.card) {
    const notice = handoffNoticeText(data.last_handoff);       // USO-T08: a hand-off left this session; the notice says why and where to
    return html`<div class="continuity-slot">
      <p class="muted small slot-empty">${t("ext.continuity.slot.not_waiting")}</p>
      ${notice && html`<p class="continuity-handoff-notice" role="status"><strong>${t("ext.continuity.handoff_notice.title")}</strong> ${notice}</p>`}
    </div>`;
  }
  return html`<div class="continuity-slot">
    ${error && html`<p class="message error small" role="alert">${t("ext.continuity.slot.refresh_error", { message: error.message })}</p>`}
    <${DecisionCard} key=${data.card.task_id} token=${token} card=${data.card} showOpen=${false} onChanged=${reload} onStale=${reload} />
  </div>`;
}
