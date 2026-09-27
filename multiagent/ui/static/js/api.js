// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
const API_ROOT = "/api/v1";

export class ApiError extends Error {
  constructor({ status, code, message, details = {} }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request(path, { method = "GET", body } = {}) {
  const options = {
    method,
    headers: { Accept: "application/json" },
  };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(`${API_ROOT}${path}`, options);
  } catch (error) {
    throw new ApiError({
      status: 0,
      code: "network_error",
      message: "The API could not be reached. Check that the local server is running.",
      details: { cause: error instanceof Error ? error.message : String(error) },
    });
  }

  const contentType = response.headers.get("content-type") || "";
  let payload;
  if (contentType.includes("application/json")) {
    try {
      payload = await response.json();
    } catch {
      throw new ApiError({
        status: response.status,
        code: "invalid_response",
        message: "The API returned invalid JSON.",
      });
    }
  } else {
    payload = {};
  }

  if (!response.ok) {
    const error = payload?.detail;
    throw new ApiError({
      status: response.status,
      code: error?.code || "http_error",
      message: error?.message || `Request failed with HTTP ${response.status}.`,
      details: error?.details || {},
    });
  }
  if (!contentType.includes("application/json")) {
    throw new ApiError({
      status: response.status,
      code: "invalid_response",
      message: "The API response was not JSON.",
    });
  }
  return payload;
}

export function apiGet(path) {
  return request(path);
}

export function apiPost(path, body) {
  return request(path, { method: "POST", body });
}

export function getHealth() {
  return apiGet("/health");
}

export function getBundles() {
  return apiGet("/bundles");
}

export function getWorkflows(bundle = "") {
  const query = bundle ? `?${new URLSearchParams({ bundle })}` : "";
  return apiGet(`/workflows${query}`);
}

export function getWorkflow(workflowId, bundle = "") {
  const query = bundle ? `?${new URLSearchParams({ bundle })}` : "";
  return apiGet(`/workflows/${encodeURIComponent(workflowId)}${query}`);
}

export function createRun(requestBody, bundle = "") {
  const query = bundle ? `?${new URLSearchParams({ bundle })}` : "";
  return apiPost(`/runs${query}`, requestBody);
}
