// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The pure part of the work map (MAP-T01): the summary line, the project/stage/app filters, "what is left" and the
// remembered layout. No DOM, no network and no imports: work-map.js draws what this module computes, and Node tests it.
// The filters work on the rows the map already loaded (/gui/api/map?only=all); nothing here is sent to the server.

const MAX_FILTER = 200;
const DONE = new Set(["done", "completed", "integrated", "closed", "success", "archived"]);
const DOING = new Set(["doing", "running", "in_progress"]);
const REVIEW = new Set(["review", "waiting_review"]);

export const emptyFilters = () => ({ project: "", stage: "", app: "", left: false });

const clean = (value) => typeof value === "string" ? value.slice(0, MAX_FILTER) : "";

export function normalizeFilters(filters) {
  const source = filters && typeof filters === "object" ? filters : {};
  return { project: clean(source.project), stage: clean(source.stage), app: clean(source.app).toLowerCase(), left: source.left === true };
}

export const sameFilters = (a, b) => {
  const x = normalizeFilters(a);
  const y = normalizeFilters(b);
  return x.project === y.project && x.stage === y.stage && x.app === y.app && x.left === y.left;
};

// The address that carries the filters: "#map" or "#map/<url-encoded query>" (route.js reads the part after the slash as the id).
export function mapAddress(filters) {
  const f = normalizeFilters(filters);
  const params = new URLSearchParams();
  if (f.project) params.set("project", f.project);
  if (f.stage) params.set("stage", f.stage);
  if (f.app) params.set("app", f.app);
  if (f.left) params.set("left", "1");
  const query = params.toString();
  return "#map" + (query ? "/" + encodeURIComponent(query) : "");
}

