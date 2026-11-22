// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure view-model for «Apps y agentes» (#apps): transforms /gui/api/connections and /gui/api/setup/status without DOM or network.
import { formatQuotaLines } from "../../js/quota-text.js";
import { limitReachedText, quotaWarningText } from "../../js/new-session-model.js";

export const EXECUTOR_IDS = ["claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"];
export const MODEL_IDS = ["gemini", "openai", "ollama"];

const LABELS = {
  claude_cli: "Claude Code",
  codex_cli: "Codex CLI",
  copilot_cli: "GitHub Copilot CLI",
  antigravity_cli: "Antigravity (agy)",
  gemini: "Gemini",
  openai: "OpenAI",
  ollama: "Ollama",
};

const REASONS = {
  not_installed: "No está instalado.",
  disabled: "No está habilitado.",
  notice_not_accepted: "Falta aceptar el aviso de agy.",
  session_missing: "Falta iniciar sesión.",
  key_missing: "Falta configurar la clave.",
  key_invalid: "Clave inválida.",
  no_access: "Sin acceso.",
  limit_reached: "Límite alcanzado · sin hora conocida",
  status_unknown: "No se pudo comprobar la conexión.",
  session_unverifiable: "No se puede comprobar la sesión sin gastar cuota.",
  reachability_unknown: "El acceso no se prueba sin gastar.",
  unreachable: "No responde.",
  tls_certificate: "No se pudo verificar el certificado TLS.",
  quota_paused: "Límite alcanzado · sin hora conocida",
  quota_below_pause_floor: "Queda menos del 10 % de cuota.",
  quota_below_launch_floor: "Queda menos del 15 % de cuota.",
  usage_unavailable: "No se pudo comprobar la cuota.",
};

export const AGY_NOTICE =
  "Usa tu suscripción de agy. Revisa las condiciones de tu plan y mantén desactivados los créditos de IA. " +
  "Respeta la cuota que muestra agy /usage; no uses cuentas compartidas o de terceros.";

export const EXECUTOR_ENABLE_NOTICE =
  "Habilitar un ejecutor permite que el framework lance esta app bajo tu supervisión y usando tu suscripción. " +
  "El visor nunca modifica estos archivos por ti.";

export function quotaLines(quota) {
  return formatQuotaLines(quota, { emptyWhenMissing: true });
}

function installedLabel(row, kind) {
  if (kind === "model") return row.id === "ollama" ? "Local" : "No aplica (API)";
  if (row.installed === true) return "Sí";
  if (row.installed === false) return "No";
  return "Desconocido";
}

function sessionLabel(row, kind) {
  if (kind === "model") {
    if (row.id === "ollama") {
      if (row.reachable === true) return "Responde";
      if (row.reachable === false) return "No responde";
      return "Desconocido";
    }
    const setting = row.key_setting || (row.id === "gemini" ? "GEMINI_API_KEY" : "OPENAI_API_KEY");
    const file = row.key_file || ".local/.env";
    return row.key_configured
      ? `Clave configurada (${setting} en ${file})`
      : `Falta clave (${setting} en ${file})`;
  }
  if (row.session === "signed_in") return row.id === "antigravity_cli" ? "Iniciada (por /usage)" : "Iniciada";
  if (row.session === "signed_out") return "No iniciada";
  if (row.id === "copilot_cli") return "No comprobable sin gastar cuota";
  if (row.id === "antigravity_cli") return "Desconocida (requiere /usage)";
  return "Desconocida";
}

function enabledLabel(row) {
  if (row.id === "antigravity_cli") {
    if (row.enabled && row.notice_accepted) return "Habilitado y aviso aceptado";
    if (row.enabled && !row.notice_accepted) return "En catálogo, pero falta aceptar el aviso";
    return "Deshabilitado";
  }
  return row.enabled ? "Habilitado" : "Deshabilitado";
}

export function permissionSummary(id, perm, kind) {
  if (kind === "model") return "No aplica";
  if (!perm) return "Sin información de plantillas";
  if (perm.mode === "template") {
    if (perm.active) {
      return `Activa en ${perm.active_path}` + (perm.local_present ? ` (con ${perm.local_path})` : "");
    }
    return `Inactiva · plantilla en ${perm.template_path} (guía: ${perm.guide})`;
  }
  if (perm.mode === "per_run") return `Opciones por ejecución (guía: ${perm.guide})`;
  if (perm.mode === "supervised_modes") return `Modos supervisados (--mode plan / --mode auto; guía: ${perm.guide})`;
  return "Sin datos";
}

function consoleAction(row, step, title, detail, setupStatus, { optional = false } = {}) {
  const plan = setupStatus?.plans?.[row.id]?.[step] || null;
  const commandText = Array.isArray(plan?.command) && plan.command.length ? plan.command.join(" ") : null;
  return {
    kind: "console",
    step,
    optional,
    title,
    detail,
    commandText,
    source: plan?.source || null,
    sourceDate: plan?.source_date || null,
  };
}

