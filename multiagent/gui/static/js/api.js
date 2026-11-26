// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Calls to /gui/api with the local token. POST bodies are always JSON.
import { authHeaders } from "./auth.js";
import { t } from "./i18n.js";

export class ApiError extends Error {
  constructor({ status = 0, code = "network_error", message = "", details = {} }) {
    super(message || code);
    this.status = status;
    this.code = code;
    this.details = details || {};
  }
}

async function request(method, path, token, body) {
  const headers = authHeaders(token);
  const options = { method, headers, cache: "no-store" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body === undefined ? {} : body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (_) {
    throw new ApiError({ code: "network_error", message: t("shell.local_server_unreachable") });
  }
  let payload = null;
  try { payload = await response.json(); } catch (_) { /* empty or non-JSON body */ }
  if (!response.ok) {
    const detail = payload && typeof payload.detail === "object" && payload.detail ? payload.detail : {};
    throw new ApiError({
      status: response.status,
      code: detail.code || "http_" + response.status,
      message: detail.message || (typeof payload?.detail === "string" ? payload.detail : "HTTP " + response.status),
      details: detail.details || {},
    });
  }
  return payload;
}

export const apiGet = (path, token) => request("GET", path, token);
export const apiPost = (path, token, body) => request("POST", path, token, body);
export const apiPut = (path, token, body) => request("PUT", path, token, body);

export const runPath = (runId, action = "") =>
  "/gui/api/runs/" + encodeURIComponent(runId) + (action ? "/" + action : "");
