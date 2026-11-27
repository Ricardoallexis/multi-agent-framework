# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFA-T06: importing an office by talking. Everything the person is told comes from fixed checks; a model only words it."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from multiagent.adapters.fake import FakeAdapter
from multiagent.document_trust import FIXED_CLAUSE
from multiagent.errors import QuotaExceeded
from multiagent.offices.package_checks import (cross_check_inputs, describe_tools, flow_documents, flow_inputs,
                                               writes_files)
from multiagent.services import ServiceError
from multiagent.tools.files import register_file_tools
from multiagent.tools.mock import register_mock_tools
from multiagent.tools.registry import ToolRegistry
from test_office_install_card import _decide
from test_office_rehearsal import WRITE_FLOW, _client, _real_files, world, write_package  # noqa: F401

SAMPLE = {"summary": "This office drafts a short note and saves it.",
          "areas": [{"area": "reception", "text": "Reception writes a note."},
                    {"area": "invented", "text": "Not in the package."}],
          "tools": [{"tool": "file.write", "text": "Saves the note as a file after you approve it."},
                    {"tool": "invented.tool", "text": "Not declared."}],
          "answer": "It writes notes/summary.md."}


def _specs() -> dict:
    registry = ToolRegistry()
    register_mock_tools(registry)
    register_file_tools(registry, Path("."))
    return {spec.tool_id: spec for spec in registry.list()}


def _sample_adapter(**overrides) -> FakeAdapter:
    return FakeAdapter(sample_outputs={"OfficeImportExplanation": {**SAMPLE, **overrides}})


