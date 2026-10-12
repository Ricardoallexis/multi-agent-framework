// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Create a run: workflow inputs as named fields (required ones marked and enforced), and a text
// preview of the prompt rendered by the server. The server rejects empty required inputs too.
// A declared input shows its label, a short description under it, the example as placeholder text,
// and a select for choices or a text area for long text; an undeclared one is derived from the
// prompt and shows the prompt line where it appears. Everything is plain text.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "./api.js";
import { Help, helpFor } from "./help.js";
import { fieldHelpEntry, modeHelpEntry } from "./help-text.js";
import { MODE_LABELS, errorText, exampleText, inputHelp, inputsNote, skipReason } from "./text.js";

const PREVIEW_DEBOUNCE_MS = 400;
const MODES = ["auto", "local", "cloud", "human_guided"];

const newKey = () => (globalThis.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());
const asError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));

function FieldControl({ item, id, value, invalid, disabled, onChange, onBlur }) {
  const common = {
    id, disabled, "aria-required": item.required ? "true" : null, "aria-invalid": invalid ? "true" : null,
    onBlur: () => onBlur(item.name),
  };
  const change = (event) => onChange(item.name, event.target.value);
  if (item.type === "choice") {
    return html`<select ...${common} ...${helpFor("field." + item.name, { also: id + "-help" })} value=${value} onChange=${change}>
      <option value="">${item.required ? "Elige una opción" : "Sin elegir"}</option>
      ${(item.choices || []).map((choice) => html`<option key=${choice} value=${choice}>${choice}</option>`)}
    </select>`;
  }
  const placeholder = exampleText(item);
  // A declared "text" input is one line; long text and every derived input (its kind is unknown) use a text area.
  if (item.source === "declared" && item.type !== "long_text") {
    return html`<input type="text" ...${common} ...${helpFor("field." + item.name, { also: id + "-help" })} value=${value} placeholder=${placeholder} onInput=${change} />`;
  }
  const rows = item.type === "long_text" ? 5 : item.context ? 3 : 2;
  return html`<textarea ...${common} ...${helpFor("field." + item.name, { also: id + "-help" })} rows=${rows} value=${value} placeholder=${placeholder} onInput=${change}></textarea>`;
}

function Field({ item, value, touched, disabled, onChange, onBlur }) {
  const id = "input-" + item.name;
  const empty = !value.trim();
  const invalid = item.required && touched && empty;
  return html`<div class="field">
    <label for=${id}>${item.label} <code>${item.name}</code>
      ${item.required
        ? html`<span class="req"><span aria-hidden="true">*</span> obligatorio</span>`
        : html`<span class="muted">(opcional)</span>`}</label>
    <${Help} topic=${"field." + item.name} entry=${fieldHelpEntry(item)} hint=${item.description || null} />
    <${FieldControl} item=${item} id=${id} value=${value} invalid=${invalid} disabled=${disabled}
      onChange=${onChange} onBlur=${onBlur} />
    <p id=${id + "-help"} class="help">${inputHelp(item)}</p>
    ${invalid && html`<p class="field-error" role="alert">Este campo es obligatorio: escribe un valor.</p>`}
  </div>`;
}