export function parseMapAddress(hash) {
  const text = String(hash || "").replace(/^#\/?/, "");
  if (text !== "map" && !text.startsWith("map/")) return emptyFilters();
  let query = "";
  try { query = decodeURIComponent(text.slice(4)); } catch (_) { return emptyFilters(); }
  const params = new URLSearchParams(query);
  return normalizeFilters({
    project: params.get("project") || "", stage: params.get("stage") || "", app: params.get("app") || "", left: params.get("left") === "1",
  });
}

const stateKey = (task) => String(task?.label?.key || task?.status || task?.state || "").toLowerCase();
export const taskBucket = (task) => {
  const key = stateKey(task);
  if (DONE.has(key)) return "done";
  if (key === "cancelled") return "cancelled";
  if (key === "blocked") return "blocked";
  if (REVIEW.has(key)) return "review";
  if (DOING.has(key)) return "doing";
  return "other";
};
// What is not finished: a done or cancelled task is out; a failed or stopped one still needs somebody.
const isLeft = (task) => !["done", "cancelled"].includes(taskBucket(task));
const taskId = (node) => String(node?.id || "").replace(/^task:/, "");
const appOf = (value) => String(value || "").toLowerCase();
const taskApp = (task) => {
  const info = task?.assignee_info;
  const text = typeof info === "string" ? info : (info && typeof info === "object" ? (info.source || info.app || "") : "");
  return appOf(text || (typeof task?.assignee === "string" ? task.assignee : "") || (typeof task?.preferred === "string" ? task.preferred : ""));
};
const stageTasks = (stage) => (stage?.tasks || []).filter((task) => task && typeof task === "object");

export function mapFilterOptions(data) {
  const projects = (data?.projects || []).map((project) => ({ value: String(project.id), label: project.title || project.name || String(project.id) }));
  const stages = [];
  const several = (data?.projects || []).length > 1;
  for (const project of data?.projects || []) {
    for (const stage of project.stages || []) {
      const value = String(stage.group_id || stage.code || "");
      if (!value) continue;
      const title = stage.title || stage.code || value;
      stages.push({ value, project: String(project.id), label: several ? `${title} · ${project.title || project.name || project.id}` : title });
    }
  }
  const apps = new Set();
  for (const session of Object.values(data?.sessions || {})) {
    if (session?.source) apps.add(appOf(session.source));   // only what the filter can find: a session of that app
  }
  return { projects, stages, apps: [...apps].sort() };
}

// Drops the values the loaded data does not have (a stale address), so a filter never hides everything by a typo.
export function sanitizeFilters(filters, options) {
  const f = normalizeFilters(filters);
  if (!options) return f;
  if (f.project && !options.projects.some((item) => item.value === f.project)) f.project = "";
  if (f.stage && !options.stages.some((item) => item.value === f.stage && (!f.project || item.project === f.project))) f.stage = "";
  if (f.app && !options.apps.includes(f.app)) f.app = "";
  return f;
}

function descendants(sessions, roots) {
  const found = new Set();
  const queue = [...roots].filter((id) => sessions[id]);
  while (queue.length) {
    const id = queue.pop();
    if (found.has(id)) continue;
    found.add(id);
    for (const [other, value] of Object.entries(sessions)) if (value?.parent_id === id) queue.push(other);
    for (const child of sessions[id]?.children || []) if (child?.link_id && sessions[child.link_id]) queue.push(child.link_id);
  }
  return found;
}

// The rows of the map under the chosen filters, in the same shape: projectRows and mapMetrics read the result.
export function applyMapFilters(data, filters) {
  const f = normalizeFilters(filters);
  if (!data || !(f.project || f.stage || f.app || f.left)) return data;
  const sessions = data.sessions || {};
  const links = data.links || [];
  const appSessions = new Set(f.app ? Object.keys(sessions).filter((id) => appOf(sessions[id]?.source) === f.app) : []);
  const keepTask = (task) => {
    if (f.left && !isLeft(task)) return false;
    if (!f.app) return true;
    return links.some((link) => link.task_id === task.id && appSessions.has(link.session_id)) || taskApp(task) === f.app;
  };
  const keptIds = new Set();
  const projects = [];
  for (const project of data.projects || []) {
    if (f.project && String(project.id) !== f.project) continue;
    const stages = [];
    for (const stage of project.stages || []) {
      if (f.stage && String(stage.group_id || stage.code || "") !== f.stage) continue;
      const tasks = stageTasks(stage).filter(keepTask);
      if ((f.app || f.left) && !tasks.length) continue;
      tasks.forEach((task) => keptIds.add(task.id));
      stages.push({ ...stage, tasks });
    }
    if ((f.app || f.left) && !stages.length) continue;
    const next = { ...project, stages };
    if (project.attention && typeof project.attention === "object") {
      const keep = (items) => (items || []).filter((task) => keptIds.has(task.id));
      const attention = project.attention;
      next.attention = {
        ...attention, reviews: keep(attention.reviews), mine: keep(attention.mine), blocked: keep(attention.blocked),
        verifications: f.app ? [] : (attention.verifications || []).filter((stage) => !f.stage || String(stage.group_id || stage.code || "") === f.stage),
      };
    }
    projects.push(next);
  }
  const keptLinks = links.filter((link) => keptIds.has(link.task_id) && (!f.app || appSessions.has(link.session_id)));
  let kept;
  if (f.app && !(f.project || f.stage || f.left)) kept = appSessions;
  else if (f.project || f.stage || f.left) {
    kept = descendants(sessions, keptLinks.map((link) => link.session_id));
    if (f.app) kept = new Set([...kept].filter((id) => appSessions.has(id)));
  } else kept = new Set(Object.keys(sessions));
  const scoped = {};
  for (const id of Object.keys(sessions)) if (kept.has(id)) scoped[id] = sessions[id];
  return { ...data, projects, sessions: scoped, links: f.app ? links.filter((link) => keptIds.has(link.task_id) && appSessions.has(link.session_id)) : keptLinks };
}

const sessionTokens = (tokens) => {
  if (!tokens || typeof tokens !== "object") return null;
  const input = Number.isFinite(tokens.input) ? tokens.input : Number.isFinite(tokens.input_tokens) ? tokens.input_tokens : null;
  const output = Number.isFinite(tokens.output) ? tokens.output : Number.isFinite(tokens.output_tokens) ? tokens.output_tokens : null;
  return input === null && output === null ? null : (input || 0) + (output || 0);
};

// The figures of the summary line, from the rows the map loaded. A figure that is not known is null and is not drawn.
export function mapMetrics(data) {
  if (!data) return null;
  const counts = { doing: 0, review: 0, blocked: 0, done: 0, total: 0 };
  const seen = new Set();
  for (const project of data.projects || []) {
    const blocked = new Set((project.attention?.blocked || []).map((task) => task.id));
    for (const stage of project.stages || []) {
      for (const task of stageTasks(stage)) {
        if (seen.has(task.id)) continue;
        seen.add(task.id);
        const bucket = taskBucket(task);
        if (bucket === "cancelled") continue;
        counts.total += 1;
        if (bucket === "done") counts.done += 1;
        else if (bucket === "doing") counts.doing += 1;
        else if (bucket === "review") counts.review += 1;
        else if (bucket === "blocked" || blocked.has(task.id)) counts.blocked += 1;
      }
    }
  }
  let tokens = null;
  for (const session of Object.values(data.sessions || {})) {
    const count = sessionTokens(session?.tokens);
    if (count !== null) tokens = (tokens || 0) + count;
  }
  return { projects: (data.projects || []).filter((project) => !project.archived).length, ...counts, tokens };
}

// "What is left": the unfinished tasks, the ones that hold up the most other tasks first (ties keep the map's order).
export function orderRemaining(nodes) {
  const tasks = [];
  const seen = new Set();
  for (const node of nodes || []) {
    if (node.kind !== "task" || seen.has(node.id) || !isLeft({ status: node.status })) continue;
    seen.add(node.id);
    tasks.push(node);
  }
  const holds = new Map();
  const pairs = new Set();
  const hold = (blocker, blocked) => {
    const pair = blocker + "\0" + blocked;
    if (pairs.has(pair)) return;
    pairs.add(pair);
    holds.set(blocker, (holds.get(blocker) || 0) + 1);
  };
  for (const node of tasks) {
    for (const id of node.dependsOn || []) hold(String(id), taskId(node));
    for (const blocker of node.blockers || []) if (blocker?.task_id) hold(String(blocker.task_id), taskId(node));
  }
  return tasks.map((node, index) => ({ node: { ...node, blocks: holds.get(taskId(node)) || 0 }, index }))
    .sort((a, b) => b.node.blocks - a.node.blocks || a.index - b.index).map((entry) => entry.node);
}

// The map opens as a list; a remembered "tree" is kept, except on a narrow screen, where the tree never fit.
export const initialLayout = (remembered, narrow) => remembered === "tree" && !narrow ? "tree" : "list";
