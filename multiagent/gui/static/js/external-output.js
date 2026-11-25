// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Salida externa» of one run: the JSON output and its evidence files, a preview with their hashes, a confirmation
// and the receipt. The page only sends and shows data; it never reads the content of an evidence file.
import { html, useState } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiPost, runPath } from "./api.js";
import { Help, helpFor } from "./help.js";
import { Confirm } from "./human-actions.js";
import { currentLanguage, t } from "./i18n.js";

const epoch = (seconds) => {
  const date = new Date(Number(seconds) * 1000);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString(currentLanguage());
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
  if (details.reason) parts.push(t("runs.external.failure.reason", { reason: details.reason }));
  for (const problem of details.problems || []) {
    parts.push(t("runs.external.failure.problem", {
      path: problem.path ? t("runs.external.failure.problem_path", { path: problem.path }) : "",
      message: problem.message,
    }));
  }
  return t("runs.external.failure", {
    code: error.code, message: error.message, details: parts.length ? " " + parts.join(" ") : "",
  });
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
      setMessage({ kind: "error", text: t("runs.external.invalid_json", { message: error.message }) });
      return;
    }
    if (output === null || typeof output !== "object" || Array.isArray(output)) {
      setMessage({ kind: "error", text: t("runs.external.object_required") });
      return;
    }
    setBusy(true);
    setMessage({ kind: "info", text: t("runs.external.preparing") });
    try {
      const evidence = [];
      for (const file of files) evidence.push({ name: file.name, content_base64: await toBase64(file) });
      const result = await apiPost(runPath(view.id, "external-output/preview"), token, { output, evidence });
      setPreview(result);
      setReceipt(null);
      setConfirming(false);
      setMessage({ kind: "ok", text: t("runs.external.preview_ready") });
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
    setMessage({ kind: "info", text: t("runs.external.confirming") });
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
      setMessage({ kind: "ok", text: t("runs.external.confirmed") });
    } catch (error) {
      const failure = error instanceof ApiError ? error : new ApiError({ message: String(error) });
      setConfirming(false);
      if (failure.status === 409 || failure.status === 404) {
        setPreview(null);
        setMessage({ kind: "error", text: t("runs.external.preview_stale", { message: failureText(failure) }) });
        onStale();
      } else {
        setMessage({ kind: "error", text: failureText(failure) });
      }
    } finally {
      setBusy(false);
    }
  }

  return html`<section class="stack external-output" aria-labelledby="external-output-title">
    <h4 id="external-output-title">${t("runs.external.title")}</h4>
    <${Help} topic="external.section" />
    <form class="stack" noValidate onSubmit=${makePreview}>
      <label for="external-json">${t("runs.external.output.label")}</label>
      <${Help} topic="external.output" />
      <textarea id="external-json" rows="8" value=${text} disabled=${busy} ...${helpFor("external.output")}
        onInput=${(event) => edit(() => setText(event.target.value))}></textarea>
      <label for="external-files">${t("runs.external.evidence.label")} <span class="muted">${t("runs.human.optional")}</span></label>
      <${Help} topic="external.evidence" />
      <input id="external-files" key=${pickerKey} type="file" multiple disabled=${busy} ...${helpFor("external.evidence")}
        onChange=${(event) => edit(() => setFiles(Array.from(event.target.files || [])))} />
      <div class="action-item"><button type="submit" class="btn secondary" disabled=${busy || !text.trim()}
        ...${helpFor("external.preview")}>${t("runs.external.preview.button")}</button>
        <${Help} topic="external.preview" /></div>
    </form>
    ${preview && html`<div class="stack" role="region" aria-label=${t("runs.external.preview.aria")}>
      <p>${t("runs.external.preview.summary", { step_id: preview.step_id, attempt: preview.attempt, expires: epoch(preview.expires_at) })}</p>
      <pre class="json">${JSON.stringify(preview.output, null, 2)}</pre>
      ${preview.files.length
        ? html`<ul>${preview.files.map((item) => html`<li key=${item.file}>${item.name} · ${item.kind} · ${item.size} bytes ·
            SHA-256 <code>${item.sha256}</code></li>`)}</ul>`
        : html`<p class="muted">${t("runs.external.evidence.empty")}</p>`}
      ${confirming
        ? html`<${Confirm} title=${t("runs.external.confirm.title")} confirmLabel=${t("runs.external.confirm.button")} keepLabel=${t("runs.external.confirm.back")}
            busy=${busy} onKeep=${() => setConfirming(false)} onConfirm=${confirmPreview}>
            <p class="muted">${t("runs.external.confirm.warning")}</p>
          <//>`
        : html`<div class="action-item"><button type="button" class="btn" disabled=${busy} ...${helpFor("external.confirm")}
            onClick=${() => setConfirming(true)}>${t("runs.external.confirm.prompt")}</button>
            <${Help} topic="external.confirm" /></div>`}
    </div>`}
    ${receipt && html`<div class="stack" role="region" aria-label=${t("runs.external.receipt.aria")}>
      <h5>${t("runs.external.receipt.title")}</h5>
      <dl class="tool-approval">
        <dt>${t("runs.external.receipt.id")}</dt><dd><code>${receipt.id}</code></dd>
        <dt>${t("runs.external.receipt.actor")}</dt><dd>${receipt.actor}</dd>
        <dt>${t("runs.external.receipt.channel")}</dt><dd>${receipt.channel}</dd>
        <dt>${t("runs.external.receipt.time")}</dt><dd>${epoch(receipt.confirmed_at)}</dd>
        <dt>${t("runs.external.receipt.manifest_hash")}</dt><dd><code>${receipt.manifest_sha256}</code></dd>
        <dt>${t("runs.external.receipt.file_hashes")}</dt><dd>${Object.keys(receipt.file_sha256 || {}).length
          ? html`<ul>${Object.entries(receipt.file_sha256).map(([file, hash]) => html`<li key=${file}>${receipt.names[file] || file}: <code>${hash}</code></li>`)}</ul>`
          : t("runs.external.receipt.no_files")}</dd>
      </dl>
    </div>`}
    ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
  </section>`;
}
