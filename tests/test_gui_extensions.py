# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI extension points (FLU-T04): a view, a right-panel slot or a help text is added by a folder, not by editing shared files.

Four things are checked: the server finds the folders of ``static/extensions/`` and serves them under the strict CSP; the
client registries and the loader behave (Node, skipped when Node is not installed); the real extension folders, whatever
they are today, follow the hygiene rules (code, stylesheet, help: the help rules live in tests/test_gui_help.py); and two
example views that live only in a temporary copy of the static folder load, route, show a panel slot and pass every guard
without any shared file changing. A guard that never fails proves nothing, so the broken variants are checked too."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import multiagent.gui as gui_package
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.gui import STATIC_DIR
from multiagent.gui.extensions import discover_extensions, extension_dirs
from multiagent.gui.security import CSP
from multiagent.gui.token import ensure_token
from test_gui_help import NODE, check_extensions, extension_help
from test_gui_sessions_view import FORBIDDEN

JS = STATIC_DIR / "js"
ROOT = Path(__file__).resolve().parents[1]
# The files every GUI task used to edit; a view added through an extension folder changes none of them.
SHARED = ["index.html", "app.css", "js/main.js", "js/route.js", "js/help.js", "js/help-text.js", "js/panel-slots.js",
          "js/right-panel.js", "js/extensions.js"]


# --- two example views, written only into a temporary copy of the static folder ---------------------------------------

VIEW_JS = """// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { registerHelp, registerSlot, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { HELP } from "./help.js";

registerHelp(HELP, "@NAME@");

function View({ token, route }) {
  return html`<section class="@CSS@-view" aria-labelledby="@CSS@-title">
    <h3 id="@CSS@-title">@LABEL@</h3><${Help} topic="@NAME@.view" />
    <button type="button" class="@CSS@-action" ...${helpFor("@NAME@.action")}>Actuar</button>
    <${Help} topic="@NAME@.action" />
    <p class="@CSS@-id">${route.id}</p>
  </section>`;
}

function Card({ sessionId }) {
  return html`<p class="@CSS@-card">${sessionId || "Sin sesión"}</p>`;
}

registerView({ id: "@NAME@", owner: "@NAME@", label: "@LABEL@", component: View, order: 60 });
registerSlot("@NAME@", Card, { title: "@LABEL@", topic: "@NAME@.card", owner: "@NAME@", order: 20 });
"""


def view_files(name: str, label: str, *, slot: bool) -> dict[str, str]:
    """index.js, help.js and style.css of an example view called ``name`` (lowercase and ``_``), the way a task writes them."""
    css = name.replace("_", "-")
    index = VIEW_JS.replace("@CSS@", css).replace("@NAME@", name).replace("@LABEL@", label)
    entries = {
        f"{name}.view": (label, f"Muestra la pantalla {label}.", ["Es una pantalla de ejemplo.", "No hace nada más."]),
        f"{name}.action": ("Actuar", "Ejecuta la acción de ejemplo.", ["Solo existe en la prueba.", "No cambia ningún dato."]),
        f"{name}.card": ("Tarjeta", "Resume la sesión abierta.", ["Aparece en el panel derecho.", "Cada tarjeta es una sección."]),
    }
    if not slot:       # a view without a panel slot: no Card, no registerSlot, and no help text for it
        index = re.sub(r"function Card\(.*?\n}\n\n", "", index, flags=re.S)
        index = re.sub(r"registerSlot\(.*?\);\n", "", index).replace("registerSlot, ", "")
        del entries[f"{name}.card"]
    body = ",\n".join(
        f'  "{key}": {{\n    "title": {json.dumps(title, ensure_ascii=False)},\n    "hint": {json.dumps(hint, ensure_ascii=False)},\n'
        f'    "more": {json.dumps(more, ensure_ascii=False)}\n  }}' for key, (title, hint, more) in entries.items())
    help_js = ("// Copyright 2026 Ricardoallexis and contributors\n// SPDX-License-Identifier: Apache-2.0\n\n"
               f"export const HELP = {{\n{body}\n}};\n")
    style = (f"/* styles of {name} */\n.{css}-view {{ display: grid; gap: var(--gap, 0.5rem); }}\n"
             f".{css}-action:hover {{ color: var(--accent, inherit); }}\n.{css}-card, .{css}-id {{ margin: 0; }}\n"
             f"@media (max-width: 600px) {{ .{css}-view {{ gap: 0.25rem; }} }}\n")
    return {"index.js": index, "help.js": help_js, "style.css": style}


def write_view(static_dir: Path, name: str, label: str, *, slot: bool = False, **changes: str) -> Path:
    folder = static_dir / "extensions" / name
    folder.mkdir(parents=True, exist_ok=True)
    files = {**view_files(name, label, slot=slot), **changes}
    for filename, text in files.items():
        if text is not None:
            (folder / filename).write_text(text, encoding="utf-8")
    return folder


@pytest.fixture
def static_copy(tmp_path):
    """A copy of the static folder (without any real extension) holding two example views and nothing else new."""
    target = tmp_path / "static"
    shutil.copytree(STATIC_DIR, target, ignore=shutil.ignore_patterns("extensions", "__pycache__"))
    write_view(target, "alpha_view", "Alfa")
    write_view(target, "beta_view", "Beta", slot=True)
    return target


def content_hashes(folder: Path, outside: str | None = None) -> dict[str, str]:
    """relative path -> SHA-256 of every file of the static folder, leaving out the folder called ``outside``."""
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and outside not in path.relative_to(folder).parts}


