// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Left sidebar (GUI2-T02): "Nueva sesión", the «Buscar» row (Ctrl+K, GUI3-T07), and sessions and framework runs grouped by project. The
// «Actividades» filter (a button of the list header) instead shows open project activities and links to their boards. The shell (shell.js) puts it
// inside <nav id="sidebar">. Names, titles and folders are untrusted text: they are only ever rendered as text nodes.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { debounce, subscribe } from "./bus.js";
import { Help, helpFor } from "./help.js";
import { navigate, officeHref, routeHref, NEW_SECTION } from "./route.js";
import { CommandPalette } from "./command-palette.js";
import { helpEntries, isPaletteKey, itemEntries, sectionEntries } from "./command-palette-model.js";
import { isRegisteredView, registeredViews } from "./extensions.js";
import { visibleIn } from "./gui-mode.js";
import { HELP } from "./help-text.js";
import { pendingTabs, tabRoute } from "./pending-model.js";
import { relativeTime } from "./session-text.js";
import { activityItems, appLabel, buildGroups, countsOf, isCurrent, LIST_LIMIT, latestFirst, limitRows, navigationSections, officeRows, projectRows, runItems, sessionItems } from "./sidebar-model.js";
import { inStartSidebar } from "./start-model.js";   // INI-T03
import { refreshStart, useStartStatus } from "./start.js";   // INI-T03
import { onChoiceChange, readMoreNoticeSeen, readPinnedSections, readSectionOpen, writeHelpMode, writeMoreNoticeSeen, writeSectionOpen } from "./shell-state.js";
import { PendingLink } from "./pending.js";
import { Icon } from "./ui/icon.js";
import { StatusChip } from "./ui/status-chip.js";
import { t } from "./i18n.js";

const SESSION_LIMIT = 100;
const RUN_LIMIT = 50;
const EVENT_DEBOUNCE_MS = 1500; // runs change with the event stream
const REFRESH_MS = 60000;       // sessions of the apps are files: they are looked at again once a minute

// The dot is a StatusChip (ui/status-chip.js): a mark whose shape differs per state, with the words for screen readers.
function Item({ item, current, now }) {
  const meta = item.who
    ? appLabel(item.app) + " · " + item.who + " · " + relativeTime(item.updatedAt, now)
    : appLabel(item.app) + " · " + relativeTime(item.updatedAt, now);
  return html`<li>
    <a class="side-item" href=${routeHref(item.route[0], item.route[1])} aria-current=${current ? "page" : null}
      title=${item.folder || item.title}>
      ${item.dotLabels
        ? html`<${StatusChip} status=${item.dot} labels=${item.dotLabels} variant="dot" />`
        : html`<${StatusChip} status=${item.dot} labels="dot" variant="dot" detail=${item.detail} />`}
      <span class="side-text">
        <span class="side-title">${item.title}</span>
        <span class="side-meta">${meta}</span>
      </span>
    </a>
  </li>`;
}

function Group({ group, route, now }) {
  const counts = countsOf(group);
  return html`<details class="group" open>
    <summary class="group-head" title=${group.folders.join("\n") || group.name}>
      <span class="group-name">${group.name}</span>
      <span class="group-counts">${counts.map((entry) => html`<span key=${entry.app} class=${"count src-" + entry.app}>
        <span aria-hidden="true">${entry.label} ${entry.count}</span><span class="sr-only">${t("shell.group_count", { count: entry.count, label: entry.label })}</span></span>`)}</span>
    </summary>
    <ul class="group-items">
      ${group.items.map((item) => html`<${Item} key=${item.key} item=${item} current=${isCurrent(item, route)} now=${now} />`)}
    </ul>
  </details>`;
}

