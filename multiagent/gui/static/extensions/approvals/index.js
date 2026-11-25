// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Extension «approvals» (#approvals, #approvals/<id>, SEC-T04): one-click human approval cards.
// SKL-T03 adds its bundle-scan block here later. Follows ARR2-T04: single page heading from main.js, no own h1-h3.
import "./i18n.js";
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "../../js/api.js";
import { getApprovalSessionError } from "../../js/auth.js";
import { registerHelp, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { errorText, fmtDate } from "../../js/text.js";
import { t } from "../../js/i18n.js";
import { HELP } from "./help.js";
import {
  describeApproval,
  hasSelectedApproval,
  orderApprovals,
  sessionUnavailableNotice,
  submitApprovalDecision,
} from "./model.js";
import { BundleScanCard, BundleScanStatus, isBundleScanApproval } from "./bundle-scan.js";

registerHelp(HELP, "approvals");

const APPROVALS_PATH = "/gui/api/approvals";
const POLL_MS = 10000;
const scopeOf = (id) => String(id).replace(/[^A-Za-z0-9_-]+/g, "-");
const asError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));

function ApprovalCard({ item, token, selected, sessionUnavailable, onSessionUnavailable, onDecided, onStale }) {
  const [busy, setBusy] = useState(false);
  const [cardError, setCardError] = useState(null);
  const view = describeApproval(item);
  const scope = scopeOf(view.id || "item");
  const titleId = "approvals-card-" + scope + "-title";

  async function decide(decision) {
    if (busy || sessionUnavailable) return;
    setBusy(true);
    setCardError(null);
    const outcome = await submitApprovalDecision({
      item,
      decision,
      post: (path, body) => apiPost(path, token, body),
    });
    setBusy(false);
    if (outcome.ok) {
      try {
        window.dispatchEvent(new CustomEvent("multiagent:approval-decided", {
          detail: { id: item.id, decision: decision === "approve" ? "approved" : "rejected" },
        }));
      } catch (_) { /* ignore */ }
      onDecided(item.id, decision);
      return;
    }
    if (outcome.sessionUnavailable) {
      onSessionUnavailable();
    }
    setCardError({ message: outcome.message, retryable: Boolean(outcome.retryable), decision });
    if (outcome.stale) onStale();
  }

  return html`<li class=${"approvals-card" + (selected ? " approvals-card-selected" : "")} aria-labelledby=${titleId}>
    <div class="approvals-card-head">
      <p id=${titleId} class="approvals-card-title"><strong>${view.title}</strong></p>
      <time class="approvals-time muted small" dateTime=${view.createdAt || null}>${fmtDate(view.createdAt)}</time>
    </div>
    <p class="approvals-reason">${view.reason}</p>
    ${view.conflict ? html`<dl class="approvals-facts approvals-clash">
      <dt>${t("ext.approvals.new_note")}</dt><dd><strong class="approvals-clash-title">${view.conflict.new.title}</strong>
        <span class="approvals-clash-body">${view.conflict.new.body}</span></dd>
      <dt>${t("ext.approvals.your_note")}</dt><dd><strong class="approvals-clash-title">${view.conflict.existing.title}</strong>
        <span class="approvals-clash-body">${view.conflict.existing.body}</span></dd>
    </dl>` : view.memory ? html`<dl class="approvals-facts">
      <dt>${t("ext.approvals.note")}</dt><dd>${view.entryTitle}</dd>
    </dl>` : html`<dl class="approvals-facts">
      <dt>${t("ext.approvals.destination")}</dt><dd>${view.destination}</dd>
      <dt>${t("ext.approvals.origin")}</dt><dd>${view.location}</dd>
      <dt>${t("ext.approvals.types")}</dt><dd>${view.typesText}</dd>
      <dt>${t("ext.approvals.masked_sample")}</dt><dd><code class="approvals-sample">${view.maskedSample}</code></dd>
    </dl>`}
    ${cardError && html`<div class="approvals-card-error" role="alert">
      <p class="message error">${cardError.message}</p>
      ${cardError.retryable && html`<div class="approvals-retry">
        <button type="button" class="btn secondary approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.retry_code", { scope })} onClick=${() => decide(cardError.decision)}>${t("ext.approvals.try_again")}</button>
        <${Help} topic="approvals.retry_code" compact=${true} scope=${scope} />
      </div>`}
    </div>`}
    <div class="approvals-actions" role="group" aria-label=${t("ext.approvals.decision_for", { title: view.title })}>
      ${view.kind === "memory_trust" && view.conflict && html`<span class="approvals-action">
        <button type="button" class="btn approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.memory_clash", { scope })} onClick=${() => decide("approve")}>
          ${busy ? t("ext.approvals.sending") : view.approveLabel}
        </button>
        <${Help} topic="approvals.memory_clash" compact=${true} scope=${scope} />
      </span>`}
      ${view.kind === "memory_trust" && !view.conflict && html`<span class="approvals-action">
        <button type="button" class="btn approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.memory_trust", { scope })} onClick=${() => decide("approve")}>
          ${busy ? t("ext.approvals.sending") : view.approveLabel}
        </button>
        <${Help} topic="approvals.memory_trust" compact=${true} scope=${scope} />
      </span>`}
      ${view.kind === "memory_delete" && html`<span class="approvals-action">
        <button type="button" class="btn approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.memory_delete", { scope })} onClick=${() => decide("approve")}>
          ${busy ? t("ext.approvals.sending") : view.approveLabel}
        </button>
        <${Help} topic="approvals.memory_delete" compact=${true} scope=${scope} />
      </span>`}
      ${!view.memory && html`<span class="approvals-action">
        <button type="button" class="btn approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.approve", { scope })} onClick=${() => decide("approve")}>
          ${busy ? t("ext.approvals.sending") : view.approveLabel}
        </button>
        <${Help} topic="approvals.approve" compact=${true} scope=${scope} />
      </span>`}
      <span class="approvals-action">
        <button type="button" class="btn secondary approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.reject", { scope })} onClick=${() => decide("reject")}>
          ${view.rejectLabel}
        </button>
        <${Help} topic="approvals.reject" compact=${true} scope=${scope} />
      </span>
    </div>
  </li>`;
}

