// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Extension «apps» (#apps, INS-T05): shows connection status, INS-T03 permission template status, and only the missing step.
import "./i18n.js";
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "../../js/api.js";
import { registerHelp, registerView } from "../../js/extensions.js";
import { currentMode } from "../../js/gui-mode.js";   // SIM-T03
import { Help, helpFor } from "../../js/help.js";
import { errorText, fmtDate } from "../../js/text.js";
import { t } from "../../js/i18n.js";
import { StatusChip } from "../../js/ui/status-chip.js";
import { Tabs } from "../../js/ui/tabs.js";
import { HELP } from "./help.js";
import { buildAppsView, canShowAllReady, groupModelsByType, showSignInAfterConsole } from "./model.js";
import { activationNote, stopAllConfirmText } from "./activation-model.js";   // EJE-T02
import { activateExecutor, deactivateExecutor, requestActivation, stopAllExecutors } from "./activation.js";
import { AcpPanel } from "./acp.js";   // ACP-T04
import { DiscoveryCard } from "./discovery.js";   // EXA-T05
import { AgentsSection } from "./add-agent.js";   // EXA-T06
import { DISCOVERY_PATH } from "./discovery-model.js";

registerHelp(HELP, "apps");

const chipLabels = () => ({
  ok: { text: t("ext.apps.status.ready"), tone: "success", shape: "disc", explanation: t("ext.apps.chip.ready") },
  waiting: { text: t("ext.apps.status.missing_step"), tone: "warning", shape: "diamond", explanation: t("ext.apps.chip.missing_step") },
  unknown: { text: t("ext.apps.status.unknown"), tone: "neutral", shape: "ring", explanation: t("ext.apps.chip.unknown") },
  error: { text: t("ext.apps.reason.unavailable"), tone: "danger", shape: "square", explanation: t("ext.apps.chip.unavailable") },
  conflict: { text: t("ext.apps.status.conflict"), tone: "danger", shape: "square",
    explanation: t("ext.apps.chip.conflict") },
  unverified: { text: t("ext.apps.status.unverified"), tone: "neutral", shape: "ring", explanation: t("ext.apps.chip.unverified") },
  unsearched: { text: t("ext.apps.discovery.chip"), tone: "neutral", shape: "ring", explanation: t("ext.apps.chip.unsearched") },   // EXA-T05
  connected: { text: t("ext.apps.status.connected"), tone: "success", shape: "disc", explanation: t("ext.apps.chip.connected") },
  key_invalid: { text: t("ext.apps.status.key_invalid"), tone: "danger", shape: "square", explanation: t("ext.apps.chip.key_invalid") },
  no_access: { text: t("ext.apps.status.no_access"), tone: "danger", shape: "square", explanation: t("ext.apps.chip.no_access") },
  limit_reached: { text: t("ext.apps.status.limit_reached"), tone: "danger", shape: "square", explanation: t("ext.apps.chip.limit_reached") },
  // EJE-T02: the five states of an executor («Activar»).
  not_installed: { text: t("ext.apps.activation.state.not_installed"), tone: "danger", shape: "square", explanation: t("ext.apps.chip.not_installed") },
  no_session: { text: t("ext.apps.activation.state.no_session"), tone: "warning", shape: "diamond", explanation: t("ext.apps.chip.no_session") },
  ready: { text: t("ext.apps.activation.state.ready"), tone: "warning", shape: "diamond", explanation: t("ext.apps.chip.activation_ready") },
  active: { text: t("ext.apps.activation.state.active"), tone: "success", shape: "disc", explanation: t("ext.apps.chip.active") },
  paused: { text: t("ext.apps.activation.state.paused"), tone: "warning", shape: "diamond", explanation: t("ext.apps.chip.paused") },
});

const CONSOLE_POLL_MS = 4000;

const asError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));

function settingText(text) {
  return String(text).split(/(GEMINI_API_KEY|OPENAI_API_KEY)/g).map((part) => {
    if (part !== "GEMINI_API_KEY" && part !== "OPENAI_API_KEY") return part;
    const provider = part === "GEMINI_API_KEY" ? "GEMINI" : "OPENAI";
    return html`<span class="apps-key-name">${provider}_<wbr />API_<wbr />KEY</span>`;
  });
}

