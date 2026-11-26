// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Project view (PRY-T03): the screen «Proyectos» (#project, #project/<id>) and a card in the right panel. «Resumen» shows the
// progress, what the user has to do (reviews, verifications, own tasks, blockers), who works now and the stages' dates;
// «Etapas y tareas» shows the stages with collapsible phases, filters and the detail of a task (dependencies, deliverables of
// its result envelope, approve or ask for changes). It only shows what /gui/api/projects answers (the core's own views). The
// one write is the review of a submitted task, through the token and the services' own rules (multiagent/gui/project_api.py).
import "./i18n.js";
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { useContentReport } from "../../js/context-report.js";
import { registerHelp, registerSlot, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { apiGet, apiPost } from "../../js/api.js";
import { authHeaders } from "../../js/auth.js";
import { t } from "../../js/i18n.js";
import { navigate, routeHref } from "../../js/route.js";
import { parseBlock } from "../../js/stream.js";
import { Progress } from "../../js/ui/progress.js";
import { Tabs } from "../../js/ui/tabs.js";
import { HELP } from "./help.js";
import { labelSummary, list, NO_LIST_FILTER, needsText, pickProject, progressOf } from "./model.js";
import { ListPanel, Overview, StagesPanel, TaskDetail } from "./parts.js";

registerHelp(HELP, "project");

const NO_FILTER = { text: "", label: "" };
const NO_REVIEW = { open: false, text: "", busy: false };
const enc = encodeURIComponent;
const CHANGED = "project:changed";      // the screen tells the panel card that the numbers moved (a decision, a refresh)
const announce = () => window.dispatchEvent(new Event(CHANGED));
const PROJECT_POLL_MS = 10000;
const PROJECT_REFRESH_MS = 1000;
const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 30000;

// The shared stream parser handles SSE framing; this view supplies the project URL and lifecycle.
export function followProject({ token, projectId, after, onChange, onState }) {
  let stopped = false;
  let controller = null;
  let cursor = after;
  let delay = RECONNECT_MIN_MS;
  let timer = null;
  let wake = null;
  (async () => {
    while (!stopped) {
      controller = new AbortController();
      let connected = false;
      try {
        const response = await fetch("/gui/api/stream?project=" + enc(projectId) + "&after=" + enc(cursor), {
          headers: { ...authHeaders(token), Accept: "text/event-stream", "Last-Event-ID": String(cursor) },
          cache: "no-store", signal: controller.signal,
        });
        if (stopped) return;
        if (response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
          onState("unavailable");
          return;
        }
        if (!response.ok || !response.body) throw new Error("Project stream unavailable");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done || stopped) break;
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
          let end;
          while ((end = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, end);
            buffer = buffer.slice(end + 2);
            if (block === ": connected") {
              connected = true;
              delay = RECONNECT_MIN_MS;
              onState("connected");
              continue;
            }
            const event = parseBlock(block);
            if (event?.type !== "project") continue;
            try {
              const seq = JSON.parse(event.data)?.seq;
              if (Number.isSafeInteger(seq) && seq > cursor) { cursor = seq; onChange(seq); }
            } catch (_) { /* ignore malformed notices */ }
          }
        }
        if (!stopped && !connected) { onState("unavailable"); return; }
        if (!stopped) onState("reconnecting");
      } catch (_) {
        if (stopped) return;
        onState("failed");
      }
      if (stopped) return;
      await new Promise((resolve) => {
        wake = resolve;
        timer = setTimeout(resolve, delay);
      });
      timer = null;
      wake = null;
      delay = Math.min(delay * 2, RECONNECT_MAX_MS);
    }
  })();
  return () => {
    stopped = true;
    if (controller) controller.abort();
    if (timer !== null) clearTimeout(timer);
    if (wake) wake();
  };
}

export function projectPollFallback(refresh) {
  let timer = null;
  return {
    onState(state) {
      if (state === "connected" || state === "unavailable") {
        if (timer !== null) { clearInterval(timer); timer = null; }
      } else if (timer === null) {
        timer = setInterval(refresh, PROJECT_POLL_MS);
      }
    },
    stop() { if (timer !== null) { clearInterval(timer); timer = null; } },
  };
}

export function projectRefreshQueue(seenProject, refresh) {
  let lastRefresh = 0;
  let timer = null;
  let stopped = false;
  const refreshNow = () => {
    if (stopped) return;
    lastRefresh = Date.now();
    refresh();
  };
  return {
    refresh: refreshNow,
    queue(seq) {
      if (stopped || seq <= seenProject.current.seq) return;
      seenProject.current.seq = seq;
      if (timer !== null) return;
      timer = setTimeout(() => { timer = null; refreshNow(); },
        Math.max(0, PROJECT_REFRESH_MS - (Date.now() - lastRefresh)));
    },
    stop() { stopped = true; if (timer !== null) clearTimeout(timer); },
  };
}
// What the user reads when a call fails: a clash of revisions or a missing task gets its own words; the rest is the server's.
function failure(error, fallbackKey) {
  if (error && error.status === 409) return t("ext.project.error.changed");
  if (error && error.status === 404) return t("ext.project.error.missing");
  return (error && error.message) || t(fallbackKey);
}

