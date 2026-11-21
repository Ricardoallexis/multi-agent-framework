// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Extension «apps» (#apps, INS-T05): shows connection status, INS-T03 permission template status, and only the missing step.
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "../../js/api.js";
import { registerHelp, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { errorText, fmtDate } from "../../js/text.js";
import { StatusChip } from "../../js/ui/status-chip.js";
import { HELP } from "./help.js";
import { buildAppsView } from "./model.js";

registerHelp(HELP, "apps");

const CHIP_LABELS = {
  ok: { text: "Listo", tone: "success", shape: "disc", explanation: "Listo para usar." },
  waiting: { text: "Falta un paso", tone: "warning", shape: "diamond", explanation: "Falta completar un paso." },
  unknown: { text: "Sin verificar", tone: "neutral", shape: "ring", explanation: "No se puede comprobar sin gastar." },
  error: { text: "No disponible", tone: "danger", shape: "square", explanation: "No disponible en este momento." },
  unverified: { text: "Sin comprobar", tone: "neutral", shape: "ring", explanation: "Clave configurada; se comprueba con el primer mensaje." },
  connected: { text: "Conectado", tone: "success", shape: "disc", explanation: "Conectado y comprobado con una respuesta reciente." },
  key_invalid: { text: "Clave inválida", tone: "danger", shape: "square", explanation: "El proveedor rechazó la clave configurada." },
  no_access: { text: "Sin acceso", tone: "danger", shape: "square", explanation: "No se pudo conectar con el proveedor." },
  limit_reached: { text: "Límite alcanzado", tone: "danger", shape: "square", explanation: "Se alcanzó el límite de uso." },
};

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
  if (error.code === "console_already_open") return "Ya hay otra consola de configuración abierta. Ciérrala antes de abrir otra.";
  if (error.code === "launch_disabled") return "Abrir consola está desactivado (gui_allow_launch). Copia el comando y ejecútalo tú.";
  if (error.code === "launch_unsupported") return "Abrir consola visible solo está disponible en Windows. Copia el comando y ejecútalo tú.";
  if (error.code === "effect_not_authorized") return "La política activa no autorizó abrir la consola.";
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
      <button type="button" class="btn secondary small" ...${helpFor("apps.copy", { scope })} onClick=${copy}>Copiar</button>
      <${Help} topic="apps.copy" compact=${true} scope=${scope} />
      <span class="apps-status-line muted" role="status" aria-live="polite">
        ${copied === "ok" ? (statusText || "Copiado al portapapeles.") : copied === "failed" ? "No se pudo copiar; selecciónalo y cópialo a mano." : ""}
      </span>
    </div>
  </div>`;
}

function ConsoleStep({ item, action, allowLaunch, consoleSupported, token, onDone, busyAny, onBusyChange }) {
  const [phase, setPhase] = useState("idle"); // idle | confirm | running
  const [result, setResult] = useState(null);
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
      setError(new ApiError({ code: "console_failed", message: "No se pudo seguir la consola." }));
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
    ${!canOpen && action.commandText && html`
      <p class="apps-status-line muted">${!allowLaunch
        ? "El visor tiene desactivado abrir consolas (gui_allow_launch)."
        : "Abrir consola visible solo está disponible en Windows."}</p>
      <${CopyBlock} label="Comando oficial para ejecutar en tu terminal:" text=${action.commandText} scope=${item.id + "-cmd"} />
    `}
    ${canOpen && phase === "idle" && html`<div class="apps-actions">
      <button type="button" class="btn" disabled=${busyAny} ...${helpFor("apps.open_console", { scope: item.id })}
        onClick=${() => { setError(null); setResult(null); setPhase("confirm"); }}>Abrir consola</button>
      <${Help} topic="apps.open_console" compact=${true} scope=${item.id} />
    </div>`}
    ${canOpen && phase !== "idle" && html`<div class="apps-confirm" role="group" aria-label=${"Confirmar abrir consola para " + item.label}>
      <p>Se abrirá una consola visible de Windows${action.commandText ? html` con el comando <code>${action.commandText}</code>` : ""}.
        ${action.source ? ` (Fuente: ${action.source}${action.sourceDate ? ", " + action.sourceDate : ""}).` : ""}
        Pulsa Enter dentro de la consola para ejecutarlo; no se leen credenciales.</p>
      <div class="apps-actions">
        <button type="button" class="btn" ref=${confirmRef} data-help-exempt="confirm" disabled=${phase === "running"} onClick=${launch}>
          ${phase === "running" ? "Consola abierta…" : "Confirmar y abrir consola"}
        </button>
        <button type="button" class="btn secondary" data-help-exempt="confirm" disabled=${phase === "running"}
          onClick=${() => setPhase("idle")}>Cancelar</button>
      </div>
    </div>`}
    ${result && html`<p class="message ok apps-status-line" role="status">
      Consola cerrada (código ${result.console_exit_code ?? 0}). Estado actualizado; pulsa «Volver a comprobar» si hace falta.
    </p>`}
    ${error && html`<div role="alert">
      <p class="message error apps-status-line">${consoleErrorText(error)}</p>
      ${action.commandText && html`<${CopyBlock} label="Comando oficial:" text=${action.commandText} scope=${item.id + "-errcmd"} />`}
    </div>`}
  </div>`;
}