function consoleErrorText(error) {
  if (!error) return "";
  if (error.code === "console_already_open") return t("ext.apps.console.already_open");
  if (error.code === "launch_disabled") return t("ext.apps.console.launch_disabled");
  if (error.code === "launch_unsupported") return t("ext.apps.console.launch_unsupported");
  if (error.code === "effect_not_authorized") return t("ext.apps.console.effect_not_authorized");
  return errorText(error);
}

function CopyBlock({ label, text, scope, statusText }) {
  const [copied, setCopied] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied("ok");
    } catch (_) {
      setCopied("failed");
    }
  }
  return html`<div class="apps-snippet">
    ${label && html`<p class="apps-snippet-label">${label}</p>`}
    <pre class="data">${settingText(text)}</pre>
    <div class="apps-actions">
      <button type="button" class="btn secondary small" ...${helpFor("apps.copy", { scope })} onClick=${copy}>${t("ext.apps.copy")}</button>
      <${Help} topic="apps.copy" compact=${true} scope=${scope} />
      <span class="apps-status-line muted" role="status" aria-live="polite">
        ${copied === "ok" ? (statusText || t("ext.apps.copy.copied")) : copied === "failed" ? t("ext.apps.copy.failed") : ""}
      </span>
    </div>
  </div>`;
}

function ConsoleStep({ item, action, allowLaunch, consoleSupported, token, onDone, busyAny, onBusyChange }) {
  const [phase, setPhase] = useState("idle"); // idle | confirm | running
  const [result, setResult] = useState(null);
  const [ranStep, setRanStep] = useState(null);   // EXA-T05: the step that was launched, whatever the row says afterwards
  const [error, setError] = useState(null);
  const confirmRef = useRef(null);

  useEffect(() => {
    if (phase === "confirm" && confirmRef.current) confirmRef.current.focus();
  }, [phase]);

  // ARR2-T05: the server answers 202 «open» at once; this polls the console state until it closes.
  const [consoleId, setConsoleId] = useState(null);
  const finishRef = useRef(null);
  finishRef.current = (state) => {
    if (state.state === "closed") {
      setResult(state);
      onDone();
    } else {
      setError(new ApiError({ code: "console_failed", message: t("ext.apps.console.poll_failed") }));
    }
    setConsoleId(null);
    onBusyChange(false);
    setPhase("idle");
  };

  useEffect(() => {
    if (!consoleId) return undefined;
    let stopped = false;
    const timer = setInterval(async () => {
      try {
        const state = await apiGet("/gui/api/setup/console", token);
        if (stopped) return;
        if (state.console_id === consoleId && state.state === "open") return;
        stopped = true;
        clearInterval(timer);
        finishRef.current(state.console_id === consoleId ? state : { state: "failed" });
      } catch (_) {
        // A missed poll is not an error: the next one reads the same state.
      }
    }, CONSOLE_POLL_MS);
    return () => { stopped = true; clearInterval(timer); };
  }, [consoleId, token]);

  async function launch() {
    setRanStep(action.step);
    setPhase("running");
    onBusyChange(true);
    setError(null);
    setResult(null);
    try {
      const answer = await apiPost("/gui/api/setup/console", token, { id: item.id, step: action.step });
      if (answer.console_id) {
        setConsoleId(answer.console_id);
        return;
      }
      setResult(answer);
      onDone();
    } catch (failure) {
      setError(asError(failure));
    }
    onBusyChange(false);
    setPhase("idle");
  }

  const canOpen = allowLaunch && consoleSupported;
  return html`<div class="apps-step">
    <p class="apps-step-title">${action.title}</p>
    <p class="apps-step-text">${action.detail}</p>
    ${!canOpen && html`
      <p class="apps-status-line muted">${!allowLaunch
        ? t("ext.apps.console.disabled")
        : t("ext.apps.console.windows_only")}</p>
      ${action.commandText && html`<${CopyBlock} label=${t("ext.apps.console.official_command_terminal")} text=${action.commandText} scope=${item.id + "-cmd"} />`}
    `}
    ${canOpen && phase === "idle" && html`<div class="apps-actions">
      <button type="button" class="btn" disabled=${busyAny} ...${helpFor("apps.open_console", { scope: item.id })}
        onClick=${() => { setError(null); setResult(null); setPhase("confirm"); }}>${action.step === "install" ? t("ext.apps.install.button", { name: item.label }) : t("ext.apps.console.open")}</button>
      <${Help} topic="apps.open_console" compact=${true} scope=${item.id} />
    </div>`}
    ${canOpen && phase !== "idle" && html`<div class="apps-confirm" role="group" aria-label=${t("ext.apps.console.confirm_label", { name: item.label })}>
      <p>${t("ext.apps.console.confirm_before")}${action.commandText ? html` ${t("ext.apps.console.command")} <code>${action.commandText}</code>` : ""}.
        ${action.source ? ` (${t("ext.apps.console.source")}: ${action.source}${action.sourceDate ? ", " + action.sourceDate : ""}).` : ""}
        ${t("ext.apps.console.confirm_after")}</p>
      <div class="apps-actions">
        <button type="button" class="btn" ref=${confirmRef} data-help-exempt="confirm" disabled=${phase === "running"} onClick=${launch}>
          ${phase === "running" ? t("ext.apps.console.opened") : t("ext.apps.console.confirm_open")}
        </button>
        <button type="button" class="btn secondary" data-help-exempt="confirm" disabled=${phase === "running"}
          onClick=${() => setPhase("idle")}>${t("ext.apps.cancel")}</button>
      </div>
    </div>`}
    ${result && html`<p class="message ok apps-status-line" role="status">
      ${t("ext.apps.console.closed", { code: result.console_exit_code ?? 0 })}
    </p>`}
    ${showSignInAfterConsole(ranStep, result) && html`<p class="apps-step-text">${t("ext.apps.install.sign_in_yourself")}</p>`}
    ${error && html`<div role="alert">
      <p class="message error apps-status-line">${consoleErrorText(error)}</p>
      ${action.commandText && html`      <${CopyBlock} label=${t("ext.apps.console.official_command")} text=${action.commandText} scope=${item.id + "-errcmd"} />`}
    </div>`}
  </div>`;
}

