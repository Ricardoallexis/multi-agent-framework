# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T07: the floor plan extension (V1): layout, fit and zoom, the list equivalent and the scene poller.

The extension is a folder (``static/extensions/floor_plan/``). Its pure modules run under Node on an example scene written
here (generic names, no real data); what needs real geometry (the drawing at 390 and 1280 px, the pinch, the keyboard) is
checked by hand."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
FOLDER = STATIC_DIR / "extensions" / "floor_plan"
JS = STATIC_DIR / "js"


def member(member_id, name, kind="agent", state="idle", role=""):
    return {"id": member_id, "kind": kind, "name": name, "role": role, "state": state,
            "label": "x", "worker": "-", "since": None}


def area(area_id, name, members, *, data_class="non_sensitive", state="idle"):
    return {"id": area_id, "name": name, "bundle": f"{area_id}-bundle", "data_class": data_class, "degraded": [],
            "state": state, "label": "x", "worker": "-", "since": None, "members": members}


def example_scene():
    """A generic office: a coordinator, four areas and one relation of each kind (plus one that points nowhere)."""
    return {
        "schema": "maf.office-scene/1",
        "generated_at": "2026-11-25T12:00:00+00:00",
        "office": {"id": "example-office", "name": "Example Office", "state": "ready"},
        "orchestrator": {"enabled": True, "name": "Coordinator", "state": "doing", "label": "x", "since": None,
                         "plan": {"proposal_id": "p1", "status": "approved", "tasks": 3, "done": 2, "href": "#new/plan/p1"}},
        "areas": [
            area("sales", "Sales", [member("m1", "Agent One", state="review"), member("m2", "Person Two", "person", "doing")],
                 state="review"),
            area("stock", "Stock", [member("m3", "Agent Three", state="failed"), member("m4", "Agent Four")],
                 data_class="restricted", state="failed"),
            area("support", "Support", [member("m5", "Agent Five", state="done")], state="done"),
            area("shipping", "Shipping", [member("m6", "Agent Six", state="doing"), member("m7", "Person Seven", "person")],
                 data_class="sensitive", state="doing"),
        ],
        "relations": [
            {"from": "sales", "to": "stock", "kind": "hands_off", "label": "entrega", "warning": None},
            {"from": "stock", "to": "support", "kind": "reviews", "label": "revisa", "warning": None},
            {"from": "support", "to": "sales", "kind": "consults", "label": "consulta", "warning": None},
            {"from": "sales", "to": "gone", "kind": "hands_off", "label": "entrega", "warning": "office_relation_unknown_area"},
        ],
        "truncated": False,
    }


def many_areas_scene(count, members_each):
    scene = example_scene()
    scene["areas"] = [area(f"a{n}", f"Area {n}", [member(f"a{n}m{k}", f"Agent {n}-{k}") for k in range(members_each)])
                      for n in range(count)]
    scene["relations"] = []
    return scene


def run_node(script: str):
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def url(path) -> str:
    return json.dumps(path.as_uri())


def layouts_for(scenes: dict):
    return run_node(f"""
      const {{ layoutScene }} = await import({url(FOLDER / "layout.js")});
      const scenes = {json.dumps(scenes)};
      console.log(JSON.stringify(Object.fromEntries(Object.entries(scenes).map(([name, scene]) => [name, layoutScene(scene)]))));
    """)


def overlaps(a, b):
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def inside(box, bounds):
    return box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["w"] <= bounds["width"] and box["y"] + box["h"] <= bounds["height"]


# --- the folder follows the extension rules (the shared guards run on it too) ----------------------------------------

def test_the_folder_has_the_files_of_the_ticket_and_registers_one_simple_view():
    for name in ("index.js", "model.js", "scene-poller.js", "layout.js", "help.js", "i18n.js", "style.css"):
        assert (FOLDER / name).is_file(), name
    source = (FOLDER / "index.js").read_text(encoding="utf-8")
    call = re.search(r"registerView\(\{(.*?)\}\);", source, re.S)[1]
    assert re.search(r'\bowner:\s*"floor_plan"', call) and re.search(r'\bmode:\s*"simple"', call)
    assert re.search(r'\bicon:\s*"map"', call) and re.search(r'\bid:\s*"floor_plan"', call)
    assert 'registerHelp(HELP, "floor_plan")' in source and re.search(r'^import "\./i18n\.js";$', source, re.M)
    assert not re.search(r"<h[123]\b", source) and 'aria-label=${t("ext.floor_plan.aria")}' in source


