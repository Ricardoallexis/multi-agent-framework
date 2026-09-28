// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import {
  ApiError,
  createRun,
  getBundles,
  getHealth,
  getWorkflow,
  getWorkflows,
} from "./api.js";

const RESERVED_INPUTS = new Set(["previous_output", "revision_feedback"]);
const INPUT_NAME = /^[A-Za-z][A-Za-z0-9_]*$/;

function element(tag, attributes = {}, text = "") {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === "className") node.className = value;
    else if (name.startsWith("on") && typeof value === "function") node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  if (text) node.textContent = text;
  return node;
}

function setMessage(node, kind, message) {
  node.replaceChildren();
  node.className = kind || "";
  node.setAttribute("role", kind === "error" ? "alert" : "status");
  node.textContent = message;
}

function validationMessages(error) {
  const details = error.details || {};
  const messages = [];
  for (const item of details.errors || []) {
    if (item?.msg) messages.push(item.msg);
  }
  for (const issue of details.issues || []) {
    if (issue?.message) messages.push(issue.message);
  }
  if (details.workflows) {
    for (const workflow of details.workflows) {
      for (const issue of workflow?.issues || []) {
        if (issue?.message) messages.push(issue.message);
      }
    }
  }
  return [...new Set(messages)];
}

function presentError(error) {
  if (error instanceof ApiError) {
    const issues = validationMessages(error);
    return {
      summary: `${error.message} (${error.code})`,
      issues,
    };
  }
  return {
    summary: "The request could not be completed. Your entries have been kept.",
    issues: [],
  };
}

function appendError(container, error) {
  const { summary, issues } = presentError(error);
  const box = element("div", { className: "error", role: "alert" });
  box.append(element("p", {}, summary));
  if (issues.length) {
    const list = element("ul", { className: "validation-list" });
    for (const issue of issues) list.append(element("li", {}, issue));
    box.append(list);
  }
  container.replaceChildren(box);
}

function readBundle(selection) {
  return selection.value || "";
}

function addInputRow(rows, initial = {}) {
  const row = element("div", { className: "input-row" });
  const nameId = `input-name-${crypto.randomUUID()}`;
  const valueId = `input-value-${crypto.randomUUID()}`;
  const nameField = element("div", { className: "field" });
  const nameLabel = element("label", { for: nameId }, "Input name");
  const nameInput = element("input", { id: nameId, name: "input-name", autocomplete: "off" });
  nameInput.value = initial.name || "";
  nameInput.setAttribute("aria-describedby", `${nameId}-help`);
  nameField.append(nameLabel, nameInput, element("p", { id: `${nameId}-help`, className: "field-help" }, "Use a letter followed by letters, numbers, or underscores."));

  const valueField = element("div", { className: "field" });
  const valueLabel = element("label", { for: valueId }, "Value");
  const valueInput = element("input", { id: valueId, name: "input-value" });
  valueInput.value = initial.value || "";
  valueField.append(valueLabel, valueInput);

  const remove = element("button", { type: "button", className: "secondary remove-input", "aria-label": "Remove input" }, "Remove");
  remove.addEventListener("click", () => {
    row.remove();
    if (!rows.querySelector(".input-row")) addInputRow(rows);
  });
  row.append(nameField, valueField, remove);
  rows.append(row);
}

function readInputs(rows) {
  const inputs = Object.create(null);
  const errors = [];
  for (const row of rows.querySelectorAll(".input-row")) {
    const name = row.querySelector('[name="input-name"]').value.trim();
    const value = row.querySelector('[name="input-value"]').value;
    if (!name && !value) continue;
    if (!name) {
      errors.push("Every non-empty input row needs a name.");
      continue;
    }
    if (!INPUT_NAME.test(name)) {
      errors.push(`Input name "${name}" must start with a letter and contain only letters, numbers, or underscores.`);
      continue;
    }
    if (RESERVED_INPUTS.has(name) || name.endsWith("_output")) {
      errors.push(`Input name "${name}" is reserved by the runtime.`);
      continue;
    }
    if (Object.hasOwn(inputs, name)) {
      errors.push(`Input name "${name}" is duplicated.`);
      continue;
    }
    inputs[name] = value;
  }
  return { inputs, errors };
}

