// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Human actions of one run: review decisions, cancellation, the copy-and-paste step and external output.
// The server decides what is possible; every action answers with the updated run.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost, runPath } from "./api.js";
import { Help, helpFor } from "./help.js";
import { waitingReasonLabel } from "./runs-layout.js";
import { errorText } from "./text.js";
import { ExternalOutput } from "./external-output.js";
import { ToolApprovalPreview } from "./thread-cards.js";
import { t } from "./i18n.js";

// Pasted answers survive a rejection or a re-render; memory only, keyed by run, step and attempt.
const drafts = new Map();
function translatedWaitingReasonLabel(waiting) {
  if (!waiting) return waitingReasonLabel(waiting);
  return waitingReasonLabel(waiting, t);
}

function Message({ message }) {
  if (!message) return null;
  const role = message.kind === "error" ? "alert" : "status";
  return html`<p class=${"message " + message.kind} role=${role}>${message.text}</p>`;
}

export function Confirm({ title, children, confirmLabel, keepLabel, danger, busy, onConfirm, onKeep }) {
  const confirmButton = useRef(null);
  useEffect(() => { if (confirmButton.current) confirmButton.current.focus(); }, []);
  return html`<div class="confirm" role="group" aria-label=${title}>
    <p><strong>${title}</strong></p>
    ${children}
    <div class="actions">
      <button type="button" ref=${confirmButton} data-help-exempt="confirm" class=${danger ? "btn danger" : "btn"} disabled=${busy}
        onClick=${onConfirm}>${confirmLabel}</button>
      <button type="button" data-help-exempt="confirm" class="btn secondary" disabled=${busy} onClick=${onKeep}>${keepLabel}</button>
    </div>
  </div>`;
}

