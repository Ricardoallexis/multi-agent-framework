// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// "Abrir en la app" (GUI-T06). A Codex or Copilot session opens through a link that the browser follows; a Claude
// session asks the server to launch the Claude app in a new console (the server picks the id from its own data).
// The link and the command come ONLY from the API's open_in_app value and only after it matches the strict pattern
// of its own source (the identifier is a UUID); titles, folders and every other field never build a link.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { apiGet, apiPost } from "./api.js";
import { Help, helpFor } from "./help.js";
import { t } from "./i18n.js";
import { errorText } from "./text.js";

const UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const CODEX_URL = /^codex:\/\/threads\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$/;
const COPILOT_URL = /^ghapp:\/\/sessions\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$/;
const LINK_APPS = new Map([
  ["codex", { label: "Codex", pattern: CODEX_URL }],
  ["copilot", { label: "Copilot", pattern: COPILOT_URL }],
]);

// The identifier inside the link or command must be the session's own.
const sameSession = (summary, uuid) =>
  String(summary.native_id).toLowerCase() === uuid.toLowerCase() && summary.id === summary.source + ":" + summary.native_id;

// The URL to follow for a Codex or Copilot session, or null when open_in_app is missing or does not match.
export function linkTarget(summary) {
  const app = summary ? LINK_APPS.get(summary.source) : null;
  const open = summary ? summary.open_in_app : null;
  if (!app || !open || open.kind !== "url" || typeof open.value !== "string") return null;
  const match = app.pattern.exec(open.value);
  return match && sameSession(summary, match[1]) ? open.value : null;
}

// The command of a Claude session as text to show or copy, or null: exactly claude --desktop --resume <uuid>.
export function commandTarget(summary) {
  const open = summary ? summary.open_in_app : null;
  if (!summary || summary.source !== "claude" || !open || open.kind !== "command" || !Array.isArray(open.value)) return null;
  const [name, desktop, resume, uuid, ...extra] = open.value;
  if (name !== "claude" || desktop !== "--desktop" || resume !== "--resume" || extra.length) return null;
  if (typeof uuid !== "string" || !UUID.test(uuid) || !sameSession(summary, uuid)) return null;
  return "claude --desktop --resume " + uuid;
}

const ERRORS = {
  launch_disabled: () => t("open.error.launch_disabled"),
  claude_not_found: () => t("open.error.claude_not_found"),
  launch_unsupported: () => t("open.error.launch_unsupported"),
  launch_failed: () => t("open.error.launch_failed"),
  too_many_launches: () => t("open.error.too_many_launches"),
  unsupported_source: () => t("open.error.unsupported_source"),
  invalid_open_target: () => t("open.error.invalid_open_target"),
  not_found: () => t("open.error.not_found"),
};
const launchErrorText = (error) => ERRORS[error.code]?.() || errorText(error);

const NOTE_REOPEN = (app) => t("open.note_reopen", { app });

function CopyCommand({ command, cwd }) {
  const [copied, setCopied] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied("ok");
    } catch (_) {
      setCopied("failed");
    }
  }
  return html`<div class="open-copy">
    <p class="note small">${t("open.copy.instructions")}${cwd ? html`, ${t("open.copy.folder")} <code>${cwd}</code>` : ""}:</p>
    <p><code class="command">${command}</code>
      <button type="button" class="btn secondary small" ...${helpFor("open.copy", { scope: "command" })} onClick=${copy}>${t("open.copy.button")}</button></p>
    <${Help} topic="open.copy" scope="command" compact=${true} />
    <p class="note small" role="status" aria-live="polite">${copied === "ok" ? t("open.copy.copied") : copied === "failed" ? t("open.copy.failed") : ""}</p>
  </div>`;
}