/* GUI4-T03 */
// A folding section of the bar (Oficinas, Proyectos, Recientes): the title is a button that folds it, «| N» is the count, and the
// «+» (when there is one) is a button beside it. The folded state is remembered in this browser (shell-state.js).
function SideSection({ id, title, count, addLabel = "", onAdd = null, children }) {
  const [open, setOpen] = useState(() => readSectionOpen(id));
  const toggle = () => { const next = !open; setOpen(next); writeSectionOpen(id, next); };
  const labelId = "side-" + id + "-label";
  const bodyId = "side-" + id + "-body";
  return html`<section class="side-section" data-section=${id} aria-labelledby=${labelId}>
    <div class="side-section-head">
      <button type="button" class="side-section-toggle" id=${labelId} data-help-exempt="navigation" aria-expanded=${open ? "true" : "false"}
        aria-controls=${bodyId} onClick=${toggle}><span class="section-caret" aria-hidden="true">${open ? "▾" : "▸"}</span><span>${title}</span><span class="section-count" aria-hidden="true"> | ${count}</span><span class="sr-only">, ${count}</span></button>
      ${onAdd && html`<button type="button" class="side-mini" data-nav data-help-exempt="navigation" aria-label=${addLabel} title=${addLabel}
        onClick=${onAdd}><span aria-hidden="true">+</span></button>`}
    </div>
    <div class="side-section-body" id=${bodyId} hidden=${!open}>${children}</div>
  </section>`;
}

// Five rows and «Ver N más»; a row that waits for the person is always shown (limitRows).
function LimitedList({ rows, render }) {
  const [expanded, setExpanded] = useState(false);
  const { shown, hidden } = limitRows(rows, expanded);
  return html`<div class="side-list">
    ${shown.map(render)}
    ${(hidden > 0 || (expanded && rows.length > LIST_LIMIT)) && html`<button type="button" class="show-list-more" data-help-exempt="navigation"
      aria-expanded=${expanded ? "true" : "false"} onClick=${() => setExpanded((value) => !value)}>${expanded ? t("shell.show_fewer") : t("shell.show_more", { count: hidden })}</button>`}
  </div>`;
}

// One office: a <details> (its areas load when it opens) with the «+» beside the summary, not inside it, so the keyboard and
// touch reach it. The «Ver plano» row is there only while the floor-plan view is registered.
function OfficeRow({ office, token, plan }) {
  const [areas, setAreas] = useState(null);
  const onToggle = (event) => {
    if (!event.currentTarget.open || areas !== null) return;
    apiGet("/gui/api/offices/" + encodeURIComponent(office.id), token).then(
      (detail) => setAreas(Array.isArray(detail?.areas) ? detail.areas.filter((area) => area && typeof area.id === "string") : []),
      () => setAreas([]),
    );
  };
  return html`<div class="office-row">
    <details onToggle=${onToggle}>
      <summary class="office-folder"><span class="side-text"><span class="side-title">${office.name}</span></span>${office.attention && html`<${StatusChip} status="error" labels="dot" variant="dot" detail=${t("shell.needs_attention")} />`}</summary>
      <div class="office-areas" role="group" aria-label=${t("shell.office_areas", { name: office.name })}>
        ${(areas || []).map((area) => html`<div class="area-row" key=${area.id}><span class="side-title">${area.name || area.id}</span>
          <button type="button" class="side-mini" data-nav data-help-exempt="navigation" aria-label=${t("shell.new_in", { name: area.name || area.id })}
            title=${t("shell.new_in", { name: area.name || area.id })} onClick=${() => { location.hash = officeHref(office.id, area.id); }}><span aria-hidden="true">+</span></button></div>`)}
        ${plan && html`<a class="side-item office-plan" href=${routeHref("floor_plan", office.id)}><${Icon} name="building" /><span class="side-title">${t("shell.view_plan")}</span></a>`}
      </div>
    </details>
    <button type="button" class="side-mini office-plus" data-nav data-help-exempt="navigation" aria-label=${t("shell.new_in", { name: office.name })}
      title=${t("shell.new_in", { name: office.name })} onClick=${() => { location.hash = officeHref(office.id); }}><span aria-hidden="true">+</span></button>
  </div>`;
}

