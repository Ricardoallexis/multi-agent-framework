# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PRY-T06: «Nuevo proyecto», the GUI side of creating a project by talking: the three routes under /gui/api/projects
(draft, plan, create), the pure rules of project-draft-model.js run in Node, and static checks of the box (it offers nothing
ready-made, and only the button «Crear proyecto» creates)."""
from __future__ import annotations

import json
import re

import pytest

from multiagent.gui import STATIC_DIR
from test_gui_project import PROJECT_DIR, auth, env, needs_node, run_node  # noqa: F401  (env is a fixture)

JS = STATIC_DIR / "js"
REQUEST = "preparar el lanzamiento de un producto y luego la campana de anuncios"
DRAFT_URL = "/gui/api/projects/draft"
PLAN_URL = "/gui/api/projects/plan"
CREATE_URL = "/gui/api/projects/create"
GOOD = {"title": "Launch plan", "stages": [
    {"title": "Prepare", "tasks": [{"title": "List the audiences", "objective": "Know who it is for."}]},
    {"title": "Announce", "tasks": [{"title": "Publish the news", "objective": ""}]}]}


def post(env, url, body):
    return env.client.post(url, headers=auth(env), json=body)


# --- the routes ------------------------------------------------------------------------------------------------------------

def test_the_routes_need_the_token_and_validate_the_body(env):
    for url, body in ((DRAFT_URL, {"text": "x"}), (PLAN_URL, {"draft": GOOD}),
                      (CREATE_URL, {"draft": GOOD, "plan_sha256": "0" * 64})):
        assert env.client.post(url, json=body).status_code in (401, 403)
    assert post(env, DRAFT_URL, {"text": ""}).status_code == 422
    assert post(env, DRAFT_URL, {"text": "x" * 2001}).status_code == 422
    assert post(env, DRAFT_URL, {"text": "x", "extra": 1}).status_code == 422
    assert post(env, DRAFT_URL, {"text": "x", "correction": "quita algo"}).status_code == 422     # no previous draft
    assert post(env, PLAN_URL, {}).status_code == 422
    assert post(env, PLAN_URL, {"draft": {"title": "T", "stages": []}}).status_code == 422
    assert post(env, PLAN_URL, {"draft": {**GOOD, "owner": "user:ana"}}).status_code == 422
    assert post(env, CREATE_URL, {"draft": GOOD, "plan_sha256": "short"}).status_code == 422
    assert post(env, CREATE_URL, {"draft": GOOD}).status_code == 422


def test_draft_answers_with_a_card_and_creates_nothing(env):
    answer = post(env, DRAFT_URL, {"text": REQUEST})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["reason"] == "" and body["draft"]["stages"][0]["tasks"] and body["visibility"] == "private"
    assert body["check"]["ok"] and len(body["check"]["plan"]["plan_sha256"]) == 64
    assert env.client.get("/gui/api/projects", headers=auth(env)).json() == {"projects": []}
    corrected = post(env, DRAFT_URL, {"text": REQUEST, "previous": body["draft"], "correction": "quita la etapa de anuncios"})
    assert [stage["title"] for stage in corrected.json()["draft"]["stages"]] == ["preparar el lanzamiento de un producto"]


def test_a_remote_model_is_asked_about_first(env):
    env.services.settings.gui_new_session_model = "cloud_fast"
    held = post(env, DRAFT_URL, {"text": REQUEST}).json()
    assert held["reason"] == "cloud_confirmation_required" and held["draft"] is None and held["model"] == "cloud_fast"
    assert held["visibility"] == "private" and "local_default" in held["local_models"]
    sent = post(env, DRAFT_URL, {"text": REQUEST, "confirm_cloud": True}).json()
    assert sent["reason"] != "cloud_confirmation_required"


def test_plan_gives_the_huella_and_writes_nothing(env):
    answer = post(env, PLAN_URL, {"draft": GOOD})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["status"] == "planned" and body["key"] == "launchplan" and len(body["plan_sha256"]) == 64
    assert body["counts"] == {"stages": 2, "tasks": 2, "dependencies": 0}
    assert env.client.get("/gui/api/projects", headers=auth(env)).json() == {"projects": []}


def test_create_needs_the_huella_of_the_preview_and_uses_the_operator_actor(env):
    huella = post(env, PLAN_URL, {"draft": GOOD}).json()["plan_sha256"]
    wrong = post(env, CREATE_URL, {"draft": GOOD, "plan_sha256": "0" * 64})
    assert wrong.status_code == 409 and wrong.json()["detail"]["details"]["reason"] == "plan_changed"
    other = post(env, CREATE_URL, {"draft": {**GOOD, "stages": GOOD["stages"][:1]}, "plan_sha256": huella})
    assert other.status_code == 409
    assert env.client.get("/gui/api/projects", headers=auth(env)).json() == {"projects": []}
    made = post(env, CREATE_URL, {"draft": GOOD, "plan_sha256": huella})
    assert made.status_code == 200, made.text
    assert made.json()["id"] == "launchplan"
    listed = env.client.get("/gui/api/projects", headers=auth(env)).json()["projects"]
    assert [item["id"] for item in listed] == ["launchplan"]
    with env.services.tasks.store.db.connect() as conn:
        row = conn.execute("SELECT actor FROM team_events WHERE type = 'project.created'").fetchone()
    assert row["actor"] == "user:operator"
    assert env.client.get("/gui/api/projects/launchplan", headers=auth(env)).status_code == 200


def test_a_taken_name_is_a_409_with_its_reason_in_plan_and_create(env):
    huella = post(env, PLAN_URL, {"draft": GOOD}).json()["plan_sha256"]
    assert post(env, CREATE_URL, {"draft": GOOD, "plan_sha256": huella}).status_code == 200
    for url, body in ((PLAN_URL, {"draft": GOOD}), (CREATE_URL, {"draft": GOOD, "plan_sha256": huella})):
        answer = post(env, url, body)
        assert answer.status_code == 409 and answer.json()["detail"]["details"]["reason"] == "project_exists"


def test_the_new_routes_do_not_shadow_the_project_routes(env):
    assert env.client.get("/gui/api/projects/draft", headers=auth(env)).status_code == 404     # a project called «draft» does not exist
    assert env.client.get("/gui/api/projects", headers=auth(env)).status_code == 200


# --- the box (static) ------------------------------------------------------------------------------------------------------

def source(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


def test_the_box_offers_nothing_ready_made_and_only_the_button_creates():
    box = source("project-draft.js")
    model = source("project-draft-model.js")
    assert "<select" not in box and "<option" not in box and "type=\"radio\"" not in box
    assert not re.search(r"template|preset|example", box + model, re.I)
    assert not re.search(r"innerHTML|dangerouslySetInnerHTML|insertAdjacentHTML", box + model)
    # CREATE_PATH is posted from one place, the function that the «Crear proyecto» button runs, and from nowhere else
    assert box.count("apiPost(CREATE_PATH") == 1 and box.count("onClick=${create}") == 1
    assert box.count("async function create()") == 1 and "create()" not in box.replace("async function create()", "")
    assert "disabled=${busy || !view.canCreate}" in box
    assert "apiPost(DRAFT_PATH" in box and box.count("apiPost(") == 2
    index = source("index.js")
    assert index.count("<${NewProject}") == 1 and "project-draft.js" in index
    assert re.search(r'helpFor\("project\.new"\)', index)


def test_the_entry_does_not_touch_the_right_panel_card():
    index = source("index.js")
    slot = index[index.index("export function ProjectSlot"):]
    assert "NewProject" not in slot and "draft" not in slot


def test_every_new_control_has_help_and_both_languages():
    box, index = source("project-draft.js"), source("index.js")
    topics = {topic for topic in re.findall(r'"(project\.(?:new|draft_[a-z]+))"', box + index)}
    assert topics == {"project.new", "project.draft_back", "project.draft_text", "project.draft_send", "project.draft_cloud",
                      "project.draft_change", "project.draft_redraft", "project.draft_create"}
    help_text = source("help.js")
    texts = source("i18n.js")
    for topic in topics:
        assert f'"{topic}": {{' in help_text and f'"help.{topic}": {{' in texts
    keys = set(re.findall(r'"(ext\.project\.draft\.[a-z_.]+)"', box + source("project-draft-model.js") + index))
    en_block = texts[:texts.index("const es = {")]
    es_block = texts[texts.index("const es = {"):]
    for key in keys:
        assert f'"{key}":' in en_block and f'"{key}":' in es_block, key


def test_the_styles_are_scoped_and_have_forced_colors():
    css = source("style.css")
    new = css[css.index("PRY-T06"):]
    assert "forced-colors: active" in new and ".project-new-card" in new
    for selector in re.findall(r"^([.#@][^{]*?)\s*\{", new, re.M):
        assert selector.startswith((".project-", "@media")), selector


# --- the pure rules (Node) -------------------------------------------------------------------------------------------------

def preamble(language: str = "es") -> str:
    return f"""
      const ui = await import({json.dumps((JS / "i18n.js").as_uri())});
      await import({json.dumps((PROJECT_DIR / "i18n.js").as_uri())});
      ui.setLanguage({json.dumps(language)});
      const m = await import({json.dumps((PROJECT_DIR / "project-draft-model.js").as_uri())});
      globalThis.fetch = () => {{ throw new Error('the model must not touch the network'); }};
    """


def answer(env_or_none=None, **extra):
    base = {"reason": "", "draft": GOOD, "model": "local_default", "tier": "local", "visibility": "private",
            "executors_active": False,
            "check": {"ok": True, "plan": {"plan_sha256": "a" * 64}, "problems": []}}
    return {**base, **extra}


@needs_node
def test_bodies_and_kinds_run_in_node():
    out = run_node(f"""{preamble()}
      const a = {json.dumps(answer())};
      console.log(JSON.stringify({{
        plain: m.draftBody("  quiero un plan  "),
        fix: m.draftBody("plan", {{ previous: a.draft, correction: " quita algo ", confirmCloud: true, approvalId: "ap1" }}),
        orphan: m.draftBody("plan", {{ correction: "quita algo" }}),
        kinds: [m.answerKind(a), m.answerKind({{ reason: "disabled", draft: null }}), m.answerKind(null),
                m.answerKind({{ reason: "", draft: {{ title: "x", stages: [] }} }}), m.answerKind({{ reason: "cloud_confirmation_required" }})],
        can: [m.canDraft(""), m.canDraft("  "), m.canDraft("x"), m.canDraft("x".repeat(2001)), m.canRedraft(" a "), m.canRedraft("")],
      }}));
    """)
    assert out["plain"] == {"text": "quiero un plan"}
    assert out["fix"] == {"text": "plan", "previous": GOOD, "correction": "quita algo", "confirm_cloud": True, "approval_id": "ap1"}
    assert out["orphan"] == {"text": "plan"}                    # a correction never travels without the draft it corrects
    assert out["kinds"] == ["draft", "disabled", "model_error", "model_error", "cloud_confirmation_required"]
    assert out["can"] == [False, False, True, False, True, False]


@needs_node
def test_the_card_creates_only_what_the_plan_verified():
    ok = answer()
    unverified = answer(check={"ok": False, "plan": None, "problems": [{"reason": "project_exists", "message": "Project already exists: x"}]})
    nohash = answer(check={"ok": True, "plan": {"plan_sha256": "nope"}, "problems": []})
    out = run_node(f"""{preamble()}
      const cases = {json.dumps([ok, unverified, nohash, {"reason": "disabled", "draft": None}])};
      const views = cases.map((c) => m.cardView(c));
      console.log(JSON.stringify({{
        views, bodies: cases.map((c) => m.createBody(c)),
        executors: m.cardView({json.dumps(answer(executors_active=True))}).noExecutor,
        remote: m.cardView({json.dumps(answer(tier="cloud", model="cloud_fast"))}).modelText,
      }}));
    """)
    good, bad, hashless, none = out["views"]
    assert good["canCreate"] and good["planSha256"] == "a" * 64 and good["counts"] == {"stages": 2, "tasks": 2}
    assert good["title"] == "Launch plan" and good["stages"][0]["tasks"][0]["objective"] == "Know who it is for."
    assert good["noExecutor"] is True and good["modelText"] == "Redactado con local_default (en este equipo)"
    assert bad["canCreate"] is False and bad["problems"] == ["Ya existe un proyecto con ese nombre. Pide otro nombre."]
    assert hashless["canCreate"] is False and none is None
    assert out["bodies"] == [{"draft": GOOD, "plan_sha256": "a" * 64}, None, None, None]
    assert out["executors"] is False
    assert out["remote"] == "Redactado con cloud_fast (modelo remoto)"


@needs_node
def test_the_notices_name_the_model_where_the_text_goes_and_that_it_is_private():
    out = run_node(f"""{preamble()}
      const held = {json.dumps({"reason": "cloud_confirmation_required", "model": "cloud_fast", "tier": "cloud",
                                "visibility": "private", "local_models": ["local_default", "ollama_small"], "draft": None})};
      console.log(JSON.stringify({{
        cloud: m.cloudNotice(held), bare: m.cloudNotice({{ model: "m" }}),
        reasons: ["disabled", "secret_review_required", "budget_cut", "refused", "limit_reached", "invalid_output", "model_error", "other"]
          .map((reason) => m.reasonText({{ reason, draft: null }})),
        links: ["disabled", "limit_reached", "secret_review_required", "refused"].map((reason) => m.reasonLink({{ reason }})),
        failure: [m.createFailure({{ details: {{ reason: "plan_changed" }} }}), m.createFailure({{ details: {{ reason: "project_exists" }} }}),
                  m.createFailure({{ message: "boom" }}), m.createFailure(null)],
      }}));
    """)
    cloud = out["cloud"]
    assert cloud["text"] == "Tu texto se enviará a cloud_fast, un modelo remoto." and cloud["privateText"] == "El proyecto se creará como privado."
    assert "local_default, ollama_small" in cloud["localText"] and cloud["sendLabel"] == "Enviar a cloud_fast"
    assert out["bare"]["localText"] == ""
    assert len(set(out["reasons"])) == 8 and all(out["reasons"])
    assert [link and link["href"] for link in out["links"]] == ["#apps", "#apps", "#approvals", None]
    assert out["failure"][0].startswith("El borrador cambió") and out["failure"][1].startswith("Ya existe")
    assert out["failure"][2] == "boom" and out["failure"][3] == "No se pudo crear el proyecto."


@needs_node
def test_the_english_texts_exist_too():
    out = run_node(f"""{preamble("en")}
      console.log(JSON.stringify({{ cloud: m.cloudNotice({{ model: "cloud_fast", local_models: [] }}).text,
        reason: m.reasonText({{ reason: "disabled" }}), fail: m.createFailure(null) }}));
    """)
    assert out == {"cloud": "Your text will be sent to cloud_fast, a remote model.",
                   "reason": "The model for new conversations is turned off, so no project can be drafted.",
                   "fail": "Could not create the project."}



def test_cloud_model_travels_with_the_confirmation_and_another_model_asks_again(env):
    env.services.settings.gui_new_session_model = "cloud_fast"
    ok = post(env, DRAFT_URL, {"text": REQUEST, "confirm_cloud": True, "cloud_model": "cloud_fast"}).json()
    assert ok["reason"] != "cloud_confirmation_required"
    held = post(env, DRAFT_URL, {"text": REQUEST, "confirm_cloud": True, "cloud_model": "another_model"}).json()
    assert held["reason"] == "cloud_confirmation_required" and held["model"] == "cloud_fast" and held["draft"] is None
    assert post(env, DRAFT_URL, {"text": "x", "cloud_model": "y" * 121}).status_code == 422


def test_the_box_remembers_the_confirmation_for_its_model_only():
    box = source("project-draft.js")
    assert 'useRef("")' in box and "confirmedModel" in box
    assert box.count("confirmedModel.current = cloud.model") == 1            # set only by the «send» button of the notice
    assert 'confirmedModel.current = ""; setPending' in box                  # a new request for confirmation forgets the old model
    assert "confirmCloud: true, cloudModel: model" in box                    # every later draft sends it with the model
    assert "if (!busy && canDraft(request))" in box


@needs_node
def test_the_body_sends_the_confirmed_model_only_with_the_confirmation():
    out = run_node(f"""{preamble()}
      console.log(JSON.stringify({{ yes: m.draftBody("p", {{ confirmCloud: true, cloudModel: "cloud_fast" }}),
        none: m.draftBody("p", {{ cloudModel: "cloud_fast" }}), bare: m.draftBody("p", {{ confirmCloud: true }}) }}));
    """)
    assert out == {"yes": {"text": "p", "confirm_cloud": True, "cloud_model": "cloud_fast"}, "none": {"text": "p"},
                   "bare": {"text": "p", "confirm_cloud": True}}


def test_the_loading_notice_hides_while_the_box_is_open():
    assert "!error && !creating && projects && projects.length" in source("index.js")
