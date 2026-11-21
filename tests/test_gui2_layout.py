# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI2-T02: the three-zone app shell (left sidebar, centre, right panel).

Static checks over the client files and the stylesheet (landmarks, skip link, toggle attributes, drawer and
responsive rules, dark mode, no HTML injection, help for every new control), the pure logic of the sidebar and of the
folded work block run with Node (skipped when Node is not installed), and server checks: ``/gui`` serves the shell, the
new files carry the CSP, and the token protection of the GUI API is the same as before."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui import STATIC_DIR
from multiagent.gui.security import CSP
from multiagent.gui.token import ensure_token
from session_fixtures import (
    CLAUDE_ID,
    add_framework_run,
    make_services,
    make_stores,
    write_claude,
    write_codex,
    write_copilot,
)
from test_gui_sessions_view import FORBIDDEN as BASE_FORBIDDEN

JS = STATIC_DIR / "js"
CSS = STATIC_DIR / "app.css"
NODE = shutil.which("node")

SHELL_FILES = ["shell.js", "shell-state.js", "sidebar.js", "sidebar-model.js", "right-panel.js", "panel-slots.js", "work-fold.js"]
# localStorage is allowed in exactly one file: the one that wraps it in try/catch.
FORBIDDEN = [(pattern, label) for pattern, label in BASE_FORBIDDEN if label != "localStorage"]
NEW_HELP_TOPICS = ["shell.menu", "shell.panel", "shell.new_session", "shell.search",
                   "panel.changes", "subagents.panel", "panel.tokens", "panel.sources"]    # one topic for the subagents section


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def css() -> str:
    """The stylesheet without its comments (they would end up in the selectors ``rule`` looks for)."""
    return re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)


