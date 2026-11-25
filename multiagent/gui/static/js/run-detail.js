// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// One run: state, steps (with skipped ones and why), counters, human history and results.
// It refreshes when the shared SSE stream carries an event of this run; polling is only the
// degraded mode (stream down, or a bundle run whose store the stream does not follow yet).
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, runPath } from "./api.js";
import { debounce, subscribe } from "./bus.js";
import { Help, helpFor } from "./help.js";
import { HumanActions } from "./human-actions.js";
import { routeHref, threadHref } from "./route.js";
import { connectionLabel, limitReachedText } from "./new-session-model.js";
import {
  DEFAULT_RUN_TAB, NARROW_QUERY, PHONE_QUERY, RUN_DETAIL_TABS, computeRunsLayout, resolveRunTab,
} from "./runs-layout.js";
import {
  actorText, cursorText, errorText, executorText, fmtDate, fmtJson, historyText, skipReason,
  statusInfo, waitingText, MODE_LABELS,
} from "./text.js";
import { contextEventText } from "./session-text.js";
import { Progress } from "./ui/progress.js";
import { StatusChip } from "./ui/status-chip.js";
import { Tabs } from "./ui/tabs.js";
import { t } from "./i18n.js";

const DEGRADED_POLL_MS = 10000;
const REFRESH_DEBOUNCE_MS = 250;

const OPERATING_MODE_LABELS = {
  supervised: "runs.operating_mode.supervised",
  autonomous: "runs.operating_mode.autonomous",
};

const OPERATING_MODE_SOURCES = {
  run: "runs.operating_mode.source.run",
  team: "runs.operating_mode.source.team",
};

export function operatingModeInfo(view) {
  for (const event of (view && view.events) || []) {
    if (!event || event.type !== "operating_mode_set") continue;
    const payload = event.payload;
    if (!payload || typeof payload.mode !== "string" || !payload.mode) continue;
    return {
      mode: payload.mode,
      source: typeof payload.source === "string" ? payload.source : "",
    };
  }
  if (view && typeof view.operating_mode === "string" && view.operating_mode) {
    return {
      mode: view.operating_mode,
      source: typeof view.operating_mode_source === "string" ? view.operating_mode_source : "",
    };
  }
  return null;
}

export function RunOperatingMode({ view }) {
  const modeInfo = operatingModeInfo(view);
  if (!modeInfo) return null;
  const labelKey = OPERATING_MODE_LABELS[modeInfo.mode];
  const sourceKey = OPERATING_MODE_SOURCES[modeInfo.source];
  const label = labelKey ? t(labelKey) : modeInfo.mode;
  const sourceLabel = modeInfo.source ? (sourceKey ? t(sourceKey) : modeInfo.source) : "";
  return html`<span class="badge operating-mode-badge">${t("runs.operating_mode.label", { label, source: sourceLabel ? t("runs.operating_mode.source_suffix", { source: sourceLabel }) : "" })}</span>
    <${Help} topic="run.operating_mode" compact=${true} />`;
}

function Counters({ view }) {
  const counters = view.counters;
  return html`<section class="panel" aria-labelledby="counters-title">
    <h4 id="counters-title">${t("runs.counters.title")}</h4>
    <${Help} topic="run.counters" />
    <dl class="facts">
      <dt>${t("runs.counter.model_calls")}</dt>
      <dd>${t("runs.counter.count_max", { current: counters.model_calls, maximum: counters.max_model_calls })}
        <${Help} topic="counter.model_calls" /></dd>
      <dt>${t("runs.counter.human_steps")}</dt>
      <dd>${counters.human_steps}
        <${Help} topic="counter.human_steps" /></dd>
      <dt>${t("runs.counter.steps")}</dt>
      <dd>${t("runs.counter.steps_count", { done: counters.steps_done, total: counters.steps_total })}${counters.steps_skipped
        ? t(counters.steps_skipped === 1 ? "runs.counter.skipped_one" : "runs.counter.skipped_many", { count: counters.steps_skipped }) : ""}
        <${Help} topic="counter.steps" /></dd>
      <dt>${t("runs.counter.active_time")}</dt>
      <dd>${t("runs.counter.seconds_max", { current: counters.active_seconds, maximum: counters.max_seconds })}
        <${Help} topic="counter.time" /></dd>
    </dl>
  </section>`;
}

