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
import { QuickGuide } from "./quick-guide.js";   // GUI4-T06
import { requestQuickGuide } from "./quick-guide-model.js";
import { Icon } from "./ui/icon.js";
import { ResizeHandle } from "./ui/resize-handle.js";
import { t } from "./i18n.js";
import { LimitsChip } from "./limits-chip.js";   // GUI4-T10
import { instanceLabel, onInstanceLabelChange } from "./instance.js";   // PRV-T01
import { navigate } from "./route.js";
import { HIDE_BELOW, NARROW_QUERY, PANES, defaultPanelOpen, isHelpKey, isSidebarKey, keyWidth, onChoiceChange, readAccent, readHelpMode, readPaneWidth, readPanelOpen, readSideHidden, resolvePaneWidth, writeHelpMode, writePaneWidth, writePanelOpen, writeSideHidden } from "./shell-state.js";

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

// GUI4-T03 (B1): the width of a pane goes to the .app element as a CSS variable through the CSSOM (style.setProperty): the CSP blocks a
// style attribute, not this. While dragging, the variable follows the pointer; a width below HIDE_BELOW px hides the pane when the
// pointer is released. The separator itself is ui/resize-handle.js.
const sideKeyWidth = (width, key, edge) => keyWidth("side", width, key, edge);
const panelKeyWidth = (width, key, edge) => keyWidth("panel", width, key, edge);

const focusable = (root) => (root ? root.querySelector("button:not([disabled]), input:not([disabled]), a[href]") : null);