# --- the server: folders found, manifest, files served under the CSP ----------------------------------------------------

def test_discovery_finds_valid_folders_in_name_order_and_ignores_the_rest(tmp_path):
    root = tmp_path / "extensions"
    for name in ("zeta", "alpha_view", "Bad", "1x", "with-dash", "no_entry", "__pycache__"):
        (root / name).mkdir(parents=True)
    for name in ("zeta", "alpha_view", "Bad", "1x", "with-dash", "__pycache__"):
        (root / name / "index.js").write_text("// entry", encoding="utf-8")
    (root / "alpha_view" / "style.css").write_text("/* css */", encoding="utf-8")
    (root / "loose.js").write_text("// not in a folder", encoding="utf-8")
    assert [folder.name for folder in extension_dirs(tmp_path)] == ["alpha_view", "zeta"]
    assert discover_extensions(tmp_path) == [
        {"id": "alpha_view", "script": "/gui/static/extensions/alpha_view/index.js", "style": "/gui/static/extensions/alpha_view/style.css"},
        {"id": "zeta", "script": "/gui/static/extensions/zeta/index.js", "style": None},
    ]
    assert discover_extensions(tmp_path / "missing") == []       # no extensions folder at all is normal


@pytest.fixture
def client_for(tmp_path, monkeypatch):
    """A GUI app serving the given static folder (the module reads STATIC_DIR when the app is built)."""
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gui_token_file=tmp_path / "gui-token", gui_heartbeat_seconds=0.05, gui_poll_seconds=0.02,
                        gui_stream_max_seconds=0.4)
    database = Database(settings)
    database.migrate()
    token = ensure_token(settings.gui_token_file)
    services = SimpleNamespace(system=SimpleNamespace(settings=settings, worker=Mock(), store=Store(database)),
                               runs=Mock(), definitions=Mock())
    clients = []

    def build(static_dir: Path):
        monkeypatch.setattr(gui_package, "STATIC_DIR", static_dir)
        client = TestClient(create_app(services=services), base_url="http://localhost")
        client.__enter__()
        clients.append(client)
        return client, {"Authorization": f"Bearer {token}"}

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def test_manifest_route_needs_the_token_and_lists_the_folders(static_copy, client_for):
    client, auth = client_for(static_copy)
    assert client.get("/gui/api/extensions").status_code == 401
    assert client.get("/gui/api/extensions", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/gui/api/extensions", headers=auth)
    assert response.status_code == 200 and response.headers["content-security-policy"] == CSP
    assert response.json() == {"extensions": discover_extensions(static_copy)}
    assert [item["id"] for item in response.json()["extensions"]] == ["alpha_view", "beta_view"]


def test_a_folder_added_later_shows_up_without_restarting(static_copy, client_for):
    client, auth = client_for(static_copy)
    assert len(client.get("/gui/api/extensions", headers=auth).json()["extensions"]) == 2
    write_view(static_copy, "gamma_view", "Gamma")
    assert [item["id"] for item in client.get("/gui/api/extensions", headers=auth).json()["extensions"]] == \
        ["alpha_view", "beta_view", "gamma_view"]


def test_extension_files_are_served_with_the_csp_and_the_page_is_untouched(static_copy, client_for):
    client, _ = client_for(static_copy)
    for path, kind in (("alpha_view/index.js", "javascript"), ("alpha_view/style.css", "css"), ("beta_view/help.js", "javascript")):
        response = client.get(f"/gui/static/extensions/{path}")
        assert response.status_code == 200 and kind in response.headers["content-type"], path
        assert response.headers["content-security-policy"] == CSP and response.headers["x-content-type-options"] == "nosniff"
    page = client.get("/gui/")
    assert page.status_code == 200 and "alpha_view" not in page.text and "extensions" not in page.text   # no line per view
    assert re.findall(r"<script[^>]*>", page.text) == ['<script type="module" src="/gui/static/js/main.js">']


def test_the_real_manifest_route_answers_with_a_list(client_for):
    client, auth = client_for(STATIC_DIR)
    body = client.get("/gui/api/extensions", headers=auth).json()
    assert list(body) == ["extensions"] and isinstance(body["extensions"], list)
    assert body["extensions"] == discover_extensions(STATIC_DIR)


def test_extension_folders_ship_in_the_wheel():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = data["tool"]["setuptools"]["package-data"]["multiagent.gui"]
    assert "static/extensions/*/*" in patterns and "static/extensions/*/*/*" in patterns


# --- the page keeps one script, and no shared file knows any extension ----------------------------------------------------

def test_no_shared_file_names_an_extension_and_the_page_has_one_script_and_leading_app_css():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script[^>]*>", html) == ['<script type="module" src="/gui/static/js/main.js">']
    links = re.findall(r'<link rel="stylesheet" href="([^"]+)">', html)
    assert links[0] == "/gui/static/app.css" and "extensions" not in html
    for path in sorted(JS.rglob("*.js")):
        for spec in re.findall(r"""\bfrom\s+["']([^"']+)["']""", path.read_text(encoding="utf-8")):
            assert "extensions/" not in spec, f"{path.name} imports {spec}: shared code must not depend on a view"


def test_the_only_dynamic_import_is_the_guarded_one_in_extensions_js():
    users = sorted(path.name for path in JS.rglob("*.js") if re.search(r"\bimport\s*\(", path.read_text(encoding="utf-8")))
    assert users == ["extensions.js"]
    source = (JS / "extensions.js").read_text(encoding="utf-8")
    assert "ENTRY_URL.exec(" in source and "import(`/gui/static/extensions/${id}/index.js`)" in source      # constant path + validated id
    assert r"ENTRY_URL = /^\/gui\/static\/extensions\/([a-z][a-z_]{0,39})\/index\.js$/;" in source     # the only URLs it imports


def test_reserved_names_match_the_built_in_routes_and_slots():
    extensions = (JS / "extensions.js").read_text(encoding="utf-8")
    sections = re.findall(r'"(\w+)"', re.search(r"RESERVED_SECTIONS = \[(.*?)\]", extensions)[1])
    route = (JS / "route.js").read_text(encoding="utf-8")
    built_in = re.findall(r'"(\w+)"', re.search(r"const SECTION_IDS = \[(.*?)\]", route)[1])
    assert sorted(sections) == sorted([*built_in, "new"])
    slots = re.findall(r'"(\w+)"', re.search(r"RESERVED_SLOTS = \[(.*?)\]", extensions)[1])
    panel = (JS / "right-panel.js").read_text(encoding="utf-8") + (JS / "panel-slots.js").read_text(encoding="utf-8")
    assert sorted(slots) == sorted(set(re.findall(r'data-slot="(\w+)"', panel)))


# --- hygiene of the real extension folders, whatever they are -------------------------------------------------------------

def check_extension_code(static_dir: Path) -> int:
    """Code of every extension: no HTML injection, eval, inline handler or remote code; imports only its own folder, js/ and vendor/."""
    count = 0
    for folder in extension_dirs(static_dir):
        allowed = [folder.resolve(), (static_dir / "js").resolve(), (static_dir / "vendor").resolve()]
        for path in sorted(folder.rglob("*.js")):
            text = path.read_text(encoding="utf-8")
            count += 1
            for pattern, label in FORBIDDEN:
                assert not re.search(pattern, text), f"{folder.name}/{path.name}: {label}"
            assert "style=" not in text, f"{folder.name}/{path.name}: inline style"
            for spec in re.findall(r"""(?:\bfrom|\bimport\s*\(?)\s*["']([^"']+)["']""", text):
                assert spec.startswith("."), f"{folder.name}/{path.name}: import of {spec} (only relative imports)"
                target = (path.parent / spec).resolve()
                assert any(target.is_relative_to(root) for root in allowed), \
                    f"{folder.name}/{path.name}: imports {spec}, which is outside its own folder, js/ and vendor/"
    return count


def check_extension_styles(static_dir: Path) -> int:
    """Stylesheets of every extension: every rule is scoped to the view's own class or id, colours come from app.css."""
    count = 0
    for folder in extension_dirs(static_dir):
        prefix = folder.name.replace("_", "-")
        own = re.compile(rf"[.#]{re.escape(prefix)}(?:-|(?![\w-]))")
        for sheet in sorted(folder.rglob("*.css")):
            text = re.sub(r"/\*.*?\*/", "", sheet.read_text(encoding="utf-8"), flags=re.S)
            label = f"{folder.name}/{sheet.name}"
            count += 1
            assert "@import" not in text and "url(" not in text, f"{label}: external or inlined resources"
            for name in re.findall(r"@keyframes\s+([\w-]+)", text):
                assert name.startswith(prefix), f"{label}: keyframes {name} must start with {prefix}"
            for prelude, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
                assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla)\(", body), \
                    f"{label}: colours come from the app.css variables (dark mode follows them): {body.strip()[:60]}"
                if prelude.strip().startswith("@"):
                    continue
                for part in (p.strip() for p in prelude.split(",")):
                    if re.fullmatch(r"from|to|\d+(?:\.\d+)?%", part):
                        continue
                    assert own.search(part), f"{label}: selector '{part}' is not scoped to .{prefix}-* or #{prefix}-*"
    return count


def test_real_extension_folders_pass_the_code_and_style_rules():
    check_extension_code(STATIC_DIR)
    check_extension_styles(STATIC_DIR)
    extension_help(STATIC_DIR)


# --- the example views: every guard passes, no shared file changes, and the guards do fail on broken variants ----------------

def test_two_example_views_pass_every_guard_and_change_no_shared_file(static_copy):
    seen = check_extensions(static_copy)
    assert seen["extensions"] == 2 and seen["controls"] == 2 and seen["sections"] == 2     # the checks really see the views
    assert check_extension_code(static_copy) >= 4 and check_extension_styles(static_copy) == 2
    assert sorted(extension_help(static_copy)) == ["alpha_view.action", "alpha_view.view", "beta_view.action", "beta_view.card", "beta_view.view"]
    # Outside the two extension folders the tree is byte for byte the real one: no shared file was added to or edited.
    assert content_hashes(static_copy, outside="extensions") == content_hashes(STATIC_DIR, outside="extensions")
    assert all(name in content_hashes(static_copy, outside="extensions") for name in SHARED)       # and the guard really looks at them
    assert sorted(content_hashes(static_copy)) == sorted(
        [*content_hashes(static_copy, outside="extensions"),
         *(f"extensions/{view}/{name}" for view in ("alpha_view", "beta_view") for name in ("index.js", "help.js", "style.css"))])


@pytest.mark.parametrize("old, new", [
    ('owner: "alpha_view"', 'owner: "beta_view"'),
    ('owner: "alpha_view"', ''),
    ('registerHelp(HELP, "alpha_view")', 'registerHelp(HELP)'),
])
def test_extension_guard_requires_its_own_owner(static_copy, old, new):
    path = static_copy / "extensions" / "alpha_view" / "index.js"
    source = path.read_text(encoding="utf-8")
    assert old in source
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(AssertionError, match="must pass owner alpha_view"):
        check_extensions(static_copy)


def test_extension_heading_help_accepts_single_quoted_topic(static_copy):
    path = static_copy / "extensions" / "alpha_view" / "index.js"
    source = path.read_text(encoding="utf-8")
    source = source.replace('topic="alpha_view.view"', "topic='alpha_view.view'")
    path.write_text(source, encoding="utf-8")
    check_extensions(static_copy)


BROKEN_VIEWS = [      # (view name, file to break, text to find, text to put instead (None: delete the file), the guard's complaint)
    ("control_view", "index.js", ' ...${helpFor("control_view.action")}', "", "control without help"),
    ("heading_view", "index.js", 'id="heading-view-title"', 'id="title"', "must start with 'heading-view-'"),
    ("key_view", "help.js", '"key_view.view"', '"other.view"', "must start with 'key_view.'"),
    ("unused_view", "help.js", "\n};", ',\n  "unused_view.extra": {"title": "Sobra", "hint": "Nadie la usa.", "more": ["Primera frase.", "Segunda frase."]}\n};',
     "help texts nobody uses"),
    ("silent_view", "index.js", "registerView(", "// registerView(", "registers no view and no slot"),
    ("helpless_view", "help.js", None, None, "go together"),
    ("loose_view", "index.js", '<${Help} topic="loose_view.view" />', "", "heading id"),
]


@pytest.mark.parametrize("name, filename, old, new, message", BROKEN_VIEWS)
def test_the_help_guard_fails_on_a_broken_view(tmp_path, name, filename, old, new, message):
    write_view(tmp_path, name, "Rota")
    check_extensions(tmp_path)                                    # the view is fine until it is broken
    path = tmp_path / "extensions" / name / filename
    if old is None:
        path.unlink()
    else:
        text = path.read_text(encoding="utf-8")
        assert old in text, (name, old)
        path.write_text(text.replace(old, new), encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape(message) if message != "heading id" else "is not followed by its help"):
        check_extensions(tmp_path)


@pytest.mark.parametrize("old, new, message", [
    ("innerHTML", "document.body.innerHTML = token;", "innerHTML"),
    ("import", 'import { x } from "../beta_view/help.js";', "outside its own folder"),
    ("remote", 'const url = "https://example.invalid/x.js";', "remote URL"),
])
def test_the_code_guard_fails_on_unsafe_or_coupled_code(static_copy, old, new, message):
    index = static_copy / "extensions" / "alpha_view" / "index.js"
    index.write_text(index.read_text(encoding="utf-8") + "\n" + new + "\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=message):
        check_extension_code(static_copy)


@pytest.mark.parametrize("rule, message", [
    (".card { margin: 0; }", "not scoped"),
    (".alpha-view-x { color: #fff; }", "colours come from"),
    ("@import url(other.css);", "external"),
    ("@keyframes spin { from { opacity: 0; } to { opacity: 1; } }", "keyframes spin must start with"),
])
def test_the_style_guard_fails_on_a_rule_that_could_hit_another_view(static_copy, rule, message):
    sheet = static_copy / "extensions" / "alpha_view" / "style.css"
    sheet.write_text(sheet.read_text(encoding="utf-8") + rule + "\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=message):
        check_extension_styles(static_copy)


# --- the client registries and the loader, run in Node --------------------------------------------------------------------

def run_node(script: str):
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def url(path: Path) -> str:
    return json.dumps(path.as_uri())


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_registries_validate_and_order_what_extensions_register():
    out = run_node(f"""
      import {{ registerView, registeredViews, isRegisteredView, registerSlot, registeredSlots, registerHelp, registeredHelp }} from {url(JS / "extensions.js")};
      const attempt = (fn) => {{ try {{ fn(); return null; }} catch (error) {{ return error.message; }} }};
      const View = () => null;
      const result = {{}};
      result.badView = [
        attempt(() => registerView({{ id: "Bad", label: "x", component: View }})),
        attempt(() => registerView({{ id: "runs", label: "x", component: View }})),
        attempt(() => registerView({{ id: "new", label: "x", component: View }})),
        attempt(() => registerView({{ id: "ok", label: "", component: View }})),
        attempt(() => registerView({{ id: "ok", label: "x", component: "nope" }})),
        attempt(() => registerView()),
      ];
      registerView({{ id: "zeta", label: "Zeta", component: View, order: 10 }});
      registerView({{ id: "alpha", label: "Alfa", component: View }});
      registerView({{ id: "beta", label: "Beta", component: View, order: 10 }});
      result.duplicate = attempt(() => registerView({{ id: "alpha", label: "Otra", component: View }}));
      result.views = registeredViews().map((v) => v.id);
      result.isRegistered = [isRegisteredView("alpha"), isRegisteredView("runs"), isRegisteredView("nope")];
      result.badSlot = [
        attempt(() => registerSlot("tokens", View, {{ title: "x" }})),
        attempt(() => registerSlot("Bad", View, {{ title: "x" }})),
        attempt(() => registerSlot("ok_slot", View, {{}})),
        attempt(() => registerSlot("ok_slot", "nope", {{ title: "x" }})),
        attempt(() => registerSlot("ok_slot", View, {{ title: "x", topic: "notatopic" }})),
      ];
      registerSlot("late", View, {{ title: "Tarde", order: 90 }});
      registerSlot("early", View, {{ title: "Pronto", topic: "early.card", order: 5 }});
      result.duplicateSlot = attempt(() => registerSlot("late", View, {{ title: "Otra" }}));
      result.slots = registeredSlots().map((s) => [s.name, s.title, s.topic]);
      result.badHelp = [
        attempt(() => registerHelp(null)), attempt(() => registerHelp([])),
        attempt(() => registerHelp({{ nodot: {{ title: "a", hint: "b", more: ["c"] }} }})),
        attempt(() => registerHelp({{ "a.b": {{ title: "a", hint: "b" }} }})),
      ];
      registerHelp({{ "alpha.view": {{ title: "A", hint: "B", more: ["C."] }} }});
      result.duplicateHelp = attempt(() => registerHelp({{ "alpha.view": {{ title: "A", hint: "B", more: ["C."] }} }}));
      result.help = [registeredHelp("alpha.view").title, registeredHelp("alpha.other")];
      console.log(JSON.stringify(result));
    """)
    assert all(out["badView"]) and "route of a built-in screen" in out["badView"][1] and "route of a built-in screen" in out["badView"][2]
    assert "already registered" in out["duplicate"]
    assert out["views"] == ["beta", "zeta", "alpha"]               # by order, then by id; no order means 100
    assert out["isRegistered"] == [True, False, False]
    assert all(out["badSlot"]) and "built-in panel" in out["badSlot"][0]
    assert "already registered" in out["duplicateSlot"]
    assert out["slots"] == [["early", "Pronto", "early.card"], ["late", "Tarde", None]]
    assert all(out["badHelp"]) and "already registered" in out["duplicateHelp"]
    assert out["help"] == ["A", None]


@needs_node
def test_slot_and_usage_labels_limit_the_trimmed_text():
    out = run_node(f"""
      import {{ registerSlot, registeredSlots, registerUsageSection, registeredUsageSections }} from {url(JS / "extensions.js")};
      const View = () => null;
      const attempt = (fn) => {{ try {{ fn(); return null; }} catch (error) {{ return error.message; }} }};
      const padded = "  " + "a".repeat(30) + "  ";
      registerSlot("padded_slot", View, {{ title: padded, owner: "sample" }});
      registerUsageSection({{ id: "padded_usage", label: padded, component: View, owner: "sample" }});
      console.log(JSON.stringify({{
        slot: registeredSlots()[0].title, usage: registeredUsageSections()[0].label,
        tooLongSlot: attempt(() => registerSlot("long_slot", View, {{ title: "a".repeat(31), owner: "sample" }})),
        tooLongUsage: attempt(() => registerUsageSection({{ id: "long_usage", label: "a".repeat(31), component: View, owner: "sample" }})),
      }}));
    """)
    assert out == {"slot": "a" * 30, "usage": "a" * 30,
                   "tooLongSlot": "registerSlot: title must be 1 to 30 characters",
                   "tooLongUsage": "registerUsageSection: label must be 1 to 30 characters"}


@needs_node
def test_loader_keeps_going_past_a_broken_extension_and_refuses_urls_outside_the_convention():
    out = run_node(f"""
      import {{ loadExtensions }} from {url(JS / "extensions.js")};
      const good = (id, style) => ({{ id, script: "/gui/static/extensions/" + id + "/index.js", style: style ? "/gui/static/extensions/" + id + "/style.css" : null }});
      const imported = [], sheets = [];
      const importModule = async (target) => {{ imported.push(target); if (target.includes("/boom/")) throw new Error("boom in module"); }};
      const addStylesheet = async (href) => {{ sheets.push(href); }};
      const manifest = {{ extensions: [
        good("alpha", true), good("boom", true), good("gamma", false),
        {{ id: "evil", script: "https://example.invalid/x.js", style: null }},
        {{ id: "other", script: "/gui/static/extensions/alpha/index.js", style: null }},
        {{ id: "dots", script: "/gui/static/extensions/../js/main.js", style: null }},
        {{ id: "sheet", script: "/gui/static/extensions/sheet/index.js", style: "/gui/static/app.css" }},
        null,
      ] }};
      const first = await loadExtensions({{ fetchManifest: async () => manifest, importModule, addStylesheet }});
      const warned = [];
      const second = await loadExtensions({{ fetchManifest: async () => {{ throw new Error("HTTP 404"); }}, importModule, addStylesheet }});
      const third = await loadExtensions({{ fetchManifest: async () => ({{ extensions: "nope" }}), importModule, addStylesheet }});
      console.log(JSON.stringify({{ first, second, third, imported, sheets }}));
    """)
    assert out["first"]["loaded"] == ["alpha", "gamma"]
    assert [item["id"] for item in out["first"]["failed"]] == ["boom", "evil", "other", "dots", "sheet", "null"]
    assert "boom in module" in out["first"]["failed"][0]["message"]
    assert out["imported"] == ["/gui/static/extensions/alpha/index.js", "/gui/static/extensions/boom/index.js",
                               "/gui/static/extensions/gamma/index.js"]        # nothing outside the convention is imported
    assert out["sheets"] == ["/gui/static/extensions/alpha/style.css", "/gui/static/extensions/boom/style.css"]
    assert out["second"] == {"loaded": [], "failed": [{"id": "extensions", "message": "HTTP 404"}]}
    assert out["third"]["loaded"] == [] and [item["id"] for item in out["third"]["failed"]] == ["extensions"]    # a list that is not a list


@needs_node
def test_two_example_views_load_route_and_render_in_node(static_copy):
    manifest = discover_extensions(static_copy)
    out = run_node(f"""
      import {{ pathToFileURL }} from "node:url";
      import {{ loadExtensions, registeredViews, registeredSlots, registeredHelp }} from {url(static_copy / "js" / "extensions.js")};
      import {{ parseRoute, routeHref }} from {url(static_copy / "js" / "route.js")};
      import {{ registerSlot }} from {url(static_copy / "js" / "panel-slots.js")};
      import * as registry from {url(static_copy / "js" / "extensions.js")};
      const base = {json.dumps(str(static_copy))};
      const sheets = [];
      const before = parseRoute("#alpha_view/42");
      const report = await loadExtensions({{
        fetchManifest: async () => ({{ extensions: {json.dumps(manifest)} }}),
        importModule: (target) => import(pathToFileURL(base + target.replace("/gui/static", "")).href),
        addStylesheet: async (href) => {{ sheets.push(href); }},
      }});
      const views = registeredViews();
      const vnode = views[0].component({{ token: "t", route: {{ section: "alpha_view", id: "42" }} }});
      const slot = registeredSlots()[0];
      console.log(JSON.stringify({{
        before, report, sheets, labels: views.map((v) => [v.id, v.label]), vnode: Boolean(vnode && vnode.type),
        after: [parseRoute("#alpha_view/42"), parseRoute("#beta_view"), parseRoute("#gamma_view"), routeHref("beta_view", "x y")],
        slot: [slot.name, slot.title, slot.topic, typeof slot.component], reexport: registerSlot === registry.registerSlot,
        help: registeredHelp("beta_view.card").title,
      }}));
    """)
    assert out["before"] == {"section": "sessions", "id": None}          # unknown before the extension registers its view
    assert out["report"] == {"loaded": ["alpha_view", "beta_view"], "failed": []}
    assert out["sheets"] == ["/gui/static/extensions/alpha_view/style.css", "/gui/static/extensions/beta_view/style.css"]
    assert out["labels"] == [["alpha_view", "Alfa"], ["beta_view", "Beta"]] and out["vnode"] is True
    assert out["after"][0] == {"section": "alpha_view", "id": "42"} and out["after"][1] == {"section": "beta_view", "id": None}
    assert out["after"][2] == {"section": "sessions", "id": None} and out["after"][3] == "#beta_view/x%20y"
    assert out["slot"] == ["beta_view", "Beta", "beta_view.card", "function"] and out["reexport"] is True
    assert out["help"] == "Tarjeta"


# --- the page never waits for the extensions beyond a budget (a hung list, a bad answer, a slow or broken module) ----------------

@needs_node
def test_a_list_that_hangs_fails_or_answers_badly_never_holds_the_page():
    out = run_node(f"""
      import {{ loadExtensions, extensionFailures }} from {url(JS / "extensions.js")};
      const modules = [];
      const importModule = async (target) => modules.push(target);
      const run = async (fetchManifest) => {{
        const started = performance.now();
        const answer = await loadExtensions({{ fetchManifest, importModule, addStylesheet: async () => {{}}, timeoutMs: 150 }});
        return {{ ...answer, ms: performance.now() - started }};
      }};
      const result = {{}};
      let aborted = false;
      result.hangs = await run(({{ signal }}) => new Promise((resolve, reject) => {{
        signal.addEventListener("abort", () => {{ aborted = true; reject(new Error("aborted by the page")); }});
      }}));
      result.aborted = aborted;
      result.hangsIgnoringTheSignal = await run(() => new Promise(() => {{}}));
      result.invalidJson = await run(async () => {{ throw new SyntaxError("Unexpected token < in JSON at position 0"); }});
      result.serverError = await run(async () => {{ throw Object.assign(new Error("HTTP 500"), {{ status: 500 }}); }});
      result.shapes = [];
      for (const body of [null, {{}}, [], {{ extensions: "nope" }}, "text"]) result.shapes.push(await run(async () => body));
      result.stillFailed = extensionFailures().map((item) => item.id);
      result.empty = await run(async () => ({{ extensions: [] }}));
      result.afterEmpty = extensionFailures().map((item) => item.id);
      result.modules = modules;
      console.log(JSON.stringify(result));
    """)
    for key in ("hangs", "hangsIgnoringTheSignal"):
        assert out[key]["loaded"] == [] and out[key]["ms"] < 1500, key                       # answers by the budget, not when the list does
        assert [item["id"] for item in out[key]["failed"]] == ["extensions"]
        assert "still loading after 150 ms" in out[key]["failed"][0]["message"]
    assert out["aborted"] is True                                                            # the request itself is cancelled
    assert "Unexpected token" in out["invalidJson"]["failed"][0]["message"] and "HTTP 500" in out["serverError"]["failed"][0]["message"]
    for answer in out["shapes"]:
        assert answer["loaded"] == [] and [item["id"] for item in answer["failed"]] == ["extensions"], answer
        assert "not valid" in answer["failed"][0]["message"]
    assert out["stillFailed"] == ["extensions"]                                              # the notice has something to say
    assert out["empty"] == {"loaded": [], "failed": [], "ms": out["empty"]["ms"]} and out["afterEmpty"] == []   # a good list clears it
    assert out["modules"] == []


@needs_node
def test_a_slow_or_hung_extension_does_not_hold_the_first_render_and_a_late_one_registers_when_it_arrives():
    out = run_node(f"""
      import {{ loadExtensions, extensionFailures, registeredViews, registerView, subscribeRegistry, registryVersion }} from {url(JS / "extensions.js")};
      const entry = (id) => ({{ id, script: "/gui/static/extensions/" + id + "/index.js", style: null }});
      let release;
      const slowGate = new Promise((resolve) => {{ release = resolve; }});
      const importModule = async (target, id) => {{
        if (id === "fast") return registerView({{ id: "fast", label: "Rapida", component: () => null }});
        if (id === "slow") {{ await slowGate; return registerView({{ id: "slow", label: "Lenta", component: () => null }}); }}
        if (id === "boom") throw new Error("boom in module");
        return new Promise(() => {{}});                                  // "hung": never finishes
      }};
      let notified = 0;
      subscribeRegistry(() => {{ notified += 1; }});
      const started = performance.now();
      const answer = await loadExtensions({{
        fetchManifest: async () => ({{ extensions: ["fast", "slow", "boom", "hung"].map(entry) }}),
        importModule, addStylesheet: async () => {{}}, timeoutMs: 200,
      }});
      const ms = performance.now() - started;
      const early = {{ views: registeredViews().map((v) => v.id), failures: extensionFailures().map((f) => f.id), notified }};
      const version = registryVersion();
      release();
      await new Promise((resolve) => setTimeout(resolve, 50));
      const late = {{ views: registeredViews().map((v) => v.id), failures: extensionFailures().map((f) => f.id), notified, grew: registryVersion() > version }};
      // a sheet that never loads does not hold the page either, and does not make its extension a failure
      const sheet = await loadExtensions({{
        fetchManifest: async () => ({{ extensions: [entry("quiet")] }}), importModule: async () => {{}},
        addStylesheet: () => new Promise(() => {{}}), timeoutMs: 150,
      }});
      console.log(JSON.stringify({{ answer, ms, early, late, sheet }}));
    """)
    assert out["ms"] < 1500
    assert out["answer"]["loaded"] == ["fast"]
    assert [item["id"] for item in out["answer"]["failed"]] == ["slow", "boom", "hung"]          # manifest order
    assert "still loading after 200 ms" in out["answer"]["failed"][0]["message"] and "boom in module" in out["answer"]["failed"][1]["message"]
    assert out["early"]["views"] == ["fast"] and sorted(out["early"]["failures"]) == ["boom", "hung", "slow"]
    assert out["early"]["notified"] >= 1
    assert out["late"]["views"] == ["fast", "slow"] and sorted(out["late"]["failures"]) == ["boom", "hung"]   # the late one arrived and its failure went
    assert out["late"]["notified"] > out["early"]["notified"] and out["late"]["grew"] is True
    assert out["sheet"]["loaded"] == ["quiet"] and out["sheet"]["failed"] == []


def test_the_page_draws_whatever_the_extension_list_does():
    main = (JS / "main.js").read_text(encoding="utf-8")
    assert "const start = () => render(" in main and ".then(start, start)" in main                  # drawn after success AND after failure
    assert re.search(r"catch \(_\) \{\s*start\(\);", main)                                         # and if loadExtensions itself could not start
    assert "getJSON(\"/gui/api/extensions\", token, signal)" in main                              # the request can be cancelled
    assert "export async function getJSON(path, token, signal)" in (JS / "auth.js").read_text(encoding="utf-8")
    assert "extensionFailures()" in main and "No se pudo cargar:" in main                          # the notice comes from the registry
    assert "useRegistry();" in main and "subscribeRegistry" in (JS / "route.js").read_text(encoding="utf-8")   # late arrivals redraw
    budget = int(re.search(r"export const LOAD_BUDGET_MS = (\d+);", (JS / "extensions.js").read_text(encoding="utf-8"))[1])
    assert 1000 <= budget <= 5000
    assert "current?.label" in main


@needs_node
def test_failed_parallel_import_rolls_back_only_its_explicit_owner():
    out = run_node(f"""
      import {{ loadExtensions, registerView, registerSlot, registerHelp, registerUsageSection,
        registeredViews, registeredSlots, registeredHelp, registeredUsageSections }} from {url(JS / "extensions.js")};
      const entry = (id) => ({{ id, script: `/gui/static/extensions/${{id}}/index.js` }});
      const C = () => null;
      let release;
      const gate = new Promise((resolve) => {{ release = resolve; }});
      const warnings = [];
      console.warn = (message) => warnings.push(message);
      const importModule = async (_url, id) => {{
        registerView({{ id, owner: id, label: id, component: C }});
        registerSlot(id, C, {{ owner: id, title: id }});
        registerHelp({{ [`${{id}}.view`]: {{ title: id, hint: id, more: [] }} }}, id);
        registerUsageSection({{ id, owner: id, label: id, component: C }});
        if (id === "broken") {{ await gate; throw new Error("after registering"); }}
      }};
      const loading = loadExtensions({{ fetchManifest: async () => ({{ extensions: [entry("broken"), entry("healthy")] }}),
        importModule, addStylesheet: async () => {{}} }});
      await new Promise((resolve) => setTimeout(resolve, 0));
      release();
      const report = await loading;
      registerView({{ id: "legacy", label: "Legacy", component: C }});
      const second = await loadExtensions({{ fetchManifest: async () => ({{ extensions: [entry("legacy")] }}),
        importModule: async () => {{ throw new Error("legacy failed"); }}, addStylesheet: async () => {{}} }});
      console.log(JSON.stringify({{ report, second, views: registeredViews().map((v) => v.id),
        slots: registeredSlots().map((s) => s.name), usage: registeredUsageSections().map((s) => s.id),
        help: [registeredHelp("broken.view"), registeredHelp("healthy.view")?.title], warnings }}));
    """)
    assert out["report"]["loaded"] == ["healthy"]
    assert out["report"]["failed"][0]["id"] == "broken"
    assert out["views"] == ["healthy", "legacy"]
    assert out["slots"] == ["healthy"] and out["usage"] == ["healthy"]
    assert out["help"] == [None, "healthy"]
    assert out["second"]["failed"][0]["id"] == "legacy"
    assert any("no explicit owner" in warning for warning in out["warnings"])


@needs_node
def test_view_label_is_checked_after_trimming():
    out = run_node(f"""
      import {{ registerView, registeredViews }} from {url(JS / "extensions.js")};
      registerView({{ id: "padded", owner: "padded", label: "  Name  ", component: () => null }});
      registerView({{ id: "boundary", owner: "boundary", label: " " + "x".repeat(30) + " ", component: () => null }});
      let message = null;
      try {{ registerView({{ id: "long", owner: "long", label: "x".repeat(31) + "  ", component: () => null }}); }}
      catch (error) {{ message = error.message; }}
      console.log(JSON.stringify({{ labels: registeredViews().map((view) => view.label), message }}));
    """)
    assert out == {"labels": ["x" * 30, "Name"], "message": "registerView: label must be 1 to 30 characters"}


def test_a_folder_whose_entry_is_not_a_file_is_not_listed(tmp_path):
    (tmp_path / "extensions" / "odd_one" / "index.js").mkdir(parents=True)       # a directory called index.js
    (tmp_path / "extensions" / "fine" ).mkdir(parents=True)
    (tmp_path / "extensions" / "fine" / "index.js").write_text("// entry", encoding="utf-8")
    assert [item["id"] for item in discover_extensions(tmp_path)] == ["fine"]


@needs_node
def test_register_view_accepts_optional_help_topic_and_rejects_invalid_ones():
    out = run_node(f"""
      import {{ registerView, registeredViews }} from {url(JS / "extensions.js")};
      const View = () => null;
      const attempt = (fn) => {{ try {{ fn(); return null; }} catch (error) {{ return error.message; }} }};
      registerView({{ id: "plain_view", owner: "plain_view", label: "Sin tema", component: View }});
      registerView({{ id: "null_topic", owner: "null_topic", label: "Nulo", topic: null, component: View }});
      registerView({{ id: "with_topic", owner: "with_topic", label: "Con tema", topic: "with_topic.view", component: View }});
      const invalid = [123, true, {{}}, ["a.b"], "", "notopic", "Bad.topic"].map((topic) =>
        attempt(() => registerView({{ id: "bad_topic", owner: "bad_topic", label: "Malo", topic, component: View }}))
      );
      const topics = Object.fromEntries(registeredViews().map((v) => [v.id, v.topic]));
      console.log(JSON.stringify({{ topics, invalid }}));
    """)
    assert out["topics"] == {"plain_view": None, "null_topic": None, "with_topic": "with_topic.view"}
    assert all(msg == 'registerView: topic must be a help key like "name.topic"' for msg in out["invalid"])


@needs_node
def test_real_extension_views_have_a_single_title_and_reachable_help():
    expected = {
        "apps": ("Apps y agentes", "extensions/apps/index.js"),
        "continuity": ("Decisiones", "extensions/continuity/view.js"),
        "document_alerts": ("Alertas de documentos", "extensions/document_alerts/index.js"),
        "project": ("Proyectos", "extensions/project/index.js"),
        "task_waiting": ("Esperan tu decisión", "extensions/task_waiting/index.js"),
    }
    main = (JS / "main.js").read_text(encoding="utf-8")
    assert "<${Help}" in main and "topic=${entry.topic}" in main
    for ext_id, (label, rel_path) in expected.items():
        source = (STATIC_DIR / rel_path).read_text(encoding="utf-8")
        assert not re.search(r"<h[123]\b", source), f"{rel_path} must rely on main.js <h2> instead of drawing its own title"
        assert f'aria-label="{label}"' in source, f"{rel_path} missing section aria-label"
        assert f'topic="{ext_id}.view"' not in source, f"{rel_path} must not duplicate {ext_id}.view help"

    manifest = discover_extensions(STATIC_DIR)
    out = run_node(f"""
      import {{ pathToFileURL }} from "node:url";
      import {{ loadExtensions, registeredViews, registeredHelp }} from {url(JS / "extensions.js")};
      const base = {json.dumps(str(STATIC_DIR))};
      const report = await loadExtensions({{
        fetchManifest: async () => ({{ extensions: {json.dumps(manifest)} }}),
        importModule: (target) => import(pathToFileURL(base + target.replace("/gui/static", "")).href),
        addStylesheet: async () => {{}},
      }});
      const views = Object.fromEntries(registeredViews().map((v) => [v.id, {{
        label: v.label,
        topic: v.topic,
        help: v.topic ? registeredHelp(v.topic) : null,
      }}]));
      console.log(JSON.stringify({{ report, views }}));
    """)
    assert out["report"]["failed"] == []
    for ext_id, (label, _) in expected.items():
        info = out["views"][ext_id]
        assert info["label"] == label
        assert info["topic"] == f"{ext_id}.view"
        assert info["help"] and info["help"]["title"] and info["help"]["hint"] and info["help"]["more"]
