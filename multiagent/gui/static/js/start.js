// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Inicio» (INI-T03, route #start, simple mode): only the steps that are still missing, each with its action, and «Tiempos». The
// server decides what is done (GET /gui/api/start); this view draws it, asks again every POLL_MS while it is open and never leaves
// a request pending behind another (the first answer of a process can take several seconds). Every text is plain text.
// The sidebar reads the same answer through useStartStatus, so both share one request at a time.
import { html, useCallback, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet, apiPost } from "./api.js";
import { Help, helpFor } from "./help.js";
import { CHECKING_TEXT, PACKAGES_PATH, POLL_MS, START_PATH, backToMockAction, copyTimes, startRows, timesText } from "./start-model.js";

// ---- one shared answer, one request at a time --------------------------------------------------------------------------------
let snapshot = { status: null, failed: false };
let inflight = null;
const listeners = new Set();

// Asks for the status unless a request is already running (then it returns that one).
export function refreshStart(token) {
  if (inflight) return inflight;
  inflight = apiGet(START_PATH, token)
    .then((status) => { snapshot = { status, failed: false }; })
    .catch(() => { snapshot = { status: snapshot.status, failed: true }; })
    .finally(() => { inflight = null; for (const listener of [...listeners]) listener(snapshot); });
  return inflight;
}

// The latest answer ({ status, failed }); `status` is null until the first one arrives.
export function useStartStatus(token) {
  const [value, setValue] = useState(snapshot);
  useEffect(() => {
    listeners.add(setValue);
    setValue(snapshot);
    return () => { listeners.delete(setValue); };
  }, []);
  useEffect(() => { refreshStart(token); }, [token]);
  return value;
}

const errorText = (error) => (error && error.message) || "No se pudo completar la acción.";

export function Start({ token }) {
  const { status, failed } = useStartStatus(token);
  const [example, setExample] = useState(false);   // example_available of GET /gui/api/packages
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);    // { kind: "ok" | "error", text }
  const [asking, setAsking] = useState(null);      // the action whose confirmation is open
  const [switched, setSwitched] = useState(null);  // "real" once the setting was changed and the person has not restarted yet
  const [copied, setCopied] = useState("");
  const packages = useRef(null);

  // The «install» step needs to know if the example is there; the pending ones are the only ones that ask.
  const loadExample = useCallback(() => {
    if (packages.current) return packages.current;
    packages.current = apiGet(PACKAGES_PATH, token)
      .then((answer) => setExample(Boolean(answer && answer.example_available)))
      .catch(() => setExample(false))
      .finally(() => { packages.current = null; });
    return packages.current;
  }, [token]);
  const refresh = useCallback(() => { refreshStart(token); loadExample(); }, [token, loadExample]);
  useEffect(() => {
    loadExample();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh, loadExample]);

  const run = async (action) => {
    setBusy(true);
    setMessage(null);
    try {
      const answer = await apiPost(action.path, token, action.body || {});
      if (action.path.endsWith("/settings/models")) setSwitched(answer && answer.models === "real" ? "real" : null);
      setMessage({ kind: "ok", text: (answer && answer.text) || "Listo." });
      setAsking(null);
      await refreshStart(token);
    } catch (error) {
      setMessage({ kind: "error", text: errorText(error) });
    } finally {
      setBusy(false);
    }
  };
  const press = (action) => (action.confirm ? setAsking(action) : run(action));
  const copy = async () => {
    try {
      setCopied((await copyTimes(status, (text) => navigator.clipboard.writeText(text))) ? "Copiado." : "");
    } catch (_) {
      setCopied("No se pudo copiar.");
    }
  };

  const rows = startRows(status ? { ...status, example_available: example } : null);
  const back = backToMockAction(status) || (switched === "real" ? backToMockAction({ models: "real" }) : null);
  const text = timesText(status);
  return html`<div class="start">
    <${Help} topic="start.steps" />
    ${!status && !failed && html`<p class="muted small" role="status" aria-busy="true">${CHECKING_TEXT}</p>`}
    ${!status && failed && html`<p class="message error small" role="alert">No se pudo leer el estado. Se reintentará sola.</p>`}
    ${status && failed && html`<p class="message error small" role="alert">No se pudo actualizar; se reintentará sola.</p>`}
    ${status && rows.length === 0 && html`<p class="muted" role="status">No falta ningún paso.</p>`}
    <ol class="start-steps">
      ${rows.map((row) => html`<li key=${row.id} class="start-step">
        <span class="start-step-title">${row.title}</span>
        ${row.note && html`<span class="muted small">${row.note}</span>`}
        <div class="start-actions">
          ${row.actions.map((action) => (action.kind === "link"
            ? html`<a key=${action.label} class="btn secondary" href=${action.href} ...${helpFor("start.steps")}>${action.label}</a>`
            : html`<button key=${action.label} type="button" class="btn secondary" disabled=${busy}
                ...${action.confirm ? helpFor("start.real_models", { scope: row.id }) : helpFor("start.steps")} onClick=${() => press(action)}>${action.label}</button>`))}
        </div>
        ${row.actions.some((action) => action.confirm) && html`<${Help} topic="start.real_models" scope=${row.id} compact=${true} />`}
        ${asking && row.actions.some((action) => action.label === asking.label) && html`<div class="start-confirm" role="alert">
          <p class="small">${asking.note}</p>
          <button type="button" class="btn" disabled=${busy} ...${helpFor("start.real_models", { scope: row.id })} onClick=${() => run(asking)}>Pasar a modelos reales</button>
          <button type="button" class="btn secondary" ...${helpFor("start.real_models", { scope: row.id })} onClick=${() => setAsking(null)}>Cancelar</button>
        </div>`}
      </li>`)}
    </ol>
    ${back && html`<div class="start-back">
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("start.real_models", { scope: "back" })} onClick=${() => run(back)}>${back.label}</button>
      <${Help} topic="start.real_models" scope="back" compact=${true} />
    </div>`}
    ${message && html`<p class=${"message small" + (message.kind === "error" ? " error" : "")} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
    <section class="start-times" aria-label="Tiempos">
      <h3 class="start-times-title">Tiempos</h3>
      <${Help} topic="start.times" />
      <pre class="start-times-text">${text || CHECKING_TEXT}</pre>
      <button type="button" class="btn secondary" disabled=${!text} ...${helpFor("start.times")} onClick=${copy}>Copiar</button>
      ${copied && html`<span class="muted small" role="status">${copied}</span>`}
    </section>
  </div>`;
}
