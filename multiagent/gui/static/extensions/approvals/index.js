// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Extension «approvals» (#approvals, #approvals/<id>, SEC-T04): one-click human approval cards.
// SKL-T03 adds its bundle-scan block here later. Follows ARR2-T04: single page heading from main.js, no own h1-h3.
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "../../js/api.js";
import { getApprovalSessionError } from "../../js/auth.js";
import { registerHelp, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { errorText, fmtDate } from "../../js/text.js";
import { HELP } from "./help.js";
import {
  SESSION_UNAVAILABLE_NOTICE,
  describeApproval,
  hasSelectedApproval,
  orderApprovals,
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
    <dl class="approvals-facts">
      <dt>Destino</dt><dd>${view.destination}</dd>
      <dt>Origen</dt><dd>${view.location}</dd>
      <dt>Tipos</dt><dd>${view.typesText}</dd>
      <dt>Muestra enmascarada</dt><dd><code class="approvals-sample">${view.maskedSample}</code></dd>
    </dl>
    ${cardError && html`<div class="approvals-card-error" role="alert">
      <p class="message error">${cardError.message}</p>
      ${cardError.retryable && html`<div class="approvals-retry">
        <button type="button" class="btn secondary approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.retry_code", { scope })} onClick=${() => decide(cardError.decision)}>Intentar de nuevo</button>
        <${Help} topic="approvals.retry_code" compact=${true} scope=${scope} />
      </div>`}
    </div>`}
    <div class="approvals-actions" role="group" aria-label=${"Decisión para " + view.title}>
      <span class="approvals-action">
        <button type="button" class="btn approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.approve", { scope })} onClick=${() => decide("approve")}>
          ${busy ? "Enviando…" : view.approveLabel}
        </button>
        <${Help} topic="approvals.approve" compact=${true} scope=${scope} />
      </span>
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
    setNotice(decision === "approve" ? "Solicitud aprobada una sola vez." : "Solicitud rechazada.");
    load({ silent: true });
  }

  return html`<section class="approvals-view" aria-label="Aprobaciones" aria-busy=${loading ? "true" : "false"}>
    <div class="approvals-toolbar">
      <span class="approvals-action">
        <button type="button" class="btn secondary approvals-btn" disabled=${refreshing}
          ...${helpFor("approvals.refresh")} onClick=${() => { setNotice(""); load(); }}>
          ${refreshing ? "Actualizando…" : "Actualizar"}
        </button>
        <${Help} topic="approvals.refresh" compact=${true} />
      </span>
      <p class="approvals-count muted" role="status" aria-live="polite">
        ${loading ? "Leyendo aprobaciones pendientes…"
          : ordered.length === 0 ? "No hay aprobaciones pendientes."
          : ordered.length === 1 ? "1 aprobación pendiente." : ordered.length + " aprobaciones pendientes."}
      </p>
    </div>
    ${sessionUnavailable && html`<p class="approvals-notice message error" role="alert">${SESSION_UNAVAILABLE_NOTICE}</p>`}
    ${notice && html`<p class="message ok approvals-status" role="status">${notice}</p>`}
    ${error && html`<p class="message error approvals-status" role="alert">No se pudieron leer las aprobaciones: ${errorText(error)}</p>`}
    ${missingSelected && html`<p class="approvals-missing muted" role="status">La solicitud seleccionada ya no está pendiente.</p>`}
    ${ordered.length > 0 && html`<ul class="approvals-list">
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
  label: "Aprobaciones",
  topic: "approvals.view",
  component: ApprovalsView,
  order: 45,
  owner: "approvals",
});
