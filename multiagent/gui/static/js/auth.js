// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Local token handling: read it from the URL fragment once, keep it in sessionStorage.
const KEY = "multiagent.gui.token";

export function loadToken() {
  const match = /(?:^#|&)token=([^&]+)/.exec(location.hash);
  if (match) {
    try { sessionStorage.setItem(KEY, decodeURIComponent(match[1])); } catch (_) { /* storage blocked */ }
    // Drop the fragment so the token is not left in the address bar or history.
    history.replaceState(null, "", location.pathname + location.search);
    return decodeURIComponent(match[1]);
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
