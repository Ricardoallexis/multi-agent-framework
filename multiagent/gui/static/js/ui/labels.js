// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Status labels (GUI2-T11): the one place with the words, tone and explanation the GUI shows for a status.
// Pure module (no DOM, no Preact): the views, StatusChip (status-chip.js) and the Node tests all read it.
//
// A label MAP is a plain object: status -> { text, tone, explanation, pulse?, shape? }
//   text         what is shown ("En ejecución"); the status is never shown by colour alone
//   tone         one of TONES; the colour comes from the tone (app.css), never from the status name
//   explanation  one sentence for the tooltip and for screen readers
//   pulse        true: the mark pulses (work in progress; it stops with prefers-reduced-motion)
//   shape        disc | ring | square | diamond: only for dots; it defaults from the tone, so a dot differs in shape too
// This is the same shape as the single label map of PRY-T02 (multiagent/tasks/labels.json: text, tone, explanation per
// key, under "task" and "stage"), so that map plugs in with registerLabelSet("task", labels.task) and then
// <StatusChip labels="task" status="review" />. The old key names {label, help} are accepted too.
// The default sets below are the statuses the GUI shows today; each view reads its own through labelSet(name).

export const TONES = ["success", "active", "info", "attention", "warning", "danger", "muted", "neutral"];
export const SHAPES = ["disc", "ring", "square", "diamond"];

// Names the GUI used before there was a shared vocabulary (and the ones of the PRY map are already canonical).
const TONE_ALIASES = { ok: "success", accent: "active", warn: "warning", bad: "danger", quiet: "muted", run: "success", test: "warning" };
export const normalizeTone = (tone) => (TONES.includes(tone) ? tone : TONE_ALIASES[tone] || "neutral");

const DEFAULT_SHAPE = {
  success: "disc", active: "disc", info: "ring", attention: "diamond", warning: "diamond",
  danger: "square", muted: "ring", neutral: "ring",
};

const own = (object, key) => Boolean(object) && Object.prototype.hasOwnProperty.call(object, key);

// The entry of `status` in `labels`, always complete. A status the map does not know shows its own name, neutral.
export function labelEntry(labels, status) {
  const key = String(status);
  let raw = own(labels, key) ? labels[key] : null;
  if (typeof raw === "string") raw = { text: raw };
  const known = Boolean(raw) && typeof raw === "object";
  const entry = known ? raw : {};
  const text = entry.text !== undefined ? entry.text : entry.label;
  const explanation = entry.explanation !== undefined ? entry.explanation : entry.help;
  const tone = normalizeTone(known ? entry.tone : "neutral");
  return {
    key,
    known,
    text: text === undefined || text === null || text === "" ? key : String(text),
    tone,
    explanation: explanation ? String(explanation) : "",
    pulse: Boolean(entry.pulse),
    shape: SHAPES.includes(entry.shape) ? entry.shape : DEFAULT_SHAPE[tone],
  };
}

// "Trabajando" or, with a detail, "Trabajando (en ejecución)": what a dot says to a screen reader and in its tooltip.
export const statusText = (entry, detail) => entry.text + (detail ? " (" + String(detail).toLowerCase() + ")" : "");

// --- the statuses of the GUI today ---------------------------------------------------------------------------------