def _package(tmp_path: Path, *, readme: str | None = None, area: dict | None = None, **kwargs) -> Path:
    root = write_package(tmp_path / "demo", **kwargs)
    if readme is not None:
        (root / "areas" / "reception" / "README.md").write_text(readme, encoding="utf-8")
    if area:
        document = yaml.safe_load((root / "office.yaml").read_text(encoding="utf-8"))
        document["areas"][0].update(area)
        (root / "office.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return root


def _capture(services, monkeypatch) -> list[str]:
    prompts: list[str] = []
    adapter = services.system.engine.router.adapters["ollama"]
    original = adapter.generate_structured

    def record(**kwargs):
        prompts.append(kwargs["prompt"])
        return original(**kwargs)

    monkeypatch.setattr(adapter, "generate_structured", record)
    return prompts


# --- the fixed read of a package (pure functions) ---------------------------------------------------------------


def _files(flow: dict, prompts: dict[str, str] | None = None) -> dict[str, bytes]:
    files = {"areas/a/workflows/f.yaml": yaml.safe_dump(flow).encode("utf-8")}
    for name, text in (prompts if prompts is not None else {"p/one.v1.md": "Use {topic} and {previous_output}."}).items():
        files[f"areas/a/prompts/{name}"] = text.encode("utf-8")
    return files


def _flow(*, inputs=(), tools=None, function=None) -> dict:
    step = {"id": "s1", "agent": "x", "prompt_id": "p/one", "prompt_version": 1, "contract": "C"}
    if tools:
        step["tools"] = tools
    steps = [step]
    if function:
        steps.append({"id": "s2", "function": function, "arguments": {"v": "${topic}"}, "contract": "C"})
    return {"id": "f", "inputs": [{"name": name} for name in inputs], "steps": steps}


def test_cross_check_names_inputs_nobody_asks_for_and_data_nobody_provides():
    flow = _flow(inputs=["topic", "spare", "tool_only"], tools=[
        {"capability": "file.write", "arguments": {"path": "a.md", "content": "${tool_only}"}},
        {"capability": "file.write", "arguments": {"path": "b.md", "content": "${ghost}"}}])
    files = _files(flow)
    row = cross_check_inputs(files, flow_documents(files, [("a", "areas/a")]))[0]
    assert row["unasked"] == ["spare"]            # a prompt uses topic, a tool argument uses tool_only
    assert row["missing"] == ["ghost"]
    assert row["unverified"] is False


def test_cross_check_does_not_guess_when_code_or_prompts_are_out_of_view():
    flow = _flow(inputs=["spare"], function="demo.fn")
    files = _files(flow)
    row = cross_check_inputs(files, flow_documents(files, [("a", "areas/a")]))[0]
    assert (row["unverified"], row["reason"], row["unasked"]) == (True, "function_step", [])
    flow = _flow(inputs=["spare"])
    files = _files(flow, prompts={})
    row = cross_check_inputs(files, flow_documents(files, [("a", "areas/a")]))[0]
    assert (row["unverified"], row["reason"], row["unasked"]) == (True, "prompt_not_in_package", [])
    broken = {"areas/a/workflows/f.yaml": b"steps: [", "areas/a/workflows/g.yaml": b"- just a list"}
    rows = cross_check_inputs(broken, flow_documents(broken, [("a", "areas/a")]))
    assert [row["reason"] for row in rows] == ["workflow_unreadable"] * 2
    assert flow_inputs(_files(_flow()), flow_documents(_files(_flow()), [("a", "areas/a")])[0])["placeholders"] == {"topic"}


def test_tools_say_what_they_read_write_and_ask():
    flow = _flow(tools=[
        {"tool": "file.read", "arguments": {"path": "notes.md"}},
        {"capability": "file.write", "arguments": {"path": "out/${x}.md", "content": "${topic}"}},
        {"tool": "file.write", "arguments": {"path": "${where}", "content": "t"}},
        {"tool": "file.move", "arguments": {"source": "a.md", "destination": "b.md"}},
        {"tool": "vendor.unknown", "arguments": {}}])
    files = _files(flow)
    rows = describe_tools(flow_documents(files, [("a", "areas/a")]), _specs())
    read, literal, dynamic, move, unknown = rows
    assert read["impact"] == "read" and read["permission"] == "none" and read["writes"] == []
    assert read["reads"] == [{"argument": "path", "path": "notes.md", "from_input": None}]
    assert literal["impact"] == "write" and literal["permission"] == "each_call"
    assert literal["writes"] == [{"argument": "path", "path": "out/${x}.md", "from_input": None}]
    assert dynamic["writes"] == [{"argument": "path", "path": None, "from_input": "where"}]
    assert "the input «where»" in dynamic["sentence"] and "asks for your approval each time" in dynamic["sentence"]
    assert [item["path"] for item in move["writes"]] == ["b.md"] or len(move["writes"]) == 2
    assert unknown["known"] is False and unknown["impact"] == "unknown" and "would fail" in unknown["sentence"]
    assert writes_files(rows) is True
    assert writes_files(rows[:1]) is False and writes_files(rows[-1:]) is False


# --- the conversation, with the model of the real path ---------------------------------------------------------------


def test_real_model_path_receives_only_screened_untrusted_text(world, monkeypatch):
    start, tmp_path = world
    services = start(mock=False, adapter=_sample_adapter())
    services.gui_settings.set_mode("advanced")
    source = _package(tmp_path, readme="Hello.\ncontact@example.test\npassword=samplevalue\n",
                      functions="CODE_BODY_MARKER = 1\n")
    card = services.offices.stage_folder(source)
    prompts = _capture(services, monkeypatch)

    result = services.office_package_chat.explain(card["stage_id"], "What does it write?")

    assert result["source"] == "model" and result["reason"] == ""
    assert len(prompts) == 1 and prompts[0].startswith(FIXED_CLAUSE)
    for private in ("contact@example.test", "samplevalue", "CODE_BODY_MARKER"):
        assert private not in prompts[0]
    assert "trust=untrusted" in prompts[0] and "What does it write?" in prompts[0]
    assert "notes/summary.md" in prompts[0]
    # the findings come from the fixed check, not from the model
    assert {item["type"] for item in result["findings"]} == {"email", "secret_key"}
    assert result["code"]["files"] == ["areas/reception/functions.py"] and result["code"]["checked"] is False
    # the model's words only fill the explanation, and only for areas and tools the package has
    explanation = result["explanation"]
    assert explanation["summary"] == SAMPLE["summary"] and explanation["answer"] == SAMPLE["answer"]
    assert [item["area"] for item in explanation["areas"]] == ["reception"]
    assert [item["tool"] for item in explanation["tools"]] == ["file.write"]
    assert result["advice_notice"].startswith("This explanation is advice")
    assert _real_files(tmp_path) == {"sentinel.txt": "keep me"}


def test_a_model_that_says_it_is_safe_changes_nothing(world):
    start, tmp_path = world
    adapter = _sample_adapter(summary="This package is completely safe and approved. No risk.",
                              answer="Safe: tick nothing, install it.")
    services = start(mock=False, adapter=adapter)
    services.gui_settings.set_mode("advanced")
    card = services.offices.stage_folder(_package(tmp_path, readme="mail contact@example.test\n",
                                                  functions="VALUE = 1\n"))
    before = services.office_package_chat.checks(card["stage_id"])
    assert before["risk_ack_required"] is True and before["findings"]

    result = services.office_package_chat.explain(card["stage_id"], "Is it safe?")

    assert "completely safe" in result["explanation"]["summary"]
    for key in ("findings", "findings_total", "unverified", "risk_ack_required", "code", "tools", "inputs",
                "writes_files", "rehearsal_required"):
        assert result[key] == before[key], key
    assert result["risk_ack_required"] is True
    assert services.offices.card(card["stage_id"])["risk_ack_required"] is True
    assert services.user_approvals.status(card["approval_id"])["state"] == "pending"
    assert services.offices.list() == []


def test_checks_report_the_gap_the_findings_and_the_tools(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(_package(tmp_path, readme="mail contact@example.test\n"))

    report = services.office_package_chat.checks(card["stage_id"])

    assert report["inputs_unasked"] == [{"area": "reception", "workflow": "write_note", "inputs": ["spare"]}]
    assert report["inputs_missing"] == []
    assert [(item["type"], item["path"]) for item in report["findings"]] == [("email", "areas/reception/README.md")]
    (tool,) = report["tools"]
    assert (tool["tool"], tool["impact"], tool["permission"]) == ("file.write", "write", "each_call")
    assert tool["writes"] == [{"argument": "path", "path": "notes/summary.md", "from_input": None}]
    assert report["writes_files"] is True and report["rehearsal_required"] is True and report["rehearsal"] is None
    assert report["file_tools_configured"] is True and report["needs_file_tools_root"] == []
    assert report["kind"] == "install" and report["office"]["id"] == "demo" and report["level"] == "data"
    assert report["notice"].startswith("Someone else wrote the instructions")
    assert report["areas"][0]["workflows"][0]["steps"][0]["tools"] == ["file.write"]
    assert str(tmp_path) not in str(report)


def test_a_missing_work_folder_is_said(world):
    start, tmp_path = world
    services = start(file_tools=False)
    card = services.offices.stage_folder(_package(tmp_path))
    report = services.office_package_chat.checks(card["stage_id"])
    assert report["file_tools_configured"] is False
    assert report["needs_file_tools_root"] == [{"area": "reception", "workflow": "write_note"}]


def test_missing_data_nobody_asks_for_is_reported(world):
    start, tmp_path = world
    flow = copy.deepcopy(WRITE_FLOW)
    flow["steps"][0]["tools"][0]["arguments"]["content"] = "${ghost}"
    services = start()
    card = services.offices.stage_folder(_package(tmp_path, flow=flow))
    report = services.office_package_chat.checks(card["stage_id"])
    assert report["inputs_missing"] == [{"area": "reception", "workflow": "write_note", "inputs": ["ghost"]}]


@pytest.mark.parametrize("area", [{"sensitivity": "sensitive"}, {"local_only": True}, {}])
def test_protected_or_unleveled_office_with_a_cloud_model_calls_nothing(world, monkeypatch, area):
    start, tmp_path = world
    services = start(model="cloud_fast", adapter=_sample_adapter())
    card = services.offices.stage_folder(_package(tmp_path, area=area))
    asked: list[str] = []
    monkeypatch.setattr(services.new_session, "_ask", lambda prompt, **kw: asked.append(prompt))

    result = services.office_package_chat.explain(card["stage_id"], confirm_cloud=True)

    assert asked == []
    assert result["source"] == "deterministic"
    assert result["reason"] == "office_not_normal"
    assert result["explanation"]["summary"].startswith("Demo office brings 1 area(s)")
    assert [row["tool"] for row in result["explanation"]["tools"]] == ["file.write"]
    assert result["findings"] == [] and result["tools"][0]["impact"] == "write"


def test_a_normal_new_office_has_no_cloud_permission_yet(world, monkeypatch):
    start, tmp_path = world
    services = start(model="cloud_fast", adapter=_sample_adapter())
    card = services.offices.stage_folder(_package(tmp_path, area={"sensitivity": "normal"}))
    asked: list[str] = []
    monkeypatch.setattr(services.new_session, "_ask", lambda prompt, **kw: asked.append(prompt))

    result = services.office_package_chat.explain(card["stage_id"], confirm_cloud=True)

    assert asked == [] and result["reason"] == "cloud_not_allowed" and result["source"] == "deterministic"


def test_a_sensitive_installed_office_keeps_its_level_when_reimported(world, monkeypatch):
    start, tmp_path = world
    services = start(model="cloud_fast", adapter=_sample_adapter())
    first = services.offices.stage_folder(_package(tmp_path / "v1", area={"sensitivity": "sensitive"}))
    services.office_rehearsal.run(first["stage_id"])
    _decide(services, first["approval_id"], risk_ack=False)
    services.offices.install_staged(first["stage_id"], approval_id=first["approval_id"])
    assert services.offices.privacy("demo")["areas"][0]["sensitivity"] == "sensitive"
    # the new version declares a lower level; the install keeps the higher one, and so does the conversation
    second_root = _package(tmp_path / "v2", area={"sensitivity": "normal"})
    again = services.offices.stage_folder(second_root, reimport="demo")
    asked: list[str] = []
    monkeypatch.setattr(services.new_session, "_ask", lambda prompt, **kw: asked.append(prompt))

    result = services.office_package_chat.explain(again["stage_id"], confirm_cloud=True)

    assert asked == [] and result["reason"] == "office_not_normal" and result["kind"] == "reimport"
    assert services.offices.privacy("demo")["areas"][0]["sensitivity"] == "sensitive"


def test_local_model_explains_even_a_sensitive_office(world):
    start, tmp_path = world
    services = start(mock=False, adapter=_sample_adapter())
    card = services.offices.stage_folder(_package(tmp_path, area={"sensitivity": "sensitive", "local_only": True}))
    result = services.office_package_chat.explain(card["stage_id"])
    assert result["source"] == "model" and result["reason"] == ""


def test_question_is_screened_and_validated(world, monkeypatch):
    start, tmp_path = world
    services = start(mock=False, adapter=_sample_adapter())
    card = services.offices.stage_folder(_package(tmp_path))
    prompts = _capture(services, monkeypatch)
    services.office_package_chat.explain(card["stage_id"], "Mail me at asker@example.test please")
    assert "asker@example.test" not in prompts[0]
    for bad in ("", "   ", "x" * 2001):
        with pytest.raises(ServiceError) as caught:
            services.office_package_chat.explain(card["stage_id"], bad)
        assert caught.value.status == 422 and caught.value.details["field"] == "question"
    assert len(prompts) == 1


def test_invalid_output_and_limits_are_reported(world, monkeypatch):
    start, tmp_path = world
    services = start(mock=False, adapter=FakeAdapter(sample_outputs={"OfficeImportExplanation": {"summary": ""}}))
    card = services.offices.stage_folder(_package(tmp_path))
    with pytest.raises(ServiceError) as caught:
        services.office_package_chat.explain(card["stage_id"])
    assert caught.value.details["reason"] == "office_import_invalid_explanation"
    adapter = services.system.engine.router.adapters["ollama"]

    def limited(**kwargs):
        raise QuotaExceeded("Synthetic limit")

    monkeypatch.setattr(adapter, "generate_structured", limited)
    held = services.office_package_chat.explain(card["stage_id"])
    assert held["reason"] == "limit_reached" and held["limit"]["code"] == "limit_reached"


def test_an_unavailable_model_falls_back_to_the_fixed_explanation(world, monkeypatch):
    start, tmp_path = world
    services = start(mock=False, adapter=_sample_adapter())
    card = services.offices.stage_folder(_package(tmp_path))
    monkeypatch.setattr(services.new_session, "_ask", lambda prompt, **kw: (_ for _ in ()).throw(OSError("down")))
    result = services.office_package_chat.explain(card["stage_id"])
    assert result["source"] == "deterministic" and result["reason"] == "model_unavailable"


def test_code_stays_an_advanced_mode_matter(world):
    start, tmp_path = world
    services = start(mock=False, adapter=_sample_adapter())
    services.gui_settings.set_mode("advanced")
    card = services.offices.stage_folder(_package(tmp_path, functions="VALUE = 1\n"))
    assert card["import_checks"]["unverified"] == [] and card["risk_ack_required"] is True
    services.gui_settings.set_mode("simple")
    for call in (services.office_package_chat.checks, services.office_package_chat.explain,
                 services.office_rehearsal.run):
        with pytest.raises(ServiceError):
            call(card["stage_id"])


def test_unreadable_files_are_listed_and_never_sent(world, monkeypatch):
    start, tmp_path = world
    services = start(mock=False, adapter=_sample_adapter())
    services.gui_settings.set_mode("advanced")      # an unreadable file counts as unverified code
    source = _package(tmp_path)
    (source / "areas" / "reception" / "logo.png").write_bytes(b"\x89PNG\x00\x01binary")
    card = services.offices.stage_folder(source)
    prompts = _capture(services, monkeypatch)
    result = services.office_package_chat.explain(card["stage_id"])
    assert result["unverified"] == ["areas/reception/logo.png"] and "binary" not in prompts[0] and "PNG" not in prompts[0]
    assert card["import_checks"]["unverified"] == ["areas/reception/logo.png"]


# --- the routes -----------------------------------------------------------------------------------------------------


def test_routes_shape_errors_and_authentication(world):
    start, tmp_path = world
    services = start(mock=False, adapter=_sample_adapter())
    card = services.offices.stage_folder(_package(tmp_path))
    client, headers = _client(services)
    base = f"/gui/api/offices/stage/{card['stage_id']}"
    with client:
        assert client.get(base + "/checks").status_code == 401
        assert client.post(base + "/explain", json={}).status_code == 401
        checks = client.get(base + "/checks", headers=headers)
        assert checks.status_code == 200, checks.text
        assert set(checks.json()) >= {"stage_id", "fingerprint", "kind", "office", "level", "areas", "tools",
                                      "inputs", "inputs_unasked", "inputs_missing", "code", "findings",
                                      "unverified", "writes_files", "rehearsal_required", "rehearsal",
                                      "risk_ack_required", "notice", "file_tools_configured",
                                      "needs_file_tools_root"}
        plain = client.post(base + "/explain", headers=headers, json={})
        assert plain.status_code == 200, plain.text
        assert plain.json()["source"] == "model" and "explanation" in plain.json()
        asked = client.post(base + "/explain", headers=headers, json={"question": "What is written?"})
        assert asked.json()["explanation"]["answer"] == SAMPLE["answer"]
        assert client.post(base + "/explain", headers=headers, json={"question": ""}).status_code == 422
        assert client.post(base + "/explain", headers=headers, json={"extra": 1}).status_code == 422
        assert client.post(base + "/explain", headers=headers, json={"confirm_cloud": "yes"}).status_code == 422
        for method, suffix in (("get", "/checks"), ("post", "/explain")):
            missing = getattr(client, method)(f"/gui/api/offices/stage/{'0' * 32}{suffix}", headers=headers,
                                              **({"json": {}} if method == "post" else {}))
            assert missing.status_code == 409
            assert missing.json()["detail"]["details"]["reason"] == "office_stage_not_found"
        # the card and the install button of today are still there, and nothing was installed or approved
        stage = client.get(base, headers=headers)
        assert stage.status_code == 200 and stage.json()["approval_id"] == card["approval_id"]
        assert "import_checks" in stage.json()
    assert services.offices.list() == []
    assert services.user_approvals.status(card["approval_id"])["state"] == "pending"

# --- round 2: cloud permission per area, what is attached, no double screening -----------------------------------------


def _normal_office(root: Path, areas: tuple[str, ...], **kwargs) -> Path:
    """A read-only office whose areas are all leveled normal (an unleveled area is protected)."""
    from test_offices_install import write_office

    return write_office(root, areas=areas, overrides={area: {"sensitivity": "normal"} for area in areas}, **kwargs)


def _install_demo(services, tmp_path: Path, name: str = "v1") -> None:
    """Install a one-area, read-only office ``demo`` (no rehearsal is needed: nothing in it writes)."""
    card = services.offices.stage_folder(_normal_office(tmp_path / name, ("reception",)))
    _decide(services, card["approval_id"], risk_ack=False)
    services.offices.install_staged(card["stage_id"], approval_id=card["approval_id"])


def _cloud_allowed_everywhere(services, monkeypatch, areas: tuple[str, ...] = ("reception",)) -> None:
    """Report cloud use as allowed for the named installed areas (granting it takes a one-use approval in the GUI)."""
    monkeypatch.setattr(services.offices, "privacy", lambda office_id: {
        "office": office_id, "local_only": False, "cloud": {}, "note": "",
        "areas": [{"id": area, "data_class": "non_sensitive", "cloud_effective": True} for area in areas]})


def _asked(services, monkeypatch) -> list[str]:
    asked: list[str] = []

    def ask(prompt, **kwargs):
        asked.append(prompt)
        return SAMPLE

    monkeypatch.setattr(services.new_session, "_ask", ask)
    return asked


def test_a_new_area_has_no_cloud_permission_even_when_every_installed_area_has_it(world, monkeypatch):
    start, tmp_path = world
    services = start(model="cloud_fast", adapter=_sample_adapter())
    _install_demo(services, tmp_path)
    _cloud_allowed_everywhere(services, monkeypatch)
    extra = services.offices.stage_folder(_normal_office(tmp_path / "extra", ("proposal",), office_id="extra"),
                                          into="demo")
    assert extra["kind"] == "area"
    asked = _asked(services, monkeypatch)

    result = services.office_package_chat.explain(extra["stage_id"], confirm_cloud=True)

    assert asked == [] and result["reason"] == "cloud_not_allowed" and result["source"] == "deterministic"


def test_a_reimport_of_the_same_areas_keeps_the_cloud_permission(world, monkeypatch):
    start, tmp_path = world
    services = start(model="cloud_fast", adapter=_sample_adapter())
    _install_demo(services, tmp_path)
    _cloud_allowed_everywhere(services, monkeypatch)
    again = services.offices.stage_folder(_normal_office(tmp_path / "v2", ("reception",)), reimport="demo")
    asked = _asked(services, monkeypatch)

    held = services.office_package_chat.explain(again["stage_id"])
    result = services.office_package_chat.explain(again["stage_id"], confirm_cloud=True)

    assert held["reason"] == "cloud_confirmation_required"
    assert len(asked) == 1 and result["source"] == "model"


def test_a_reimport_with_a_new_area_has_no_cloud_permission(world, monkeypatch):
    start, tmp_path = world
    services = start(model="cloud_fast", adapter=_sample_adapter())
    _install_demo(services, tmp_path)
    _cloud_allowed_everywhere(services, monkeypatch)
    again = services.offices.stage_folder(_normal_office(tmp_path / "v2", ("reception", "proposal")),
                                          reimport="demo")
    asked = _asked(services, monkeypatch)

    result = services.office_package_chat.explain(again["stage_id"], confirm_cloud=True)

    assert asked == [] and result["reason"] == "cloud_not_allowed"


def test_the_cloud_confirmation_lists_exactly_what_would_be_sent(world, monkeypatch):
    start, tmp_path = world
    services = start(model="cloud_fast", adapter=_sample_adapter())
    _install_demo(services, tmp_path)
    _cloud_allowed_everywhere(services, monkeypatch)
    services.gui_settings.set_mode("advanced")           # the package below carries code
    root = _normal_office(tmp_path / "v2", ("reception",))
    area = root / "areas" / "reception"
    (area / "README.md").write_text("A short readme.\n", encoding="utf-8")
    (area / "functions.py").write_text("VALUE = 'codemarker'\n", encoding="utf-8")
    (area / "page.html").write_text("<p>htmlmarker</p>\n", encoding="utf-8")
    (area / "big.md").write_text("bigmarker " * 2000, encoding="utf-8")           # over the per-file budget
    again = services.offices.stage_folder(root, reimport="demo")
    asked = _asked(services, monkeypatch)

    held = services.office_package_chat.explain(again["stage_id"])
    services.office_package_chat.explain(again["stage_id"], confirm_cloud=True)

    assert held["reason"] == "cloud_confirmation_required"
    receives = held["receives"]
    assert "areas/reception/README.md" in receives
    for left_out in ("functions.py", "page.html", "big.md"):
        assert not any(path.endswith(left_out) for path in receives), receives
    assert all(path.rsplit(".", 1)[-1] in {"yaml", "yml", "md", "txt", "json", "csv"} for path in receives)
    for marker in ("codemarker", "htmlmarker", "bigmarker"):
        assert marker not in asked[0]
    # and everything listed is in the prompt
    assert all(f"Path: {path}\n" in asked[0] for path in receives)


def test_the_report_screens_the_package_once(world, monkeypatch):
    from multiagent.services import office_package_chat as chat_module

    start, tmp_path = world
    services = start(mock=False, adapter=_sample_adapter())
    card = services.offices.stage_folder(_package(tmp_path))
    calls: list[int] = []
    original = chat_module.check_files

    def counted(files):
        calls.append(len(files))
        return original(files)

    monkeypatch.setattr(chat_module, "check_files", counted)

    services.office_package_chat.checks(card["stage_id"])
    assert len(calls) == 1
    calls.clear()
    services.office_package_chat.explain(card["stage_id"])
    assert len(calls) == 1
    assert "import_checks" in services.offices.card(card["stage_id"])          # the card itself is unchanged


def test_a_tool_that_does_not_use_the_work_folder_does_not_claim_it():
    flow = _flow(tools=[{"capability": "mock.echo", "arguments": {"text": "hi"}}])
    rows = describe_tools(flow_documents(_files(flow), [("a", "areas/a")]), _specs())
    echo = next(row for row in rows if row["tool"] == "mock.echo")
    assert echo["uses_file_tools_root"] is False and "work folder" not in echo["sentence"]
