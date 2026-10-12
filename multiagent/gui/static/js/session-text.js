// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Texts and formatters of the sessions screen (Spanish). The server sends codes and facts; the wording lives here.
import { legacyInfo } from "./ui/labels.js";

export const SOURCES = [
  { id: "claude", label: "Claude" },
  { id: "codex", label: "Codex" },
  { id: "copilot", label: "Copilot" },
  { id: "framework", label: "Framework" },
];

export const SURFACES = [
  { id: "desktop", label: "Escritorio" },
  { id: "cli", label: "CLI (terminal)" },
  { id: "framework", label: "Framework" },
  { id: "unknown", label: "Desconocida" },
];

const label = (list, id, fallback) => (list.find((entry) => entry.id === id) || { label: fallback || String(id) }).label;

export const sourceLabel = (id) => label(SOURCES, id);
export const surfaceLabel = (id) => label(SURFACES, id);
// The words of the statuses live in ui/labels.js (the single label map); this is its {label, tone, help} view.
export const statusInfo = (id) => legacyInfo("session", id);

// A subagent has no title of its own: it is named after its id ("agent-xxxx"); the others show the start of theirs.
export function sessionTitle(session) {
  if (session.title) return session.title;
  const native = String(session.native_id || session.id);
  if (session.parent_id) return "Subagente " + native.slice(native.lastIndexOf(":") + 1);
  return "Sin título (" + native.slice(0, 8) + ")";
}

const number = (value) => value.toLocaleString("es");
const known = (value) => typeof value === "number" && Number.isFinite(value);

// {input, output, cache_read, cache_write, total?} or null (unknown: never shown as 0).
export function tokensText(tokens, { brief = false } = {}) {
  if (!tokens) return "desconocido";
  const parts = [];
  if (known(tokens.input)) parts.push("entrada " + number(tokens.input));
  if (known(tokens.output)) parts.push("salida " + number(tokens.output));
  if (!brief) {
    if (known(tokens.cache_read)) parts.push("caché leída " + number(tokens.cache_read));
    if (known(tokens.cache_write)) parts.push("caché escrita " + number(tokens.cache_write));
  }
  if (known(tokens.total) && !parts.length) parts.push("total " + number(tokens.total));
  return parts.length ? parts.join(" · ") : "desconocido";
}

export const modelsText = (models) => (models && models.length ? models.join(", ") : "sin indicar");

const RELATIVE = typeof Intl !== "undefined" && Intl.RelativeTimeFormat
  ? new Intl.RelativeTimeFormat("es", { numeric: "auto" }) : null;

export function relativeTime(value, now = Date.now()) {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return String(value);
  const seconds = (then - now) / 1000;
  const abs = Math.abs(seconds);
  if (abs < 45) return "justo ahora";
  const units = [["minute", 60, 60], ["hour", 3600, 24], ["day", 86400, 30], ["month", 2592000, 12], ["year", 31536000, Infinity]];
  let size = 60;
  let unit = "minute";
  for (const [name, perUnit, limit] of units) {
    unit = name;
    size = perUnit;
    if (abs / perUnit < limit) break;
  }
  const amount = Math.round(seconds / size);
  return RELATIVE ? RELATIVE.format(amount, unit) : "hace " + Math.abs(amount) + " " + unit;
}

const KIND = {
  user_message: "Usuario",
  assistant_message: "Asistente",
  reasoning_omitted: "Razonamiento omitido",
  tool_call: "Herramienta",
  tool_result: "Resultado de herramienta",
  system: "Sistema",
  subagent: "Subagente",
  compaction: "Compactación de contexto",
  usage: "Uso de tokens",
  unknown: "Registro desconocido",
};
export const kindLabel = (kind) => KIND[kind] || KIND.unknown;

const WARNINGS = {
  no_allowed_roots: () => "Todavía no hay carpetas permitidas (gui_session_roots).",
  source_unavailable: (rest) => "Fuente no disponible" + (rest ? " (" + rest + ")" : "") + "; se muestran las demás.",
  cursor_reset: () => "El archivo de la sesión cambió y se volvió a leer desde el principio.",
  broken_json_lines: (rest) => "Líneas dañadas omitidas: " + rest + ".",
  oversized_lines_skipped: (rest) => "Líneas demasiado grandes omitidas: " + rest + ".",
  unknown_record_types: (rest) => "Registros de un tipo que este visor no conoce: " + rest + ".",
  session_file_unavailable: () => "El archivo de la sesión no está disponible.",
  no_local_history: () => "Esta sesión no tiene historial local.",
};

// The reader sends "<code>: <detail>" or just "<code>".
export function warningText(warning) {
  const text = String(warning);
  const split = text.indexOf(":");
  const code = split < 0 ? text : text.slice(0, split);
  const rest = split < 0 ? "" : text.slice(split + 1).trim();
  const describe = WARNINGS[code];
  return describe ? describe(rest) : text;
}

export const ROOTS_HELP = {
  title: "Todavía no hay carpetas permitidas",
  intro: "Por privacidad, el visor no muestra ninguna sesión hasta que indiques qué carpetas de trabajo se pueden ver. " +
    "Solo aparecen las sesiones cuya carpeta esté dentro de una de ellas.",
  steps: [
    "Define la opción gui_session_roots (variable de entorno GUI_SESSION_ROOTS, o la misma línea en el archivo .env de la carpeta local del framework) con una o varias carpetas, separadas por «;» o como lista JSON.",
    "Vuelve a arrancar la interfaz con python -m multiagent.gui y recarga esta página.",
  ],
  example: "GUI_SESSION_ROOTS=C:\\proyectos;D:\\trabajo",
};