export function missingAction(row, kind, setupStatus) {
  if (row.available === true) return null;
  const step = row.missing_step;
  if (step === "install") {
    if (row.id === "antigravity_cli") {
      return {
        kind: "info",
        title: "Falta instalar Antigravity (agy)",
        detail: "Instala agy desde su distribución oficial y asegúrate de que esté en el PATH. Luego pulsa «Volver a comprobar».",
      };
    }
    return consoleAction(
      row,
      "install",
      "Falta instalarlo",
      "Abre una consola visible con el comando oficial y pulsa Enter dentro de la consola para ejecutarlo.",
      setupStatus,
    );
  }
  if (step === "enable") {
    if (kind === "executor") {
      const isAgy = row.id === "antigravity_cli";
      const catalogLines = `executor_routing:\n  executors:\n    ${row.id}:\n      enabled: true`;
      const envLines = isAgy ? "ANTIGRAVITY_NOTICE_ACCEPTED=agy-third-party-notice/1" : null;
      const copyText = envLines ? `${catalogLines}\n\n# En .local/.env:\n${envLines}` : catalogLines;
      return {
        kind: "enable_executor",
        title: isAgy && !row.notice_accepted ? "Falta habilitarlo y aceptar el aviso" : "Falta habilitarlo",
        notice: isAgy ? AGY_NOTICE : EXECUTOR_ENABLE_NOTICE,
        catalogFile: "catalog/models.yaml",
        catalogLines,
        envFile: envLines ? ".local/.env" : null,
        envLines,
        copyText,
      };
    }
    return {
      kind: "enable_model",
      title: "Falta habilitarlo",
      detail: "Añade esta línea en .local/.env, reinicia la aplicación y pulsa «Volver a comprobar».",
      file: ".local/.env",
      line: "OPENAI_ENABLED=true",
      copyText: "OPENAI_ENABLED=true",
    };
  }
  if (step === "session") {
    if (row.id === "antigravity_cli") {
      return {
        kind: "info",
        title: "Falta iniciar sesión",
        detail: "Inicia sesión tú en agy desde tu terminal; su sesión no se puede comprobar sin gastar. Luego pulsa «Volver a comprobar».",
      };
    }
    return consoleAction(
      row,
      "login",
      "Falta iniciar sesión",
      "Abre una consola visible para iniciar sesión en la app y luego vuelve a comprobar.",
      setupStatus,
    );
  }
  if (step === "key") {
    const setting = row.key_setting || (row.id === "gemini" ? "GEMINI_API_KEY" : "OPENAI_API_KEY");
    const file = row.key_file || ".local/.env";
    return {
      kind: "key",
      title: "Falta configurar la clave",
      setting,
      file,
      detail: `Añade ${setting} en ${file}, reinicia la aplicación y pulsa «Volver a comprobar». Nunca escribas la clave aquí.`,
    };
  }
  if (row.id === "copilot_cli" && row.reason === "session_unverifiable") {
    return consoleAction(
      row,
      "login",
      "Sesión sin comprobar",
      "No se puede comprobar la sesión de Copilot sin gastar cuota. Si aún no iniciaste sesión, puedes abrir una consola para hacerlo.",
      setupStatus,
      { optional: true },
    );
  }
  if (row.reason === "reachability_unknown") {
    return {
      kind: "info",
      title: "Acceso sin comprobar",
      detail: "La clave está configurada; el acceso no se prueba sin gastar cuota.",
    };
  }
  if (row.reason === "tls_certificate") {
    return {
      kind: "info",
      title: REASONS.tls_certificate,
      detail:
        "No se pudo verificar el certificado TLS. Un antivirus o un proxy puede estar cambiando los certificados. " +
        "Si confías en este equipo, usa el botón de certificados del sistema. " +
        "En modo avanzado también puedes elegir tu propio archivo .pem con PROVIDER_TLS_TRUST=file:RUTA en .local/.env.",
    };
  }
  if (row.reason === "unreachable") {
    return {
      kind: "info",
      title: REASONS.unreachable,
      detail: "Ollama no respondió a su comprobación. Comprueba el servicio local y pulsa «Volver a comprobar».",
    };
  }
  if (row.reason === "key_invalid") {
    const setting = row.key_setting || (row.id === "gemini" ? "GEMINI_API_KEY" : "OPENAI_API_KEY");
    const file = row.key_file || ".local/.env";
    return {
      kind: "key",
      title: "Clave inválida",
      setting,
      file,
      detail: `El proveedor rechazó ${setting} en ${file}. Revisa la clave, reinicia la aplicación y pulsa «Volver a comprobar». Nunca escribas la clave aquí.`,
    };
  }
  if (row.reason === "no_access") {
    return {
      kind: "info",
      title: "Sin acceso",
      detail: "No se pudo conectar con el proveedor. Revisa tu conexión o certificados y pulsa «Volver a comprobar».",
    };
  }
  if (row.limit || row.reason === "limit_reached" || row.reason === "quota_paused") {
    const limitText = limitReachedText(row.limit || row.resets_at || row.paused_until);
    return {
      kind: "info",
      title: "Límite alcanzado",
      detail: `${limitText}. Después pulsa «Volver a comprobar».`,
    };
  }
  return {
    kind: "info",
    title: REASONS[row.reason] || "No disponible",
    detail: row.paused_until
      ? `En pausa hasta ${row.paused_until}. Después pulsa «Volver a comprobar».`
      : "Revisa el estado mostrado arriba y pulsa «Volver a comprobar».",
  };
}

