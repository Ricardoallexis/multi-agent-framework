# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI2-T11: the shared Preact components (StatusChip, Progress, Tabs, LevelBadge), their single label map, and the
accessibility and mobile rules of the whole GUI.

Static checks over the client files and the stylesheet (markup, ARIA attributes, no inline style or HTML injection,
every view uses the components, colour contrast of the tones, focus, 44 px targets, narrow screens), the pure modules
and the components run with Node (skipped when Node is not installed: the components are called as functions and the
vnodes they return are inspected, with a fake keyboard event for the tabs), and the server check that the new folder is
served with the CSP and shipped in the wheel. The browser smoke test (desktop, dark, 375 px, keyboard on the tabs) is
manual and is reported with the task."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui import STATIC_DIR
from multiagent.gui.security import CSP
from multiagent.gui.token import ensure_token
from session_fixtures import make_services, make_stores
from test_gui_sessions_view import FORBIDDEN

JS = STATIC_DIR / "js"
UI = JS / "ui"
NODE = shutil.which("node")
UI_FILES = ["labels.js", "status-chip.js", "progress.js", "tabs.js", "level-badge.js", "index.js"]
COMPONENT_FILES = ["status-chip.js", "progress.js", "tabs.js", "level-badge.js"]
VIEWS = ["main.js", "runs.js", "run-detail.js", "sessions.js", "session-detail.js", "sidebar.js", "subagents-panel.js"]
TONES = ["success", "active", "info", "attention", "warning", "danger", "muted", "neutral"]


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def ui(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def sheet() -> str:
    """app.css without its comments."""
    return re.sub(r"/\*.*?\*/", "", (STATIC_DIR / "app.css").read_text(encoding="utf-8"), flags=re.S)


def rule(source: str, selector: str) -> str:
    """Body of the first rule whose selector list contains ``selector`` exactly."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", source):
        if selector in [part.strip() for part in match[1].split(",")]:
            return match[2]
    raise AssertionError(f"no rule for {selector}")


# --- the files ---------------------------------------------------------------------------------------------------

def test_the_ui_folder_has_the_components_and_the_single_label_map():
    for name in UI_FILES:
        assert (UI / name).is_file(), name
    index = ui("index.js")
    for name in ("StatusChip", "Progress", "Tabs", "LevelBadge", "registerLabelSet", "DEFAULT_LABELS"):
        assert name in index, name


@pytest.mark.parametrize("name", UI_FILES)
def test_ui_code_has_no_html_injection_eval_inline_handlers_inline_style_or_remote_code(name):
    text = ui(name)
    for pattern, label in FORBIDDEN:
        assert not re.search(pattern, text), f"{name}: {label}"
    assert "__html" not in text and "style=" not in text and "document." not in text   # no inline style under the CSP


@pytest.mark.parametrize("name", COMPONENT_FILES)
def test_every_component_starts_with_a_comment_block_that_documents_its_props(name):
    head = []
    for line in ui(name).splitlines()[2:]:
        if not line.startswith("//"):
            break
        head.append(line)
    text = "\n".join(head)
    assert len(head) >= 8, f"{name}: the API comment block is too short"
    assert "<${" in text, f"{name}: no usage example"
    for prop in re.findall(r"^//\s{3}(\w+)\s", text, re.M):                     # "//   prop   what it does"
        assert re.search(rf"\b{prop}\b", ui(name).split("export function")[-1]), f"{name}: documented prop {prop} is not read"


def test_components_import_only_the_vendored_library_and_their_siblings():
    for name in UI_FILES:
        imported = set(re.findall(r'from "([^"]+)"', ui(name)))
        for target in imported:
            assert target == "../../vendor/htm-preact-standalone.module.js" or re.fullmatch(r"\./[a-z-]+\.js", target), (name, target)
    # components never reach into the views
    assert not re.search(r'from "\.\./[a-z-]+\.js"', "".join(ui(name) for name in UI_FILES))


def test_the_ui_folder_ships_in_the_wheel_and_is_served_with_the_csp(tmp_path):
    data = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "static/js/ui/*.js" in data["tool"]["setuptools"]["package-data"]["multiagent.gui"]
    stores = make_stores(tmp_path)
    services = make_services(stores)
    ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        for name in UI_FILES:
            response = client.get(f"/gui/static/js/ui/{name}")
            assert response.status_code == 200 and "javascript" in response.headers["content-type"], name
            assert response.headers["content-security-policy"] == CSP
    services.runs.close()


# --- hand-rolled widgets are gone: the views use the components ---------------------------------------------------

def test_tabs_and_progress_bars_exist_only_in_their_components():
    for path in sorted(JS.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        for attribute in ('role="tablist"', 'role="tab"', 'role="tabpanel"', 'role="progressbar"', "aria-valuenow"):
            assert attribute not in text, f"{path.name} draws {attribute} by hand; use ui/tabs.js or ui/progress.js"


def test_every_use_of_tabs_passes_its_help_and_names_the_tab_list():
    uses = 0
    for path in sorted(JS.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<\$\{Tabs\}(.*?)>\s*\n", text, re.S):
            uses += 1
            tag = match[1]
            assert "helpFor(" in tag, f"{path.name}: <Tabs> without help"       # test_gui_help.py: every control has help
            assert re.search(r'\blabel="[^"]+"', tag) and re.search(r'\bid="[a-z]+"', tag), path.name
    assert uses >= 1


def test_the_views_draw_statuses_with_statuschip_and_not_by_hand():
    for name in VIEWS:
        text = read(name)
        for stale in ('"badge tone-"', '"badge step-"', "sub-dot", "sub-status", '"dot dot-"'):
            assert stale not in text, f"{name}: {stale}"
    assert '<${StatusChip} status=${run.status} labels="run" />' in read("runs.js")
    detail = read("run-detail.js")
    assert '<${StatusChip} status=${view.status} labels="run" />' in detail
    assert '<${StatusChip} status=${step.state} labels="step" />' in detail
    assert '<${StatusChip} status=${session.status} labels="session" />' in read("sessions.js")
    assert '<${StatusChip} status=${summary.status} labels="session" />' in read("session-detail.js")
    assert 'variant="dot"' in read("sidebar.js")
    panel = read("subagents-panel.js")
    assert 'variant="inline"' in panel and "<${LevelBadge} level=${child.level} />" in panel and "<${Tabs}" in panel


def test_the_run_view_shows_the_steps_as_a_progress_bar_with_its_words():
    detail = read("run-detail.js")
    assert re.search(r'<\$\{Progress\} label="[^"]+" value=\$\{[^}]+\}\s*max=\$\{[^}]+\} valueText=\$\{stepsProgressText', detail)
    assert "steps_total > 0" in detail                                              # no bar for a run with no steps


def test_the_status_words_have_one_home():
    # the text modules read the shared maps instead of keeping their own copies
    for name, setname in (("text.js", "run"), ("session-text.js", "session"), ("subagents-text.js", "subagent")):
        text = read(name)
        assert f'legacyInfo("{setname}"' in text and "./ui/labels.js" in text, name
        assert not re.search(r"^const STATUS = \{", text, re.M), name
    assert "STEP_STATES" not in read("text.js") and "DEFAULT_LABELS" in read("sidebar-model.js")


# --- the stylesheet ----------------------------------------------------------------------------------------------

def test_every_tone_has_a_colour_and_the_components_have_their_rules():
    css = sheet()
    for tone in TONES:
        assert f".tone-{tone}" in css, tone
        assert "--tone:" in rule(css, f".tone-{tone}")
    assert "color: var(--tone)" in rule(css, '.badge[class*="tone-"]')
    for selector in (".status-dot", ".status-inline", ".dot", ".progress-track", ".progress-fill", ".tab", ".tab-panel", ".level"):
        assert any(selector in part for part in re.findall(r"([^{}]+)\{", css)), selector
    for shape in ("disc", "ring", "square", "diamond"):
        assert f'.dot[data-shape="{shape}"]' in css and f'.mark[data-shape="{shape}"]' in css
    assert "aria-selected" in css and "box-shadow: inset 0 -3px 0 var(--accent)" in rule(css, '.tab[aria-selected="true"]')   # not only a colour
    assert "min-height: var(--target)" in rule(css, ".tab") and "min-width: var(--target)" in rule(css, ".tab")
    assert "style=" not in css and "@import" not in css and "http" not in css


def test_motion_stops_with_prefers_reduced_motion():
    css = sheet()
    for selector in (".dot.pulse, .mark.pulse", ".progress.indeterminate .progress-fill"):
        assert re.search(r"@media \(prefers-reduced-motion: reduce\) \{ " + re.escape(selector) + r" \{ animation: none; \} \}", css), selector
    assert "forced-colors: active" in css                                         # the bar and the badges survive high-contrast mode


def luminance(colour: str) -> float:
    colour = colour.lstrip("#")
    if len(colour) == 3:
        colour = "".join(char * 2 for char in colour)
    channels = [int(colour[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def variables(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,6})\b", block))


DARK_ROOT = re.compile(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{([^{}]*)\}\s*\}")


def themes() -> dict[str, dict[str, str]]:
    """Colour variables of the light theme (every top-level ``:root``) and of the dark one (light plus its overrides)."""
    css = sheet()
    light: dict[str, str] = {}
    for block in re.finditer(r":root \{([^{}]*)\}", DARK_ROOT.sub("", css)):
        light.update(variables(block[1]))
    dark = dict(light)
    for block in DARK_ROOT.findall(css):
        dark.update(variables(block))
    return {"light": light, "dark": dark}


def test_every_tone_colour_has_normal_text_contrast_on_the_page_panel_and_chrome():
    for theme, colours in themes().items():
        for name in ("ok", "warn", "bad", "accent", "muted", "text"):
            for background in ("bg", "panel", "chrome", "hover", "accent-soft", "warn-soft"):
                ratio = contrast(colours[name], colours[background])
                assert ratio >= 4.5, f"{theme}: --{name} on --{background} is {ratio:.2f}:1"


def test_level_badges_have_normal_text_contrast_and_four_distinct_fills():
    light = themes()["light"]
    fills = []
    for level in (1, 2, 3, 4):
        fill, ink = light[f"lvl-{level}-bg"], light[f"lvl-{level}-ink"]
        assert contrast(fill, ink) >= 4.5, f"T{level}: {contrast(fill, ink):.2f}:1"
        fills.append(fill)
    assert len(set(fills)) == 4
    # the badge has its own fill, so it reads the same in both themes; its border keeps the darkest one visible on a dark page
    assert not any("lvl-" in block for block in DARK_ROOT.findall(sheet()))
    assert "border: 1px solid var(--line-strong)" in rule(sheet(), ".level")


def test_focus_is_visible_everywhere_and_never_removed_from_keyboard_focus():
    css = sheet()
    focus = rule(css, ".btn:focus-visible")
    assert "outline: 2px solid var(--accent)" in focus and "outline-offset: 2px" in focus
    listed = [part.strip() for part in re.search(r"([^{}]+)\{" + re.escape(focus) + r"\}", css)[1].split(",")]
    for selector in ("button:focus-visible", "a:focus-visible", "input:focus-visible", "select:focus-visible",
                     "textarea:focus-visible", "summary:focus-visible", "[tabindex]:focus-visible"):
        assert selector in listed, selector
    # the tab panel has tabindex 0, so [tabindex]:focus-visible draws its ring too
    assert 'tabindex="0"' in ui("tabs.js")
    # an outline is removed only for programmatic (:focus) focus of a container, never for :focus-visible
    for match in re.finditer(r"([^{}]+)\{[^{}]*outline:\s*(?:none|0)\b[^{}]*\}", css):
        assert ":focus-visible" not in match[1], match[1]
        assert match[1].strip() in {".session-detail:focus"}, match[1]


def test_every_hit_area_is_44px_or_more():
    css = sheet()
    assert "--target: 44px" in css
    for selector in (".btn", ".tool-btn", ".tab", ".side-item", ".views button", ".skip-link", ".group > summary",
                     ".work-fold > details > summary", ".check-inline", ".check label", "details > summary", ".sub-more", ".sub-item"):
        body = rule(css + (STATIC_DIR / "subagents-panel.css").read_text(encoding="utf-8"), selector)
        assert re.search(r"min-height:\s*(var\(--target\)|44px)", body), selector
    assert "min-height: var(--target)" in rule(css, 'input:not([type="checkbox"])')
    assert re.search(r"width:\s*1\.25rem", rule(css, 'input[type="checkbox"]')) and "inset: -10px" in rule(css, ".help-toggle::after")


def test_narrow_screens_do_not_scroll_sideways():
    css = sheet()
    assert "overflow-x: clip" in rule(css, ".app")
    narrow = css[css.index("@media (max-width: 599.98px)"):]
    narrow = narrow[:narrow.index("\n}\n")]
    assert ".topbar .hint-text" in narrow and "display: none" in narrow            # an open "?" of the top bar stays on screen
    sub = re.sub(r"/\*.*?\*/", "", (STATIC_DIR / "subagents-panel.css").read_text(encoding="utf-8"), flags=re.S)
    for source, selector in ((css, ".progress-label"), (css, ".progress-text"), (css, ".group-name"), (css, ".session-title"),
                             (sub, ".sub-title"), (sub, ".sub-detail")):
        assert "overflow-wrap" in rule(source, selector), selector                  # long names and titles break instead of widening the page
    assert "flex-wrap: wrap" in rule(css, ".tabs-row") and "flex-wrap: wrap" in rule(css, ".tabs")
    assert "min-width: 0" in rule(css, ".progress")


# --- the pure modules and the components, run with Node ----------------------------------------------------------------

@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    """A copy of the client folder where ``.js`` is an ES module on every Node version (a package.json says so)."""
    root = tmp_path_factory.mktemp("static")
    shutil.copytree(STATIC_DIR / "js", root / "js")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    (root / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    return root


def run_node(root: Path, name: str, body: str):
    script = root / f"{name}.mjs"
    script.write_text(body, encoding="utf-8")
    run = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_label_entries_are_complete_tolerant_and_never_throw(node_root):
    out = run_node(node_root, "labels_entries", """
      import { labelEntry, normalizeTone, statusText, TONES, DEFAULT_LABELS } from "./js/ui/labels.js";
      const map = {
        done: { text: "Hecho", tone: "success", explanation: "Listo." },
        old: { label: "Viejo", tone: "warn", help: "Forma antigua." },
        text: "Solo texto",
        spin: { text: "Girando", tone: "run", pulse: true },
        odd: { text: "Raro", tone: "no-such-tone", shape: "triangle" },
        square: { text: "Cuadrado", tone: "success", shape: "square" },
      };
      const pick = (key) => labelEntry(map, key);
      console.log(JSON.stringify({
        done: pick("done"), old: pick("old"), text: pick("text"), spin: pick("spin"), odd: pick("odd"), square: pick("square"),
        missing: pick("zzz"), proto: pick("constructor"), noMap: labelEntry(null, "x"), numeric: labelEntry({ 3: "Tres" }, 3),
        tones: ["ok", "accent", "warn", "bad", "quiet", "run", "test", "success", "weird", undefined].map(normalizeTone),
        statusText: [statusText(pick("done")), statusText(pick("done"), "En Ejecución"), statusText(pick("done"), "")],
        allTones: TONES,
        defaults: Object.fromEntries(Object.entries(DEFAULT_LABELS).map(([set, entries]) => [set,
          Object.entries(entries).map(([key, entry]) => [key, entry.text, TONES.includes(entry.tone)])])),
      }));
    """)
    assert out["done"] == {"key": "done", "known": True, "text": "Hecho", "tone": "success", "explanation": "Listo.", "pulse": False, "shape": "disc"}
    assert out["old"]["text"] == "Viejo" and out["old"]["tone"] == "warning" and out["old"]["explanation"] == "Forma antigua."   # {label, help} still work
    assert out["text"]["text"] == "Solo texto" and out["text"]["tone"] == "neutral" and out["text"]["known"] is True
    assert out["spin"]["tone"] == "success" and out["spin"]["pulse"] is True            # old tone name "run" is an alias
    assert out["odd"]["tone"] == "neutral" and out["odd"]["shape"] == "ring"           # unknown tone and shape fall back, never throw
    assert out["square"]["shape"] == "square"
    assert out["missing"] == {"key": "zzz", "known": False, "text": "zzz", "tone": "neutral", "explanation": "", "pulse": False, "shape": "ring"}
    assert out["proto"]["known"] is False and out["proto"]["text"] == "constructor"    # a status named like an Object property is unknown
    assert out["noMap"]["text"] == "x" and out["numeric"]["text"] == "Tres"
    assert out["tones"] == ["success", "active", "warning", "danger", "muted", "success", "warning", "success", "neutral", "neutral"]
    assert out["statusText"] == ["Hecho", "Hecho (en ejecución)", "Hecho"]
    assert out["allTones"] == TONES
    for entries in out["defaults"].values():
        assert entries and all(text and ok for _, text, ok in entries)                 # every default entry has a text and a known tone


@needs_node
def test_the_default_sets_keep_the_words_of_the_maps_they_replaced(node_root):
    out = run_node(node_root, "labels_parity", """
      import { statusInfo, stepStateLabel } from "./js/text.js";
      import { statusInfo as sessionStatus } from "./js/session-text.js";
      import { statusInfo as childStatus, childLabels } from "./js/subagents-text.js";
      import { DOTS, dotText } from "./js/sidebar-model.js";
      const runs = ["queued", "running", "waiting_human", "paused", "completed", "failed", "cancelled", "rejected", "interrupted", "weird"];
      console.log(JSON.stringify({
        runs: runs.map((s) => statusInfo(s)),
        steps: ["done", "skipped", "will_skip", "waiting_review", "waiting_human", "running", "queued", "pending", "failed", "not_run", "weird"].map(stepStateLabel),
        sessions: ["active", "idle", "archived", "unknown", "weird"].map((s) => sessionStatus(s)),
        children: ["running", "testing", "idle", "done", "stopped", "unknown", "weird"].map((s) => childStatus(s)),
        note: childStatus("running", "waiting_human").label,
        dots: DOTS, dotText: [dotText("working", "En ejecución"), dotText("error"), dotText("finished", "Sin actividad")],
      }));
    """)
    labels = [(info["label"], info["tone"]) for info in out["runs"]]
    assert labels == [("En cola", "muted"), ("En ejecución", "active"), ("Esperando a una persona", "warning"), ("Pausada", "warning"),
                      ("Completada", "success"), ("Falló", "danger"), ("Cancelada", "muted"), ("Rechazada", "muted"),
                      ("Interrumpida", "warning"), ("weird", "neutral")]
    assert out["runs"][2]["help"] == "Necesita una decisión o una respuesta tuya." and out["runs"][9]["help"] == ""
    assert out["steps"] == ["Hecho", "Saltado", "Se saltará", "Espera tu revisión", "Espera tu respuesta", "En curso", "En cola",
                            "Pendiente", "Falló", "No llegó a ejecutarse", "weird"]
    assert [(i["label"], i["tone"]) for i in out["sessions"]] == [("Activa", "success"), ("Sin actividad", "muted"), ("Archivada", "muted"),
                                                                  ("Estado desconocido", "muted"), ("weird", "neutral")]
    assert [i["label"] for i in out["children"]] == ["Trabajando", "Ejecutando prueba", "Sin actividad", "Terminado", "Detenido",
                                                     "Estado desconocido", "weird"]
    assert out["children"][1]["help"].startswith("Tuvo actividad hace menos de 15 minutos y su última herramienta es una prueba")
    assert out["note"] == "Esperando a una persona"
    assert out["dots"] == {"working": "Trabajando", "finished": "Terminada", "error": "Con error"}
    assert out["dotText"] == ["Trabajando (en ejecución)", "Con error", "Terminada (sin actividad)"]


@needs_node
def test_a_label_map_can_be_swapped_by_name_like_the_one_of_pry_t02(node_root):
    out = run_node(node_root, "labels_registry", """
      import { labelSet, labelEntry, registerLabelSet, resetLabelSets, resolveLabels, DEFAULT_LABELS } from "./js/ui/labels.js";
      import { StatusChip } from "./js/ui/status-chip.js";
      // the shape of multiagent/tasks/labels.json: text, tone and explanation under "task" and "stage"
      const pry = { schema: "maf.task-labels/1", task: {
        review: { text: "Espera tu revisión", tone: "attention", explanation: "Falta que la apruebes." },
        done: { text: "Terminada", tone: "success", explanation: "Aceptada." },
        blocked: { text: "Bloqueada", tone: "warning", explanation: "Espera a otra tarea." },
        ready: { text: "Lista", tone: "info", explanation: "Ya se puede tomar." } } };
      const before = labelSet("task");
      registerLabelSet("task", pry.task);
      const chip = (status, extra = {}) => StatusChip({ status, labels: "task", ...extra });
      const result = {
        before, after: Object.keys(labelSet("task")), entry: labelEntry(labelSet("task"), "review"),
        chip: chip("review"), byMap: StatusChip({ status: "done", labels: pry.task }), typo: StatusChip({ status: "done", labels: "no-such-set" }),
        resolved: [resolveLabels("task") === pry.task, resolveLabels(pry.task) === pry.task, resolveLabels(undefined)],
      };
      // replacing a default set swaps it for every view that reads it by name
      registerLabelSet("run", { running: { text: "Corriendo", tone: "active" } });
      result.swapped = StatusChip({ status: "running", labels: "run" });
      resetLabelSets();
      result.reset = [Object.keys(labelSet("run")).length === Object.keys(DEFAULT_LABELS.run).length, Object.keys(labelSet("task")).length];
      let error = null; try { registerLabelSet("", {}); } catch (e) { error = e.name; }
      result.error = error;
      const text = (v) => (v == null || typeof v === "boolean" ? "" : Array.isArray(v) ? v.map(text).join("") : typeof v === "object" ? text(v.props.children) : String(v));
      const view = (v) => ({ type: v.type, cls: v.props.class, text: text(v), title: v.props.title });
      console.log(JSON.stringify({ ...result, chip: view(result.chip), byMap: view(result.byMap), typo: view(result.typo), swapped: view(result.swapped) }));
    """)
    assert out["before"] == {} and out["after"] == ["review", "done", "blocked", "ready"]
    assert out["entry"]["tone"] == "attention" and out["entry"]["text"] == "Espera tu revisión"
    assert out["chip"] == {"type": "span", "cls": "badge status-chip tone-attention", "text": "Espera tu revisión", "title": "Falta que la apruebes."}
    assert out["byMap"]["text"] == "Terminada" and out["byMap"]["cls"].endswith("tone-success")
    assert out["typo"]["text"] == "done" and out["typo"]["cls"].endswith("tone-neutral")     # a wrong set name shows the raw status
    assert out["resolved"] == [True, True, {}]
    assert out["swapped"]["text"] == "Corriendo"
    assert out["reset"] == [True, 0] and out["error"] == "TypeError"


@needs_node
def test_progress_numbers_are_clamped_and_say_their_words(node_root):
    out = run_node(node_root, "progress_info", """
      import { progressInfo } from "./js/ui/progress.js";
      const cases = [
        { value: 3, max: 5 }, { value: 63 }, { value: 0, max: 0 }, { value: -4, max: 10 }, { value: 99, max: 10 }, { value: null },
        { value: undefined, max: 5 }, { value: NaN, max: 5 }, { value: "3", max: 5 }, { value: 5, max: 3 }, { value: 7, min: 5, max: 9 },
        { value: 1, max: 4, valueText: "1 paso de 4" }, { value: Infinity, max: 4 }, { value: 2.5, max: 5 }, {},
      ];
      console.log(JSON.stringify(cases.map((c) => progressInfo(c))));
    """)
    keys = ("determinate", "min", "max", "now", "percent", "valueText")
    got = [tuple(entry[key] for key in keys) for entry in out]
    assert got == [
        (True, 0, 5, 3, 60, "3 de 5"), (True, 0, 100, 63, 63, "63 %"),
        (False, 0, 0, None, None, "En curso"),                        # max <= min: nothing to measure
        (True, 0, 10, 0, 0, "0 de 10"), (True, 0, 10, 10, 100, "10 de 10"),      # clamped into the range
        (False, 0, 100, None, None, "En curso"), (False, 0, 5, None, None, "En curso"), (False, 0, 5, None, None, "En curso"),
        (False, 0, 5, None, None, "En curso"),                        # a string is not a number: indeterminate, never a made-up 0
        (True, 0, 3, 3, 100, "3 de 3"),                               # 5 of 3 is clamped to a full bar
        (True, 5, 9, 7, 50, "7 de 9"), (True, 0, 4, 1, 25, "1 paso de 4"), (False, 0, 4, None, None, "En curso"),
        (True, 0, 5, 2.5, 50, "2.5 de 5"), (False, 0, 100, None, None, "En curso"),
    ]


@needs_node
def test_tab_keys_move_wrap_and_ignore_the_rest(node_root):
    out = run_node(node_root, "tab_keys", """
      import { nextTabIndex, tabText, tabDomIds } from "./js/ui/tabs.js";
      const keys = ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End", "Tab", "Enter", " ", "a"];
      const row = (current, count, options) => keys.map((key) => nextTabIndex(key, current, count, options));
      console.log(JSON.stringify({
        horizontal: [row(0, 3), row(1, 3), row(2, 3)], vertical: [row(0, 3, { orientation: "vertical" }), row(2, 3, { orientation: "vertical" })],
        one: row(0, 1), none: row(0, 0), text: [tabText({ label: "Activos", count: 3 }), tabText({ label: "Todos" }), tabText({ label: "Cero", count: 0 }), tabText({ label: "Raro", count: "2" })],
        ids: [tabDomIds("sub", "done"), tabDomIds("sub", "done", "lista")],
      }));
    """)
    #            Right Left Down  Up  Home End Tab Enter Space a
    assert out["horizontal"] == [[1, 2, -1, -1, 0, 2, -1, -1, -1, -1],
                                 [2, 0, -1, -1, 0, 2, -1, -1, -1, -1],
                                 [0, 1, -1, -1, 0, 2, -1, -1, -1, -1]]           # wraps at both ends; Home and End jump
    assert out["vertical"] == [[-1, -1, 1, 2, 0, 2, -1, -1, -1, -1], [-1, -1, 0, 1, 0, 2, -1, -1, -1, -1]]
    assert out["one"] == [0, 0, -1, -1, 0, 0, -1, -1, -1, -1] and out["none"] == [-1] * 10
    assert out["text"] == ["Activos · 3", "Todos", "Cero · 0", "Raro"]            # a count of 0 is shown; a non-number is not
    assert out["ids"] == [{"tab": "sub-tab-done", "panel": "sub-panel"}, {"tab": "sub-tab-done", "panel": "lista"}]


@needs_node
def test_level_info_accepts_t1_to_t4_only(node_root):
    out = run_node(node_root, "level_info", """
      import { levelInfo, LEVELS } from "./js/ui/level-badge.js";
      console.log(JSON.stringify({
        ok: ["T1", "t2", " T3 ", "4", 4, "T4"].map(levelInfo),
        bad: ["T0", "T5", "", null, undefined, "T12", "alto", {}, "T1T2"].map(levelInfo),
        levels: LEVELS,
      }));
    """)
    assert [(i["id"], i["rank"], i["name"]) for i in out["ok"]] == [("T1", 1, "económico"), ("T2", 2, "estándar"), ("T3", 3, "avanzado"),
                                                                   ("T4", 4, "máximo"), ("T4", 4, "máximo"), ("T4", 4, "máximo")]
    assert out["ok"][1]["label"] == "Nivel T2, estándar"
    assert out["bad"] == [None] * 9
    assert list(out["levels"]) == ["T1", "T2", "T3", "T4"]


# The vnodes the components return are plain objects: a tiny walker reads their markup without a browser.
WALKER = """
const kids = (v) => (v == null || typeof v === "boolean" ? [] : Array.isArray(v) ? v.flatMap(kids) : typeof v === "object" ? [v] : []);
const all = (v) => kids(v).flatMap((n) => [n, ...all(n.props.children)]);
const text = (v) => (v == null || typeof v === "boolean" ? "" : Array.isArray(v) ? v.map(text).join("") : typeof v === "object" ? text(v.props.children) : String(v));
const withAttr = (v, name, value) => all(v).filter((n) => n.props[name] === value);
const attrs = (n, ...names) => Object.fromEntries(names.map((name) => [name, n.props[name] === undefined ? null : n.props[name]]));
"""


@needs_node
def test_tabs_markup_follows_the_aria_pattern_with_a_roving_tabindex_and_the_arrow_keys(node_root):
    out = run_node(node_root, "tabs_component", WALKER + """
      import { Tabs } from "./js/ui/tabs.js";
      const selected = [];
      const tabs = [{ id: "a", label: "Activos", count: 3 }, { id: "b", label: "Terminados", count: 0 }, { id: "c", label: "Todos" }];
      const make = (props = {}) => Tabs({ id: "t", label: "Estado", tabs, selected: "b", onSelect: (id) => selected.push(id),
        children: "contenido", "aria-describedby": "help-x-hint", panelProps: { "aria-busy": "false" }, ...props });
      const view = make();
      const list = withAttr(view, "role", "tablist")[0];
      const buttons = withAttr(view, "role", "tab");
      const panel = withAttr(view, "role", "tabpanel")[0];

      const press = (key, from, extra = {}, options = {}) => {
        const focused = [];
        const nodes = buttons.map((_, i) => ({ focus: () => focused.push(i) }));
        let prevented = false;
        const event = { key, ctrlKey: false, altKey: false, metaKey: false, shiftKey: false, isComposing: false, ...extra,
          currentTarget: { querySelectorAll: () => nodes }, target: from >= 0 ? nodes[from] : {}, preventDefault: () => { prevented = true; } };
        selected.length = 0;
        (options.view ? withAttr(options.view, "role", "tablist")[0] : list).props.onKeyDown(event);
        return { selected: [...selected], focused, prevented };
      };
      const vertical = make({ orientation: "vertical" });
      const none = make({ selected: "nope" });
      buttons[0].props.onClick(); const clicked = [...selected];
      console.log(JSON.stringify({
        list: attrs(list, "aria-label", "aria-orientation"), verticalList: attrs(withAttr(vertical, "role", "tablist")[0], "aria-orientation"),
        buttons: buttons.map((b) => ({ ...attrs(b, "id", "aria-selected", "aria-controls", "tabindex", "type", "aria-describedby"), text: text(b) })),
        panel: attrs(panel, "id", "aria-labelledby", "tabindex", "aria-busy"), panelText: text(panel),
        noneSelected: withAttr(none, "role", "tab").map((b) => b.props.tabindex), nonePanel: attrs(withAttr(none, "role", "tabpanel")[0], "aria-labelledby"),
        right: press("ArrowRight", 1), wrap: press("ArrowRight", 2), left: press("ArrowLeft", 0), home: press("Home", 2), end: press("End", 0),
        down: press("ArrowDown", 0), tab: press("Tab", 0), ctrl: press("ArrowRight", 0, { ctrlKey: true }), shift: press("ArrowRight", 0, { shiftKey: true }),
        composing: press("ArrowRight", 0, { isComposing: true }), outside: press("ArrowRight", -1),
        verticalDown: press("ArrowDown", 0, {}, { view: vertical }), verticalRight: press("ArrowRight", 0, {}, { view: vertical }),
        clicked, after: text(make({ after: "AYUDA" })).includes("AYUDA"),
      }));
    """)
    assert out["list"] == {"aria-label": "Estado", "aria-orientation": None} and out["verticalList"] == {"aria-orientation": "vertical"}
    assert [b["id"] for b in out["buttons"]] == ["t-tab-a", "t-tab-b", "t-tab-c"]
    assert [b["aria-selected"] for b in out["buttons"]] == ["false", "true", "false"]
    assert [b["tabindex"] for b in out["buttons"]] == [-1, 0, -1]                  # roving tabindex: one tab stop
    assert {b["aria-controls"] for b in out["buttons"]} == {"t-panel"} and {b["type"] for b in out["buttons"]} == {"button"}
    assert {b["aria-describedby"] for b in out["buttons"]} == {"help-x-hint"}       # the help of the view reaches every tab
    assert [b["text"] for b in out["buttons"]] == ["Activos · 3", "Terminados · 0", "Todos"]
    assert out["panel"] == {"id": "t-panel", "aria-labelledby": "t-tab-b", "tabindex": "0", "aria-busy": "false"} and out["panelText"] == "contenido"
    assert out["noneSelected"] == [0, -1, -1] and out["nonePanel"] == {"aria-labelledby": "t-tab-a"}   # no valid selection: the first tab is the stop
    assert out["right"] == {"selected": ["c"], "focused": [2], "prevented": True}                        # selects and focuses
    assert out["wrap"] == {"selected": ["a"], "focused": [0], "prevented": True}
    assert out["left"] == {"selected": ["c"], "focused": [2], "prevented": True}
    assert out["home"] == {"selected": ["a"], "focused": [0], "prevented": True}
    assert out["end"] == {"selected": ["c"], "focused": [2], "prevented": True}
    ignored = {"selected": [], "focused": [], "prevented": False}
    for key in ("down", "tab", "ctrl", "shift", "composing", "outside", "verticalRight"):
        assert out[key] == ignored, key                                            # other keys, modified keys and clicks outside a tab are left alone
    assert out["verticalDown"] == {"selected": ["b"], "focused": [1], "prevented": True}
    assert out["clicked"] == ["a"] and out["after"] is True


@needs_node
def test_statuschip_says_its_status_in_words_and_marks_are_decoration(node_root):
    out = run_node(node_root, "status_chip", WALKER + """
      import { StatusChip } from "./js/ui/status-chip.js";
      const shape = (v) => ({ type: v.type, cls: v.props.class, title: v.props.title === undefined ? null : v.props.title, status: v.props["data-status"], text: text(v),
        hidden: all(v).filter((n) => n.props["aria-hidden"] === "true").map((n) => n.props.class || n.type),
        sr: all(v).filter((n) => n.props.class === "sr-only").map(text), marks: all(v).filter((n) => n.props["data-shape"]).map((n) => n.props["data-shape"] + ":" + n.props.class) });
      console.log(JSON.stringify({
        chip: shape(StatusChip({ status: "running" })),
        chipTone: ["completed", "failed", "waiting_human", "queued"].map((s) => StatusChip({ status: s, labels: "run" }).props.class),
        dot: shape(StatusChip({ status: "working", labels: "dot", variant: "dot", detail: "En ejecución" })),
        dots: ["working", "finished", "error"].map((s) => shape(StatusChip({ status: s, labels: "dot", variant: "dot" })).marks[0]),
        inline: shape(StatusChip({ status: "running", labels: "subagent", variant: "inline" })),
        inlineQuiet: shape(StatusChip({ status: "done", labels: "subagent", variant: "inline" })),
        unknown: shape(StatusChip({ status: "mystery" })),
        extra: (({ role, "aria-live": live, class: cls }) => ({ role, live, cls }))(StatusChip({ status: "idle", labels: "session", className: "mine", role: "status", "aria-live": "polite" }).props),
      }));
    """)
    assert out["chip"] == {"type": "span", "cls": "badge status-chip tone-active", "title": "El framework está ejecutando un paso.",
                           "status": "running", "text": "En ejecución", "hidden": [], "sr": [], "marks": []}
    assert out["chipTone"] == ["badge status-chip tone-success", "badge status-chip tone-danger", "badge status-chip tone-warning",
                               "badge status-chip tone-muted"]
    dot = out["dot"]
    assert dot["cls"] == "status-dot tone-success" and dot["title"] == "Trabajando (en ejecución)"
    assert dot["sr"] == ["Estado: Trabajando (en ejecución)"]                       # the words are for screen readers
    assert dot["hidden"] == ["dot pulse"] and dot["marks"] == ["disc:dot pulse"]    # the dot itself is decoration
    assert out["dots"] == ["disc:dot pulse", "ring:dot", "square:dot"]              # the shape differs per state, not only the colour
    assert out["inline"]["cls"] == "status-inline tone-success" and out["inline"]["text"] == "Trabajando"
    assert out["inline"]["hidden"] == ["mark pulse"] and out["inline"]["marks"] == ["disc:mark pulse"]
    assert out["inlineQuiet"]["marks"] == ["ring:mark"] and out["inlineQuiet"]["cls"].endswith("tone-muted")
    assert out["unknown"]["text"] == "mystery" and out["unknown"]["cls"] == "badge status-chip tone-neutral" and out["unknown"]["title"] is None
    assert out["extra"]["role"] == "status" and out["extra"]["live"] == "polite" and out["extra"]["cls"].endswith(" mine")   # attributes reach the element


@needs_node
def test_progress_markup_is_a_progressbar_with_values_text_and_no_inline_style(node_root):
    out = run_node(node_root, "progress_component", WALKER + """
      import { Progress } from "./js/ui/progress.js";
      const bar = (props) => { const v = Progress(props); const b = withAttr(v, "role", "progressbar")[0];
        return { cls: v.props.class, bar: b ? attrs(b, "aria-label", "aria-valuemin", "aria-valuemax", "aria-valuenow", "aria-valuetext", "class") : null,
          rect: all(v).filter((n) => n.type === "rect").map((n) => n.props.width), head: text(withAttr(v, "class", "progress-head")[0]),
          style: all(v).some((n) => "style" in n.props), headHidden: withAttr(v, "class", "progress-head")[0].props["aria-hidden"],
          labelClass: all(v).map((n) => n.props.class).filter((c) => String(c || "").startsWith("progress-label"))[0] || null }; };
      console.log(JSON.stringify({
        some: bar({ label: "Pasos hechos", value: 3, max: 5 }), custom: bar({ label: "Contexto", value: 63, tone: "warning", valueText: "63 % del contexto" }),
        none: bar({ label: "Cargando" }), hidden: bar({ label: "Oculta", value: 1, max: 2, hideLabel: true }), full: bar({ label: "Lleno", value: 9, max: 5 }),
        badTone: bar({ label: "x", value: 1, max: 2, tone: "nope" }).cls,
      }));
    """)
    assert out["some"]["bar"] == {"aria-label": "Pasos hechos", "aria-valuemin": 0, "aria-valuemax": 5, "aria-valuenow": 3,
                                  "aria-valuetext": "3 de 5", "class": "progress-track"}
    assert out["some"]["cls"] == "progress tone-active" and out["some"]["rect"] == [60] and out["some"]["head"] == "Pasos hechos3 de 5"
    assert out["custom"]["cls"] == "progress tone-warning" and out["custom"]["bar"]["aria-valuetext"] == "63 % del contexto"
    assert out["none"]["bar"]["aria-valuenow"] is None and out["none"]["bar"]["aria-valuetext"] == "En curso"   # indeterminate: no value claimed
    assert out["none"]["cls"] == "progress tone-active indeterminate" and out["none"]["rect"] == [100]
    assert out["hidden"]["labelClass"] == "progress-label sr-only"
    assert out["full"]["rect"] == [100] and out["full"]["bar"]["aria-valuenow"] == 5
    assert out["badTone"] == "progress tone-neutral"
    for key in ("some", "custom", "none", "hidden", "full"):
        assert out[key]["style"] is False and out[key]["headHidden"] == "true"        # the words live in aria-label and aria-valuetext


@needs_node
def test_levelbadge_has_text_marks_and_a_full_name_for_screen_readers(node_root):
    out = run_node(node_root, "level_badge", WALKER + """
      import { LevelBadge } from "./js/ui/level-badge.js";
      const shape = (v) => v === null ? null : ({ cls: v.props.class, level: v.props["data-level"], title: v.props.title,
        visible: text(withAttr(v, "class", "level-text")[0]), sr: text(withAttr(v, "class", "sr-only")[0]),
        pips: all(withAttr(v, "class", "level-pips")[0].props.children).map((n) => n.props.class), hidden: all(v).filter((n) => n.props["aria-hidden"] === "true").map((n) => n.props.class) });
      console.log(JSON.stringify({ t2: shape(LevelBadge({ level: "T2" })), t4: shape(LevelBadge({ level: "t4", showName: true, className: "mine" })),
        none: [LevelBadge({ level: null }), LevelBadge({ level: "T9" }), LevelBadge({})] }));
    """)
    t2 = out["t2"]
    assert t2["cls"] == "level level-2" and t2["level"] == "T2" and t2["title"] == "Nivel T2, estándar"
    assert t2["visible"] == "T2" and t2["sr"] == "Nivel T2, estándar"               # text, not only colour
    assert t2["pips"] == ["on", "on", "", ""]                                       # and as many filled marks as the level
    assert t2["hidden"] == ["level-pips", "level-text"]                              # the sighted-only parts are hidden from screen readers
    assert out["t4"]["cls"] == "level level-4 mine" and out["t4"]["visible"] == "T4 máximo" and out["t4"]["pips"] == ["on"] * 4
    assert out["none"] == [None, None, None]                                         # an unclassified model draws nothing


@needs_node
def test_run_steps_line_never_repeats_or_contradicts_the_status(node_root):
    """GUI test 2026-11-20: a completed run said «Todos los pasos terminaron.» twice, or beside «La ejecución terminó
    antes de recorrer todos los pasos.» when a step was skipped and the stored cursor lagged."""
    out = run_node(node_root, "cursor_text", """
      import { cursorText, statusInfo } from "./js/text.js";
      const view = (status, terminal, index, total) => ({ definition_available: true, status, terminal,
        cursor: { index, total, finished: total > 0 && index >= total } });
      console.log(JSON.stringify({
        help: statusInfo("completed").help,
        completedSkipped: cursorText(view("completed", true, 3, 4)),
        completedFinished: cursorText(view("completed", true, 4, 4)),
        rejected: cursorText(view("rejected", true, 1, 4)),
        running: cursorText(view("running", false, 1, 4)),
      }));
    """)
    assert out["help"] == "Todos los pasos terminaron."
    assert out["completedSkipped"] == "" and out["completedFinished"] == ""   # the summary above the card already says it
    assert out["rejected"] == "La ejecución terminó antes de recorrer todos los pasos."
    assert out["running"] == "Va por el paso 2 de 4."
    assert "${line && html`<p class=\"muted\">${line}</p>`}" in read("run-detail.js")   # an empty line draws nothing


@needs_node
def test_history_lines_without_a_step_read_as_sentences(node_root):
    """GUI test 2026-11-20: an old run's approval without a step read «Se aprobó el resultado del paso .»."""
    out = run_node(node_root, "history_text", """
      import { historyText } from "./js/text.js";
      const kinds = ["prompt_delivered", "response_submitted", "response_rejected", "approved", "changes_requested", "regenerated"];
      console.log(JSON.stringify({
        without: kinds.map((kind) => historyText({ kind })),
        with: kinds.map((kind) => historyText({ kind, step_id: "create", attempt: 2 })),
      }));
    """)
    for line in out["without"]:
        assert "  " not in line and " ." not in line and "«" not in line
    assert out["without"][3] == "Se aprobó el resultado."
    assert out["with"][3] == "Se aprobó el resultado del paso «create» (intento 2)."
    assert all("«create» (intento 2)" in line for line in out["with"])


def test_changing_screen_starts_at_the_top():
    """GUI test 2026-11-20: «Apps y agentes» opened 288 px down because the page kept the scroll of the previous screen."""
    assert "useEffect(() => { window.scrollTo(0, 0); }, [section]);" in read("main.js")
