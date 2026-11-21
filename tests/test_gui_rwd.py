# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""RWD-T01: reference widths (360/390 px phone, 768 px tablet, desktop), unified breakpoints, and per-view checks.

All checks are static (no browser, no Playwright, no TestClient) using the ``rule`` and ``media`` helpers from
``tests/test_gui2_layout.py`` plus a pure Node check on ``shell-state.js`` (skipped if Node is absent). Things that
depend on real rendered geometry (visual wrapping, no horizontal overflow with live content at 390 and 768 px) are
documented in each test's docstring and verified by manual QA."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from multiagent.gui import STATIC_DIR
from test_gui2_layout import media, rule

JS = STATIC_DIR / "js"
EXT = STATIC_DIR / "extensions"
NODE = shutil.which("node")

TABLET_QUERY = "(max-width: 899.98px)"
PHONE_QUERY = "(max-width: 599.98px)"
ALLOWED_WIDTH_QUERIES = {TABLET_QUERY, PHONE_QUERY}

CORE_SHEETS = [
    STATIC_DIR / "app.css",
    STATIC_DIR / "new-session.css",
    STATIC_DIR / "panels.css",
    STATIC_DIR / "agent-tree.css",
    STATIC_DIR / "subagents-panel.css",
]


def strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def all_sheets() -> list[Path]:
    return [*CORE_SHEETS, *sorted(EXT.glob("*/style.css"))]


def read_sheet(path: Path) -> str:
    return strip_comments(path.read_text(encoding="utf-8"))


def all_media(source: str, query: str) -> str:
    """Concatenate every ``@media <query>`` block in ``source`` using ``media`` from ``test_gui2_layout``."""
    parts: list[str] = []
    pos = 0
    needle = f"@media {query} {{"
    while True:
        idx = source.find(needle, pos)
        if idx < 0:
            break
        block = media(source[idx:], query)
        parts.append(block)
        pos = idx + len(needle) + len(block) + 1
    return "\n".join(parts)


# --- 1. Reference widths, unified breakpoints, fixed widths, overflow containers, targets, right panel -------------

def test_app_css_documents_reference_widths_and_unified_breakpoints():
    """Verify app.css documents 360/390 px, 768 px, and the two breakpoints, and all sheets use only those two cuts.

    Manual QA at 390 px and 768 px confirms the actual viewport transitions match the intended breakpoints."""
    raw = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    header = raw[:raw.index(":root")]
    for token in ("360", "390", "768", "899.98px", "599.98px"):
        assert token in header, f"app.css header comment missing {token}"

    for path in all_sheets():
        text = read_sheet(path)
        width_queries = [
            f"({cond.strip()})"
            for cond in re.findall(r"@media\s*\(([^)]*(?:width)[^)]*)\)\s*\{", text)
        ]
        unexpected = [q for q in width_queries if q not in ALLOWED_WIDTH_QUERIES]
        assert not unexpected, f"{path.relative_to(STATIC_DIR)} has non-unified breakpoints: {unexpected}"


def test_no_rule_fixes_width_or_min_width_above_360px_without_max_width_100():
    """Verify no CSS rule fixes a width/min-width > 360 px (or 22.5 rem) without ``max-width: 100%``.

    Manual QA at 390 px and 768 px confirms no dynamic text or unstyled element forces page-level horizontal scroll."""
    for path in all_sheets():
        text = read_sheet(path)
        for prelude, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
            if prelude.strip().startswith("@"):
                continue
            has_cap = bool(re.search(r"max-width\s*:\s*100(?:%|vw)", body))
            for prop, val in re.findall(r"(?<![-\w])((?:min-)?width)\s*:\s*([^;]+)", body):
                clean = val.strip()
                if clean.startswith(("min(", "clamp(")):
                    continue
                px_match = re.fullmatch(r"(\d+(?:\.\d+)?)px", clean)
                rem_match = re.fullmatch(r"(\d+(?:\.\d+)?)rem", clean)
                px = float(px_match[1]) if px_match else (float(rem_match[1]) * 16 if rem_match else 0.0)
                assert px <= 360 or has_cap, f"{path.relative_to(STATIC_DIR)} {prelude.strip()}: {prop}: {clean} > 360px without max-width: 100%"
            for minmax_arg in re.findall(r"minmax\(\s*([^,)]+)", body):
                arg = minmax_arg.strip()
                if arg.startswith("min("):
                    continue
                px_match = re.fullmatch(r"(\d+(?:\.\d+)?)px", arg)
                rem_match = re.fullmatch(r"(\d+(?:\.\d+)?)rem", arg)
                px = float(px_match[1]) if px_match else (float(rem_match[1]) * 16 if rem_match else 0.0)
                assert px <= 360, f"{path.relative_to(STATIC_DIR)} {prelude.strip()}: minmax({arg}, ...) > 360px"


