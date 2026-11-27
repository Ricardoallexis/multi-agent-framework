# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PRY-T12: «Add a stage», the GUI side of growing an open project by talking.

The three routes under /gui/api/projects/{project_id}/stages (draft, plan, create) answer what the box needs, in plain Mock; the
pure rules of project-draft-model.js (stage mode) run in Node, also over the real answers of the routes; and static checks hold
the box to its contract: the entry only in an open, not archived project, only «Add this stage» writes, the project id only in
the path, and every text in both languages. Names and data here are made up."""
from __future__ import annotations

import json
import re

import pytest

from multiagent.gui import STATIC_DIR
from test_gui_project import PROJECT_DIR, auth, env, make_project, needs_node, run_node  # noqa: F401  (env is a fixture)

JS = STATIC_DIR / "js"
REQUEST = "interview some users and run a survey and write the findings"
STAGE = {"title": "User testing", "tasks": [
    {"title": "Invite participants", "objective": "Recruit volunteers."}, {"title": "Run the sessions", "objective": ""}]}


def stage_url(project: str, action: str) -> str:
    return f"/gui/api/projects/{project}/stages/{action}"


def post(env, url, body):
    return env.client.post(url, headers=auth(env), json=body)


def project_of(env, key: str = "snt") -> dict:
    return env.client.get(f"/gui/api/projects/{key}", headers=auth(env)).json()


def draft_of(env, text=REQUEST, key="snt", **extra):
    revision = project_of(env, key)["revision"]
    return post(env, stage_url(key, "draft"), {"text": text, "expected_revision": revision, **extra})


@pytest.fixture
def snt(env):
    make_project(env, "snt", submitted=False)
    return env


# --- the routes, seen from the box ---------------------------------------------------------------------------------------------

def test_the_stage_routes_need_the_token_and_validate_the_body(snt):
    for action, body in (("draft", {"text": "x", "expected_revision": 1}), ("plan", {"stage": STAGE, "expected_revision": 1}),
                         ("create", {"stage": STAGE, "expected_revision": 1, "plan_sha256": "0" * 64})):
        assert snt.client.post(stage_url("snt", action), json=body).status_code in (401, 403)
    assert post(snt, stage_url("snt", "draft"), {"text": "x"}).status_code == 422                  # a revision is required
    assert post(snt, stage_url("snt", "draft"), {"text": "x", "expected_revision": 0}).status_code == 422
    assert post(snt, stage_url("snt", "draft"), {"text": "x", "expected_revision": 1, "project_id": "other"}).status_code == 422
    assert post(snt, stage_url("snt", "plan"), {"stage": STAGE}).status_code == 422
    assert post(snt, stage_url("snt", "create"), {"stage": STAGE, "expected_revision": 1}).status_code == 422
    assert post(snt, stage_url("snt", "draft"), {"text": "x", "expected_revision": 1, "correction": "drop it"}).status_code == 422


def test_plain_mock_drafts_one_stage_with_several_tasks_and_writes_nothing(snt):
    before = project_of(snt)
    answer = draft_of(snt)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["reason"] == "" and body["expected_revision"] == before["revision"] and body["visibility"] == "private"
    assert set(body["draft"]) == {"title", "tasks"} and len(body["draft"]["tasks"]) == 3           # one stage, one task per part
    assert body["check"]["ok"] and len(body["check"]["plan"]["plan_sha256"]) == 64 and body["check"]["problems"] == []
    assert project_of(snt) == before                                                               # asking creates nothing


def test_corrections_remove_and_add_tasks_without_a_limit(snt):
    draft = draft_of(snt).json()["draft"]
    for number in range(1, 5):                                                                    # four corrections in a row
        previous = draft
        draft = draft_of(snt, previous=previous, correction="remove the survey" if number == 1 else f"also check item {number}")
        assert draft.status_code == 200, draft.text
        draft = draft.json()["draft"]
        if number == 1:
            assert len(draft["tasks"]) == len(previous["tasks"]) - 1 and not any("survey" in t["title"] for t in draft["tasks"])
        else:
            assert len(draft["tasks"]) == len(previous["tasks"]) + 1
    assert project_of(snt)["revision"] == 1


def test_the_project_is_only_the_one_in_the_path(env):
    make_project(env, "snt", submitted=False)
    make_project(env, "oth", submitted=False)
    # the text names another project: it changes nothing about which one is checked, and the model cannot pick one
    answer = post(env, stage_url("snt", "draft"), {"text": "add this to project oth: user testing", "expected_revision": 1}).json()
    assert answer["check"]["plan"]["project_id"] == "snt" and answer["check"]["plan"]["stages"][0]["group_id"].startswith("snt-")
    assert post(env, stage_url("missing", "draft"), {"text": "x", "expected_revision": 1}).status_code == 404
    huella = post(env, stage_url("snt", "plan"), {"stage": STAGE, "expected_revision": 1}).json()["plan_sha256"]
    other = post(env, stage_url("oth", "create"), {"stage": STAGE, "expected_revision": 1, "plan_sha256": huella})
    assert other.status_code == 409 and other.json()["detail"]["details"]["reason"] == "plan_changed"
    assert project_of(env, "oth")["revision"] == 1 and len(project_of(env, "oth")["stages"]) == 2


def test_only_create_writes_and_the_new_stage_starts_unauthorized(snt):
    plan = post(snt, stage_url("snt", "plan"), {"stage": STAGE, "expected_revision": 1})
    assert plan.status_code == 200 and project_of(snt)["revision"] == 1
    huella = plan.json()["plan_sha256"]
    made = post(snt, stage_url("snt", "create"), {"stage": STAGE, "expected_revision": 1, "plan_sha256": huella})
    assert made.status_code == 200, made.text
    view = made.json()
    assert view["revision"] == 2 and [stage["code"] for stage in view["stages"]] == ["INV", "CON", "S1"]
    added = view["stages"][-1]
    assert added["title"] == "User testing" and added["authorized"] is False and len(added["tasks"]) == 2
    with snt.services.tasks.store.db.connect() as conn:
        rows = conn.execute("SELECT actor FROM team_events WHERE type = 'project.stage_attached'").fetchall()
    assert [row["actor"] for row in rows] == ["user:operator"]


def test_a_changed_plan_an_old_revision_and_a_taken_id_write_nothing_with_a_409(snt):
    huella = post(snt, stage_url("snt", "plan"), {"stage": STAGE, "expected_revision": 1}).json()["plan_sha256"]
    body = {"stage": STAGE, "expected_revision": 1, "plan_sha256": huella}
    wrong = post(snt, stage_url("snt", "create"), {**body, "plan_sha256": "0" * 64})
    assert wrong.status_code == 409 and wrong.json()["detail"]["details"]["reason"] == "plan_changed"
    changed = post(snt, stage_url("snt", "create"), {**body, "stage": {**STAGE, "title": "Another title"}})
    assert changed.status_code == 409
    assert project_of(snt)["revision"] == 1
    assert post(snt, stage_url("snt", "create"), body).status_code == 200
    stale = post(snt, stage_url("snt", "create"), body)                                            # the revision moved on
    assert stale.status_code == 409 and "revision" in stale.json()["detail"]["details"]
    again = draft_of(snt)
    assert again.json()["check"]["ok"] is True and again.json()["expected_revision"] == 2
    # an unrelated group that already has the next id: the preview says so, and nothing is created
    snt.services.tasks.create_group("snt-S2", "Unrelated", actor="user:ana")
    taken = draft_of(snt).json()
    assert taken["check"]["ok"] is False and taken["check"]["problems"][0]["reason"] == "id_collision"
    refused = post(snt, stage_url("snt", "create"), {"stage": STAGE, "expected_revision": 2, "plan_sha256": "1" * 64})
    assert refused.status_code == 409 and refused.json()["detail"]["details"]["reason"] == "id_collision"
    assert len(project_of(snt)["stages"]) == 3


def test_a_remote_model_is_asked_about_first_and_names_the_model(snt):
    snt.services.settings.gui_new_session_model = "cloud_fast"
    held = draft_of(snt).json()
    assert held["reason"] == "cloud_confirmation_required" and held["draft"] is None and held["model"] == "cloud_fast"
    assert "local_default" in held["local_models"] and held["expected_revision"] == 1
    sent = draft_of(snt, confirm_cloud=True, cloud_model="cloud_fast").json()
    assert sent["reason"] != "cloud_confirmation_required"


@needs_node
def test_the_real_answer_of_the_server_goes_through_the_box_rules_and_creates(snt):
    answer = draft_of(snt).json()
    out = run_node(f"""{preamble("en")}
      const answer = {json.dumps(answer)};
      const view = m.cardView(answer, "stage");
      console.log(JSON.stringify({{ view, body: m.createBody(answer, "stage"), kind: m.answerKind(answer, "stage"),
        asProject: m.answerKind(answer) }}));
    """)
    assert out["kind"] == "draft" and out["asProject"] == "model_error"                            # a stage is not a project draft
    assert out["view"]["canCreate"] and out["view"]["revision"] == 1 and len(out["view"]["tasks"]) == 3
    assert set(out["body"]) == {"stage", "expected_revision", "plan_sha256"} and out["body"]["stage"] == answer["draft"]
    made = post(snt, stage_url("snt", "create"), out["body"])
    assert made.status_code == 200, made.text
    assert len(made.json()["stages"]) == 3


# --- the box (static) ------------------------------------------------------------------------------------------------------------

def source(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


def test_the_entry_is_in_the_open_project_only_and_the_box_has_no_ready_made_choice():
    index, box, model = source("index.js"), source("project-draft.js"), source("project-draft-model.js")
    entry = index[index.index('class="project-stage-entry"') - 60:index.index('class="project-stage-entry"') + 420]
    assert "project && !project.archived" in entry and 'helpFor("project.stage_add")' in entry
    assert "stageFor === project.id && !project.archived && !creating" in index           # a box of another project never shows
    slot = index[index.index("export function ProjectSlot"):]
    assert "stage" not in slot.lower().replace("stages", "")                              # the right-panel card has no entry
    assert "<select" not in box and "<option" not in box and 'type="radio"' not in box
    assert not re.search(r"template|preset|example", box + model, re.I)
    assert index.count("<${NewProject}") == 2 and index.count("stage=${{ projectId: project.id, revision: project.revision }}") == 1


def test_only_add_this_stage_writes_and_the_project_id_only_travels_in_the_path():
    box, model = source("project-draft.js"), source("project-draft-model.js")
    assert box.count("apiPost(") == 2 and box.count("apiPost(createPath") == 1 and "apiPost(draftPath" in box
    assert box.count("async function create()") == 1 and box.count("onClick=${create}") == 2    # one button per mode, one function
    assert box.count('stagePath(stage.projectId, "create")') == 1 and box.count('stagePath(stage.projectId, "draft")') == 1
    assert box.count("disabled=${busy || !view.canCreate}") == 2
    assert 'helpFor("project.stage_create")' in box and 'helpFor("project.draft_create")' in box
    assert "project_id" not in box.replace("projectId", "") and "project_id" not in model       # no id in any body
    assert not re.search(r"innerHTML|dangerouslySetInnerHTML|insertAdjacentHTML|setInterval|setTimeout|style=", box + model)
    assert "expected_revision: view.revision" in model and "stage: answer.draft" in model
    assert "isConflict(problem)) { setAnswer(null)" in box                                      # a 409 drops the card: draft again


def test_no_entry_in_the_thread_of_a_run():
    for path in sorted(JS.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert "ext.project.stage" not in text and "/stages/" not in text, path.name


def test_the_stage_help_exists_in_both_languages_and_is_used():
    box, index, help_text, texts = source("project-draft.js"), source("index.js"), source("help.js"), source("i18n.js")
    topics = {"project.stage_add", "project.stage_back", "project.stage_send", "project.stage_create"}
    for topic in topics:
        assert f'"{topic}": {{' in help_text and f'"help.{topic}": {{' in texts, topic
        assert f'helpFor("{topic}")' in box + index and f'topic="{topic}"' in box + index, topic


def test_every_stage_text_is_in_both_languages_and_used():
    texts = source("i18n.js")
    en_block, es_block = texts[:texts.index("const es = {")], texts[texts.index("const es = {"):]
    en_keys = set(re.findall(r'^  "(ext\.project\.stage\.[a-z_.]+)":', en_block, re.M))
    es_keys = set(re.findall(r'^  "(ext\.project\.stage\.[a-z_.]+)":', es_block, re.M))
    assert en_keys and en_keys == es_keys
    en_keys -= {f"ext.project.stage.{name}" for name in ("aria_label", "closed", "filtered", "progress")}     # the stage list's own texts
    used = set(re.findall(r'"(ext\.project\.stage\.[a-z_.]+)"', "".join(source(name) for name in ("index.js", "project-draft.js", "project-draft-model.js"))))
    assert used == en_keys
    assert '"ext.project.stage.add": "Add a stage"' in en_block and '"ext.project.stage.add": "Añadir una etapa"' in es_block
    assert '"ext.project.stage.create": "Add this stage"' in en_block and '"ext.project.stage.create": "Añadir esta etapa"' in es_block


def test_the_entry_fits_a_phone_without_inline_styles():
    css = source("style.css")
    entry = css[css.index("PRY-T12"):]
    assert ".project-stage-entry" in entry and "@media (max-width: 599.98px)" in entry and "min-width: 0" in entry
    assert "white-space: nowrap" not in entry
    for name in ("index.js", "project-draft.js", "project-draft-model.js"):
        assert "style=" not in source(name), name


# --- the pure rules in stage mode (Node) -------------------------------------------------------------------------------------

def preamble(language: str = "es") -> str:
    return f"""
      const ui = await import({json.dumps((JS / "i18n.js").as_uri())});
      await import({json.dumps((PROJECT_DIR / "i18n.js").as_uri())});
      ui.setLanguage({json.dumps(language)});
      const m = await import({json.dumps((PROJECT_DIR / "project-draft-model.js").as_uri())});
      globalThis.fetch = () => {{ throw new Error('the model must not touch the network'); }};
    """


def answer(**extra):
    base = {"reason": "", "draft": STAGE, "model": "local_default", "tier": "local", "visibility": "private",
            "executors_active": False, "expected_revision": 3,
            "check": {"ok": True, "plan": {"plan_sha256": "a" * 64, "expected_revision": 3}, "problems": []}}
    return {**base, **extra}


@needs_node
def test_stage_paths_bodies_and_kinds_run_in_node():
    out = run_node(f"""{preamble()}
      const a = {json.dumps(answer())};
      console.log(JSON.stringify({{
        path: [m.stagePath("snt", "draft"), m.stagePath("a b/c", "create")],
        plain: m.draftBody("  user testing  ", {{ expectedRevision: 3 }}),
        fix: m.draftBody("t", {{ previous: a.draft, correction: " drop the sessions ", expectedRevision: 3, confirmCloud: true, cloudModel: "cloud_fast" }}),
        project: m.draftBody("t"),
        kinds: [m.answerKind(a, "stage"), m.answerKind({{ reason: "disabled", draft: null }}, "stage"),
                m.answerKind({{ reason: "", draft: {{ title: "x", tasks: [] }} }}, "stage"), m.answerKind(a)],
      }}));
    """)
    assert out["path"] == ["/gui/api/projects/snt/stages/draft", "/gui/api/projects/a%20b%2Fc/stages/create"]
    assert out["plain"] == {"text": "user testing", "expected_revision": 3}
    assert out["fix"] == {"text": "t", "expected_revision": 3, "previous": STAGE, "correction": "drop the sessions",
                          "confirm_cloud": True, "cloud_model": "cloud_fast"}
    assert out["project"] == {"text": "t"}                                                       # the project mode is unchanged
    assert out["kinds"] == ["draft", "disabled", "model_error", "model_error"]


@needs_node
def test_the_card_adds_only_what_the_plan_verified_with_its_revision():
    ok = answer()
    collision = answer(check={"ok": False, "plan": None, "problems": [{"reason": "id_collision", "message": "A stage or task id already exists"}]})
    stale = answer(check={"ok": False, "plan": None, "problems": [{"reason": "invalid_state", "message": "Stale project revision"}]})
    norevision = answer(expected_revision=None)
    nohash = answer(check={"ok": True, "plan": {"plan_sha256": "nope"}, "problems": []})
    out = run_node(f"""{preamble("en")}
      const cases = {json.dumps([ok, collision, stale, norevision, nohash, {"reason": "disabled", "draft": None}])};
      console.log(JSON.stringify({{ views: cases.map((c) => m.cardView(c, "stage")), bodies: cases.map((c) => m.createBody(c, "stage")),
        remote: m.cardView({json.dumps(answer(tier="cloud", model="cloud_fast"))}, "stage").modelText }}));
    """)
    good, bad, old, norev, nohash_view, none = out["views"]
    assert good["canCreate"] and good["planSha256"] == "a" * 64 and good["revision"] == 3 and good["title"] == "User testing"
    assert [task["title"] for task in good["tasks"]] == ["Invite participants", "Run the sessions"] and good["stages"] == []
    assert good["countsText"] == "Tasks: 2"
    assert good["unauthorizedText"] == "This stage will start unauthorized. Its tasks remain pending until you authorize it."
    assert bad["canCreate"] is False and bad["problems"] == ["A stage or task with the same id already exists, so nothing was added. Draft it again."]
    assert old["canCreate"] is False and old["problems"] == ["Recheck this stage: the project changed since the preview. Nothing was added. Draft it again."]
    assert norev["canCreate"] is False and nohash_view["canCreate"] is False and none is None
    assert out["bodies"] == [{"stage": STAGE, "expected_revision": 3, "plan_sha256": "a" * 64}, None, None, None, None, None]
    assert out["remote"] == "Drafted with cloud_fast (remote model)"


@needs_node
def test_the_cloud_notice_says_the_project_title_and_stage_titles_go_too():
    held = {"reason": "cloud_confirmation_required", "model": "cloud_fast", "tier": "cloud", "visibility": "private",
            "local_models": ["local_default"], "draft": None, "expected_revision": 3}
    script = f"""
      const held = {json.dumps(held)};
      console.log(JSON.stringify({{ stage: m.cloudNotice(held, "stage"), project: m.cloudNotice(held) }}));
    """
    en = run_node(preamble("en") + script)
    assert en["stage"]["contextText"] == ("Before sending, confirm that the request, project title, and existing stage titles "
                                          "may go to the selected cloud model.")
    assert en["stage"]["text"] == "Your text will be sent to cloud_fast, a remote model." and en["stage"]["privateText"] == ""
    assert "local_default" in en["stage"]["localText"] and en["stage"]["sendLabel"] == "Send to cloud_fast"
    assert en["project"]["contextText"] == "" and en["project"]["privateText"] == "The project will be created as private."
    es = run_node(preamble("es") + script)
    assert es["stage"]["contextText"].startswith("Antes de enviar, confirma que la petición, el título del proyecto y los títulos")
    assert es["stage"]["text"] == "Tu texto se enviará a cloud_fast, un modelo remoto."


@needs_node
def test_every_answer_without_a_draft_has_its_sentence_in_both_languages():
    reasons = ["disabled", "secret_review_required", "budget_cut", "refused", "limit_reached", "invalid_output", "model_error", "other"]
    script = f"""
      console.log(JSON.stringify({{
        reasons: {json.dumps(reasons)}.map((reason) => m.reasonText({{ reason, draft: null }}, "stage")),
        projectReasons: {json.dumps(reasons)}.map((reason) => m.reasonText({{ reason, draft: null }})),
        links: ["disabled", "limit_reached", "secret_review_required", "refused"].map((reason) => m.reasonLink({{ reason }}, "stage")),
        failures: [m.createFailure({{ status: 409, details: {{ reason: "plan_changed" }} }}, "stage"),
                   m.createFailure({{ status: 409, details: {{ revision: 4, expected_revision: 3 }} }}, "stage"),
                   m.createFailure({{ status: 409, details: {{ reason: "id_collision" }} }}, "stage"),
                   m.createFailure({{ status: 500, message: "boom" }}, "stage"), m.createFailure(null, "stage"),
                   m.createFailure({{ status: 409, details: {{ reason: "plan_changed" }} }})],
        conflicts: [m.isConflict({{ status: 409 }}), m.isConflict({{ status: 404 }}), m.isConflict(null)],
        texts: ["title", "back", "intro", "text_label", "model_note", "send", "card_title", "not_created", "create", "no_executor"]
          .map((name) => m.say("stage", name)),
      }}));
    """
    en = run_node(preamble("en") + script)
    es = run_node(preamble("es") + script)
    for out in (en, es):
        assert len(set(out["reasons"])) == 8 and all(out["reasons"])
        assert [link and link["href"] for link in out["links"]] == ["#apps", "#apps", "#approvals", None]
        assert out["failures"][0] == out["failures"][1] and out["failures"][0] != out["failures"][2]
        assert out["failures"][3] == "boom" and out["failures"][4] and out["conflicts"] == [True, False, False]
        assert len(set(out["texts"])) == 10 and all(out["texts"])
    assert en["reasons"][0] == "The model for new conversations is turned off, so no stage can be drafted."
    assert en["reasons"][5].startswith("The model's answer did not have the shape of one stage")
    assert en["reasons"][7] == "The stage could not be drafted."
    assert en["reasons"][1:5] == en["projectReasons"][1:5] and en["reasons"][6] == en["projectReasons"][6]   # the shared ones are shared
    assert en["texts"][0] == "Add a stage" and en["texts"][5] == "Draft the stage" and en["texts"][8] == "Add this stage"
    assert es["texts"][0] == "Añadir una etapa" and es["texts"][8] == "Añadir esta etapa" and es["texts"][9].startswith("No hay ninguna app ni agente")
    assert es["failures"][0].startswith("Vuelve a comprobar esta etapa: el proyecto cambió desde la vista previa.")
    assert en["failures"][5].startswith("The draft changed after you saw it")                       # project mode keeps its own words
