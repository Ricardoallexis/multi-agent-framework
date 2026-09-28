// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
//
// Human actions for the selected run (UI0-T07): review decisions, cancellation, and the Human Bridge.
// The API is the only authority: controls follow the run's status and waiting reason, every action
// refreshes from the server response, and nothing is stored outside this page's memory.
//
// Events on `document`:
//   listens `run:selected` (detail: run) and `run:updated` (detail: run, e.g. from the run view's polling);
//   emits   `run:updated` (detail: run) after each successful action or refresh.
import { ApiError, apiGet, apiPost } from "./api.js";

const TERMINAL = new Set(["completed", "failed", "cancelled", "rejected"]);
const CANCELLABLE = new Set(["queued", "running", "waiting_human", "paused", "interrupted"]);
const BRIDGE_REASONS = new Set(["execute_step", "research"]);

function element(tag, attributes = {}, text = "") {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === "className") node.className = value;
    else if (name.startsWith("on") && typeof value === "function") node.addEventListener(name.slice(2), value);
    else if (value !== false && value !== undefined && value !== null) node.setAttribute(name, value === true ? "" : value);
  }
  if (text) node.textContent = text;
  return node;
}

function runPath(runId, action = "") {
  return `/runs/${encodeURIComponent(runId)}${action ? `/${action}` : ""}`;
}

function errorText(error) {
  if (!(error instanceof ApiError)) return String(error);
  const lines = [`${error.code}: ${error.message}`];
  for (const item of error.details?.errors || []) if (item?.msg) lines.push(item.msg);
  for (const issue of error.details?.issues || []) if (issue?.message) lines.push(issue.message);
  return lines.join("\n");
}