def test_every_table_and_pre_in_views_has_overflow_x_auto_container():
    """Verify every ``<table>`` and ``<pre>`` rendered by the GUI views has an ``overflow-x: auto`` container or rule.

    Manual QA at 390 px and 768 px confirms wide tables and long JSON/diff blocks scroll locally without widening the page."""
    app = read_sheet(STATIC_DIR / "app.css")
    table_wrap = rule(app, ".usage-table-wrap")
    assert "overflow-x: auto" in table_wrap and "max-width: 100%" in table_wrap and "min-width: 0" in table_wrap
    pre_rule = rule(app, "pre")
    assert "overflow-x: auto" in pre_rule and "max-width: 100%" in pre_rule
    pre_kinds = rule(app, "pre.data")
    assert "overflow-x: auto" in pre_kinds and "max-width: 100%" in pre_kinds
    # pre.json (used by external-output.js) shares the same selector list as pre.data and pre.prompt.
    assert "overflow-x: auto" in rule(app, "pre.json") and "overflow-x: auto" in rule(app, "pre.prompt")

    for js_file in sorted([*JS.rglob("*.js"), *EXT.rglob("*.js")]):
        src = js_file.read_text(encoding="utf-8")
        for match in re.finditer(r"<table\b([^>]*)>", src):
            before = src[max(0, match.start() - 120):match.start()]
            assert 'class="usage-table-wrap"' in before, f"{js_file.name}: <table> not wrapped in .usage-table-wrap"
        for match in re.finditer(r"<pre\b([^>]*)>", src):
            attrs = match[1]
            cls = re.search(r'class="([^"]+)"', attrs)
            if cls and cls[1] not in {"data", "prompt", "json"}:
                # Any other <pre> class (e.g. CHT-T02's .thread-output) needs its own rule that scrolls inside itself.
                own = [body for sheet in CORE_SHEETS for body in re.findall(
                    r"(?:^|[},])\s*(?:pre)?\." + re.escape(cls[1]) + r"\s*(?:,[^{]*)?\{([^{}]*)\}", read_sheet(sheet))]
                assert any(re.search(r"overflow(?:-x)?:\s*auto", body) for body in own), \
                    f"{js_file.name}: <pre class='{cls[1]}'> has no rule with overflow auto"


def test_right_panel_starts_collapsed_under_phone_and_tablet_breakpoints():
    """Verify the right panel is hidden by default below 900 px (covering both tablet 768 px and phone 360/390 px).

    Manual QA at 390 px and 768 px confirms the panel starts collapsed on first visit and opens via the Panel button."""
    app = read_sheet(STATIC_DIR / "app.css")
    tablet = all_media(app, TABLET_QUERY)
    assert "position: static" in rule(tablet, ".rightpanel")
    assert "display: none !important" in rule(app, "[hidden]")

    shell = (JS / "shell.js").read_text(encoding="utf-8")
    assert "readPanelOpen(defaultPanelOpen())" in shell
    assert "hidden=${!panelOpen}" in shell

    state = (JS / "shell-state.js").read_text(encoding="utf-8")
    assert 'WIDE_QUERY = "(min-width: 900px)"' in state

    if NODE is not None:
        script = """
          const store = new Map();
          let width = 390;
          globalThis.window = {
            localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) },
            matchMedia: (q) => ({ matches: q === "(min-width: 900px)" ? width >= 900 : width < 900 }),
          };
          const { defaultPanelOpen, readPanelOpen } = await import(%s);
          const out = {};
          for (const w of [360, 390, 768, 1200]) {
            width = w;
            out[w] = readPanelOpen(defaultPanelOpen());
          }
          console.log(JSON.stringify(out));
        """ % json.dumps((JS / "shell-state.js").as_uri())
        run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        assert run.returncode == 0, run.stderr
        out = json.loads(run.stdout)
        assert out == {"360": False, "390": False, "768": False, "1200": True}


