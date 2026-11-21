// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// App shell (GUI2-T02): the three zones around the views and the keyboard and responsive behaviour that goes with them.
//   banner   the brand corner: menu button (narrow screens), short title and MOCK badge
//   nav      the left sidebar with connection and help; below ~900 px it is a drawer (Esc and the scrim close it)
//   main     the view of the current route
//   aside    the «Contexto» panel; closed by default, offered only with a session or run open, and the choice is remembered (shell-state.js)
// The sidebar and the panel content come in as props (sidebar.js, right-panel.js); this file only lays them out.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { Help, helpFor } from "./help.js";
import { Icon } from "./ui/icon.js";
import { navigate } from "./route.js";
import { NARROW_QUERY, defaultPanelOpen, isHelpKey, onChoiceChange, readHelpMode, readPanelOpen, writeHelpMode, writePanelOpen } from "./shell-state.js";

// True while the media query matches; follows the window when it is resized.
function useMedia(query) {
  const read = () => { try { return window.matchMedia(query).matches; } catch (_) { return false; } };
  const [matches, setMatches] = useState(read);
  useEffect(() => {
    let list = null;
    try { list = window.matchMedia(query); } catch (_) { return undefined; }
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}

const focusable = (root) => (root ? root.querySelector("button:not([disabled]), input:not([disabled]), a[href]") : null);

// GUI3-T04: `hasSubject` is true while a session or a run is open; only then is «Contexto» offered. `contextDot` says that
// a card of the closed panel has content.
export function Shell({ status, statusText, mock, sidebar, panel, hasSubject = false, contextDot = false, children }) {
  const narrow = useMedia(NARROW_QUERY);
  const [drawer, setDrawer] = useState(false);
  const [panelOpen, setPanelOpen] = useState(() => readPanelOpen(defaultPanelOpen()));
  const [helpOn, setHelpOn] = useState(readHelpMode);   // GUI3-T02: the "?" blocks show only in help mode
  const helpRef = useRef(helpOn);
  helpRef.current = helpOn;
  const drawerOpen = drawer && narrow;
  const panelShown = panelOpen && hasSubject;
  const menuButton = useRef(null);
  const sidebarBox = useRef(null);
  const mainBox = useRef(null);
  const state = useRef({ open: false, restore: false, was: false, toMain: false });
  state.current.open = drawerOpen;

  const closeDrawer = (restoreFocus) => { state.current.restore = restoreFocus; setDrawer(false); };
  const toggleDrawer = () => { if (drawerOpen) closeDrawer(true); else setDrawer(true); };
  const togglePanel = () => { const next = !panelOpen; setPanelOpen(next); writePanelOpen(next); };
  const toggleHelp = () => { const next = !helpRef.current; helpRef.current = next; setHelpOn(next); writeHelpMode(next); };

  // «Personalizar» changes help mode too (GUI3-T07): it is the same state.
  useEffect(() => onChoiceChange((change) => { if ("help" in change) { helpRef.current = change.help; setHelpOn(change.help); } }), []);

  // F1 toggles help mode (the browser's own help is cancelled); no other key is captured. If a browser does not let the
  // page intercept F1, the "Ayuda" button does the same.
  useEffect(() => {
    const onHelpKey = (event) => {
      if (!isHelpKey(event)) return;
      event.preventDefault();
      toggleHelp();
    };
    document.addEventListener("keydown", onHelpKey);
    return () => document.removeEventListener("keydown", onHelpKey);
  }, []);

  // Esc closes the drawer. It listens in the capture phase and marks the key as handled, so the session screen (which
  // goes back to the list with Esc) leaves it alone; an open "?" help still gets its own Esc first.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "Escape" || event.isComposing || !state.current.open) return;
      if (event.target && event.target.closest && event.target.closest(".help-block.open, dialog[open]")) return;   // the Ctrl+K dialog closes itself
      event.preventDefault();
      closeDrawer(true);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);

  useEffect(() => { if (!narrow) setDrawer(false); }, [narrow]);

  // Opening moves the focus into the drawer; closing with Esc, the menu button or the scrim gives it back to the button.
  // Closing because the address changed with no click (GUI3-T07) moves it to the new view.
  useEffect(() => {
    if (drawerOpen) {
      const first = focusable(sidebarBox.current);
      if (first) first.focus();
    } else if (state.current.was && state.current.toMain && mainBox.current) {
      mainBox.current.focus();
    } else if (state.current.was && state.current.restore && menuButton.current) {
      menuButton.current.focus();
    }
    state.current.toMain = false;
    state.current.was = drawerOpen;
  }, [drawerOpen]);

  // Any change of address while the drawer is open closes it, also a route opened with Intro in the Ctrl+K dialog
  // (GUI3-T07), which is not a click (a click on a link already closed it in onSidebarClick).
  useEffect(() => {
    const onHash = () => { if (state.current.open) { state.current.toMain = true; closeDrawer(false); } };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // A link or navigation button inside the drawer closes it: the view behind changes.
  const onSidebarClick = (event) => {
    if (!state.current.open) return;
    const target = event.target;
    if (target && target.closest && target.closest("a[href], [data-nav]")) closeDrawer(false);
  };

  // The skip link does not change the address (the address is the router's), it moves the focus.
  const skip = (event) => {
    event.preventDefault();
    if (mainBox.current) mainBox.current.focus();
  };

  return html`<div class="app" data-help=${helpOn ? "on" : "off"} data-drawer=${drawerOpen ? "open" : "closed"} data-panel=${panelShown ? "open" : "closed"}>
    <a class="skip-link" href="#main-content" onClick=${skip}>Saltar al contenido</a>
    <header class="topbar">
      <div class="menu-only">
        <button type="button" class="tool-btn" ref=${menuButton} aria-expanded=${drawerOpen ? "true" : "false"} aria-controls="sidebar"
          ...${helpFor("shell.menu")} onClick=${toggleDrawer}><span class="burger" aria-hidden="true"></span>Menú</button>
        <${Help} topic="shell.menu" compact=${true} />
      </div>
      <h1>Multi-Agent</h1>
      ${mock && html`<span class="badge mock" title="Resultados sintéticos, sin llamadas a modelos">MOCK</span>`}
    </header>
    <div class="scrim" hidden=${!drawerOpen} onClick=${() => closeDrawer(true)}></div>
    <nav id="sidebar" class="sidebar" aria-label="Sesiones y ejecuciones" ref=${sidebarBox} onClick=${onSidebarClick}>
      ${sidebar}
      <div class="side-footer">
        <span class=${"side-connection " + status} role="status" aria-live="polite"><span class="side-connection-dot" aria-hidden="true"></span>${statusText}</span>
        <div class="side-footer-tools">
        <div class="panel-tool">
          <button type="button" class="tool-btn" data-nav ...${helpFor("shell.customize")} onClick=${() => navigate("customize")}><${Icon} name="settings" />Personalizar</button>
          <${Help} topic="shell.customize" compact=${true} />
        </div>
        <div class="panel-tool help-tool">
          <button type="button" class="tool-btn" aria-pressed=${helpOn ? "true" : "false"}
            ...${helpFor("shell.help_mode")} onClick=${toggleHelp}><${Icon} name="help" />Ayuda</button>
          <${Help} topic="shell.help_mode" compact=${true} />
        </div>
        </div>
      </div>
    </nav>
    <main id="main-content" class="content" tabindex="-1" ref=${mainBox} inert=${drawerOpen}>
      ${hasSubject && html`<div class="content-toolbar"><div class="panel-tool">
        <button type="button" class="tool-btn" aria-expanded=${panelOpen ? "true" : "false"} aria-controls="right-panel"
          ...${helpFor("shell.panel")} onClick=${togglePanel}><${Icon} name="panel" />Contexto${!panelOpen && contextDot && html`<span class="context-dot" aria-hidden="true"></span><span class="sr-only"> (hay contenido)</span>`}</button>
        <${Help} topic="shell.panel" compact=${true} />
      </div></div>`}
      ${children}
    </main>
    <aside id="right-panel" class="rightpanel" aria-label="Contexto de la sesión" hidden=${!panelShown} inert=${drawerOpen}>${panel}</aside>
  </div>`;
}
