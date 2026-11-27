# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFA-T06: «Explain this office to me» and «Try in Mock», the GUI side: the routes under /gui/api/offices/stage/{stage_id}
(checks, explain, rehearse) as the card uses them, the pure rules of import-assistant-model.js run in Node, and static checks of
the card (today's card and install button stay, the conversation cannot approve, simple mode shows no folder or code, what comes
from the package is drawn as text, every text exists in both languages and the styles fit a phone)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from test_office_import_chat import SAMPLE, _package, _sample_adapter
from test_office_install_card import _decide
from test_office_rehearsal import _client, _real_files, world  # noqa: F401  (world is a fixture)

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
JS = STATIC_DIR / "js"
EXT = STATIC_DIR / "extensions" / "offices"
HOSTILE = "<img src=x onerror=alert(1)>"


def source(name: str) -> str:
    return (EXT / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def stage_url(card: dict, step: str = "") -> str:
    return f"/gui/api/offices/stage/{card['stage_id']}" + (f"/{step}" if step else "")


# --- the routes, as the card uses them ------------------------------------------------------------------------------------

def test_routes_need_the_token_and_validate_the_body(world):
    start, tmp_path = world
    services = start(mock=False, adapter=_sample_adapter())
    card = services.offices.stage_folder(_package(tmp_path))
    client, headers = _client(services)
    with client:
        assert client.get(stage_url(card, "checks")).status_code in (401, 403)
        assert client.post(stage_url(card, "explain"), json={}).status_code in (401, 403)
        assert client.post(stage_url(card, "rehearse"), json={}).status_code in (401, 403)
        assert client.post(stage_url(card, "explain"), headers=headers, json={"question": ""}).status_code == 422
        assert client.post(stage_url(card, "explain"), headers=headers, json={"question": "x" * 2001}).status_code == 422
        assert client.post(stage_url(card, "explain"), headers=headers, json={"extra": 1}).status_code == 422
        assert client.post(stage_url(card, "rehearse"), headers=headers, json={"area": ""}).status_code == 422
        gone = client.get(f"/gui/api/offices/stage/{'0' * 32}/checks", headers=headers)
        assert gone.status_code == 409 and gone.json()["detail"]["details"]["reason"] == "office_stage_not_found"


def test_the_card_flow_explains_then_tries_and_neither_approves(world):
    start, tmp_path = world
    services = start(mock=False, adapter=_sample_adapter())
    card = services.offices.stage_folder(_package(tmp_path))
    client, headers = _client(services)
    before = _real_files(tmp_path)
    with client:
        checks = client.get(stage_url(card, "checks"), headers=headers).json()
        assert checks["writes_files"] is True and checks["rehearsal_required"] is True and checks["rehearsal"] is None
        assert [row["tool"] for row in checks["tools"]] == ["file.write"]
        assert checks["inputs_unasked"] == [{"area": "reception", "workflow": "write_note", "inputs": ["spare"]}]
        asked = client.post(stage_url(card, "explain"), headers=headers, json={"question": "What is written?"}).json()
        assert asked["source"] == "model" and asked["explanation"]["answer"] == SAMPLE["answer"]
        assert asked["stage_id"] == card["stage_id"]
        # the model's answer never touches the facts the card reads
        assert asked["inputs_unasked"] == checks["inputs_unasked"] and asked["risk_ack_required"] is False
        assert services.user_approvals.status(card["approval_id"])["state"] == "pending"
        # no rehearsal yet: the install is refused with the reason the card reads, and the approval is not consumed
        _decide(services, card["approval_id"])
        refused = client.post(stage_url(card, "install"), headers=headers, json={"approval_id": card["approval_id"]})
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "rehearsal_required"
        assert refused.json()["detail"]["details"] == {"reason": "office_rehearsal_required", "stage_id": card["stage_id"]}
        assert services.user_approvals.status(card["approval_id"])["state"] == "approved"
        trial = client.post(stage_url(card, "rehearse"), headers=headers, json={}).json()
        assert trial["state"] == "finished" and trial["actor"] == "user:gui" and trial["folder_deleted"] is True
        assert [item["path"] for item in trial["files"]] == ["notes/summary.md"]
        # the same approval, decided once before the 409, installs the office: there is one approval per version
        again = client.post(stage_url(card, "install"), headers=headers, json={"approval_id": card["approval_id"]})
        assert again.status_code == 200, again.text
    assert _real_files(tmp_path) == before and services.offices.get("demo")["state"] == "ok"


def test_a_protected_office_and_a_new_area_get_the_fixed_explanation_from_the_route(world, monkeypatch):
    start, tmp_path = world
    services = start(model="cloud_fast", adapter=_sample_adapter())
    asked: list[str] = []
    monkeypatch.setattr(services.new_session, "_ask", lambda prompt, **kw: asked.append(prompt))
    client, headers = _client(services)
    protected = services.offices.stage_folder(_package(tmp_path / "a", area={"local_only": True}))
    normal = services.offices.stage_folder(_package(tmp_path / "b", area={"sensitivity": "normal"}))
    with client:
        first = client.post(stage_url(protected, "explain"), headers=headers, json={"confirm_cloud": True}).json()
        second = client.post(stage_url(normal, "explain"), headers=headers, json={"confirm_cloud": True}).json()
    assert asked == []
    assert (first["source"], first["reason"]) == ("deterministic", "office_not_normal")
    assert (second["source"], second["reason"]) == ("deterministic", "cloud_not_allowed")
    assert first["explanation"]["summary"] and first["explanation"]["areas"]


def test_a_failed_trial_is_an_answer_the_install_accepts(world):
    start, tmp_path = world
    services = start()
    from test_office_rehearsal import WRITE_FLOW, write_package
    flow = {**WRITE_FLOW, "steps": [{**WRITE_FLOW["steps"][0], "tools": [
        {"capability": "file.write", "arguments": {"path": "a.md", "content": "${ghost}"}}]}]}
    card = services.offices.stage_folder(write_package(tmp_path / "demo", flow=flow))
    client, headers = _client(services)
    with client:
        trial = client.post(stage_url(card, "rehearse"), headers=headers, json={}).json()
        assert trial["state"] == "failed" and trial["flows"]
        _decide(services, card["approval_id"])
        done = client.post(stage_url(card, "install"), headers=headers, json={"approval_id": card["approval_id"]})
        assert done.status_code == 200, done.text


# --- the pure rules (Node) ---------------------------------------------------------------------------------------------------

def preamble(language: str = "es") -> str:
    return f"""
      const ui = await import({json.dumps((JS / "i18n.js").as_uri())});
      await import({json.dumps((EXT / "i18n.js").as_uri())});
      ui.setLanguage({json.dumps(language)});
      const m = await import({json.dumps((EXT / "import-assistant-model.js").as_uri())});
      globalThis.fetch = () => {{ throw new Error('the pure rules must not touch the network'); }};
    """


def run_node(script: str):
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


REPORT = {
    "stage_id": "s1", "source": "model", "reason": "",
    "areas": [{"id": "reception", "name": "Reception " + HOSTILE, "workflows": [
        {"id": "write_note", "description": "Writes a note. " + HOSTILE, "unreadable": False, "steps": []}]}],
    "tools": [
        {"area": "reception", "workflow": "write_note", "step": "draft", "tool": "file.write", "title": "Write file",
         "known": True, "impact": "write", "permission": "each_call", "reads": [],
         "writes": [{"argument": "path", "path": "notes/summary.md", "from_input": None}]},
        {"area": "reception", "workflow": "write_note", "step": "load", "tool": "file.read", "title": "Read file",
         "known": True, "impact": "read", "permission": "none",
         "reads": [{"argument": "path", "path": None, "from_input": "topic"}], "writes": []},
        {"area": "reception", "workflow": "write_note", "step": "ghost", "tool": "ghost.tool", "title": "ghost.tool",
         "known": False, "impact": "unknown", "permission": "unknown", "reads": [], "writes": []}],
    "inputs": [{"area": "reception", "workflow": "write_note", "unverified": True, "reason": "function_step"}],
    "inputs_unasked": [{"area": "reception", "workflow": "write_note", "inputs": ["spare"]}],
    "inputs_missing": [{"area": "reception", "workflow": "write_note", "inputs": ["ghost"]}],
    "needs_file_tools_root": [{"area": "reception", "workflow": "write_note"}],
    "code": {"files": ["areas/reception/functions.py"], "checked": False, "run_in_rehearsal": False},
    "findings": [{"path": "areas/reception/README.md", "line": 4, "type": "email"},
                 {"path": "/abs/outside.txt", "line": 1, "type": "email"}],
    "unverified": ["areas/reception/logo.png"],
    "explanation": {"summary": "A synthetic office. " + HOSTILE,
                    "areas": [{"area": "reception", "text": "Reception writes a note."}],
                    "tools": [{"tool": "file.write", "text": "Saves the note after you approve it."}],
                    "answer": "It writes notes/summary.md."},
}
TRIAL = {"state": "finished", "folder_deleted": True, "flows_omitted": 2, "files": [{"path": "notes/summary.md", "size": 14},
                                                                                      {"path": "../escape.txt", "size": 1}],
         "flows": [
             {"area": "reception", "workflow": "write_note", "status": "completed", "reason": "",
              "steps": [{"id": "draft", "state": "ran", "reason": None}],
              "inputs": {"synthetic": ["spare", "topic"], "missing": ["ghost"]}, "files": [{"path": "notes/summary.md", "size": 14}]},
             {"area": "reception", "workflow": "coded", "status": "not_rehearsed", "reason": "untrusted_code",
              "steps": [{"id": "fn", "state": "not_rehearsed", "reason": "untrusted_code"}], "inputs": {}, "files": []},
             {"area": "reception", "workflow": "person", "status": "not_rehearsed", "reason": "needs_a_person", "steps": []},
             {"area": "reception", "workflow": "bad", "status": "failed", "reason": "write_outside_rehearsal_folder", "steps": []}]}


@needs_node
def test_bodies_follow_the_routes_and_the_question_is_optional():
    out = run_node(f"""{preamble()}
      console.log(JSON.stringify({{
        none: m.explainBody(""), spaces: m.explainBody("   "), asked: m.explainBody("  what is written?  "),
        full: m.explainBody("x", {{ confirmCloud: true, approvalId: "ap1" }}),
        rehearse: m.rehearseBody(), path: m.stagePath("a b", "explain"),
        can: [m.canAsk(""), m.canAsk("x".repeat(2000)), m.canAsk("x".repeat(2001))],
      }}));
    """)
    assert out["none"] == {} and out["spaces"] == {} and out["asked"] == {"question": "what is written?"}
    assert out["full"] == {"question": "x", "confirm_cloud": True, "approval_id": "ap1"}
    assert out["rehearse"] == {} and out["path"] == "/gui/api/offices/stage/a%20b/explain"
    assert out["can"] == [True, True, False]


@needs_node
def test_every_answer_without_an_explanation_is_read_by_its_reason():
    pauses = [
        {"reason": "cloud_confirmation_required", "model": "cloud_fast", "tier": "cloud", "receives": ["a.md", "b.md"]},
        {"reason": "secret_review_required", "approval_id": "ap7"}, {"reason": "budget_cut"}, {"reason": "refused"},
        {"reason": "limit_reached", "limit": {"code": "limit_reached", "resets_at": None, "target": "local_default", "suggestions": []}},
        {"reason": "cloud_not_allowed"}, {"reason": "something_new"}, None]
    out = run_node(f"""{preamble("en")}
      const pauses = {json.dumps(pauses)};
      const fixed = {json.dumps({"stage_id": "s1", "reason": "office_not_normal", "explanation": {"summary": "x"}})};
      console.log(JSON.stringify({{ kinds: pauses.map((a) => m.answerKind(a)), views: pauses.map((a) => m.pauseView(a)),
        fixed: [m.answerKind(fixed), m.pauseView(fixed)] }}));
    """)
    assert out["kinds"] == ["cloud_confirmation_required", "secret_review_required", "budget_cut", "refused", "limit_reached",
                            "cloud_not_allowed", "something_new", "model_error"]
    cloud, secret, budget, refused, limit, not_allowed, other, none = out["views"]
    assert cloud["cloud"]["model"] == "cloud_fast" and cloud["cloud"]["receives"] == ["a.md", "b.md"]
    assert cloud["cloud"]["text"] == "The model is in the cloud. It would receive only the screened text listed here."
    assert cloud["cloud"]["sendLabel"] == "Send to cloud_fast" and cloud["cloud"]["filesText"] == "It receives 2 files."
    assert secret["approvalId"] == "ap7" and secret["link"]["href"] == "#approvals" and "ask again" in secret["text"]
    assert limit["text"].startswith("Usage limit reached") and limit["link"]["href"] == "#apps"
    assert "not called" in budget["text"] and "not called" in refused["text"]
    assert "does not allow remote models" in not_allowed["text"]
    assert other["text"] == none["text"] == "The explanation could not be prepared."
    assert out["fixed"] == ["explanation", None]      # a fixed explanation with a reason is still an explanation
    assert len({v["text"] for v in (cloud, secret, budget, refused, limit, not_allowed, other)}) == 7


@needs_node
def test_the_read_of_a_package_shows_tools_inputs_and_what_could_not_be_verified():
    out = run_node(f"""{preamble("en")}
      const report = {json.dumps(REPORT)};
      console.log(JSON.stringify({{ simple: m.reportView(report), advanced: m.reportView(report, {{ advanced: true }}),
        fixed: m.reportView({{ ...report, source: "deterministic", reason: "office_not_normal" }}),
        none: [m.reportView(null), m.reportView("x")] }}));
    """)
    simple, advanced, fixed = out["simple"], out["advanced"], out["fixed"]
    assert out["none"] == [None, None]
    assert simple["areas"][0]["text"] == "Reception writes a note." and "write_note" in simple["areas"][0]["workflowsText"]
    write, read, ghost = simple["tools"]
    assert write["readsText"] == "Reads: nothing declared" and write["writesText"] == "Writes: notes/summary.md"
    assert write["permissionText"] == "Permission: it asks for your approval each time."
    assert read["readsText"] == 'Reads: the input "topic"' and read["permissionText"].startswith("Permission: none needed")
    assert ghost["permissionText"].startswith("Permission: unknown") and ghost["known"] is False
    assert write["note"] == "Saves the note after you approve it." and read["note"] == ""
    assert simple["unasked"] == [{"where": "reception / write_note", "inputs": ["spare"]}]
    assert simple["missing"] == [{"where": "reception / write_note", "inputs": ["ghost"]}]
    assert simple["needsRoot"] == ["reception / write_note"]
    assert simple["unverifiedLines"] == ["reception / write_note: it runs code, so its inputs cannot be judged."]
    assert simple["codeNotChecked"] is True
    # simple mode: counts and no identifiers or paths; advanced: the paths, the identifiers and every finding
    assert simple["codeFiles"] == [] and simple["unverifiedFiles"] == [] and simple["findings"] == []
    assert simple["unverifiedFilesText"] == "1 files could not be checked." and simple["findingsCount"] == 1
    assert simple["tools"][0]["id"] == "" and advanced["tools"][0]["id"] == "file.write"
    assert advanced["codeFiles"] == ["areas/reception/functions.py"] and advanced["unverifiedFiles"] == ["areas/reception/logo.png"]
    assert [item["text"] for item in advanced["findings"]] == ["areas/reception/README.md, line 4: email"]
    # the fixed explanation says so and shows no model words; the model's answer only describes, it never edits the facts
    assert fixed["sourceText"] == "This is a fixed summary: no model was used." and fixed["whyText"].startswith("This office is protected")
    assert fixed["tools"][0]["note"] == "" and fixed["advice"] == "" and simple["advice"].startswith("This explanation is advice")
    assert simple["answer"] == "It writes notes/summary.md." and simple["findingsCount"] == advanced["findingsCount"]
    # what comes from the package or the model stays a plain string
    assert HOSTILE in simple["areas"][0]["name"] and HOSTILE in simple["summary"]


@needs_node
def test_the_trial_shows_steps_reasons_missing_inputs_and_files_in_both_languages():
    expected = {
        "en": {"untrusted_code": "Not tried: it uses code you have not accepted.",
               "needs_a_person": "Not tried: it needs a person at a step.",
               "write_outside": "Stopped: it tried to write outside the disposable folder.",
               "done": "Nothing was installed and your folder was not touched.", "files_none": "It would not have written any file."},
        "es": {"untrusted_code": "No probado: usa código que no has aceptado.",
               "needs_a_person": "No probado: necesita a una persona en un paso.",
               "write_outside": "Detenido: intentó escribir fuera de la carpeta desechable.",
               "done": "No se instaló nada y tu carpeta no se tocó.", "files_none": "No habría escrito ningún archivo."}}
    for language, words in expected.items():
        out = run_node(f"""{preamble(language)}
          const view = m.rehearsalView({json.dumps(TRIAL)});
          console.log(JSON.stringify({{ view, none: [m.rehearsalView(null), m.rehearsalView({{ state: "running" }})],
            failed: m.rehearsalView({{ state: "failed", flows: [], files: [] }}), empty: ui.t("ext.offices.ia.files_none") }}));
        """)
        view = out["view"]
        assert out["none"] == [None, None] and out["failed"]["failed"] is True and view["failed"] is False
        ran, coded, person, bad = view["flows"]
        assert ran["steps"] and "{" not in ran["steps"][0] and "draft" in ran["steps"][0]
        assert ran["syntheticText"] and ran["missingText"] and "ghost" in ran["missingText"]
        assert coded["reasonText"] == words["untrusted_code"] and person["reasonText"] == words["needs_a_person"]
        assert bad["reasonText"] == words["write_outside"]
        assert len({ran["title"], coded["title"], bad["title"]}) == 3
        assert view["doneText"] == words["done"] and out["empty"] == words["files_none"]
        # the file that tries to leave the folder is never listed; the real one is, with its size
        assert [item["path"] for item in view["files"]] == ["notes/summary.md"] and "14" in view["files"][0]["text"]
        assert view["omittedText"] and "2" in view["omittedText"]


@needs_node
def test_the_fixed_line_and_the_button_texts_exist_in_both_languages():
    en = run_node(f"""{preamble("en")}
      console.log(JSON.stringify([ui.t("ext.offices.ia.rehearse"), ui.t("ext.offices.ia.entry"), ui.t("ext.offices.ia.rehearse_line"),
        ui.t("ext.offices.ia.rehearse_running"), ui.t("ext.offices.ia.error.busy"), ui.t("ext.offices.ia.gate.required")]));
    """)
    assert en == ["Try in Mock", "Explain this office to me",
                  "Try in Mock runs in a temporary folder with made-up data, and the folder is deleted when it ends.",
                  "Trying the office in a disposable copy…", "Another trial is running.",
                  "This office writes files. Try it in Mock before installing."]
    es = run_node(f"""{preamble("es")}
      console.log(JSON.stringify([ui.t("ext.offices.ia.rehearse"), ui.t("ext.offices.ia.rehearse_running"),
        ui.t("ext.offices.ia.error.busy"), ui.t("ext.offices.ia.gate.required")]));
    """)
    assert es == ["Probar en Mock", "Probando la oficina en una copia desechable…", "Hay otra prueba en curso.",
                  "Esta oficina escribe archivos. Pruébala en Mock antes de instalarla."]


@needs_node
def test_a_writing_package_needs_a_trial_and_a_failed_trial_does_not_block():
    out = run_node(f"""{preamble("en")}
      const writes = {{ writes_files: true, rehearsal_required: true, rehearsal: null }};
      const readOnly = {{ writes_files: false, rehearsal_required: false, rehearsal: null }};
      const recorded = {{ ...writes, rehearsal: {{ state: "finished" }} }};
      const gates = {{
        required: m.rehearsalGate(writes), recommended: m.rehearsalGate(readOnly), none: m.rehearsalGate(undefined),
        done: m.rehearsalGate(writes, {{ state: "finished" }}), failed: m.rehearsalGate(writes, {{ state: "failed" }}),
        recorded: m.rehearsalGate(recorded), forced: m.rehearsalGate(recorded, null, {{ forced: true }}),
        forcedThenTrial: m.rehearsalGate(recorded, {{ state: "finished" }}, {{ forced: true }}),
        running: m.rehearsalGate(writes, {{ state: "running" }}),
      }};
      console.log(JSON.stringify(gates));
    """)
    assert out["required"]["blocksInstall"] is True and out["required"]["kind"] == "required"
    assert out["required"]["text"] == "This office writes files. Try it in Mock before installing."
    assert out["recommended"]["blocksInstall"] is False and out["recommended"]["kind"] == "recommended"
    assert "recommended" in out["recommended"]["text"] and out["none"]["blocksInstall"] is False
    assert out["done"]["blocksInstall"] is False and out["done"]["kind"] == "done"
    # a trial that failed is a trial that ended: the person reads it and decides with today's risk box
    assert out["failed"]["blocksInstall"] is False and out["failed"]["kind"] == "failed" and out["failed"]["done"] is True
    assert "does not block" in out["failed"]["text"]
    assert out["recorded"]["blocksInstall"] is False
    assert out["forced"]["blocksInstall"] is True and out["forcedThenTrial"]["blocksInstall"] is False
    assert out["running"]["blocksInstall"] is True


@needs_node
def test_the_409_of_the_install_is_read_by_its_reason_and_every_failure_has_words():
    out = run_node(f"""{preamble("en")}
      const f = (e) => m.failureView(e);
      console.log(JSON.stringify({{
        required: [m.rehearsalRequiredText({{ details: {{ reason: "office_rehearsal_required" }} }}),
                   m.rehearsalRequiredText({{ code: "rehearsal_required" }}),
                   m.rehearsalRequiredText({{ details: {{ reason: "risk_ack_required" }} }}), m.rehearsalRequiredText(null)],
        busy: f({{ details: {{ reason: "office_rehearsal_busy" }} }}), unsafe: f({{ details: {{ reason: "office_rehearsal_folder_unsafe" }} }}),
        none: f({{ details: {{ reason: "office_rehearsal_no_workflows" }} }}), unknown: f({{ details: {{ reason: "office_rehearsal_unknown_flow" }} }}),
        gone: f({{ details: {{ reason: "office_stage_expired" }} }}), changed: f({{ details: {{ reason: "office_changed_since_card" }} }}),
        invalid: f({{ details: {{ reason: "office_import_invalid_explanation" }} }}),
        question: [f({{ details: {{ field: "question" }} }}), f({{ details: {{ errors: [{{ loc: ["body", "question"] }}] }} }})],
        advanced: f({{ details: {{ reason: "advanced_mode_required", action: "install_code" }} }}),
        plain: f({{ message: "Something broke" }}), empty: f(null),
      }}));
    """)
    assert out["required"][0].startswith("Nothing was installed: this office writes files") and out["required"][0] == out["required"][1]
    assert out["required"][2:] == ["", ""]
    assert out["busy"]["text"] == "Another trial is running." and out["unsafe"]["text"].startswith("The temporary folder")
    assert out["none"]["text"] == "This office has no workflow to try." and out["unknown"]["text"].startswith("That workflow")
    assert out["gone"]["stale"] is True and out["changed"]["stale"] is True and out["busy"]["stale"] is False
    assert out["invalid"]["text"].startswith("The model's answer did not have the expected shape")
    assert out["question"][0]["text"] == out["question"][1]["text"] == "Write a question of 1 to 2000 characters."
    assert out["advanced"]["text"] and out["plain"]["text"] == "Something broke" and out["empty"]["text"]
    assert len({out[k]["text"] for k in ("busy", "unsafe", "none", "unknown", "gone", "invalid")}) == 6


# --- the card and its entry (static) -----------------------------------------------------------------------------------------

def test_the_card_and_the_install_button_of_today_stay_and_the_entry_sits_beside_them():
    plain = source("import.js")
    for fragment in ('<p class="offices-import-notice">${view.notice}</p>', "t(\"ext.offices.install.button\")",
                     "onClick=${install}>${busy ? t(\"ext.offices.import.installing\")", 't("ext.offices.import.risk_ack")',
                     't("ext.offices.import.review")', 'apiPost(BASE + "/stage-zip", token, body)',
                     'stagePath(card.stage_id) + "/install"', "needsScanDecision", "view.riskAckRequired"):
        assert fragment in plain, fragment
    assert plain.count("<${ImportAssistant}") == 1 and plain.count("decideApproval(") == 3      # definition, scan, install
    code, entry, scan = (plain.index(text) for text in ('<div class="offices-import-code">', "<${ImportAssistant}",
                                                        "${view.needsScanDecision && html`"))
    assert code < entry < scan
    assert 'from "./import-assistant.js"' in plain and plain.count("rehearsalGate(") == 1
    # a trial belongs to its card: a new card starts without one
    assert plain.count("setTrial(null)") >= 3


def test_the_conversation_cannot_approve_trust_or_install():
    box, model, plain = source("import-assistant.js"), source("import-assistant-model.js"), source("import.js")
    both = box + model
    assert not re.search(r"/install|/decision|/challenge|approvals/|riskAck|risk_ack|accept_code_risk|decideApproval|onDone", both)
    calls = re.findall(r"apiPost\((stagePath\([^)]*\)),", box)
    assert calls == ['stagePath(stageId, "rehearse")', 'stagePath(stageId, "explain")', 'stagePath(stageId, "explain")'], calls
    assert re.findall(r"apiGet\((stagePath\([^)]*\)),", box) == ['stagePath(stageId, "checks")']
    # the only install is the card's own button, one approval decided per card, and the gate stops it before the approval
    assert plain.count('stagePath(card.stage_id) + "/install"') == 1
    assert plain.index("if (gate && gate.blocksInstall)") < plain.index("await decideApproval(token, card.approval_id, {")
    assert "approvedForTrial.current !== card.approval_id" in plain
    assert not re.search(r"setTimeout|setInterval|requestAnimationFrame|location\.|localStorage|sessionStorage|indexedDB", both)


def test_the_trial_runs_one_at_a_time_and_says_so():
    box = source("import-assistant.js")
    assert "if (trying || disabled) return;" in box and "disabled=${trying || disabled}" in box
    assert 't("ext.offices.ia.rehearse_running")' in box and box.count("setTrying(true)") == 1
    assert 't("ext.offices.ia.rehearse_line")' in box and "&&" not in box[box.index('t("ext.offices.ia.rehearse_line")') - 40:
                                                                         box.index('t("ext.offices.ia.rehearse_line")')]
    assert 'role=${gate.kind === "required" ? "alert" : "status"}' in box


def test_simple_mode_shows_no_folder_no_code_diffs_and_no_risk_box_of_its_own():
    box, model, plain = source("import-assistant.js"), source("import-assistant-model.js"), source("import.js")
    assert "showIncludeCode && html`<label" in plain and plain.count('t("ext.offices.import.folder_path")') == 1
    assert 'type="checkbox"' not in box and "folder_path" not in box + model and 'type="text"' not in box
    assert "codeFiles: advanced ? codeFiles : []" in model and "unverifiedFiles: advanced ? unverifiedFiles : []" in model
    assert "findings: advanced ? findings : []" in model and "id: advanced ? text(row.tool) : \"\"" in model
    assert 'const advanced = mode === "advanced";' in box and "onModeChange(setMode)" in box


def test_what_comes_from_the_package_or_a_model_is_drawn_as_text():
    box, model = source("import-assistant.js"), source("import-assistant-model.js")
    assert not re.search(r"innerHTML|outerHTML|dangerouslySetInnerHTML|insertAdjacentHTML|document\.write|\.html\(|style=|eval\(|new Function",
                         box + model)
    assert "<img" not in box + model and "<script" not in box + model and "href=${notice.link.href}" in box
    assert "target=" not in box                                       # the extension rules forbid it; the link is the export one


def test_the_origin_notice_of_today_stays_visible():
    plain = source("import.js")
    assert plain.count("view.notice") == 1 and "offices-import-notice" in plain


def test_every_text_exists_in_both_languages_and_every_help_topic_is_written():
    box, model, plain = source("import-assistant.js"), source("import-assistant-model.js"), source("import.js")
    texts, help_js = source("i18n.js"), source("help.js")
    en_block, es_block = texts[:texts.index("const es = {")], texts[texts.index("const es = {"):]
    used = {key for key in re.findall(r'"(ext\.offices\.[a-z_.]+)"', box + model) if not key.endswith(".")}
    used |= {f"ext.offices.ia.gate.{kind}" for kind in ("required", "recommended", "failed", "done")}
    assert len(used) > 60
    for key in used:
        assert f'"{key}":' in en_block and f'"{key}":' in es_block, key
    own = set(re.findall(r'"(ext\.offices\.ia\.[a-z_.]+)":', en_block))
    assert own == set(re.findall(r'"(ext\.offices\.ia\.[a-z_.]+)":', es_block)) and len(own) > 70
    topics = set(re.findall(r'"(offices\.(?:import_assistant[a-z_]*|rehearse))"', box + plain))
    assert topics == {"offices.import_assistant", "offices.import_assistant_question", "offices.import_assistant_cloud",
                      "offices.rehearse"}
    for topic in topics:
        assert f'"{topic}": {{' in help_js and f'"help.{topic}": {{' in texts, topic


def test_no_help_id_repeats_while_the_card_and_the_conversation_are_open():
    box = source("import-assistant.js")
    pairs = re.findall(r'<\$\{Help\} topic="(offices\.[a-z_]+)" compact=\$\{true\} scope=\$\{([^}]+)\}', box)
    assert len(pairs) == len(set(pairs)), pairs
    assert ("offices.import_assistant", 'scope + "-panel"') in pairs and ("offices.import_assistant", "here") in pairs
    assert ("offices.rehearse", "scope") in pairs


def test_the_styles_are_scoped_fit_a_phone_and_have_forced_colors():
    css = source("style.css")
    new = css[css.index("OFA-T06"):css.index("OFA-T05")]
    assert "forced-colors: active" in new and "max-width: 599.98px" in new
    for selector in re.findall(r"^([.#@][^{]*?)\s*\{", new, re.M):
        assert selector.startswith((".offices-ia", "@media")), selector
    assert re.search(r"\.offices-ia \{[^}]*grid-template-columns: minmax\(0, 1fr\)", new)
    for rule in (".offices-ia-panel,", ".offices-ia-list {", ".offices-ia-actions {", ".offices-ia-gate {"):
        assert rule in new
    assert "overflow-wrap: anywhere" in new and "min-width: 0" in new and "width: 100%" in new
    assert "overflow-x" not in new and not re.search(r"(?<![-\w])width:\s*\d+px", new) and not re.search(r"#[0-9a-fA-F]{3,8}\b", new)
    phone = new[new.index("599.98px"):new.index("forced-colors")]
    assert "max-width: 100%" in phone and "overflow-wrap: anywhere" in phone


# --- round 4: the held secret review, the remembered card, stale results, area text and the note ----------------------------

def test_a_secret_review_keeps_its_approval_and_resends_it_until_an_answer_or_a_4xx():
    box = source("import-assistant.js")
    assert "const held = createHeldStore();" in box and 'from "./export-assistant-model.js"' in box
    assert box.count("approvalId: approval.current") == 2                      # both calls of ask (the plain one and the cloud yes)
    assert "held.set(stageId, { request: value, approvalId: paused.approvalId, confirmedModel: confirmedModel.current });" in box
    assert 'const approval = useRef(kept ? kept.approvalId : "");' in box and "held.get(stageId)" in box
    assert "useState(kept ? kept.request : \"\")" in box and "confirmedModel = useRef(kept ? kept.confirmedModel" in box
    # cleared by a valid explanation, by a 4xx that carried an approval and by closing the assistant
    assert 'approval.current = "";\n        held.clear(stageId);\n        setAnswer(reply);' in box
    assert "failed.status >= 400 && failed.status < 500) { approval.current = \"\"; held.clear(stageId); }" in box
    assert "onClick=${() => { held.clear(stageId); onClose(); }}" in box
    assert not re.search(r"localStorage|sessionStorage|indexedDB|document\.cookie", box)


@needs_node
def test_the_approval_id_travels_in_the_next_explain_body_and_a_question_alone_does_not_carry_one():
    out = run_node(f"""{preamble()}
      const store = (await import({json.dumps((EXT / "export-assistant-model.js").as_uri())})).createHeldStore();
      const pause = m.pauseView({{ reason: "secret_review_required", approval_id: "ap9" }});
      store.set("stage1", {{ request: "is it safe?", approvalId: pause.approvalId, confirmedModel: "" }});
      const back = store.get("stage1");
      const retry = m.explainBody(back.request, {{ approvalId: back.approvalId }});
      const other = store.get("stage2");
      store.clear("stage1");
      console.log(JSON.stringify({{ retry, other, after: store.get("stage1"), plain: m.explainBody("is it safe?") }}));
    """)
    assert out["retry"] == {"question": "is it safe?", "approval_id": "ap9"}
    assert out["other"] is None and out["after"] is None and out["plain"] == {"question": "is it safe?"}


def test_the_card_of_a_plain_import_survives_a_visit_to_approvals_in_memory_only():
    plain, box = source("import.js"), source("import-assistant.js")
    assert "let rememberedStage = null;" in plain and "const plainImport = !into && !reimport && !initialCard;" in plain
    assert "rememberedStage = card.stage_id" in plain
    assert "apiGet(stagePath(stageId), token).then(setCard)" in plain      # the route refreshCard already uses
    assert not re.search(r"localStorage|sessionStorage|indexedDB|document\.cookie", plain + box)
    # forgotten when installed, discarded (the scan refusal and Cancel), on a 4xx of the restore and of a refresh
    assert plain.count("if (plainImport) forgetStage();") == 4 and plain.count("forgetStage()") == 5
    install = plain[plain.index('"/install", token'):]
    assert install.index("forgetStage()") < install.index("onDone()")
    assert plain.count("failure?.status >= 400 && failure?.status < 500") == 2
    # the assistant that was open reopens, and forgetting the card forgets it and its held question
    assert "const openStages = new Set();" in box and "useState(() => openStages.has(stageId))" in box
    assert "export function forgetAssistant(stageId) { held.clear(stageId); openStages.delete(stageId); }" in box
    assert "forgetAssistant(rememberedStage)" in plain


def test_a_gone_package_clears_what_was_shown_about_it():
    box = source("import-assistant.js")
    assert box.count("setAnswer(null); setChecks(null);") == 2                  # the checks call and ask
    assert "if (failed.stale) onTrial(null);" in box
    assert "onTrial=${(result) => { setTrial(result); setForced(false); }}" in source("import.js")


@needs_node
def test_without_a_model_an_area_carries_no_text_and_the_note_is_the_import_one():
    out = run_node(f"""{preamble("en")}
      const report = {json.dumps(REPORT)};
      const baseline = {{ ...report, source: "deterministic", reason: "office_not_normal",
        explanation: {{ ...report.explanation, areas: [{{ area: "reception", text: "Reception: no workflows." }}] }} }};
      console.log(JSON.stringify({{ fixed: m.reportView(baseline).areas[0].text, model: m.reportView(report).areas[0].text,
        note: ui.t("ext.offices.ia.note"), view: m.reportView(report).noteText }}));
    """)
    assert out["fixed"] == "" and out["model"] == "Reception writes a note."
    assert out["note"] == "This only explains the package. It does not approve or install anything: you decide when you install."
    assert out["view"] == out["note"]
    es = run_node(f"""{preamble("es")}
      console.log(JSON.stringify(ui.t("ext.offices.ia.note")));
    """)
    assert es == "Esto solo explica el paquete. No aprueba ni instala nada: tú decides al instalar."
    box = source("import-assistant.js")
    assert 'role="note">${t("ext.offices.ia.note")}' in box and 'ext.offices.xa.note' not in box