export function HumanActions({ token, view, onUpdate, onStale, actionBar = false }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [feedback, setFeedback] = useState("");
  const [comments, setComments] = useState([]);
  const [feedbackInvalid, setFeedbackInvalid] = useState(false);
  const [reason, setReason] = useState("");
  const cardRef = useRef(null);

  function focusActions() {
    if (!cardRef.current) return;
    let reduced = false;
    try { reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (_) { reduced = false; }
    cardRef.current.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
    const target = cardRef.current.querySelector("button, textarea, input, select");
    if (target) target.focus({ preventScroll: true });
  }

  // A new run (or a new state of it) starts without the previous message or open confirmation.
  const stateKey = [view.id, view.status, view.waiting && view.waiting.reason, view.waiting && view.waiting.step_id,
    view.waiting && view.waiting.attempt, view.cancel_requested].join("|");
  useEffect(() => { setConfirm(null); setFeedbackInvalid(false); }, [stateKey]);
  useEffect(() => { setMessage(null); setFeedback(""); setComments([]); setReason(""); }, [view.id]);

  function updateComment(index, patch) {
    setComments(comments.map((item, i) => i === index ? { ...item, ...patch } : item));
  }

  async function act(path, body, okText) {
    if (busy) return false;
    setBusy(true);
    setMessage({ kind: "info", text: t("runs.human.sending") });
    try {
      const updated = await apiPost(path, token, body);
      setConfirm(null);
      onUpdate(updated);
      setMessage({ kind: "ok", text: okText(updated) });
      return true;
    } catch (error) {
      const failure = error instanceof ApiError ? error : new ApiError({ message: String(error) });
      setMessage({ kind: "error", text: errorText(failure) });
      if (failure.status === 409 || failure.status === 404) onStale();
      return false;
    } finally {
      setBusy(false);
    }
  }

  const id = view.id;
  const waiting = view.waiting;
  const cancelBox = (running) => html`<${Confirm} title=${t("runs.human.cancel.title")} confirmLabel=${t("runs.human.cancel.confirm")}
    keepLabel=${t("runs.human.cancel.keep")} danger=${true} busy=${busy} onKeep=${() => setConfirm(null)}
    onConfirm=${() => act(runPath(id, "cancel"), {}, (v) => v.status === "cancelled"
      ? t("runs.human.cancel.done") : t("runs.human.cancel.requested"))}>
    <p class="muted">${running
      ? t("runs.human.cancel.running")
      : t("runs.human.cancel.irreversible")}</p>
  <//>`;
  const cancelControl = !view.terminal && !view.cancel_requested
    ? (confirm === "cancel"
      ? cancelBox(view.status === "running")
      : html`<div class="action-item"><button type="button" class="btn secondary" disabled=${busy} ...${helpFor("human.cancel")}
          onClick=${() => setConfirm("cancel")}>${t("runs.human.cancel.button")}</button>
        <${Help} topic="human.cancel" /></div>`)
    : null;
  // Rejecting a tool call fails the run without running the tool; rejecting a review ends it as rejected.
  const rejectControl = (toolCall) => (confirm === "reject"
    ? html`<${Confirm} title=${t("runs.human.reject.title")} confirmLabel=${t("runs.human.reject.confirm")} keepLabel=${t("runs.human.cancel.keep")} danger=${true}
        busy=${busy} onKeep=${() => setConfirm(null)}
        onConfirm=${() => act(runPath(id, "reject"), { feedback: reason.trim() }, () => toolCall
          ? t("runs.human.reject.tool_done") : t("runs.human.reject.done"))}>
        <p class="muted">${toolCall
          ? t("runs.human.reject.tool_warning")
          : t("runs.human.reject.warning")}</p>
        <label for="reject-reason">${t("runs.human.reject.reason_optional")}</label>
        <${Help} topic="human.reject_reason" />
        <textarea id="reject-reason" rows="2" maxlength="4000" value=${reason} ...${helpFor("human.reject_reason")}
          onInput=${(event) => setReason(event.target.value)}></textarea>
      <//>`
    : html`<div class="action-item"><button type="button" class="btn secondary" disabled=${busy} ...${helpFor("human.reject")}
        onClick=${() => setConfirm("reject")}>${t("runs.human.reject.button")}</button>
      <${Help} topic="human.reject" /></div>`);

  let body;
  if (view.terminal) {
    body = html`<p class="muted">${t("runs.human.terminal")}</p>`;
  } else if (waiting && (waiting.reason === "review" || waiting.reason === "approval_pending" || waiting.reason === "budget_exhausted" || waiting.reason === "")) {
    const cut = waiting.reason === "budget_exhausted" || view.counters.budget_exhausted;
    // The server says whether a correction is still possible (a cut by the run's own limits allows it).
    const exhausted = cut && view.corrections_allowed === false;
    body = html`<div>
      ${exhausted && html`<p class="notice">${t("runs.human.budget.exhausted")}</p>`}
      ${cut && !exhausted && html`<p class="notice">${t("runs.human.budget.correction_available")}</p>`}
      <p class="muted">${t("runs.human.review.instruction")}</p>
      <div class="action-item">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("human.approve")} onClick=${() => act(runPath(id, "approve"), {},
          (v) => t(v.status === "completed" ? "runs.human.approve.completed" : "runs.human.approve.continues"))}>${t("runs.human.approve.button")}</button>
        <${Help} topic="human.approve" />
      </div>
      ${!exhausted && html`<div class="action-item">
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("human.regenerate")}
          onClick=${() => act(runPath(id, "regenerate"), {}, () => t("runs.human.regenerate.done"))}>${t("runs.human.regenerate.button")}</button>
        <${Help} topic="human.regenerate" />
      </div>`}
      ${!exhausted && html`<form class="stack" noValidate onSubmit=${(event) => {
        event.preventDefault();
        if (!feedback.trim() && !comments.length) { setFeedbackInvalid(true); setMessage({ kind: "error", text: t("runs.human.changes.required") }); return; }
        if (comments.some((comment) => !comment.text.trim() || !comment.target.trim())) { setMessage({ kind: "error", text: t("runs.human.comment.required_fields") }); return; }
        setFeedbackInvalid(false);
        act(runPath(id, "changes"), { feedback: feedback.trim(), comments: comments.map(({ target, kind, text, quote }) => ({ target: target.trim(), kind, text: text.trim(), quote: quote || null })) },
          () => t("runs.human.changes.done"))
          .then((ok) => { if (ok) { setFeedback(""); setComments([]); } });
      }}>
        <label for="changes-feedback">${t("runs.human.changes.label")} <span class="muted">${t("runs.human.changes.detail")}</span></label>
        <${Help} topic="human.changes" />
        <textarea id="changes-feedback" rows="3" maxlength="4000" value=${feedback} aria-invalid=${feedbackInvalid ? "true" : null}
          disabled=${busy} ...${helpFor("human.changes")} onInput=${(event) => setFeedback(event.target.value)}></textarea>
        ${comments.map((comment, index) => {
          const output = view.artifacts && view.artifacts.length ? view.artifacts[view.artifacts.length - 1].data : null;
          const keys = output && typeof output === "object" && !Array.isArray(output) ? Object.keys(output) : [];
          const choices = ["general", ...keys];
          return html`<fieldset key=${index} class="stack"><legend>${t("runs.human.comment.legend", { count: index + 1 })}</legend>
            <label>${t("runs.human.comment.part")}</label><${Help} topic="human.comment_target" />
            <select aria-label=${t("runs.human.comment.part_aria", { count: index + 1 })} value=${choices.includes(comment.target) ? comment.target : "_manual"} disabled=${busy} ...${helpFor("human.comment_target")}
              onChange=${(event) => updateComment(index, { target: event.target.value === "_manual" ? "" : event.target.value })}>
              <option value="general">${t("runs.human.comment.general")}</option>${keys.map((key) => html`<option value=${key}>${key}</option>`)}<option value="_manual">${t("runs.human.comment.write_path")}</option>
            </select>
            ${!choices.includes(comment.target) && html`<input aria-label=${t("runs.human.comment.path_aria")} maxlength="200" value=${comment.target} disabled=${busy}
              ...${helpFor("human.comment_target")} onInput=${(event) => updateComment(index, { target: event.target.value })} />`}
            <label>${t("runs.human.comment.type")}</label><${Help} topic="human.comment_kind" />
            <select aria-label=${t("runs.human.comment.type_aria", { count: index + 1 })} value=${comment.kind} disabled=${busy} ...${helpFor("human.comment_kind")}
              onChange=${(event) => updateComment(index, { kind: event.target.value })}>
              <option value="fix">${t("runs.comment_kind.fix")}</option><option value="add">${t("runs.comment_kind.add")}</option><option value="remove">${t("runs.comment_kind.remove")}</option>
                <option value="rewrite">${t("runs.comment_kind.rewrite")}</option><option value="other">${t("runs.comment_kind.other")}</option>
            </select>
            <label>${t("runs.human.comment.text")}</label><${Help} topic="human.comment_text" />
            <textarea aria-label=${t("runs.human.comment.text_aria", { count: index + 1 })} rows="2" maxlength="2000" value=${comment.text} disabled=${busy} ...${helpFor("human.comment_text")}
              onInput=${(event) => updateComment(index, { text: event.target.value })}></textarea>
            <label>${t("runs.human.comment.quote_optional")}</label><${Help} topic="human.comment_quote" />
            <textarea aria-label=${t("runs.human.comment.quote_aria", { count: index + 1 })} rows="2" maxlength="2000" value=${comment.quote} disabled=${busy} ...${helpFor("human.comment_quote")}
              onInput=${(event) => updateComment(index, { quote: event.target.value })}></textarea>
            <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("human.comment_remove")}
              onClick=${() => setComments(comments.filter((_, i) => i !== index))}>${t("runs.human.comment.remove")}</button><${Help} topic="human.comment_remove" />
          </fieldset>`;
        })}
        <div class="action-item"><button type="button" class="btn secondary" disabled=${busy || comments.length >= 20}
          ...${helpFor("human.comment_add")} onClick=${() => setComments([...comments, { target: "general", kind: "fix", text: "", quote: "" }])}>${t("runs.human.comment.add")}</button>
          <${Help} topic="human.comment_add" /></div>
        <div class="actions"><button type="submit" class="btn secondary" disabled=${busy} ...${helpFor("human.changes")}>${t("runs.human.changes.button")}</button></div>
      </form>`}
      <div class="action-list">
        ${rejectControl(false)}
        ${cancelControl}
      </div>
    </div>`;
  } else if (waiting && waiting.reason === "tool_approval") {
    const call = view.tool_approval;
    body = call
      ? html`<div>
          <p class="muted">${t("runs.human.tool.request", { step_id: call.step_id, tool_id: call.tool_id })}</p>
          ${call.tool_id === "file.write" && html`<${ToolApprovalPreview} runId=${id} token=${token}
            permissionRequestId=${call.permission_request_id} />`}
          <dl class="tool-approval">
            <dt>${t("runs.human.tool.summary")}</dt><dd>${call.summary || t("runs.human.tool.no_summary")}</dd>
            <dt>${t("runs.human.tool.effects")}</dt><dd>${call.impacts.length ? call.impacts.join(", ") : t("runs.human.tool.no_effects")}</dd>
            <dt>${t("runs.human.tool.actions")}</dt><dd>${call.actions.length
              ? html`<ul>${call.actions.map((item, index) => html`<li key=${index}>${item.kind}${item.target ? ": " + item.target : ""}</li>`)}</ul>`
              : t("runs.human.tool.no_actions")}${call.actions_omitted && html` <span class="muted">${call.actions_omitted}</span>`}</dd>
          </dl>
          <div class="action-item">
            <button type="button" class="btn" disabled=${busy} ...${helpFor("human.tool_approve")}
              onClick=${() => act(runPath(id, "approve"), { permission_request_id: call.permission_request_id },
                (v) => t(v.status === "completed" ? "runs.human.tool.approved_completed" : "runs.human.tool.approved_continues"))}>${t("runs.human.tool.approve")}</button>
            <${Help} topic="human.tool_approve" />
          </div>
          <div class="action-list">
            ${rejectControl(true)}
            ${cancelControl}
          </div>
        </div>`
      : html`<div><p class="muted">${t("runs.human.tool.missing_request")}</p>
          <div class="action-list">${cancelControl}</div></div>`;
  } else if (waiting && view.pending_human) {
    body = html`<div>
      <${HumanStep} token=${token} view=${view} busy=${busy} act=${act} setMessage=${setMessage} onStale=${onStale} />
      <div class="action-list">${cancelControl}</div>
    </div>`;
  } else if (waiting) {
    body = html`<div><p class="muted">${t("runs.human.unknown_waiting", { reason: waiting.reason })}</p>
      <div class="action-list">${cancelControl}</div></div>`;
  } else {
    body = html`<div>
      <p class="muted">${view.cancel_requested
        ? t("runs.human.cancel.requested")
        : t("runs.human.no_pending_action")}</p>
      <div class="action-list">${cancelControl}</div>
    </div>`;
  }

  const isWaiting = Boolean(waiting && !view.terminal);
  const waitLabel = isWaiting ? translatedWaitingReasonLabel(waiting) : "";
  const isReviewLike = isWaiting && (waiting.reason === "review" || waiting.reason === "approval_pending" || waiting.reason === "budget_exhausted" || waiting.reason === "");
  const isToolApproval = isWaiting && waiting.reason === "tool_approval" && Boolean(view.tool_approval);

  return html`<section ref=${cardRef} class=${"panel" + (isWaiting ? " human-actions-waiting" : "")} aria-labelledby="human-actions-title">
    <h4 id="human-actions-title">${t("runs.human.title")}</h4>
    <${Help} topic="human.section" />
    ${isWaiting && html`<p class="human-waiting-reason"><strong>${t("runs.human.waiting_reason")}</strong> ${waitLabel}</p>`}
    ${body}
    ${waiting && ["review", "execute_step", "research"].includes(waiting.reason) && !view.cancel_requested && html`<${ExternalOutput}
      key=${view.id} token=${token} view=${view} onUpdate=${onUpdate} onStale=${onStale} />`}
    <${Message} message=${message} />
    ${isWaiting && actionBar && html`<div class="human-action-bar" role="region" aria-label=${t("runs.human.pending_action")}>
      <p class="human-bar-reason"><strong>${waitLabel}</strong></p>
      <div class="human-bar-actions">
        ${isReviewLike && html`<button type="button" class="btn" disabled=${busy} ...${helpFor("human.approve")}
          onClick=${() => act(runPath(id, "approve"), {},
            (v) => t(v.status === "completed" ? "runs.human.approve.completed" : "runs.human.approve.continues"))}>${t("runs.human.approve.button")}</button>`}
        ${isToolApproval && html`<button type="button" class="btn" disabled=${busy} ...${helpFor("human.tool_approve")}
          onClick=${() => act(runPath(id, "approve"), { permission_request_id: view.tool_approval.permission_request_id },
            (v) => t(v.status === "completed" ? "runs.human.tool.approved_completed" : "runs.human.tool.approved_continues"))}>${t("runs.human.tool.approve_short")}</button>`}
        <button type="button" class="btn secondary" ...${helpFor("human.bar_focus")} onClick=${focusActions}>
          ${t(isReviewLike || isToolApproval ? "runs.human.more_actions" : "runs.human.go_to_action")}
        </button>
        <${Help} topic="human.bar_focus" compact=${true} />
      </div>
    </div>`}
  </section>`;
}