function statusChipFor(row) {
  if (row.id === "gemini") {
    if (row.available === true) {
      return row.verification === "verified"
        ? { status: "connected", label: "Conectado" }
        : { status: "unverified", label: "Sin comprobar" };
    }
    if (row.reason === "key_invalid") return { status: "key_invalid", label: "Clave inválida" };
    if (row.reason === "no_access") return { status: "no_access", label: "Sin acceso" };
    if (row.reason === "limit_reached") return { status: "limit_reached", label: "Límite alcanzado" };
  }
  if (row.available !== true && (row.limit || row.reason === "limit_reached" || row.reason === "quota_paused")) {
    return { status: "limit_reached", label: "Límite alcanzado" };
  }
  if (row.available === true) return { status: "ok", label: "Listo" };
  if (row.missing_step) return { status: "waiting", label: "Falta un paso" };
  if (row.reason === "session_unverifiable" || row.reason === "reachability_unknown") {
    return { status: "unknown", label: "Sin verificar" };
  }
  return { status: "error", label: "No disponible" };
}

export function isPendingStep(row) {
  return Boolean(row && row.available !== true && row.missing_step);
}

export function missingSummaryLine(row, action, chip, reasonText) {
  if (!row || row.available === true) return null;
  if (action && action.title && action.title !== chip.label) return action.title;
  if (reasonText && reasonText !== chip.label) return reasonText;
  return null;
}

// ARR3-T07: the server decides when a card may offer the certificate button; here it is only read.
function tlsButton(button) {
  if (!button || typeof button.trust !== "string" || typeof button.label !== "string" || !button.label) return null;
  return { trust: button.trust, label: button.label };
}

function buildEntry(row, kind, setupStatus) {
  const perm = setupStatus?.permissions?.[row.id] || null;
  const chip = statusChipFor(row);
  const limited = row.available !== true && (Boolean(row.limit) || row.reason === "limit_reached" || row.reason === "quota_paused");
  const reasonText = row.available
    ? null
    : limited
      ? limitReachedText(row.limit || row.resets_at || row.paused_until)
      : (REASONS[row.reason] || row.reason || "No disponible.");
  const action = missingAction(row, kind, setupStatus);
  return {
    id: row.id,
    kind,
    label: LABELS[row.id] || row.id,
    available: row.available === true,
    defaultOpen: isPendingStep(row),
    missingLine: missingSummaryLine(row, action, chip, reasonText),
    chip,
    installed: installedLabel(row, kind),
    version: row.version || (kind === "model" && row.id !== "ollama" ? "No aplica" : "Sin dato"),
    session: sessionLabel(row, kind),
    enabled: enabledLabel(row),
    permissions: permissionSummary(row.id, perm, kind),
    quota: row.id === "gemini" ? ["La API de Gemini no informa la cuota."] : quotaLines(row.quota),
    warning: quotaWarningText(row.warning),
    canProbe: kind === "model",
    tlsFix: tlsButton(row.tls_fix),
    tlsRevert: tlsButton(row.tls_revert),
    reasonText,
    checkedAt: row.checked_at || null,
    action,
  };
}

export function buildAppsView(connectionsPayload, setupStatus = null) {
  const rows = Array.isArray(connectionsPayload?.connections) ? connectionsPayload.connections : [];
  const byId = new Map(rows.map((row) => [row.id, row]));
  const executors = EXECUTOR_IDS.map((id) => byId.get(id)).filter(Boolean).map((row) => buildEntry(row, "executor", setupStatus));
  const models = MODEL_IDS.map((id) => byId.get(id)).filter(Boolean).map((row) => buildEntry(row, "model", setupStatus));
  return {
    executors,
    models,
    allowLaunch: setupStatus ? Boolean(setupStatus.allow_launch) : true,
    consoleSupported: setupStatus ? Boolean(setupStatus.console_supported) : true,
    empty: executors.length === 0 && models.length === 0,
  };
}