def test_the_plan_only_reads_the_scene_and_never_stores_the_zoom():
    sources = {path.name: path.read_text(encoding="utf-8") for path in FOLDER.glob("*.js")}
    for name, text in sources.items():
        assert "apiPost" not in text and "localStorage" not in text and "sessionStorage" not in text, name
        assert 'method: "POST"' not in text and "fetch(" not in text, name
    assert '"/scene"' in sources["index.js"] and "apiGet(scenePath(officeId), token)" in sources["index.js"]


@needs_node
def test_the_extension_loads_and_registers_its_view_and_help_in_both_languages():
    out = run_node(f"""
      import {{ pathToFileURL }} from "node:url";
      const ui = await import({url(JS / "i18n.js")});
      ui.setLanguage("es");
      const reg = await import({url(JS / "extensions.js")});
      const base = {json.dumps(str(STATIC_DIR))};
      const report = await reg.loadExtensions({{
        fetchManifest: async () => ({{ extensions: [{{ id: "floor_plan", script: "/gui/static/extensions/floor_plan/index.js", style: null }}] }}),
        importModule: (target) => import(pathToFileURL(base + target.replace("/gui/static", "")).href),
        addStylesheet: async () => {{}},
      }});
      const view = reg.registeredViews().find((v) => v.id === "floor_plan");
      const topics = ["view", "tabs", "ask", "zoom", "layers", "node"].map((name) => "floor_plan." + name);
      const help = Object.fromEntries(topics.map((topic) => [topic, Boolean(reg.registeredHelp(topic))]));
      const spanish = Object.fromEntries(topics.map((topic) => [topic, ui.t("help." + topic).title]));
      console.log(JSON.stringify({{ failed: report.failed, view: {{ label: view.label, icon: view.icon, mode: view.mode,
        topic: view.topic, group: view.group }}, help, spanish }}));
    """)
    assert out["failed"] == []
    assert out["view"] == {"label": "Plano", "icon": "map", "mode": "simple", "topic": "floor_plan.view", "group": "more"}
    assert all(out["help"].values()) and all(isinstance(title, str) and title for title in out["spanish"].values())


def test_literal_text_keys_of_the_extension_exist_in_both_dictionaries():
    keys = set(re.findall(r'"(ext\.floor_plan\.[a-z_.]+)":', (FOLDER / "i18n.js").read_text(encoding="utf-8")))
    used = set()
    for path in FOLDER.glob("*.js"):
        used.update(re.findall(r'\bt\(\s*"(ext\.floor_plan\.[a-z_.]+)"', path.read_text(encoding="utf-8")))
    used.update(re.findall(r'"(ext\.floor_plan\.[a-z_.]+)"', (FOLDER / "model.js").read_text(encoding="utf-8")))
    used = {key for key in used if not key.endswith(".")}        # "ext.floor_plan.layer." + a layer: the four are listed below
    used |= {f"ext.floor_plan.layer.{layer}" for layer in ("assignment", "handoff", "review", "question")}
    assert used and used <= keys, used - keys


# --- layout: everything fits, nothing overlaps, every relation is a finite curve ------------------------------------

@needs_node
def test_example_scene_layout_places_every_zone_node_and_relation_inside_the_plan():
    layout = layouts_for({"example": example_scene()})["example"]
    bounds = layout["bounds"]
    assert bounds["width"] == 900 and bounds["height"] >= 600
    zones = layout["zones"]
    assert [zone["id"] for zone in zones] == ["sales", "stock", "support", "shipping"]
    boxes = [*zones, layout["coordinator"]]
    assert all(inside(box, bounds) for box in boxes)
    assert not any(overlaps(a, b) for index, a in enumerate(boxes) for b in boxes[index + 1:])
    assert sum(len(zone["nodes"]) for zone in zones) == 7
    for zone in zones:
        assert all(inside(node, bounds) and node["x"] >= zone["x"] and node["x"] + node["w"] <= zone["x"] + zone["w"]
                   and node["y"] >= zone["y"] and node["y"] + node["h"] <= zone["y"] + zone["h"] for node in zone["nodes"])
        assert not any(overlaps(a, b) for index, a in enumerate(zone["nodes"]) for b in zone["nodes"][index + 1:])
    layers = sorted(relation["layer"] for relation in layout["relations"])
    assert layers == ["assignment"] * 4 + ["handoff", "question", "review"]     # the relation to an unknown area is not drawn
    assert len({relation["key"] for relation in layout["relations"]}) == len(layout["relations"])
    for relation in layout["relations"]:
        assert re.fullmatch(r"M[-\d. ]+(?:L[-\d. ]+|Q[-\d. ]+)", relation["d"]), relation["d"]
        assert "NaN" not in relation["d"] and "undefined" not in relation["d"]