function MissingStep({ item, allowLaunch, consoleSupported, token, onRecheck, busyAny, onBusyChange }) {
  const action = item.action;
  if (!action) {
    if (!canShowAllReady(item)) return null;
    return html`<p class="apps-status-line message ok">${t("ext.apps.all_ready")}</p>`;
  }
  if (action.kind === "acp_install" || action.kind === "acp_probe") return null;   // ACP-T04: the card draws them (acp.js)
  if (action.kind === "console") {
    return html`<${ConsoleStep} item=${item} action=${action} allowLaunch=${allowLaunch} consoleSupported=${consoleSupported}
      token=${token} onDone=${onRecheck} busyAny=${busyAny} onBusyChange=${onBusyChange} />`;
  }
  if (action.kind === "advanced_only") {   // SIM-T03: simple mode, no lines to paste and no «Copiar»
    return html`<div class="apps-step">
      <p class="apps-step-title">${action.title}</p>
      <p class="apps-step-text apps-advanced-step">${action.text}<${Help} topic="apps.advanced_step" compact=${true} scope=${item.id} /></p>
    </div>`;
  }
  if (action.kind === "key") {
    return html`<div class="apps-step">
      <p class="apps-step-title">${action.title}</p>
      <p class="apps-step-text">${settingText(action.detail)}</p>
      <${CopyBlock} label=${t("ext.apps.setting_name_for", { file: action.file })} text=${`${action.setting}=`} scope=${item.id + "-key"} />
    </div>`;
  }
  if (action.kind === "enable_executor") {   // EJE-T02: the button is in the activation panel of the card
    return html`<div class="apps-step">
      <p class="apps-step-title">${item.activation && item.activation.state === "paused" ? item.activation.label : action.title}</p>
      <p class="apps-notice">${action.notice}</p>
      ${action.manualText && html`<p class="apps-step-text muted">${action.manualText}</p>`}
    </div>`;
  }
  if (action.kind === "enable_model") {
    return html`<div class="apps-step">
      <p class="apps-step-title">${action.title}</p>
      <p class="apps-step-text">${action.detail}</p>
      <${CopyBlock} label=${t("ext.apps.in_file", { file: action.file })} text=${action.line} scope=${item.id + "-enable"} />
    </div>`;
  }
  return html`<div class="apps-step">
    <p class="apps-step-title">${action.title}</p>
    <p class="apps-step-text">${action.detail}</p>
  </div>`;
}

