// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Agent tree (GUI2-T08): who works on one session and on what. The orchestrator (the session) on top and, under it, its
// subagents, threads and workers grouped by level (T1 to T4), each with its model, level, status, what it works on and its
// time; on one side the detail of the chosen node, below a short log of the status changes since the page opened.
// Read-only. Everything is plain text through htm/Preact (no HTML injection). It refreshes like the subagents panel does:
// the "children" stream of /gui/api/stream (followChildren) and, whenever that is down, every 10 s.
// The level badge and the status chip are small local functions on purpose: GUI2-T11 brings shared ones to switch to.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet } from "./api.js";
import { Help, helpFor } from "./help.js";
import { routeHref } from "./route.js";
import { sessionTitle, sourceLabel, surfaceLabel } from "./session-text.js";
import { followChildren } from "./subagents-panel.js";
import { NO_CONVERSATION, childrenWarning } from "./subagents-text.js";
import { errorText } from "./text.js";
import { currentLanguage, onLanguageChange, t } from "./i18n.js";
import {
  LEVELS, ROOT_ID, UNKNOWN_LEVEL, childLink, countStatuses, describeNode, describeRoot, detailFacts, diffSnapshots,
  filterChildren, groupByLevel, headerStats, headerTexts, levelShares, levelText, moveFocus, nodeAriaLabel, rootFacts, runStepChildren,
  snapshot, visibleOrder,
} from "./agent-tree-model.js";

const TICK_MS = 10000;        // fallback poll while the stream is down; also keeps "Lleva N min" honest
const CONNECTED_EVERY = 3;    // while the stream is up, refresh anyway every 3rd tick (the session's own data)
const LOG_LIMIT = 12;
const ALL_LEVELS = [...LEVELS, UNKNOWN_LEVEL];

const sessionPath = (id, tail = "") => "/gui/api/sessions/" + encodeURIComponent(id) + tail;
const FRAMEWORK_PREFIX = "framework:";
const asApiError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
const clock = (time) => new Date(time).toLocaleTimeString(currentLanguage(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });

// --- local pieces (to be replaced by the shared components of GUI2-T11) ---

function StatusChip({ status }) {
  return html`<span class="at-chip"><span class=${"at-dot tone-" + status.tone} aria-hidden="true"></span>
    <span class=${"at-status tone-" + status.tone} title=${status.help}>${status.label}</span></span>`;
}

// The level is written (T1..T4, or why there is none); the colour only helps.
function LevelBadge({ view }) {
  return html`<span class=${"at-level-badge lv-" + view.tier}>${view.level || view.levelText}</span>`;
}

const levelHeading = (level, count) => t("agent_tree.level.heading", {
  level: level === UNKNOWN_LEVEL ? t("agent_tree.level.unavailable") : levelText(level),
  count,
  agents: t(count === 1 ? "agent_tree.level.agent_singular" : "agent_tree.level.agent_plural"),
});
const logLine = (entry) => {
  const timestamp = clock(entry.time);
  return entry.appeared
    ? `${timestamp} · ${entry.title} · ${t("agent_tree.log.appears")} · ${entry.state}`
    : `${timestamp} · ${entry.title} · ${entry.previousState} → ${entry.state}`;
};

// --- the tree ---

function NodeItem({ view, root = false, selected, tabStop, onSelect, onFocusNode, register }) {
  return html`<div role="treeitem" class=${"at-node" + (root ? " root" : "") + (selected ? " selected" : "")}
    aria-level=${root ? 1 : 2} aria-selected=${selected ? "true" : "false"} aria-label=${nodeAriaLabel(view)}
    tabindex=${tabStop ? 0 : -1} data-node-id=${view.id} ref=${register} onClick=${onSelect} onFocus=${onFocusNode}>
    <div class="at-node-head">
      <strong class="at-node-title">${view.title}</strong>
      <${LevelBadge} view=${view} />
    </div>
    <div class="at-node-model small muted">${view.model}</div>
    <div class="at-node-what">${view.what}</div>
    <div class="at-node-foot small">
      <${StatusChip} status=${view.status} />
      <span class="muted">${view.elapsed}</span>
    </div>
    <div class="at-node-tokens small muted">${view.tokens}</div>
  </div>`;
}

