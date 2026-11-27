# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFA-T05: «Prepare the export with the assistant», the GUI side: the three routes under /gui/api/offices/{id}/export
(draft, preview, download) as the box uses them, the pure rules of export-assistant-model.js run in Node, and static checks
of the box (only its download button produces the file, the buttons of today stay, simple mode has no code option, every text
exists in both languages and the styles fit a phone)."""
from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import zipfile

import pytest

from multiagent.gui import STATIC_DIR
from test_office_export_chat import _client, real_world  # noqa: F401  (real_world is a fixture)

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
JS = STATIC_DIR / "js"
EXT = STATIC_DIR / "extensions" / "offices"
ROUTE = "/gui/api/offices/demo/export/"
NOTE_EN = "Pattern checks cannot guarantee that no sensitive data remains. Review the package before sharing."
NOTE_ES = "Los detectores de patrones no garantizan que no queden datos sensibles. Revisa el paquete antes de compartirlo."


def source(name: str) -> str:
    return (EXT / name).read_text(encoding="utf-8").replace("\r\n", "\n")


# --- the routes, as the box uses them --------------------------------------------------------------------------------------

def test_routes_need_the_token_and_validate_the_body(real_world):
    services, _, _ = real_world()
    client, headers = _client(services)
    with client:
        for step, body in (("draft", {"text": "x"}), ("preview", {"version": 1}), ("download", {"version": 1})):
            assert client.post(ROUTE + step, json=body).status_code in (401, 403)
        assert client.post(ROUTE + "draft", headers=headers, json={"text": ""}).status_code == 422
        assert client.post(ROUTE + "draft", headers=headers, json={"text": "x" * 2001}).status_code == 422
        assert client.post(ROUTE + "draft", headers=headers, json={"text": "x", "extra": 1}).status_code == 422
        assert client.post(ROUTE + "preview", headers=headers, json={"version": 0}).status_code == 422
        assert client.post(ROUTE + "download", headers=headers, json={}).status_code == 422
        assert client.post("/gui/api/offices/absent/export/draft", headers=headers, json={"text": "x"}).status_code == 404
        blank = client.post(ROUTE + "draft", headers=headers, json={"text": "   "})
        assert blank.status_code == 422 and blank.json()["detail"]["details"]["field"] == "text"


def test_the_box_flow_drafts_checks_and_only_download_returns_a_file(real_world):
    services, _, _ = real_world()
    client, headers = _client(services)
    with client:
        draft = client.post(ROUTE + "draft", headers=headers, json={"text": "I want to share this office"})
        assert draft.status_code == 200, draft.text
        card = draft.json()
        assert card["source"] == "model" and card["version"] >= 1 and card["notice"]
        assert "zip_base64" not in card
        # The box asks for the preview with the omissions the model suggested; it never gets bytes from it.
        choice = {"version": card["version"], "omit": card["suggested_omit"]}
        preview = client.post(ROUTE + "preview", headers=headers, json=choice)
        assert preview.status_code == 200 and "zip_base64" not in preview.json()
        assert preview.json()["omitted"] == sorted(card["suggested_omit"]) and preview.json()["blocked"] is False
        file = client.post(ROUTE + "download", headers=headers, json=choice)
        assert file.status_code == 200, file.text
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(file.json()["zip_base64"]))) as archive:
            names = archive.namelist()
        assert names and not any(name in names for name in card["suggested_omit"])


def test_errors_the_box_reads_by_their_reason(real_world):
    services, source_folder, package = real_world()
    client, headers = _client(services)
    with client:
        card = client.post(ROUTE + "draft", headers=headers, json={"text": "Share it"}).json()
        # 409 advanced_mode_required: code is refused in simple mode on the three routes.
        refused = client.post(ROUTE + "draft", headers=headers, json={"text": "Share it", "include_code": True})
        assert refused.status_code == 409 and refused.json()["detail"]["details"]["reason"] == "advanced_mode_required"
        # 422 office_export_check_failed: where each finding is, and no file.
        (package / "README.md").write_text("Contact contact@example.test", encoding="utf-8")
        failed = client.post(ROUTE + "download", headers=headers, json={"version": card["version"]})
        assert failed.status_code == 422
        details = failed.json()["detail"]["details"]
        assert details["reason"] == "office_export_check_failed" and "zip_base64" not in failed.text
        assert details["found"] and {"path", "line", "type"} <= set(details["found"][0])
        # 409 office_export_stale: the office changed after the draft.
        (source_folder / "areas" / "reception" / "README.md").write_text("New version.", encoding="utf-8")
        services.offices.reimport_folder("demo", source_folder, actor="user:local")
        stale = client.post(ROUTE + "preview", headers=headers, json={"version": card["version"]})
        assert stale.status_code == 409 and stale.json()["detail"]["details"]["reason"] == "office_export_stale"


def test_the_pause_answers_carry_a_reason_and_no_draft(real_world, monkeypatch):
    from multiagent.errors import QuotaExceeded
    services, _, _ = real_world()
    adapter = services.system.engine.router.adapters["ollama"]

    def limited(**kwargs):
        raise QuotaExceeded("Synthetic limit")

    monkeypatch.setattr(adapter, "generate_structured", limited)
    client, headers = _client(services)
    with client:
        paused = client.post(ROUTE + "draft", headers=headers, json={"text": "Share it"}).json()
    assert paused["reason"] == "limit_reached" and paused["limit"]["code"] == "limit_reached"
    assert "version" not in paused and "summary" not in paused


# --- the pure rules (Node) -------------------------------------------------------------------------------------------------

def preamble(language: str = "es") -> str:
    return f"""
      const ui = await import({json.dumps((JS / "i18n.js").as_uri())});
      await import({json.dumps((EXT / "i18n.js").as_uri())});
      ui.setLanguage({json.dumps(language)});
      const m = await import({json.dumps((EXT / "export-assistant-model.js").as_uri())});
      const im = await import({json.dumps((EXT / "import-model.js").as_uri())});
      globalThis.fetch = () => {{ throw new Error('the model must not touch the network'); }};
    """


def run_node(script: str):
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


CARD = {"source": "model", "reason": "", "version": 3, "summary": "A synthetic review office.", "keep": [],
        "suggested_omit": ["areas/reception/samples/example.txt"], "questions": ["Share the examples?"],
        "included": ["areas/reception/README.md", "areas/reception/samples/example.txt"],
        "excluded": ["areas/reception/functions.py"], "unverified": ["areas/reception/big.txt"],
        "findings": [{"path": "areas/reception/README.md", "line": 4, "type": "email"},
                     {"path": "/abs/outside.txt", "line": 1, "type": "email"}],
        "local_models": ["reception: sample_local"], "local_models_travel": False, "notice": NOTE_EN}
PREVIEW = {"version": 3, "included": ["areas/reception/README.md"], "omitted": ["areas/reception/samples/example.txt"],
           "findings": [], "unverified": [], "blocked": False, "notice": NOTE_EN}


@needs_node
def test_bodies_follow_the_routes_and_corrections_join_the_request():
    out = run_node(f"""{preamble()}
      const card = {json.dumps(CARD)};
      console.log(JSON.stringify({{
        plain: m.draftBody("  share it  "),
        full: m.draftBody("share it", {{ includeCode: true, confirmCloud: true, approvalId: "ap1" }}),
        choice: m.choiceBody(card),
        code: m.choiceBody(card, {{ includeCode: true, acceptCodeRisk: true }}),
        codeNoRisk: m.choiceBody(card, {{ includeCode: true }}),
        orphan: m.choiceBody(null),
        composed: m.composeText("  share it ", " leave out the examples "),
        same: m.composeText("share it", "  "),
        can: [m.canDraft(""), m.canDraft(" "), m.canDraft("x"), m.canDraft("x".repeat(2001)),
              m.canRedraft("share it", "no examples"), m.canRedraft("share it", ""), m.canRedraft("x".repeat(1999), "ab")],
        path: m.exportPath("demo office", "download"),
        code_in: [m.includeCodeAllowed("simple"), m.includeCodeAllowed("advanced")],
      }}));
    """)
    assert out["plain"] == {"text": "share it"}
    assert out["full"] == {"text": "share it", "include_code": True, "confirm_cloud": True, "approval_id": "ap1"}
    assert out["choice"] == {"version": 3, "include_code": False, "omit": ["areas/reception/samples/example.txt"]}
    assert out["code"]["accept_code_risk"] is True and out["code"]["include_code"] is True
    assert "accept_code_risk" not in out["codeNoRisk"]
    assert out["orphan"] == {"version": 0, "include_code": False, "omit": []}
    assert out["composed"] == "share it\n\nCorrection: leave out the examples" and out["same"] == "share it"
    assert out["can"] == [False, False, True, False, True, False, False]
    assert out["path"] == "/gui/api/offices/demo%20office/export/download"
    assert out["code_in"] == [False, True]


@needs_node
def test_every_pause_is_read_by_its_reason_first():
    pauses = [
        {"reason": "cloud_confirmation_required", "model": "cloud_fast", "tier": "cloud", "receives": ["a.md", "b.md"]},
        {"reason": "cloud_not_allowed"},
        {"reason": "secret_review_required", "approval_id": "ap7"},
        {"reason": "budget_cut"}, {"reason": "refused"},
        {"reason": "limit_reached", "limit": {"code": "limit_reached", "resets_at": None, "target": "local_default", "suggestions": []}},
        {"reason": "something_new"}, None,
    ]
    out = run_node(f"""{preamble()}
      const views = {json.dumps(pauses)}.map((a) => m.pauseView(a));
      console.log(JSON.stringify({{ views, kinds: {json.dumps(pauses)}.map((a) => m.answerKind(a)),
        card: m.pauseView({json.dumps(CARD)}), withReason: m.answerKind({{ ...{json.dumps(CARD)}, reason: "office_not_normal" }}) }}));
    """)
    cloud, not_allowed, secret, budget, refused, limit, other, none = out["views"]
    assert out["kinds"][:6] == ["cloud_confirmation_required", "cloud_not_allowed", "secret_review_required", "budget_cut",
                                "refused", "limit_reached"]
    assert out["kinds"][6:] == ["something_new", "model_error"]
    assert cloud["cloud"]["model"] == "cloud_fast" and cloud["cloud"]["receives"] == ["a.md", "b.md"]
    assert cloud["cloud"]["text"] == "Los archivos de la oficina se enviarán a cloud_fast, un modelo remoto."
    assert "puede tener un costo" in cloud["cloud"]["costText"] and cloud["cloud"]["sendLabel"] == "Enviar a cloud_fast"
    assert cloud["cloud"]["filesText"] == "Recibe 2 archivos."
    assert secret["approvalId"] == "ap7" and secret["link"]["href"] == "#approvals"
    assert "Aprobaciones" in secret["text"]
    assert limit["text"].startswith("Límite alcanzado") and "Ningún modelo se cambia solo" in limit["text"]
    assert limit["link"]["href"] == "#apps"
    assert "no se llamó al modelo" in budget["text"] and "no se llamó al modelo" in refused["text"]
    assert "no permite modelos remotos" in not_allowed["text"] and not_allowed["cloud"] is None
    assert other["text"] == none["text"] == "No se pudo redactar el paquete."
    assert out["card"] is None and out["withReason"] == "card"      # a summary prepared without a model is still a card
    assert len({v["text"] for v in (cloud, not_allowed, secret, budget, refused, limit, other)}) == 7


@needs_node
def test_the_card_shows_what_the_server_checked_and_download_needs_a_clean_preview():
    blocked = {**PREVIEW, "findings": [{"path": "areas/reception/README.md", "line": 4, "type": "email"}],
               "unverified": ["areas/reception/img.bin"], "blocked": True}
    out = run_node(f"""{preamble()}
      const card = {json.dumps(CARD)};
      const fallback = {json.dumps({**CARD, "source": "deterministic", "reason": "office_not_normal"})};
      console.log(JSON.stringify({{
        simple: m.cardView(card, {json.dumps(PREVIEW)}),
        advanced: m.cardView(card, {json.dumps(PREVIEW)}, {{ advanced: true }}),
        unchecked: m.cardView(card),
        blocked: m.cardView(card, {json.dumps(blocked)}, {{ advanced: true }}),
        blockedSimple: m.cardView(card, {json.dumps(blocked)}),
        fallback: m.cardView(fallback, {json.dumps(PREVIEW)}),
        none: m.cardView({{ reason: "budget_cut" }}),
        note: m.fixedNote(),
      }}));
    """)
    simple, advanced = out["simple"], out["advanced"]
    assert simple["summary"] == "A synthetic review office." and simple["version"] == 3
    assert simple["included"] == ["areas/reception/README.md"] and simple["omitted"] == ["areas/reception/samples/example.txt"]
    assert simple["countsText"] == "Se incluirán 1 archivos y se dejarán fuera 1."
    assert simple["questions"] == ["Share the examples?"] and simple["localModels"] == ["reception: sample_local"]
    assert simple["canDownload"] is True and simple["checked"] is True and simple["sourceText"] == "Redactado por un modelo"
    # simple mode: no detail lists, only counts; advanced adds them
    assert simple["excluded"] == [] and simple["unverified"] == [] and simple["findings"] == []
    assert advanced["excluded"] == ["areas/reception/functions.py"]
    assert out["unchecked"]["canDownload"] is False and out["unchecked"]["checked"] is False
    # a finding on a path outside the package is dropped, never shown (checked below with the advanced view)
    assert out["unchecked"]["blocked"] is True and out["unchecked"]["findingsCount"] == 1
    detail = out["blocked"]
    assert detail["blocked"] is True and detail["canDownload"] is False
    assert detail["findings"][0]["text"] == "areas/reception/README.md, línea 4: email"
    assert detail["unverified"] == ["areas/reception/img.bin"]
    assert out["blockedSimple"]["findings"] == [] and out["blockedSimple"]["findingsCount"] == 1
    assert out["blockedSimple"]["unverifiedCount"] == 1
    assert out["fallback"]["sourceText"] == "Resumen preparado en este equipo, sin modelo"
    assert "no hay un modelo local" in out["fallback"]["whyText"]
    assert out["none"] is None and out["note"] == NOTE_ES
    unchecked_findings = run_node(f"""{preamble()}
      console.log(JSON.stringify(m.cardView({json.dumps(CARD)}, null, {{ advanced: true }}).findings));
    """)
    assert [item["path"] for item in unchecked_findings] == ["areas/reception/README.md"]


@needs_node
def test_every_failure_has_its_own_words_and_the_check_failure_says_where():
    errors = [
        {"details": {"reason": "office_export_stale"}}, {"details": {"reason": "advanced_mode_required", "action": "office_export_code"}},
        {"details": {"reason": "office_export_check_failed", "unverified": ["img.bin", "../x"],
                     "found": [{"path": "areas/a/README.md", "line": 2, "type": "email"}, {"path": "C:\\\\x", "line": 1, "type": "email"}]}},
        {"details": {"reason": "office_export_invalid_proposal"}}, {"details": {"reason": "office_export_code_risk_required"}},
        {"details": {"reason": "export_path_not_portable"}}, {"details": {"field": "text"}},
        {"message": "boom", "details": {}}, None,
    ]
    out = run_node(f"""{preamble()}
      console.log(JSON.stringify({json.dumps(errors)}.map((e) => m.failureView(e))));
    """)
    stale, advanced, check, invalid, risk, portable, empty, plain, none = out
    assert stale["stale"] is True and "cambió" in stale["text"] and not any(v["stale"] for v in out[1:])
    assert advanced["text"] == "Esta acción necesita el modo avanzado."
    assert check["lines"] == ["areas/a/README.md, línea 2: email", "img.bin: no se pudo comprobar"]
    assert check["text"].startswith("No se creó ningún archivo")
    assert "forma esperada" in invalid["text"] and "riesgos" in risk["text"] and "lista portable" in portable["text"]
    assert empty["text"] == "Escribe una petición de 1 a 2000 caracteres."
    assert plain["text"] == "boom" and none["text"] == "No se pudo redactar el paquete."
    assert len({v["text"] for v in (stale, check, invalid, risk, portable, empty)}) == 6


@needs_node
def test_texts_in_both_languages_and_the_direct_button_warning():
    for language, note, personal in (("es", NOTE_ES, "datos personales"), ("en", NOTE_EN, "personal data")):
        out = run_node(f"""{preamble(language)}
          console.log(JSON.stringify({{ note: m.fixedNote(), warn: im.exportWarningText({{ code: "export_personal_data_found" }}),
            other: im.exportWarningText({{ code: "export_model_not_portable" }}),
            plain: im.exportWarningText({{ code: "something" }}),
            pause: m.pauseView({{ reason: "budget_cut" }}).text, card: m.cardView({json.dumps(CARD)}, {json.dumps(PREVIEW)}).countsText }}));
        """)
        assert out["note"] == note
        assert personal in out["warn"] and ("review" in out["warn"].lower() or "revísalo" in out["warn"].lower())
        assert len({out["warn"], out["other"], out["plain"]}) == 3
        assert out["pause"] and out["card"] and "{" not in out["card"]
    en = run_node(f"""{preamble("en")}
      console.log(JSON.stringify({{ cloud: m.pauseView({{ reason: "cloud_confirmation_required", model: "cloud_fast", receives: [] }}).cloud,
        limit: m.pauseView({{ reason: "limit_reached", limit: {{ resets_at: null }} }}).text }}));
    """)
    assert en["cloud"]["costText"] == "This model receives screened, redacted office text. Remote use may cost money."
    assert en["limit"].startswith("Usage limit reached") and en["cloud"]["sendLabel"] == "Send to cloud_fast"


# --- the box and its entry (static) ---------------------------------------------------------------------------------------

def test_only_the_download_button_produces_the_file_and_nothing_runs_by_itself():
    box, model = source("export-assistant.js"), source("export-assistant-model.js")
    assert box.count('exportPath(officeId, "download")') == 1 and box.count("exportOffice(") == 1
    assert box.count("async function download()") == 1 and box.count("onClick=${download}") == 1
    assert "download()" not in box.replace("async function download()", "")
    assert "disabled=${busy || !view.canDownload || codeStale || codeNeedsRisk}" in box
    # the server returns a file from no other step the box calls
    assert 'post("draft"' in box and 'post("preview"' in box and box.count("apiPost(") == 2
    assert not re.search(r"setTimeout|setInterval|requestAnimationFrame|\.click\(|createObjectURL|location\.", box + model)
    assert not re.search(r"innerHTML|dangerouslySetInnerHTML|insertAdjacentHTML|style=", box + model)
    assert not re.search(r"<select|<option|type=\"radio\"|template|preset", box + model, re.I)
    # the draft is asked from the form and from «Change something» and the cloud notice, never from an effect
    assert "useEffect(() => { if (answer && heading.current) heading.current.focus(); }, [answer]);" in box
    assert box.count("useEffect(") == 3 and "ask(" in box and "download(" not in re.sub(r"async function download\(\)|onClick=\$\{download\}", "", box)


def test_the_buttons_of_today_stay_and_the_entry_sits_beside_them():
    index, plain = source("index.js"), source("import.js")
    for key in ("ext.offices.export.office", "ext.offices.export.area", "ext.offices.reimport", "ext.offices.add_area",
                "ext.offices.privacy.button", "ext.offices.remove.button"):
        assert f't("{key}")' in index, key
    assert index.count("onClick=${() => doExport()}") == 1 and index.count("onClick=${() => doExport(row.id)}") == 1
    assert "exportOffice({ token, officeId: office.id, area, includeCode, post: apiPost })" in index
    assert 'BASE + "/" + encodeURIComponent(officeId) + "/export"' in plain      # the direct route is untouched
    assert index.count('t("ext.offices.xa.entry")') == 1 and index.count("<${ExportAssistant}") == 1
    entry, export_button, reimport = (index.index(text) for text in (
        't("ext.offices.xa.entry")', 'onClick=${() => doExport()}', 'scope: officeScope + "-reimport"'))
    assert export_button < entry < reimport
    assert 'from "./export-assistant.js"' in index and index.count("ExportAssistant") == 2


def test_simple_mode_has_no_code_option():
    box = source("export-assistant.js")
    assert box.count('t("ext.offices.include_code")') == 1
    assert re.search(r'\$\{advanced && html`<label class="check-inline">\s*<input type="checkbox" checked=\$\{includeCode\}', box)
    assert "const withCode = advanced && includeCode;" in box
    assert box.count("includeCode: withCode") == 3 and "includeCode: includeCode" not in box
    assert 'includeCode: drafted.includeCode' in box and "setDrafted({ includeCode: withCode })" in box
    assert box.count('${withCode && html`<label class="check-inline">') == 1


def test_the_fixed_note_is_always_visible_and_not_behind_a_condition():
    box = source("export-assistant.js")
    note = box.index("fixedNote()")
    line = box[box.rindex("\n", 0, note):box.index("\n", note)]
    assert 'class="offices-xa-note" role="note"' in line and "&&" not in line
    before = box[:note]
    assert before.rindex("return html`<section") > before.rindex("const canSend")


def test_every_text_exists_in_both_languages_and_every_help_topic_is_written():
    box, model, index = source("export-assistant.js"), source("export-assistant-model.js"), source("index.js")
    texts, help_js = source("i18n.js"), source("help.js")
    en_block, es_block = texts[:texts.index("const es = {")], texts[texts.index("const es = {"):]
    keys = set(re.findall(r'"(ext\.offices\.(?:xa\.[a-z_.]+|export\.personal_data))"', box + model + index + source("import-model.js")))
    keys |= set(re.findall(r'"(ext\.offices\.xa\.[a-z_.]+)"', texts))
    assert len(keys) > 50
    for key in keys:
        assert f'"{key}":' in en_block and f'"{key}":' in es_block, key
    assert NOTE_EN in en_block and NOTE_ES in es_block
    topics = set(re.findall(r'"(offices\.export_assistant[a-z_]*)"', box + index))
    assert topics == {"offices.export_assistant", "offices.export_assistant_text", "offices.export_assistant_cloud",
                      "offices.export_assistant_change", "offices.export_assistant_download", "offices.export_assistant_code"}
    for topic in topics:
        assert f'"{topic}": {{' in help_js and f'"help.{topic}": {{' in texts, topic
    assert "style=" not in box and "style=" not in index


def test_the_styles_are_scoped_fit_a_phone_and_have_forced_colors():
    css = source("style.css")
    new = css[css.index("OFA-T05"):]
    assert "forced-colors: active" in new and "max-width: 599.98px" in new
    for selector in re.findall(r"^([.#@][^{]*?)\s*\{", new, re.M):
        assert selector.startswith((".offices-xa", "@media")), selector
    for rule in (".offices-xa {", ".offices-xa-card,", ".offices-xa-list {", ".offices-xa-actions {"):
        assert rule in new
    assert "overflow-wrap: anywhere" in new and "min-width: 0" in new and "width: 100%" in new
    assert "overflow-x" not in new and not re.search(r"(?<![-\w])width:\s*\d+px", new) and not re.search(r"#[0-9a-fA-F]{3,8}\b", new)


# --- round 4: held request, help ids, long corrections, FastAPI 422, phone styles ----------------------------------------

@needs_node
def test_a_request_waiting_for_a_secret_decision_survives_unmounting():
    out = run_node(f"""{preamble()}
      const store = m.createHeldStore();
      // mount: a secret pause arrives for office "demo"; the box keeps the request and the approval id
      const pause = m.pauseView({{ reason: "secret_review_required", approval_id: "ap9" }});
      store.set("demo", {{ request: "share it", approvalId: pause.approvalId, confirmedModel: "cloud_fast" }});
      // unmount (a visit to #approvals), mount again: the row is there for that office only
      const back = store.get("demo");
      const retry = m.draftBody(back.request, {{ approvalId: back.approvalId }});
      const other = store.get("another");
      // a draft arrived: the row is cleared
      store.clear("demo");
      console.log(JSON.stringify({{ back, retry, other, after: store.get("demo") }}));
    """)
    assert out["back"] == {"request": "share it", "approvalId": "ap9", "confirmedModel": "cloud_fast"}
    assert out["retry"] == {"text": "share it", "approval_id": "ap9"}
    assert out["other"] is None and out["after"] is None


def test_the_held_request_is_cleared_by_a_draft_a_close_or_a_refused_approval_and_never_stored_in_the_browser():
    box, index = source("export-assistant.js"), source("index.js")
    assert "held.set(officeId, { request: value, approvalId: view.approvalId" in box
    assert box.count("held.clear(officeId)") == 3          # a draft, the close button, a 4xx that carried an approval
    assert 'held.clear(officeId);\n        baseText.current = value;' in box
    assert "useState(() => assistantHeld(office.id))" in index
    assert "const approval = useRef(kept ? kept.approvalId : \"\")" in box
    assert not re.search(r"localStorage|sessionStorage|indexedDB|document\.cookie", box + source("export-assistant-model.js"))


def test_no_help_id_repeats_while_the_assistant_is_open():
    box, index = source("export-assistant.js"), source("index.js")
    found = []
    for text in (index, box):
        for topic, scope in re.findall(r'<\$\{Help\} topic="(offices\.export_assistant[a-z_]*)" compact=\$\{true\} scope=\$\{([^}]+)\}', text):
            found.append((topic, scope.replace("officeScope", "officeId")))
    assert ("offices.export_assistant", "officeId + \"-panel\"") in found
    assert ("offices.export_assistant", "officeId") in found                      # the entry in the card
    assert len(found) == len(set(found)), found
    assert 'helpFor("offices.export_assistant", { scope: officeId + "-panel" })' in box


@needs_node
def test_a_correction_that_does_not_fit_says_so_in_both_languages():
    for language, fragment in (("es", "pasan de 2000"), ("en", "pass 2000")):
        out = run_node(f"""{preamble(language)}
          console.log(JSON.stringify({{ long: m.correctionTooLong("x".repeat(1990), "a longer correction"),
            fits: m.correctionTooLong("share it", "no examples"), empty: m.correctionTooLong("x".repeat(1999), "  "),
            text: ui.t("ext.offices.xa.too_long") }}));
        """)
        assert out["long"] is True and out["fits"] is False and out["empty"] is False
        assert fragment in out["text"]
    assert "correctionTooLong(baseText.current, correction)" in source("export-assistant.js")


@needs_node
def test_the_validation_error_of_the_framework_reads_like_the_text_error():
    out = run_node(f"""{preamble()}
      console.log(JSON.stringify([
        m.failureView({{ message: "Invalid request", details: {{ errors: [{{ loc: ["body", "text"], msg: "too long" }}] }} }}).text,
        m.failureView({{ details: {{ field: "text" }} }}).text,
        m.failureView({{ message: "Invalid request", details: {{ errors: [{{ loc: ["body", "version"] }}] }} }}).text,
      ]));
    """)
    assert out[0] == out[1] == "Escribe una petición de 1 a 2000 caracteres."
    assert out[2] == "Invalid request"


def test_the_phone_styles_bound_the_grid_the_notice_and_the_buttons():
    css = source("style.css")
    new = css[css.index("OFA-T05"):]
    assert re.search(r"\.offices-xa \{[^}]*grid-template-columns: minmax\(0, 1fr\)", new)
    assert re.search(r"\.offices-xa-notice \{[^}]*min-width: 0", new)
    phone = new[new.index("599.98px"):new.index("forced-colors")]
    assert "max-width: 100%" in phone and "overflow-wrap: anywhere" in phone
