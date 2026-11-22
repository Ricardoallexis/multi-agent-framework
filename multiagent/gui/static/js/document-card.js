// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// ARR3-T06: the "Documento" card of a completed run. The Markdown is shown as plain text in a <pre> (it is never drawn),
// and "Guardar como Markdown" builds the file in the browser, so the server writes nothing.
import { html, useEffect, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet, runPath } from "./api.js";
import { Help, helpFor } from "./help.js";
import { errorText } from "./text.js";
import { SAVE_LABEL, documentState, downloadMarkdown } from "./document-model.js";

// No hooks: the card body for a payload the server gave.
export function DocumentView({ payload, onSave }) {
  return html`<section class="document-card" aria-label="Documento del resultado">
    <h4>Documento</h4><${Help} topic="thread.document" compact=${true} />
    <pre class="document-text">${payload.markdown}</pre>
    <div class="document-actions">
      <button type="button" class="btn secondary" ...${helpFor("thread.document_save")} onClick=${onSave}>${SAVE_LABEL}</button>
      <${Help} topic="thread.document_save" compact=${true} />
    </div>
  </section>`;
}

export function DocumentCard({ token, runId }) {
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let active = true;
    setPayload(null); setError(null);
    apiGet(runPath(runId, "document"), token).then(
      (value) => { if (active) setPayload(value); },
      (failure) => { if (active) setError(failure); });
    return () => { active = false; };
  }, [runId, token]);
  const state = documentState(payload, error);
  if (state === "none") return null;
  if (state === "error") return html`<p class="message error" role="alert">No se pudo leer el documento: ${errorText(error)}</p>`;
  return html`<${DocumentView} payload=${payload} onSave=${() => downloadMarkdown(payload, document, URL)} />`;
}