function HumanStep({ token, view, busy, act, setMessage, onStale }) {
  const pendingKey = view.pending_human.step_id + "|" + view.pending_human.attempt;
  const draftKey = view.id + "|" + pendingKey;
  const [pending, setPending] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [draft, setDraft] = useState(() => drafts.get(draftKey) || { response: "", provider: "human", model: "", prompt_used: "", notes: "" });
  const [invalid, setInvalid] = useState(false);
  const [check, setCheck] = useState(null);
  const promptBox = useRef(null);

  useEffect(() => {
    let current = true;
    setPending(null);
    setLoadError(null);
    setDraft(drafts.get(draftKey) || { response: "", provider: "human", model: "", prompt_used: "", notes: "" });
    setCheck(null);
    apiGet(runPath(view.id, "human-next"), token).then((result) => {
      if (current) setPending(result);
    }).catch((error) => {
      if (!current) return;
      setLoadError(error);
      if (error.status === 409) onStale();
    });
    return () => { current = false; };
  }, [draftKey, token]);

  function update(patch) {
    const next = { ...draft, ...patch };
    drafts.set(draftKey, next);
    setDraft(next);
    if ("response" in patch) setCheck(null);
  }

  async function copyPrompt() {
    try {
      await navigator.clipboard.writeText(pending.prompt);
      setMessage({ kind: "ok", text: t("runs.human.prompt.copied") });
    } catch (_) {
      if (promptBox.current) { promptBox.current.focus(); promptBox.current.select(); }
      setMessage({ kind: "info", text: t("runs.human.prompt.copy_blocked") });
    }
  }

  if (loadError) {
    return html`<p class="message error" role="alert">${t("runs.human.pending.load_error", { message: loadError.message })}</p>`;
  }
  if (!pending) return html`<p class="muted" aria-busy="true">${t("runs.human.pending.loading")}</p>`;

  const submit = async (event) => {
    event.preventDefault();
    if (!draft.response.trim()) {
      setInvalid(true);
      setMessage({ kind: "error", text: t("runs.human.response.required") });
      return;
    }
    setInvalid(false);
    const ok = await act(runPath(view.id, "human-submit"), {
      raw_response: draft.response, provider: draft.provider.trim() || "human", model: draft.model.trim(),
      prompt_used: draft.prompt_used, notes: draft.notes.trim(),
    }, (v) => t(v.status === "waiting_human" ? "runs.human.response.accepted_review" : "runs.human.response.accepted_continues"));
    if (ok) drafts.delete(draftKey);
  };

  const validate = async () => {
    if (!draft.response.trim()) {
      setInvalid(true);
      setMessage({ kind: "error", text: t("runs.human.response.required_check") });
      return;
    }
    setInvalid(false);
    try {
      const result = await apiPost(runPath(view.id, "human-check"), token, { raw_response: draft.response });
      setCheck(result);
    } catch (error) {
      setMessage({ kind: "error", text: errorText(error) });
      if (error.status === 409) onStale();
    }
  };

  return html`<div class="stack">
    <p>${t("runs.human.pending.description", { step_id: pending.step_id, attempt: pending.attempt })} <code>${pending.expected_contract}</code>.</p>
    <label for="human-prompt">${t("runs.human.prompt.label")} <span class="muted">${t("runs.human.prompt.version", {
      prompt_id: pending.prompt_id, version: pending.prompt_version,
    })}</span></label>
    <${Help} topic="human.prompt" />
    <textarea id="human-prompt" ref=${promptBox} rows="10" readOnly value=${pending.prompt} ...${helpFor("human.prompt")}></textarea>
    <div class="actions"><button type="button" class="btn secondary" ...${helpFor("human.copy_prompt")} onClick=${copyPrompt}>${t("runs.human.prompt.copy")}</button>
      <${Help} topic="human.copy_prompt" compact=${true} /></div>
    <form class="stack" noValidate onSubmit=${submit}>
      <label for="human-response">${t("runs.human.response.label")} <span class="req">${t("runs.human.required")}</span></label>
      <${Help} topic="human.response" />
      <textarea id="human-response" rows="10" value=${draft.response} disabled=${busy}
        aria-invalid=${invalid ? "true" : null} ...${helpFor("human.response")}
        onInput=${(event) => update({ response: event.target.value })}></textarea>
      <div class="two">
        <div><label for="human-provider">${t("runs.human.provider")}</label>
          <${Help} topic="human.provider" />
          <input id="human-provider" maxlength="120" autocomplete="off" value=${draft.provider} disabled=${busy}
            ...${helpFor("human.provider")} onInput=${(event) => update({ provider: event.target.value })} /></div>
        <div><label for="human-model">${t("runs.human.model")} <span class="muted">${t("runs.human.optional")}</span></label>
          <${Help} topic="human.model" />
          <input id="human-model" maxlength="200" autocomplete="off" value=${draft.model} disabled=${busy}
            ...${helpFor("human.model")} onInput=${(event) => update({ model: event.target.value })} /></div>
      </div>
      <label for="human-prompt-used">${t("runs.human.prompt.used")} <span class="muted">${t("runs.human.optional")}</span></label>
      <${Help} topic="human.prompt_used" />
      <textarea id="human-prompt-used" rows="3" maxlength="50000" value=${draft.prompt_used} disabled=${busy}
        ...${helpFor("human.prompt_used")} onInput=${(event) => update({ prompt_used: event.target.value })}></textarea>
      <label for="human-notes">${t("runs.human.notes")} <span class="muted">${t("runs.human.optional")}</span></label>
      <${Help} topic="human.notes" />
      <textarea id="human-notes" rows="3" maxlength="4000" value=${draft.notes} disabled=${busy}
        ...${helpFor("human.notes")} onInput=${(event) => update({ notes: event.target.value })}></textarea>
      <div class="action-item"><button type="button" class="btn secondary" disabled=${busy}
        ...${helpFor("human.check")} onClick=${validate}>${t("runs.human.check")}</button>
        <${Help} topic="human.check" /></div>
      ${check && html`<div role="status" aria-live="polite">
        ${check.ok ? t("runs.human.response.valid") : html`<ul>${check.problems.map((problem) => html`<li><code>${problem.path}</code>: ${problem.message}</li>`)}</ul>`}
      </div>`}
      <div class="action-item"><button type="submit" class="btn" disabled=${busy} ...${helpFor("human.submit")}>${t("runs.human.response.send")}</button>
        <${Help} topic="human.submit" /></div>
    </form>
  </div>`;
}
