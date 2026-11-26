# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""INI-T03: the «Inicio» screen. Its pure model and the sidebar rule run in Node (skipped without it); the wiring is static.

No server, no browser and no network: the status is a literal shaped like ``GET /gui/api/start`` and the clipboard is a double."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from gui_text import ui_text

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
STEP_IDS = ["install", "try_mock", "connect", "activate", "real_run"]
TITLES = {"install": "Instalar una oficina", "try_mock": "Probarlo en Mock y ver el documento", "connect": "Conectar un agente",
          "activate": "Activarlo", "real_run": "Correr con tus datos y guardar el documento"}


def read(name: str) -> str:
    return ui_text(JS / name)


def run_node(source: str):
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8",
                            cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


def uri(name: str) -> str:
    return json.dumps((JS / name).as_uri())


def status(done=(), **extra) -> dict:
    steps = [{"id": step, "title": TITLES[step], "done": step in done, "done_at": "2026-11-23T10:00:00Z" if step in done else None}
             for step in STEP_IDS]
    base = {"schema": "maf.first-run/1", "steps": steps, "pending": [s["id"] for s in steps if not s["done"]],
            "all_done": len(done) == len(STEP_IDS), "mock": True, "models": "mock", "offer_real": False,
            "install_started_at": "2026-11-23T09:50:00Z", "times": [], "times_text": "Inicio: 2026-11-23T09:50:00Z\ninstall: pendiente"}
    return {**base, **extra}


SETUP = """
  import { startRows, inStartSidebar, timesText, copyTimes, backToMockAction, REAL_MODELS_NOTE, POLL_MS } from %s;
  import { navigationSections } from %s;
""" % (uri("start-model.js"), uri("sidebar-model.js"))


def real_sections() -> list[dict]:
    block = re.search(r"const SECTIONS = \[(.*?)\n\];", read("main.js"), re.S)[1]
    rows = re.findall(r'\{ id: "(\w+)", label: "([^"]+)", icon: "(\w+)", mode: "(\w+)" \}', block)
    return [{"id": a, "label": b, "icon": c, "mode": d} for a, b, c, d in rows]


@needs_node
def test_only_pending_steps_in_order():
    out = run_node(SETUP + "console.log(JSON.stringify(startRows(%s).map((row) => row.id)));" % json.dumps(status(done=["install"])))
    assert out == ["try_mock", "connect", "activate", "real_run"]
    out = run_node(SETUP + "console.log(JSON.stringify(startRows(%s).map((row) => row.id)));" % json.dumps(status(done=["install", "connect"])))
    assert out == ["try_mock", "activate", "real_run"]
    assert run_node(SETUP + "console.log(JSON.stringify([startRows(%s), startRows(null), startRows({})]));" % json.dumps(status(done=STEP_IDS))) == [[], [], []]


@needs_node
def test_start_in_sidebar_while_pending():
    pending, finished = status(done=["install"]), status(done=STEP_IDS)
    out = run_node(SETUP + """
      const sections = %s;
      const ids = (list) => list.map((row) => row.id);
      const show = (value) => { const nav = navigationSections(sections, [], [], "simple", inStartSidebar(value)); return [ids(nav.primary), ids(nav.more)]; };
      console.log(JSON.stringify({ pending: show(%s), done: show(%s), unknown: show(null),
        defaults: JSON.stringify(navigationSections(sections, [], [], "simple")) === JSON.stringify(navigationSections(sections, [], [], "simple", false)) }));
    """ % (json.dumps(real_sections()), json.dumps(pending), json.dumps(finished)))
    assert out["pending"][0][0] == "start"   # the first row of the primary list, right after «Buscar»
    assert "start" not in out["pending"][1]
    assert "start" not in out["done"][0] and "start" in out["done"][1]
    assert "start" not in out["unknown"][0] and "start" in out["unknown"][1]   # no answer yet: «Más»
    assert out["defaults"] is True   # the fifth parameter is optional and false by default


@needs_node
def test_start_goes_first_even_when_pinned_later():
    out = run_node(SETUP + """
      const sections = %s;
      const nav = navigationSections(sections, [], ["usage", "start"], "simple", true);
      console.log(JSON.stringify(nav.primary.map((row) => row.id)));
    """ % json.dumps(real_sections()))
    assert out[0] == "start" and out.count("start") == 1 and "usage" in out


@needs_node
def test_install_actions():
    with_example = status(example_available=True)
    without = status(example_available=False)
    out = run_node(SETUP + """
      const install = (value) => startRows(value).find((row) => row.id === "install").actions;
      console.log(JSON.stringify({ yes: install(%s), no: install(%s), unknown: install(%s) }));
    """ % (json.dumps(with_example), json.dumps(without), json.dumps(status())))
    assert [(a["kind"], a.get("path"), a.get("href")) for a in out["yes"]] == [
        ("post", "/gui/api/offices/example", None), ("link", None, "#offices")]
    assert out["yes"][0]["label"] == "Instalar la oficina de ejemplo"
    assert out["yes"][1]["label"] == "Importar una oficina"
    assert [(a["kind"], a.get("href")) for a in out["no"]] == [("link", "#offices")]
    assert out["unknown"] == out["no"]


@needs_node
def test_other_step_actions_are_links():
    out = run_node(SETUP + "console.log(JSON.stringify(startRows(%s)));" % json.dumps(status(done=["install"])))
    by_id = {row["id"]: row for row in out}
    assert by_id["try_mock"]["actions"] == [{"kind": "link", "label": "Probar en Mock", "href": "#new"}]
    assert by_id["try_mock"]["note"] == "Cuando termine, verás el documento en el hilo"
    assert by_id["connect"]["actions"] == [{"kind": "link", "label": "Conectar un agente", "href": "#apps"}]
    assert by_id["activate"]["actions"] == [{"kind": "link", "label": "Activar un agente", "href": "#apps"}]
    assert [a["kind"] for a in by_id["real_run"]["actions"]] == ["link"]     # no offer_real: only the link