// "3 de 5 pasos" (done and skipped steps both count as passed); the skipped ones are named so the number is honest.
function stepsProgressText(counters) {
  const passed = counters.steps_done + counters.steps_skipped;
  const skipped = counters.steps_skipped
    ? t(counters.steps_skipped === 1 ? "runs.progress.skipped_one" : "runs.progress.skipped_many", { count: counters.steps_skipped }) : "";
  return t(counters.steps_total === 1 ? "runs.progress.steps_one" : "runs.progress.steps_many", {
    passed, total: counters.steps_total, skipped,
  });
}

export function stepRolesByStep(view) {
  const roles = new Map();
  for (const event of (view && view.events) || []) {
    if (!event || event.type !== "step_started") continue;
    const payload = event.payload;
    if (!payload || typeof payload.step_id !== "string" || !payload.step_id) continue;
    if (typeof payload.role !== "string" || !payload.role) continue;
    roles.set(payload.step_id, {
      role: payload.role,
      member: typeof payload.member === "string" ? payload.member : "",
      team_id: typeof payload.team_id === "string" ? payload.team_id : "",
      team_version_id: typeof payload.team_version_id === "string" ? payload.team_version_id : "",
      team_version: typeof payload.team_version === "number" ? payload.team_version : null,
    });
  }
  for (const step of (view && view.steps) || []) {
    if (step && typeof step.role === "string" && step.role) {
      roles.set(step.id, {
        role: step.role,
        member: typeof step.member === "string" ? step.member : "",
        team_id: typeof step.team_id === "string" ? step.team_id : "",
        team_version_id: typeof step.team_version_id === "string" ? step.team_version_id : "",
        team_version: typeof step.team_version === "number" ? step.team_version : null,
      });
    }
  }
  return roles;
}

export function roleTeamTitle(info) {
  if (!info || !info.role) return null;
  const parts = [];
  if (info.team_id) parts.push(t("runs.role.team", { team_id: info.team_id }));
  if (info.team_version !== null && info.team_version !== undefined) parts.push(t("runs.role.version", { version: info.team_version }));
  if (info.team_version_id) parts.push(info.team_version_id);
  return parts.length ? parts.join(" · ") : null;
}

export function roleTeamLabel(info) {
  if (!info || !info.role) return "";
  const parts = [];
  if (info.team_id) parts.push(info.team_id);
  if (info.team_version !== null && info.team_version !== undefined) parts.push(t("runs.role.version_short", { version: info.team_version }));
  return parts.length ? "(" + parts.join(" ") + ")" : "";
}

export function Steps({ view }) {
  const roles = stepRolesByStep(view);
  const hasRoles = roles.size > 0;
  const line = cursorText(view);
  return html`<section class="panel" aria-labelledby="steps-title">
    <h4 id="steps-title">${t("runs.tab.steps")}</h4>
    ${line && html`<p class="muted">${line}</p>`}
    ${view.counters.steps_total > 0 && html`<${Progress} label=${t("runs.progress.label")} value=${view.counters.steps_done + view.counters.steps_skipped}
      max=${view.counters.steps_total} valueText=${stepsProgressText(view.counters)} tone=${view.terminal && view.status !== "completed" ? "warning" : "active"} />`}
    <${Help} topic="run.steps" />
    ${view.steps.some((step) => step.skip) && html`<${Help} topic="run.skipped" />`}
    ${hasRoles && html`<${Help} topic="run.role" scope="steps" />`}
    <ol class="steps">
      ${view.steps.map((step) => {
        const roleInfo = roles.get(step.id);
        const teamLabel = roleTeamLabel(roleInfo);
        return html`<li key=${step.id} class=${"step " + step.state}>
          <span class="step-name">${step.id}</span>
          <${StatusChip} status=${step.state} labels="step" />
          ${roleInfo && html`<span class="badge role-badge" title=${roleTeamTitle(roleInfo)}>${t("runs.role.badge", {
            role: roleInfo.role, member: roleInfo.member ? t("runs.role.member", { member: roleInfo.member }) : "",
          })}${teamLabel && html` <span class="muted small">${teamLabel}</span>`}</span>`}
          ${step.skip && html`<span class="step-note">${t(step.state === "will_skip" ? "runs.step.will_skip" : "runs.step.skipped", { reason: skipReason(step.skip) })}</span>`}
          ${step.state === "done" && step.executor && html`<span class="step-note">${t("runs.step.executor_note", {
            executor: executorText(step.executor),
            attempts: step.attempts > 1 ? t("runs.step.attempts", { count: step.attempts }) : "",
          })}</span>`}
          ${step.state === "not_run" && html`<span class="step-note">${t("runs.step.not_run")}</span>`}
        </li>`;
      })}
    </ol>
  </section>`;
}