def rule(source: str, selector: str) -> str:
    """Body of the first top-level-looking rule whose selector list contains ``selector`` exactly."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", source):
        if selector in [part.strip() for part in match[1].split(",")]:
            return match[2]
    raise AssertionError(f"no rule for {selector}")


def media(source: str, query: str) -> str:
    """Text of the first ``@media`` block whose condition is exactly ``query``."""
    start = source.index(f"@media {query} {{")
    depth, index = 0, source.index("{", start)
    for position in range(index, len(source)):
        depth += {"{": 1, "}": -1}.get(source[position], 0)
        if depth == 0:
            return source[index + 1:position]
    raise AssertionError(query)


# --- the files ------------------------------------------------------------------------------------------------

def test_shell_files_exist_and_the_page_still_loads_only_main():
    for name in SHELL_FILES:
        assert (JS / name).is_file(), name
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script[^>]*>", html) == ['<script type="module" src="/gui/static/js/main.js">']
    assert 'id="app"' in html and "<style" not in html and "style=" not in html


@pytest.mark.parametrize("name", SHELL_FILES + ["main.js", "runs.js", "session-items.js"])
def test_shell_code_has_no_html_injection_eval_inline_handlers_or_remote_code(name):
    text = read(name)
    for pattern, label in FORBIDDEN:
        assert not re.search(pattern, text), f"{name}: {label}"


def test_local_storage_lives_only_in_shell_state_and_every_access_is_guarded():
    users = sorted(path.name for path in JS.glob("*.js") if "localStorage" in path.read_text(encoding="utf-8"))
    assert users == ["shell-state.js"]
    source = read("shell-state.js")
    uses = [m.start() for m in re.finditer(r"window\.localStorage", source)]
    assert len(uses) == 2   # one read, one write
    for position in uses:
        before = source[:position]
        assert before.rfind("try {") > before.rfind("catch"), position     # inside a try that is not closed yet
    assert source.count("catch (_)") >= 3     # the read, the write and the media-query lookup


# --- landmarks, skip link, keyboard -----------------------------------------------------------------------------

def test_shell_has_banner_nav_main_and_complementary_landmarks():
    shell = read("shell.js")
    assert '<header class="topbar">' in shell
    assert re.search(r'<nav id="sidebar" class="sidebar" aria-label="[^"]+"', shell)
    assert re.search(r'<main id="main-content" class="content" tabindex="-1"', shell)
    assert re.search(r'<aside id="right-panel" class="rightpanel" aria-label="[^"]+"', shell)
    assert shell.count("<h1>") == 1 and "<h2" not in shell
    assert shell.index('<header class="topbar">') < shell.index('<nav id="sidebar"')
    assert shell.index('badge mock') < shell.index('<nav id="sidebar"')  # MOCK stays visible outside the mobile drawer
    assert 'class="side-footer"' in shell and 'role="status" aria-live="polite"' in shell
    assert 'class="content-toolbar"' in shell and '<${PendingInbox}' not in shell
    # the views and the panel come in as props: main.js composes them
    main = read("main.js")
    for name in ("Shell", "Sidebar", "RightPanel"):
        assert f"<${{{name}}}" in main, name
    assert '<${Sessions} token=${token} route=${route}' in main and "<${Runs}" in main    # existing views kept


def test_skip_link_is_first_and_does_not_fight_the_hash_router():
    shell = read("shell.js")
    app = shell[shell.index('<div class="app"'):]
    assert app.index('class="skip-link"') < app.index("<header")
    assert 'href="#main-content"' in shell
    skip = shell[shell.index("const skip ="):]
    skip = skip[:skip.index("};")]
    # the address belongs to the router ("#main-content" would be read as an unknown section): move the focus instead
    assert "preventDefault()" in skip and "focus()" in skip


def test_drawer_closes_with_esc_without_going_back_in_the_session():
    shell = read("shell.js")
    assert 'event.key !== "Escape"' in shell
    assert 'document.addEventListener("keydown", onKey, true)' in shell     # capture: before the session screen
    assert 'document.removeEventListener("keydown", onKey, true)' in shell
    assert "event.preventDefault()" in shell and ".help-block.open" in shell   # an open "?" help keeps its own Esc
    assert "inert=${drawerOpen}" in shell                                       # nothing behind the drawer is reachable
    detail = read("session-detail.js")
    assert "!event.defaultPrevented" in detail                                  # what makes the two cooperate
    assert "menuButton.current.focus()" in shell                                # focus goes back to the button


def test_both_toggles_are_buttons_with_aria_expanded_and_aria_controls_that_exist():
    shell = read("shell.js")
    menu = re.search(r'<button type="button" class="tool-btn" ref=\$\{menuButton\}[^>]*?>', shell, re.S)[0]
    panel = re.search(r'<button type="button" class="tool-btn" aria-expanded=\$\{panelOpen[^>]*?>', shell, re.S)[0]
    assert 'aria-expanded=${drawerOpen ? "true" : "false"}' in menu and 'aria-controls="sidebar"' in menu
    assert 'aria-expanded=${panelOpen ? "true" : "false"}' in panel and 'aria-controls="right-panel"' in panel
    assert 'id="sidebar"' in shell and 'id="right-panel"' in shell
    assert "hidden=${!panelOpen}" in shell                                       # collapsed means really gone
    assert "readPanelOpen(defaultPanelOpen())" in shell and "writePanelOpen(next)" in shell
    sidebar = read("sidebar.js")
    assert 'aria-expanded=${moreOpen ? "true" : "false"}' in sidebar and 'aria-controls="side-more"' in sidebar
    assert 'id="side-more"' in sidebar and 'hidden=${!moreOpen}' in sidebar


def test_sidebar_has_new_session_search_groups_and_readable_status():
    sidebar = read("sidebar.js")
    assert "Nueva sesión" in sidebar and "navigate(NEW_SECTION)" in sidebar
    assert 'role="search"' in sidebar and '<label for="side-search">' in sidebar and 'id="side-search"' in sidebar
    assert 'type="search"' in sidebar and 'aria-live="polite"' in sidebar
    assert 'side-summary" role="status"' not in sidebar  # the connection in the sidebar footer is its status landmark
    assert '<${PendingInbox} />' in sidebar and 'class="side-pending"' in sidebar
    assert 'class="side-sessions-head"' in sidebar and 'Ver todas' in sidebar
    assert 'navigation.primary.map' in sidebar and 'navigation.more.map' in sidebar
    # the status is text too: the dot is a StatusChip (GUI2-T11) whose component says the words for screen readers
    chip = (JS / "ui" / "status-chip.js").read_text(encoding="utf-8")
    assert '<${StatusChip} status=${item.dot} labels="dot" variant="dot"' in sidebar
    assert 'class="sr-only"' in chip and "Estado: " in chip and 'aria-hidden="true"' in chip
    assert 'aria-current=${current ? "page" : null}' in sidebar
    assert "<details" in sidebar and "<summary" in sidebar                          # project groups fold natively
    assert "countsOf(group)" in sidebar                                             # a count per app
    assert "/gui/api/sessions" in sidebar and "/gui/api/runs" in sidebar
    assert "/api/v1" not in sidebar and "/tasks" not in sidebar                     # no task board: no GUI endpoint serves it
    assert not re.search(r"setInterval\([^;]*,\s*\d{1,4}\s*\)", sidebar)           # no fast polling


def test_right_panel_has_the_four_sections_and_a_named_slot_for_subagents():
    panel = read("right-panel.js")
    titles = re.findall(r'<h3 id="(panel-[a-z]+-title)">([^<]+)</h3>', panel)
    assert titles == [("panel-changes-title", "Cambios"), ("panel-subagents-title", "Subagentes"),
                      ("panel-tokens-title", "Tokens"), ("panel-sources-title", "Fuentes")]
    assert len(re.findall(r'<section class="rp-section" aria-labelledby="panel-[a-z]+-title">', panel)) == 4
    slots = read("panel-slots.js")
    assert re.search(r'id="subagents-slot" class="slot" data-slot="subagents"', slots)
    for slot in ("ChangesSlot", "SubagentsSlot", "TokensSlot", "SourcesSlot"):
        assert re.search(rf"export function {slot}\(", slots), slot
        assert f"<${{{slot}}} ...${{slot}} />" in panel
    assert slots.count("Sin ") >= 3 and "GUI2-T04" in slots                         # empty states and who plugs in where
    assert re.search(r"const slot = \{ token, route, sessionId \}", panel)


def test_the_subagents_panel_is_inside_its_section_and_there_is_one_right_panel_only():
    panel, slots, mount = read("right-panel.js"), read("panel-slots.js"), read("subagents-mount.js")
    # the section «Subagentes» holds the slot, the slot holds the mount, the mount holds the T04 panel
    section = panel[panel.index('aria-labelledby="panel-subagents-title"'):panel.index('aria-labelledby="panel-tokens-title"')]
    assert "<${SubagentsSlot} ...${slot} />" in section and 'topic="subagents.panel"' in section
    assert "<${SubagentsMount} token=${token} openId=${sessionId} />" in slots
    assert "<${SubagentsPanel}" in mount and "export function SubagentsMount" in mount
    # no second column: no dock element, no body class, no side effect when the module loads, no import from main.js
    for name in ("subagents-mount.js", "subagents-panel.js", "panel-slots.js"):
        source = read(name)
        for forbidden in ("subagents-dock", "has-subagents", "document.body", "appendChild", "render("):
            assert forbidden not in source, (name, forbidden)
    assert "subagents" not in read("main.js")
    sheet = css() + (STATIC_DIR / "subagents-panel.css").read_text(encoding="utf-8")
    assert "subagents-dock" not in sheet and "has-subagents" not in sheet
    # the heading and its "?" are drawn once (by the section), so the help ids are not repeated
    assert "<h3" not in read("subagents-panel.js") and 'topic="subagents.panel"' not in read("subagents-panel.js")
    assert read("shell.js").count("<aside") == 1 and read("shell.js").count('id="right-panel"') == 1


def test_the_old_children_list_of_the_session_screen_is_gone_with_its_help():
    detail, help_text = read("session-detail.js"), read("help-text.js")
    assert "function Children" not in detail and "children-title" not in detail and "session.children" not in detail
    assert '"session.children"' not in help_text and '"panel.subagents"' not in help_text
    assert ".child-list" not in css()


# --- help for every new control (tests/test_gui_help.py enforces the rest) ---------------------------------------

def test_every_new_control_and_section_has_a_help_entry_that_a_view_uses():
    text = read("help-text.js")
    used = "".join(read(name) for name in ("shell.js", "sidebar.js", "right-panel.js"))
    for topic in NEW_HELP_TOPICS:
        assert f'"{topic}": {{' in text, topic
        assert f'topic="{topic}"' in used, topic
    controls = {"shell.js": ["shell.menu", "shell.panel"], "sidebar.js": ["shell.new_session", "shell.search"]}
    for name, topics in controls.items():
        for topic in topics:
            assert f'helpFor("{topic}")' in read(name), (name, topic)


# --- stylesheet --------------------------------------------------------------------------------------------------

def test_stylesheet_has_dark_mode_neutral_tokens_one_accent_and_the_plex_font_stacks():
    sheet = css()
    assert "@media (prefers-color-scheme: dark)" in sheet and "color-scheme: light dark" in sheet
    dark = media(sheet, "(prefers-color-scheme: dark)")
    for token in ("--bg:", "--panel:", "--chrome:", "--hover:", "--text:", "--muted:", "--line:", "--accent:", "--accent-soft:", "--warn-soft:"):
        assert token in dark, token
    assert len(re.findall(r"--accent:", sheet)) == 2          # one neutral action colour, in light and in dark
    assert re.search(r'--sans: "IBM Plex Sans", system-ui', sheet) and re.search(r'--mono: "IBM Plex Mono", ui-monospace', sheet)
    assert "sans-serif;" in sheet and "monospace;" in sheet   # system fallbacks at the end of both stacks
    for forbidden in ("@import", "@font-face", "url(", "style=", "http://", "https://"):
        assert forbidden not in sheet, forbidden


def test_stylesheet_has_the_drawer_and_the_stacking_rules_below_900px():
    sheet = css()
    narrow = media(sheet, "(max-width: 899.98px)")
    assert "position: fixed" in rule(narrow, ".sidebar") and "translateX(-100%)" in rule(narrow, ".sidebar")
    assert "visibility: hidden" in rule(narrow, ".sidebar")                       # a closed drawer is not tabbable
    assert re.search(r'\.app\[data-drawer="open"\] \.sidebar \{[^}]*visibility: visible', narrow)
    assert "display: block" in rule(narrow, ".scrim") and "display: flex" in rule(narrow, ".menu-only")
    assert "position: static" in rule(narrow, ".rightpanel")                      # the panel stacks below the content
    assert "grid-template-columns: minmax(0, 1fr)" in narrow
    state = read("shell-state.js")
    assert 'WIDE_QUERY = "(min-width: 900px)"' in state and 'NARROW_QUERY = "(max-width: 899.98px)"' in state
    assert "overflow-x: clip" in rule(sheet, ".app")                              # no horizontal page scroll
    assert "max-width: 599.98px" in sheet and "flex-wrap: wrap" in media(sheet, "(max-width: 599.98px)")


def test_stylesheet_has_visible_focus_touch_targets_and_reduced_motion():
    sheet = css()
    assert re.search(r"--target: 44px", sheet)
    for selector in (".btn", ".tool-btn", ".side-item", ".views button", ".skip-link", ".work-fold > details > summary",
                     ".group > summary"):
        assert "min-height: var(--target)" in rule(sheet, selector), selector
    assert "min-height: var(--target)" in rule(sheet, 'input:not([type="checkbox"])')
    assert "inset: -10px" in rule(sheet, ".help-toggle::after")                   # "?" keeps a 44 px hit area
    focus = rule(sheet, ".btn:focus-visible")
    assert "outline: 2px solid var(--accent)" in focus and "a:focus-visible" in sheet and "summary:focus-visible" in sheet
    assert ".skip-link:focus" in sheet and ".sr-only" in sheet
    assert sheet.count("prefers-reduced-motion") >= 3
    for shape in ("disc", "ring", "square", "diamond"):                           # the dot differs in shape, not only in colour
        assert f'.dot[data-shape="{shape}"]' in sheet


# --- the logic, run with Node ------------------------------------------------------------------------------------

def run_node(script: str):
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def uri(name: str) -> str:
    return json.dumps((JS / name).as_uri())


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_sidebar_groups_by_project_with_dots_counts_and_search():
    out = run_node("""
      import { sessionItems, runItems, buildGroups, countsOf, dotText, isCurrent, folderName, sessionDot, runDot } from %s;
      const sessions = [
        { id: "claude:1", source: "claude", native_id: "1", title: "Arreglar CSS", cwd: "C:\\\\work\\\\Proj", status: "active", parent_id: null, updated_at: "2026-10-11T10:00:00Z" },
        { id: "codex:2", source: "codex", native_id: "2", title: "", cwd: "/home/u/proj/", status: "idle", parent_id: null, updated_at: "2026-10-10T10:00:00Z" },
        { id: "claude:3", source: "claude", native_id: "agent-3", title: "", cwd: "C:\\\\work\\\\Proj", status: "active", parent_id: "claude:1", updated_at: "2026-10-11T10:05:00Z" },
        { id: "framework:r1", source: "framework", native_id: "r1", title: "social_post", cwd: null, status: "idle", parent_id: null, updated_at: "2026-10-11T11:00:00Z" },
        { id: "copilot:4", source: "copilot", native_id: "4", title: "Docs", cwd: null, status: "unknown", parent_id: null, updated_at: "2026-10-09T10:00:00Z" },
      ];
      const runs = [
        { id: "r1", workflow_id: "social_post", project_name: "proj", status: "running", updated_at: "2026-10-11T11:00:00Z" },
        { id: "r2", workflow_id: "resumen", project_name: "Otro", status: "failed", updated_at: "2026-10-08T11:00:00Z" },
        { id: "r3", workflow_id: "resumen", project_name: " ", status: "completed", created_at: "2026-10-07T11:00:00Z" },
      ];
      const items = sessionItems(sessions).concat(runItems(runs));
      const groups = buildGroups(items, "");
      const route = { section: "runs", id: "r1" };
      console.log(JSON.stringify({
        keys: items.map((i) => i.key),
        groups: groups.map((g) => ({ name: g.name, items: g.items.map((i) => i.key), counts: countsOf(g).map((c) => c.app + ":" + c.count), folders: g.folders })),
        dots: items.map((i) => i.key + "=" + i.dot),
        text: dotText("working", "En ejecución"),
        search: {
          error: buildGroups(items, "falló").flatMap((g) => g.items.map((i) => i.key)),
          noAccent: buildGroups(items, "EJECUCION en").flatMap((g) => g.items.map((i) => i.key)),
          two: buildGroups(items, "claude css").flatMap((g) => g.items.map((i) => i.key)),
          none: buildGroups(items, "zzz").length,
        },
        current: items.filter((i) => isCurrent(i, route)).map((i) => i.key),
        folder: [folderName("C:\\\\a\\\\b\\\\"), folderName("/x/y"), folderName(""), folderName(null)],
        direct: [sessionDot("active"), sessionDot("archived"), runDot("waiting_human"), runDot("interrupted"), runDot("cancelled"), runDot("zzz")],
      }));
    """ % uri("sidebar-model.js"))
    assert out["keys"] == ["claude:1", "codex:2", "copilot:4", "run:r1", "run:r2", "run:r3"]   # no subagent, no framework session
    by_name = {group["name"]: group for group in out["groups"]}
    assert set(by_name) == {"Proj", "Sin proyecto", "Otro"}                                    # "Proj" = "proj" = run project "proj"
    assert by_name["Proj"]["items"] == ["run:r1", "claude:1", "codex:2"]                        # newest first
    assert by_name["Proj"]["counts"] == ["claude:1", "codex:1", "workflow:1"]
    assert by_name["Sin proyecto"]["items"] == ["copilot:4", "run:r3"] and by_name["Sin proyecto"]["counts"] == ["copilot:1", "workflow:1"]
    assert [group["name"] for group in out["groups"]] == ["Proj", "Sin proyecto", "Otro"]      # the most recently active first
    assert by_name["Proj"]["folders"] == ["C:\\work\\Proj", "/home/u/proj/"]
    assert out["dots"] == ["claude:1=working", "codex:2=finished", "copilot:4=finished", "run:r1=working",
                           "run:r2=error", "run:r3=finished"]
    assert out["text"] == "Trabajando (en ejecución)"
    assert out["search"]["error"] == ["run:r2"] and out["search"]["two"] == ["claude:1"] and out["search"]["none"] == 0
    assert out["search"]["noAccent"] == ["run:r1"]                       # "En ejecución" found without accents or capitals
    assert out["current"] == ["run:r1"]
    assert out["folder"] == ["b", "y", "", ""]
    assert out["direct"] == ["working", "finished", "working", "error", "finished", "finished"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_work_blocks_fold_consecutive_tool_items_into_one_summary():
    out = run_node("""
      import { foldWork, workLabel } from %s;
      const at = (kind, seconds) => ({ kind, timestamp: seconds === null ? null : new Date(Date.UTC(2026, 9, 11, 10, 0, seconds)).toISOString() });
      const items = [
        at("user_message", 0), at("reasoning_omitted", 1), at("tool_call", 2), at("tool_result", 62), at("tool_call", 100),
        at("tool_result", 122), at("assistant_message", 130), at("tool_result", 131), at("usage", 132), at("system", 140),
        at("tool_call", 150), at("assistant_message", 155), at("tool_call", null), at("tool_call", null),
      ];
      const blocks = foldWork(items);
      const shape = blocks.map((b) => (b.fold ? "fold:" + b.entries.map((e) => e.index).join(",") : "item:" + b.index));
      console.log(JSON.stringify({
        shape,
        labels: blocks.filter((b) => b.fold).map(workLabel),
        tools: blocks.filter((b) => b.fold).map((b) => b.tools),
        none: foldWork([]).length,
        onlyResults: foldWork([at("tool_result", 1), at("tool_result", 2)]).map((b) => b.fold),
      }));
    """ % uri("work-fold.js"))
    assert out["shape"] == ["item:0", "fold:1,2,3,4,5", "item:6", "item:7", "item:8", "item:9", "fold:10", "item:11", "fold:12,13"]
    assert out["labels"] == ["Trabajó 2 min · 2 herramientas",                               # 1 s to 122 s, rounded to minutes
                             "Trabajó · 1 herramienta", "Trabajó · 2 herramientas"]          # no timestamps: no duration
    assert out["tools"] == [2, 1, 2] and out["none"] == 0 and out["onlyResults"] == [False, False]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_short_work_says_less_than_a_minute():
    out = run_node("""
      import { foldWork, workLabel } from %s;
      const at = (kind, s) => ({ kind, timestamp: new Date(Date.UTC(2026, 9, 11, 10, 0, s)).toISOString() });
      const label = (items) => workLabel(foldWork(items)[0]);
      console.log(JSON.stringify([label([at("tool_call", 0), at("tool_result", 20)]), label([at("tool_call", 0), at("tool_result", 40)]),
        label([at("tool_call", 0), at("tool_result", 3600)])]));
    """ % uri("work-fold.js"))
    assert out == ["Trabajó menos de 1 min · 1 herramienta", "Trabajó 1 min · 1 herramienta", "Trabajó 60 min · 1 herramienta"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_panel_state_survives_blocked_storage_and_the_router_knows_new():
    out = run_node("""
      const store = new Map();
      let mode = "ok";
      const storage = {
        getItem(key) { if (mode === "throw") throw new Error("blocked"); return store.has(key) ? store.get(key) : null; },
        setItem(key, value) { if (mode === "throw") throw new Error("blocked"); store.set(key, String(value)); },
      };
      let wide = true;
      globalThis.window = {
        get localStorage() { if (mode === "getter") throw new Error("denied"); return storage; },
        matchMedia() { if (mode === "media") throw new Error("no media queries"); return { matches: wide }; },
      };
      const state = await import(%s);
      const result = {};
      result.fresh = state.readPanelOpen(true);
      state.writePanelOpen(false); result.closed = state.readPanelOpen(true);
      state.writePanelOpen(true); result.opened = state.readPanelOpen(false);
      store.set("multiagent.gui.right-panel", "garbage"); result.garbage = state.readPanelOpen(false);
      mode = "throw"; result.thrown = [state.readPanelOpen(true), state.readPanelOpen(false)]; state.writePanelOpen(true);
      mode = "getter"; result.getter = state.readPanelOpen(true); state.writePanelOpen(false);
      mode = "ok"; wide = false; result.narrow = state.defaultPanelOpen(); wide = true; result.wide = state.defaultPanelOpen();
      mode = "media"; result.media = state.defaultPanelOpen();
      const route = await import(%s);
      result.route = [route.parseRoute("#new"), route.parseRoute("#new/"), route.parseRoute("#runs/abc"),
        route.parseRoute("#sessions"), route.parseRoute("#run=xyz"), route.parseRoute("#main-content"), route.routeHref("new")];
      console.log(JSON.stringify(result));
    """ % (uri("shell-state.js"), uri("route.js")))
    assert out["fresh"] is True and out["closed"] is False and out["opened"] is True and out["garbage"] is False
    assert out["thrown"] == [True, False] and out["getter"] is True        # blocked storage: the fallback, never an exception
    assert out["narrow"] is False and out["wide"] is True and out["media"] is True
    assert out["route"] is not None                                         # route.js loads in Node (it needs no DOM at import)
    new, new_slash, run, sessions, legacy, unknown, href = out["route"]
    assert new == {"section": "new", "id": None} and run == {"section": "runs", "id": "abc"}
    assert new_slash["section"] == "sessions"                               # only "#new" itself is the new-session address
    assert sessions == {"section": "sessions", "id": None} and legacy == {"section": "runs", "id": "xyz"}
    assert unknown["section"] == "sessions" and href == "#new"


# --- how it is served ----------------------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    stores = make_stores(tmp_path)
    services = make_services(stores)
    write_claude(stores)
    write_codex(stores)
    write_copilot(stores)
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token, auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


def test_gui_serves_the_shell_page_without_a_token_and_with_the_csp(world):
    page = world.client.get("/gui/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert page.headers["content-security-policy"] == CSP
    assert '<div id="app"></div>' in page.text and "/gui/static/js/main.js" in page.text
    assert "<script>" not in page.text and "style=" not in page.text
    sheet = world.client.get("/gui/static/app.css")
    assert sheet.status_code == 200 and ".sidebar" in sheet.text and ".rightpanel" in sheet.text
    assert "prefers-color-scheme: dark" in sheet.text


@pytest.mark.parametrize("name", SHELL_FILES + ["main.js", "route.js", "runs.js", "session-items.js", "help-text.js"])
def test_shell_files_are_served_with_the_csp_and_no_token(world, name):
    response = world.client.get(f"/gui/static/js/{name}")
    assert response.status_code == 200 and "javascript" in response.headers["content-type"]
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"


def test_main_imports_exactly_the_modules_that_are_served(world):
    imported = set(re.findall(r'from "\./([a-z0-9-]+\.js)"', read("main.js")))
    assert {"shell.js", "sidebar.js", "right-panel.js", "runs.js", "sessions.js", "route.js"} <= imported
    for name in imported:
        assert world.client.get(f"/gui/static/js/{name}").status_code == 200, name


@pytest.mark.parametrize("path", ["/gui/api/session", "/gui/api/sessions", "/gui/api/runs", "/gui/api/workflows",
                                  "/gui/api/bundles", "/gui/api/stream"])
def test_the_gui_api_is_still_protected_by_the_token(world, path):
    assert world.client.get(path).status_code == 401
    assert world.client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert world.client.get(path, headers={"Authorization": f"Basic {world.token}"}).status_code == 401
    if not path.endswith("/stream"):
        assert world.client.get(path, headers=world.auth).status_code == 200


def used(text: str, name: str) -> set[str]:
    return set(re.findall(rf"\b{name}\.([a-z_]+)", text))


def test_the_fields_the_sidebar_reads_are_the_ones_the_apis_send(world):
    run_id = add_framework_run(world.services, name="proj-run", events=1)
    sessions = world.client.get("/gui/api/sessions?limit=100", headers=world.auth).json()
    runs = world.client.get("/gui/api/runs?limit=50", headers=world.auth).json()
    model = read("sidebar-model.js")
    rows = [s for s in sessions["sessions"] if s["source"] != "framework"]
    assert rows and any(s["id"] == f"framework:{run_id}" for s in sessions["sessions"])   # the API repeats runs as sessions...
    assert 'session.source !== "framework"' in model                                        # ...and the sidebar leaves them out
    assert used(model, "session") <= set(rows[0]), used(model, "session") - set(rows[0])
    assert used(model, "run") <= set(runs[0]), used(model, "run") - set(runs[0])
    assert {"sessions", "hint"} <= set(sessions) and runs[0]["id"] == run_id
    assert {s["source"] for s in rows} == {"claude", "codex", "copilot"} and CLAUDE_ID in "".join(s["id"] for s in rows)
