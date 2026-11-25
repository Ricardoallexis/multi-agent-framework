// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure helpers of the floor plan (GUI4-T07): texts of states and privacy, the list equivalent of the drawing, and the
// arithmetic of the view (fit, zoom, pan). No DOM and no network; every text comes through t().
import { t } from "../../js/i18n.js";

export const SCENE_SCHEMA = "maf.office-scene/1";
export const LAYERS = ["assignment", "handoff", "review", "question"];
export const MAX_ZOOM = 6;
export const ZOOM_IN_FACTOR = 0.8;
export const ZOOM_OUT_FACTOR = 1.25;
export const WHEEL_IN_FACTOR = 0.86;
export const WHEEL_OUT_FACTOR = 1.16;
export const PAN_STEP = 0.12;             // of the visible width, per arrow key
const DRAG_THRESHOLD = 4;                 // pixels before a press becomes a drag (below it, it is still a click)

const asArray = (value) => (Array.isArray(value) ? value : []);
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

// --- what the scene says -----------------------------------------------------------------------------------------

export function sceneUsable(scene) {
  return Boolean(scene) && typeof scene === "object" && scene.schema === SCENE_SCHEMA && Array.isArray(scene.areas);
}

// The tone drives the outline of a node (with the words next to it, so colour is never the only cue).
const STATES = {
  review: { tone: "wait", key: "ext.floor_plan.state.review" },
  changes: { tone: "wait", key: "ext.floor_plan.state.changes" },
  failed: { tone: "bad", key: "ext.floor_plan.state.failed" },
  blocked: { tone: "bad", key: "ext.floor_plan.state.blocked" },
  doing: { tone: "busy", key: "ext.floor_plan.state.doing" },
  ready: { tone: "busy", key: "ext.floor_plan.state.ready" },
  todo: { tone: "idle", key: "ext.floor_plan.state.todo" },
  done: { tone: "ok", key: "ext.floor_plan.state.done" },
  cancelled: { tone: "idle", key: "ext.floor_plan.state.cancelled" },
  idle: { tone: "idle", key: "ext.floor_plan.state.idle" },
};

export function stateInfo(state) {
  const entry = STATES[state] || { tone: "idle", key: "ext.floor_plan.state.unknown" };
  return { tone: entry.tone, text: t(entry.key) };
}

export function privacyLevel(dataClass) {
  const key = dataClass === "restricted" ? "ext.floor_plan.level.local"
    : dataClass === "sensitive" ? "ext.floor_plan.level.sensitive"
      : dataClass === "non_sensitive" ? "ext.floor_plan.level.normal" : "ext.floor_plan.level.none";
  return t(key);
}

export const privacyText = (dataClass) => t("ext.floor_plan.privacy", { level: privacyLevel(dataClass) });

export function planProgress(scene) {
  const plan = scene?.orchestrator?.plan;
  const tasks = Number(plan?.tasks);
  if (!plan || !Number.isFinite(tasks) || tasks <= 0) return "";
  const done = clamp(Number(plan.done) || 0, 0, tasks);
  return t("ext.floor_plan.coordinator_plan", { done, tasks });
}

export function memberName(member) {
  return String(member?.name || member?.id || t("ext.floor_plan.member"));
}

export function memberTitle(member, area) {
  const role = member?.role ? ` (${member.role})` : "";
  return `${memberName(member)}${role}, ${areaName(area)}`;
}

export function areaName(area) {
  return String(area?.name || area?.id || t("ext.floor_plan.area"));
}

// Cut a long label so it stays in its box; the full text goes to the tooltip and the screen-reader label.
export function clip(text, limit) {
  const value = String(text ?? "");
  const chars = Array.from(value);
  return chars.length <= limit ? value : chars.slice(0, Math.max(1, limit - 1)).join("").trimEnd() + "…";
}

export function layerLabel(layer) { return t("ext.floor_plan.layer." + layer); }

// The drawing as a list, in the same order the plan reads: the coordinator, then each area with its positions.
export function describeScene(scene, relations = []) {
  const items = [];
  const orchestrator = scene?.orchestrator;
  if (orchestrator?.enabled) {
    const state = stateInfo(orchestrator.state).text;
    const progress = planProgress(scene);
    items.push(progress
      ? t("ext.floor_plan.list.coordinator_plan", { state, progress })
      : t("ext.floor_plan.list.coordinator", { state }));
  }
  const names = new Map();
  for (const area of asArray(scene?.areas)) {
    names.set(String(area.id), areaName(area));
    const members = asArray(area.members).map((member) =>
      t("ext.floor_plan.list.member", { name: memberName(member), state: stateInfo(member.state).text }));
    items.push(t("ext.floor_plan.list.area", {
      area: areaName(area), level: privacyLevel(area.data_class), state: stateInfo(area.state).text,
      members: members.length ? t("ext.floor_plan.list.positions", { list: members.join("; ") }) : t("ext.floor_plan.list.no_positions"),
    }));
  }
  for (const relation of relations) {
    if (relation.kind === "assign") continue;
    items.push(t("ext.floor_plan.list.relation", {
      layer: layerLabel(relation.layer), from: names.get(relation.from) || relation.from, to: names.get(relation.to) || relation.to,
    }));
  }
  return items;
}

