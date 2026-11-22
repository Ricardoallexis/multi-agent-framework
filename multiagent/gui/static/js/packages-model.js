// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Paquetes» (PAQ-T02), pure part: the rows of the list, the body of an install, the name taken from a file and the texts. No DOM
// and no network, so Node tests it. A row never carries a path: the origin is the `source_label` the server keeps (the name of the
// file the person chose), and what the server sends as `path` is not part of the payload at all.

export const ZIP_UPLOAD_MAX_BYTES = 25 * 2 ** 20;   // the same cap as the server (installed.py)
export const STATE_TEXT = { ok: "Listo", changed: "Cambió desde que se instaló", missing: "Falta la copia instalada" };
export const REMOVE_NOTICE = "Se quita de la lista. Se conservan sus datos";
export const REINSTALL_LABEL = "Reinstalar desde un archivo";
export const REMOVE_LABEL = "Quitar";
export const LEVEL_TEXT = { data: "Solo datos", code: "Con código" };
const NAME_LIMIT = 120;
const SHIPPED_ORIGIN = "Paquete de ejemplo del producto";

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
      origin: item.source_label || (item.source === "shipped" ? SHIPPED_ORIGIN : "Sin origen"),
      state,
      stateText: STATE_TEXT[state] || state,
      degraded: state !== "ok",
      canReinstall: state !== "ok" && copy,      // only a copy can be replaced; the example has nothing to replace
      reinstallLabel: REINSTALL_LABEL,
      removeLabel: REMOVE_LABEL,
      showTrust: trustBoxVisible(mode, item.level),
      functionsText: trustBoxVisible(mode, item.level) && functions.present
        ? (functions.trusted ? "Sus funciones son de confianza" : "Sus funciones no están confiadas") : "",
    };
  });
}

// What the server answered, in plain words. `error` is an ApiError (code, message, details).
const REASON_TEXT = {
  zip_unsafe_entry: "El archivo .zip trae una ruta que no se permite, así que no se instaló nada.",
  zip_invalid: "Ese archivo no es un .zip válido.",
  zip_too_large: "El archivo es demasiado grande: 25 MB como máximo, y 50 MB o 2000 archivos al descomprimir.",
  example_not_available: "El paquete de ejemplo solo está en una copia del producto con sus carpetas.",
  bundle_name_in_use: "Ya hay un paquete con ese nombre. Elige otro.",
  bundle_busy: "El paquete tiene ejecuciones en curso. Espera a que terminen.",
  bundle_not_installed: "Ese paquete ya no está en la lista.",
  installed_bundle_changed: "La copia instalada cambió o falta: reinstálala desde un archivo.",
  copy_fingerprint_mismatch: "La copia no coincide con el original, así que no se instaló nada.",
  links_not_allowed: "Un paquete no puede traer enlaces simbólicos.",
};
const ACTION_TEXT = {
  install_code: "Este paquete trae código. Instalarlo es del modo avanzado.",
  install_folder: "Instalar desde una carpeta es del modo avanzado.",
  trust_functions: "Confiar en las funciones de un paquete es del modo avanzado.",
};

export function packageErrorText(error) {
  const details = (error && error.details) || {};
  if (details.reason === "advanced_mode_required") return ACTION_TEXT[details.action] || "Esta acción es del modo avanzado.";
  if (REASON_TEXT[details.reason]) return REASON_TEXT[details.reason];
  if (error && error.status === 413) return REASON_TEXT.zip_too_large;
  return "No se pudo completar. Revisa el nombre y el archivo, e inténtalo de nuevo.";
}
