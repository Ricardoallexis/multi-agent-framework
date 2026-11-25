// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Activar» of an executor (EJE-T02): the calls of the GUI to the routes of EJE-T01 and to the one-use code of the approvals.
// It does not import the «approvals» extension: the few paths it needs are copied here. ``post(path, body)`` is injected (the
// view passes ``apiPost`` with the token; the tests pass a fake that notes the routes), so nothing here touches the network.
//
// The browser takes the approvals session when the page loads (js/auth.js, from the start link); this module cannot repeat that
// step, so it asks for the challenge directly. Without that session the answer says so.
import "./i18n.js";
import { t } from "../../js/i18n.js";
import { executorName, stopAllConfirmText, stopAllText } from "./activation-model.js";

const GUI = "/gui/api";
const executorPath = (executorId, action) => `${GUI}/executors/${encodeURIComponent(executorId)}/${action}`;
const approvalPath = (approvalId, action) => `${GUI}/approvals/${encodeURIComponent(approvalId)}/${action}`;
export const STOP_ALL_PATH = `${GUI}/executors/stop-all`;

const details = (failure) => (failure && typeof failure.details === "object" && failure.details ? failure.details : {});
const reasonOf = (failure) => details(failure).reason || null;

const SESSION_UNAVAILABLE = () => t("ext.apps.activation.session_unavailable");
const EXPIRED_CODE = () => t("ext.apps.activation.expired_code");

function failureResult(failure, name) {
  const reason = reasonOf(failure);
  const cause = details(failure).cause;
  if (reason === "approval_session_unavailable" || cause === "missing_or_invalid_session" || failure?.code === "approval_session_unavailable") {
    return { ok: false, retryable: false, restart: false, message: SESSION_UNAVAILABLE() };
  }
  if (cause === "invalid_or_expired_code" || failure?.code === "invalid_or_expired_code") {
    return { ok: false, retryable: true, restart: true, message: EXPIRED_CODE() };
  }
  if (reason === "not_installed") {
    return { ok: false, retryable: false, restart: false, message: t("ext.apps.activation.not_installed", { name }) };
  }
  if (reason === "no_session") {
    return { ok: false, retryable: false, restart: false, message: t("ext.apps.activation.no_session", { name }) };
  }
  if (reason === "executor_paused_changed") {
    return {
      ok: false, retryable: true, restart: true,
      message: t("ext.apps.activation.changed_before_click", { name }),
    };
  }
  if (reason === "person_required") {
    return { ok: false, retryable: true, restart: true, message: t("ext.apps.activation.person_required") };
  }
  if (reason === "not_active") {
    return { ok: false, retryable: false, restart: false, message: t("ext.apps.activation.not_active", { name }), refresh: true };
  }
  if (failure && failure.status === 409) {
    return { ok: false, retryable: true, restart: true, message: t("ext.apps.activation.not_pending") };
  }
  return { ok: false, retryable: false, restart: false, message: (failure && failure.message) || t("ext.apps.activation.action_failed") };
}

// Step 1, when the person presses «Activar»: the server makes the approval and gives the fixed note. Nothing is decided here.
export async function requestActivation({ executorId, post }) {
  if (typeof post !== "function") throw new Error("Missing post function");
  try {
    const request = await post(executorPath(executorId, "activation"), {});
    if (!request || typeof request.approval_id !== "string" || !request.approval_id) {
      return { ok: false, retryable: true, restart: false, message: t("ext.apps.activation.note_missing") };
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
      return { ok: false, retryable: true, restart: false, message: t("ext.apps.activation.note_missing") };
    }
    const challenge = await post(approvalPath(approvalId, "challenge"), {});
    if (!challenge || typeof challenge.code !== "string" || !challenge.code) {
      return { ok: false, retryable: true, restart: true, message: t("ext.apps.activation.code_missing") };
    }
    if (note.sha256 && challenge.sha256 && note.sha256 !== challenge.sha256) {
      return { ok: false, retryable: true, restart: true, message: t("ext.apps.activation.changed_after_note", { name }) };
    }
    await post(approvalPath(approvalId, "decision"), { code: challenge.code, decision: "approve" });
    const row = await post(executorPath(executorId, "activate"), { approval_id: approvalId });
    return { ok: true, message: t("ext.apps.activation.activated", { name }), row };
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
    return { ok: true, message: t("ext.apps.activation.deactivated", { name }), row };
  } catch (failure) {
    return failureResult(failure, name);
  }
}

// «Apagar todos»: asks ``confirm(STOP_ALL_CONFIRM)`` first; only a true answer reaches the route.
export async function stopAllExecutors({ token, post, confirm }) {
  if (typeof post !== "function") throw new Error("Missing post function");
  if (typeof confirm !== "function" || (await confirm(stopAllConfirmText())) !== true) {
    return { ok: false, cancelled: true, message: "" };
  }
  try {
    const result = await post(STOP_ALL_PATH, {});
    return { ok: true, message: stopAllText(result), result };
  } catch (failure) {
    return failureResult(failure, t("ext.apps.activation.executors"));
  }
}