// A bar for the share of the workers' tokens of one level; the figure is written, the bar only helps.
function LevelBar({ share }) {
  const text = share.percent === null
    ? t("agent_tree.bar.no_data")
    : t("agent_tree.bar.share", { value: share.percent }) + (share.partial ? " · " + t("agent_tree.stats.partial") : "");
  return html`<div class="at-bar-row" title=${t("agent_tree.bar.help")}>
    <span class="at-bar" aria-hidden="true">${share.percent !== null && html`<span class=${"at-bar-fill lv-" + share.level + " at-w-" + share.step}></span>`}</span>
    <span class="at-bar-text small muted">${text}</span>
  </div>`;
}

function Detail({ sessionId, child, summary, now }) {
  const link = child ? childLink(child, sessionId) : null;
  const facts = child ? detailFacts(child, now) : rootFacts(summary, now);
  const view = child ? describeNode(child, now) : describeRoot(summary, now);
  return html`<div>
    <h4 id="at-detail-title">${view.title}</h4>
    <dl class="facts at-facts">
      ${facts.map(([name, value]) => html`<dt key=${"t" + name}>${name}</dt><dd key=${"d" + name}>${value}</dd>`)}
    </dl>
    ${child && link && html`<p class="at-open"><a class="btn secondary" href=${routeHref(link.section, link.id)}
      ...${helpFor("agent_tree.open_child")}>${link.label}</a><${Help} topic="agent_tree.open_child" compact=${true} /></p>`}
    ${child && !link && html`<p class="muted small">${NO_CONVERSATION}</p>`}
  </div>`;
}

