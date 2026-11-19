// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The run conversation in Nueva sesión. Reads only while this view is mounted and visible.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { apiGet, apiPost, runPath } from "./api.js";
import { Help, helpFor } from "./help.js";
import { errorText } from "./text.js";
import { ThreadCard } from "./thread-cards.js";
import { POLL_MS, createThread, entryKey, isTerminal, mergeThread, pollAfter } from "./thread-model.js";

export function RunThread({ token, runId }) {
  const [thread, setThread] = useState(createThread);
  const [error, setError] = useState(null);
  const [reviewView, setReviewView] = useState(null);
  const [busy, setBusy] = useState(false);
  const current = useRef(createThread());
  const ticket = useRef(0);
  const inFlight = useRef(false);

  async function load(full = false) {
    if (document.hidden) return;
    if (inFlight.current && !full) return;
    inFlight.current = true;
    const request = ++ticket.current;
    try {
      const after = full ? 0 : pollAfter(current.current);
      const page = await apiGet("/gui/api/threads/runs/" + encodeURIComponent(runId) + "?after=" + after, token);
      let view = null;
      let reviewFailure = null;
      if (page.status === "waiting_human") {
        try { view = await apiGet(runPath(runId), token); }
        catch (failure) { reviewFailure = failure; }
      }
      if (request !== ticket.current) return;
      current.current = mergeThread(full ? createThread() : current.current, page);
      setThread(current.current);
      setReviewView(view);
      setError(reviewFailure);
    } catch (failure) {
      if (request === ticket.current) setError(failure);
    } finally {
      if (request === ticket.current) inFlight.current = false;
    }
  }

  useEffect(() => {
    current.current = createThread(); setThread(current.current); setReviewView(null); setError(null);
    load(true);
    const timer = setInterval(() => { if (!isTerminal(current.current.status)) load(); }, POLL_MS);
    const visible = () => { if (!document.hidden && !isTerminal(current.current.status)) load(); };
    document.addEventListener("visibilitychange", visible);
    return () => { ticket.current += 1; inFlight.current = false; clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, [runId, token]);

  async function act(action, body) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      await apiPost(runPath(runId, action), token, body);
      await load(true);
    } catch (failure) {
      setError(failure);
      if (failure.status === 409 || failure.status === 404) await load(true);
    } finally { setBusy(false); }
  }

  return html`<section class="ns thread-view" aria-label="Hilo de la ejecución">
    <div class="thread-head"><h3>Hilo de la ejecución</h3><span class="muted">${runId}</span>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("thread.refresh")}
        onClick=${() => load(true)}>Actualizar</button><${Help} topic="thread.refresh" compact=${true} /></div>
    <${Help} topic="thread.section" />
    ${error && html`<p class="message error" role="alert">No se pudo actualizar el hilo o su revisión: ${errorText(error)}</p>`}
    ${!thread.loaded && !error && html`<p class="muted" aria-busy="true">Cargando el hilo…</p>`}
    <ol class="thread-list" role="log" aria-label="Mensajes de la ejecución">
      ${thread.entries.map((entry) => html`<${ThreadCard} key=${entryKey(entry)} entry=${entry} status=${thread.status}
        runId=${runId} token=${token} reviewView=${reviewView} busy=${busy} onAction=${act} />`)}
    </ol>
    <div class="thread-compose-slot" aria-label="Espacio para mensajes al run">
      <p class="muted">Mensajes al run disponibles próximamente.</p>
    </div>
  </section>`;
}