function MissingStep({ item, allowLaunch, consoleSupported, token, onRecheck, busyAny, onBusyChange }) {
  const action = item.action;
  if (!action) {
    return html`<p class="apps-status-line message ok">Todo listo para usar.</p>`;
  }
  if (action.kind === "console") {
    return html`<${ConsoleStep} item=${item} action=${action} allowLaunch=${allowLaunch} consoleSupported=${consoleSupported}
      token=${token} onDone=${onRecheck} busyAny=${busyAny} onBusyChange=${onBusyChange} />`;
  }
  if (action.kind === "key") {
    return html`<div class="apps-step">
      <p class="apps-step-title">${action.title}</p>
      <p class="apps-step-text">${settingText(action.detail)}</p>
      <${CopyBlock} label=${`Nombre del ajuste para ${action.file}:`} text=${`${action.setting}=`} scope=${item.id + "-key"} />
    </div>`;
  }
  if (action.kind === "enable_executor") {
    return html`<div class="apps-step">
      <p class="apps-step-title">${action.title}</p>
      <p class="apps-notice">${action.notice}</p>
      <${CopyBlock} label=${`En ${action.catalogFile}:`} text=${action.catalogLines} scope=${item.id + "-cat"} />
      ${action.envLines && html`<${CopyBlock} label=${`En ${action.envFile}:`} text=${action.envLines} scope=${item.id + "-env"} />`}
      <p class="apps-step-text muted">Si decides habilitarlo, pega estas líneas, reinicia la aplicación y pulsa «Volver a comprobar».</p>
    </div>`;
  }
  if (action.kind === "enable_model") {
    return html`<div class="apps-step">
      <p class="apps-step-title">${action.title}</p>
      <p class="apps-step-text">${action.detail}</p>
      <${CopyBlock} label=${`En ${action.file}:`} text=${action.line} scope=${item.id + "-enable"} />
    </div>`;
  }
  return html`<div class="apps-step">
    <p class="apps-step-title">${action.title}</p>
    <p class="apps-step-text">${action.detail}</p>
  </div>`;
}

