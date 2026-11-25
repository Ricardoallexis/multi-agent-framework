# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI project view (PRY-T03): static checks of the extension (code, styles, help, accessibility, small screens), the data
rules of ``model.js`` run in Node over the real answers of the API, and the token-protected routes under /gui/api/projects.

The extension lives in ``static/extensions/project/`` (FLU-T04 convention) and is held by tests/test_gui_extensions.py and
tests/test_gui_help.py like every other one; this file adds what is specific to the project view."""
from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from multiagent.gui.extensions import discover_extensions
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from test_gui_extensions import check_extension_code, check_extension_styles
from test_gui_help import NODE, check_extensions, extension_help
from test_gui_sessions_view import FORBIDDEN

PROJECT_DIR = STATIC_DIR / "extensions" / "project"
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

SYNTHETIC_PLAN = {
    "schema": "maf.project-template/1",
    "id": "synthetic-campaign",
    "title": "Campaña Sintética",
    "description": "Plan sintético para validar la pantalla de proyectos.",
    "stages": [
        {
            "code": "INV",
            "title": "Investigación",
            "phases": ["Fuentes", "Análisis"],
            "planned": {"start": "2026-11-01", "end": "2026-11-15"},
            "tasks": [
                {"id": "T01", "title": "Estudio de audiencia", "objective": "Identificar tres segmentos clave.",
                 "phase": "Fuentes", "assignee": "agent:writer/m1", "done_when": "Existe un informe con 3 segmentos"},
                {"id": "T02", "title": "Análisis de competidores", "objective": "Evaluar competidores directos.",
                 "phase": "Análisis", "depends_on": ["T01"], "due": "2026-11-10", "assignee": "agent:writer/m1"},
                {"id": "T03", "title": "Notas sueltas", "objective": "Una tarea sin fase.", "assignee": "user:ana"},
            ],
        },
        {
            "code": "CON",
            "title": "Contenidos",
            "phases": ["Redacción"],
            "tasks": [
                {"id": "T01", "title": "Calendario editorial", "objective": "Planificar las publicaciones del mes.",
                 "phase": "Redacción", "depends_on": ["stage:INV"], "assignee": "user:ana"},
            ],
        },
    ],
}


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        _env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True, gemini_api_key="",
        openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(settings, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def auth(env):
    return {"Authorization": f"Bearer {env.token}"}


def submit(env, task_id: str, summary: str, *, with_patch: bool) -> None:
    """The assignee takes the task, starts it and sends a result; the blob of the patch is stored first (put_blob)."""
    agent = "agent:writer/m1"
    env.services.tasks.transition(task_id, "claim", actor=agent)
    env.services.tasks.transition(task_id, "start", actor=agent)
    result = {"summary": summary, "decisions": ["Segmento principal: equipos pequeños"], "tests_not_run": ["Revisión editorial"]}
    if with_patch:
        blob = env.services.envelopes.put_blob(b"informe de audiencia\n", actor=agent, media_type="text/plain")
        result["patch"] = {"blob": "sha256:" + blob["sha256"], "files": ["docs/audiencia.md"]}
    env.services.envelopes.submit_result(task_id, result, actor=agent)


def make_project(env, key: str = "snt", *, submitted: bool = True) -> dict:
    """A synthetic project: INV is authorized and its first task is delivered, waiting for the review (when ``submitted``)."""
    doc = copy.deepcopy(SYNTHETIC_PLAN)
    plan = env.services.projects.plan(doc, key=key)
    project = env.services.projects.create(doc, key=key, plan_sha256=plan["plan_sha256"], actor="user:ana")
    env.services.tasks.update_group(f"{key}-INV", actor="user:ana", authorized=True)
    if submitted:
        submit(env, f"{key}-INV-T01", "Informe de audiencia terminado.", with_patch=True)
    return project


def review(env, task_id: str, body: dict, project: str = "snt", headers=None):
    return env.client.post(f"/gui/api/projects/{project}/tasks/{task_id}/review",
                           headers=headers if headers is not None else auth(env), json=body)


# --- the extension folder: files, discovery, code, styles, help ---------------------------------------------------------

def test_project_extension_files_exist_and_are_discovered():
    for name in ("index.js", "parts.js", "model.js", "help.js", "style.css"):
        assert (PROJECT_DIR / name).is_file(), name
    entries = [entry for entry in discover_extensions(STATIC_DIR) if entry["id"] == "project"]
    assert len(entries) == 1
    assert entries[0]["script"] == "/gui/static/extensions/project/index.js"
    assert entries[0]["style"] == "/gui/static/extensions/project/style.css"


def test_project_code_follows_the_extension_rules():
    check_extension_code(STATIC_DIR)       # no html injection, eval, inline style or handler; imports only its folder, js/ and vendor/
    for path in sorted(PROJECT_DIR.rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for pattern, label in FORBIDDEN:
            assert not re.search(pattern, text), f"{path.name}: {label}"


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def app_variables() -> set[str]:
    return set(re.findall(r"(--[\w-]+)\s*:", strip_comments((STATIC_DIR / "app.css").read_text(encoding="utf-8"))))


def test_project_styles_are_scoped_and_use_only_colours_that_exist():
    check_extension_styles(STATIC_DIR)     # selectors scoped to .project-*, no hex or rgb colours, no url(), no @import
    css = strip_comments((PROJECT_DIR / "style.css").read_text(encoding="utf-8"))
    for prelude, _ in re.findall(r"([^{}]+)\{([^{}]*)\}", css):       # comments are stripped first: they are not selectors
        if prelude.strip().startswith("@"):
            continue
        for part in (p.strip() for p in prelude.split(",")):
            assert re.match(r"\.project-", part), f"selector '{part}' must start from a .project-* class"
    # A variable that app.css does not define makes the colour vanish (no light/dark): every var(--x) must exist there.
    used = set(re.findall(r"var\(\s*(--[\w-]+)", css))
    assert used, "the sheet uses no variable of app.css"
    assert used <= app_variables(), f"variables that app.css does not define: {sorted(used - app_variables())}"
    assert {"--line", "--panel", "--text"} <= used      # borders, surface and text really come from the theme


def test_project_styles_fit_a_phone():
    css = strip_comments((PROJECT_DIR / "style.css").read_text(encoding="utf-8"))
    assert re.search(r"@media\s*\(max-width:\s*599\.98px\)", css), "no rule for a phone"
    assert "overflow-wrap" in css and "min-width: 0" in css, "long words must wrap and flex children must shrink"
    assert "white-space: nowrap" not in css, "nowrap makes a line scroll sideways"
    declarations = " ".join(re.findall(r"\{([^{}]*)\}", css))        # the rules' bodies; a query's own condition is not a width
    assert not re.search(r"(?<![-\w])(?:min-)?width:\s*\d{3,}px", declarations), "a fixed width of 100 px or more does not fit 375 px"
    assert "var(--target)" in css, "controls keep the 44 px hit area"
    # The two columns start only when the view itself is wide: the panels around it change how much room it has.
    assert re.search(r"@container\s*\(min-width:", css) and "container-type: inline-size" in css


def test_project_help_follows_the_rules_and_every_text_is_used():
    seen = check_extensions(STATIC_DIR)      # help or exemption on every control, heading help, topics defined and used
    assert seen["extensions"] >= 1
    entries = {key: value for key, value in extension_help(STATIC_DIR).items() if key.startswith("project.")}
    assert len(entries) >= 15
    sources = "".join(path.read_text(encoding="utf-8") for path in PROJECT_DIR.glob("*.js") if path.name != "help.js")
    for key in entries:
        assert f'"{key}"' in sources, f"help text {key} is never used"


def test_project_view_is_accessible_by_construction():
    index = ui_text((PROJECT_DIR / "index.js"))
    parts = ui_text((PROJECT_DIR / "parts.js"))
    # The shared components draw role=tabpanel / role=progressbar (tests/test_gui2_components.py); the view must use them.
    assert "<${Tabs}" in index and "helpFor(\"project.tabs\")" in index
    assert parts.count("<${Progress}") >= 1 and "<${Progress}" in index
    # Collapsible phases say whether they are open and what they control.
    assert "aria-expanded" in parts and "aria-controls" in parts
    # Errors and the result of a decision are announced; the detail takes the focus when it opens.
    assert 'role="alert"' in index + parts and 'role="status"' in parts
    assert "heading.current.focus()" in index and 'tabindex="-1"' in parts
    # Every field has a visible label tied to it, and a status is never colour alone: the chip always carries the words.
    for source in (index, parts):
        for tag in re.finditer(r"<(?:input|select|textarea)\b[^>]*?\bid=\"([\w-]+)\"", source):
            assert f'for="{tag[1]}"' in source, f"{tag[1]} has no <label for>"
    assert "StatusChip" in parts and "chipOf(" in parts
    # Short names: the project key prefix is hidden everywhere an id is shown.
    assert "shortId(" in parts


def test_the_panel_card_follows_the_screen_and_cleans_up_after_itself():
    index = ui_text((PROJECT_DIR / "index.js"))
    assert index.count('addEventListener(CHANGED') == 1 and index.count('removeEventListener(CHANGED') == 1
    assert "announce()" in index and "announce)" in index          # after a decision and after «Actualizar»
    assert "alive = false" in index                                 # an answer that arrives after the card closed is dropped


def test_project_view_has_no_hand_made_copy_of_the_label_table():
    # The words and tones of the states come from the core with every task (label.text / label.tone): no table in the page.
    for name in ("index.js", "parts.js", "model.js"):
        text = ui_text((PROJECT_DIR / name))
        assert 'labels="run"' not in text and "Bloqueada" not in text and "Terminada" not in text, name


# --- the data rules of model.js, run in Node over the real answers of the API ------------------------------------------------

def run_node(script: str) -> dict:
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@needs_node
def test_model_rules_over_the_real_project_answer(env):
    make_project(env)
    project = env.client.get("/gui/api/projects/snt", headers=auth(env)).json()
    detail = env.client.get("/gui/api/projects/snt/tasks/snt-INV-T01", headers=auth(env)).json()
    waiting = env.client.get("/gui/api/projects/snt/tasks/snt-INV-T02", headers=auth(env)).json()
    assert "phase" not in detail and "due_at" not in detail and "assignee_info" not in waiting     # why the page merges (detailOf)
    out = run_node(f"""
      import * as m from {json.dumps((PROJECT_DIR / "model.js").as_uri())};
      const project = {json.dumps(project)};
      const detail = {json.dumps(detail)};
      const waiting = {json.dumps(waiting)};
      const inv = project.stages[0];
      const noText = {{ text: "", label: "" }};
      const groups = m.phaseGroups(inv, inv.tasks.filter((task) => m.matchesFilter(task, noText)));
      const filtered = m.phaseGroups(inv, inv.tasks.filter((task) => m.matchesFilter(task, {{ text: "AUDIENCIA", label: "" }})));
      console.log(JSON.stringify({{
        shorts: [m.shortId("snt-INV-T01", "snt"), m.shortId("otro-INV-T01", "snt"), m.shortId("snt-INV-T01", "")],
        bar: m.progressOf(project.progress), empty: m.progressOf({{ done: 0, total: 0, cancelled: 0, percent: null }}),
        cancelledAside: m.progressOf({{ done: 1, total: 3, cancelled: 1, percent: 50 }}),
        chip: m.chipOf(project.stages[0].tasks[0].label), noChip: m.chipOf(null),
        summary: m.labelSummary(project).map((item) => [item.label.key, item.count]),
        groups: groups.map((g) => [g.name, g.tasks.map((t) => t.id), g.all.length]),
        filtered: filtered.map((g) => [g.name, g.tasks.map((t) => t.id), g.all.length]),
        byLabel: inv.tasks.filter((t) => m.matchesFilter(t, {{ text: "", label: "blocked" }})).map((t) => t.id),
        canReview: [m.canReview(detail), m.canReview(inv.tasks[1]), m.canReview(null)],
        who: inv.tasks.map((t) => m.whoText(t)), due: inv.tasks.map((t) => m.dayText(t.due_at)),
        plan: [m.planText(inv), m.planText(project.stages[1]), m.dayText("2026-11-15"), m.dayText(null), m.dayText("2026-13-40")],
        deliverables: m.deliverables(detail.result), noDeliverables: m.deliverables(null),
        merged: [m.detailOf(project, detail), m.detailOf(project, waiting), m.detailOf(project, null), m.detailOf(project, {{ id: "x" }})]
          .map((task) => task && [task.phase, m.whoText(task), m.dayText(task.due_at), task.label && task.label.key, task.id]),
        names: [m.taskName("snt-INV-T01", project), m.taskName("snt-NOPE", project)],
        pick: [m.pickProject([], ""), m.pickProject([{{ id: "a", needs_you: {{}} }}, {{ id: "b", needs_you: {{ reviews: 2 }} }}], ""),
               m.pickProject([{{ id: "a" }}, {{ id: "b" }}], "b"), m.pickProject([{{ id: "a" }}], "zzz")],
        needs: [m.needsText({{ reviews: 1, blocked: 2 }}), m.needsText({{}}), m.needsText(undefined)],
        requires: [m.requiresText("accepted"), m.requiresText("submitted"), m.requiresText(undefined)],
        garbage: [m.list(null), m.list({{}}), m.phaseGroups({{}}, []).length],
      }}));
    """)
    assert out["shorts"] == ["INV-T01", "otro-INV-T01", "snt-INV-T01"]
    assert out["bar"] == {"value": 0, "max": 4, "valueText": "0 de 4 tareas terminadas (0 %)"}
    assert out["empty"]["value"] is None and "Sin tareas" in out["empty"]["valueText"]
    assert out["cancelledAside"]["max"] == 2           # the cancelled task does not count (PRY-T01 section 3)
    # The chip carries the core's own words and tone for the label (the delivered task waits for the review).
    assert out["chip"] == {"status": "review", "labels": {"review": {"text": "Espera tu revisión", "tone": "attention"}}, "detail": ""}
    assert out["noChip"]["status"] == "none" and out["noChip"]["labels"]["none"]["text"] == "Sin etiqueta"
    keys = [key for key, _ in out["summary"]]
    assert keys == sorted(keys, key=["review", "changes", "doing", "ready", "blocked", "todo", "done", "failed", "cancelled"].index)
    assert dict(out["summary"]) == {"review": 1, "ready": 1, "blocked": 1, "todo": 1}
    # Every task of the stage is in some group: the one without a phase does not vanish.
    assert out["groups"] == [["Fuentes", ["snt-INV-T01"], 1], ["Análisis", ["snt-INV-T02"], 1], ["Sin fase", ["snt-INV-T03"], 1]]
    # The filter hides tasks but keeps the phase and says how many there are in all.
    assert out["filtered"] == [["Fuentes", ["snt-INV-T01"], 1], ["Análisis", [], 1], ["Sin fase", [], 1]]
    assert out["byLabel"] == ["snt-INV-T02"]
    assert out["canReview"] == [True, False, False]
    assert out["who"] == ["writer / m1", "writer / m1 (sugerido)", "ana (sugerido)"]   # the first task was taken
    assert out["due"] == ["Sin fecha", "10 nov 2026", "Sin fecha"]
    assert out["plan"] == ["Previsto: 1 nov 2026 a 15 nov 2026", "Sin fechas previstas", "15 nov 2026", "Sin fecha", "Sin fecha"]
    delivered = out["deliverables"]
    assert delivered["summary"] == "Informe de audiencia terminado." and delivered["files"] == ["docs/audiencia.md"]
    assert delivered["decisions"] == ["Segmento principal: equipos pequeños"] and delivered["testsNotRun"] == ["Revisión editorial"]
    assert out["noDeliverables"] is None
    assert out["merged"] == [["Fuentes", "writer / m1", "Sin fecha", "review", "snt-INV-T01"],
                             ["Análisis", "writer / m1 (sugerido)", "10 nov 2026", "blocked", "snt-INV-T02"], None,
                             [None, "Sin asignar", "Sin fecha", None, "x"]]        # a task the project does not list stays as it came
    assert out["names"] == ["INV-T01 · Estudio de audiencia", "NOPE"]        # an id the project does not know: only its short form
    assert out["pick"] == ["", "b", "b", "a"]
    assert out["needs"] == ["1 por revisar · 2 bloqueadas", "Nada pendiente de ti", "Nada pendiente de ti"]
    assert out["requires"] == ["aceptada", "entregada", "aceptada"]
    assert out["garbage"] == [[], [], 1]


@needs_node
def test_the_extension_registers_its_view_slot_and_help_when_loaded():
    out = run_node(f"""
      import * as registry from {json.dumps((STATIC_DIR / "js" / "extensions.js").as_uri())};
      await import({json.dumps((PROJECT_DIR / "index.js").as_uri())});
      const view = registry.registeredViews().find((v) => v.id === "project");
      const slot = registry.registeredSlots().find((s) => s.name === "project");
      console.log(JSON.stringify({{
        view: [view.label, typeof view.component], slot: [slot.title, slot.topic, typeof slot.component],
        help: [registry.registeredHelp("project.view").title, registry.registeredHelp("project.slot").title],
      }}));
    """)
    assert out["view"] == ["Proyectos", "function"] and out["slot"] == ["Proyecto", "project.slot", "function"]
    assert out["help"] == ["Proyectos", "Proyecto"]


# --- the routes under /gui/api/projects ------------------------------------------------------------------------------------

PAGE_READS_PROJECT = {
    "top": {"id", "title", "progress", "counts", "stages", "attention", "working_now", "milestones"},
    "progress": {"done", "cancelled", "total", "percent"},
    "stage": {"group_id", "code", "title", "label", "phases", "planned_start", "planned_end", "closed_at", "progress", "tasks"},
    "task": {"id", "title", "state", "label", "phase", "due_at", "assignee", "preferred", "assignee_info", "revision"},
    "attention": {"reviews", "verifications", "mine", "blocked"},
    "milestone": {"group_id", "code", "title", "planned", "actual"},
    "detail": {"id", "title", "objective", "label", "state", "revision", "attempt", "depends_on", "dependents", "result"},
    "list": {"id", "title", "progress", "needs_you"},
}


def test_project_routes_need_the_gui_token(env):
    make_project(env)
    for path in ("/projects", "/projects/snt", "/projects/snt/tasks/snt-INV-T01"):
        assert env.client.get("/gui/api" + path).status_code == 401
        assert env.client.get("/gui/api" + path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    body = {"action": "accept", "expected_revision": 1}
    assert env.client.post("/gui/api/projects/snt/tasks/snt-INV-T01/review", json=body).status_code == 401
    assert review(env, "snt-INV-T01", body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert env.services.tasks.get_task("snt-INV-T01")["state"] == "submitted"          # nothing was decided


def test_a_review_needs_a_json_body(env):
    make_project(env)
    res = env.client.post("/gui/api/projects/snt/tasks/snt-INV-T01/review", content="accept",
                          headers={**auth(env), "Content-Type": "text/plain"})
    assert res.status_code == 415
    assert env.services.tasks.get_task("snt-INV-T01")["state"] == "submitted"


def test_the_list_gives_progress_and_what_needs_the_user(env):
    make_project(env)
    res = env.client.get("/gui/api/projects", headers=auth(env))
    assert res.status_code == 200, res.text
    item = next(p for p in res.json()["projects"] if p["id"] == "snt")
    assert PAGE_READS_PROJECT["list"] <= set(item)
    assert item["needs_you"]["reviews"] == 1 and item["progress"]["total"] == 4
    assert env.client.get("/gui/api/projects?archived=true", headers=auth(env)).json() == {"projects": []}


def test_the_project_answer_has_every_field_the_page_reads(env):
    make_project(env)
    res = env.client.get("/gui/api/projects/snt", headers=auth(env))
    assert res.status_code == 200, res.text
    project = res.json()
    assert PAGE_READS_PROJECT["top"] <= set(project)
    assert PAGE_READS_PROJECT["progress"] <= set(project["progress"])
    assert PAGE_READS_PROJECT["attention"] <= set(project["attention"])
    for stage in project["stages"]:
        assert PAGE_READS_PROJECT["stage"] <= set(stage)
        for task in stage["tasks"]:
            assert PAGE_READS_PROJECT["task"] <= set(task)
            assert {"key", "text", "tone"} <= set(task["label"])        # the words and the tone come from the core
    for milestone in project["milestones"]:
        assert PAGE_READS_PROJECT["milestone"] <= set(milestone)
    # The summary: progress, what waits for the user (with the blockers), who works now and the stages.
    assert [stage["code"] for stage in project["stages"]] == ["INV", "CON"]
    assert [t["id"] for t in project["attention"]["reviews"]] == ["snt-INV-T01"]
    blocked = {t["id"]: t for t in project["attention"]["blocked"]}
    assert blocked["snt-INV-T02"]["blockers"][0]["task_id"] == "snt-INV-T01"
    assert {"task_id", "state", "requires"} <= set(blocked["snt-INV-T02"]["blockers"][0])
    assert project["working_now"] == []
    assert project["progress"] == {"done": 0, "cancelled": 0, "total": 4, "percent": 0}


def test_the_detail_has_dependencies_with_their_state_and_the_deliverables(env):
    make_project(env)
    delivered = env.client.get("/gui/api/projects/snt/tasks/snt-INV-T01", headers=auth(env))
    assert delivered.status_code == 200, delivered.text
    task = delivered.json()
    assert PAGE_READS_PROJECT["detail"] <= set(task)
    assert task["state"] == "submitted" and task["label"]["key"] == "review" and task["dependents"]
    assert task["result"]["summary"] == "Informe de audiencia terminado."
    assert task["result"]["patch"]["files"] == ["docs/audiencia.md"]
    assert task["result"]["decisions"] == ["Segmento principal: equipos pequeños"]
    waiting = env.client.get("/gui/api/projects/snt/tasks/snt-INV-T02", headers=auth(env)).json()
    assert waiting["result"] is None                                    # never submitted: no result, not an error
    dep = waiting["depends_on"][0]
    assert dep["task_id"] == "snt-INV-T01" and dep["requires"] == "accepted" and dep["satisfied"] is False


def test_reading_writes_nothing(env):
    make_project(env)
    before = env.services.tasks.events(after=0, limit=1000)
    for path in ("/projects", "/projects/snt", "/projects/snt/tasks/snt-INV-T01", "/projects/snt/tasks/snt-INV-T02"):
        assert env.client.get("/gui/api" + path, headers=auth(env)).status_code == 200
    assert env.services.tasks.events(after=0, limit=1000) == before


def test_unknown_project_and_task_are_404_with_the_core_error(env):
    make_project(env)
    res = env.client.get("/gui/api/projects/nope", headers=auth(env))
    assert res.status_code == 404 and res.json()["detail"]["code"] == "not_found"
    res = env.client.get("/gui/api/projects/snt/tasks/snt-INV-T99", headers=auth(env))
    assert res.status_code == 404 and res.json()["detail"]["code"] == "not_found"
    assert review(env, "snt-INV-T99", {"action": "accept", "expected_revision": 1}).status_code == 404


def test_a_task_of_another_project_is_not_found_through_this_one(env):
    make_project(env, "snt")
    make_project(env, "oth")
    assert env.client.get("/gui/api/projects/oth/tasks/oth-INV-T01", headers=auth(env)).status_code == 200
    res = env.client.get("/gui/api/projects/snt/tasks/oth-INV-T01", headers=auth(env))
    assert res.status_code == 404 and res.json()["detail"]["details"] == {"task_id": "oth-INV-T01", "project_id": "snt"}
    other = env.services.tasks.get_task("oth-INV-T01")
    res = review(env, "oth-INV-T01", {"action": "accept", "expected_revision": other["revision"]}, project="snt")
    assert res.status_code == 404
    assert env.services.tasks.get_task("oth-INV-T01")["state"] == "submitted"        # the review did not reach it


def test_only_the_routes_of_the_view_exist(env):
    make_project(env)
    task = env.services.tasks.get_task("snt-INV-T01")
    for method, path in (("get", "/gui/api/tasks/snt-INV-T01"), ("post", "/gui/api/tasks/snt-INV-T01/transition"),
                         ("post", "/gui/api/projects/snt/tasks/snt-INV-T01/transition"), ("get", "/gui/api/projects/snt/map")):
        kwargs = {"json": {"action": "cancel", "expected_revision": task["revision"]}} if method == "post" else {}
        assert getattr(env.client, method)(path, headers=auth(env), **kwargs).status_code in (404, 405), path
    assert env.services.tasks.get_task("snt-INV-T01")["state"] == "submitted"


def test_approving_accepts_the_task_as_the_operator_and_updates_the_project(env):
    make_project(env)
    task = env.services.tasks.get_task("snt-INV-T01")
    res = review(env, "snt-INV-T01", {"action": "accept", "expected_revision": task["revision"], "cause": "Aprobada"})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "accepted" and res.json()["label"]["key"] == "done"
    event = next(e for e in env.services.tasks.events(task_id="snt-INV-T01") if e["type"] == "task.accepted")
    assert event["actor"] == "user:operator" and event["cause"] == "Aprobada"
    project = env.client.get("/gui/api/projects/snt", headers=auth(env)).json()
    assert project["attention"]["reviews"] == [] and project["progress"]["done"] == 1
    # The accepted task released its dependent (INV-T02 is ready now), which the page shows on the next load.
    assert env.client.get("/gui/api/projects/snt/tasks/snt-INV-T02", headers=auth(env)).json()["label"]["key"] == "ready"


def test_asking_for_changes_needs_the_observations_and_sends_the_task_back(env):
    make_project(env)
    revision = env.services.tasks.get_task("snt-INV-T01")["revision"]
    for body in ({"action": "request_changes", "expected_revision": revision},
                 {"action": "request_changes", "expected_revision": revision, "cause": "   "}):
        res = review(env, "snt-INV-T01", body)
        assert res.status_code == 422 and res.json()["detail"]["details"] == {"field": "cause"}
    assert env.services.tasks.get_task("snt-INV-T01")["state"] == "submitted"
    res = review(env, "snt-INV-T01", {"action": "request_changes", "expected_revision": revision,
                                      "cause": "  Ampliar a competidores internacionales  "})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "needs_changes" and res.json()["label"]["key"] == "changes"
    event = next(e for e in env.services.tasks.events(task_id="snt-INV-T01") if e["type"] == "task.changes_requested")
    assert event["actor"] == "user:operator" and event["cause"] == "Ampliar a competidores internacionales"


def test_a_review_of_a_result_the_reviewer_did_not_see_is_refused(env):
    make_project(env)
    seen = env.services.tasks.get_task("snt-INV-T01")["revision"]
    res = review(env, "snt-INV-T01", {"action": "accept", "expected_revision": seen - 1})
    assert res.status_code == 409, res.text
    assert env.services.tasks.get_task("snt-INV-T01")["state"] == "submitted"
    res = review(env, "snt-INV-T01", {"action": "accept"})                                  # the revision is not optional
    assert res.status_code == 422
    assert review(env, "snt-INV-T01", {"action": "accept", "expected_revision": "3"}).status_code == 422
    assert review(env, "snt-INV-T01", {"action": "accept", "expected_revision": True}).status_code == 422
    assert env.services.tasks.get_task("snt-INV-T01")["state"] == "submitted"


def test_the_review_route_does_no_other_transition_and_takes_no_actor(env):
    make_project(env)
    revision = env.services.tasks.get_task("snt-INV-T01")["revision"]
    for action in ("cancel", "fail", "reopen", "claim", "start", "submit", "gate_pass", "unclaim", ""):
        res = review(env, "snt-INV-T01", {"action": action, "expected_revision": revision, "cause": "x"})
        assert res.status_code == 422, action
    res = review(env, "snt-INV-T01", {"action": "accept", "expected_revision": revision, "actor": "user:someone"})
    assert res.status_code == 422                                                           # the page cannot choose who decides
    task = env.services.tasks.get_task("snt-INV-T01")
    assert task["state"] == "submitted" and task["revision"] == revision


def test_the_services_rules_still_apply_to_a_review(env):
    make_project(env)
    waiting = env.services.tasks.get_task("snt-INV-T02")                  # not delivered: nothing to accept
    res = review(env, "snt-INV-T02", {"action": "accept", "expected_revision": waiting["revision"]})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "invalid_state"
    assert env.services.tasks.get_task("snt-INV-T02")["state"] == "open"
    revision = env.services.tasks.get_task("snt-INV-T01")["revision"]
    assert review(env, "snt-INV-T01", {"action": "accept", "expected_revision": revision}).status_code == 200
    again = env.services.tasks.get_task("snt-INV-T01")
    res = review(env, "snt-INV-T01", {"action": "request_changes", "expected_revision": again["revision"], "cause": "tarde"})
    assert res.status_code == 409                                         # an accepted task cannot be sent back from here
    assert env.services.tasks.get_task("snt-INV-T01")["state"] == "accepted"