export function Sidebar({ token, status, route, sections, mode = "advanced" }) {
  const [data, setData] = useState({ sessions: [], runs: [], hint: null, failed: [], loaded: false });
  const [activityData, setActivityData] = useState({ projects: [], loaded: false, loading: false, failed: false });
  const [activitiesMode, setActivitiesMode] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [pinned, setPinned] = useState(readPinnedSections);
  const [noticeSeen, setNoticeSeen] = useState(readMoreNoticeSeen);
  const [moreOpen, setMoreOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [lists, setLists] = useState({ offices: [], projects: [] });   // GUI4-T03: the rows of «Oficinas» and «Proyectos»
  const ticket = useRef(0);
  const activityTicket = useRef(0);
  const start = useStartStatus(token);   // INI-T03: one shared request; «Inicio» leads the list while a step is missing
  useEffect(() => { refreshStart(token); }, [route.section]);

  async function load() {
    const mine = ++ticket.current;
    const [sessions, runs] = await Promise.allSettled([
      apiGet("/gui/api/sessions?limit=" + SESSION_LIMIT, token),
      apiGet("/gui/api/runs?limit=" + RUN_LIMIT, token),
    ]);
    if (mine !== ticket.current) return;
    const ok = (result) => result.status === "fulfilled";
    setData((previous) => ({
      sessions: ok(sessions) ? sessions.value.sessions : previous.sessions,
      hint: ok(sessions) ? sessions.value.hint || null : previous.hint,
      runs: ok(runs) ? runs.value : previous.runs,
      failed: [ok(sessions) ? "" : t("shell.sessions"), ok(runs) ? "" : t("shell.runs")].filter(Boolean),
      loaded: true,
    }));
  }

  // «Oficinas» and «Proyectos» are read from the same routes as their views; a list that cannot be read stays as it was.
  async function loadLists() {
    const [offices, projects] = await Promise.allSettled([
      apiGet("/gui/api/offices", token),
      apiGet("/gui/api/projects", token),
    ]);
    setLists((previous) => ({
      offices: offices.status === "fulfilled" ? officeRows(offices.value?.offices) : previous.offices,
      projects: projects.status === "fulfilled" ? projectRows(projects.value?.projects) : previous.projects,
    }));
  }

  async function loadActivities() {
    const mine = ++activityTicket.current;
    setActivityData((previous) => ({ ...previous, loading: true, failed: false }));
    try {
      const answer = await apiGet("/gui/api/activities", token);
      if (!Array.isArray(answer?.projects)) throw new Error(t("shell.invalid_activities_response"));
      if (mine === activityTicket.current) {
        setActivityData({ projects: answer.projects, loaded: true, loading: false, failed: false });
      }
    } catch (_) {
      if (mine === activityTicket.current) {
        setActivityData({ projects: [], loaded: true, loading: false, failed: true });
      }
    }
  }

  useEffect(() => onChoiceChange((change) => { if (change.pinned) setPinned(change.pinned); }), []);   // «Personalizar» pins and releases sections
  useEffect(() => {
    const onKey = (event) => {
      if (!isPaletteKey(event)) return;
      event.preventDefault();
      setPaletteOpen(true);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { load(); loadLists(); }, [token]);
  useEffect(() => { if (status === "connected") { load(); loadLists(); } }, [status]);
  useEffect(() => {
    const refresh = debounce(load, EVENT_DEBOUNCE_MS);
    const off = subscribe(() => refresh());
    return () => { off(); refresh.cancel(); };
  }, [token]);
  useEffect(() => {
    const timer = setInterval(() => { setNow(Date.now()); load(); loadLists(); }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [token]);
  useEffect(() => {
    if (activitiesMode) loadActivities();
    else activityTicket.current += 1;
  }, [activitiesMode, token]);

  const items = activitiesMode
    ? activityItems(activityData.projects)
    : sessionItems(data.sessions).concat(runItems(data.runs, mode));
  const groups = buildGroups(items);
  const shown = groups.reduce((total, group) => total + group.items.length, 0);
  const [recentExpanded, setRecentExpanded] = useState(false);
  const recent = limitRows(latestFirst(groups.flatMap((group) => group.items)), recentExpanded);
  const recentKeys = new Set(recent.shown.map((item) => item.key));
  const recentGroups = recentExpanded ? groups : groups.map((group) => ({ ...group, items: group.items.filter((item) => recentKeys.has(item.key)) })).filter((group) => group.items.length > 0);
  const hasFloorPlan = isRegisteredView("floor_plan");
  const navigation = navigationSections(sections.filter((s) => !s.group), sections.filter((s) => s.group), pinned, mode, inStartSidebar(start.status));
  const dismissNotice = () => { setNoticeSeen(true); writeMoreNoticeSeen(); };
  useEffect(() => {
    if (route.section !== "sessions" && navigation.more.some((section) => section.id === route.section)) {
      setMoreOpen(true);
      if (!noticeSeen) dismissNotice();
    }
  }, [route.section]);
  const toggleMore = () => {
    setMoreOpen((open) => !open);
    if (!noticeSeen) dismissNotice();
  };

  let summary = "";
  if (activitiesMode && (!activityData.loaded || activityData.loading)) summary = t("shell.loading_activities");
  else if (activitiesMode && activityData.failed) summary = t("shell.activities_failed");
  else if (activitiesMode && items.length === 0) summary = t("shell.no_open_activities");
  else if (!activitiesMode && !data.loaded) summary = t("shell.loading_list");
  else if (!activitiesMode && items.length === 0) summary = t("shell.no_sessions_or_runs");
  else summary = t("shell.list_summary", {
    count: shown, item_label: shown === 1 ? t("shell.item") : t("shell.items"),
    projects: groups.length, project_label: groups.length === 1 ? t("shell.project") : t("shell.projects"),
  });

  /* GUI4-T08 */
  const paletteEntries = [
    ...sectionEntries(navigation, pendingTabs(registeredViews().filter((view) => visibleIn(view, mode)), [], false)
      .map((tab) => ({ ...tab, route: tabRoute(tab.id) })), mode),
    ...itemEntries(sessionItems(data.sessions).concat(runItems(data.runs, mode)), mode),
    ...helpEntries(HELP),
  ];

  return html`<div class="side">
    <div class="side-top">
      <div class="side-new">
        <button type="button" class="btn side-new-btn" data-nav aria-current=${route.section === NEW_SECTION ? "page" : null}
          ...${helpFor("shell.new_session")} onClick=${() => navigate(NEW_SECTION)}><span aria-hidden="true">+ </span>${t("shell.new_session")}</button>
        <${Help} topic="shell.new_session" compact=${true} />
      </div>
      <div class="side-search">
        <button type="button" class="side-search-btn" aria-keyshortcuts="Control+K" ...${helpFor("shell.search")}
          onClick=${() => setPaletteOpen(true)}><${Icon} name="search" />${t("shell.search")}<kbd class="side-kbd">Ctrl K</kbd></button>
        <${Help} topic="shell.search" compact=${true} />
      </div>
      <div class="views" aria-label=${t("shell.sections")}>
        <div class="side-pending"><${PendingLink} current=${route.section === "pending" || navigation.pending.some((section) => section.id === route.section)} /></div>
        ${navigation.primary.map((s) => html`<button key=${s.id} type="button" data-nav data-help-exempt="navigation"
          aria-current=${s.id === route.section ? "page" : null} onClick=${() => navigate(s.id)}>
          <${Icon} name=${s.icon} />${s.label}</button>`)}
        <button type="button" class="side-more-button" aria-expanded=${moreOpen ? "true" : "false"} aria-controls="side-more"
          ...${helpFor("shell.more")} onClick=${toggleMore}><${Icon} name="dots" />${t("shell.more")}</button>
        <${Help} topic="shell.more" compact=${true} />
        ${!noticeSeen && html`<div class="side-more-notice">
          <p>${t("shell.more_notice")}</p>
          <button type="button" class="btn secondary" ...${helpFor("shell.more_notice")} onClick=${dismissNotice}>${t("shell.understood")}</button>
          <${Help} topic="shell.more_notice" compact=${true} />
        </div>`}
        <div id="side-more" class="side-more-list" hidden=${!moreOpen}>
          ${navigation.more.filter((s) => !s.action).map((s) => html`<button key=${s.id} type="button" data-nav data-help-exempt="navigation"
            aria-current=${s.id === route.section ? "page" : null} onClick=${() => navigate(s.id)}>
            <${Icon} name=${s.icon} />${s.label}</button>`)}
          ${navigation.more.some((s) => s.action === "help") && html`<button type="button" class="side-help-row" data-nav
            ...${helpFor("shell.help_mode", { scope: "more" })} onClick=${() => writeHelpMode(true)}><${Icon} name="help" />${t("shell.help")}</button>
          <${Help} topic="shell.help_mode" scope="more" compact=${true} />`}
        </div>
      </div>
    </div>
    <div class="side-scroll">
      ${isRegisteredView("offices") && html`<${SideSection} id="offices" title=${t("shell.offices_heading")} count=${lists.offices.length}
        addLabel=${t("shell.add_office")} onAdd=${() => navigate("offices")}>
        ${lists.offices.length === 0 ? html`<p class="muted small side-empty">${t("shell.no_offices")}</p>`
          : html`<${LimitedList} rows=${lists.offices} render=${(office) => html`<${OfficeRow} key=${office.id} office=${office} token=${token} plan=${hasFloorPlan} />`} />`}
      </${SideSection}>`}
      ${isRegisteredView("project") && html`<${SideSection} id="projects" title=${t("shell.projects_heading")} count=${lists.projects.length}>
        ${lists.projects.length === 0 ? html`<p class="muted small side-empty">${t("shell.no_projects")}</p>`
          : html`<${LimitedList} rows=${lists.projects} render=${(project) => html`<a key=${project.id} class="side-item" href=${routeHref("project", project.id)}
            aria-current=${route.section === "project" && route.id === project.id ? "page" : null}><span class="side-text"><span class="side-title">${project.name}</span></span></a>`} />`}
      </${SideSection}>`}
      <${SideSection} id="recent" title=${t("shell.recent_heading")} count=${shown}>
    <div class="side-sessions-head"><span>${t("shell.sessions_heading")}</span><span class="side-head-actions">
      <button type="button" class="side-activities" aria-pressed=${activitiesMode ? "true" : "false"}
        ...${helpFor("shell.activities")} onClick=${() => setActivitiesMode((enabled) => !enabled)}>${t("shell.activities")}</button>
      <${Help} topic="shell.activities" compact=${true} />
      <button type="button" data-nav data-help-exempt="navigation"
        aria-current=${route.section === "sessions" ? "page" : null} onClick=${() => navigate("sessions")}>${t("shell.view_all")}</button></span></div>
    <p class="muted small side-summary" aria-live="polite"
      aria-busy=${activitiesMode ? (activityData.loaded && !activityData.loading ? "false" : "true") : (data.loaded ? "false" : "true")}>${summary}</p>
    ${activitiesMode && activityData.failed && html`<p class="message error small" role="alert">${t("shell.activities_read_failed")}</p>`}
    ${!activitiesMode && data.failed.length > 0 && html`<p class="message error small" role="alert">${t("shell.list_read_failed", { kinds: data.failed.join(t("shell.or")) })}</p>`}
    ${!activitiesMode && data.loaded && data.hint && data.sessions.length === 0 && html`<p class="muted small">${t("shell.session_roots_hint")}</p>`}
    <div class="side-groups">
      ${recentGroups.map((group) => html`<${Group} key=${group.key} group=${group} route=${route} now=${now} />`)}
    </div>
        ${(recent.hidden > 0 || (recentExpanded && shown > LIST_LIMIT)) && html`<button type="button" class="show-list-more" data-help-exempt="navigation"
          aria-expanded=${recentExpanded ? "true" : "false"} onClick=${() => setRecentExpanded((value) => !value)}>${recentExpanded ? t("shell.show_fewer") : t("shell.show_more", { count: recent.hidden })}</button>`}
      </${SideSection}>
    </div>
    <${CommandPalette} open=${paletteOpen} entries=${paletteEntries} onClose=${() => setPaletteOpen(false)}
      onGo=${(target) => navigate(target[0], target[1])} />
  </div>`;
}