// EJE-T02: «Activar» / «Desactivar» / «Revisar y activar» of one executor card. The note comes from the server and is drawn as text.
const postWith = (token) => (path, body) => apiPost(path, token, body);

function ActivationPanel({ item, token, onChanged }) {
  const view = item.activation;
  const [phase, setPhase] = useState("idle"); // idle | requesting | note | working
  const [note, setNote] = useState(null);
  const [request, setRequest] = useState(null);
  const [message, setMessage] = useState(null); // {ok, text}
  const confirmRef = useRef(null);
  const scope = item.id;

  useEffect(() => {
    if (phase === "note" && confirmRef.current) confirmRef.current.focus();
  }, [phase]);

  async function openNote() {
    if (phase !== "idle") return;
    setMessage(null);
    setPhase("requesting");
    const outcome = await requestActivation({ executorId: item.id, post: postWith(token) });
    if (!outcome.ok) {
      setMessage({ ok: false, text: outcome.message });
      setPhase("idle");
      if (outcome.refresh || outcome.restart) onChanged(item.id);
      return;
    }
    setRequest(outcome.request);
    setNote(activationNote(outcome.request, item.id));
    setPhase("note");
  }

  function cancelNote() {
    setNote(null);
    setRequest(null);
    setPhase("idle");
  }

  async function accept() {
    if (phase !== "note") return;
    setPhase("working");
    const outcome = await activateExecutor({ token, executorId: item.id, post: postWith(token), request });
    setNote(null);
    setRequest(null);
    setPhase("idle");
    setMessage({ ok: outcome.ok, text: outcome.message });
    if (outcome.ok || outcome.refresh || outcome.restart) onChanged(item.id);
  }

  async function turnOff() {
    if (phase !== "idle") return;
    setMessage(null);
    setPhase("working");
    const outcome = await deactivateExecutor({ token, executorId: item.id, post: postWith(token) });
    setPhase("idle");
    setMessage({ ok: outcome.ok, text: outcome.message });
    if (outcome.ok || outcome.refresh) onChanged(item.id);
  }

  const busy = phase !== "idle";
  const can = (action) => view.actions.includes(action);
  return html`<div class="apps-step apps-activation">
    ${view.detail && html`<p class="apps-step-text apps-activation-detail">${view.detail}${view.state === "paused"
      ? html`<${Help} topic="apps.paused" compact=${true} scope=${scope} />` : ""}</p>`}
    ${phase !== "note" && (can("activate") || can("review") || can("deactivate")) && html`<div class="apps-actions">
      ${can("activate") && html`<button type="button" class="btn" disabled=${busy} ...${helpFor("apps.activate", { scope })}
        onClick=${openNote}>${phase === "requesting" ? t("ext.apps.activation.preparing") : t("ext.apps.activation.activate")}</button>
      <${Help} topic="apps.activate" compact=${true} scope=${scope} />`}
      ${can("review") && html`<button type="button" class="btn" disabled=${busy} ...${helpFor("apps.paused", { scope: scope + "-review" })}
        onClick=${openNote}>${phase === "requesting" ? t("ext.apps.activation.preparing") : t("ext.apps.activation.review_activate")}</button>`}
      ${can("deactivate") && html`<button type="button" class="btn secondary" disabled=${busy} ...${helpFor("apps.deactivate", { scope })}
        onClick=${turnOff}>${phase === "working" ? t("ext.apps.activation.deactivating") : t("ext.apps.activation.deactivate")}</button>
      <${Help} topic="apps.deactivate" compact=${true} scope=${scope} />`}
    </div>`}
    ${phase === "note" && note && html`<div class="apps-confirm apps-activation-note" role="group" aria-label=${note.title}>
      <p class="apps-step-title">${note.title}<${Help} topic="apps.activation_note" compact=${true} scope=${scope} /></p>
      ${note.lines.map((line, index) => html`<p key=${index} class="apps-step-text">${line}</p>`)}
      <div class="apps-actions">
        <button type="button" class="btn" ref=${confirmRef} data-help-exempt="confirm" onClick=${accept}>${note.confirmLabel}</button>
        <button type="button" class="btn secondary" data-help-exempt="confirm" onClick=${cancelNote}>${t("ext.apps.cancel")}</button>
      </div>
    </div>`}
    ${phase === "working" && html`<p class="apps-status-line muted" role="status">${t("ext.apps.activation.working")}</p>`}
    ${message && html`<p class=${"message apps-status-line " + (message.ok ? "ok" : "error")} role=${message.ok ? "status" : "alert"}>${message.text}</p>`}
  </div>`;
}