function History({ view }) {
  return html`<section class="panel" aria-labelledby="history-title">
    <h4 id="history-title">${t("runs.history.title")}</h4>
    <${Help} topic="run.history" />
    ${view.history.length === 0
      ? html`<p class="muted">${t("runs.history.empty")}</p>`
      : html`<ol class="history">
        ${view.history.map((entry) => html`<li key=${entry.seq}>
          <span class="when">${fmtDate(entry.ts)}</span>
          <span class="who">${actorText(entry)}</span>
          <span class="what">${historyText(entry)}</span>
          ${entry.feedback && html`<blockquote>${entry.feedback}</blockquote>`}
          ${entry.error && html`<blockquote class="error-text">${entry.error}</blockquote>`}
        </li>`)}
      </ol>`}
  </section>`;
}

const REVIEW_LABELS = {
  pending: "runs.review.pending",
  approved: "runs.review.approved",
  changes_requested: "runs.review.changes_requested",
  regenerated: "runs.review.regenerated",
  rejected: "runs.review.rejected",
};

const COMMENT_KIND_LABELS = {
  fix: "runs.comment_kind.fix",
  add: "runs.comment_kind.add",
  remove: "runs.comment_kind.remove",
  rewrite: "runs.comment_kind.rewrite",
  other: "runs.comment_kind.other",
};

const CHANGE_LABELS = {
  added: "runs.change.added",
  removed: "runs.change.removed",
  changed: "runs.change.changed",
};

function producerLabel(producer) {
  if (!producer || !producer.kind) return "—";
  if (producer.kind === "human") {
    return t("runs.producer.human", {
      provider: producer.provider || "human",
      model: producer.model ? t("runs.producer.model_suffix", { model: producer.model }) : "",
    });
  }
  const parts = [producer.model, producer.provider].filter(Boolean);
  const names = parts.join(" · ");
  return names ? t("runs.producer.model", { model: names }) : t("runs.producer.model_unknown");
}

function tokensLabel(tokens) {
  if (!tokens) return "—";
  const parts = [];
  if (tokens.tokens_in !== null && tokens.tokens_in !== undefined) parts.push(t("runs.tokens.input", { count: tokens.tokens_in }));
  if (tokens.tokens_out !== null && tokens.tokens_out !== undefined) parts.push(t("runs.tokens.output", { count: tokens.tokens_out }));
  if (tokens.tokens_total !== null && tokens.tokens_total !== undefined) parts.push(t("runs.tokens.total", { count: tokens.tokens_total }));
  return parts.length ? parts.join(" · ") : "—";
}

function groupArtifactsByStep(artifacts) {
  const groups = new Map();
  for (const artifact of artifacts || []) {
    const list = groups.get(artifact.step_id) || [];
    list.push(artifact);
    groups.set(artifact.step_id, list);
  }
  return Array.from(groups.entries()).map(([stepId, list]) => ({
    stepId,
    items: list.slice().sort((a, b) => a.attempt - b.attempt),
  }));
}

