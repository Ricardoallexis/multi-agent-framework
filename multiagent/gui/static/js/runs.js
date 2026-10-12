// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// "Ejecuciones" section: list on the left; create form or run detail on the right.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet } from "./api.js";
import { debounce, subscribe } from "./bus.js";
import { CreateRun } from "./create-run.js";
import { Help, helpFor } from "./help.js";
import { navigate } from "./route.js";
import { RunDetail } from "./run-detail.js";
import { errorText, fmtDate } from "./text.js";
import { StatusChip } from "./ui/status-chip.js";

const LIST_DEBOUNCE_MS = 500;

function RunList({ token, status, selected, version, onSelect }) {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);
  const latest = useRef(0);

  async function load() {
    const ticket = ++latest.current;
    try {
      const result = await apiGet("/gui/api/runs?limit=50", token);
      if (ticket === latest.current) { setRuns(result); setError(null); }
    } catch (failure) {
      if (ticket === latest.current) setError(failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
    }
  }

  useEffect(() => { load(); }, [token, version]);
  useEffect(() => { if (status === "connected") load(); }, [status]);
  useEffect(() => {
    const refresh = debounce(load, LIST_DEBOUNCE_MS);
    const off = subscribe(() => refresh());
    return () => { off(); refresh.cancel(); };
  }, [token]);

  if (error && !runs) return html`<p class="message error" role="alert">${errorText(error)}</p>`;
  if (!runs) return html`<p class="muted" aria-busy="true">Cargando ejecuciones…</p>`;
  if (runs.length === 0) return html`<p class="muted">Todavía no hay ejecuciones. Crea la primera con «Nueva ejecución».</p>`;
  return html`<ul class="run-list">
    ${runs.map((run) => html`<li key=${run.id}>
      <button type="button" data-help-exempt="data-row" aria-current=${run.id === selected ? "true" : null} onClick=${() => onSelect(run.id)}>
        <span class="run-title">${run.workflow_id} <span class="muted">· ${run.project_name}</span></span>
        <${StatusChip} status=${run.status} labels="run" />
        <span class="muted small">${fmtDate(run.created_at)}${run.bundle ? " · " + run.bundle : ""}</span>
      </button>
    </li>`)}
  </ul>`;
}

// startCreating: the address is "#new" (the "Nueva sesión" button of the shell), so the create form is open; closing it
// or creating the run moves the address on, which keeps the Back button and the shell's highlight honest.
export function Runs({ token, status, mock, initialRunId = null, startCreating = false }) {
  const [selected, setSelected] = useState(initialRunId);
  const [creating, setCreating] = useState(startCreating);
  const [version, setVersion] = useState(0);
  const wasStarting = useRef(startCreating);
  const bump = () => setVersion((value) => value + 1);

  // A link from the sessions screen ("#runs/<id>") opens that run.
  useEffect(() => { if (initialRunId) { setSelected(initialRunId); setCreating(false); } }, [initialRunId]);
  useEffect(() => {
    if (startCreating) setCreating(true);
    else if (wasStarting.current) setCreating(false);
    wasStarting.current = startCreating;
  }, [startCreating]);

  let main;
  if (creating) {
    main = html`<${CreateRun} token=${token} mock=${mock}
      onClose=${() => { setCreating(false); if (startCreating) navigate("runs"); }}
      onCreated=${(run) => { setCreating(false); setSelected(run.id); bump(); if (startCreating) navigate("runs", run.id); }} />`;
  } else if (selected) {
    main = html`<${RunDetail} token=${token} runId=${selected} status=${status} onChanged=${bump} />`;
  } else {
    main = html`<p class="muted">Elige una ejecución de la lista o crea una nueva.</p>`;
  }
  return html`<div class="runs-page">
    <${Help} topic="runs.section" />
    <div class="runs">
      <aside class="runs-side" aria-label="Lista de ejecuciones">
        <div>
          <button type="button" class="btn" ...${helpFor("runs.new")} onClick=${() => { setCreating(true); }}>Nueva ejecución</button>
          <${Help} topic="runs.new" />
        </div>
        <${Help} topic="runs.list" />
        <${RunList} token=${token} status=${status} selected=${creating ? null : selected} version=${version}
          onSelect=${(id) => { setCreating(false); setSelected(id); if (startCreating) navigate("runs", id); }} />
      </aside>
      <section class="runs-main" aria-live="off">${main}</section>
    </div>
  </div>`;
}