export function ProjectView({ token, route }) {
  const tabs = [
    { id: "overview", label: t("ext.project.tab.overview") },
    { id: "stages", label: t("ext.project.tab.stages") },
    { id: "list", label: t("ext.project.tab.list") },
  ];
  const [projects, setProjects] = useState(null);       // null while the list loads
  const [selected, setSelected] = useState(route?.id || "");
  const [project, setProject] = useState(null);
  const [costs, setCosts] = useState(null);             // PRY-T07: tokens and API cost; null until read or when they cannot be
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("overview");
  const [filter, setFilter] = useState(NO_FILTER);
  const [listFilter, setListFilter] = useState(NO_LIST_FILTER);
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [taskId, setTaskId] = useState("");
  const [task, setTask] = useState(null);
  const [taskLoading, setTaskLoading] = useState(false);
  const [taskError, setTaskError] = useState("");
  const [notice, setNotice] = useState("");
  const [review, setReview] = useState(NO_REVIEW);
  const calls = useRef({ project: 0, task: 0, costs: 0 });          // an answer that arrives after a newer request is dropped
  const seenProject = useRef({ id: "", seq: 0 });
  const heading = useRef(null);
  const focusTask = useRef(false);
  const taskIdRef = useRef("");
  taskIdRef.current = taskId;

  const closeTask = () => {
    calls.current.task += 1;
    setTaskId(""); setTask(null); setTaskLoading(false); setTaskError(""); setNotice(""); setReview(NO_REVIEW);
  };

  const selectProject = (id) => {
    calls.current.project += 1;
    seenProject.current = { id, seq: 0 };
    calls.current.costs += 1;
    setSelected(id); setProject(null); setCosts(null); setError(""); setFilter(NO_FILTER); setListFilter(NO_LIST_FILTER); setCollapsed(new Set()); closeTask();
  };

  const loadProjects = async () => {
    try {
      const data = await apiGet("/gui/api/projects", token);
      const all = list(data?.projects);
      setProjects(all);
      setSelected((current) => (current && (all.some((p) => p.id === current) || current === route?.id) ? current : pickProject(all, route?.id)));
    } catch (err) {
      setProjects([]);
      setError(failure(err, "ext.project.error.load_projects"));
    }
  };

  const loadProject = async (id) => {
    if (!id) return;
    const call = ++calls.current.project;
    setLoading(true); setError("");
    try {
      const data = await apiGet("/gui/api/projects/" + enc(id), token);
      if (call === calls.current.project) {
        const seq = data?.last_change?.seq || 0;
        seenProject.current = { id, seq: Math.max(seenProject.current.id === id ? seenProject.current.seq : 0, seq) };
        setProject(data);
        loadCosts(id);
      }
    } catch (err) {
      if (call === calls.current.project) setError(failure(err, "ext.project.error.load_project"));
    } finally {
      if (call === calls.current.project) setLoading(false);
    }
  };

  // The costs come apart from the project: if they cannot be read the summary shows «sin dato» and nothing else changes.
  const loadCosts = async (id) => {
    const call = ++calls.current.costs;
    try {
      const data = await apiGet("/gui/api/projects/" + enc(id) + "/costs", token);
      if (call === calls.current.costs) setCosts(data);
    } catch (_) {
      if (call === calls.current.costs) setCosts(null);
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
      if (call === calls.current.task) { setTask(null); setTaskError(failure(err, "ext.project.error.load_task")); }
    } finally {
      if (call === calls.current.task) setTaskLoading(false);
    }
  };

  useEffect(() => { loadProjects(); }, [token]);
  useEffect(() => { if (selected) loadProject(selected); }, [selected, token]);
  useEffect(() => {
    if (!selected || project?.id !== selected) return;
    if (seenProject.current.id !== selected) seenProject.current = { id: selected, seq: project.last_change?.seq || 0 };
    let alive = true;
    const refresh = () => {
      if (!alive) return;
      loadProject(selected).then(() => { if (alive) announce(); });
      if (taskIdRef.current) loadTask(taskIdRef.current);
    };
    const refreshQueue = projectRefreshQueue(seenProject, refresh);
    const fallback = projectPollFallback(refreshQueue.refresh);
    const stop = followProject({ token, projectId: selected, after: seenProject.current.seq, onChange: refreshQueue.queue,
      onState: (state) => { if (alive) fallback.onState(state); } });
    return () => {
      alive = false;
      stop();
      refreshQueue.stop();
      fallback.stop();
    };
  }, [selected, project?.id, token]);
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

  const openTask = (id, inTab = "stages") => {
    setTab(inTab);
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
        cause: action === "accept" ? t("ext.project.approved_from_view") : review.text.trim(),
      });
      setReview(NO_REVIEW);
      await Promise.all([loadProject(selected), loadTask(task.id)]);
      announce();
      setNotice(action === "accept" ? t("ext.project.notice.approved") : t("ext.project.notice.changes_requested"));
    } catch (err) {
      patchReview({ busy: false });
      if (err && (err.status === 409 || err.status === 404)) await Promise.all([loadProject(selected), loadTask(task.id)]);
      setTaskError(failure(err, "ext.project.error.save_decision"));
    }
  };

  const labels = project ? labelSummary(project) : [];
  const stagesTab = project ? html`<div class=${"project-split" + (taskId ? " project-has-detail" : "")}>
      <${StagesPanel} project=${project} filter=${filter} labels=${labels} collapsed=${collapsed} selectedId=${taskId}
        onFilter=${setFilter} onToggle=${toggle} onOpen=${openTask} />
      ${taskId ? html`<${TaskDetail} project=${project} task=${task} loading=${taskLoading} error=${taskError} notice=${notice}
        review=${review} headingRef=${heading} onReview=${sendReview} onFeedback=${patchReview} />` : null}
    </div>` : null;

  const listTab = project ? html`<div class=${"project-split" + (taskId ? " project-has-detail" : "")}>
      <${ListPanel} project=${project} filter=${listFilter} labels=${labels} selectedId=${taskId} onFilter=${setListFilter}
        onOpen=${(id) => openTask(id, "list")} />
      ${taskId ? html`<${TaskDetail} project=${project} task=${task} loading=${taskLoading} error=${taskError} notice=${notice}
        review=${review} headingRef=${heading} onReview=${sendReview} onFeedback=${patchReview} />` : null}
    </div>` : null;

  return html`<section class="project-view" aria-label=${t("ext.project.title")}>
    <div class="project-head">
      <div class="project-picker">
        <label for="project-select">${t("ext.project.project")}</label>
        <div class="project-picker-row">
          <select id="project-select" value=${selected} disabled=${!projects || !projects.length}
            onChange=${(event) => { navigate("project", event.target.value); selectProject(event.target.value); }} ...${helpFor("project.select")}>
            ${list(projects).map((p) => html`<option key=${p.id} value=${p.id}>${p.title || p.name || p.id}</option>`)}
          </select>
          <button type="button" class="btn secondary" disabled=${!selected} onClick=${() => { loadProject(selected).then(announce); if (taskId) loadTask(taskId); }}
            ...${helpFor("project.refresh")}>${t("ext.project.refresh")}</button>
        </div>
        <${Help} topic="project.select" /><${Help} topic="project.refresh" compact=${true} />
      </div>
    </div>

    ${error ? html`<p class="project-error" role="alert">${error}</p>` : null}
    ${projects === null ? html`<p class="project-empty">${t("ext.project.loading.projects")}</p>` : null}
    ${projects && !projects.length && !error ? html`<p class="project-empty">${t("ext.project.empty.projects")}</p>` : null}
    ${selected && !project && !error && projects && projects.length ? html`<p class="project-empty">${t("ext.project.loading.project")}</p>` : null}

    ${project ? html`<${Tabs} id="project-tabs" label=${t("ext.project.tabs.aria_label")} tabs=${tabs} selected=${tab} onSelect=${setTab}
        after=${html`<${Help} topic="project.tabs" compact=${true} />`} panelProps=${{ "aria-busy": loading ? "true" : "false" }}
        ...${helpFor("project.tabs")}>
      ${tab === "overview" ? html`<${Overview} project=${project} costs=${costs} onOpen=${openTask} />` : tab === "list" ? listTab : stagesTab}
    <//>` : null}
  </section>`;
}

// The card of the right panel: the project open in the screen (or the first one that needs the user), its progress and a link.
export function ProjectSlot({ token, route, onContent }) {
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
  useContentReport(onContent, projects !== null && !failed && projects.length > 0);   // GUI3-T04
  if (projects === null) return html`<p class="project-empty">${t("ext.project.loading.projects")}</p>`;
  if (failed) return html`<p class="project-empty">${t("ext.project.error.read_projects")}</p>`;
  const chosen = projects.find((p) => p.id === pickProject(projects, route?.section === "project" ? route.id : ""));
  if (!chosen) return html`<p class="project-empty">${t("ext.project.empty.projects_short")}</p>`;
  const bar = progressOf(chosen.progress);
  return html`<div class="project-slot">
    <span class="project-title">${chosen.title || chosen.name || chosen.id}</span>
    <${Progress} label=${t("ext.project.progress.title")} value=${bar.value} max=${bar.max} valueText=${bar.valueText} />
    <p class="project-meta">${needsText(chosen.needs_you)}</p>
    <a class="btn secondary small" href=${routeHref("project", chosen.id)} ...${helpFor("project.open")}>${t("ext.project.open")}</a>
    <${Help} topic="project.open" compact=${true} />
  </div>`;
}

registerView({ id: "project", owner: "project", group: "primary", icon: "folder", label: t("ext.project.title"), topic: "project.view", component: ProjectView, order: 40, mode: "simple" });
registerSlot("project", ProjectSlot, { title: t("ext.project.project"), topic: "project.slot", owner: "project", order: 40 });