export function PreviewPanel({ preview, error, loading }) {
  const [copied, setCopied] = useState("");
  async function copy(step) {
    try { await navigator.clipboard.writeText(step.text); setCopied("Prompt del paso «" + step.id + "» copiado."); }
    catch (_) { setCopied("El navegador bloqueó copiar; selecciona el texto y pulsa Ctrl+C."); }
  }
  const firstRunnable = preview ? preview.steps.findIndex((step) => !step.skipped) : -1;
  return html`<section class="panel" aria-labelledby="preview-title">
    <h4 id="preview-title">Vista previa del prompt</h4>
    <${Help} topic="preview.panel" />
    ${error && html`<p class="message error" role="alert">No se pudo generar la vista previa: ${errorText(error)}</p>`}
    ${!preview && !error && html`<p class="muted">${loading ? "Generando la vista previa…" : "Elige un workflow para ver su prompt."}</p>`}
    ${preview && html`<div aria-busy=${loading ? "true" : "false"}>
      ${preview.missing_inputs.length > 0 && html`<p class="notice">Todavía falta: ${preview.missing_inputs.join(", ")}. Esos huecos salen vacíos en el prompt.</p>`}
      ${preview.empty_optional_inputs.length > 0 && html`<p class="muted">Quedan vacíos en el prompt (opcionales): ${preview.empty_optional_inputs.join(", ")}.</p>`}
      ${preview.steps.map((step, index) => step.skipped
        ? html`<p key=${step.id} class="skipped-step">Paso «${step.id}»: se saltará ${skipReason(step.skip)}.</p>`
        : html`<details key=${step.id} open=${index === firstRunnable}>
            <summary>Paso «${step.id}» · ${step.prompt_id} v${step.prompt_version} · contrato ${step.contract}</summary>
            ${step.needs_previous_outputs && html`<p class="muted">Este paso usa resultados de pasos anteriores: aparecen vacíos hasta que esos pasos se ejecuten.</p>`}
            <pre class="prompt">${step.text}</pre>
            <div class="actions"><button type="button" class="btn secondary small" ...${helpFor("preview.copy", { scope: step.id })}
              onClick=${() => copy(step)}>Copiar este prompt</button>
              <${Help} topic="preview.copy" scope=${step.id} compact=${true} /></div>
          </details>`)}
      <p class="muted" role="status">${copied}</p>
    </div>`}
  </section>`;
}

