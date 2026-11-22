// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Paquetes» (PAQ-T02, route #packages, simple mode): the packages that are installed, and how to install one without a console.
// A package comes from a .zip the person picks in the file selector (it travels as base64 in JSON), or, in simple mode too, from
// the example package of the product. A folder path is typed only in advanced mode. A package whose copy changed or is missing is
// shown degraded, with «Reinstalar desde un archivo» and «Quitar». Everything is plain text; the origin is a label, never a path.
import { html, useCallback, useEffect, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet, apiPost } from "./api.js";
import { Help, helpFor } from "./help.js";
import { currentMode, onModeChange } from "./gui-mode.js";
import { REMOVE_NOTICE, ZIP_UPLOAD_MAX_BYTES, bytesToBase64, folderBody, packageErrorText, packageNameFromFile, packageRows,
         zipBody } from "./packages-model.js";

const BASE = "/gui/api/packages";

// The .zip as base64 and its name, or an error text when it is too big. Never reads a file over the cap.
async function readZip(file) {
  if (file.size > ZIP_UPLOAD_MAX_BYTES) return { error: packageErrorText({ status: 413 }) };
  return { base64: bytesToBase64(new Uint8Array(await file.arrayBuffer())), filename: file.name };
}

export function Packages({ token }) {
  const [payload, setPayload] = useState(null);
  const [mode, setMode] = useState(currentMode);
  const [zip, setZip] = useState(null);            // { base64, filename } of the chosen file
  const [name, setName] = useState("");
  const [folder, setFolder] = useState("");
  const [trust, setTrust] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);    // { kind: "ok" | "error", text }
  const [removing, setRemoving] = useState(null);  // the name whose «Quitar» waits for a second click
  useEffect(() => onModeChange(setMode), []);

  const load = useCallback(() => apiGet(BASE, token).then(setPayload).catch(() => setPayload({ packages: [], example_available: false })), [token]);
  useEffect(() => { load(); }, [load]);

  // One action at a time: it runs, the list is read again, and the answer is one line.
  const act = async (run, done) => {
    setBusy(true);
    setMessage(null);
    try {
      await run();
      setMessage({ kind: "ok", text: done });
      await load();
      return true;
    } catch (error) {
      setMessage({ kind: "error", text: packageErrorText(error) });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const chooseFile = async (event) => {
    const file = event.target.files && event.target.files[0];
    setZip(null);
    if (!file) return;
    const read = await readZip(file);
    if (read.error) { setMessage({ kind: "error", text: read.error }); return; }
    setMessage(null);
    setZip(read);
    setName(packageNameFromFile(file.name));
  };
  const installZip = async () => {
    if (await act(() => apiPost(BASE + "/zip", token, zipBody(name, zip.filename, zip.base64, trust)), "Paquete instalado.")) {
      setZip(null);
      setName("");
      setTrust(false);
    }
  };
  const installFolder = async () => {
    if (await act(() => apiPost(BASE + "/folder", token, folderBody(name, folder, trust)), "Paquete instalado.")) {
      setFolder("");
      setName("");
      setTrust(false);
    }
  };
  const installExample = () => act(() => apiPost(BASE + "/example", token, {}), "Paquete de ejemplo instalado.");
  const remove = async (target) => {
    setRemoving(null);
    await act(() => apiPost(BASE + "/" + encodeURIComponent(target) + "/remove", token, {}), "Se quitó de la lista. Sus datos se conservan.");
  };
  const reinstall = async (target, event) => {
    const file = event.target.files && event.target.files[0];
    event.target.value = "";   // the same file can be chosen again
    if (!file) return;
    const read = await readZip(file);
    if (read.error) { setMessage({ kind: "error", text: read.error }); return; }
    await act(() => apiPost(BASE + "/" + encodeURIComponent(target) + "/reinstall-zip", token, { filename: read.filename, zip_base64: read.base64 }),
      "Paquete reinstalado.");
  };

  const rows = packageRows(payload, mode);
  const advanced = mode === "advanced";
  return html`<div class="packages">
    <section class="packages-block" aria-label="Instalar un paquete">
      <h3 class="packages-title">Instalar un paquete</h3>
      <${Help} topic="packages.install_zip" />
      <div class="packages-form">
        <label class="packages-field">Archivo .zip
          <input type="file" accept=".zip,application/zip" disabled=${busy} ...${helpFor("packages.install_zip")} onChange=${chooseFile} />
        </label>
        ${zip && html`<label class="packages-field">Nombre del paquete
          <input type="text" value=${name} maxlength="120" disabled=${busy} ...${helpFor("packages.install_zip")}
            onInput=${(event) => setName(event.target.value)} />
        </label>`}
        ${zip && advanced && html`<label class="check-inline">
          <input type="checkbox" checked=${trust} disabled=${busy} ...${helpFor("packages.trust_functions")}
            onChange=${(event) => setTrust(event.target.checked)} />
          Confiar en las funciones de este paquete</label>`}
        ${zip && html`<button type="button" class="btn" disabled=${busy || !name} ...${helpFor("packages.install_zip")}
          onClick=${installZip}>Instalar</button>`}
      </div>
      ${payload && payload.example_available && html`<div class="packages-example">
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("packages.example")} onClick=${installExample}>Instalar el paquete de ejemplo</button>
        <${Help} topic="packages.example" compact=${true} />
      </div>`}
      ${advanced && html`<div class="packages-form packages-folder">
        <label class="packages-field">Instalar desde una carpeta
          <input type="text" value=${folder} placeholder="Carpeta del paquete" disabled=${busy}
            ...${helpFor("packages.install_zip", { scope: "folder" })} onInput=${(event) => setFolder(event.target.value)} />
        </label>
        <label class="packages-field">Nombre del paquete
          <input type="text" value=${name} maxlength="120" disabled=${busy} ...${helpFor("packages.install_zip", { scope: "folder" })}
            onInput=${(event) => setName(event.target.value)} />
        </label>
        <${Help} topic="packages.install_zip" scope="folder" compact=${true} />
        <label class="check-inline">
          <input type="checkbox" checked=${trust} disabled=${busy} ...${helpFor("packages.trust_functions", { scope: "folder" })}
            onChange=${(event) => setTrust(event.target.checked)} />
          Confiar en las funciones de este paquete</label>
        <${Help} topic="packages.trust_functions" scope="folder" compact=${true} />
        <button type="button" class="btn secondary" disabled=${busy || !folder.trim() || !name} ...${helpFor("packages.install_zip", { scope: "folder" })}
          onClick=${installFolder}>Instalar desde una carpeta</button>
      </div>`}
      ${zip && advanced && html`<${Help} topic="packages.trust_functions" compact=${true} />`}
      ${message && html`<p class=${"message small" + (message.kind === "error" ? " error" : "")} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
    </section>
    <section class="packages-block" aria-label="Paquetes instalados">
      <h3 class="packages-title">Paquetes instalados</h3>
      ${payload && rows.length === 0 && html`<p class="muted small">Todavía no hay paquetes instalados.</p>`}
      <ul class="packages-list">
        ${rows.map((row) => html`<li key=${row.name} class=${"package-row" + (row.degraded ? " degraded" : "")}>
          <div class="package-main">
            <span class="package-name">${row.name}</span>
            <span class="package-meta muted small">${[row.levelText, row.versionText, row.origin].filter(Boolean).join(" · ")}</span>
            <span class=${"package-state" + (row.degraded ? " degraded" : "")}>${row.stateText}</span>
            ${row.functionsText && html`<span class="package-meta muted small">${row.functionsText}</span>`}
          </div>
          ${row.degraded && html`<${Help} topic="packages.degraded" scope=${row.name} compact=${true} />`}
          <div class="package-actions">
            ${row.canReinstall && html`<label class="btn secondary package-reinstall">${row.reinstallLabel}
              <input type="file" accept=".zip,application/zip" class="package-file" disabled=${busy}
                ...${helpFor("packages.degraded", { scope: row.name })} onChange=${(event) => reinstall(row.name, event)} />
            </label>`}
            <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("packages.remove", { scope: row.name })}
              onClick=${() => setRemoving(row.name)}>${row.removeLabel}</button>
            <${Help} topic="packages.remove" scope=${row.name} compact=${true} />
          </div>
          ${removing === row.name && html`<div class="package-confirm" role="alert">
            <p class="small">${REMOVE_NOTICE}</p>
            <button type="button" class="btn" disabled=${busy} ...${helpFor("packages.remove", { scope: row.name })} onClick=${() => remove(row.name)}>${row.removeLabel}</button>
            <button type="button" class="btn secondary" ...${helpFor("packages.remove", { scope: row.name })} onClick=${() => setRemoving(null)}>Cancelar</button>
          </div>`}
        </li>`)}
      </ul>
    </section>
  </div>`;
}
