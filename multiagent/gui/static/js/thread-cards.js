// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Cards of the run conversation. Values from the server are rendered as text by htm/Preact.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet, runPath } from "./api.js";
import { Help, helpFor } from "./help.js";
import { errorText, fmtJson } from "./text.js";
import { readable } from "./readable.js";
import { actionsFor, changesBody, detailHref, modelLimitNotice, previousStepIds, quoteInto, reviewArtifact, showRawOutput,
  threadLinksFor } from "./thread-model.js";
import { t } from "./i18n.js";

const LABELS = {
  launched: "thread.card.started",
  step: "thread.card.step",
  user_message: "thread.card.user_message",
  message_consumed: "thread.card.message_read",
  waiting: "thread.card.waiting",
  final: "thread.card.final",
};
const KIND_LABELS = {
  fix: "runs.comment_kind.fix",
  add: "runs.comment_kind.add",
  remove: "runs.comment_kind.remove",
  rewrite: "runs.comment_kind.rewrite",
  other: "runs.comment_kind.other",
};
const REASONS = {
  review: "thread.reason.review",
  tool_approval: "thread.reason.tool_approval",
  execute_step: "thread.reason.execute_step",
  research: "thread.reason.research",
  escalation_approval: "thread.reason.escalation_approval",
  budget_exhausted: "thread.reason.budget_exhausted",
  content_review: "thread.reason.content_review",
  function_review: "thread.reason.function_review",
  function_failure: "thread.reason.function_failure",
};
const WAITING_TEXT_KEYS = {
  review: "thread.event.waiting_review",
  tool_approval: "thread.event.waiting_tool_approval",
  execute_step: "thread.event.waiting_execute_step",
  research: "thread.event.waiting_research",
  escalation_approval: "thread.event.waiting_escalation",
  budget_exhausted: "thread.event.waiting_budget",
  content_review: "thread.event.waiting_content_review",
  function_review: "thread.event.waiting_function_review",
  function_failure: "thread.event.waiting_function_failure",
};
const FINAL_TEXT_KEYS = {
  completed: "thread.event.completed",
  failed: "thread.event.failed",
  cancelled: "thread.event.cancelled",
  rejected: "thread.event.rejected",
  interrupted: "thread.event.interrupted",
};

export function threadEntryText(entry) {
  if (entry.kind === "launched" && typeof entry.workflow_id === "string" && entry.text === "Started " + entry.workflow_id) {
    return t("thread.event.started", { workflow_id: entry.workflow_id });
  }
  if (entry.kind === "step" && typeof entry.step_id === "string" && entry.text === entry.step_id + " completed") {
    return t("thread.event.step_completed", { step_id: entry.step_id });
  }
  if (entry.kind === "message_consumed" && entry.text === "Message read") return t("thread.event.message_read");
  if (entry.kind === "waiting" && WAITING_TEXT_KEYS[entry.reason] && entry.text === "Waiting: " + entry.reason) {
    return t(WAITING_TEXT_KEYS[entry.reason]);
  }
  if (entry.kind === "final" && FINAL_TEXT_KEYS[entry.status] && entry.text === "Run " + entry.status) {
    return t(FINAL_TEXT_KEYS[entry.status]);
  }
  return entry.text || "";
}

export function ReadableValue({ node }) {   // CHT-T06: also used by plan-thread.js and plans.js
  if (node.kind === "long") return html`<details class="thread-long"><summary ...${helpFor("thread.output")}>${t("thread.value.long")}</summary>
    <pre class="thread-readable-text">${node.text}</pre></details>`;
  if (node.kind === "fields") return html`<dl class="thread-fields">${node.fields.map(({ name, value }) => html`
    <dt key=${name + "-name"}>${name}</dt><dd key=${name}><${ReadableValue} node=${value} /></dd>`)}</dl>`;
  if (node.kind === "table") return html`<div class="usage-table-wrap" role="region" aria-label=${t("thread.value.table")} tabindex="0">
    <table><thead><tr>${node.columns.map((name) => html`<th key=${name} scope="col">${name}</th>`)}</tr></thead>
      <tbody>${node.rows.map((row, index) => html`<tr key=${index}>${row.map((cell, column) => html`
        <td key=${column}><${ReadableValue} node=${cell} /></td>`)}</tr>`)}</tbody></table></div>`;
  if (node.kind === "list") return html`<ol class="thread-value-list">${node.items.map((item, index) => html`
    <li key=${index}><${ReadableValue} node=${item} /></li>`)}</ol>`;
  return html`<span>${node.text}</span>`;
}