def test_controls_and_shell_navigation_meet_target_height_and_wrap():
    """Verify touch-target height (--target: 44px) and wrapping of sidebar view buttons and inputs.

    Manual QA at 390 px and 768 px confirms tapped controls have >= 44 px height and sidebar buttons never overflow."""
    app = read_sheet(STATIC_DIR / "app.css")
    assert "--target: 44px" in rule(app, ":root")
    for sel in (".btn", ".tool-btn", ".views button", ".side-sessions-head button", ".side-item", ".run-list button", ".session-row",
                ".tab", 'input:not([type="checkbox"])'):
        assert "min-height: var(--target)" in rule(app, sel), sel
    targets = app[app.index('input:not([type="checkbox"])'):]
    for sel in ("select", "textarea"):
        assert "min-height: var(--target)" in rule(targets, sel), sel
    assert "flex-wrap: wrap" in rule(app, ".views")
    assert "min-width: 0" in rule(app, ".views button")


# --- 2. One static check per view in the specification -------------------------------------------------------------

def test_view_new_session_rwd():
    """Nueva sesión: chat cards, routing selectors, composer, and classic create-run form adapt cleanly down to 360 px.

    Manual QA at 390 px and 768 px: open #new, step through workflow cards, routing selects, and 'Ver como formulario'."""
    ns_css = read_sheet(STATIC_DIR / "new-session.css")
    assert "min-width: 0" in rule(ns_css, ".ns")
    assert "minmax(min(100%, 15rem), 1fr)" in rule(ns_css, ".ns-cards")
    assert "min-width: 0" in rule(ns_css, ".ns-question")
    assert "min-width: 0" in rule(ns_css, ".ns-confirm")
    assert "min-width: 0" in rule(ns_css, ".ns-routing")
    assert "flex-wrap: wrap" in rule(ns_css, ".ns-route-chip") and "max-width: 100%" in rule(ns_css, ".ns-route-chip")
    phone = all_media(ns_css, PHONE_QUERY)
    assert "width: 100%" in rule(phone, ".ns-route-chip")
    assert "max-width: 100%" in rule(phone, ".ns-route-chip select")


def test_view_teams_rwd():
    """Equipos: team list table, version table, and Mermaid <pre> wrap or scroll inside their containers.

    Manual QA at 390 px and 768 px: open #teams and a team detail; verify table horizontal scroll and Mermaid block."""
    teams = (JS / "teams.js").read_text(encoding="utf-8")
    assert '<div class="usage-table-wrap"><table class="usage-table">' in teams
    assert '<pre class="data"' in teams
    app = read_sheet(STATIC_DIR / "app.css")
    assert "min-width: 0" in rule(app, ".usage-view")
    assert "min-width: 0" in rule(app, ".usage-comparison")


def test_view_plans_rwd():
    """Planes: proposal/task tables wrap in .usage-table-wrap, two-column fields stack on tablet, facts stack on phone.

    Manual QA at 390 px and 768 px: open #plans and a plan detail with 'Editar tareas' open."""
    plans = (JS / "plans.js").read_text(encoding="utf-8")
    assert plans.count('<div class="usage-table-wrap"><table class="usage-table">') == 2
    app = read_sheet(STATIC_DIR / "app.css")
    tablet = all_media(app, TABLET_QUERY)
    phone = all_media(app, PHONE_QUERY)
    assert "grid-template-columns: minmax(0, 1fr)" in rule(tablet, ".two")
    assert "grid-template-columns: minmax(0, 1fr)" in rule(phone, ".facts")
    assert "grid-template-columns: minmax(0, 1fr)" in rule(phone, ".history li")


