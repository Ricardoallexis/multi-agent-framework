// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Extension «floor_plan» (#floor_plan/<office>, GUI4-T07): the office as a drawing. Zones are the areas, boxes are the
// positions, lines are the relations. It only reads GET /gui/api/offices/{id}/scene; nothing is changed from here.
import "./i18n.js";
import { html, useEffect, useMemo, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet } from "../../js/api.js";
import { registerHelp, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { onLanguageChange, t } from "../../js/i18n.js";
import { officeHref, routeHref } from "../../js/route.js";
import { HELP } from "./help.js";
import { charsFor, layoutScene } from "./layout.js";
import {
  LAYERS, PAN_STEP, WHEEL_IN_FACTOR, WHEEL_OUT_FACTOR, ZOOM_IN_FACTOR, ZOOM_OUT_FACTOR,
  areaName, canZoomIn, canZoomOut, clampView, clip, describeScene, fitView, keyAction, layerLabel, memberName, memberTitle,
  movedEnough, nodeLabel, panView, pinchOf, planLabel, planProgress, pointToFraction, privacyText, sceneUsable,
  selectedLine, selectionStillThere, stateInfo, unitsPerPixel, viewBoxText, zoomView,
} from "./model.js";
import { createScenePoller } from "./scene-poller.js";

registerHelp(HELP, "floor_plan");

const LIST_PATH = "/gui/api/offices";
const scenePath = (id) => LIST_PATH + "/" + encodeURIComponent(id) + "/scene";
const ARROW = "url(#floor-plan-arrow)";

function useLanguageRefresh() {
  const [, bump] = useState(0);
  useEffect(() => onLanguageChange(() => bump((n) => n + 1)), []);
}

// Without an office in the address, the person picks one.
function OfficePicker({ token }) {
  const [offices, setOffices] = useState(null);
  const [failed, setFailed] = useState(false);
  useLanguageRefresh();
  useEffect(() => {
    let active = true;
    apiGet(LIST_PATH, token).then(
      (answer) => { if (active) setOffices(Array.isArray(answer?.offices) ? answer.offices : []); },
      () => { if (active) setFailed(true); },
    );
    return () => { active = false; };
  }, [token]);
  return html`<section class="floor-plan-view" aria-label=${t("ext.floor_plan.aria")}>
    ${failed && html`<p class="message error" role="alert">${t("ext.floor_plan.picker_error")}</p>`}
    ${!failed && offices === null && html`<p class="muted" role="status">${t("ext.floor_plan.loading")}</p>`}
    ${offices && offices.length === 0 && html`<p class="muted" role="status">${t("ext.floor_plan.picker_empty")}</p>`}
    ${offices && offices.length > 0 && html`<p class="muted">${t("ext.floor_plan.picker")}</p>
      <ul class="floor-plan-offices">
        ${offices.map((office) => html`<li key=${office.id}>
          <a class="btn secondary" href=${routeHref("floor_plan", office.id)} ...${helpFor("floor_plan.tabs")}>${office.name || office.id}</a>
        </li>`)}
      </ul>
      <${Help} topic="floor_plan.tabs" compact=${true} />`}
  </section>`;
}

function Plan({ token, officeId }) {
  const [scene, setScene] = useState(null);
  const [problem, setProblem] = useState(null);          // "missing" | "offline" | null
  const [zoom, setZoom] = useState(null);                // null shows everything (fit)
  const [layers, setLayers] = useState(() => Object.fromEntries(LAYERS.map((layer) => [layer, true])));
  const [selected, setSelected] = useState(null);
  const svgRef = useRef(null);
  const canvasRef = useRef(null);
  const pointers = useRef(new Map());
  const gesture = useRef({ origin: null, dragging: false, pinch: 0 });
  const latest = useRef({ zoom: null, shown: null, bounds: null });
  useLanguageRefresh();

  // A new office starts from nothing: the zoom and the selection belong to one plan.
  useEffect(() => {
    setScene(null);
    setProblem(null);
    setZoom(null);
    setSelected(null);
  }, [officeId]);

  useEffect(() => {
    const visible = () => typeof document === "undefined" || document.visibilityState !== "hidden";
    const poller = createScenePoller({
      fetchScene: () => apiGet(scenePath(officeId), token),
      onScene: (next) => { setScene(next); setProblem(null); },
      onFailure: (error) => setProblem(error instanceof ApiError && error.status === 404 ? "missing" : "offline"),
      isVisible: visible,
    });
    const wake = () => { poller.refresh(); };
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", wake);
    poller.start();
    return () => {
      poller.stop();
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", wake);
    };
  }, [token, officeId]);

  const usable = sceneUsable(scene);
  const layout = useMemo(() => (usable ? layoutScene(scene) : null), [scene, usable]);
  const bounds = layout ? layout.bounds : { width: 900, height: 600 };
  const view = zoom ? clampView(zoom, bounds) : null;
  const shown = view || fitView(bounds);
  latest.current = { zoom: view, shown, bounds };
  const selectedKey = layout ? selectionStillThere(layout, selected) : null;

  const applyZoom = (factor, focus) => {
    const { zoom: current, bounds: box } = latest.current;
    setZoom(zoomView(current, box, factor, focus));
  };
  const focusOf = (event) => {
    const rect = svgRef.current ? svgRef.current.getBoundingClientRect() : null;
    return rect && rect.width ? pointToFraction(latest.current.shown, rect, event.clientX, event.clientY) : undefined;
  };

  const onWheel = (event) => {
    if (!event.ctrlKey && !event.metaKey) return;      // a plain wheel still scrolls the page
    event.preventDefault();
    applyZoom(event.deltaY < 0 ? WHEEL_IN_FACTOR : WHEEL_OUT_FACTOR, focusOf(event));
  };

  const onPointerDown = (event) => {
    if (event.target.closest && event.target.closest(".floor-plan-tools")) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.current.size === 1) gesture.current = { origin: { x: event.clientX, y: event.clientY }, dragging: false, pinch: 0 };
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      gesture.current = { origin: null, dragging: true, pinch: pinchOf(a, b).distance };
    }
  };

  const onPointerMove = (event) => {
    const before = pointers.current.get(event.pointerId);
    if (!before) return;
    const point = { x: event.clientX, y: event.clientY };
    pointers.current.set(event.pointerId, point);
    const state = gesture.current;
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const { distance, middle } = pinchOf(a, b);
      const rect = svgRef.current ? svgRef.current.getBoundingClientRect() : null;
      if (state.pinch && distance && rect && rect.width) {
        applyZoom(state.pinch / distance, pointToFraction(latest.current.shown, rect, middle.x, middle.y));
      }
      state.pinch = distance;
      return;
    }
    if (!state.dragging && state.origin && movedEnough(state.origin, point)) {
      state.dragging = true;
      try { canvasRef.current.setPointerCapture(event.pointerId); } catch (_) { /* a pointer that is gone cannot be captured */ }
    }
    const { zoom: current, shown: box, bounds: all } = latest.current;
    const rect = svgRef.current ? svgRef.current.getBoundingClientRect() : null;
    if (state.dragging && current && rect && rect.width) {
      const units = unitsPerPixel(box, rect);
      setZoom(panView(box, all, -(point.x - before.x) * units, -(point.y - before.y) * units));
    }
  };

  const onPointerEnd = (event) => {
    pointers.current.delete(event.pointerId);
    gesture.current = { origin: null, dragging: false, pinch: 0 };
  };

  const onKeyDown = (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const action = keyAction(event.key);
    if (!action) return;
    const { zoom: current, shown: box, bounds: all } = latest.current;
    if (action.type === "zoom") { event.preventDefault(); applyZoom(action.factor); }
    else if (action.type === "fit") { event.preventDefault(); setZoom(null); }
    else if (current) {                                 // arrows move a zoomed plan; a fitted one leaves them to the page
      event.preventDefault();
      setZoom(panView(box, all, action.x * box.w * PAN_STEP, action.y * box.h * PAN_STEP));
    }
  };

  const toggleLayer = (layer) => setLayers((current) => ({ ...current, [layer]: !current[layer] }));
  const choose = (key) => setSelected((current) => (current === key ? null : key));
  const nodeKeys = (key) => (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    choose(key);
  };

  const names = new Map((usable ? scene.areas : []).map((area) => [String(area.id), areaName(area)]));
  const coordinatorState = usable ? stateInfo(scene.orchestrator?.state) : null;
  const progress = usable ? planProgress(scene) : "";
  const officeName = usable ? String(scene.office?.name || scene.office?.id || officeId) : officeId;
  const entries = new Map();                            // what the line under the plan says about each position
  if (layout) {
    if (layout.coordinator) entries.set("coordinator", { kind: "coordinator", state: coordinatorState.text });
    for (const zone of layout.zones) {
      for (const node of zone.nodes) {
        entries.set(node.key, { kind: "member", name: memberName(node.member), area: areaName(zone.area), state: stateInfo(node.member.state).text });
      }
    }
  }

  return html`<section class="floor-plan-view" aria-label=${t("ext.floor_plan.aria")}>
    <div class="floor-plan-head">
      <span class="floor-plan-office">${officeName}</span>
      <nav class="floor-plan-tabs" aria-label=${t("ext.floor_plan.tabs")}>
        <a class="btn floor-plan-tab is-current" href=${routeHref("floor_plan", officeId)} aria-current="page" ...${helpFor("floor_plan.tabs")}>${t("ext.floor_plan.tab.plan")}</a>
        <a class="btn secondary floor-plan-tab" href=${routeHref("offices")} ...${helpFor("floor_plan.tabs")}>${t("ext.floor_plan.tab.list")}</a>
      </nav>
      <${Help} topic="floor_plan.tabs" compact=${true} />
      <a class="btn secondary floor-plan-ask" href=${officeHref(officeId)} ...${helpFor("floor_plan.ask")}>${t("ext.floor_plan.ask")}</a>
      <${Help} topic="floor_plan.ask" compact=${true} />
    </div>

    ${problem === "missing" && html`<p class="message error" role="alert">${t("ext.floor_plan.note.missing")}</p>`}
    ${problem === "offline" && html`<p class="message" role="status">${t("ext.floor_plan.note.offline")}</p>`}
    ${!scene && !problem && html`<p class="muted" role="status">${t("ext.floor_plan.loading")}</p>`}
    ${scene && !usable && html`<p class="message error" role="alert">${t("ext.floor_plan.note.unsupported")}</p>`}

    ${layout && html`
      <div class="floor-plan-legend" role="group" aria-label=${t("ext.floor_plan.layers")}>
        ${LAYERS.map((layer) => html`<button type="button" key=${layer} class=${"floor-plan-chip is-" + layer}
          aria-pressed=${layers[layer] ? "true" : "false"} ...${helpFor("floor_plan.layers")}
          onClick=${() => toggleLayer(layer)}><span class=${"floor-plan-line-key is-" + layer} aria-hidden="true"></span>${layerLabel(layer)}</button>`)}
        <${Help} topic="floor_plan.layers" compact=${true} />
      </div>

      <div class=${"floor-plan-canvas" + (view ? " is-zoomed" : "")} ref=${canvasRef}
        onWheel=${onWheel} onPointerDown=${onPointerDown} onPointerMove=${onPointerMove}
        onPointerUp=${onPointerEnd} onPointerCancel=${onPointerEnd} onKeyDown=${onKeyDown}>
        <div class="floor-plan-tools" role="group" aria-label=${t("ext.floor_plan.zoom")}>
          <button type="button" class="floor-plan-zoom" aria-label=${t("ext.floor_plan.zoom_in")} disabled=${!canZoomIn(view, bounds)}
            ...${helpFor("floor_plan.zoom")} onClick=${() => applyZoom(ZOOM_IN_FACTOR)}>+</button>
          <button type="button" class="floor-plan-zoom" aria-label=${t("ext.floor_plan.zoom_out")} disabled=${!canZoomOut(view, bounds)}
            ...${helpFor("floor_plan.zoom")} onClick=${() => applyZoom(ZOOM_OUT_FACTOR)}>−</button>
          <button type="button" class="floor-plan-zoom is-fit" disabled=${!canZoomOut(view, bounds)}
            ...${helpFor("floor_plan.zoom")} onClick=${() => setZoom(null)}>${t("ext.floor_plan.zoom_fit")}</button>
        </div>
        <ul class="sr-only" aria-label=${t("ext.floor_plan.list_label")}>
          ${describeScene(scene, layout.relations).map((line, index) => html`<li key=${index}>${line}</li>`)}
        </ul>
        <svg ref=${svgRef} class="floor-plan-svg" viewBox=${viewBoxText(shown)} preserveAspectRatio="xMidYMid meet"
          role="group" aria-label=${planLabel(scene, layout)}>
          <defs>
            <marker id="floor-plan-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse">
              <path class="floor-plan-arrow-head" d="M0 0 8 4 0 8z" />
            </marker>
          </defs>
          ${layout.zones.map((zone) => {
    const limit = Math.floor(charsFor(zone.w - 40) * 0.75);
    const status = stateInfo(zone.area.state).text;
    return html`<g key=${zone.id} class="floor-plan-zone-group">
              <title>${areaName(zone.area)}. ${privacyText(zone.area.data_class)}. ${status}</title>
              <rect class="floor-plan-zone" x=${zone.x} y=${zone.y} width=${zone.w} height=${zone.h} rx="24" />
              <text class="floor-plan-zone-label" x=${zone.x + 20} y=${zone.y + 30}>${clip(areaName(zone.area), limit)}</text>
              <text class="floor-plan-zone-level" x=${zone.x + 20} y=${zone.y + 50}>${clip(privacyText(zone.area.data_class), charsFor(zone.w - 40))}</text>
              <text class="floor-plan-zone-level" x=${zone.x + 20} y=${zone.y + 68}>${clip(status, charsFor(zone.w - 40))}</text>
            </g>`;
  })}
          ${layout.relations.filter((relation) => layers[relation.layer]).map((relation) => html`<path key=${relation.key}
            class=${"floor-plan-relation is-" + relation.layer} d=${relation.d} marker-end=${ARROW}>
            <title>${layerLabel(relation.layer)}: ${relation.from === "__coordinator" ? t("ext.floor_plan.coordinator") : names.get(relation.from)} → ${names.get(relation.to)}</title>
          </path>`)}
          ${layout.coordinator && html`<g class=${"floor-plan-node is-coordinator is-" + coordinatorState.tone + (selectedKey === "coordinator" ? " is-selected" : "")}
            tabindex="0" role="button" aria-pressed=${selectedKey === "coordinator" ? "true" : "false"}
            aria-label=${t("ext.floor_plan.selected_coordinator", { state: coordinatorState.text })} ...${helpFor("floor_plan.node")}
            onClick=${() => choose("coordinator")} onKeyDown=${nodeKeys("coordinator")}>
            <rect class="floor-plan-node-shape" x=${layout.coordinator.x} y=${layout.coordinator.y} width=${layout.coordinator.w} height=${layout.coordinator.h} rx="18" />
            <text class="floor-plan-node-title" x=${layout.coordinator.x + layout.coordinator.w / 2} y=${layout.coordinator.y + 29} text-anchor="middle">${t("ext.floor_plan.coordinator")}</text>
            <text class="floor-plan-node-state" x=${layout.coordinator.x + layout.coordinator.w / 2} y=${layout.coordinator.y + 49} text-anchor="middle">${clip(progress ? coordinatorState.text + " · " + progress : coordinatorState.text, charsFor(layout.coordinator.w - 12))}</text>
          </g>`}
          ${layout.zones.map((zone) => zone.nodes.map((node) => {
    const info = stateInfo(node.member.state);
    const picked = selectedKey === node.key;
    return html`<g key=${node.key} class=${"floor-plan-node is-" + info.tone + (picked ? " is-selected" : "")}
              tabindex="0" role="button" aria-pressed=${picked ? "true" : "false"} aria-label=${nodeLabel(node.member, zone.area)}
              ...${helpFor("floor_plan.node")} onClick=${() => choose(node.key)} onKeyDown=${nodeKeys(node.key)}>
              <title>${memberTitle(node.member, zone.area)}</title>
              <rect class="floor-plan-node-shape" x=${node.x} y=${node.y} width=${node.w} height=${node.h} rx="14" />
              ${node.member.kind === "person"
      ? html`<circle class="floor-plan-avatar" cx=${node.x + 24} cy=${node.y + node.h / 2} r="13" />`
      : html`<rect class="floor-plan-avatar" x=${node.x + 11} y=${node.y + node.h / 2 - 13} width="26" height="26" rx="7" />`}
              <text class="floor-plan-node-title" x=${node.x + 46} y=${node.y + 19}>${clip(memberName(node.member), charsFor(node.w - 56))}</text>
              <text class="floor-plan-node-state" x=${node.x + 46} y=${node.y + 34}>${clip(info.text, charsFor(node.w - 56))}</text>
            </g>`;
  }))}
        </svg>
      </div>
      <p class="floor-plan-selected" role="status" aria-live="polite">${selectedLine(entries.get(selectedKey))}</p>
      <${Help} topic="floor_plan.zoom" compact=${true} />
      <${Help} topic="floor_plan.node" compact=${true} />
      ${!layout.zones.length && !layout.coordinator && html`<p class="muted" role="status">${t("ext.floor_plan.note.empty")}</p>`}
      ${scene.truncated && html`<p class="muted small" role="status">${t("ext.floor_plan.note.truncated")}</p>`}
    `}
  </section>`;
}

export function FloorPlanView({ token, route }) {
  const officeId = route && route.id ? route.id : null;
  return officeId ? html`<${Plan} key=${officeId} token=${token} officeId=${officeId} />` : html`<${OfficePicker} token=${token} />`;
}

registerView({ id: "floor_plan", label: t("ext.floor_plan.view"), topic: "floor_plan.view", component: FloorPlanView, order: 35, owner: "floor_plan", group: "more", icon: "map", mode: "simple" });