function LinkOpen({ summary, url }) {
  const app = LINK_APPS.get(summary.source);
  const asks = Boolean(summary.open_in_app.needs_confirmation);
  return html`<div class="open-in-app">
    <a class="btn secondary" href=${url} ...${helpFor("open." + summary.source)}>${t("open.link.button", { app: app.label })}</a>
    <${Help} topic=${"open." + summary.source} />
    <ul class="open-notes note small">
      ${asks && html`<li>${t("open.link.confirm", { app: app.label })}</li>`}
      ${summary.source === "copilot" && html`<li>${NOTE_REOPEN(app.label)}</li>`}
      ${summary.source === "codex" && html`<li>${t("open.codex.read_only")}</li>`}
    </ul>
  </div>`;
}

function ClaudeOpen({ token, summary, command }) {
  const [allow, setAllow] = useState(null); // null: not known yet, the server decides
  const [step, setStep] = useState("idle"); // idle | confirm | sending
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const confirmButton = useRef(null);

  useEffect(() => {
    let cancelled = false;
    apiGet("/gui/api/open-in-app", token)
      .then((options) => { if (!cancelled) setAllow(Boolean(options && options.allow_launch)); })
      .catch(() => { /* unknown: the button stays and the server answers when it is used */ });
    return () => { cancelled = true; };
  }, [token]);

  useEffect(() => {
    if (step === "confirm" && confirmButton.current) confirmButton.current.focus();
  }, [step]);

  async function launch() {
    setStep("sending");
    setError(null);
    setResult(null);
    try {
      setResult(await apiPost("/gui/api/sessions/" + encodeURIComponent(summary.id) + "/open", token, {}));
    } catch (failure) {
      setError(failure);
    }
    setStep("idle");
  }

  const notes = html`<ul class="open-notes note small">
    <li>${NOTE_REOPEN("Claude")}</li>
  </ul>`;
  if (allow === false) {
    return html`<div class="open-in-app">
      <p class="note small">${t("open.claude.disabled")}</p>
      <${CopyCommand} command=${command} cwd=${summary.cwd} />
      ${notes}
    </div>`;
  }
  return html`<div class="open-in-app">
    ${step === "idle" && html`<button type="button" class="btn secondary" ...${helpFor("open.claude")}
      onClick=${() => { setResult(null); setError(null); setStep("confirm"); }}>${t("open.claude.button")}</button>`}
    <${Help} topic="open.claude" />
    ${step !== "idle" && html`<div class="panel open-confirm" role="group" aria-label=${t("open.claude.confirm_label")}>
      <p>${t("open.claude.confirm_before")} <code class="command">${command}</code>${t("open.claude.confirm_after")}</p>
      <div class="actions">
        <button type="button" class="btn" ref=${confirmButton} data-help-exempt="confirm" disabled=${step === "sending"} onClick=${launch}>
          ${step === "sending" ? t("open.claude.opening") : t("open.claude.open")}</button>
        <button type="button" class="btn secondary" data-help-exempt="confirm" disabled=${step === "sending"} onClick=${() => setStep("idle")}>${t("open.claude.cancel")}</button>
      </div>
    </div>`}
    ${result && html`<p class="message ok" role="status">${t("open.claude.launched_before")} <code class="command">${result.command.join(" ")}</code>${t("open.claude.launched_after")}</p>`}
    ${error && html`<div role="alert"><p class="message error">${launchErrorText(error)}</p>
      <${CopyCommand} command=${command} cwd=${summary.cwd} /></div>`}
    ${notes}
  </div>`;
}

export function OpenInApp({ token, summary }) {
  if (summary.source === "framework") return null; // a run has its own link to the runs view (see the header)
  const url = linkTarget(summary);
  if (url) return html`<${LinkOpen} summary=${summary} url=${url} />`;
  const command = commandTarget(summary);
  if (command) return html`<${ClaudeOpen} token=${token} summary=${summary} command=${command} />`;
  return html`<p class="note small">${summary.parent_id
    ? t("open.subagent")
    : t("open.invalid_session")}</p>`;
}