export function ApprovalsView({ token, route }) {
  const selectedId = (route && route.id) || null;
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState("");
  const [sessionUnavailable, setSessionUnavailable] = useState(() => Boolean(getApprovalSessionError()));
  const ticket = useRef(0);
  const alive = useRef(true);

  async function load({ silent = false } = {}) {
    const mine = ++ticket.current;
    if (!silent) setRefreshing(true);
    try {
      const answer = await apiGet(APPROVALS_PATH, token);
      if (!alive.current || mine !== ticket.current) return;
      setItems(Array.isArray(answer?.items) ? answer.items : []);
      setError(null);
    } catch (failure) {
      if (!alive.current || mine !== ticket.current) return;
      setError(asError(failure));
    } finally {
      if (alive.current && mine === ticket.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    load();
    const timer = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) load({ silent: true });
    }, POLL_MS);
    return () => { alive.current = false; ticket.current += 1; clearInterval(timer); };
  }, [token]);

  useEffect(() => {
    if (getApprovalSessionError()) setSessionUnavailable(true);
    const onSessionError = () => setSessionUnavailable(true);
    window.addEventListener("approval-session-error", onSessionError);
    return () => window.removeEventListener("approval-session-error", onSessionError);
  }, []);

  const ordered = orderApprovals(items, selectedId);
  const missingSelected = !loading && !error && !hasSelectedApproval(ordered, selectedId);

  function handleDecided(id, decision) {
    setItems((current) => current.filter((item) => item.id !== id));
    setNotice(decision === "approve" ? t("ext.approvals.approved_once") : t("ext.approvals.rejected"));
    load({ silent: true });
  }

  return html`<section class="approvals-view" aria-label=${t("ext.approvals.view")} aria-busy=${loading ? "true" : "false"}>
    <div class="approvals-toolbar">
      <span class="approvals-action">
        <button type="button" class="btn secondary approvals-btn" disabled=${refreshing}
          ...${helpFor("approvals.refresh")} onClick=${() => { setNotice(""); load(); }}>
          ${refreshing ? t("ext.approvals.refreshing") : t("ext.approvals.refresh")}
        </button>
        <${Help} topic="approvals.refresh" compact=${true} />
      </span>
      <p class="approvals-count muted" role="status" aria-live="polite">
        ${loading ? t("ext.approvals.loading")
          : ordered.length === 0 ? t("ext.approvals.pending.none")
          : ordered.length === 1 ? t("ext.approvals.pending.one")
          : t("ext.approvals.pending.many", { count: ordered.length })}
      </p>
    </div>
    ${sessionUnavailable && html`<p class="approvals-notice message error" role="alert">${sessionUnavailableNotice()}</p>`}
    ${notice && html`<p class="message ok approvals-status" role="status">${notice}</p>`}
    ${error && html`<p class="message error approvals-status" role="alert">${t("ext.approvals.read_failed")}: ${errorText(error)}</p>`}
    ${missingSelected && html`<p class="approvals-missing muted" role="status">${t("ext.approvals.selected_not_pending")}</p>`}
    ${ordered.length > 0 && html`<ul class="approvals-list help-always">
      ${ordered.map((item) => html`<${isBundleScanApproval(item) ? BundleScanCard : ApprovalCard} key=${item.id} item=${item} token=${token}
        selected=${Boolean(selectedId && item.id === selectedId)}
        sessionUnavailable=${sessionUnavailable}
        onSessionUnavailable=${() => setSessionUnavailable(true)}
        onDecided=${handleDecided}
        onStale=${() => load({ silent: true })} />`)}
    </ul>`}
    <${BundleScanStatus} token=${token} items=${ordered} onRefresh=${() => load({ silent: true })} />
  </section>`;
}

registerView({
  id: "approvals",
  label: t("ext.approvals.view"),
  topic: "approvals.view",
  component: ApprovalsView,
  order: 45,
  owner: "approvals",
  group: "pending",
  icon: "shield",
  pendingType: "approval",
  mode: "simple",
});