def test_view_runs_list_and_detail_rwd():
    """Ejecuciones (lista y detalle): master-detail stacks under tablet cut, compare grids shrink, external JSON <pre> scrolls.

    Manual QA at 390 px and 768 px: open #runs, select a run with comparison diffs, technical events, and external output."""
    runs = (JS / "runs.js").read_text(encoding="utf-8")
    detail = (JS / "run-detail.js").read_text(encoding="utf-8")
    ext_out = (JS / "external-output.js").read_text(encoding="utf-8")
    assert 'class="runs"' in runs and 'class="runs-side"' in runs and 'class="runs-main"' in runs
    assert 'class="compare-values"' in detail and '<pre class="json">' in ext_out
    app = read_sheet(STATIC_DIR / "app.css")
    assert "min-width: 0" in rule(app, ".runs-side")
    assert "min-width: 0" in rule(app, ".runs-main")
    assert "min-width: 0" in rule(app, ".compare-values > div")
    assert "overflow-x: auto" in rule(app, "pre.json")
    tablet = all_media(app, TABLET_QUERY)
    assert "grid-template-columns: minmax(0, 1fr)" in rule(tablet, ".runs")
    assert "grid-template-columns: minmax(0, 1fr)" in rule(tablet, ".compare-values")


def test_view_results_evaluation_rwd():
    """Resultados (Evaluación): evaluation set, runs, and case comparison tables sit inside .usage-table-wrap.

    Manual QA at 390 px and 768 px: open #evaluation and an evaluation set detail."""
    evaluation = (JS / "evaluation.js").read_text(encoding="utf-8")
    assert evaluation.count('<div class="usage-table-wrap"><table class="usage-table">') == 3
    app = read_sheet(STATIC_DIR / "app.css")
    assert "min-width: 0" in rule(app, ".usage-table-wrap")
    assert "overflow-x: auto" in rule(app, ".usage-table-wrap")


def test_view_usage_rwd():
    """Uso: plan/cost grids use min(100%, 280px), token table scrolls, and refresh control goes full-width on phone.

    Manual QA at 390 px and 768 px: open #usage and switch between 'Planes', 'Global', and 'API y presupuesto'."""
    usage = (JS / "usage.js").read_text(encoding="utf-8")
    assert '<div class="usage-table-wrap"><table class="usage-table">' in usage
    app = read_sheet(STATIC_DIR / "app.css")
    assert "minmax(min(100%, 280px), 1fr)" in rule(app, ".usage-grid")
    phone = all_media(app, PHONE_QUERY)
    assert "width: 100%" in rule(phone, ".usage-refresh-control")
    assert "min-width: 0" in rule(phone, ".usage-refresh-control > .btn")


def test_view_sessions_rwd():
    """Sesiones: filter grid fits down to 360 px, session items wrap timestamps on phone, and <pre> blocks scroll.

    Manual QA at 390 px and 768 px: open #sessions, expand filters, and open a session with tool results."""
    sessions = (JS / "sessions.js").read_text(encoding="utf-8")
    items = (JS / "session-items.js").read_text(encoding="utf-8")
    assert 'class="filter-grid"' in sessions and '<pre class="data"' in items
    app = read_sheet(STATIC_DIR / "app.css")
    assert "minmax(min(100%, 190px), 1fr)" in rule(app, ".filter-grid")
    assert "min-width: 0" in rule(app, ".filter-grid > div")
    assert "min-height: var(--target)" in rule(app, ".session-row")
    phone = all_media(app, PHONE_QUERY)
    assert "padding: 12px" in rule(phone, ".content")
    assert "flex-basis: 100%" in rule(phone, ".item-head .when")


