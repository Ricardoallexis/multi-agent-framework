// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// ARR3-T06: pure helpers of the result document card. No DOM, no network: the download is built from what the caller gives.
export const SAVE_LABEL = "Guardar como Markdown";
export const MARKDOWN_TYPE = "text/markdown;charset=utf-8";
export const FALLBACK_FILENAME = "documento.md";

// The 409 reasons that mean "this run has no document (yet)": the card is simply not shown.
const NO_DOCUMENT_REASONS = ["run_not_completed", "no_result"];

export function documentFilename(payload) {
  const name = payload && typeof payload.filename === "string" ? payload.filename.trim() : "";
  return name || FALLBACK_FILENAME;
}

export function markdownBlob(text) {
  return new Blob([typeof text === "string" ? text : ""], { type: MARKDOWN_TYPE });
}

// "ready": show the card; "none": no card and no error (the run has no document); "error": the read failed.
export function documentState(payload, error) {
  if (error) {
    const reason = error.details && error.details.reason;
    return error.status === 409 && NO_DOCUMENT_REASONS.includes(reason) ? "none" : "error";
  }
  return payload && typeof payload.markdown === "string" && payload.markdown !== "" ? "ready" : "none";
}

// Builds the file in the browser: a Blob, a temporary object URL and a click on a link with `download`.
// `doc` and `urls` are the page's document and URL; tests pass fakes.
export function downloadMarkdown(payload, doc, urls) {
  const url = urls.createObjectURL(markdownBlob(payload.markdown));
  try {
    const link = doc.createElement("a");
    link.href = url;
    link.download = documentFilename(payload);
    link.rel = "noopener";
    doc.body.appendChild(link);
    link.click();
    doc.body.removeChild(link);
  } finally {
    urls.revokeObjectURL(url);
  }
}