function field(root, labelText, id, control, helpText = "") {
  const wrapper = element("div", { className: "field" });
  wrapper.append(element("label", { for: id }, labelText));
  control.id = id;
  wrapper.append(control);
  if (helpText) wrapper.append(element("p", { className: "field-help" }, helpText));
  root.append(wrapper);
  return control;
}

export function mount(container, context = {}) {
  const mode = document.querySelector("#server-mode");
  const feedback = element("div", { "aria-live": "polite" });
  const formArea = element("div");
  const title = element("h2", { id: "catalog-title" }, "Create a run");
  container.replaceChildren(title, feedback, formArea);

  let selectedWorkflow = null;
  let submissionInFlight = false;
  let idempotencyPayload = null;
  let idempotencyKey = null;

  async function renderForm(bundles, workflows) {
    feedback.replaceChildren();
    formArea.replaceChildren();

    const form = element("form");
    const catalogControls = element("div", { className: "catalog-controls" });
    const bundleSelect = element("select", { "aria-label": "Workflow bundle" });
    bundleSelect.append(element("option", { value: "" }, "Built-in workflows"));
    for (const bundle of bundles) {
      bundleSelect.append(element("option", { value: bundle.name }, `Bundle: ${bundle.name}`));
    }
    field(catalogControls, "Workflow source", "bundle-select", bundleSelect);

    const workflowSelect = element("select", { required: "required" });
    workflowSelect.append(element("option", { value: "" }, "Choose a workflow"));
    for (const workflow of workflows) {
      workflowSelect.append(element("option", { value: workflow.id }, workflow.id));
    }
    field(catalogControls, "Workflow", "workflow-select", workflowSelect);

    const workflowInfo = element("div", { className: "workflow-details", "aria-live": "polite" });
    const projectName = field(form, "Project name", "project-name", element("input", {
      name: "project_name",
      required: "required",
      minlength: "2",
      maxlength: "120",
      autocomplete: "off",
    }));

    const inputHeading = element("h3", {}, "Workflow inputs");
    const inputHelp = element("p", { className: "field-help" }, "Provide text values as key/value pairs. The API validates workflow-specific inputs.");
    const inputRows = element("div");
    const addInput = element("button", { type: "button", className: "secondary" }, "Add input");
    addInput.addEventListener("click", () => addInputRow(inputRows));
    addInputRow(inputRows);

    const modeSelect = element("select");
    for (const value of ["auto", "local", "cloud", "human_guided"]) {
      modeSelect.append(element("option", { value }, value));
    }
    field(form, "Execution mode", "execution-mode", modeSelect);

    const realModeConfirmation = element("label", { className: "notice", for: "real-mode-confirm" });
    const confirmInput = element("input", { id: "real-mode-confirm", type: "checkbox" });
    realModeConfirmation.prepend(confirmInput, document.createTextNode(" I understand this server is not in Mock mode; allow this run to use its configured execution mode."));
    realModeConfirmation.hidden = true;
    form.append(realModeConfirmation);

    const actions = element("div", { className: "form-actions" });
    const submit = element("button", { type: "submit" }, "Create run");
    actions.append(submit);
    form.append(catalogControls, workflowInfo, inputHeading, inputHelp, inputRows, addInput, actions);
    formArea.append(form);

    let workflowRequest = 0;
    let detailRequest = 0;

    async function refreshWorkflows() {
      const requestId = ++workflowRequest;
      detailRequest += 1;
      selectedWorkflow = null;
      feedback.replaceChildren();
      formArea.setAttribute("aria-busy", "true");
      workflowSelect.replaceChildren(element("option", { value: "" }, "Loading workflows…"));
      workflowInfo.replaceChildren();
      workflowInfo.removeAttribute("aria-busy");
      try {
        const items = await getWorkflows(readBundle(bundleSelect));
        if (requestId !== workflowRequest) return;
        workflowSelect.replaceChildren(element("option", { value: "" }, "Choose a workflow"));
        for (const workflow of items) {
          workflowSelect.append(element("option", { value: workflow.id }, workflow.id));
        }
        if (!items.length) setMessage(feedback, "notice", "No workflows are available for this selection.");
      } catch (error) {
        if (requestId !== workflowRequest) return;
        appendError(feedback, error);
        workflowSelect.replaceChildren(element("option", { value: "" }, "Catalog unavailable"));
      } finally {
        if (requestId === workflowRequest) formArea.removeAttribute("aria-busy");
      }
    }

    bundleSelect.addEventListener("change", refreshWorkflows);
    workflowSelect.addEventListener("change", async () => {
      const requestId = ++detailRequest;
      selectedWorkflow = null;
      workflowInfo.replaceChildren();
      workflowInfo.removeAttribute("aria-busy");
      if (!workflowSelect.value) return;
      workflowInfo.setAttribute("aria-busy", "true");
      try {
        const workflow = await getWorkflow(workflowSelect.value, readBundle(bundleSelect));
        if (requestId !== detailRequest) return;
        selectedWorkflow = workflow;
        const heading = element("strong", {}, selectedWorkflow.id);
        const steps = element("p", { className: "muted" }, `${(selectedWorkflow.steps || []).length} workflow steps. Input fields follow the key/value editor below.`);
        workflowInfo.replaceChildren(heading, steps);
      } catch (error) {
        if (requestId !== detailRequest) return;
        selectedWorkflow = null;
        appendError(workflowInfo, error);
      } finally {
        if (requestId === detailRequest) workflowInfo.removeAttribute("aria-busy");
      }
    });

    if (mode?.dataset.dryRun === "false") {
      realModeConfirmation.hidden = false;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submissionInFlight) return;
      feedback.replaceChildren();
      if (!selectedWorkflow || selectedWorkflow.id !== workflowSelect.value) {
        setMessage(feedback, "error", "Choose a workflow and wait for its details to load.");
        workflowSelect.focus();
        return;
      }
      if (!projectName.reportValidity()) return;
      if (mode?.dataset.dryRun === "false" && !confirmInput.checked) {
        setMessage(feedback, "error", "Confirm that you want to create a run on this non-Mock server.");
        confirmInput.focus();
        return;
      }
      const { inputs, errors } = readInputs(inputRows);
      if (errors.length) {
        appendError(feedback, new ApiError({
          status: 0,
          code: "invalid_request",
          message: "Review the workflow inputs.",
          details: { errors: errors.map((msg) => ({ msg })) },
        }));
        inputRows.querySelector('[name="input-name"]')?.focus();
        return;
      }

      const requestBody = {
        workflow_id: selectedWorkflow.id,
        project_name: projectName.value.trim(),
        inputs,
        execution_mode: modeSelect.value,
      };
      const payloadFingerprint = JSON.stringify([readBundle(bundleSelect), requestBody]);
      if (payloadFingerprint !== idempotencyPayload) {
        idempotencyPayload = payloadFingerprint;
        idempotencyKey = crypto.randomUUID();
      }
      requestBody.idempotency_key = idempotencyKey;

      submissionInFlight = true;
      submit.disabled = true;
      submit.textContent = "Creating…";
      form.setAttribute("aria-busy", "true");
      try {
        const run = await createRun(requestBody, readBundle(bundleSelect));
        setMessage(feedback, "success", `Run ${run.id} created (${run.status}).`);
        context.run = run;
        document.dispatchEvent(new CustomEvent("run:selected", { detail: run }));
        idempotencyPayload = null;
        idempotencyKey = null;
      } catch (error) {
        appendError(feedback, error);
      } finally {
        submissionInFlight = false;
        submit.disabled = false;
        submit.textContent = "Create run";
        form.removeAttribute("aria-busy");
      }
    });

    workflowSelect.replaceChildren(element("option", { value: "" }, "Choose a workflow"));
    for (const workflow of workflows) {
      workflowSelect.append(element("option", { value: workflow.id }, workflow.id));
    }
    if (!workflows.length) setMessage(feedback, "notice", "No workflows are available for this selection.");
  }

  async function initialize() {
    formArea.setAttribute("aria-busy", "true");
    setMessage(feedback, "notice", "Connecting to the local API…");
    try {
      const [health, bundles] = await Promise.all([getHealth(), getBundles()]);
      const isMock = Boolean(health.dry_run);
      mode.dataset.dryRun = String(isMock);
      mode.className = `mode-status ${isMock ? "mock" : "real"}`;
      mode.textContent = isMock
        ? "MOCK mode is active. Runs use synthetic outputs."
        : "Mock mode is not active. Creating a run requires explicit confirmation.";
      await renderForm(bundles, await getWorkflows());
    } catch (error) {
      appendError(feedback, error);
      setMessage(mode, "error", "The API is unavailable; run creation is disabled.");
    } finally {
      formArea.removeAttribute("aria-busy");
    }
  }

  void initialize();
}
