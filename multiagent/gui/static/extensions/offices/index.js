// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Oficinas» (#offices): a read-only list of the office scene and the fixes it reports.
import { html, useEffect, useState } from "../../vendor/htm-preact-standalone.module.js";
import { apiGet } from "../../js/api.js";
import { currentMode } from "../../js/gui-mode.js";
import { registerHelp, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { planThreadHref, teamThreadHref } from "../../js/route.js";
import { HELP } from "./help.js";
import {
  createScenePoller,
  degradedAction,
  degradedMessage,
  EMPTY_TEXT,
  OFFLINE_TEXT,
  officeRows,
  sceneTargets,
} from "./model.js";

registerHelp(HELP, "offices");

const LIST_PATH = "/gui/api/offices";
const officePath = (id) => LIST_PATH + "/" + encodeURIComponent(id);
const scenePath = (id) => officePath(id) + "/scene";
const requestError = (error, fallback) => (error && error.message) || fallback;

function fixHref(entry, action) {
  const prefix = "#new/team/";
  if (entry.code === "team_not_approved" && action.href?.startsWith(prefix)) {
    return teamThreadHref(action.href.slice(prefix.length));
  }
  return action.href;
}

function DegradedLine({ entry }) {
  const action = degradedAction(entry);
  const simple = currentMode() === "simple";
  const href = fixHref(entry, action);
  return html`<li class="offices-degraded">
    <span>Funciona a medias: ${degradedMessage(entry.code)}</span>
    ${!simple && html`<code class="small">${entry.code}</code>`}
    ${href ? html`<a class="btn secondary small" href=${href} ...${helpFor("offices.degraded")}>${action.label}</a>`
      : action.action === "reimport" ? html`<span class="muted small">${action.label}</span>` : null}
    <${Help} topic="offices.degraded" compact=${true} />
  </li>`;
}

function OfficeCard({ office, detail, detailError, scene, offline }) {
  const rows = officeRows(scene, detail?.requirements);
  const simple = currentMode() === "simple";
  return html`<article class="offices-card" aria-label=${office.name}>
    <div class="offices-card-head">
      <p class="offices-title">${office.name}</p>
      <span class=${"offices-office-state" + (office.state === "ok" ? " is-ok" : " is-degraded")}>
        ${office.state === "ok" ? "Lista" : "Funciona a medias"}
      </span>
    </div>
    ${detailError && html`<p class="message error small" role="alert">${detailError}</p>`}
    ${offline && html`<p class="message error small" role="status">${OFFLINE_TEXT}</p>`}
    ${!scene && !offline && !detailError && html`<p class="muted small" role="status">Leyendo el estado de la oficina…</p>`}
    ${scene && html`<div class="offices-rows">
      ${rows.map((row) => row.kind === "orchestrator"
        ? html`<div key=${row.key} class="offices-row offices-coordinator">
            <div class="offices-row-main">
              <strong>${row.name}</strong><span class="offices-state">${row.label}</span>
            </div>
            ${row.plan && html`<p class="offices-meta">Plan de ${row.plan.tasks} tareas ·
              <a href=${planThreadHref(row.plan.proposal_id)} ...${helpFor("offices.plan")}>Abrir plan</a></p>
              <${Help} topic="offices.plan" compact=${true} />`}
            <${Help} topic="offices.member_state" compact=${true} />
          </div>`
        : row.kind === "area"
          ? html`<div key=${row.key} class="offices-row offices-area">
              <div class="offices-row-main">
                <strong>${row.name}</strong><span class="offices-state">${row.label}</span>
              </div>
              <p class="offices-meta">Nivel: ${row.level}</p>
              <${Help} topic="offices.levels" compact=${true} />
              ${row.requirementText && html`<p class="offices-meta">${row.requirementText}</p>`}
              ${row.permissions && html`<p class="offices-meta">${row.permissions}</p><${Help} topic="offices.declared" compact=${true} />`}
              ${row.degraded.length > 0 && html`<ul class="offices-degraded-list">
                ${row.degraded.map((entry) => html`<${DegradedLine} key=${entry.code} entry=${entry} />`)}
              </ul>`}
            </div>`
          : html`<div key=${row.key} class="offices-row offices-member">
              <div class="offices-row-main">
                <strong>${row.name}</strong><span class="offices-state">${row.label}</span>
              </div>
              <p class="offices-meta">${row.line}</p>
              <${Help} topic="offices.member_state" compact=${true} />
            </div>`)}
    </div>`}
    ${simple && scene && scene.truncated && html`<p class="muted small">Se muestran los datos recientes disponibles.</p>`}
  </article>`;
}

export function OfficesView({ token }) {
  const [offices, setOffices] = useState(null);
  const [details, setDetails] = useState({});
  const [detailErrors, setDetailErrors] = useState({});
  const [scenes, setScenes] = useState({});
  const [offline, setOffline] = useState({});
  const [listError, setListError] = useState("");

  useEffect(() => {
    let active = true;
    setOffices(null);
    setDetails({});
    setDetailErrors({});
    setScenes({});
    setOffline({});
    setListError("");
    async function load() {
      let rows;
      try {
        const answer = await apiGet(LIST_PATH, token);
        rows = Array.isArray(answer?.offices) ? answer.offices : [];
      } catch (error) {
        if (active) setListError(requestError(error, "No se pudo leer la lista de oficinas."));
        return;
      }
      if (!active) return;
      setOffices(rows);
      if (!rows.length) return;
      const results = await Promise.all(rows.map(async (office) => {
        try {
          return [office.id, await apiGet(officePath(office.id), token), ""];
        } catch (error) {
          return [office.id, null, requestError(error, "No se pudo leer la oficina.")];
        }
      }));
      if (!active) return;
      setDetails(Object.fromEntries(results.map(([id, detail]) => [id, detail])));
      setDetailErrors(Object.fromEntries(results.filter(([, , error]) => error).map(([id, , error]) => [id, error])));
    }
    load();
    return () => { active = false; };
  }, [token]);

  useEffect(() => {
    const ids = sceneTargets(offices);
    if (!ids.length) return undefined;
    let active = true;
    const pollers = ids.map((id) => {
      const poller = createScenePoller({
        fetchScene: () => apiGet(scenePath(id), token),
        onScene: (scene) => {
          if (!active) return;
          setScenes((current) => ({ ...current, [id]: scene }));
          setOffline((current) => ({ ...current, [id]: false }));
        },
        onFailure: () => {
          if (active) setOffline((current) => ({ ...current, [id]: true }));
        },
        isVisible: () => typeof document === "undefined" || !document.hidden,
      });
      poller.start();
      return poller;
    });
    const onVisible = () => {
      if (typeof document === "undefined" || !document.hidden) {
        for (const poller of pollers) void poller.refresh();
      }
    };
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisible);
      for (const poller of pollers) poller.stop();
    };
  }, [offices, token]);

  return html`<section class="offices-view" aria-label="Oficinas">
    ${listError && html`<p class="message error" role="alert">${listError}</p>`}
    ${offices === null && !listError && html`<p class="muted" role="status">Leyendo oficinas…</p>`}
    ${offices && offices.length === 0 && html`<p class="offices-empty" role="status">${EMPTY_TEXT}</p>`}
    ${offices && offices.length > 0 && html`<div class="offices-list">
      ${offices.map((office) => html`<${OfficeCard} key=${office.id} office=${office}
        detail=${details[office.id]} detailError=${detailErrors[office.id]}
        scene=${scenes[office.id]} offline=${Boolean(offline[office.id])} />`)}
    </div>`}
  </section>`;
}

registerView({
  id: "offices",
  label: "Oficinas",
  topic: "offices.view",
  component: OfficesView,
  order: 30,
  owner: "offices",
  group: "primary",
  icon: "building",
  mode: "simple",
});