@needs_node
def test_activation_not_applicable_has_no_action_or_pending_entry():
    value = status(times_text="Start: 2026-11-23T09:50:00Z\nActivate it: not applicable")
    value["steps"][3]["applicable"] = False
    value["pending"].remove("activate")
    out = run_node(SETUP + """
      Object.defineProperty(globalThis, "navigator", {value: {language: "es-MX"}, configurable: true});
      const value = %s;
      const row = startRows(value).find((item) => item.id === "activate");
      console.log(JSON.stringify({ row, pending: value.pending, copied: timesText(value) }));
    """ % json.dumps(value))
    assert out["row"]["actions"] == []
    assert out["row"]["note"] == "No aplica: no se detectó ningún ejecutor CLI en este equipo."
    assert "activate" not in out["pending"]
    assert out["copied"] == value["times_text"]


@needs_node
def test_try_mock_opens_the_example_office_when_installed():
    out = run_node(SETUP + """
      const ordinary = startRows(%s).find((row) => row.id === "try_mock").actions[0];
      const example = startRows(%s).find((row) => row.id === "try_mock").actions[0];
      console.log(JSON.stringify({ ordinary, example }));
    """ % (json.dumps(status()), json.dumps(status(example_installed=True))))
    assert out == {
        "ordinary": {"kind": "link", "label": "Probar en Mock", "href": "#new"},
        "example": {"kind": "link", "label": "Probar en Mock", "href": "#new/office/cafe/menu"},
    }


@needs_node
def test_real_models_offer_and_note():
    offered, plain = status(done=["install", "try_mock"], offer_real=True), status(done=["install", "try_mock"])
    out = run_node(SETUP + """
      const real = (value) => startRows(value).find((row) => row.id === "real_run").actions;
      console.log(JSON.stringify({ offered: real(%s), plain: real(%s), note: REAL_MODELS_NOTE, back: [backToMockAction(%s), backToMockAction(%s)] }));
    """ % (json.dumps(offered), json.dumps(plain), json.dumps(status(models="real")), json.dumps(plain)))
    assert out["note"] == "Los modelos reales pueden costar dinero o usar tu cuota. Puedes volver a Mock aquí mismo"
    first = out["offered"][0]
    assert first["label"] == "Pasar a modelos reales" and first["kind"] == "post" and first["confirm"] is True
    assert first["note"] == out["note"] and first["path"] == "/gui/api/settings/models" and first["body"] == {"models": "real"}
    assert out["offered"][-1] == {"kind": "link", "label": "Correr con tus datos", "href": "#new"}
    assert [a["label"] for a in out["plain"]] == ["Correr con tus datos"]      # never offered without offer_real
    assert out["back"][0]["body"] == {"models": "mock"} and out["back"][1] is None


@needs_node
def test_times_text_copy():
    text = "Inicio: 2026-11-23T09:50:00Z\nInstalar una oficina: 2026-11-23T09:52:00Z (2 min desde el inicio)"
    out = run_node(SETUP + """
      const written = [];
      const value = %s;
      const copied = await copyTimes(value, async (text) => { written.push(text); });
      console.log(JSON.stringify({ shown: timesText(value), copied, written, empty: timesText({}), nothing: await copyTimes({}, async () => { throw new Error("no"); }) }));
    """ % json.dumps(status(times_text=text)))
    assert out["shown"] == text and out["written"] == [text] and out["copied"] is True   # exactly what the server wrote
    assert out["empty"] == "" and out["nothing"] is False


@needs_node
def test_text_is_plain():
    hostile = "<img src=x onerror=alert(1)>"
    out = run_node(SETUP + """
      const value = %s;
      value.steps[0].title = %s;
      console.log(JSON.stringify(startRows(value)[0]));
    """ % (json.dumps(status()), json.dumps(hostile)))
    assert out["title"] == hostile     # the model hands the title over untouched; the view only prints it as text
    source = read("start.js") + read("start-model.js")
    assert "innerHTML" not in source and "dangerouslySetInnerHTML" not in source and "insertAdjacentHTML" not in source
    assert "style=" not in source


def test_start_is_simple():
    row = next(row for row in real_sections() if row["id"] == "start")
    assert row == {"id": "start", "label": "Inicio", "icon": "check", "mode": "simple"}


def test_poll_is_four_seconds_and_never_stacks():
    model, view = read("start-model.js"), read("start.js")
    assert "POLL_MS = 4000" in model
    assert "if (inflight) return inflight;" in view      # a request in flight is reused, never doubled
    assert "setInterval(refresh, POLL_MS)" in view and "CHECKING_TEXT" in view
    assert 'section === "start"' in read("main.js") and "<${Start} token=${token} />" in read("main.js")


def test_start_is_routed_and_reserved():
    assert '"start"' in read("route.js") and '"start"' in read("extensions.js")
    assert "inStartSidebar(start.status)" in read("sidebar.js")


def test_start_reads_example_office_availability():
    model, view = read("start-model.js"), read("start.js")
    assert 'OFFICES_PATH = "/gui/api/offices"' in model
    assert 'EXAMPLE_PATH = "/gui/api/offices/example"' in model
    assert "apiGet(OFFICES_PATH, token)" in view
    assert "example_installed: installed.some((office) => office && office.id === EXAMPLE_OFFICE_ID)" in view


def test_start_css_block_is_at_the_end_and_has_no_inline_style():
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    block = css[css.index("/* INI-T03 */"):]
    assert ".start-steps" in block and ".start-times" in block
    assert "style=" not in block