// GUI3-T04: `hasSubject` is true while a session or a run is open; only then is «Contexto» offered. `contextDot` says that
// a card of the closed panel has content.
export function Shell({ status, statusText, mock, sidebar, panel, hasSubject = false, contextDot = false, children }) {
  const [label, setLabel] = useState(instanceLabel);   // PRV-T01: the instance label follows the session read of main.js
  useEffect(() => onInstanceLabelChange(setLabel), []);
  const narrow = useMedia(NARROW_QUERY);
  const [drawer, setDrawer] = useState(false);
  const [panelOpen, setPanelOpen] = useState(() => readPanelOpen(defaultPanelOpen()));
  const [helpOn, setHelpOn] = useState(readHelpMode);   // GUI3-T02: the "?" blocks show only in help mode
  const [accent, setAccent] = useState(readAccent);   // GUI3-T09: the palette; app.css gives its colours by data-accent
  const [sideHidden, setSideHidden] = useState(readSideHidden);   // GUI4-T03: Ctrl+B, the hide button and the edge tab
  const [sideWidth, setSideWidth] = useState(() => readPaneWidth("side") || PANES.side.def);
  const [panelWidth, setPanelWidth] = useState(() => readPaneWidth("panel") || PANES.panel.def);
  const appRef = useRef(null);
  const panelButton = useRef(null);   // the «Contexto» button of the top bar (narrow screens)
  const panelBox = useRef(null);
  const sideHideButton = useRef(null);
  const edgeSideButton = useRef(null);
  const helpRef = useRef(helpOn);
  helpRef.current = helpOn;
  const drawerOpen = drawer && narrow;
  const panelShown = panelOpen && hasSubject;
  const sideGone = sideHidden && !narrow;   // below 900 px the bar is the drawer: «hidden» does not apply
  const menuButton = useRef(null);
  const sidebarBox = useRef(null);
  const mainBox = useRef(null);
  const overlay = panelShown && narrow;   // below 900 px the panel is laid over the content
  const state = useRef({ open: false, restore: false, was: false, toMain: false, overlay: false, panelWas: false, panelRestore: false, sideFocus: false });
  state.current.open = drawerOpen;
  state.current.overlay = overlay;
  state.current.narrow = narrow;
  state.current.sideHidden = sideHidden;

  const closeDrawer = (restoreFocus) => { state.current.restore = restoreFocus; setDrawer(false); };
  const toggleDrawer = () => { if (drawerOpen) closeDrawer(true); else setDrawer(true); };
  const togglePanel = () => { const next = !panelOpen; setPanelOpen(next); writePanelOpen(next); };
  const closePanelOverlay = (restoreFocus) => { state.current.panelRestore = restoreFocus; setPanelOpen(false); writePanelOpen(false); };
  const toggleSide = (hide) => { state.current.sideFocus = true; setSideHidden(hide); writeSideHidden(hide); };
  // The end of a drag (px = where the edge ended) or a key: a width inside the range, or the pane hidden at the edge.
  const commitWidth = (kind, px) => {
    const next = resolvePaneWidth(kind, px);
    applyWidths(kind === "side" && !next.hidden ? next.width : sideWidth, kind === "panel" && !next.hidden ? next.width : panelWidth);   // a drop that hides puts the old width back
    if (next.hidden) {
      if (kind === "side") toggleSide(true); else { setPanelOpen(false); writePanelOpen(false); }
    } else if (kind === "side") { setSideWidth(next.width); writePaneWidth("side", next.width); }
    else { setPanelWidth(next.width); writePaneWidth("panel", next.width); }
  };
  const measureWidth = (kind) => (event) => {
    const box = appRef.current ? appRef.current.getBoundingClientRect() : { left: 0, right: window.innerWidth };
    return kind === "side" ? event.clientX - box.left : box.right - event.clientX;
  };
  const previewWidth = (kind) => (px) => {
    const app = appRef.current;
    if (!app) return;
    try { app.style.setProperty("--" + kind + "-w", resolvePaneWidth(kind, px).width + "px"); } catch (_) { /* no CSSOM: the drop still applies the width */ }
    app.setAttribute("data-resizing", px < HIDE_BELOW ? "hide" : "on");
  };
  const resetWidth = (kind) => {
    if (kind === "side") setSideWidth(PANES.side.def); else setPanelWidth(PANES.panel.def);
    writePaneWidth(kind, null);
  };
  const toggleHelp = () => { const next = !helpRef.current; helpRef.current = next; setHelpOn(next); writeHelpMode(next); };

  // «Personalizar» changes help mode too (GUI3-T07): it is the same state.
  useEffect(() => onChoiceChange((change) => { if ("help" in change) { helpRef.current = change.help; setHelpOn(change.help); } }), []);

  useEffect(() => onChoiceChange((change) => { if (change.accent) setAccent(change.accent); }), []);

  // The stored widths reach the grid as CSS variables; with the default width the variable is removed, so the stylesheet decides.
  const applyWidths = (side, panelPx) => {
    const app = appRef.current;
    if (!app) return;
    try {
      for (const [kind, value] of [["side", side], ["panel", panelPx]]) {
        const name = "--" + kind + "-w";
        if (value === PANES[kind].def) app.style.removeProperty(name); else app.style.setProperty(name, value + "px");
      }
    } catch (_) { /* a browser without the CSSOM keeps the default widths */ }
    app.removeAttribute("data-resizing");
  };
  useEffect(() => { applyWidths(sideWidth, panelWidth); }, [sideWidth, panelWidth]);

  // Ctrl+B hides and shows the sidebar; «Nueva conversación» has no key of its own.
  useEffect(() => {
    const onSideKey = (event) => {
      if (!isSidebarKey(event) || state.current.narrow) return;
      if (document.querySelector("dialog[open]")) return;   // GUI4-T06: with a dialog open the bar stays as it is
      event.preventDefault();
      toggleSide(!state.current.sideHidden);
    };
    document.addEventListener("keydown", onSideKey);
    return () => document.removeEventListener("keydown", onSideKey);
  }, []);

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

  // Esc closes the drawer, or else the panel laid over the content on a narrow screen (GUI4-T03). It listens in the capture phase and
  // marks the key as handled, so the session screen (which goes back to the list with Esc) leaves it alone; an open "?" help and
  // the Ctrl+K dialog still get their own Esc first.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "Escape" || event.isComposing || !(state.current.open || state.current.overlay)) return;
      if (event.target && event.target.closest && event.target.closest(".help-block.open, dialog[open], .limits:has(> .limits-popover:not([hidden]))")) return;   // the Ctrl+K dialog closes itself; GUI4-T10: so does the limits popover
      event.preventDefault();
      if (state.current.open) closeDrawer(true); else closePanelOverlay(true);
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

  // The panel laid over the content takes the focus when it opens and gives it back to the top-bar button when it closes.
  useEffect(() => {
    if (overlay) {
      if (panelBox.current) panelBox.current.focus();
    } else if (state.current.panelWas && state.current.panelRestore && panelButton.current) {
      panelButton.current.focus();
    }
    state.current.panelRestore = false;
    state.current.panelWas = overlay;
  }, [overlay]);

  // Hiding the sidebar moves the focus to the edge tab, showing it to the hide button, so it never stays on a hidden node.
  useEffect(() => {
    const topbar = appRef.current ? appRef.current.querySelector(".topbar") : null;
    if (topbar) topbar.inert = sideGone;   // the brand corner is out of the tab order while the sidebar is hidden
    if (!state.current.sideFocus) return;
    state.current.sideFocus = false;
    const target = sideGone ? edgeSideButton.current : sideHideButton.current;
    if (target) target.focus();
  }, [sideGone]);

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

  /* GUI4-T10 */
  const limitsSlot = html`<div class="side-slot" data-slot="limits"><${LimitsChip} /></div>`;

  return html`<div class="app" data-help=${helpOn ? "on" : "off"} data-accent=${accent} data-drawer=${drawerOpen ? "open" : "closed"} data-panel=${panelShown ? "open" : "closed"} data-side=${sideGone ? "hidden" : "shown"} ref=${appRef}>
    <a class="skip-link" href="#main-content" onClick=${skip}>${t("shell.skip_link")}</a>
    <header class="topbar">
      <div class="menu-only">
        <button type="button" class="tool-btn" ref=${menuButton} aria-expanded=${drawerOpen ? "true" : "false"} aria-controls="sidebar"
          ...${helpFor("shell.menu")} onClick=${toggleDrawer}><span class="burger" aria-hidden="true"></span>${t("shell.menu")}</button>
        <${Help} topic="shell.menu" compact=${true} />
      </div>
      <h1>Multi-Agent</h1>
      ${label && html`<span class="badge instance" title=${label}>${label}</span><${Help} topic="shell.instance_label" compact=${true} />`}
      ${mock && html`<span class="badge mock" title=${t("shell.mock_title")}>MOCK</span>`}
      <button type="button" class="tool-btn side-hide" ref=${sideHideButton} data-help-exempt="navigation" aria-keyshortcuts="Control+B" title=${t("shell.hide_sidebar") + " (Ctrl+B)"}
        aria-label=${t("shell.hide_sidebar")} onClick=${() => toggleSide(true)}><span aria-hidden="true">«</span></button>
      ${hasSubject && html`<div class="panel-tool panel-only"><button type="button" class="tool-btn" ref=${panelButton} aria-expanded=${panelOpen ? "true" : "false"} aria-controls="right-panel"
        ...${helpFor("shell.panel", { scope: "top" })} onClick=${togglePanel}><${Icon} name="panel" />${t("shell.panel")}${!panelOpen && contextDot && html`<span class="context-dot" aria-hidden="true"></span><span class="sr-only">${t("shell.panel_has_content")}</span>`}</button>
        <${Help} topic="shell.panel" scope="top" compact=${true} /></div>`}
    </header>
    ${sideGone && html`<button type="button" class="edge-tab edge-side" ref=${edgeSideButton} data-help-exempt="navigation" aria-keyshortcuts="Control+B"
      title=${t("shell.show_sidebar") + " (Ctrl+B)"} aria-label=${t("shell.show_sidebar")} onClick=${() => toggleSide(false)}><span aria-hidden="true">»</span></button>`}
    ${hasSubject && !panelShown && !narrow && html`<button type="button" class="edge-tab edge-panel" data-help-exempt="navigation"
      title=${t("shell.show_panel")} aria-label=${t("shell.show_panel")} onClick=${togglePanel}><span aria-hidden="true">«</span></button>`}
    <div class="scrim" hidden=${!drawerOpen} onClick=${() => closeDrawer(true)}></div>
    <div class="scrim panel-scrim" hidden=${!overlay} onClick=${() => closePanelOverlay(true)}></div>
    <nav id="sidebar" class="sidebar" aria-label=${t("shell.nav_label")} ref=${sidebarBox} inert=${sideGone} onClick=${onSidebarClick}>
      ${sidebar}
      <div class="side-footer">
        ${limitsSlot}
        <span class=${"side-connection " + status} role="status" aria-live="polite"><span class="side-connection-dot" aria-hidden="true"></span>${statusText}</span>
        <div class="side-footer-tools">
        <div class="panel-tool">
          <button type="button" class="tool-btn" data-nav           ...${helpFor("shell.customize")} onClick=${() => navigate("customize")}><${Icon} name="settings" />${t("shell.customize")}</button>
          <${Help} topic="shell.customize" compact=${true} />
        </div>
        <div class="panel-tool help-tool">
          <button type="button" class="tool-btn" aria-pressed=${helpOn ? "true" : "false"}
            ...${helpFor("shell.help_mode")} onClick=${toggleHelp}><${Icon} name="help" />${t("shell.help")}</button>
          <${Help} topic="shell.help_mode" compact=${true} />
        </div>
        <!-- GUI4-T06: «Guía rápida» row; the dialog itself is mounted at the end of the app, outside the sidebar -->
        <div class="panel-tool guide-tool">
          <button type="button" class="tool-btn" aria-haspopup="dialog"
            ...${helpFor("shell.quick_guide")} onClick=${() => requestQuickGuide()}><${Icon} name="help" />${t("quick_guide.open")}</button>
          <${Help} topic="shell.quick_guide" compact=${true} />
        </div>
        </div>
      </div>
    </nav>
    <main id="main-content" class="content" tabindex="-1" ref=${mainBox} inert=${drawerOpen || overlay}>
      ${hasSubject && html`<div class="content-toolbar"><div class="panel-tool">
        <button type="button" class="tool-btn" aria-expanded=${panelOpen ? "true" : "false"} aria-controls="right-panel"
          ...${helpFor("shell.panel")} onClick=${togglePanel}><${Icon} name="panel" />${t("shell.panel")}${!panelOpen && contextDot && html`<span class="context-dot" aria-hidden="true"></span><span class="sr-only">${t("shell.panel_has_content")}</span>`}</button>
        <${Help} topic="shell.panel" compact=${true} />
      </div></div>`}
      ${children}
    </main>
    <aside id="right-panel" class="rightpanel" aria-label=${t("shell.panel_aria")} tabindex="-1" ref=${panelBox} hidden=${!panelShown} inert=${drawerOpen}>${panel}</aside>
    ${!narrow && !sideGone && html`<${ResizeHandle} kind="side" width=${sideWidth} min=${PANES.side.min} max=${PANES.side.max} edge="right"
      measure=${measureWidth("side")} keyWidth=${sideKeyWidth} onPreview=${previewWidth("side")} onCommit=${(px) => commitWidth("side", px)} onReset=${() => resetWidth("side")} />`}
    ${!narrow && panelShown && html`<${ResizeHandle} kind="panel" width=${panelWidth} min=${PANES.panel.min} max=${PANES.panel.max} edge="left"
      measure=${measureWidth("panel")} keyWidth=${panelKeyWidth} onPreview=${previewWidth("panel")} onCommit=${(px) => commitWidth("panel", px)} onReset=${() => resetWidth("panel")} />`}
    <!-- GUI4-T06 -->
    <${QuickGuide} />
  </div>`;
}