@needs_node
def test_layout_is_deterministic_and_handles_empty_small_and_large_offices():
    scenes = {"example": example_scene(), "empty": {**example_scene(), "areas": [], "relations": []},
              "one": {**many_areas_scene(1, 2)}, "two": many_areas_scene(2, 1), "ten": many_areas_scene(10, 5),
              "quiet": {**example_scene(), "orchestrator": {"enabled": False}}}
    first, second = layouts_for(scenes), layouts_for(scenes)
    assert first == second
    assert first["empty"]["zones"] == [] and first["empty"]["coordinator"] is not None
    assert first["empty"]["relations"] == []
    assert first["quiet"]["coordinator"] is None
    assert [relation["layer"] for relation in first["quiet"]["relations"]].count("assignment") == 0
    for name in ("one", "two", "ten"):
        layout = first[name]
        boxes = [*layout["zones"], layout["coordinator"]]
        assert all(inside(box, layout["bounds"]) for box in boxes), name
        assert not any(overlaps(a, b) for index, a in enumerate(boxes) for b in boxes[index + 1:]), name
    assert len(first["ten"]["zones"]) == 10
    assert first["ten"]["bounds"]["height"] > 600          # a long office grows the plan, it does not squeeze it
    assert sum(1 for relation in first["ten"]["relations"] if relation["layer"] == "assignment") == 10


@needs_node
def test_relations_between_the_same_two_areas_fan_out_instead_of_hiding_each_other():
    scene = example_scene()
    scene["relations"] = [{"from": "sales", "to": "stock", "kind": kind, "label": "x", "warning": None}
                          for kind in ("hands_off", "reviews", "consults")]
    layout = layouts_for({"scene": scene})["scene"]
    paths = [relation["d"] for relation in layout["relations"] if relation["layer"] != "assignment"]
    assert len(paths) == 3 and len(set(paths)) == 3


# --- fit and zoom ------------------------------------------------------------------------------------------------------

@needs_node
def test_the_plan_opens_fitted_and_zoom_stays_between_fit_and_six_times():
    out = run_node(f"""
      const model = await import({url(FOLDER / "model.js")});
      const bounds = {{ width: 900, height: 600 }};
      const fit = model.fitView(bounds);
      let view = null;
      const steps = [];
      for (let i = 0; i < 12; i += 1) {{ view = model.zoomView(view, bounds, model.ZOOM_IN_FACTOR); steps.push(view && view.w); }}
      const zoomedOut = model.zoomView(view, bounds, model.ZOOM_OUT_FACTOR);
      let back = view;
      for (let i = 0; i < 20; i += 1) back = model.zoomView(back, bounds, model.ZOOM_OUT_FACTOR);
      const corner = model.zoomView(null, bounds, 0.5, {{ x: 0, y: 0 }});
      const edge = model.zoomView(null, bounds, 0.5, {{ x: 1, y: 1 }});
      console.log(JSON.stringify({{
        fit, fitText: model.viewBoxText(fit), steps, last: view, zoomedOutW: zoomedOut && zoomedOut.w, back,
        corner, edge, canIn: [model.canZoomIn(null, bounds), model.canZoomIn(view, bounds)],
        canOut: [model.canZoomOut(null, bounds), model.canZoomOut(view, bounds)],
        zoomOutFromFit: model.zoomView(null, bounds, model.ZOOM_OUT_FACTOR),
        level: model.zoomLevel(view, bounds),
      }}));
    """)
    assert out["fit"] == {"x": 0, "y": 0, "w": 900, "h": 600} and out["fitText"] == "0 0 900 600"
    assert out["steps"][0] == pytest.approx(720) and out["steps"][-1] == pytest.approx(150)      # 900 / 6
    assert out["last"]["h"] == pytest.approx(100) and out["zoomedOutW"] == pytest.approx(187.5)
    assert out["back"] is None                      # zooming out all the way is "fit" again
    assert out["corner"] == {"x": 0, "y": 0, "w": 450, "h": 300} and out["edge"] == {"x": 450, "y": 300, "w": 450, "h": 300}
    assert out["canIn"] == [True, False] and out["canOut"] == [False, True]
    assert out["zoomOutFromFit"] is None and out["level"] == pytest.approx(6)


