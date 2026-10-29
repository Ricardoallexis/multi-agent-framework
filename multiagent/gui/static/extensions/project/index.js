// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Project view (PRY-T03): the screen «Proyectos» (#project, #project/<id>) and a card in the right panel. «Resumen» shows the
// progress, what the user has to do (reviews, verifications, own tasks, blockers), who works now and the stages' dates;
// «Etapas y tareas» shows the stages with collapsible phases, filters and the detail of a task (dependencies, deliverables of
// its result envelope, approve or ask for changes). It only shows what /gui/api/projects answers (the core's own views). The
// one write is the review of a submitted task, through the token and the services' own rules (multiagent/gui/project_api.py).
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { registerHelp, registerSlot, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { apiGet, apiPost } from "../../js/api.js";
import { navigate, routeHref } from "../../js/route.js";
import { Progress } from "../../js/ui/progress.js";
import { Tabs } from "../../js/ui/tabs.js";
import { HELP } from "./help.js";
import { labelSummary, list, needsText, pickProject, progressOf } from "./model.js";
import { Overview, StagesPanel, TaskDetail } from "./parts.js";

registerHelp(HELP);

const NO_FILTER = { text: "", label: "" };
const NO_REVIEW = { open: false, text: "", busy: false };
const enc = encodeURIComponent;
const CHANGED = "project:changed";      // the screen tells the panel card that the numbers moved (a decision, a refresh)
const announce = () => window.dispatchEvent(new Event(CHANGED));
const TABS = [{ id: "overview", label: "Resumen" }, { id: "stages", label: "Etapas y tareas" }];

// What the user reads when a call fails: a clash of revisions or a missing task gets its own words; the rest is the server's.
function failure(error, fallback) {
  if (error && error.status === 409) return "La tarea cambió mientras la veías. Se actualizó; revísala otra vez antes de decidir.";
  if (error && error.status === 404) return "Esa tarea o ese proyecto ya no existe.";
  return (error && error.message) || fallback;
}

export function ProjectView({ token, route }) {
  const [projects, setProjects] = useState(null);       // null while the list loads
  const [selected, setSelected] = useState(route?.id || "");
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("overview");
  const [filter, setFilter] = useState(NO_FILTER);
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [taskId, setTaskId] = useState("");
  const [task, setTask] = useState(null);
  const [taskLoading, setTaskLoading] = useState(false);
  const [taskError, setTaskError] = useState("");
  const [notice, setNotice] = useState("");
  const [review, setReview] = useState(NO_REVIEW);
  const calls = useRef({ project: 0, task: 0 });          // an answer that arrives after a newer request is dropped
  const heading = useRef(null);
  const focusTask = useRef(false);

  const closeTask = () => {
    calls.current.task += 1;
    setTaskId(""); setTask(null); setTaskLoading(false); setTaskError(""); setNotice(""); setReview(NO_REVIEW);
  };

  const selectProject = (id) => {
    calls.current.project += 1;
    setSelected(id); setProject(null); setError(""); setFilter(NO_FILTER); setCollapsed(new Set()); closeTask();
  };

  const loadProjects = async () => {
    try {
      const data = await apiGet("/gui/api/projects", token);
      const all = list(data?.projects);
      setProjects(all);
      setSelected((current) => (current && (all.some((p) => p.id === current) || current === route?.id) ? current : pickProject(all, route?.id)));
    } catch (err) {
      setProjects([]);
      setError(failure(err, "No se pudo cargar la lista de proyectos."));
    }
  };

  const loadProject = async (id) => {
    if (!id) return;
    const call = ++calls.current.project;
    setLoading(true); setError("");
    try {
      const data = await apiGet("/gui/api/projects/" + enc(id), token);
      if (call === calls.current.project) setProject(data);
    } catch (err) {
      if (call === calls.current.project) setError(failure(err, "No se pudo cargar el proyecto."));
    } finally {
      if (call === calls.current.project) setLoading(false);
    }
  };

  const loadTask = async (id, { focus = false } = {}) => {
    if (!id || !selected) return;
    const call = ++calls.current.task;
    focusTask.current = focus;
    setTaskId(id); setTaskLoading(true); setTaskError("");
    try {
      const data = await apiGet("/gui/api/projects/" + enc(selected) + "/tasks/" + enc(id), token);
      if (call === calls.current.task) setTask(data);
    } catch (err) {
      if (call === calls.current.task) { setTask(null); setTaskError(failure(err, "No se pudo cargar la tarea.")); }
    } finally {
      if (call === calls.current.task) setTaskLoading(false);
    }
  };

  useEffect(() => { loadProjects(); }, [token]);
  useEffect(() => { if (selected) loadProject(selected); }, [selected, token]);
  // «#project/<id>» from a link or the Back button.
  useEffect(() => { if (route?.id && route.id !== selected) selectProject(route.id); }, [route?.id]);
  // After a task opens from a row, the detail takes the focus (on a phone it is below the list).
  useEffect(() => {
    if (!task || !focusTask.current) return;
    focusTask.current = false;
    if (heading.current) {
      heading.current.focus();
      if (heading.current.scrollIntoView) heading.current.scrollIntoView({ block: "nearest" });
    }
  }, [task]);

  const openTask = (id) => {
    setTab("stages");
    if (id !== taskId) { setTask(null); setNotice(""); setReview(NO_REVIEW); }
    loadTask(id, { focus: true });
  };

  const toggle = (key) => setCollapsed((previous) => {
    const next = new Set(previous);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const patchReview = (changes) => setReview((previous) => ({ ...previous, ...changes }));

  const sendReview = async (action) => {
    if (!task || review.busy) return;
    patchReview({ busy: true }); setTaskError(""); setNotice("");
    try {
      await apiPost("/gui/api/projects/" + enc(selected) + "/tasks/" + enc(task.id) + "/review", token, {
        action,
        expected_revision: task.revision,       // the revision the user saw: a result seen is the only one that can be decided on
        cause: action === "accept" ? "Aprobada desde la pantalla Proyectos." : review.text.trim(),
      });
      setReview(NO_REVIEW);
      await Promise.all([loadProject(selected), loadTask(task.id)]);
      announce();
      setNotice(action === "accept" ? "Tarea aprobada." : "Cambios pedidos: la tarea vuelve a su responsable.");
    } catch (err) {
      patchReview({ busy: false });
      if (err && (err.status === 409 || err.status === 404)) await Promise.all([loadProject(selected), loadTask(task.id)]);
      setTaskError(failure(err, "No se pudo guardar la decisión."));
    }
  };

  const labels = project ? labelSummary(project) : [];
  const stagesTab = project ? html`<div class=${"project-split" + (taskId ? " project-has-detail" : "")}>
      <${StagesPanel} project=${project} filter=${filter} labels=${labels} collapsed=${collapsed} selectedId=${taskId}
        onFilter=${setFilter} onToggle=${toggle} onOpen=${openTask} />
      ${taskId ? html`<${TaskDetail} project=${project} task=${task} loading=${taskLoading} error=${taskError} notice=${notice}
        review=${review} headingRef=${heading} onReview=${sendReview} onFeedback=${patchReview} />` : null}
    </div>` : null;

  return html`<section class="project-view" aria-labelledby="project-view-title">
    <div class="project-head">
      <div>
        <h3 id="project-view-title">Proyectos</h3><${Help} topic="project.view" />
      </div>
      <div class="project-picker">
        <label for="project-select">Proyecto</label>
        <div class="project-picker-row">
          <select id="project-select" value=${selected} disabled=${!projects || !projects.length}
            onChange=${(event) => { navigate("project", event.target.value); selectProject(event.target.value); }} ...${helpFor("project.select")}>
            ${list(projects).map((p) => html`<option key=${p.id} value=${p.id}>${p.title || p.name || p.id}</option>`)}
          </select>
          <button type="button" class="btn secondary" disabled=${!selected} onClick=${() => { loadProject(selected).then(announce); if (taskId) loadTask(taskId); }}
            ...${helpFor("project.refresh")}>Actualizar</button>
        </div>
        <${Help} topic="project.select" /><${Help} topic="project.refresh" compact=${true} />
      </div>
    </div>

    ${error ? html`<p class="project-error" role="alert">${error}</p>` : null}
    ${projects === null ? html`<p class="project-empty">Cargando proyectos...</p>` : null}
    ${projects && !projects.length && !error ? html`<p class="project-empty">Todavía no hay proyectos. Cuando se cree uno, aparecerá aquí.</p>` : null}
    ${selected && !project && !error && projects && projects.length ? html`<p class="project-empty">Cargando el proyecto...</p>` : null}

    ${project ? html`<${Tabs} id="project-tabs" label="Secciones del proyecto" tabs=${TABS} selected=${tab} onSelect=${setTab}
        after=${html`<${Help} topic="project.tabs" compact=${true} />`} panelProps=${{ "aria-busy": loading ? "true" : "false" }}
        ...${helpFor("project.tabs")}>
      ${tab === "overview" ? html`<${Overview} project=${project} onOpen=${openTask} />` : stagesTab}
    <//>` : null}
  </section>`;
}

// The card of the right panel: the project open in the screen (or the first one that needs the user), its progress and a link.
export function ProjectSlot({ token, route }) {
  const [state, setState] = useState({ projects: null, failed: false });
  useEffect(() => {
    let alive = true;
    const load = () => apiGet("/gui/api/projects", token)
      .then((data) => { if (alive) setState({ projects: list(data?.projects), failed: false }); })
      .catch(() => { if (alive) setState({ projects: [], failed: true }); });
    load();
    window.addEventListener(CHANGED, load);
    return () => { alive = false; window.removeEventListener(CHANGED, load); };
  }, [token]);
  const { projects, failed } = state;
  if (projects === null) return html`<p class="project-empty">Cargando proyectos...</p>`;
  if (failed) return html`<p class="project-empty">No se pudo leer la lista de proyectos.</p>`;
  const chosen = projects.find((p) => p.id === pickProject(projects, route?.section === "project" ? route.id : ""));
  if (!chosen) return html`<p class="project-empty">Todavía no hay proyectos.</p>`;
  const bar = progressOf(chosen.progress);
  return html`<div class="project-slot">
    <span class="project-title">${chosen.title || chosen.name || chosen.id}</span>
    <${Progress} label="Avance" value=${bar.value} max=${bar.max} valueText=${bar.valueText} />
    <p class="project-meta">${needsText(chosen.needs_you)}</p>
    <a class="btn secondary small" href=${routeHref("project", chosen.id)} ...${helpFor("project.open")}>Abrir proyecto</a>
    <${Help} topic="project.open" compact=${true} />
  </div>`;
}

registerView({ id: "project", label: "Proyectos", component: ProjectView, order: 40 });
registerSlot("project", ProjectSlot, { title: "Proyecto", topic: "project.slot", order: 40 });