// «Apagar todos»: above the list of executors, with its confirmation.
function StopAll({ token, onChanged }) {
  const [phase, setPhase] = useState("idle"); // idle | confirm | working
  const [message, setMessage] = useState(null);

  async function stop() {
    setPhase("working");
    const outcome = await stopAllExecutors({ token, post: postWith(token), confirm: async () => true });
    setPhase("idle");
    setMessage({ ok: outcome.ok, text: outcome.message });
    if (outcome.ok) onChanged();
  }

  return html`<div class="apps-stop-all">
    ${phase === "idle" && html`<div class="apps-actions">
      <button type="button" class="btn secondary" ...${helpFor("apps.stop_all")}
        onClick=${() => { setMessage(null); setPhase("confirm"); }}>${t("ext.apps.activation.stop_all.button")}</button>
      <${Help} topic="apps.stop_all" compact=${true} />
    </div>`}
    ${phase !== "idle" && html`<div class="apps-confirm" role="group" aria-label=${t("ext.apps.activation.stop_all.aria_label")}>
      <p>${stopAllConfirmText()}.</p>
      <div class="apps-actions">
        <button type="button" class="btn" data-help-exempt="confirm" disabled=${phase === "working"} onClick=${stop}>
          ${phase === "working" ? t("ext.apps.activation.stop_all.working") : t("ext.apps.activation.stop_all.confirm_button")}</button>
        <button type="button" class="btn secondary" data-help-exempt="confirm" disabled=${phase === "working"}
          onClick=${() => setPhase("idle")}>${t("ext.apps.cancel")}</button>
      </div>
    </div>`}
    ${message && html`<p class=${"message apps-status-line " + (message.ok ? "ok" : "error")} role=${message.ok ? "status" : "alert"}>${message.text}</p>`}
  </div>`;
}