function ReviewOutput({ entry, view, runId, token, mode }) {
  const [earlier, setEarlier] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const request = useRef(0);
  useEffect(() => {
    request.current += 1;
    setEarlier(null); setLoading(false); setLoadError(false);
    return () => { request.current += 1; };
  }, [runId, entry.step_id, entry.attempt]);
  const artifact = reviewArtifact(view, entry);
  const steps = previousStepIds(view, entry);
  async function showEarlier() {
    if (loading) return;
    const ticket = ++request.current;
    setLoading(true); setLoadError(false);
    try {
      const pages = await Promise.all(steps.map((stepId) => apiGet(
        runPath(runId, "steps/" + encodeURIComponent(stepId) + "/attempts"), token)));
      if (ticket === request.current) setEarlier(steps.map((stepId, index) => ({ stepId, attempts: pages[index].attempts || [] })));
    } catch (_) { if (ticket === request.current) setLoadError(true); }
    finally { if (ticket === request.current) setLoading(false); }
  }
  return html`<${ReviewBody} artifact=${artifact} steps=${steps} earlier=${earlier} loading=${loading}
    loadError=${loadError} showEarlier=${showEarlier} mode=${mode} />`;
}

// SIM-T03: the review section as a pure function of its data, so Node can draw it. Simple mode keeps the readable output and drops
// the raw JSON and the earlier steps; advanced mode draws all of it, as before.
export function ReviewBody({ artifact, steps, earlier, loading, loadError, showEarlier, mode = "advanced" }) {
  const raw = showRawOutput(mode);
  return html`<section class="thread-review" aria-label=${t("thread.review.title")}>
    <h4>${t("thread.review.title")}</h4><${Help} topic="thread.output" compact=${true} />
    ${artifact ? html`<${ReadableValue} node=${readable(artifact.data)} />
      ${raw && html`<details class="thread-json"><summary ...${helpFor("thread.json")}>${t("thread.json.full")}</summary>
        <${Help} topic="thread.json" compact=${true} /><pre class="thread-readable-text">${fmtJson(artifact.data)}</pre></details>`}`
      : html`<p class="muted">${t("thread.review.output_missing")}</p>`}
    ${raw && steps.length > 0 && html`<button type="button" class="btn secondary" disabled=${loading}
      ...${helpFor("thread.previous")} onClick=${showEarlier}>${t(loading ? "thread.previous.loading" : "thread.previous.button")}</button>`}
    ${raw && steps.length > 0 && html`<${Help} topic="thread.previous" compact=${true} />`}
    ${raw && loadError && html`<p class="message error" role="alert">${t("thread.previous.error")}</p>`}
    ${raw && earlier && html`<div class="thread-earlier">${earlier.map(({ stepId, attempts }) => html`<section key=${stepId}>
      <h5>${t("thread.previous.step", { step_id: stepId })}</h5>${attempts.length ? attempts.map((attempt) => html`<details key=${attempt.attempt}>
        <summary ...${helpFor("thread.previous")}>${t("runs.attempt.option", { count: attempt.attempt })}</summary><${ReadableValue} node=${readable(attempt.output)} />
        <details class="thread-json"><summary ...${helpFor("thread.json")}>${t("thread.json.full")}</summary>
          <${Help} topic="thread.json" compact=${true} /><pre class="thread-readable-text">${fmtJson(attempt.output)}</pre></details>
      </details>`) : html`<p class="muted">${t("thread.previous.empty")}</p>`}</section>`)}</div>`}
  </section>`;
}

const PREVIEW_REASONS = {
  no_pending_approval: "thread.preview.no_pending_approval",
  permission_request_mismatch: "thread.preview.permission_request_mismatch",
  file_tools_disabled: "thread.preview.file_tools_disabled",
  file_write_unavailable: "thread.preview.file_write_unavailable",
  run_inputs_unavailable: "thread.preview.run_inputs_unavailable",
  declaration_unavailable: "thread.preview.declaration_unavailable",
  arguments_unavailable: "thread.preview.arguments_unavailable",
  arguments_mismatch: "thread.preview.arguments_mismatch",
};