export function AgentTree({ token, sessionId }) {
  const [summary, setSummary] = useState(null);
  const [data, setData] = useState({ children: [], warnings: [], loaded: false });
  const [error, setError] = useState(null);
  const [onlyActive, setOnlyActive] = useState(false);
  const [selected, setSelected] = useState(ROOT_ID);   // a node id, or null after Esc
  const [focused, setFocused] = useState(ROOT_ID);     // the one tab stop of the tree (roving tabindex)
  const [log, setLog] = useState([]);
  const [, setLanguageVersion] = useState(0);
  const [stream, setStream] = useState("reconnecting");
  const [now, setNow] = useState(Date.now());
  const alive = useRef(true);
  const ticket = useRef(0);
  const version = useRef(null);
  const connected = useRef(false);
  const seen = useRef(null);      // snapshot of the last answer: the log is what changed since the first one
  const items = useRef({});
  const heading = useRef(null);
  const mainBox = useRef(null);
  const detailBox = useRef(null);
  const reveal = useRef(false);   // a node was chosen: show its detail if the screen stacks it below the tree

  useEffect(() => onLanguageChange(() => setLanguageVersion((version) => version + 1)), []);

  async function refresh() {
    if (!alive.current || !sessionId) return;
    const mine = ++ticket.current;
    try {
      const [session, result] = await Promise.all([apiGet(sessionPath(sessionId), token), apiGet(sessionPath(sessionId, "/children"), token)]);
      if (!alive.current || mine !== ticket.current) return;
      let next = result.children || [];
      // A framework run whose reader lists no steps: draw them from the run itself (the answer the run panel already loads).
      if (next.length === 0 && sessionId.startsWith(FRAMEWORK_PREFIX)) {
        try {
          const run = await apiGet("/gui/api/runs/" + encodeURIComponent(sessionId.slice(FRAMEWORK_PREFIX.length)), token);
          if (!alive.current || mine !== ticket.current) return;
          next = runStepChildren(run);
        } catch (failure) { /* the tree stays as the reader gave it */ }
      }
      const after = snapshot(next);
      if (seen.current) {
        const lines = diffSnapshots(seen.current, after);
        if (lines.length) {
          const time = Date.now();
          const changes = [];
          for (const [id, current] of after) {
            const previous = seen.current.get(id);
            if (!previous) changes.push({ time, title: current.title, state: current.state, appeared: true });
            else if (previous.state !== current.state) {
              changes.push({ time, title: current.title, previousState: previous.state, state: current.state, appeared: false });
            }
          }
          setLog((previous) => [...changes, ...previous].slice(0, LOG_LIMIT));
        }
      }
      seen.current = after;
      version.current = result.version;
      setSummary(session);
      setData({ children: next, warnings: result.warnings || [], loaded: true });
      setError(null);
    } catch (failure) {
      if (alive.current && mine === ticket.current) setError(asApiError(failure));
    }
  }

  // One tree per session (main.js keys it by the id): load, then follow the stream and tick.
  useEffect(() => {
    if (!sessionId) return undefined;
    alive.current = true;
    connected.current = false;
    refresh();
    const stop = followChildren({
      token,
      session: sessionId,
      onChange: (event) => { if (event && event.version !== version.current) refresh(); },
      onState: (value) => {
        connected.current = value === "connected";
        if (alive.current) setStream(value);
      },
    });
    let ticks = 0;
    const timer = setInterval(() => {
      ticks += 1;
      setNow(Date.now());
      if (!connected.current || ticks % CONNECTED_EVERY === 0) refresh();
    }, TICK_MS);
    return () => { alive.current = false; stop(); clearInterval(timer); };
  }, [token, sessionId]);

  // Arriving from a link that disappears with the previous screen: the focus goes to the title of this one.
  useEffect(() => { if (heading.current) heading.current.focus({ preventScroll: true }); }, [sessionId]);

  // On a narrow screen the detail sits below the whole tree: choosing a node brings it into view.
  useEffect(() => {
    if (!reveal.current) return;
    reveal.current = false;
    const main = mainBox.current;
    const detail = detailBox.current;
    if (main && detail && detail.offsetTop >= main.offsetTop + main.offsetHeight) detail.scrollIntoView({ block: "nearest" });
  }, [selected]);

  if (!sessionId) {
    return html`<section class="agent-tree"><p class="muted">${t("agent_tree.no_session")}</p></section>`;
  }

  const children = data.children;
  const shown = filterChildren(children, onlyActive);
  const grouped = groupByLevel(shown);
  const counts = countStatuses(children);
  const stats = headerTexts(headerStats(children, summary));
  const shares = new Map(levelShares(children).map((share) => [share.level, share]));
  const ids = visibleOrder(grouped);
  const tabStop = ids.includes(focused) ? focused : ROOT_ID;
  const byId = new Map(children.map((child) => [child.id, child]));
  // a chosen child that is gone from the answer counts as no choice (the detail never shows another node's data)
  const choice = selected === ROOT_ID || byId.has(selected) ? selected : null;
  const chosen = choice && choice !== ROOT_ID ? byId.get(choice) : null;
  const rootView = describeRoot(summary, now);
  const title = summary ? sessionTitle(summary) : sessionId;
  const meta = summary
    ? [sourceLabel(summary.source), surfaceLabel(summary.surface), summary.mode && t("agent_tree.meta.mode", { value: summary.mode }),
      summary.profile && t("agent_tree.meta.profile", { value: summary.profile })].filter(Boolean).join(" · ")
    : (error ? "" : t("agent_tree.loading"));

  const choose = (id) => { reveal.current = true; setFocused(id); setSelected(id); };
  const nodeProps = (id) => ({
    selected: choice === id,
    tabStop: tabStop === id,
    onSelect: () => choose(id),
    onFocusNode: () => setFocused(id),
    register: (element) => { items.current[id] = element; },
  });

  // Arrows, Home and End move between nodes, Enter or Space chooses, Esc clears the choice. Only keys typed on a node count.
  const onTreeKey = (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const id = event.target && event.target.dataset ? event.target.dataset.nodeId : null;
    if (!id) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(id);
    } else if (event.key === "Escape") {
      if (selected !== null) { event.preventDefault(); setSelected(null); }
    } else {
      const next = moveFocus(ids, id, event.key);
      if (next === null) return;
      event.preventDefault();
      setFocused(next);
      if (items.current[next]) items.current[next].focus();
    }
  };

  const withoutAgents = data.loaded && children.length === 0;
  const nothingShown = data.loaded && children.length > 0 && shown.length === 0;
  return html`<section class="agent-tree" aria-labelledby="agent-tree-title" aria-busy=${data.loaded || error ? "false" : "true"}>
    <header class="at-head">
      <div class="at-head-text">
        <h3 id="agent-tree-title" tabindex="-1" ref=${heading}>${t("agent_tree.session_title", { title })}</h3>
        <${Help} topic="agent_tree.view" compact=${true} />
        <p class="muted small at-meta">${meta}</p>
      </div>
      <div class="header-tools">
        <a class="btn secondary" href=${routeHref("sessions", (summary && summary.parent_id) || sessionId)} ...${helpFor("agent_tree.back")}>${t("agent_tree.back_orchestrator")}</a>
        <${Help} topic="agent_tree.back" compact=${true} />
      </div>
    </header>
    <dl class="at-stats" aria-label=${t("agent_tree.stats.title")}>
      <div class="at-stat" title=${t("agent_tree.stats.active_help")}><dt>${t("agent_tree.stats.active")}</dt><dd>${stats.active}</dd></div>
      <div class="at-stat" title=${t("agent_tree.stats.tokens_help")}><dt>${t("agent_tree.stats.tokens")}</dt><dd>${stats.tokens}</dd></div>
      <div class="at-stat" title=${t("agent_tree.stats.cache_help")}><dt>${t("agent_tree.stats.cache")}</dt><dd>${stats.cache}</dd></div>
    </dl>
    <div class="at-toolbar">
      <p class="at-counts" role="status" aria-live="polite">
        <span class="at-count">${t("agent_tree.count.active")} · ${counts.active}</span>
        <span class="at-count">${t("agent_tree.count.done")} · ${counts.done}</span>
        <span class="at-count">${t("agent_tree.count.waiting")} · ${counts.waiting}</span>
      </p>
      <label class="at-filter"><input type="checkbox" checked=${onlyActive} ...${helpFor("agent_tree.filter")}
        onChange=${(event) => setOnlyActive(event.currentTarget.checked)} /> ${t("agent_tree.filter.active")}</label>
      <${Help} topic="agent_tree.filter" compact=${true} />
    </div>
    ${stream === "failed" && html`<p class="muted small">${t("agent_tree.stream.failed")}</p>`}
    ${error && html`<p class="message error" role="alert">${t("agent_tree.error.load", { error: errorText(error) })}</p>`}
    <div class="at-columns">
      <div class="at-main" ref=${mainBox}>
        <${Help} topic="agent_tree.node" />
        <div class="at-tree" role="tree" aria-label=${t("agent_tree.accessibility.tree")} ...${helpFor("agent_tree.node")} onKeyDown=${onTreeKey}>
          <${NodeItem} view=${rootView} root=${true} ...${nodeProps(ROOT_ID)} />
          ${ALL_LEVELS.map((level) => grouped[level].length > 0 && html`<div key=${level} role="group" class="at-level" aria-labelledby=${"at-level-" + level}>
            <h4 id=${"at-level-" + level}>${levelHeading(level, grouped[level].length)}</h4>
            ${shares.has(level) && html`<${LevelBar} share=${shares.get(level)} />`}
            ${grouped[level].map((child) => html`<${NodeItem} key=${child.id} view=${describeNode(child, now)} ...${nodeProps(child.id)} />`)}
          </div>`)}
        </div>
        ${withoutAgents && html`<p class="muted">${t("agent_tree.empty.no_agents")}</p>`}
        ${nothingShown && html`<p class="muted">${t("agent_tree.empty.no_active_agents")}</p>`}
        ${data.warnings.length > 0 && html`<p class="muted small">${data.warnings.map(childrenWarning).join(" ")}</p>`}
      </div>
      <aside class="at-detail" aria-labelledby="at-detail-title" ref=${detailBox}>
        ${choice === null
          ? html`<h4 id="at-detail-title">${t("agent_tree.detail.title")}</h4><p class="muted">${t("agent_tree.detail.choose")}</p>`
          : html`<${Detail} sessionId=${sessionId} child=${chosen} summary=${summary} now=${now} />`}
      </aside>
    </div>
    <section class="at-log" aria-labelledby="at-log-title">
      <div class="at-log-head">
        <h4 id="at-log-title">${t("agent_tree.log.title")}</h4>
        <${Help} topic="agent_tree.log" compact=${true} />
      </div>
      ${log.length
        ? html`<ul class="at-log-list">${log.map((entry, index) => html`<li key=${index}>${logLine(entry)}</li>`)}</ul>`
        : html`<p class="muted">${t("agent_tree.log.empty")}</p>`}
    </section>
  </section>`;
}