function StepResults({ runId, stepId, items, waiting, token }) {
  const [attemptsInfo, setAttemptsInfo] = useState(null);
  const [leftAttempt, setLeftAttempt] = useState(items.length >= 2 ? String(items[items.length - 2].attempt) : "");
  const [rightAttempt, setRightAttempt] = useState(items.length >= 1 ? String(items[items.length - 1].attempt) : "");
  const [comparing, setComparing] = useState(false);
  const [comparison, setComparison] = useState(null);
  const [compareError, setCompareError] = useState(null);

  useEffect(() => {
    if (items.length >= 2) {
      setLeftAttempt(String(items[items.length - 2].attempt));
      setRightAttempt(String(items[items.length - 1].attempt));
    } else if (items.length === 1) {
      setLeftAttempt(String(items[0].attempt));
      setRightAttempt(String(items[0].attempt));
    }
    setComparison(null);
    setCompareError(null);
  }, [runId, stepId, items.length]);

  useEffect(() => {
    let active = true;
    if (!token) return undefined;
    const path = runPath(runId, "steps/" + encodeURIComponent(stepId) + "/attempts");
    apiGet(path, token)
      .then((data) => { if (active) setAttemptsInfo(data); })
      .catch(() => { /* fallback to artifacts already present in view */ });
    return () => { active = false; };
  }, [runId, stepId, items.length, token]);

  const byAttempt = new Map();
  for (const entry of (attemptsInfo && attemptsInfo.attempts) || []) {
    byAttempt.set(entry.attempt, entry);
  }
  const attemptNumbers = items.map((item) => item.attempt);
  const canCompare = attemptNumbers.length >= 2;

  async function runCompare() {
    if (!canCompare) return;
    setComparing(true);
    setCompareError(null);
    try {
      const path = runPath(
        runId,
        "steps/" + encodeURIComponent(stepId) + "/attempts/compare?a=" +
          encodeURIComponent(leftAttempt) + "&b=" + encodeURIComponent(rightAttempt),
      );
      const data = await apiGet(path, token);
      setComparison(data);
    } catch (failure) {
      setComparison(null);
      setCompareError(failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
    } finally {
      setComparing(false);
    }
  }

  return html`<div class="step-results">
    <div class="step-results-head">
      <h5>${t("runs.step_attempts.title", { step_id: stepId, count: items.length, label: t(items.length === 1 ? "runs.attempt.one" : "runs.attempt.many") })}</h5>
      ${canCompare && html`<div class="compare-controls">
        <label class="compare-field">${t("runs.compare.attempt_a")}
          <select aria-label=${t("runs.compare.attempt_a_for_step", { step_id: stepId })} ...${helpFor("run.compare_select")}
            value=${leftAttempt} onChange=${(event) => setLeftAttempt(event.target.value)}>
            ${attemptNumbers.map((num) => html`<option key=${num} value=${String(num)}>${t("runs.attempt.option", { count: num })}</option>`)}
          </select>
        </label>
        <label class="compare-field">${t("runs.compare.attempt_b")}
          <select aria-label=${t("runs.compare.attempt_b_for_step", { step_id: stepId })} ...${helpFor("run.compare_select")}
            value=${rightAttempt} onChange=${(event) => setRightAttempt(event.target.value)}>
            ${attemptNumbers.map((num) => html`<option key=${num} value=${String(num)}>${t("runs.attempt.option", { count: num })}</option>`)}
          </select>
        </label>
        <${Help} topic="run.compare_select" compact=${true} />
        <button type="button" class="btn secondary small" disabled=${comparing} ...${helpFor("run.compare")}
          onClick=${runCompare}>${t(comparing ? "runs.compare.progress" : "runs.compare.button")}</button>
        <${Help} topic="run.compare" compact=${true} />
      </div>`}
    </div>
    ${compareError && html`<p class="message error" role="alert">${errorText(compareError)}</p>`}
    ${comparison && html`<div class="compare-panel" aria-live="polite">
      <p class="compare-summary"><strong>${t("runs.compare.summary", { first: comparison.a.attempt, second: comparison.b.attempt })}</strong></p>
      ${comparison.changes.length === 0
        ? html`<p class="muted">${t("runs.compare.no_changes", { first: comparison.a.attempt, second: comparison.b.attempt })}</p>`
        : html`<ul class="compare-list">
          ${comparison.changes.map((item) => html`<li key=${item.path + ":" + item.change} class=${"compare-item compare-" + item.change}>
            <div class="compare-item-head">
              <span class="badge">${CHANGE_LABELS[item.change] ? t(CHANGE_LABELS[item.change]) : item.change}</span>
              <code>${item.path || t("runs.compare.root")}</code>
            </div>
            ${item.diff
              ? html`<pre class="data">${item.diff}</pre>`
              : html`<div class="compare-values">
                ${item.change !== "added" && html`<div><span class="muted">${t("runs.compare.before")}</span><pre class="data">${fmtJson(item.before)}</pre></div>`}
                ${item.change !== "removed" && html`<div><span class="muted">${t("runs.compare.after")}</span><pre class="data">${fmtJson(item.after)}</pre></div>`}
              </div>`}
          </li>`)}
        </ul>`}
    </div>`}
    ${items.map((artifact, index) => {
      const detail = byAttempt.get(artifact.attempt);
      const isOpen = Boolean(waiting && waiting.step_id === artifact.step_id && index === items.length - 1);
      return html`<${AttemptDetail} key=${artifact.step_id + ":" + artifact.attempt} artifact=${artifact} detail=${detail}
        isOpen=${isOpen} scopePrefix=${runId} />`;
    })}
  </div>`;
}

export function AttemptDetail({ artifact, detail, isOpen = false, scopePrefix = "" }) {
  const review = detail && detail.review;
  const input = detail && detail.input;
  const human = detail && detail.human;
  const hasRole = Boolean(detail && typeof detail.role === "string" && detail.role);
  const teamLabel = hasRole ? roleTeamLabel(detail) : "";
  const helpScope = (scopePrefix ? scopePrefix + "-" : "") + artifact.step_id + "-" + artifact.attempt;
  return html`<details open=${isOpen}>
    <summary>${t("runs.attempt.summary", {
      count: artifact.attempt,
      approval: t(artifact.approved ? "runs.attempt.approved" : "runs.attempt.unapproved"),
      kind: artifact.kind,
      role: hasRole ? t("runs.attempt.role", { role: detail.role, member: detail.member ? t("runs.attempt.member", { member: detail.member }) : "" }) : "",
      producer: detail && detail.producer ? t("runs.attempt.producer", { producer: producerLabel(detail.producer) }) : "",
    })}</summary>
    ${detail && html`<dl class="facts attempt-meta">
      <dt>${t("runs.attempt.produced_by")}</dt><dd>${producerLabel(detail.producer)}</dd>
      ${hasRole && html`<dt>${t("runs.role.label")}</dt><dd title=${roleTeamTitle(detail)}>${detail.role}${detail.member ? " · " + detail.member : ""}${teamLabel && html` <span class="muted small">${teamLabel}</span>`}
        <${Help} topic="run.role" compact=${true} scope=${helpScope} /></dd>`}
      ${input && input.revision_feedback && html`<dt>${t("runs.attempt.feedback")}</dt><dd>${input.revision_feedback}${input.has_previous_output ? " " + t("runs.attempt.previous_output") : ""}</dd>`}
      ${review && html`<dt>${t("runs.attempt.review")}</dt><dd>${REVIEW_LABELS[review.status] ? t(REVIEW_LABELS[review.status]) : review.status}${review.actor ? " · " + review.actor : ""}${review.reviewed_at ? " · " + fmtDate(review.reviewed_at) : ""}</dd>`}
      ${review && review.channel && html`<dt>${t("runs.attempt.channel")}</dt><dd>${review.channel}</dd>`}
      ${review && review.comments && review.comments.length > 0 && html`<dt>${t("runs.attempt.comments")}</dt><dd><ol>${review.comments.map((comment, index) => html`<li key=${index}><strong>${t(comment.target === "general" ? "runs.comment.general" : "runs.comment.target", { target: comment.target })} · ${COMMENT_KIND_LABELS[comment.kind] ? t(COMMENT_KIND_LABELS[comment.kind]) : comment.kind}</strong>: ${comment.text}${comment.quote && html`<blockquote>${comment.quote}</blockquote>`}</li>`)}</ol></dd>`}
      ${human && human.notes && html`<dt>${t("runs.attempt.human_notes")}</dt><dd>${human.notes}</dd>`}
      ${detail.tokens && html`<dt>${t("runs.attempt.tokens")}</dt><dd>${tokensLabel(detail.tokens)}</dd>`}
    </dl>`}
    ${human && human.prompt_used && html`<details class="attempt-extra"><summary>${t("runs.attempt.prompt_used")}</summary><pre class="prompt">${human.prompt_used}</pre></details>`}
    <pre class="data">${fmtJson(artifact.data)}</pre>
    ${detail && detail.markdown && html`<details class="attempt-extra"><summary>${t("runs.attempt.markdown")}</summary><pre class="data">${detail.markdown}</pre></details>`}
  </details>`;
}

function Results({ view, token }) {
  const groups = groupArtifactsByStep(view.artifacts);
  return html`<section class="panel" aria-labelledby="results-title">
    <h4 id="results-title">${t("runs.tab.results")}</h4>
    <${Help} topic="run.results" />
    ${groups.length === 0
      ? html`<p class="muted">${t("runs.results.empty")}</p>`
      : groups.map((group) => html`<${StepResults} key=${group.stepId} runId=${view.id} stepId=${group.stepId}
        items=${group.items} waiting=${view.waiting} token=${token} />`)}
  </section>`;
}

function Technical({ view }) {
  return html`<details class="panel technical">
    <summary>${t("runs.technical.title", {
      total: view.events_total,
      latest: view.events_total > view.events.length ? t("runs.technical.latest", { count: view.events.length }) : "",
    })}</summary>
    <${Help} topic="run.events" />
    <ol class="events">
      ${view.events.map((event) => html`<li key=${event.seq}><code>#${event.seq}</code> ${fmtDate(event.ts)} · ${contextEventText(event.type, event.payload) || event.type}
        ${Object.keys(event.payload || {}).length > 0 && html`<pre class="data">${fmtJson(event.payload)}</pre>`}</li>`)}
    </ol>
  </details>`;
}

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

export function RunInputs({ view }) {
  return html`<section class="panel" aria-labelledby="inputs-title">
    <h4 id="inputs-title">${t("runs.inputs.title")}</h4>
    <${Help} topic="run.inputs" />
    ${Object.keys(view.inputs).length === 0
      ? html`<p class="muted">${t("runs.inputs.empty")}</p>`
      : html`<dl class="facts">${Object.entries(view.inputs).map(([name, value]) => html`<${Input} key=${name} name=${name} value=${value} />`)}</dl>`}
  </section>`;
}

export function RunTabContent({ tab, view, token }) {
  const activeTab = resolveRunTab(tab);
  if (activeTab === "results") {
    return html`<${Results} view=${view} token=${token} />`;
  }
  if (activeTab === "details") {
    return html`<${History} view=${view} />
      <${RunInputs} view=${view} />
      <${Technical} view=${view} />`;
  }
  return html`<${Steps} view=${view} />
    <${Counters} view=${view} />`;
}

export function RunDetail({ token, runId, status, onChanged, onBack = null }) {
  const [view, setView] = useState(null);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState(DEFAULT_RUN_TAB);
  const narrow = useMedia(NARROW_QUERY);
  const phone = useMedia(PHONE_QUERY);
  const latest = useRef(0);
  const current = useRef(runId);
  current.current = runId;

  async function load() {
    const ticket = ++latest.current;
    const asked = runId;
    try {
      const result = await apiGet(runPath(asked), token);
      if (ticket !== latest.current || current.current !== asked) return; // a newer answer or another run
      setView(result);
      setError(null);
    } catch (failure) {
      if (ticket !== latest.current || current.current !== asked) return;
      setError(failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
    }
  }

  function accept(updated) {
    latest.current += 1; // answers requested before this action are older than it
    setView(updated);
    setError(null);
    if (onChanged) onChanged();
  }

  useEffect(() => { setView(null); setError(null); load(); }, [runId, token]);
  useEffect(() => { setTab(DEFAULT_RUN_TAB); }, [runId]);

  useEffect(() => {
    const refresh = debounce(load, REFRESH_DEBOUNCE_MS);
    const off = subscribe((event) => { if (event.run_id === runId) refresh(); });
    return () => { off(); refresh.cancel(); };
  }, [runId, token]);

  useEffect(() => { if (status === "connected") load(); }, [status]);

  const live = status === "connected";
  const polling = view && !view.terminal && (!live || Boolean(view.bundle));
  useEffect(() => {
    if (!polling) return undefined;
    const timer = setInterval(load, DEGRADED_POLL_MS);
    return () => clearInterval(timer);
  }, [polling, runId, token]);

  const backBar = onBack ? html`<div class="header-tools back-bar">
    <button type="button" class="btn secondary small" ...${helpFor("runs.back")} onClick=${onBack}>${t("runs.back")}</button>
    <${Help} topic="runs.back" compact=${true} />
  </div>` : null;

  if (!view) {
    return html`<div class="run-detail-state">
      ${backBar}
      ${error
        ? html`<p class="message error" role="alert">${errorText(error)}</p>`
        : html`<p class="muted" aria-busy="true">${t("runs.detail.loading")}</p>`}
    </div>`;
  }
  const info = statusInfo(view.status);
  const layout = computeRunsLayout({
    narrow,
    phone,
    hasSelection: true,
    waiting: Boolean(view.waiting && !view.terminal),
    terminal: Boolean(view.terminal),
  });
  const activeTab = resolveRunTab(tab);
  const chatHref = typeof threadHref === "function" ? threadHref(view.id) : null;
  const actionsCard = html`<${HumanActions} token=${token} view=${view} onUpdate=${accept} onStale=${load} actionBar=${layout.actionBar} />`;

  return html`<article class=${"run-detail" + (layout.actionBar ? " has-action-bar" : "")} aria-labelledby="run-title">
    ${backBar}
    <header>
      <h3 id="run-title">${view.workflow_id} <span class="muted">· ${view.project_name || t("common.value.no_project")}</span></h3>
      <div class="status-line">
        <${StatusChip} status=${view.status} labels="run" />
        ${!view.terminal && html`<span class=${"badge " + (live ? "connected" : "reconnecting")}>${t(live ? "runs.live.connected" : "runs.live.disconnected")}</span>`}
        <${RunOperatingMode} view=${view} />
        ${chatHref && html`<a class="btn secondary small" href=${chatHref} ...${helpFor("run.open_chat")}>${t("runs.open_chat")}</a>
          <${Help} topic="run.open_chat" compact=${true} />`}
        <button type="button" class="btn secondary small" ...${helpFor("run.refresh")} onClick=${load}>${t("runs.refresh")}</button>
        <${Help} topic="run.refresh" compact=${true} />
      </div>
      ${!view.terminal && html`<${Help} topic="run.live" />`}
      <p class="muted">${t("runs.detail.prefix")} <code>${view.id}</code>${t("runs.detail.meta", {
        created: fmtDate(view.created_at),
        updated: fmtDate(view.updated_at),
        source: view.bundle ? t("runs.detail.bundle", { bundle: view.bundle }) : t("runs.detail.workflow"),
        mode: MODE_LABELS[view.execution_mode] || view.execution_mode,
      })}</p>
    </header>
    ${polling && html`<p class="notice">${view.bundle && live
      ? t("runs.notice.bundle_poll")
      : t("runs.notice.connection_poll")}</p>`}
    ${error && html`<p class="message error" role="alert">${t("runs.error.refresh", { message: errorText(error) })}</p>`}
    <p class="summary">${view.waiting ? waitingText(view.waiting) : info.help}${view.cancel_requested && !view.terminal ? " " + t("runs.cancel_requested") : ""}</p>
    ${view.error && html`<p class="message error" role="alert">${t("runs.error.run", { message: view.error })}</p>`}
    ${view.limit && html`<div class="notice limit-notice" role="status" aria-label=${t("runs.limit.reached")}>
      <p>${limitReachedText(view.limit)}</p>
      ${Array.isArray(view.limit.suggestions) && view.limit.suggestions.length > 0 && html`<p class="muted small">${t("runs.limit.options", { options: view.limit.suggestions.map(connectionLabel).join(", ") })}</p>`}
      <div class="actions">
        <a class="btn secondary small" href=${routeHref("new")} ...${helpFor("run.limit_retry")}>${t("runs.limit.retry")}</a>
        <${Help} topic="run.limit_retry" compact=${true} />
      </div>
    </div>`}
    ${layout.actionFirst && actionsCard}
    <${Tabs} id="rundetail" label=${t("runs.tabs.aria")} className="run-tabs" selected=${activeTab} onSelect=${setTab} ...${helpFor("run.tabs")}
      tabs=${RUN_DETAIL_TABS.map((item) => ({ ...item, label: t(item.label) }))}
      after=${html`<${Help} topic="run.tabs" compact=${true} />`}>
      <${RunTabContent} tab=${activeTab} view=${view} token=${token} />
    <//>
    ${!layout.actionFirst && actionsCard}
  </article>`;
}

// Two sibling roots: htm returns them as an array, which Preact renders as is.
function Input({ name, value }) {
  return html`<dt><code>${name}</code></dt><dd>${value || html`<span class="muted">${t("runs.inputs.empty_value")}</span>`}</dd>`;
}