export function ToolApprovalPreview({ runId, token, permissionRequestId }) {
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const request = useRef(0);
  useEffect(() => {
    const ticket = ++request.current;
    setPreview(null); setError("");
    const path = runPath(runId, "tool-approval/preview") + "?permission_request_id=" +
      encodeURIComponent(permissionRequestId || "");
    apiGet(path, token)
      .then((result) => { if (ticket === request.current) setPreview(result); })
      .catch((failure) => { if (ticket === request.current) setError(errorText(failure)); });
    return () => { request.current += 1; };
  }, [runId, token, permissionRequestId]);
  if (preview && !preview.available && preview.reason === "unsupported_tool") return null;
  return html`<section class="tool-approval-preview" aria-label=${t("thread.preview.title")}>
    <h4>${t("thread.preview.title")}</h4><${Help} topic="human.tool_preview" compact=${true} />
    ${error && html`<p class="message error" role="alert">${error}</p>`}
    ${!error && !preview && html`<p class="muted" aria-busy="true">${t("thread.preview.loading")}</p>`}
    ${preview && !preview.available && html`<p class="message info" role="status">${PREVIEW_REASONS[preview.reason] ? t(PREVIEW_REASONS[preview.reason]) : preview.message}</p>`}
    ${preview && preview.available && html`<div>
      <p><strong>${t("thread.preview.path")}</strong> ${preview.path}</p>
      ${preview.removed_lines > 0 && html`<p class="notice" role="alert">${t("thread.preview.removed_lines", { count: preview.removed_lines,
        label: t(preview.removed_lines === 1 ? "thread.preview.line.one" : "thread.preview.line.many") })}</p>`}
      ${preview.exists
        ? html`<h5>${t("thread.preview.content")}</h5><pre class="thread-readable-text">${preview.content}</pre>
          <h5>${t("thread.preview.diff")}</h5>
          ${preview.diff ? html`<pre class="thread-readable-text">${preview.diff}</pre>`
            : html`<p class="muted">${t("thread.preview.same_content")}</p>`}`
        : html`<h5>${t("thread.preview.new_file")}</h5>
          <pre class="thread-readable-text">${preview.diff || preview.content}</pre>`}
    </div>`}
  </section>`;
}

// EJC-T01: the text the person selected in the page, as long as it is not inside a form field.
function useSelectedText() {
  const [text, setText] = useState("");
  useEffect(() => {
    const read = () => {
      const selection = window.getSelection();
      const node = selection && selection.anchorNode ? (selection.anchorNode.parentElement || null) : null;
      const inField = Boolean(node && node.closest("textarea, input, select"));
      setText(selection && !inField ? selection.toString().trim() : "");
    };
    document.addEventListener("selectionchange", read);
    return () => document.removeEventListener("selectionchange", read);
  }, []);
  return text;
}

// «Citar»: hands the selected output text to the message box as plain text; it never runs anything.
export function QuoteButton({ onQuote, busy }) {
  const selected = useSelectedText();
  return html`<button type="button" class="btn secondary thread-quote" disabled=${busy || !selected} ...${helpFor("thread.quote")}
    onMouseDown=${(event) => event.preventDefault()} onClick=${() => onQuote(selected)}>${t("thread.quote")}</button>
    <${Help} topic="thread.quote" compact=${true} />`;
}