@needs_node
def test_pan_is_kept_inside_the_plan_and_a_fitted_plan_does_not_move():
    out = run_node(f"""
      const model = await import({url(FOLDER / "model.js")});
      const bounds = {{ width: 900, height: 600 }};
      const zoomed = model.zoomView(null, bounds, 0.5, {{ x: 0.5, y: 0.5 }});
      console.log(JSON.stringify({{
        zoomed,
        fitted: model.panView(null, bounds, 50, 50),
        left: model.panView(zoomed, bounds, -9999, -9999),
        right: model.panView(zoomed, bounds, 9999, 9999),
        small: model.panView(zoomed, bounds, 10, -10),
        keys: ["+", "-", "0", "ArrowLeft", "ArrowDown", "a"].map(model.keyAction),
        grown: model.clampView({{ x: 400, y: 300, w: 450, h: 300 }}, {{ width: 900, height: 900 }}),
      }}));
    """)
    assert out["zoomed"] == {"x": 225, "y": 150, "w": 450, "h": 300}
    assert out["fitted"] is None
    assert out["left"] == {"x": 0, "y": 0, "w": 450, "h": 300} and out["right"] == {"x": 450, "y": 300, "w": 450, "h": 300}
    assert out["small"] == {"x": 235, "y": 140, "w": 450, "h": 300}
    assert [key and key["type"] for key in out["keys"]] == ["zoom", "zoom", "fit", "pan", "pan", None]
    assert out["grown"]["w"] == 450 and out["grown"]["h"] == 450       # the aspect follows the (taller) plan


@needs_node
def test_screen_points_map_to_the_plan_even_with_bands_around_it():
    out = run_node(f"""
      const model = await import({url(FOLDER / "model.js")});
      const view = {{ x: 0, y: 0, w: 900, h: 600 }};
      const wide = {{ left: 100, top: 50, width: 1200, height: 600 }};      // 1200x600 box: the 3:2 drawing is 900x600, bands at the sides
      const tall = {{ left: 0, top: 0, width: 300, height: 400 }};           // 300x400 box: the drawing is 300x200, bands above and below
      const pinch = model.pinchOf({{ x: 0, y: 0 }}, {{ x: 30, y: 40 }});
      console.log(JSON.stringify({{
        centre: model.pointToFraction(view, wide, 700, 350),
        bandLeft: model.pointToFraction(view, wide, 110, 350),
        topLeftOfDrawing: model.pointToFraction(view, tall, 0, 100),
        scale: [model.unitsPerPixel(view, wide), model.unitsPerPixel(view, tall)],
        pinch, moved: [model.movedEnough({{ x: 0, y: 0 }}, {{ x: 2, y: 2 }}), model.movedEnough({{ x: 0, y: 0 }}, {{ x: 5, y: 3 }})],
      }}));
    """)
    assert out["centre"] == {"x": 0.5, "y": 0.5} and out["bandLeft"]["x"] == 0
    assert out["topLeftOfDrawing"] == {"x": 0, "y": 0}
    assert out["scale"] == [pytest.approx(1.0), pytest.approx(3.0)]
    assert out["pinch"] == {"distance": 50, "middle": {"x": 15, "y": 20}}
    assert out["moved"] == [False, True]


# --- the list equivalent and the texts ----------------------------------------------------------------------------------

@needs_node
def test_the_list_equivalent_names_every_area_position_and_relation_in_both_languages():
    scene = example_scene()
    out = run_node(f"""
      const ui = await import({url(JS / "i18n.js")});
      const model = await import({url(FOLDER / "model.js")});
      const {{ layoutScene }} = await import({url(FOLDER / "layout.js")});
      await import({url(FOLDER / "i18n.js")});
      const scene = {json.dumps(scene)};
      const layout = layoutScene(scene);
      const read = () => ({{
        list: model.describeScene(scene, layout.relations),
        node: model.nodeLabel(scene.areas[0].members[0], scene.areas[0]),
        label: model.planLabel(scene, layout),
        privacy: scene.areas.map((a) => model.privacyText(a.data_class)),
        selected: model.selectedLine({{ kind: "member", name: "Agent One", area: "Sales", state: model.stateInfo("review").text }}),
        states: ["review", "changes", "failed", "blocked", "doing", "ready", "todo", "done", "cancelled", "idle", "weird"]
          .map((s) => model.stateInfo(s)),
        clipped: [model.clip("short", 10), model.clip("a very long position name", 10), model.clip("", 5)],
      }});
      ui.setLanguage("en");
      const en = read();
      ui.setLanguage("es");
      const es = read();
      console.log(JSON.stringify({{ en, es, tones: en.states.map((s) => s.tone) }}));
    """)
    en, es = out["en"], out["es"]
    assert len(en["list"]) == 1 + 4 + 3 and len(es["list"]) == len(en["list"])        # coordinator, areas, relations
    joined = "\n".join(en["list"])
    for name in ("Sales", "Stock", "Support", "Shipping", "Agent One", "Person Two", "Agent Three", "Agent Four", "Agent Five",
                 "Agent Six", "Person Seven"):
        assert name in joined, name
    assert en["list"][0] == "Coordinator: Working, 2 of 3 tasks."
    assert "Handoff: Sales to Stock." in joined and "Question: Support to Sales." in joined
    assert en["node"] == "Agent One, Sales: Needs your OK" and en["label"] == "Example Office floor plan. Areas: 4."
    assert en["privacy"] == ["Privacy: Normal", "Privacy: Local only", "Privacy: Normal", "Privacy: Sensitive"]
    assert es["list"][0] == "Coordinador: Trabajando, 2 de 3 tareas."
    assert "Traspaso: de Sales a Stock." in "\n".join(es["list"])
    assert es["node"] == "Agent One, Sales: Necesita tu visto bueno"
    assert es["privacy"][1] == "Privacidad: Solo en este equipo" and es["label"] == "Plano de Example Office. Áreas: 4."
    assert es["selected"] == "Agent One en Sales: Necesita tu visto bueno"
    assert out["tones"] == ["wait", "wait", "bad", "bad", "busy", "busy", "idle", "ok", "idle", "idle", "idle"]
    assert en["states"][-1]["text"] == "Unknown"
    assert en["clipped"] == ["short", "a very lo…", ""]