export function mount(container, context = {}) {
  const title = container.querySelector("#human-actions-title") || element("h2", { id: "human-actions-title" }, "Human actions");
  const status = element("p", { className: "muted", role: "status", "aria-live": "polite", tabindex: "-1" });
  const content = element("div");
  container.replaceChildren(title, status, content);

  let run = null;
  let inFlight = false;
  let renderedKey = "";
  // Human Bridge drafts survive re-renders and errors, keyed by run, step, and attempt. Memory only.
  const drafts = new Map();

  function announce(kind, message) {
    status.className = kind === "error" ? "error" : kind === "success" ? "success" : "muted";
    status.setAttribute("role", kind === "error" ? "alert" : "status");
    status.textContent = message;
  }

  function stateKey(value) {
    if (!value) return "";
    return [value.id, value.status, value.waiting_reason, value.waiting_step, value.waiting_attempt, value.cancel_requested].join("|");
  }

  function publish(updated) {
    run = updated;
    context.run = updated;
    document.dispatchEvent(new CustomEvent("run:updated", { detail: updated }));
  }

  async function refresh(message) {
    if (!run) return;
    try {
      publish(await apiGet(runPath(run.id)));
      render(); // redraw only if the state changed, so drafts and focus survive an idle refresh
      if (message) announce("", message);
    } catch (error) {
      announce("error", `Could not refresh the run. ${errorText(error)}`);
    }
  }

  function setBusy(busy) {
    inFlight = busy;
    for (const control of content.querySelectorAll("button, textarea, input")) {
      if (control.dataset.keepEnabled !== "true") control.disabled = busy;
    }
    content.toggleAttribute("aria-busy", busy);
  }

  // One action at a time per run; the server response is authoritative. No automatic retries.
  async function act(label, path, body, successMessage) {
    if (inFlight || !run) return false;
    setBusy(true);
    announce("", `${label}…`);
    try {
      const updated = await apiPost(path, body);
      publish(updated);
      render(true);
      announce("success", successMessage(updated));
      status.focus();
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await refresh();
        announce("error", `${errorText(error)}\nThe run changed; the controls now show its current state.`);
      } else if (error instanceof ApiError && error.status === 422) {
        announce("error", errorText(error));
      } else {
        await refresh();
        announce("error", `${errorText(error)}\nThe result is unknown; the run was refreshed. Check it before trying again.`);
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  // Destructive actions ask for an explicit confirmation step, with focus moved into it and back.
  function confirmable(label, confirmLabel, explanation, onConfirm) {
    const wrapper = element("div");
    const trigger = element("button", { type: "button", className: "secondary" }, label);
    const panel = element("div", { className: "notice", hidden: true, role: "group", "aria-label": confirmLabel });
    const text = element("p", {}, explanation);
    const confirm = element("button", { type: "button" }, confirmLabel);
    const keep = element("button", { type: "button", className: "secondary" }, "Keep the run");
    panel.append(text, element("div", { className: "form-actions" }));
    panel.lastChild.append(confirm, keep);
    trigger.addEventListener("click", () => { panel.hidden = false; trigger.hidden = true; confirm.focus(); });
    keep.addEventListener("click", () => { panel.hidden = true; trigger.hidden = false; trigger.focus(); });
    confirm.addEventListener("click", onConfirm);
    wrapper.append(trigger, panel);
    return wrapper;
  }

  function cancelControl() {
    const explanation = run.status === "running"
      ? "Cancelling a running run asks the worker to stop; it becomes cancelled when the worker notices."
      : "Cancelling stops this run. This cannot be undone.";
    return confirmable("Cancel run…", "Confirm cancellation", explanation, () =>
      act("Cancelling", runPath(run.id, "cancel"), undefined, (updated) =>
        updated.status === "cancelled" ? "Run cancelled." : "Cancellation requested; waiting for the worker."));
  }

  function renderReview(budgetExhausted) {
    const box = element("div");
    box.append(element("p", {}, `Step "${run.waiting_step || "?"}" (attempt ${run.waiting_attempt ?? "?"}) is waiting for review. Inspect its artifact in the run view before deciding.`));
    if (budgetExhausted) {
      box.append(element("p", { className: "notice" }, "The run's budget is exhausted: you can approve the existing result, reject it, or cancel. Changes and regeneration are not available."));
    }
    const actions = element("div", { className: "form-actions" });
    actions.append(element("button", {
      type: "button",
      onclick: () => act("Approving", runPath(run.id, "approve"), undefined, (updated) => `Approved. The run is now ${updated.status}.`),
    }, "Approve"));
    if (!budgetExhausted) {
      actions.append(element("button", {
        type: "button",
        className: "secondary",
        onclick: () => act("Regenerating", runPath(run.id, "regenerate"), undefined, (updated) => `Regeneration requested. The run is now ${updated.status}.`),
      }, "Regenerate"));
    }
    box.append(actions);

    if (!budgetExhausted) {
      const form = element("form", { className: "field" });
      const label = element("label", { for: "review-feedback" }, "Requested changes");
      const help = element("p", { className: "field-help", id: "review-feedback-help" }, "Describe what should change. Required to request changes.");
      const feedback = element("textarea", { id: "review-feedback", rows: "4", maxlength: "4000", "aria-describedby": "review-feedback-help" });
      const send = element("button", { type: "submit", className: "secondary" }, "Request changes");
      form.append(label, help, feedback, element("div", { className: "form-actions" }));
      form.lastChild.append(send);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!feedback.value.trim()) {
          feedback.setAttribute("aria-invalid", "true");
          announce("error", "Write the requested changes before sending them.");
          feedback.focus();
          return;
        }
        feedback.removeAttribute("aria-invalid");
        await act("Sending requested changes", runPath(run.id, "changes"), { feedback: feedback.value.trim() },
          (updated) => `Changes requested. The run is now ${updated.status}.`);
      });
      box.append(form);
    }

    const rejectFeedback = element("textarea", { id: "reject-feedback", rows: "2", maxlength: "4000" });
    const reject = confirmable("Reject run…", "Confirm rejection", "Rejecting ends the run with status rejected. This cannot be undone.", () =>
      act("Rejecting", runPath(run.id, "reject"), { feedback: rejectFeedback.value.trim() }, () => "Run rejected."));
    const rejectPanel = reject.querySelector(".notice");
    rejectPanel.insertBefore(element("label", { for: "reject-feedback" }, "Reason (optional)"), rejectPanel.lastChild);
    rejectPanel.insertBefore(rejectFeedback, rejectPanel.lastChild);
    box.append(reject, cancelControl());
    return box;
  }

  function renderBridge() {
    const box = element("div", { "aria-busy": "true" });
    box.append(element("p", { className: "muted" }, "Loading the pending human-guided step…"));
    const runId = run.id;
    apiGet(runPath(runId, "human-next")).then((pending) => {
      if (!run || run.id !== runId) return;
      box.replaceChildren(bridgeForm(pending), cancelControl());
      box.removeAttribute("aria-busy");
    }).catch(async (error) => {
      box.removeAttribute("aria-busy");
      if (error instanceof ApiError && error.code === "human_step_unavailable") {
        await refresh("There is no pending human-guided step any more; the run was refreshed.");
      } else {
        box.replaceChildren(element("p", { className: "error", role: "alert" }, `Could not load the pending step. ${errorText(error)}`), cancelControl());
      }
    });
    return box;
  }

  function bridgeForm(pending) {
    const draftKey = `${pending.run_id}|${pending.step_id}|${pending.attempt}`;
    const form = element("form");
    form.append(element("p", {}, `Step "${pending.step_id}" (attempt ${pending.attempt}) must be executed outside the framework. Its answer must satisfy the contract ${pending.expected_contract}.`));

    const prompt = element("textarea", { id: "bridge-prompt", rows: "10", readonly: true, "aria-describedby": "bridge-prompt-help" });
    prompt.value = pending.prompt || "";
    const copyStatus = element("span", { role: "status", "aria-live": "polite", className: "muted" });
    const copy = element("button", { type: "button", className: "secondary", "data-keep-enabled": "true" }, "Copy prompt");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(prompt.value);
        copyStatus.textContent = "Prompt copied.";
      } catch {
        prompt.focus();
        prompt.select();
        copyStatus.textContent = "Copy was blocked; the prompt is selected, press Ctrl+C.";
      }
    });
    const promptField = element("div", { className: "field" });
    promptField.append(
      element("label", { for: "bridge-prompt" }, "Prompt"),
      element("p", { className: "field-help", id: "bridge-prompt-help" }, `Prompt ${pending.prompt_id} v${pending.prompt_version}. Paste it into the model or tool of your choice.`),
      prompt,
      element("div", { className: "form-actions" }),
    );
    promptField.lastChild.append(copy, copyStatus);

    const response = element("textarea", { id: "bridge-response", rows: "10", required: true, "aria-describedby": "bridge-response-help" });
    response.value = drafts.get(draftKey) || "";
    response.addEventListener("input", () => drafts.set(draftKey, response.value));
    const responseField = element("div", { className: "field" });
    responseField.append(
      element("label", { for: "bridge-response" }, "Response"),
      element("p", { className: "field-help", id: "bridge-response-help" }, "Paste the complete answer. The server validates it; it stays here if validation fails."),
      response,
    );

    const provider = element("input", { id: "bridge-provider", maxlength: "120", value: "human", autocomplete: "off" });
    const model = element("input", { id: "bridge-model", maxlength: "200", autocomplete: "off" });
    const meta = element("div", { className: "catalog-controls" });
    for (const [id, labelText, control] of [["bridge-provider", "Provider", provider], ["bridge-model", "Model (optional)", model]]) {
      const wrapper = element("div", { className: "field" });
      wrapper.append(element("label", { for: id }, labelText), control);
      meta.append(wrapper);
    }

    const submit = element("button", { type: "submit" }, "Submit response");
    form.append(promptField, responseField, meta, element("div", { className: "form-actions" }));
    form.lastChild.append(submit);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!response.value.trim()) {
        response.setAttribute("aria-invalid", "true");
        announce("error", "Paste the response before submitting it.");
        response.focus();
        return;
      }
      response.removeAttribute("aria-invalid");
      const body = { raw_response: response.value, provider: provider.value.trim() || "human", model: model.value.trim() };
      if (inFlight) return;
      setBusy(true);
      announce("", "Submitting the response…");
      let focusTarget = null; // focus only after the controls are enabled again
      try {
        const updated = await apiPost(runPath(pending.run_id, "human-submit"), body);
        drafts.delete(draftKey);
        publish(updated);
        render(true);
        announce("success", `Response accepted. The run is now ${updated.status}.`);
        status.focus();
      } catch (error) {
        if (error instanceof ApiError && error.code === "human_submission_invalid") {
          response.setAttribute("aria-invalid", "true");
          announce("error", `The response was rejected: ${error.message}\nCorrect it and submit again.`);
          focusTarget = response;
        } else if (error instanceof ApiError && error.status === 409) {
          await refresh();
          announce("error", `${errorText(error)}\nThe pending step changed; the run was refreshed.`);
        } else {
          announce("error", `${errorText(error)}\nThe result is unknown. Refresh before submitting again.`);
        }
      } finally {
        setBusy(false);
        focusTarget?.focus();
      }
    });
    return form;
  }

  function render(force = false) {
    const key = stateKey(run);
    if (!force && key === renderedKey) return; // polling without a state change must not reset the controls
    renderedKey = key;
    if (!run) {
      content.replaceChildren(element("p", { className: "muted" }, "Select or create a run to see its pending human action."));
      return;
    }
    const refreshButton = element("button", {
      type: "button",
      className: "secondary",
      onclick: () => refresh("Run refreshed."),
    }, "Refresh");
    const heading = element("p", {}, `Run ${run.id}: ${run.status}${run.waiting_reason ? ` (${run.waiting_reason})` : ""}.`);
    const blocks = [heading];

    if (run.status === "waiting_human" && run.waiting_reason === "review") {
      blocks.push(renderReview(false));
    } else if (run.status === "waiting_human" && run.waiting_reason === "budget_exhausted") {
      blocks.push(renderReview(true));
    } else if (run.status === "waiting_human" && BRIDGE_REASONS.has(run.waiting_reason)) {
      blocks.push(renderBridge());
    } else if (TERMINAL.has(run.status)) {
      blocks.push(element("p", { className: "muted" }, "This run has finished; there is no human action."));
    } else {
      blocks.push(element("p", { className: "muted" }, run.cancel_requested
        ? "Cancellation was requested; waiting for the worker."
        : "No human action is pending. This view updates when the run changes."));
      if (CANCELLABLE.has(run.status) && !run.cancel_requested) blocks.push(cancelControl());
    }
    blocks.push(element("div", { className: "form-actions" }));
    blocks[blocks.length - 1].append(refreshButton);
    content.replaceChildren(...blocks);
  }

  document.addEventListener("run:selected", (event) => {
    if (!event.detail?.id) return;
    run = event.detail;
    render(true);
    refresh();
  });
  document.addEventListener("run:updated", (event) => {
    // Ignore our own publications (same object) and updates for other runs or during an action.
    if (!event.detail?.id || !run || event.detail === run || event.detail.id !== run.id || inFlight) return;
    run = event.detail;
    render();
  });

  if (context.run?.id) {
    run = context.run;
    refresh();
  }
  render(true);
}