function Decision({ entry, busy, onAction, runId, token, mode }) {
  const [pending, setPending] = useState("");
  const [feedback, setFeedback] = useState("");
  const [comments, setComments] = useState([]);
  const [error, setError] = useState("");
  const actions = actionsFor(entry, "waiting_human");
  if (!actions.length) return null;
  const tool = entry.decision.type === "tool_approval";
  const approveBody = tool ? { permission_request_id: entry.decision.permission_request_id } : {};
  const structured = showRawOutput(mode);   // structured comments are for advanced mode; free text is the main road
  const addComment = () => setComments([...comments, { target: "general", kind: "fix", text: "", quote: "" }]);
  const patchComment = (index, patch) => setComments(comments.map((item, i) => i === index ? { ...item, ...patch } : item));
  const send = () => {
    let body = pending === "approve" ? approveBody : { feedback: feedback.trim() };
    if (pending === "changes") {
      const built = changesBody(feedback, comments, { structured });
      if (built.error) {
        setError(t(built.error === "required" ? "thread.decision.change_required" : "thread.decision.comment_required")); return;
      }
      body = built.body;
    }
    onAction(pending, body);
  };
  return html`<div class="thread-decision">
    <p class="muted">${REASONS[entry.reason] ? t(REASONS[entry.reason]) : entry.reason || t("thread.reason.stopped")} · ${t("thread.reason.step", { step_id: entry.step_id || t("common.value.unspecified") })}${entry.attempt && showRawOutput(mode) ? " · " + t("thread.reason.attempt", { count: entry.attempt }) : ""}</p>
    ${entry.reason === "function_review" && html`<p class="muted">${t("thread.function.approve_once")}</p>`}
    ${tool && html`<${ToolApprovalPreview} runId=${runId} token=${token}
      permissionRequestId=${entry.decision.permission_request_id} />`}
    ${!pending && html`<div class="thread-actions">
      ${actions.includes("approve") && html`<button type="button" class="btn" disabled=${busy || (tool && !entry.decision.permission_request_id)}
        ...${helpFor("thread.approve")} onClick=${() => setPending("approve")}>${t("thread.action.approve")}</button>`}
      ${actions.includes("changes") && html`<button type="button" class="btn secondary" disabled=${busy}
        ...${helpFor("thread.changes")} onClick=${() => setPending("changes")}>${t("thread.action.changes")}</button>`}
      ${actions.includes("reject") && html`<button type="button" class="btn secondary" disabled=${busy}
        ...${helpFor("thread.reject")} onClick=${() => setPending("reject")}>${t("thread.action.reject")}</button>`}
      <${Help} topic="thread.approve" compact=${true} /><${Help} topic="thread.changes" compact=${true} />
      <${Help} topic="thread.reject" compact=${true} />
    </div>`}
    ${pending && html`<div class="thread-confirm" role="group" aria-label=${t("thread.decision.confirm")}>
      <p><strong>${t("thread.decision.confirm_question", {
        action: t(pending === "approve" ? "thread.action.approve" : pending === "changes" ? "thread.action.changes" : "thread.action.reject"),
      })}</strong></p>
      ${(pending === "changes" || pending === "reject") && html`<label>${t("thread.decision.reason")} ${pending === "reject" ? t("runs.human.optional") : t("thread.decision.guidance")}
        <textarea rows="3" maxlength="4000" value=${feedback} disabled=${busy} ...${helpFor("thread.feedback")}
          onInput=${(event) => { setFeedback(event.target.value); setError(""); }}></textarea></label>
        <${Help} topic="thread.feedback" compact=${true} />`}
      ${pending === "changes" && html`<${QuoteButton} busy=${busy} onQuote=${(text) => { setFeedback(quoteInto(feedback, text)); setError(""); }} />`}
      ${pending === "changes" && structured && html`<div class="thread-comments">
        ${comments.map((item, index) => html`<fieldset key=${index}><legend>${t("runs.human.comment.legend", { count: index + 1 })}</legend>
          <label>${t("runs.human.comment.part")}<input maxlength="200" value=${item.target} disabled=${busy} ...${helpFor("thread.comment")}
            onInput=${(event) => patchComment(index, { target: event.target.value })} /></label>
          <label>${t("runs.human.comment.type")}<select value=${item.kind} disabled=${busy} ...${helpFor("thread.comment")}
            onChange=${(event) => patchComment(index, { kind: event.target.value })}>
            ${Object.entries(KIND_LABELS).map(([kind, key]) => html`<option key=${kind} value=${kind}>${t(key)}</option>`)}
          </select></label>
          <label>${t("runs.human.comment.text")}<textarea rows="2" maxlength="2000" value=${item.text} disabled=${busy} ...${helpFor("thread.comment")}
            onInput=${(event) => patchComment(index, { text: event.target.value })}></textarea></label>
          <label>${t("runs.human.comment.quote_optional")}<textarea rows="2" maxlength="2000" value=${item.quote} disabled=${busy}
            ...${helpFor("thread.comment")} onInput=${(event) => patchComment(index, { quote: event.target.value })}></textarea></label>
          <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("thread.comment")}
            onClick=${() => setComments(comments.filter((_, i) => i !== index))}>${t("runs.human.comment.remove")}</button>
        </fieldset>`)}
        <button type="button" class="btn secondary" disabled=${busy || comments.length >= 20} ...${helpFor("thread.comment")}
          onClick=${addComment}>${t("runs.human.comment.add")}</button><${Help} topic="thread.comment" compact=${true} />
      </div>`}
      ${error && html`<p class="message error" role="alert">${error}</p>`}
      <div class="thread-actions"><button type="button" class=${pending === "reject" ? "btn danger" : "btn"} disabled=${busy}
        ...${helpFor("thread.confirm")} onClick=${send}>${t("thread.decision.confirm_button", {
          action: t(pending === "approve" ? "thread.action.approve_infinitive" : pending === "changes" ? "thread.action.changes_infinitive" : "thread.action.reject_infinitive"),
        })}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("thread.confirm")}
          onClick=${() => { setPending(""); setError(""); }}>${t("thread.inbox.back")}</button><${Help} topic="thread.confirm" compact=${true} /></div>
    </div>`}
  </div>`;
}

