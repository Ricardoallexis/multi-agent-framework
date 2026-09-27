// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { ApiError, apiGet } from "./api.js";

const POLL_INTERVAL_MS = 2000;
const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled", "rejected", "interrupted"]);

function element(tag, attributes = {}, text = "") {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === "className") node.className = value;
    else node.setAttribute(name, value);
  }
  if (text !== "") node.textContent = text;
  return node;
}

function safeText(value) {
  return String(value ?? "")
    .replace(/\b[A-Za-z]:\\(?:[^\s<>"|]+\\?)+/g, "[local path omitted]")
    .replace(/\\\\[^\\\s]+\\[^\s<>"|]*/g, "[local path omitted]")
    .replace(/(?:^|\s)\/(?:Users|home|tmp|var|private|mnt|workspace|app)\/[^\s<>"|]*/gi, " [local path omitted]")
    .replace(/file:\/\/\/[^\s)]+/gi, "[local link omitted]");
}

function jsonText(value) {
  try {
    return JSON.stringify(safePayload(value), null, 2);
  } catch {
    return safeText(value);
  }
}

function safePayload(value, key = "") {
  if (typeof value === "string") {
    if (/(?:^|_)(?:path|file_path|uri)$/i.test(key) || /^(?:path|file_path|uri)$/i.test(key)) {
      return "[local path omitted]";
    }
    return safeText(value);
  }
  if (Array.isArray(value)) return value.map((item) => safePayload(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([name, item]) => [
      name,
      /(?:^|_)(?:path|file_path|uri)$/i.test(name) ? "[local path omitted]" : safePayload(item, name),
    ]));
  }
  return value;
}

function timestamp(value) {
  if (typeof value !== "string" || !value) return "Time not provided by API";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? safeText(value) : date.toLocaleString();
}

function formatError(error) {
  if (error instanceof ApiError) {
    const status = error.status ? `HTTP ${error.status}` : "Network";
    return `${status} · ${safeText(error.code)}: ${safeText(error.message)}`;
  }
  return "The request failed. The run state may have changed; refresh before retrying.";
}

function listOf(value) {
  return Array.isArray(value) ? value : [];
}

// The API numbers a run's events 1, 2, 3... (seq). A gap, a repeat, a lower number, or a
// missing seq means this copy is inconsistent; the view then reloads the run instead of guessing.
export function checkEventSequence(events) {
  let expected = 1;
  for (const event of listOf(events)) {
    const seq = Number(event?.seq);
    if (!Number.isInteger(seq) || seq < 1) return { ok: false, lastSeq: expected - 1, problem: "an event has no sequence number" };
    if (seq < expected) return { ok: false, lastSeq: expected - 1, problem: `event ${seq} is repeated or out of order` };
    if (seq > expected) return { ok: false, lastSeq: expected - 1, problem: `events ${expected}–${seq - 1} are missing` };
    expected = seq + 1;
  }
  return { ok: true, lastSeq: expected - 1, problem: "" };
}

function apiPathForRun(runId) {
  return `/runs/${encodeURIComponent(runId)}`;
}

function createRunViewUrl(run) {
  const query = run.bundle ? `?${new URLSearchParams({ bundle: run.bundle })}` : "";
  return `/workflows/${encodeURIComponent(run.workflow_id)}${query}`;
}

export function mount(container, context = {}) {
  const title = container.querySelector("#run-view-title") || element("h2", {}, "Run");
  const status = element("p", { className: "notice", role: "status", "aria-live": "polite" }, "No run selected. Create a run to see its API state here.");
  const errors = element("div", { "aria-live": "polite" });
  const sequenceNotice = element("p", { className: "notice", role: "status" });
  sequenceNotice.hidden = true;
  const controls = element("div", { className: "form-actions" });
  const refreshButton = element("button", { type: "button", className: "secondary" }, "Refresh run");
  const artifactButton = element("button", { type: "button", className: "secondary" }, "Refresh artifacts");
  const details = element("dl");
  const stepInfo = element("p", { className: "muted" });
  const eventsSection = element("section");
  const artifactsSection = element("section");
  const eventsTitle = element("h3", {}, "Run events");
  const artifactsTitle = element("h3", {}, "Artifacts");
  controls.append(refreshButton, artifactButton);
  eventsSection.append(eventsTitle);
  artifactsSection.append(artifactsTitle);
  container.replaceChildren(title, status, errors, sequenceNotice, controls, details, stepInfo, eventsSection, artifactsSection);

  let selectedRunId = "";
  let pollTimer = 0;
  let requestInFlight = false;
  let requestGeneration = 0;
  let lastAnnouncement = "";
  let workflowCacheKey = "";
  let workflowSteps = [];
  let workflowLoadFailed = false;
  let lastSeq = 0;
  let resyncPending = false;

  function showError(error) {
    const box = element("div", { className: "error", role: "alert" });
    box.append(element("p", {}, formatError(error)));
    if (error instanceof ApiError && Object.keys(error.details || {}).length) {
      box.append(element("pre", {}, jsonText(error.details)));
    }
    errors.replaceChildren(box);
  }

  function clearError() {
    errors.replaceChildren();
  }

  // Accept a run only when its events are consistent and not older than what is shown.
  // Otherwise reload it once from the API; if the reloaded copy is still inconsistent,
  // show it as received with a warning rather than reloading in a loop.
  function acceptSequence(run) {
    const check = checkEventSequence(run.events);
    const stale = check.ok && check.lastSeq < lastSeq;
    if (check.ok && !stale) {
      lastSeq = check.lastSeq;
      resyncPending = false;
      sequenceNotice.hidden = true;
      return true;
    }
    const problem = stale ? `this copy ends at event ${check.lastSeq}, older than event ${lastSeq} already shown` : check.problem;
    if (!resyncPending) {
      resyncPending = true;
      sequenceNotice.textContent = `Run events are inconsistent (${problem}); reloading the run from the API.`;
      sequenceNotice.hidden = false;
      window.setTimeout(() => void refreshRun(), 0);
      return false;
    }
    if (stale) return false;
    resyncPending = false;
    lastSeq = check.lastSeq;
    sequenceNotice.textContent = `Warning: run events are still inconsistent after reloading (${problem}). They are shown as the API returned them.`;
    sequenceNotice.hidden = false;
    return true;
  }

  function renderRun(run) {
    if (!acceptSequence(run)) return;
    context.run = run;
    setDetails(run);
    setEvents(run);
    setArtifacts(run.artifacts);
    const announcement = [run.id, run.status, run.current_step, run.waiting_step, run.waiting_reason, run.cancel_requested].join("|");
    if (announcement !== lastAnnouncement) {
      status.textContent = `API state: ${safeText(run.status)}. This reflects the Run resource, not process presence.`;
      status.className = TERMINAL_STATUSES.has(run.status) ? "notice success" : "notice";
      lastAnnouncement = announcement;
    }
    clearError();
    startPolling(run);
  }

  function setDetails(run) {
    const entries = [
      ["Run ID", run.id],
      ["Workflow", run.workflow_id],
      ["Project", run.project_name || run.request?.project_name],
      ["Bundle", run.bundle || "Built-in"],
      ["Run state (API)", run.status],
      ["Created (API time)", timestamp(run.created_at)],
      ["Last updated (API time)", timestamp(run.updated_at)],
    ];
    for (const [label, value] of [
      ["Waiting step", run.waiting_step],
      ["Waiting reason", run.waiting_reason],
      ["Waiting attempt", run.waiting_attempt],
      ["Revision feedback", run.revision_feedback],
      ["Model calls", Number.isFinite(Number(run.llm_calls)) ? `${run.llm_calls} / ${run.max_llm_calls ?? "limit not provided"}` : ""],
      ["Human step request", run.human_step_request?.status === "pending"
        ? `Pending${run.human_step_request.step_id ? ` · ${run.human_step_request.step_id}` : ""} (API record)`
        : ""],
      ["Cancellation requested", run.cancel_requested ? "Yes; API has not yet reported cancellation." : ""],
      ["Run error", run.error],
    ]) {
      if (value) entries.push([label, value]);
    }

    const nodes = [];
    for (const [label, value] of entries) {
      nodes.push(element("dt", {}, safeText(label)));
      nodes.push(element("dd", {}, safeText(value)));
    }
    details.replaceChildren(...nodes);

    const index = Number(run.current_step);
    let currentLabel = "Current step is not available from the API.";
    if (run.waiting_step) {
      currentLabel = `Waiting step: ${safeText(run.waiting_step)}`;
    } else if (Number.isInteger(index) && index >= 0) {
      const step = workflowSteps[index];
      currentLabel = step?.id
        ? `Current step (cursor ${index}${workflowSteps.length ? ` of ${workflowSteps.length}` : ""}, current API definition): ${safeText(step.id)}`
        : `Current step cursor: ${index}${workflowLoadFailed ? " (step definition unavailable)" : workflowCacheKey ? " (no matching step in the current workflow definition)" : ""}`;
    }
    stepInfo.textContent = `${currentLabel} · Cursor is not a percentage; the API run state is authoritative.`;
  }

  function setEvents(run) {
    const events = listOf(run.events);
    if (!events.length) {
      eventsSection.replaceChildren(eventsTitle, element("p", { className: "muted" }, "No events are included in this API response."));
      return;
    }
    const list = element("ol");
    for (const event of events) {
      const item = element("li");
      const number = Number.isInteger(Number(event.seq)) ? `#${Number(event.seq)} · ` : "";
      const heading = element("p", {}, `${number}${safeText(event.event_type || "Event")} · ${timestamp(event.ts)}`);
      item.append(heading);
      if (event.payload !== undefined && event.payload !== null) {
        item.append(element("pre", {}, jsonText(event.payload)));
      }
      list.append(item);
    }
    eventsSection.replaceChildren(eventsTitle, list);
  }

  function setArtifacts(artifacts) {
    const items = listOf(artifacts);
    if (!items.length) {
      artifactsSection.replaceChildren(artifactsTitle, element("p", { className: "muted" }, "No artifacts are included in this API response."));
      return;
    }
    const list = element("ol");
    for (const artifact of items) {
      const item = element("li");
      const label = [
        artifact.step_id ? `Step ${safeText(artifact.step_id)}` : "Artifact",
        artifact.attempt !== undefined ? `attempt ${safeText(artifact.attempt)}` : "",
        artifact.status ? `status ${safeText(artifact.status)}` : "",
        artifact.created_at ? timestamp(artifact.created_at) : "",
      ].filter(Boolean).join(" · ");
      item.append(element("p", {}, label));
      if (Object.hasOwn(artifact, "data")) {
        item.append(element("pre", {}, jsonText(artifact.data)));
      }
      list.append(item);
    }
    artifactsSection.replaceChildren(artifactsTitle, list);
  }

  function stopPolling() {
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = 0;
  }

  function startPolling(run) {
    stopPolling();
    if (!run || TERMINAL_STATUSES.has(run.status) || document.visibilityState !== "visible") return;
    pollTimer = window.setInterval(() => void refreshRun(), POLL_INTERVAL_MS);
  }

  async function loadWorkflow(run, generation) {
    const key = `${run.bundle || ""}:${run.workflow_id || ""}`;
    if (!run.workflow_id || (key === workflowCacheKey && !workflowLoadFailed)) return;
    workflowCacheKey = key;
    workflowSteps = [];
    workflowLoadFailed = false;
    try {
      const definition = await apiGet(createRunViewUrl(run));
      if (generation !== requestGeneration || selectedRunId !== run.id) return;
      workflowSteps = listOf(definition.steps);
    } catch {
      if (generation === requestGeneration) {
        workflowSteps = [];
        workflowLoadFailed = true;
      }
    }
  }

  async function refreshRun() {
    if (!selectedRunId || requestInFlight) return;
    const generation = ++requestGeneration;
    const runId = selectedRunId;
    requestInFlight = true;
    refreshButton.disabled = true;
    refreshButton.textContent = "Refreshing…";
    try {
      const run = await apiGet(apiPathForRun(runId));
      if (generation !== requestGeneration || runId !== selectedRunId) return;
      document.dispatchEvent(new CustomEvent("run:updated", { detail: run }));
      await loadWorkflow(run, generation);
      if (generation !== requestGeneration || runId !== selectedRunId) return;
      renderRun(run);
    } catch (error) {
      if (generation === requestGeneration) showError(error);
    } finally {
      if (generation === requestGeneration) {
        requestInFlight = false;
        refreshButton.disabled = false;
        refreshButton.textContent = "Refresh run";
      }
    }
  }

  async function refreshArtifacts() {
    if (!selectedRunId) return;
    const generation = requestGeneration;
    artifactButton.disabled = true;
    artifactButton.textContent = "Refreshing artifacts…";
    try {
      const artifacts = await apiGet(`${apiPathForRun(selectedRunId)}/artifacts`);
      if (generation !== requestGeneration) return;
      setArtifacts(artifacts);
      clearError();
    } catch (error) {
      if (generation === requestGeneration) showError(error);
    } finally {
      artifactButton.disabled = false;
      artifactButton.textContent = "Refresh artifacts";
    }
  }

  function selectRun(run) {
    if (!run || typeof run.id !== "string" || !run.id) return;
    stopPolling();
    requestGeneration += 1;
    requestInFlight = false;
    selectedRunId = run.id;
    workflowCacheKey = "";
    workflowSteps = [];
    workflowLoadFailed = false;
    lastAnnouncement = "";
    lastSeq = 0;
    resyncPending = false;
    sequenceNotice.hidden = true;
    context.run = run;
    status.textContent = `Selected run ${safeText(run.id)}; loading its API state…`;
    status.className = "notice";
    clearError();
    setDetails(run);
    setEvents(run);
    setArtifacts(run.artifacts);
    void refreshRun();
  }

  function updateRun(event) {
    const run = event.detail;
    if (!run || typeof run.id !== "string" || run.id !== selectedRunId) return;
    renderRun(run);
  }

  refreshButton.addEventListener("click", () => void refreshRun());
  artifactButton.addEventListener("click", () => void refreshArtifacts());
  document.addEventListener("run:selected", (event) => selectRun(event.detail));
  document.addEventListener("run:updated", updateRun);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      stopPolling();
    } else if (selectedRunId) {
      void refreshRun();
    }
  });

  if (context.run) selectRun(context.run);
}
