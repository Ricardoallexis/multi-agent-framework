// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Puts the subagents panel (subagents-panel.js) in a right-hand column whenever a session is open
// (#sessions/<id>), without touching the sessions views: main.js imports this file once and it does the rest.
// It owns a separate Preact root inside <aside id="subagents-dock">, styled by subagents-panel.css.
// Open a subagent's own conversation and the panel keeps listing its siblings (those of the main session).
import { html, render, useState, useEffect } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { loadToken } from "./auth.js";
import { useRoute } from "./route.js";
import { SubagentsPanel } from "./subagents-panel.js";

const STYLESHEET = "/gui/static/subagents-panel.css";
const DOCK_ID = "subagents-dock";
const BODY_CLASS = "has-subagents";
const CLAUDE_SUBAGENT = /^(claude:[0-9a-fA-F-]{36}):agent-[A-Za-z0-9_-]{1,64}$/;
const CODEX_THREAD = /^codex:[0-9a-fA-F-]{36}$/;

// Which session's children the panel lists while `id` is open. A Claude subagent is named after its parent;
// a Codex thread says its parent in its summary (looked up once); anything else lists its own children.
function useListedSession(token, id) {
  const [parents, setParents] = useState({});
  useEffect(() => {
    if (!id || !CODEX_THREAD.test(id) || id in parents) return undefined;
    let cancelled = false;
    apiGet("/gui/api/sessions/" + encodeURIComponent(id), token)
      .then((summary) => { if (!cancelled) setParents((previous) => ({ ...previous, [id]: summary.parent_id || null })); })
      .catch(() => { if (!cancelled) setParents((previous) => ({ ...previous, [id]: null })); });
    return () => { cancelled = true; };
  }, [id, token]);
  if (!id) return null;
  const claude = CLAUDE_SUBAGENT.exec(id);
  if (claude) return claude[1];
  if (CODEX_THREAD.test(id) && !(id in parents)) return undefined; // still asking: show nothing yet
  return parents[id] || id;
}

function Dock({ token, dock }) {
  const route = useRoute();
  const open = route.section === "sessions" ? route.id : null;
  const listed = useListedSession(token, open);

  // The column starts under the top bar, which scrolls away: follow it.
  useEffect(() => {
    document.body.classList.toggle(BODY_CLASS, Boolean(listed));
    if (!listed) return undefined;
    let frame = 0;
    const place = () => {
      frame = 0;
      const bar = document.querySelector(".topbar");
      const top = bar ? Math.max(0, bar.getBoundingClientRect().bottom) : 0;
      dock.style.setProperty("--subagents-top", top + "px");
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(place); };
    place();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      if (frame) cancelAnimationFrame(frame);
      document.body.classList.remove(BODY_CLASS);
    };
  }, [Boolean(listed)]);

  if (!listed) return null;
  return html`<${SubagentsPanel} key=${listed} token=${token} sessionId=${listed} currentId=${open} />`;
}

function start() {
  if (document.getElementById(DOCK_ID)) return;
  const token = loadToken(); // main.js has already moved a token from the address into sessionStorage
  if (!token) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = STYLESHEET;
  document.head.appendChild(link);
  const dock = document.createElement("aside");
  dock.id = DOCK_ID;
  dock.className = "subagents-dock";
  dock.setAttribute("aria-label", "Subagentes de la sesión");
  document.body.appendChild(dock);
  render(html`<${Dock} token=${token} dock=${dock} />`, dock);
}

// After main.js ran (it reads the token from the address first).
setTimeout(start, 0);