@needs_node
def test_a_scene_is_usable_only_with_its_schema_and_an_area_list():
    out = run_node(f"""
      const model = await import({url(FOLDER / "model.js")});
      const good = {json.dumps(example_scene())};
      console.log(JSON.stringify([model.sceneUsable(good), model.sceneUsable(null), model.sceneUsable({{}}),
        model.sceneUsable({{ ...good, schema: "maf.office-scene/2" }}), model.sceneUsable({{ ...good, areas: null }})]));
    """)
    assert out == [True, False, False, False, False]


# --- the poller: one request at a time, a hidden tab waits ---------------------------------------------------------------

@needs_node
def test_the_scene_poller_keeps_one_request_in_flight_and_waits_while_the_tab_is_hidden():
    out = run_node(f"""
      const {{ createScenePoller, POLL_MS }} = await import({url(FOLDER / "scene-poller.js")});
      let ticks = [];
      let resolvers = [];
      let calls = 0;
      let visible = true;
      const scenes = [];
      const failures = [];
      const poller = createScenePoller({{
        fetchScene: () => {{ calls += 1; return new Promise((resolve, reject) => resolvers.push({{ resolve, reject }})); }},
        onScene: (scene) => scenes.push(scene),
        onFailure: (error) => failures.push(String(error)),
        isVisible: () => visible,
        setIntervalFn: (fn, ms) => {{ ticks.push(fn); return ticks.length; }},
        clearIntervalFn: () => {{ ticks = []; }},
      }});
      const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
      const log = {{}};
      poller.start();
      poller.start();                                  // a second start does nothing
      log.afterStart = calls;
      ticks[0](); ticks[0](); poller.refresh();         // three more asks while the first is still in flight
      log.inFlight = calls;
      resolvers[0].resolve({{ n: 1 }}); await settle();
      log.afterFirst = [calls, scenes.length];
      ticks[0](); log.nextTick = calls;                 // the next tick asks again
      resolvers[1].resolve({{ n: 2 }}); await settle();
      visible = false;
      ticks[0](); poller.refresh(); log.hidden = calls;  // hidden: no request
      visible = true;
      poller.refresh(); log.visibleAgain = calls;        // visible again: asks at once
      resolvers[2].reject(new Error("offline")); await settle();
      log.failures = failures.length;
      ticks[0](); resolvers[3].resolve({{ n: 4 }});
      poller.stop(); await settle();                     // a late answer after stop() is dropped
      log.afterStop = [scenes.length, ticks.length];
      log.interval = POLL_MS;
      console.log(JSON.stringify(log));
    """)
    assert out["afterStart"] == 1 and out["inFlight"] == 1
    assert out["afterFirst"] == [1, 1] and out["nextTick"] == 2
    assert out["hidden"] == 2 and out["visibleAgain"] == 3 and out["failures"] == 1
    assert out["afterStop"] == [2, 0] and out["interval"] == 4000


def test_the_plan_pauses_its_poll_with_the_page_and_wakes_on_visibility_change():
    source = (FOLDER / "index.js").read_text(encoding="utf-8")
    assert 'document.visibilityState !== "hidden"' in source
    assert 'addEventListener("visibilitychange", wake)' in source and 'removeEventListener("visibilitychange", wake)' in source
    assert "poller.stop()" in source and "poller.start()" in source
