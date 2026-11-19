// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Texts of the runs screen (Spanish). The server sends codes and facts; the wording lives here.

import { labelEntry, labelSet, legacyInfo } from "./ui/labels.js";

export const MODE_LABELS = {
  auto: "Automático (el framework elige el modelo)",
  local: "Modelo local",
  cloud: "Modelo en la nube",
  human_guided: "Guiado por una persona (copiar y pegar en otro modelo)",
};

// The words of every status live in ui/labels.js (the single label map); these are the {label, tone, help} views of it.
export const statusInfo = (status) => legacyInfo("run", status);

export const stepStateLabel = (state) => labelEntry(labelSet("step"), state).text;

export function skipReason(skip) {
  const condition = skip && skip.condition;
  if (condition === "requires_web") {
    return "porque esta ejecución no pide búsqueda web (condición «requires_web»)";
  }
  if (condition === "not_requires_web") {
    return "porque esta ejecución sí pide búsqueda web (condición «not_requires_web»)";
  }
  if (condition) return "porque no se cumple la condición «" + condition + "»";
  return "el motivo no quedó registrado";
}

export function executorText(executor) {
  if (!executor || !executor.kind) return "";
  if (executor.kind === "human") {
    const model = executor.model ? ", modelo: " + executor.model : "";
    return "Lo hizo una persona o un modelo externo (proveedor declarado: " + (executor.provider || "sin indicar") + model + ")";
  }
  const parts = [executor.model, executor.provider].filter(Boolean);
  return "Lo hizo el modelo " + (parts.join(" · ") || "sin identificar");
}

export function waitingText(waiting) {
  if (!waiting) return "";
  const step = waiting.step_id ? "El paso «" + waiting.step_id + "»" + (waiting.attempt ? " (intento " + waiting.attempt + ")" : "") : "La ejecución";
  switch (waiting.reason) {
    case "review": return step + " espera tu revisión: aprueba, pide cambios o recházalo.";
    case "budget_exhausted":
      return "Se agotó el presupuesto. " + step + " tiene un resultado que puedes aprobar o rechazar; ya no se pueden pedir cambios.";
    case "execute_step":
      return step + " lo tiene que hacer una persona o un modelo externo: copia el prompt, pégalo en tu herramienta y trae la respuesta.";
    case "research":
      return step + " pide una investigación externa: copia el prompt, hazla con tu herramienta y pega la respuesta.";
    default: return step + " espera una acción humana.";
  }
}

export function cursorText(view) {
  if (!view.definition_available) {
    return "No se pudo leer la definición actual del workflow; los pasos se muestran según los eventos de la ejecución.";
  }
  if (view.cursor.finished) return "Todos los pasos terminaron.";
  if (view.terminal) return "La ejecución terminó antes de recorrer todos los pasos.";
  return "Va por el paso " + (view.cursor.index + 1) + " de " + view.cursor.total + ".";
}

export function historyText(entry) {
  const step = entry.step_id ? "«" + entry.step_id + "»" + (entry.attempt ? " (intento " + entry.attempt + ")" : "") : "";
  switch (entry.kind) {
    case "prompt_delivered": return "Se entregó el prompt del paso " + step + " para que lo ejecutes fuera del framework.";
    case "response_submitted": {
      const model = entry.model ? ", modelo " + entry.model : ", sin modelo indicado";
      return "Se pegó la respuesta del paso " + step + ". Proveedor declarado: " + (entry.provider || "sin indicar") + model + ".";
    }
    case "response_rejected": return "Se rechazó la respuesta pegada para el paso " + step + " porque no cumple el contrato.";
    case "approved": return "Se aprobó el resultado del paso " + step + ".";
    case "changes_requested": return "Se pidieron cambios sobre el paso " + step + ".";
    case "regenerated": return "Se pidió repetir el paso " + step + " sin comentarios.";
    case "rejected": return "Se rechazó la ejecución.";
    case "cancel_requested": return "Se pidió cancelar; se detendrá cuando termine el paso en curso.";
    case "cancelled": return entry.forced ? "Se canceló la ejecución de inmediato." : "La ejecución quedó cancelada.";
    default: return entry.kind;
  }
}

export function actorText(entry) {
  if (entry.actor === "system") return "Sistema";
  if (entry.actor === "declared") return "Persona o modelo externo (declarado, sin verificar)";
  if (entry.actor === "unknown") return "Sin dato";
  // ARR-T04: an approval shows who its event says gave it and the door it came through; the text is declared, not verified.
  if (entry.actor && entry.actor !== "operator" && entry.actor !== "user:operator") {
    return entry.actor + (entry.channel ? " · " + entry.channel : "");
  }
  return entry.channel ? "Operador · " + entry.channel : "Operador";
}

// Example shown as placeholder text; a choice field has no free text, so it shows none.
export function exampleText(item) {
  return item.example && item.type !== "choice" ? "Ej.: " + item.example : null;
}

// Where the form's inputs come from, in one sentence.
export function inputsNote(spec) {
  const derived = spec.inputs.filter((item) => item.source !== "declared").length;
  if (spec.inputs.length === 0) return "Este workflow no pide entradas.";
  if (derived === 0) return "Las entradas las declara el propio workflow.";
  const rule = "Se deducen de los marcadores {nombre} de los prompts de esos pasos y se muestra la línea del prompt donde aparecen.";
  if (derived === spec.inputs.length) return "Este workflow no declara sus entradas. " + rule;
  return "Las entradas sin descripción no las declara el workflow. " + rule;
}

export function inputHelp(item) {
  const parts = [];
  if (item.skipped_only) {
    parts.push("Solo la usa" + (item.used_in.length > 1 ? "n" : "") + " el paso «" + item.used_in.join("», «") +
      "», que no se ejecuta con esta configuración; por eso es opcional.");
  } else if (item.used_in.length) {
    parts.push("Se usa en: " + item.used_in.join(", ") + ".");
  }
  // A declared input explains itself (description and example); the prompt line is for the ones that do not.
  if (item.source !== "declared" && item.prompt_line) parts.push("En el prompt: «" + item.prompt_line + "».");
  return parts.join(" ");
}

export function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("es");
}

export function fmtJson(value) {
  try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); }
}

export function errorText(error) {
  const details = error.details || {};
  if (details.missing_inputs && details.missing_inputs.length) {
    return "Faltan entradas obligatorias: " + details.missing_inputs.join(", ") + ".";
  }
  switch (error.code) {
    case "unauthorized":
      return "El token de acceso ya no es válido. Vuelve a abrir el enlace que imprime python -m multiagent.gui.";
    case "network_error": return "No se pudo contactar con el servidor local. Tus datos siguen en pantalla.";
    case "invalid_state":
    case "invalid_operation":
      return "La ejecución cambió y esa acción ya no es posible (" + error.message + "). Se actualizó la vista.";
    case "budget_exceeded": return "El presupuesto está agotado y esa acción ya no es posible: " + error.message;
    case "human_submission_invalid": return "La respuesta no cumple el contrato esperado: " + error.message;
    case "human_step_unavailable": return "La ejecución ya no espera un paso hecho por una persona.";
    case "not_found": return "No se encontró lo que se pidió: " + error.message;
    case "idempotency_conflict": return "Ya existe una ejecución con esa clave y datos distintos. Cambia los datos y reintenta.";
    default: {
      const issues = [];
      for (const item of details.errors || []) if (item && item.msg) issues.push(item.msg);
      for (const issue of details.issues || []) if (issue && issue.message) issues.push(issue.message);
      return error.message + (issues.length ? " — " + issues.join("; ") : "") + " (" + error.code + ")";
    }
  }
}