def test_view_work_map_rwd():
    """Mapa de trabajo: toolbar, tree indentation, and list depth offsets adapt under the phone breakpoint.

    Manual QA at 390 px and 768 px: open #map and verify toolbar controls and nested items fit without horizontal scroll."""
    app = read_sheet(STATIC_DIR / "app.css")
    assert "min-width: 0" in rule(app, ".work-map")
    assert "min-width: 0" in rule(app, ".wm-node")
    phone = all_media(app, PHONE_QUERY)
    assert "flex-basis: 100%" in rule(phone, ".wm-toolbar > label:first-child")
    assert "margin-left: 6px" in rule(phone, ".wm-children")


def test_view_agent_tree_rwd():
    """Árbol de agentes: columns wrap with min-width: 0 and facts stack below the phone breakpoint.

    Manual QA at 390 px and 768 px: open #tree/<session> and select a node."""
    at_css = read_sheet(STATIC_DIR / "agent-tree.css")
    assert "flex-wrap: wrap" in rule(at_css, ".at-columns")
    assert "min-width: 0" in rule(at_css, ".at-main") and "min-width: 0" in rule(at_css, ".at-detail")
    phone = all_media(at_css, PHONE_QUERY)
    assert "grid-template-columns: minmax(0, 1fr)" in rule(phone, ".at-facts")


def test_extensions_project_and_decisions_rwd():
    """Extensiones (Proyecto y Decisiones: continuity, document_alerts, task_waiting): unified phone cut and shrinkable boxes.

    Manual QA at 390 px and 768 px: open #project, #continuity, #document_alerts, and #task_waiting."""
    project = read_sheet(EXT / "project" / "style.css")
    continuity = read_sheet(EXT / "continuity" / "style.css")
    alerts = read_sheet(EXT / "document_alerts" / "style.css")
    waiting = read_sheet(EXT / "task_waiting" / "style.css")

    assert "min-width: 0" in rule(project, ".project-view")
    assert "grid-template-columns: minmax(0, 1fr)" in rule(all_media(project, PHONE_QUERY), ".project-facts")

    assert "min-width: 0" in rule(continuity, ".continuity-view")
    assert "flex-direction: column" in rule(all_media(continuity, PHONE_QUERY), ".continuity-actions")

    assert "min-width: 0" in rule(alerts, ".document-alerts-view")
    assert "padding: 8px" in rule(all_media(alerts, PHONE_QUERY), ".document-alerts-item")

    assert "min-width: 0" in rule(waiting, ".task-waiting-view")
    assert "grid-template-columns: minmax(0, 1fr)" in rule(waiting, ".task-waiting-details")


def test_apps_key_names_break_at_underscores_without_changing_copied_text():
    """Apps y agentes: API setting names break only at underscores; manual QA covers 375, 390, and 768 px."""
    apps_css = read_sheet(EXT / "apps" / "style.css")
    apps_js = (EXT / "apps" / "index.js").read_text(encoding="utf-8")
    key_rule = rule(apps_css, ".apps-key-name")
    assert "word-break: normal" in key_rule and "overflow-wrap: normal" in key_rule
    assert 'return html`<span class="apps-key-name">${provider}_<wbr />API_<wbr />KEY</span>`' in apps_js
    assert "navigator.clipboard.writeText(text)" in apps_js
    assert "text=${`${action.setting}=`}" in apps_js


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_apps_key_setting_copy_value_is_exact():
    model = EXT / "apps" / "model.js"
    script = """
      import { buildAppsView } from %s;
      const { action } = buildAppsView({ connections: [{
        id: "gemini", kind: "model", key_configured: false, key_setting: "GEMINI_API_KEY",
        enabled: true, available: false, reason: "key_missing", missing_step: "key"
      }] }).models[0];
      console.log(JSON.stringify({ setting: action.setting, copied: `${action.setting}=` }));
    """ % json.dumps(model.as_uri())
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"setting": "GEMINI_API_KEY", "copied": "GEMINI_API_KEY="}