export function planLabel(scene, layout) {
  return t("ext.floor_plan.svg_label", { office: String(scene?.office?.name || scene?.office?.id || ""), count: layout.zones.length });
}

export function nodeLabel(member, area) {
  return t("ext.floor_plan.node_label", { name: memberName(member), area: areaName(area), state: stateInfo(member?.state).text });
}

export function selectedLine(entry) {
  if (!entry) return "";
  if (entry.kind === "coordinator") return t("ext.floor_plan.selected_coordinator", { state: entry.state });
  return t("ext.floor_plan.selected", { name: entry.name, area: entry.area, state: entry.state });
}

// --- the view: {x, y, w, h} in plan units, always with the aspect of the plan ---------------------------------------

export function fitView(bounds) { return { x: 0, y: 0, w: bounds.width, h: bounds.height }; }

export const viewBoxText = (view) => [view.x, view.y, view.w, view.h].map((n) => Math.round(n * 100) / 100).join(" ");

// A view that shows everything is "fit"; null stands for it so a plan that grows keeps fitting.
export function normalizeView(view, bounds) {
  if (!view || view.w >= bounds.width - 0.01) return null;
  return clampView(view, bounds);
}

export function clampView(view, bounds) {
  const w = clamp(view.w, bounds.width / MAX_ZOOM, bounds.width);
  const h = w * bounds.height / bounds.width;
  return { x: clamp(view.x, 0, bounds.width - w), y: clamp(view.y, 0, bounds.height - h), w, h };
}

export const zoomLevel = (view, bounds) => bounds.width / (view ? view.w : bounds.width);
export const canZoomIn = (view, bounds) => zoomLevel(view, bounds) < MAX_ZOOM - 0.01;
export const canZoomOut = (view, bounds) => Boolean(view);

// factor below 1 zooms in, above 1 zooms out; focus is a point of the visible area as fractions (0 to 1).
export function zoomView(view, bounds, factor, focus = { x: 0.5, y: 0.5 }) {
  const current = view || fitView(bounds);
  const w = clamp(current.w * factor, bounds.width / MAX_ZOOM, bounds.width);
  const h = w * bounds.height / bounds.width;
  const next = { x: current.x + (current.w - w) * focus.x, y: current.y + (current.h - h) * focus.y, w, h };
  return normalizeView(clampView(next, bounds), bounds);
}

export function panView(view, bounds, dx, dy) {
  if (!view) return null;
  return normalizeView(clampView({ ...view, x: view.x + dx, y: view.y + dy }, bounds), bounds);
}

// Plan units per screen pixel. The drawing keeps its aspect ("meet"), so the larger ratio rules.
export function unitsPerPixel(view, rect) { return Math.max(view.w / rect.width, view.h / rect.height); }

// A screen point as fractions of the visible plan, allowing for the bands the aspect leaves around it.
export function pointToFraction(view, rect, clientX, clientY) {
  const scale = unitsPerPixel(view, rect);
  const drawnW = view.w / scale;
  const drawnH = view.h / scale;
  const offsetX = (rect.width - drawnW) / 2;
  const offsetY = (rect.height - drawnH) / 2;
  return {
    x: clamp((clientX - rect.left - offsetX) / drawnW, 0, 1),
    y: clamp((clientY - rect.top - offsetY) / drawnH, 0, 1),
  };
}

export const movedEnough = (from, to) => Math.hypot(to.x - from.x, to.y - from.y) > DRAG_THRESHOLD;

// The two touches of a pinch: their distance and middle.
export function pinchOf(a, b) {
  return { distance: Math.hypot(a.x - b.x, a.y - b.y), middle: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
}

// What a key does to the view: "in", "out", "fit" or a pan step, or null when the key is not for the plan.
export function keyAction(key) {
  if (key === "+" || key === "=") return { type: "zoom", factor: ZOOM_IN_FACTOR };
  if (key === "-" || key === "_") return { type: "zoom", factor: ZOOM_OUT_FACTOR };
  if (key === "0") return { type: "fit" };
  if (key === "ArrowLeft") return { type: "pan", x: -1, y: 0 };
  if (key === "ArrowRight") return { type: "pan", x: 1, y: 0 };
  if (key === "ArrowUp") return { type: "pan", x: 0, y: -1 };
  if (key === "ArrowDown") return { type: "pan", x: 0, y: 1 };
  return null;
}

// Keep the selection only while its position is still in the plan.
export function selectionStillThere(layout, key) {
  if (!key) return null;
  if (key === "coordinator") return layout.coordinator ? key : null;
  return layout.zones.some((zone) => zone.nodes.some((node) => node.key === key)) ? key : null;
}