function ToolCard({ item, allowLaunch, consoleSupported, token, onRowUpdated, onActivationChanged, busyAny, onBusyChange }) {
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState(null);
  const [open, setOpen] = useState(Boolean(item.defaultOpen));

  async function recheck() {
    if (checking) return;
    setChecking(true);
    setCheckError(null);
    try {
      const updated = await apiPost(`/gui/api/connections/${encodeURIComponent(item.id)}/check`, token, {});
      onRowUpdated(updated);
    } catch (failure) {
      setCheckError(asError(failure));
    } finally {
      setChecking(false);
    }
  }

  async function probe() {
    if (checking) return;
    setChecking(true);
    setCheckError(null);
    try {
      onRowUpdated(await apiPost(`/gui/api/connections/${encodeURIComponent(item.id)}/probe`, token, {}));
    } catch (failure) {
      setCheckError(asError(failure));
    } finally {
      setChecking(false);
    }
  }

  // ARR3-T07: a trust decision of the person. The server writes it, records it and tries the connection again.
  async function setTrust(button) {
    if (checking) return;
    setChecking(true);
    setCheckError(null);
    try {
      onRowUpdated(await apiPost(`/gui/api/connections/${encodeURIComponent(item.id)}/tls-trust`, token, { trust: button.trust }));
    } catch (failure) {
      setCheckError(asError(failure));
    } finally {
      setChecking(false);
    }
  }

  return html`<li class="apps-card">
    <details class="apps-card-details" open=${open} onToggle=${(event) => setOpen(event.currentTarget.open)}>
      <summary class="apps-card-summary">
        <span class="apps-card-head">
          <strong class="apps-card-name">${item.label}</strong>
          <${StatusChip} status=${item.chip.status} labels=${chipLabels()} variant="inline" />
        </span>
        ${item.missingLine && html`<span class="apps-card-missing">${item.missingLine}</span>`}
      </summary>
      <div class="apps-card-body">
        <dl class="apps-facts">
          ${item.acp ? item.acp.facts.map((fact) => html`<dt key=${"t" + fact.id}>${fact.label}</dt><dd key=${"d" + fact.id}>${fact.value}</dd>`) : html`
          <dt>${t("ext.apps.field.installed")}</dt><dd>${item.installed}</dd>
          <dt>${t("ext.apps.field.version")}</dt><dd>${item.version}</dd>
          <dt>${t("ext.apps.field.session")}</dt><dd>${settingText(item.session)}</dd>
          <dt>${t("ext.apps.field.status")}</dt><dd>${item.enabled}</dd>
          <dt>${t("ext.apps.field.permissions")}</dt><dd>${item.permissions}</dd>`}
          ${item.quota.length > 0 && html`<dt>${t("ext.apps.field.quota")}</dt><dd class="apps-quota">${item.quota.map((line) => html`<span key=${line}>${line}</span>`)}</dd>`}
          ${item.reasonText && html`<dt>${t("ext.apps.field.reason")}</dt><dd>${item.reasonText}</dd>`}
          <dt>${t("ext.apps.field.checked")}</dt><dd>${item.checkedAt ? fmtDate(item.checkedAt) : t("ext.apps.no_date")}</dd>
        </dl>
        ${item.warning && html`<p class="apps-notice" role="status">${item.warning}</p>`}
        ${!(item.activation && item.activation.state === "active") && html`<${MissingStep} item=${item} allowLaunch=${allowLaunch}
          consoleSupported=${consoleSupported} token=${token} onRecheck=${recheck} busyAny=${busyAny} onBusyChange=${onBusyChange} />`}
        ${item.activation && ["ready", "active", "paused"].includes(item.activation.state) && html`<${ActivationPanel} item=${item}
          token=${token} onChanged=${onActivationChanged} />`}
        ${item.acp && html`<${AcpPanel} item=${item} token=${token} onRowUpdated=${onRowUpdated} onChanged=${onActivationChanged} />`}
        ${(item.tlsFix || item.tlsRevert) && html`<div class="apps-step apps-tls">
          <p class="apps-step-text">${item.tlsFix
            ? t("ext.apps.tls.system_store")
            : t("ext.apps.tls.revert")}</p>
          <div class="apps-actions">
            ${item.tlsFix && html`<button type="button" class="btn" disabled=${checking} ...${helpFor("apps.tls_fix", { scope: item.id })}
              onClick=${() => setTrust(item.tlsFix)}>${item.tlsFix.label}</button>`}
            ${item.tlsRevert && html`<button type="button" class="btn secondary" disabled=${checking} ...${helpFor("apps.tls_fix", { scope: item.id + "-revert" })}
              onClick=${() => setTrust(item.tlsRevert)}>${item.tlsRevert.label}</button>`}
            <${Help} topic="apps.tls_fix" compact=${true} scope=${item.id} />
          </div>
        </div>`}
        ${checkError && html`<p class="message error apps-status-line" role="alert">${errorText(checkError)}</p>`}
        <div class="apps-card-foot">
          <button type="button" class="btn secondary" disabled=${checking} ...${helpFor("apps.recheck", { scope: item.id })}
            onClick=${recheck}>${checking ? t("ext.apps.checking") : t("ext.apps.recheck")}</button>
          <${Help} topic="apps.recheck" compact=${true} scope=${item.id} />
          ${item.canProbe && html`<button type="button" class="btn secondary" disabled=${checking} ...${helpFor("apps.probe", { scope: item.id })}
            onClick=${probe}>${checking ? t("ext.apps.probing") : t("ext.apps.probe")}</button>
          <${Help} topic="apps.probe" compact=${true} scope=${item.id} />`}
        </div>
      </div>
    </details>
  </li>`;
}