export const DEFAULT_LABELS = Object.freeze({
  // Framework runs (GET /gui/api/runs, run detail).
  run: {
    queued: { text: "En cola", tone: "muted", explanation: "Esperando a que el worker la tome." },
    running: { text: "En ejecución", tone: "active", explanation: "El framework está ejecutando un paso." },
    waiting_human: { text: "Esperando a una persona", tone: "warning", explanation: "Necesita una decisión o una respuesta tuya." },
    paused: { text: "Pausada", tone: "warning", explanation: "La ejecución está en pausa." },
    completed: { text: "Completada", tone: "success", explanation: "Todos los pasos terminaron." },
    failed: { text: "Falló", tone: "danger", explanation: "La ejecución terminó con un error." },
    cancelled: { text: "Cancelada", tone: "muted", explanation: "Se canceló antes de terminar." },
    rejected: { text: "Rechazada", tone: "muted", explanation: "Una persona rechazó el resultado." },
    interrupted: { text: "Interrumpida", tone: "warning", explanation: "El servidor se reinició mientras corría; puede reanudarse." },
  },
  // The steps of one run.
  step: {
    done: { text: "Hecho", tone: "success" },
    skipped: { text: "Saltado", tone: "muted" },
    will_skip: { text: "Se saltará", tone: "warning" },
    waiting_review: { text: "Espera tu revisión", tone: "warning" },
    waiting_human: { text: "Espera tu respuesta", tone: "warning" },
    running: { text: "En curso", tone: "active" },
    queued: { text: "En cola", tone: "muted" },
    pending: { text: "Pendiente", tone: "muted" },
    failed: { text: "Falló", tone: "danger" },
    not_run: { text: "No llegó a ejecutarse", tone: "muted" },
  },
  // Sessions of Claude, Codex and Copilot.
  session: {
    active: { text: "Activa", tone: "success", explanation: "Tuvo actividad hace poco." },
    idle: { text: "Sin actividad", tone: "muted", explanation: "No tuvo actividad reciente." },
    archived: { text: "Archivada", tone: "muted", explanation: "La app la tiene archivada." },
    unknown: { text: "Estado desconocido", tone: "muted", explanation: "La fuente no dice en qué estado está." },
  },
  // Subagents, threads and steps listed in the subagents panel.
  subagent: {
    running: { text: "Trabajando", tone: "success", pulse: true, explanation: "Tuvo actividad hace menos de 15 minutos." },
    testing: { text: "Ejecutando prueba", tone: "warning",
      explanation: "Tuvo actividad hace menos de 15 minutos y su última herramienta es una prueba (pytest, probar o integrar) que aún no devolvió resultado." },
    idle: { text: "Sin actividad", tone: "muted", explanation: "Entre 15 y 60 minutos sin actividad." },
    done: { text: "Terminado", tone: "muted", explanation: "Terminó su trabajo." },
    stopped: { text: "Detenido", tone: "muted", explanation: "Más de 60 minutos sin actividad, o falló." },
    unknown: { text: "Estado desconocido", tone: "muted", explanation: "La fuente no dice cuándo tuvo actividad." },
  },
  // The dot next to each item of the left sidebar: solid circle = working, ring = finished, square = error.
  dot: {
    working: { text: "Trabajando", tone: "success", pulse: true, shape: "disc" },
    finished: { text: "Terminada", tone: "muted", shape: "ring" },
    error: { text: "Con error", tone: "danger", shape: "square" },
  },
});

// Team version statuses (CHT-T05): shared by the Equipos view and its chat card.
export const VERSION_STATUS = {
  draft: { text: "Borrador", tone: "muted" },
  proposed: { text: "Propuesta", tone: "attention" },
  approved: { text: "Aprobada", tone: "success" },
  rejected: { text: "Rechazada", tone: "danger" },
};

export const versionStatusText = (status) => VERSION_STATUS[status]?.text || "Estado desconocido";

// --- the registry: how a map is swapped -----------------------------------------------------------------------------

const registry = new Map(Object.entries(DEFAULT_LABELS));

// The map registered under `name` ({} when there is none, so a typo shows the raw status instead of throwing).
export const labelSet = (name) => registry.get(name) || {};

// Registers or replaces a map: registerLabelSet("task", labels.task) once the label map of PRY-T02 is served to the GUI.
export function registerLabelSet(name, map) {
  if (!name || !map || typeof map !== "object") throw new TypeError("registerLabelSet needs a name and a map");
  registry.set(String(name), map);
}

// Back to the defaults (what the Node tests use between cases).
export function resetLabelSets() {
  registry.clear();
  for (const [name, map] of Object.entries(DEFAULT_LABELS)) registry.set(name, map);
}

// What a component receives as `labels`: the name of a registered set, or the map itself.
export const resolveLabels = (labels) => (typeof labels === "string" ? labelSet(labels) : labels || {});

// {label, tone, help}: the older shape of the statusInfo() helpers of the text modules, now read from the shared sets.
export function legacyInfo(setName, status) {
  const entry = labelEntry(labelSet(setName), status);
  return { label: entry.text, tone: entry.tone, help: entry.explanation };
}