// The inputs and the outputs a function is about to read, drawn as text before the person approves it.
const filled = (value) => value != null && typeof value === "object" && Object.keys(value).length > 0;
function FunctionContext({ entry }) {
  return html`<div class="thread-function">
    ${filled(entry.inputs) && html`<section><h5>${t("thread.function.inputs")}</h5><${ReadableValue} node=${readable(entry.inputs)} /></section>`}
    ${filled(entry.prior_outputs) && html`<section><h5>${t("thread.function.outputs")}</h5>
      <${ReadableValue} node=${readable(entry.prior_outputs)} /></section>`}</div>`;
}

export function ThreadCard({ entry, status, runId, token, reviewView, busy, onAction, limit = null, mode = "advanced" }) {
  const links = threadLinksFor(entry, mode);
  const details = detailHref(runId);
  const raw = showRawOutput(mode);
  // SIM-T03: in simple mode an object output is drawn with ReadableValue; the JSON text stays in advanced mode.
  const objectOutput = entry.kind === "final" && !entry.error && entry.output != null && typeof entry.output === "object";
  const output = entry.kind === "final" ? entry.error || (entry.output == null ? "" :
    typeof entry.output === "string" ? entry.output : raw ? JSON.stringify(entry.output, null, 2) : "") : "";
  const notice = entry.kind === "final" ? modelLimitNotice(limit) : null;   // EJC-T01: only what the run detail sent
  const failed = entry.kind === "final" && entry.status === "failed";
  const voice = entry.kind === "user_message" ? " thread-msg-user" : " thread-msg-agent";
  return html`<li class=${"thread-card thread-msg thread-" + entry.kind + voice + (failed ? " thread-failure" : "")}>
    <div class="thread-card-head"><strong>${t(LABELS[entry.kind] || "thread.card.event")}</strong>
      ${links.detail && html`<a href=${details} ...${helpFor("thread.detail")}>${t("thread.detail")}</a><${Help} topic="thread.detail" compact=${true} />`}</div>
    <p>${(failed && !threadEntryText(entry)) ? t("thread.failed") : threadEntryText(entry)}</p>
    ${entry.kind === "launched" && html`<p class="muted">${entry.workflow_id} · ${entry.project_name || t("common.value.no_project")}
      ${entry.execution_mode ? " · " + entry.execution_mode : ""}${entry.operating_mode ? " · " + entry.operating_mode : ""}</p>`}
    ${entry.kind === "launched" && entry.task_id && html`<p>${t("thread.related.task", { task_id: entry.task_id })}
      ${links.related && entry.task_href && html` · <a href=${entry.task_href} ...${helpFor("thread.related")}>${t("thread.related.plan")}</a>`}</p>`}
    ${entry.kind === "launched" && entry.team && html`<p>${t("thread.related.team", { team_id: entry.team.team_id || entry.team.bundle || t("thread.related.linked") })}
      ${links.related && entry.team_href && html` · <a href=${entry.team_href} ...${helpFor("thread.related")}>${t("thread.related.team_link")}</a>`}</p>`}
    ${entry.kind === "launched" && links.related && html`<${Help} topic="thread.related" compact=${true} />`}
    ${entry.kind === "step" && html`<p class="muted">${entry.step_id}${raw && entry.executor ? " · " + (entry.executor.model || entry.executor.function || entry.executor.provider || entry.executor.kind || "") : ""}</p>`}
    ${notice && html`<p class="thread-limit">${t(notice.context_cost ? "thread.model_limit" : notice.suggested_model ? "thread.model_limit_switch" : "thread.model_limit_only", notice)}</p>`}
    ${entry.kind === "waiting" && entry.reason === "function_review" && html`<${FunctionContext} entry=${entry} />`}
    ${entry.kind === "waiting" && entry.reason === "function_failure" && (entry.code || entry.detail) && html`<div class="thread-function-failure">
      ${entry.code && html`<p class="muted">${t("thread.function.code", { code: entry.code })}</p>`}
      ${entry.detail && html`<pre class="thread-output">${entry.detail}</pre>`}</div>`}
    ${entry.kind === "waiting" && status === "waiting_human" && entry.decision &&
      ["review", "content_review", "budget_exhausted"].includes(entry.reason) && reviewView && html`
        <${ReviewOutput} entry=${entry} view=${reviewView} runId=${runId} token=${token} mode=${mode} />`}
    ${entry.kind === "waiting" && status === "waiting_human" && entry.decision && html`<${Decision} entry=${entry} busy=${busy}
      onAction=${onAction} runId=${runId} token=${token} mode=${mode} />`}
    ${objectOutput && !raw && html`<div class="thread-final-readable"><${ReadableValue} node=${readable(entry.output)} /></div>`}
    ${output && html`<pre class="thread-output">${output}</pre>`}
  </li>`;
}
