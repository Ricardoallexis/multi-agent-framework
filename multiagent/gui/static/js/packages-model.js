// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { t, onLanguageChange } from "./i18n.js";
// «Paquetes» (PAQ-T02), pure part: the rows of the list, the body of an install, the name taken from a file and the texts. No DOM
// and no network, so Node tests it. A row never carries a path: the origin is the `source_label` the server keeps (the name of the
// file the person chose), and what the server sends as `path` is not part of the payload at all.

export const ZIP_UPLOAD_MAX_BYTES = 25 * 2 ** 20;   // the same cap as the server (installed.py)
export const STATE_TEXT = { get ok() { return t("packages.ready"); }, get changed() { return t("packages.changed"); },
  get missing() { return t("packages.missing"); } };
export let REMOVE_NOTICE = t("packages.remove_notice");
export let REINSTALL_LABEL = t("packages.reinstall");
export let REMOVE_LABEL = t("packages.remove");
onLanguageChange(() => {
  REMOVE_NOTICE = t("packages.remove_notice");
  REINSTALL_LABEL = t("packages.reinstall");
  REMOVE_LABEL = t("packages.remove");
});
export const LEVEL_TEXT = { get data() { return t("packages.data_only"); }, get code() { return t("packages.with_code"); } };
const NAME_LIMIT = 120;

// "Mi paquete (1).zip" -> "Mi-paquete--1-": without ".zip", every character outside the bundle-name alphabet becomes "-", at
// most 120 characters, and the first one is a letter, a digit or "_" (the server's _BUNDLE_NAME).
export function packageNameFromFile(filename) {
  const base = String(filename || "").replace(/^.*[\\/]/, "").replace(/\.zip$/i, "");
  const name = base.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, NAME_LIMIT).replace(/^-+/, "");
  return name || "paquete";
}

export function zipBody(name, filename, base64, trustFunctions) {
  return { name: String(name || ""), filename: String(filename || "").replace(/^.*[\\/]/, "").slice(0, NAME_LIMIT),
           zip_base64: String(base64 || ""), trust_functions: Boolean(trustFunctions) };
}

export function folderBody(name, folder, trustFunctions) {
  return { name: String(name || ""), folder: String(folder || "").trim(), trust_functions: Boolean(trustFunctions) };
}

// The bytes of a file as base64, in slices so a 25 MB file does not need one giant argument list.
export function bytesToBase64(bytes) {
  let text = "";
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  for (let at = 0; at < view.length; at += 0x8000) text += String.fromCharCode(...view.subarray(at, at + 0x8000));
  return btoa(text);
}

// The «Confiar en las funciones de este paquete» box: advanced mode, and a package that has code (L3).
export const trustBoxVisible = (mode, level) => mode === "advanced" && level === "code";

// The rows of the list. `payload` is GET /gui/api/packages; `mode` the mode of the page.
export function packageRows(payload, mode) {
  const list = payload && Array.isArray(payload.packages) ? payload.packages : [];
  return list.map((item) => {
    const state = item.state || "ok";
    const copy = (item.source || "copy") === "copy";
    const functions = item.functions || {};
    return {
      name: item.name,
      levelText: LEVEL_TEXT[item.level] || "",
      versionText: item.version ? "v" + item.version : "",
      origin: item.source_label || (item.source === "shipped" ? t("packages.shipped_origin") : t("packages.no_origin")),
      state,
      stateText: STATE_TEXT[state] || state,
      degraded: state !== "ok",
      canReinstall: state !== "ok" && copy,      // only a copy can be replaced; the example has nothing to replace
      reinstallLabel: REINSTALL_LABEL,
      removeLabel: REMOVE_LABEL,
      showTrust: trustBoxVisible(mode, item.level),
      functionsText: trustBoxVisible(mode, item.level) && functions.present
        ? (functions.trusted ? t("packages.trusted") : t("packages.untrusted")) : "",
    };
  });
}

// What the server answered, in plain words. `error` is an ApiError (code, message, details).
const REASON_TEXT = {
  zip_unsafe_entry: t("packages.zip_unsafe"),
  zip_invalid: t("packages.zip_invalid"),
  zip_too_large: t("packages.zip_too_large"),
  example_not_available: t("packages.example_unavailable"),
  bundle_name_in_use: t("packages.name_in_use"),
  bundle_busy: t("packages.busy"),
  bundle_not_installed: t("packages.not_installed"),
  installed_bundle_changed: t("packages.installed_changed"),
  copy_fingerprint_mismatch: t("packages.copy_mismatch"),
  links_not_allowed: t("packages.links_disallowed"),
};
const ACTION_TEXT = {
  install_code: t("packages.install_code_advanced"),
  install_folder: t("packages.install_folder_advanced"),
  trust_functions: t("packages.trust_advanced"),
};

export function packageErrorText(error) {
  const details = (error && error.details) || {};
  if (details.reason === "advanced_mode_required") return ACTION_TEXT[details.action] || t("packages.action_advanced");
  if (REASON_TEXT[details.reason]) return REASON_TEXT[details.reason];
  if (error && error.status === 413) return REASON_TEXT.zip_too_large;
  return t("packages.action_failed");
}
