// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Salida externa» of one run: the JSON output and its evidence files, a preview with their hashes, a confirmation
// and the receipt. The page only sends and shows data; it never reads the content of an evidence file.
import { html, useState } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiPost, runPath } from "./api.js";
import { Help, helpFor } from "./help.js";
import { Confirm } from "./human-actions.js";

const epoch = (seconds) => {
  const date = new Date(Number(seconds) * 1000);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("es");
};

async function toBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

function failureText(error) {
  const details = error.details || {};
  const parts = [];
  if (details.reason) parts.push("Motivo: " + details.reason + ".");
  for (const problem of details.problems || []) parts.push((problem.path ? problem.path + ": " : "") + problem.message);
  return "Error " + error.code + ": " + error.message + (parts.length ? " " + parts.join(" ") : "");
}

export function ExternalOutput({ token, view, onUpdate, onStale }) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState([]);
  const [pickerKey, setPickerKey] = useState(0);
  const [preview, setPreview] = useState(null);
  const [receipt, setReceipt] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  const edit = (patch) => { setPreview(null); setConfirming(false); patch(); };

  async function makePreview(event) {
    event.preventDefault();
    if (busy) return;
    let output;
    try { output = JSON.parse(text); } catch (error) {
      setMessage({ kind: "error", text: "La salida no es JSON válido: " + error.message });
      return;
    }
    if (output === null || typeof output !== "object" || Array.isArray(output)) {
      setMessage({ kind: "error", text: "La salida tiene que ser un objeto JSON." });
      return;
    }
    setBusy(true);
    setMessage({ kind: "info", text: "Preparando la vista previa…" });
    try {
      const evidence = [];
      for (const file of files) evidence.push({ name: file.name, content_base64: await toBase64(file) });
      const result = await apiPost(runPath(view.id, "external-output/preview"), token, { output, evidence });
      setPreview(result);
      setReceipt(null);
      setConfirming(false);
      setMessage({ kind: "ok", text: "Vista previa lista. Nada cambió todavía: revisa y confirma." });
    } catch (error) {
      const failure = error instanceof ApiError ? error : new ApiError({ message: String(error) });
      setPreview(null);
      setMessage({ kind: "error", text: failureText(failure) });
      if (failure.status === 409 || failure.status === 404) onStale();
    } finally {
      setBusy(false);
    }
  }

  async function confirmPreview() {
    if (busy || !preview) return;
    setBusy(true);
    setMessage({ kind: "info", text: "Confirmando…" });
    try {
      const updated = await apiPost(runPath(view.id, "external-output/confirm"), token, { preview_id: preview.id });
      const names = {};
      for (const item of preview.files) names[item.file] = item.name;
      setReceipt(updated.receipt ? { ...updated.receipt, names } : null);
      setPreview(null);
      setConfirming(false);
      setText("");
      setFiles([]);
      setPickerKey(pickerKey + 1);
      onUpdate(updated);
      setMessage({ kind: "ok", text: "Salida confirmada. Guarda el recibo si lo necesitas." });
    } catch (error) {
      const failure = error instanceof ApiError ? error : new ApiError({ message: String(error) });
      setConfirming(false);
      if (failure.status === 409 || failure.status === 404) {
        setPreview(null);
        setMessage({ kind: "error", text: failureText(failure) + " La vista previa caducó, ya se usó o la ejecución cambió: prepara otra." });
        onStale();
      } else {
        setMessage({ kind: "error", text: failureText(failure) });
      }
    } finally {
      setBusy(false);
    }
  }

  return html`<section class="stack external-output" aria-labelledby="external-output-title">
    <h4 id="external-output-title">Salida externa</h4>
    <${Help} topic="external.section" />
    <form class="stack" noValidate onSubmit=${makePreview}>
      <label for="external-json">Salida en JSON</label>
      <${Help} topic="external.output" />
      <textarea id="external-json" rows="8" value=${text} disabled=${busy} ...${helpFor("external.output")}
        onInput=${(event) => edit(() => setText(event.target.value))}></textarea>
      <label for="external-files">Evidencias <span class="muted">(opcional)</span></label>
      <${Help} topic="external.evidence" />
      <input id="external-files" key=${pickerKey} type="file" multiple disabled=${busy} ...${helpFor("external.evidence")}
        onChange=${(event) => edit(() => setFiles(Array.from(event.target.files || [])))} />
      <div class="action-item"><button type="submit" class="btn secondary" disabled=${busy || !text.trim()}
        ...${helpFor("external.preview")}>Vista previa</button>
        <${Help} topic="external.preview" /></div>
    </form>
    ${preview && html`<div class="stack" role="region" aria-label="Vista previa de la salida externa">
      <p>Vista previa del paso «${preview.step_id}» (intento ${preview.attempt}). Caduca: ${epoch(preview.expires_at)}.</p>
      <pre class="json">${JSON.stringify(preview.output, null, 2)}</pre>
      ${preview.files.length
        ? html`<ul>${preview.files.map((item) => html`<li key=${item.file}>${item.name} · ${item.kind} · ${item.size} bytes ·
            SHA-256 <code>${item.sha256}</code></li>`)}</ul>`
        : html`<p class="muted">Sin evidencias.</p>`}
      ${confirming
        ? html`<${Confirm} title="¿Confirmar la salida externa?" confirmLabel="Sí, confirmar" keepLabel="Volver"
            busy=${busy} onKeep=${() => setConfirming(false)} onConfirm=${confirmPreview}>
            <p class="muted">Se guardará la salida con sus evidencias, un manifiesto y un recibo; no se puede deshacer.</p>
          <//>`
        : html`<div class="action-item"><button type="button" class="btn" disabled=${busy} ...${helpFor("external.confirm")}
            onClick=${() => setConfirming(true)}>Confirmar…</button>
            <${Help} topic="external.confirm" /></div>`}
    </div>`}
    ${receipt && html`<div class="stack" role="region" aria-label="Recibo de la salida externa">
      <h5>Recibo</h5>
      <dl class="tool-approval">
        <dt>Id</dt><dd><code>${receipt.id}</code></dd>
        <dt>Quién</dt><dd>${receipt.actor}</dd>
        <dt>Canal</dt><dd>${receipt.channel}</dd>
        <dt>Hora</dt><dd>${epoch(receipt.confirmed_at)}</dd>
        <dt>SHA-256 del manifiesto</dt><dd><code>${receipt.manifest_sha256}</code></dd>
        <dt>SHA-256 de cada archivo</dt><dd>${Object.keys(receipt.file_sha256 || {}).length
          ? html`<ul>${Object.entries(receipt.file_sha256).map(([file, hash]) => html`<li key=${file}>${receipt.names[file] || file}: <code>${hash}</code></li>`)}</ul>`
          : "sin archivos"}</dd>
      </dl>
    </div>`}
    ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
  </section>`;
}
