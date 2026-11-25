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
import { NARROW_QUERY, PHONE_QUERY, computeRunsLayout } from "./runs-layout.js";
import { errorText, fmtDate } from "./text.js";
import { t } from "./i18n.js";
import { StatusChip } from "./ui/status-chip.js";

const LIST_DEBOUNCE_MS = 500;

function useMedia(query) {
  const read = () => { try { return window.matchMedia(query).matches; } catch (_) { return false; } };
  const [matches, setMatches] = useState(read);
  useEffect(() => {
    let list = null;
    try { list = window.matchMedia(query); } catch (_) { return undefined; }
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}

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
  if (!runs) return html`<p class="muted" aria-busy="true">${t("runs.loading")}</p>`;
  if (runs.length === 0) return html`<p class="muted">${t("runs.empty")}</p>`;
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

// The guided chat of "Nueva sesión" (new-session.js, address "#new") is where a new session starts; the form below is the
// classic way to create a run from this section.
export function Runs({ token, status, mock, initialRunId = null }) {
  const [selected, setSelected] = useState(initialRunId);
  const [creating, setCreating] = useState(false);
  const [version, setVersion] = useState(0);
  const narrow = useMedia(NARROW_QUERY);
  const phone = useMedia(PHONE_QUERY);
  const bump = () => setVersion((value) => value + 1);

  // A link from the sessions screen ("#runs/<id>") opens that run.
  useEffect(() => {
    if (initialRunId) { setSelected(initialRunId); setCreating(false); }
    else { setSelected(null); }
  }, [initialRunId]);

  const layout = computeRunsLayout({ narrow, phone, hasSelection: Boolean(selected), creating });
  const goBack = () => { setCreating(false); setSelected(null); navigate("runs"); };

  let main;
  if (creating) {
    main = html`<${CreateRun} token=${token} mock=${mock}
      onClose=${() => setCreating(false)}
      onCreated=${(run) => { setCreating(false); setSelected(run.id); bump(); }} />`;
  } else if (selected) {
    main = html`<${RunDetail} token=${token} runId=${selected} status=${status} onChanged=${bump}
      onBack=${layout.showBack ? goBack : null} />`;
  } else {
    main = html`<p class="muted">${t("runs.choose")}</p>`;
  }
  return html`<div class="runs-page">
    <${Help} topic="runs.section" always=${true} />
    <div class="runs">
      <aside class="runs-side" aria-label=${t("runs.list.aria")} hidden=${!layout.showList}>
        <div>
          <button type="button" class="btn" ...${helpFor("runs.new")} onClick=${() => { setCreating(true); }}>${t("runs.new")}</button>
          <${Help} topic="runs.new" />
        </div>
        <${Help} topic="runs.list" />
        <${RunList} token=${token} status=${status} selected=${creating ? null : selected} version=${version}
          onSelect=${(id) => { setCreating(false); setSelected(id); }} />
      </aside>
      <section class="runs-main" aria-live="off" hidden=${!layout.showDetail}>${main}</section>
    </div>
  </div>`;
}