const foldedCard = (item) => ({ ...item, defaultOpen: Boolean(item.defaultOpen) });

export function AppsView({ token }) {
  const [connections, setConnections] = useState(null);
  const [setupStatus, setSetupStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [setupWarning, setSetupWarning] = useState(null);
  const [consoleBusy, setConsoleBusy] = useState(false);
  const [activations, setActivations] = useState(null);
  const [composerOptions, setComposerOptions] = useState(null);
  const [optionsWarning, setOptionsWarning] = useState(null);
  const [tab, setTab] = useState("apps");
  const [discovery, setDiscovery] = useState(null);   // EXA-T05

  async function loadAll() {
    setLoading(true);
    setError(null);
    setSetupWarning(null);
    setOptionsWarning(null);
    const [connRes, setupRes, activationRes, optionsRes, discoveryRes] = await Promise.allSettled([
      apiGet("/gui/api/connections", token),
      apiGet("/gui/api/setup/status", token),
      apiGet("/gui/api/executors/activations", token),   // EJE-T02
      apiGet("/gui/api/composer/options", token),
      apiGet(DISCOVERY_PATH, token),   // EXA-T05: reading the answer measures nothing
    ]);
    setDiscovery(discoveryRes.status === "fulfilled" ? discoveryRes.value : null);
    setActivations(activationRes.status === "fulfilled" ? activationRes.value : null);
    setComposerOptions(optionsRes.status === "fulfilled" ? optionsRes.value : null);
    if (connRes.status === "fulfilled") {
      setConnections(connRes.value);
    } else {
      setError(asError(connRes.reason));
    }
    if (setupRes.status === "fulfilled") {
      setSetupStatus(setupRes.value);
    } else {
      setSetupWarning(asError(setupRes.reason));
    }
    if (optionsRes.status === "rejected") {
      setOptionsWarning(asError(optionsRes.reason));
    }
    setLoading(false);
  }

  useEffect(() => { loadAll(); }, [token]);

  // EXA-T05: the PUT already brings the answer and the measured rows, so only what depends on them is read again.
  async function handleAnswered(saved) {
    if (saved && saved.discovery) setDiscovery(saved.discovery);
    if (saved && Array.isArray(saved.connections)) setConnections({ connections: saved.connections });
    const [activationRes, setupRes, optionsRes] = await Promise.allSettled([
      apiGet("/gui/api/executors/activations", token),
      apiGet("/gui/api/setup/status", token),
      apiGet("/gui/api/composer/options", token),
    ]);
    if (activationRes.status === "fulfilled") setActivations(activationRes.value);
    if (setupRes.status === "fulfilled") setSetupStatus(setupRes.value);
    if (optionsRes.status === "fulfilled") setComposerOptions(optionsRes.value);
  }

  function handleRowUpdated(updated) {
    if (!updated || !updated.id) return;
    setConnections((current) => {
      const prev = Array.isArray(current?.connections) ? current.connections : [];
      const exists = prev.some((row) => row.id === updated.id);
      const next = exists ? prev.map((row) => (row.id === updated.id ? updated : row)) : [...prev, updated];
      return { connections: next };
    });
    apiGet("/gui/api/setup/status", token).then(setSetupStatus).catch(() => {});
  }

  // EJE-T02: after «Activar», «Desactivar» or «Apagar todos» the states are read again and the connection is measured again.
  async function handleActivationChanged(executorId) {
    try {
      setActivations(await apiGet("/gui/api/executors/activations", token));
    } catch (_) { /* the next «Actualizar» reads it again */ }
    if (!executorId) {
      loadAll();
      return;
    }
    try {
      handleRowUpdated(await apiPost(`/gui/api/connections/${encodeURIComponent(executorId)}/check`, token, {}));
    } catch (_) { /* the card keeps its last row */ }
  }

  const view = buildAppsView(connections, setupStatus, currentMode(), activations);
  const mode = currentMode();
  const modelGroups = groupModelsByType(view.models, composerOptions);
  const tabs = mode === "simple"
    ? [
      { id: "apps", label: t("ext.apps.tab.apps.simple") },
      { id: "local", label: t("ext.apps.tab.local.simple") },
      { id: "cloud", label: t("ext.apps.tab.cloud.simple") },
    ]
    : [
      { id: "apps", label: t("ext.apps.tab.apps.advanced") },
      { id: "local", label: t("ext.apps.tab.local.advanced") },
      { id: "cloud", label: t("ext.apps.tab.cloud.advanced") },
    ];
  const selectedTab = tabs.find((item) => item.id === tab) || tabs[0];
  const items = tab === "apps" ? view.executors : tab === "local" ? modelGroups.localModels : modelGroups.cloudModels;

  return html`<section class="apps-view" aria-label=${t("ext.apps.view")} aria-busy=${loading ? "true" : "false"}>
    <div class="apps-head">
      <div class="apps-toolbar">
        <button type="button" class="btn secondary" disabled=${loading} ...${helpFor("apps.refresh")} onClick=${loadAll}>
          ${loading ? t("ext.apps.refreshing") : t("ext.apps.refresh")}
        </button>
        <${Help} topic="apps.refresh" compact=${true} />
        <a class="btn secondary" href="#concurrency" ...${helpFor("concurrency.view", { scope: "apps-link" })}>${t("ext.concurrency.link")}</a>
        <${Help} topic="concurrency.view" scope="apps-link" compact=${true} />
      </div>
    </div>
    ${error && html`<p class="message error" role="alert">${t("ext.apps.read_connections_failed")}: ${errorText(error)}</p>`}
    ${setupWarning && html`<p class="message" role="status">${t("ext.apps.read_templates_failed")}: ${errorText(setupWarning)}</p>`}
    ${optionsWarning && html`<p class="message" role="status">${t("ext.apps.read_options_failed")}: ${errorText(optionsWarning)}</p>`}
    ${loading && !connections && html`<p class="muted" role="status">${t("ext.apps.loading")}</p>`}
    ${!loading && !error && view.empty && html`<p class="muted">${t("ext.apps.empty")}</p>`}
    ${discovery && html`<${DiscoveryCard} discovery=${discovery} mode=${mode} token=${token} onAnswered=${handleAnswered} />`}
    ${!view.empty && html`
      ${view.executors.length > 0 && view.canStopAll && html`<${StopAll} token=${token} onChanged=${() => handleActivationChanged(null)} />`}
      <${Tabs} id="apps-tabs" label=${t("ext.apps.tabs.label")} className="apps-tabs" selected=${tab} tabs=${tabs} onSelect=${setTab}
        after=${tab === "apps"
          ? html`<${Help} key=${tab === "apps" ? "executors" : "models"} topic="apps.executors" compact=${true} />`
          : html`<${Help} key=${tab === "apps" ? "executors" : "models"} topic="apps.models" compact=${true} />`}
        ...${tab === "apps" ? helpFor("apps.executors") : helpFor("apps.models")}>
        <section class="apps-section" aria-label=${selectedTab.label}>
          ${items.length > 0 ? html`<ul class="apps-grid">
            ${items.map((item) => html`<${ToolCard} key=${item.id} item=${foldedCard(item)}
              allowLaunch=${view.allowLaunch} consoleSupported=${view.consoleSupported} token=${token}
              onRowUpdated=${handleRowUpdated} onActivationChanged=${handleActivationChanged} busyAny=${consoleBusy} onBusyChange=${setConsoleBusy} />`)}
          </ul>` : html`<p class="muted">${t("ext.apps.empty_group")}</p>`}
          ${tab === "apps" && html`<${AgentsSection} mode=${mode} token=${token} />`}
        </section>
      <//>`}
  </section>`;
}

registerView({ id: "apps", label: t("ext.apps.view"), topic: "apps.view", component: AppsView, order: 70, owner: "apps", group: "primary", icon: "apps", mode: "simple" });