// initial (optional): { bundle, workflowId, project, values, mode, sensitive } to open the form with those answers already
// in place; the guided chat (new-session.js) uses it for «Ver como formulario». Without it the form opens empty.
export function CreateRun({ token, mock, onCreated, onClose, initial = null }) {
  const [bundles, setBundles] = useState([]);
  const [bundle, setBundle] = useState(initial ? initial.bundle || "" : "");
  const [workflows, setWorkflows] = useState([]);
  const [workflowId, setWorkflowId] = useState(initial ? initial.workflowId || "" : "");
  const [spec, setSpec] = useState(null);
  const [values, setValues] = useState({});
  const [touched, setTouched] = useState({});
  const [project, setProject] = useState(initial ? initial.project || "" : "");
  const [mode, setMode] = useState(initial && initial.mode ? initial.mode : "auto");
  const [sensitive, setSensitive] = useState(Boolean(initial && initial.sensitive));
  const [confirmReal, setConfirmReal] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const ticket = useRef(0);
  const attempt = useRef({ fingerprint: "", key: "" });
  // The first load keeps the workflow and the values of `initial`; after that, changing the origin or the workflow clears them.
  const seed = useRef(initial);

  useEffect(() => {
    apiGet("/gui/api/bundles", token).then((list) => setBundles(list)).catch((error) => setLoadError(asError(error)));
  }, [token]);

  useEffect(() => {
    let current = true;
    if (!seed.current) setWorkflowId("");
    setSpec(null); setWorkflows([]); setLoadError(null);
    apiGet("/gui/api/workflows" + (bundle ? "?bundle=" + encodeURIComponent(bundle) : ""), token)
      .then((list) => { if (current) setWorkflows(list); })
      .catch((error) => { if (current) setLoadError(asError(error)); });
    return () => { current = false; };
  }, [bundle, token]);

  useEffect(() => {
    let current = true;
    const first = seed.current;
    seed.current = null;
    setSpec(null); setValues(first && first.values ? { ...first.values } : {}); setTouched({}); setPreview(null); setPreviewError(null);
    if (!workflowId) return undefined;
    apiGet("/gui/api/workflows/" + encodeURIComponent(workflowId) + "/inputs" + (bundle ? "?bundle=" + encodeURIComponent(bundle) : ""), token)
      .then((result) => { if (current) setSpec(result); })
      .catch((error) => { if (current) setLoadError(asError(error)); });
    return () => { current = false; };
  }, [workflowId]);

  const inputs = () => Object.fromEntries((spec ? spec.inputs : []).map((item) => [item.name, values[item.name] || ""]));

  useEffect(() => {
    if (!spec) return undefined;
    const mine = ++ticket.current;
    setPreviewing(true);
    const timer = setTimeout(async () => {
      try {
        const result = await apiPost("/gui/api/runs/preview", token, {
          workflow_id: workflowId, bundle: bundle || null, inputs: inputs(), execution_mode: mode, sensitive,
        });
        if (mine !== ticket.current) return;
        setPreview(result); setPreviewError(null); setPreviewing(false);
      } catch (error) {
        if (mine !== ticket.current) return;
        setPreviewError(asError(error)); setPreviewing(false);
      }
    }, PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [spec, values, mode, sensitive, token]);

  const missing = spec ? spec.inputs.filter((item) => item.required && !(values[item.name] || "").trim()) : [];
  const blockers = [];
  if (!workflowId) blockers.push("Elige un workflow.");
  else if (!spec) blockers.push("Espera a que cargue el workflow.");
  if (project.trim().length < 2) blockers.push("Escribe el nombre del proyecto (mínimo 2 letras).");
  if (missing.length) blockers.push("Falta completar: " + missing.map((item) => item.label + " (" + item.name + ")").join(", ") + ".");
  if (!mock && !confirmReal) blockers.push("Confirma que entiendes que el servidor no está en modo Mock.");

  function change(name, value) {
    setValues((previous) => ({ ...previous, [name]: value }));
    setSubmitError(null);
  }

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    if (blockers.length) {
      setTouched(Object.fromEntries((spec ? spec.inputs : []).map((item) => [item.name, true])));
      return;
    }
    const body = { workflow_id: workflowId, bundle: bundle || null, project_name: project.trim(), inputs: inputs(), execution_mode: mode, sensitive };
    const fingerprint = JSON.stringify(body);
    if (attempt.current.fingerprint !== fingerprint) attempt.current = { fingerprint, key: newKey() };
    setBusy(true);
    setSubmitError(null);
    try {
      const run = await apiPost("/gui/api/runs", token, { ...body, idempotency_key: attempt.current.key });
      attempt.current = { fingerprint: "", key: "" };
      onCreated(run);
    } catch (failure) {
      const error = asError(failure);
      setSubmitError(error);
      const names = error.details && error.details.missing_inputs;
      if (names) setTouched((previous) => ({ ...previous, ...Object.fromEntries(names.map((name) => [name, true])) }));
    } finally {
      setBusy(false);
    }
  }

  const group = (title, items, help) => items.length === 0 ? null : html`<fieldset class="inputs">
    <legend>${title}</legend>
    ${help}
    ${items.map((item) => html`<${Field} key=${item.name} item=${item} value=${values[item.name] || ""}
      touched=${Boolean(touched[item.name])} disabled=${busy} onChange=${change}
      onBlur=${(name) => setTouched((previous) => ({ ...previous, [name]: true }))} />`)}
  </fieldset>`;
  const all = spec ? spec.inputs : [];
  const required = all.filter((item) => item.required);
  const optional = all.filter((item) => !item.required && !item.context);
  const context = all.filter((item) => item.context && !item.required);

  return html`<div class="create-run">
    <header class="row">
      <h3>Nueva ejecución</h3>
      <div class="header-tools"><button type="button" class="btn secondary small" ...${helpFor("create.back")} onClick=${onClose}>Volver</button>
        <${Help} topic="create.back" compact=${true} /></div>
    </header>
    ${loadError && html`<p class="message error" role="alert">${errorText(loadError)}</p>`}
    <form class="stack" noValidate onSubmit=${submit} aria-busy=${busy ? "true" : "false"}>
      <div class="two">
        <div><label for="cr-bundle">Origen del workflow</label>
          <${Help} topic="create.bundle" />
          <select id="cr-bundle" ...${helpFor("create.bundle")} value=${bundle} disabled=${busy} onChange=${(event) => setBundle(event.target.value)}>
            <option value="">Workflows incluidos</option>
            ${bundles.map((item) => html`<option key=${item.name} value=${item.name}>Bundle: ${item.name}</option>`)}
          </select></div>
        <div><label for="cr-workflow">Workflow <span class="req"><span aria-hidden="true">*</span> obligatorio</span></label>
          <${Help} topic="create.workflow" />
          <select id="cr-workflow" ...${helpFor("create.workflow")} value=${workflowId} disabled=${busy} aria-required="true"
            onChange=${(event) => setWorkflowId(event.target.value)}>
            <option value="">Elige un workflow</option>
            ${workflows.map((item) => html`<option key=${item.id} value=${item.id}>${item.id}</option>`)}
          </select></div>
      </div>
      ${spec && spec.description && html`<p class="workflow-description">${spec.description}</p>`}
      ${spec && html`<div class="muted">
        <p class="help">Pasos:</p>
        <${Help} topic="create.steps" />
        <ol class="step-list">${spec.steps.map((step) => html`<li key=${step.id}><strong>${step.id}</strong>${step.skipped_by_default ? " (se salta por defecto)" : ""}${step.description ? ": " + step.description : ""}</li>`)}</ol>
        <p class="help">${inputsNote(spec)}</p></div>`}
      <div><label for="cr-project">Proyecto <span class="req"><span aria-hidden="true">*</span> obligatorio</span></label>
        <${Help} topic="create.project" />
        <input id="cr-project" ...${helpFor("create.project")} maxlength="120" autocomplete="off" value=${project} disabled=${busy}
          aria-required="true" onInput=${(event) => setProject(event.target.value)} /></div>
      ${group("Entradas obligatorias", required, html`<${Help} topic="create.inputs.required" />`)}
      ${group("Entradas opcionales", optional, html`<${Help} topic="create.inputs.optional" />`)}
      ${context.length > 0 && html`<details><summary>Contexto adicional opcional (${context.length})</summary>${group("Contexto", context, html`<${Help} topic="create.inputs.context" />`)}</details>`}
      <div class="two">
        <div><label for="cr-mode">Cómo se ejecuta</label>
          <${Help} topic="create.mode" entry=${modeHelpEntry(mode)} />
          <select id="cr-mode" ...${helpFor("create.mode")} value=${mode} disabled=${busy} onChange=${(event) => setMode(event.target.value)}>
            ${MODES.map((value) => html`<option key=${value} value=${value}>${MODE_LABELS[value]}</option>`)}
          </select></div>
        <div class="check"><label><input type="checkbox" checked=${sensitive} disabled=${busy} ...${helpFor("create.sensitive")}
          onChange=${(event) => setSensitive(event.target.checked)} /> Contenido sensible (no se enviará a la nube)</label>
          <${Help} topic="create.sensitive" /></div>
      </div>
      ${!mock && html`<div class="notice check"><label><input type="checkbox" checked=${confirmReal} disabled=${busy} ...${helpFor("create.confirm_real")}
        onChange=${(event) => setConfirmReal(event.target.checked)} /> Entiendo que el servidor no está en modo Mock y esta ejecución puede usar modelos reales.</label>
        <${Help} topic="create.confirm_real" /></div>`}
      ${submitError && html`<p class="message error" role="alert">${errorText(submitError)}</p>`}
      <p id="cr-blockers" class="hint" role="status">${blockers.length ? "No se puede crear todavía. " + blockers.join(" ") : "Todo listo para crear la ejecución."}</p>
      <div class="actions">
        <button type="submit" class="btn" disabled=${busy || blockers.length > 0} ...${helpFor("create.submit", { also: "cr-blockers" })}>${busy ? "Creando…" : "Crear ejecución"}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("create.cancel")} onClick=${onClose}>Cancelar</button>
        <${Help} topic="create.cancel" compact=${true} />
      </div>
      <${Help} topic="create.submit" />
    </form>
    <${PreviewPanel} preview=${preview} error=${previewError} loading=${previewing} />
  </div>`;
}