function ToolCard({ item, allowLaunch, consoleSupported, token, onRowUpdated, busyAny, onBusyChange }) {
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

  return html`<li class="apps-card">
    <details class="apps-card-details" open=${open} onToggle=${(event) => setOpen(event.currentTarget.open)}>
      <summary class="apps-card-summary">
        <span class="apps-card-head">
          <strong class="apps-card-name">${item.label}</strong>
          <${StatusChip} status=${item.chip.status} labels=${CHIP_LABELS} variant="inline" />
        </span>
        ${item.missingLine && html`<span class="apps-card-missing">${item.missingLine}</span>`}
      </summary>
      <div class="apps-card-body">
        <dl class="apps-facts">
          <dt>Instalada</dt><dd>${item.installed}</dd>
          <dt>Versión</dt><dd>${item.version}</dd>
          <dt>Sesión</dt><dd>${settingText(item.session)}</dd>
          <dt>Estado</dt><dd>${item.enabled}</dd>
          <dt>Permisos</dt><dd>${item.permissions}</dd>
          ${item.quota.length > 0 && html`<dt>Cuota</dt><dd class="apps-quota">${item.quota.map((line) => html`<span key=${line}>${line}</span>`)}</dd>`}
          ${item.reasonText && html`<dt>Motivo</dt><dd>${item.reasonText}</dd>`}
          <dt>Comprobado</dt><dd>${item.checkedAt ? fmtDate(item.checkedAt) : "Sin fecha"}</dd>
        </dl>
        ${item.warning && html`<p class="apps-notice" role="status">${item.warning}</p>`}
        <${MissingStep} item=${item} allowLaunch=${allowLaunch} consoleSupported=${consoleSupported} token=${token}
          onRecheck=${recheck} busyAny=${busyAny} onBusyChange=${onBusyChange} />
        ${checkError && html`<p class="message error apps-status-line" role="alert">${errorText(checkError)}</p>`}
        <div class="apps-card-foot">
          <button type="button" class="btn secondary" disabled=${checking} ...${helpFor("apps.recheck", { scope: item.id })}
            onClick=${recheck}>${checking ? "Comprobando…" : "Volver a comprobar"}</button>
          <${Help} topic="apps.recheck" compact=${true} scope=${item.id} />
        </div>
      </div>
    </details>
  </li>`;
}

export function AppsView({ token }) {
  const [connections, setConnections] = useState(null);
  const [setupStatus, setSetupStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [setupWarning, setSetupWarning] = useState(null);
  const [consoleBusy, setConsoleBusy] = useState(false);

  async function loadAll() {
    setLoading(true);
    setError(null);
    setSetupWarning(null);
    const [connRes, setupRes] = await Promise.allSettled([
      apiGet("/gui/api/connections", token),
      apiGet("/gui/api/setup/status", token),
    ]);
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
    setLoading(false);
  }

  useEffect(() => { loadAll(); }, [token]);

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

  const view = buildAppsView(connections, setupStatus);

  return html`<section class="apps-view" aria-label="Apps y agentes" aria-busy=${loading ? "true" : "false"}>
    <div class="apps-head">
      <div class="apps-toolbar">
        <button type="button" class="btn secondary" disabled=${loading} ...${helpFor("apps.refresh")} onClick=${loadAll}>
          ${loading ? "Actualizando…" : "Actualizar"}
        </button>
        <${Help} topic="apps.refresh" compact=${true} />
      </div>
    </div>
    ${error && html`<p class="message error" role="alert">No se pudo leer el estado de las conexiones: ${errorText(error)}</p>`}
    ${setupWarning && html`<p class="message" role="status">No se pudo leer el estado de plantillas: ${errorText(setupWarning)}</p>`}
    ${loading && !connections && html`<p class="muted" role="status">Comprobando apps y modelos…</p>`}
    ${!loading && !error && view.empty && html`<p class="muted">No hay herramientas configuradas para mostrar.</p>`}
    ${view.executors.length > 0 && html`<section class="apps-section" aria-labelledby="apps-executors-title">
      <div class="apps-section-head">
        <h4 id="apps-executors-title">Apps y ejecutores</h4><${Help} topic="apps.executors" />
      </div>
      <ul class="apps-grid">
        ${view.executors.map((item) => html`<${ToolCard} key=${item.id} item=${item}
          allowLaunch=${view.allowLaunch} consoleSupported=${view.consoleSupported} token=${token}
          onRowUpdated=${handleRowUpdated} busyAny=${consoleBusy} onBusyChange=${setConsoleBusy} />`)}
      </ul>
    </section>`}
    ${view.models.length > 0 && html`<section class="apps-section" aria-labelledby="apps-models-title">
      <div class="apps-section-head">
        <h4 id="apps-models-title">Modelos</h4><${Help} topic="apps.models" />
      </div>
      <ul class="apps-grid">
        ${view.models.map((item) => html`<${ToolCard} key=${item.id} item=${item}
          allowLaunch=${view.allowLaunch} consoleSupported=${view.consoleSupported} token=${token}
          onRowUpdated=${handleRowUpdated} busyAny=${consoleBusy} onBusyChange=${setConsoleBusy} />`)}
      </ul>
    </section>`}
  </section>`;
}

registerView({ id: "apps", label: "Apps y agentes", topic: "apps.view", component: AppsView, order: 70, owner: "apps", group: "primary", icon: "apps" });
