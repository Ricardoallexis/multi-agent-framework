// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Activar» of an executor (EJE-T02): the calls of the GUI to the routes of EJE-T01 and to the one-use code of the approvals.
// It does not import the «approvals» extension: the few paths it needs are copied here. ``post(path, body)`` is injected (the
// view passes ``apiPost`` with the token; the tests pass a fake that notes the routes), so nothing here touches the network.
//
// The browser takes the approvals session when the page loads (js/auth.js, from the start link); this module cannot repeat that
// step, so it asks for the challenge directly. Without that session the answer says so.
import { STOP_ALL_CONFIRM, executorName, stopAllText } from "./activation-model.js";

const GUI = "/gui/api";
const executorPath = (executorId, action) => `${GUI}/executors/${encodeURIComponent(executorId)}/${action}`;
const approvalPath = (approvalId, action) => `${GUI}/approvals/${encodeURIComponent(approvalId)}/${action}`;
export const STOP_ALL_PATH = `${GUI}/executors/stop-all`;

const details = (failure) => (failure && typeof failure.details === "object" && failure.details ? failure.details : {});
const reasonOf = (failure) => details(failure).reason || null;

const SESSION_UNAVAILABLE =
  "Las aprobaciones no están activas en esta pestaña. Abre la GUI con el enlace que te dio el arranque o reinicia la GUI, y vuelve a pulsar «Activar».";
const EXPIRED_CODE = "El código de un solo uso venció. Vuelve a pulsar «Activar» para intentarlo de nuevo.";

function failureResult(failure, name) {
  const reason = reasonOf(failure);
  const cause = details(failure).cause;
  if (reason === "approval_session_unavailable" || cause === "missing_or_invalid_session" || failure?.code === "approval_session_unavailable") {
    return { ok: false, retryable: false, restart: false, message: SESSION_UNAVAILABLE };
  }
  if (cause === "invalid_or_expired_code" || failure?.code === "invalid_or_expired_code") {
    return { ok: false, retryable: true, restart: true, message: EXPIRED_CODE };
  }
  if (reason === "not_installed") {
    return { ok: false, retryable: false, restart: false, message: `${name} no está instalado.` };
  }
  if (reason === "no_session") {
    return { ok: false, retryable: false, restart: false, message: `${name} está instalado, pero falta iniciar sesión.` };
  }
  if (reason === "executor_paused_changed") {
    return {
      ok: false, retryable: true, restart: true,
      message: `${name} cambió entre la nota y tu clic. Vuelve a empezar para leer la nota de nuevo.`,
    };
  }
  if (reason === "person_required") {
    return { ok: false, retryable: true, restart: true, message: "Hace falta tu aprobación en la GUI. Vuelve a empezar." };
  }
  if (reason === "not_active") {
    return { ok: false, retryable: false, restart: false, message: `${name} ya estaba desactivado.`, refresh: true };
  }
  if (failure && failure.status === 409) {
    return { ok: false, retryable: true, restart: true, message: "La solicitud ya no está pendiente. Vuelve a empezar." };
  }
  return { ok: false, retryable: false, restart: false, message: (failure && failure.message) || "No se pudo completar la acción." };
}

// Step 1, when the person presses «Activar»: the server makes the approval and gives the fixed note. Nothing is decided here.
export async function requestActivation({ executorId, post }) {
  if (typeof post !== "function") throw new Error("Missing post function");
  try {
    const request = await post(executorPath(executorId, "activation"), {});
    if (!request || typeof request.approval_id !== "string" || !request.approval_id) {
      return { ok: false, retryable: true, restart: false, message: "El servidor no entregó la nota de activación." };
    }
    return { ok: true, request };
  } catch (failure) {
    return failureResult(failure, executorName(executorId));
  }
}

// The person pressed the button of the note («Activar Codex»): challenge -> decision (approve) -> activate. Give ``request`` (the
// answer of ``requestActivation`` that showed the note) to decide exactly that note; without it the request is made first.
export async function activateExecutor({ token, executorId, post, request = null }) {
  if (typeof post !== "function") throw new Error("Missing post function");
  const name = executorName(executorId);
  try {
    const note = request || (await post(executorPath(executorId, "activation"), {}));
    const approvalId = note && note.approval_id;
    if (typeof approvalId !== "string" || !approvalId) {
      return { ok: false, retryable: true, restart: false, message: "El servidor no entregó la nota de activación." };
    }
    const challenge = await post(approvalPath(approvalId, "challenge"), {});
    if (!challenge || typeof challenge.code !== "string" || !challenge.code) {
      return { ok: false, retryable: true, restart: true, message: "El servidor no entregó un código de un solo uso válido." };
    }
    if (note.sha256 && challenge.sha256 && note.sha256 !== challenge.sha256) {
      return { ok: false, retryable: true, restart: true, message: `${name} cambió después de la nota. Vuelve a empezar.` };
    }
    await post(approvalPath(approvalId, "decision"), { code: challenge.code, decision: "approve" });
    const row = await post(executorPath(executorId, "activate"), { approval_id: approvalId });
    return { ok: true, message: `${name} quedó activado.`, row };
  } catch (failure) {
    return failureResult(failure, name);
  }
}

// «Desactivar»: at once, without a note. Turning off needs no approval.
export async function deactivateExecutor({ token, executorId, post }) {
  if (typeof post !== "function") throw new Error("Missing post function");
  const name = executorName(executorId);
  try {
    const row = await post(executorPath(executorId, "deactivate"), {});
    return { ok: true, message: `${name} quedó desactivado.`, row };
  } catch (failure) {
    return failureResult(failure, name);
  }
}

// «Apagar todos»: asks ``confirm(STOP_ALL_CONFIRM)`` first; only a true answer reaches the route.
export async function stopAllExecutors({ token, post, confirm }) {
  if (typeof post !== "function") throw new Error("Missing post function");
  if (typeof confirm !== "function" || (await confirm(STOP_ALL_CONFIRM)) !== true) {
    return { ok: false, cancelled: true, message: "" };
  }
  try {
    const result = await post(STOP_ALL_PATH, {});
    return { ok: true, message: stopAllText(result), result };
  } catch (failure) {
    return failureResult(failure, "los ejecutores");
  }
}
