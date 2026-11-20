// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Local token handling: read it from the URL fragment once, keep it in sessionStorage.
const KEY = "multiagent.gui.token";
let approvalSessionError = "";

export function getApprovalSessionError() { return approvalSessionError; }

function reportApprovalSessionError(message) {
  approvalSessionError = message;
  window.dispatchEvent(new CustomEvent("approval-session-error", { detail: message }));
}

export function loadToken() {
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ""));
  const match = fragment.get("token");
  if (match) {
    try { sessionStorage.setItem(KEY, match); } catch (_) { /* storage blocked */ }
    // Drop the fragment so the token is not left in the address bar or history.
    history.replaceState(null, "", location.pathname + location.search);
    const approval = fragment.get("approval");
    if (approval) {
      fetch("/gui/api/approvals/session", {
        method: "POST", headers: { ...authHeaders(match), "Content-Type": "application/json" },
        body: JSON.stringify({ code: approval }), credentials: "same-origin", cache: "no-store",
      }).then(async response => {
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          reportApprovalSessionError(detail?.detail?.message || "Reinicia la GUI para tomar las aprobaciones.");
        }
      }).catch(() => reportApprovalSessionError("No se pudo iniciar la sesión de aprobaciones."));
    }
    return match;
  }
  try { return sessionStorage.getItem(KEY) || ""; } catch (_) { return ""; }
}

export function clearToken() {
  try { sessionStorage.removeItem(KEY); } catch (_) { /* ignore */ }
}

export function authHeaders(token) {
  return { Authorization: "Bearer " + token, Accept: "application/json" };
}

// signal (optional): an AbortSignal that cancels the request (main.js uses it to bound the wait for the extension list).
export async function getJSON(path, token, signal) {
  const response = await fetch(path, { headers: authHeaders(token), cache: "no-store", signal });
  if (!response.ok) throw Object.assign(new Error("HTTP " + response.status), { status: response.status });
  return response.json();
}
