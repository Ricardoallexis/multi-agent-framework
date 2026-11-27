# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFA-T06: «Try in Mock» rehearses a staged office in a disposable system and never touches the real folder."""
from __future__ import annotations

import copy
import os
import socket
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.office_rehearsal import OfficeRehearsalServices, PREFIX, _inside
from test_office_install_card import _decide, _reason
from test_offices_install import write_office

WRITE_FLOW = {
    "id": "write_note", "description": "Writes a short note.", "max_iterations": 1,
    "inputs": [{"name": "topic", "label": "Topic"}, {"name": "spare", "label": "Never asked"}],
    "steps": [{
        "id": "extract_requirements", "description": "Drafts the note.", "agent": "requirements_analyst",
        "prompt_id": "spec/extract_requirements", "prompt_version": 1, "skills": [], "contract": "ResearchOutput",
        "preferred_model": "local_default", "fallback_model": None, "requires_web": False, "when": None,
        "checkpoint_after": False,
        "tools": [{"capability": "file.write", "arguments": {"path": "notes/summary.md", "content": "${topic}"}}],
    }],
}


def write_package(root: Path, *, flow: dict | None = None, tool_path: str | None = None,
                  functions: str | None = None) -> Path:
    """An office of one area whose only workflow uses ``file.write``; ``functions`` adds a ``functions.py``."""
    write_office(root, areas=("reception",))
    area = root / "areas" / "reception"
    team = yaml.safe_load((area / "team.yaml").read_text(encoding="utf-8"))
    team["roles"][0]["permissions"] = ["read", "write"]        # a team must allow the tools its workflows declare
    (area / "team.yaml").write_text(yaml.safe_dump(team, sort_keys=False), encoding="utf-8")
    document = copy.deepcopy(flow or WRITE_FLOW)
    if tool_path is not None:
        document["steps"][0]["tools"][0]["arguments"]["path"] = tool_path
    (area / "workflows" / "spec_review.yaml").unlink()
    (area / "workflows" / f"{document['id']}.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    if functions is not None:
        (area / "functions.py").write_text(functions, encoding="utf-8")
    return root


@pytest.fixture
def world(tmp_path):
    opened: list[ApplicationServices] = []

    def start(*, mock: bool = True, file_tools: bool = True, adapter=None, model: str = "local_default"):
        real = tmp_path / "real-files"
        real.mkdir(exist_ok=True)
        (real / "sentinel.txt").write_text("keep me", encoding="utf-8")
        settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=mock,
                            bundle_scan="off", gemini_api_key="", openai_api_key="", openai_enabled=False,
                            gui_token_file=tmp_path / "gui-token", gui_new_session_model=model,
                            file_tools_root=real if file_tools else None,
                            # empty homes: the card's requirements would read this machine's session logs otherwise
                            gui_claude_home=tmp_path / "home-a", gui_codex_home=tmp_path / "home-b",
                            gui_copilot_home=tmp_path / "home-c", gui_antigravity_home=tmp_path / "home-d")
        provider = "ollama" if model == "local_default" else "gemini"
        services = ApplicationServices.create(
            settings, dry_run=mock, adapter_overrides={provider: adapter} if adapter is not None else None)
        services.office_rehearsal.base = tmp_path / "rehearsals"
        opened.append(services)
        return services

    yield start, tmp_path
    for services in opened:
        services.bundle_scans.close()
        services.runs.close()


def _real_files(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "real-files"
    return {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*")) if path.is_file()}


def _client(services):
    token = ensure_token(services.settings.gui_token_file)
    return TestClient(create_app(services=services), base_url="http://localhost"), {"Authorization": f"Bearer {token}"}


def test_rehearsal_runs_the_flow_and_names_what_it_would_write(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))
    before = _real_files(tmp_path)
    runs_before = services.runs.list()

    result = services.office_rehearsal.run(card["stage_id"])

    assert result["state"] == "finished" and result["flows_total"] == 1
    flow = result["flows"][0]
    assert (flow["area"], flow["workflow"], flow["status"]) == ("reception", "write_note", "completed")
    assert [step["state"] for step in flow["steps"]] == ["ran"]
    assert flow["tools"] == [{"tool": "file.write", "status": "completed"}]
    assert [item["path"] for item in flow["files"]] == ["notes/summary.md"]
    assert result["files"] == flow["files"]
    assert "spare" in flow["inputs"]["synthetic"] and flow["inputs"]["missing"] == []
    assert result["actor"] == "user:gui" and "Nothing was installed" in result["notice"]
    # the person's folder, list of runs and installed offices are exactly as they were
    assert _real_files(tmp_path) == before
    assert services.runs.list() == runs_before == []
    assert services.offices.list() == []
    assert not (services.settings.data_dir / "bundles").exists()
    assert str(tmp_path) not in str(result)


def test_sentinel_in_the_real_folder_stays_intact_even_when_the_flow_names_it(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo", tool_path="sentinel.txt"))

    result = services.office_rehearsal.run(card["stage_id"])

    assert result["flows"][0]["status"] == "completed"
    assert (tmp_path / "real-files" / "sentinel.txt").read_text(encoding="utf-8") == "keep me"
    assert _real_files(tmp_path) == {"sentinel.txt": "keep me"}


def test_configured_root_is_never_inherited(world, monkeypatch):
    start, tmp_path = world
    services = start(file_tools=False)       # the person has no work folder at all
    monkeypatch.setenv("FILE_TOOLS_ROOT", str(tmp_path / "real-files"))
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))

    result = services.office_rehearsal.run(card["stage_id"])

    assert result["flows"][0]["status"] == "completed"
    assert [item["path"] for item in result["files"]] == ["notes/summary.md"]
    assert _real_files(tmp_path) == {"sentinel.txt": "keep me"}


def test_no_provider_and_no_network_are_used(world, monkeypatch):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))

    def refuse(*args, **kwargs):
        raise AssertionError("the rehearsal reached a provider or the network")

    from multiagent.adapters.gemini import GeminiAdapter
    from multiagent.adapters.ollama import OllamaAdapter
    from multiagent.adapters.openai_cloud import OpenAIAdapter
    for adapter in (OllamaAdapter, GeminiAdapter, OpenAIAdapter):
        monkeypatch.setattr(adapter, "generate_structured", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)

    result = services.office_rehearsal.run(card["stage_id"])

    assert result["state"] == "finished" and result["flows"][0]["status"] == "completed"


def test_write_outside_the_temporary_folder_is_refused(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo", tool_path="../escaped.md"))

    result = services.office_rehearsal.run(card["stage_id"])

    flow = result["flows"][0]
    assert result["state"] == "failed" and flow["status"] == "failed"
    assert flow["reason"] == "write_outside_rehearsal_folder"
    assert not (tmp_path / "escaped.md").exists() and not (tmp_path / "real-files" / "escaped.md").exists()
    assert not any(tmp_path.rglob("escaped.md"))


@pytest.mark.parametrize(("target", "inside"), [
    ("notes/a.md", True), ("a.md", True), ("../a.md", False), ("/etc/a.md", False), ("\\a.md", False),
    ("C:\\a.md", False), ("", False), (None, False), ("a\x00.md", False)])
def test_only_a_relative_path_inside_the_folder_is_approved(tmp_path, target, inside):
    assert _inside(tmp_path, target) is inside


def test_folder_is_deleted_when_it_ends_and_when_it_fails(world, monkeypatch):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))
    base = tmp_path / "rehearsals"

    first = services.office_rehearsal.run(card["stage_id"])
    assert first["folder_deleted"] is True and list(base.iterdir()) == []

    def explode(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(OfficeRehearsalServices, "_flow", explode)
    second = services.office_rehearsal.run(card["stage_id"])
    assert second["state"] == "failed" and second["reason"] == "rehearsal_error"
    assert second["error_type"] == "RuntimeError" and second["folder_deleted"] is True
    assert list(base.iterdir()) == []


def test_functions_of_an_untrusted_package_are_not_run(world):
    start, tmp_path = world
    services = start()
    services.gui_settings.set_mode("advanced")
    marker = tmp_path / "functions-ran"
    flow = {"id": "calc", "max_iterations": 1, "inputs": [{"name": "topic"}], "steps": [
        {"id": "compute", "function": "demo.static", "arguments": {"value": "${topic}"}, "contract": "ResearchOutput"},
        {"id": "note", "agent": "requirements_analyst", "prompt_id": "spec/extract_requirements",
         "prompt_version": 1, "contract": "ResearchOutput", "preferred_model": "local_default",
         "tools": [{"capability": "file.write", "arguments": {"path": "a.md", "content": "${topic}"}}]}]}
    source = ("from pathlib import Path\nfrom multiagent.functions import register_function\n"
              f"Path({str(marker)!r}).write_text('ran')\n"
              "def helper(context, arguments):\n    return {}\n"
              "register_function('demo.static', helper, description='Static registration')\n")
    card = services.offices.stage_folder(write_package(tmp_path / "demo", flow=flow, functions=source))

    result = services.office_rehearsal.run(card["stage_id"])

    entry = result["flows"][0]
    assert entry["status"] == "not_rehearsed" and entry["reason"] == "untrusted_code"
    assert [(step["id"], step["state"]) for step in entry["steps"]] == [
        ("compute", "not_rehearsed"), ("note", "not_rehearsed")]
    assert entry["steps"][0]["reason"] == "untrusted_code"
    assert not marker.exists() and result["files"] == []
    assert result["state"] == "finished"


def test_contracts_py_of_an_untrusted_package_is_not_run(world):
    start, tmp_path = world
    services = start()
    services.gui_settings.set_mode("advanced")
    marker = tmp_path / "contracts-ran"
    card_source = write_package(tmp_path / "demo")
    (card_source / "areas" / "reception" / "contracts.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\nOUTPUT_SCHEMAS = {{}}\n", encoding="utf-8")
    card = services.offices.stage_folder(card_source)

    result = services.office_rehearsal.run(card["stage_id"])

    assert result["flows"][0]["status"] == "not_rehearsed" and not marker.exists()


def test_missing_data_is_reported_and_the_failed_rehearsal_is_kept(world):
    start, tmp_path = world
    flow = {**WRITE_FLOW, "steps": [{**WRITE_FLOW["steps"][0], "tools": [
        {"capability": "file.write", "arguments": {"path": "a.md", "content": "${ghost}"}}]}]}
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo", flow=flow))

    result = services.office_rehearsal.run(card["stage_id"])

    entry = result["flows"][0]
    assert entry["inputs"]["missing"] == ["ghost"]
    assert result["state"] == "failed" and entry["status"] == "failed"
    assert services.office_rehearsal.last(card["stage_id"])["state"] == "failed"
    assert _real_files(tmp_path) == {"sentinel.txt": "keep me"}


def test_one_rehearsal_at_a_time_and_a_time_limit(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))
    assert services.office_rehearsal._lock.acquire(blocking=False)
    try:
        assert _reason(services.office_rehearsal.run, card["stage_id"]).details["reason"] == "office_rehearsal_busy"
    finally:
        services.office_rehearsal._lock.release()

    services.office_rehearsal.limit_seconds = -1.0
    result = services.office_rehearsal.run(card["stage_id"])
    assert result["state"] == "failed" and result["flows"][0]["status"] == "timed_out"
    assert result["flows"][0]["reason"] == "time_limit" and list((tmp_path / "rehearsals").iterdir()) == []


def test_orphan_folders_are_swept_by_prefix_and_age(tmp_path, monkeypatch):
    base = tmp_path / "tmp" / "maf-rehearsals"
    old, fresh, other = base / f"{PREFIX}old", base / f"{PREFIX}fresh", base / "other-folder"
    for folder in (old, fresh, other):
        (folder / "work").mkdir(parents=True)
        (folder / "work" / "a.md").write_text("x", encoding="utf-8")
    stale = time.time() - 3600
    os.utime(old, (stale, stale))
    os.utime(other, (stale, stale))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "tmp"))
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        bundle_scan="off", gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")

    services = ApplicationServices.create(settings, dry_run=True)      # the sweep runs when the services start
    try:
        assert not old.exists() and fresh.exists() and other.exists()
        assert services.office_rehearsal.sweep(older_than=0) == 1 and not fresh.exists() and other.exists()
    finally:
        services.bundle_scans.close()
        services.runs.close()


def test_the_temporary_folder_is_refused_inside_the_work_folder(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))
    services.office_rehearsal.base = tmp_path / "real-files" / "inside"

    error = _reason(services.office_rehearsal.run, card["stage_id"])

    assert error.details["reason"] == "office_rehearsal_folder_unsafe"
    assert _real_files(tmp_path) == {"sentinel.txt": "keep me"}


def test_unknown_flow_and_empty_package_errors(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))
    assert _reason(services.office_rehearsal.run, card["stage_id"], area="absent").details["reason"] == \
        "office_rehearsal_unknown_flow"
    assert _reason(services.office_rehearsal.run, card["stage_id"], workflow="absent").details["reason"] == \
        "office_rehearsal_unknown_flow"
    assert _reason(services.office_rehearsal.run, "0" * 32).details["reason"] == "office_stage_not_found"
    named = services.office_rehearsal.run(card["stage_id"], area="reception", workflow="write_note")
    assert named["flows"][0]["status"] == "completed"
    assert _reason(services.office_rehearsal.run, card["stage_id"], actor="agent:worker").code != ""


# --- the guard of install_staged -------------------------------------------------------------------------------


def test_install_needs_a_rehearsal_only_when_tools_write_files(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))
    assert card["import_checks"]["writes_files"] is True and card["import_checks"]["rehearsal_required"] is True
    assert card["import_checks"]["rehearsal"] is None
    _decide(services, card["approval_id"])

    refused = _reason(services.offices.install_staged, card["stage_id"], approval_id=card["approval_id"])

    assert refused.code == "rehearsal_required" and refused.status == 409
    assert refused.details == {"reason": "office_rehearsal_required", "stage_id": card["stage_id"]}
    assert services.offices.list() == []
    assert services.user_approvals.status(card["approval_id"])["state"] == "approved"      # not consumed by the refusal

    services.office_rehearsal.run(card["stage_id"])
    again = services.offices.card(card["stage_id"])
    assert again["import_checks"]["rehearsal"]["state"] == "finished"
    installed = services.offices.install_staged(card["stage_id"], approval_id=card["approval_id"])
    assert installed["office"]["id"] == "demo" and services.offices.get("demo")["state"] == "ok"


def test_a_failed_rehearsal_does_not_block_the_install(world):
    start, tmp_path = world
    services = start()
    flow = {**WRITE_FLOW, "steps": [{**WRITE_FLOW["steps"][0], "tools": [
        {"capability": "file.write", "arguments": {"path": "a.md", "content": "${ghost}"}}]}]}
    card = services.offices.stage_folder(write_package(tmp_path / "demo", flow=flow))
    assert services.office_rehearsal.run(card["stage_id"])["state"] == "failed"
    _decide(services, card["approval_id"])

    assert services.offices.install_staged(card["stage_id"], approval_id=card["approval_id"])["office"]["id"] == "demo"


def test_a_rehearsal_of_another_fingerprint_does_not_count(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))
    services.office_rehearsal.run(card["stage_id"])
    record = services.offices._stage_folder(card["stage_id"]) / "rehearsal.json"
    record.write_text(record.read_text(encoding="utf-8").replace(card["fingerprint"], "0" * 64), encoding="utf-8")
    _decide(services, card["approval_id"])

    assert services.office_rehearsal.last(card["stage_id"]) is None
    refused = _reason(services.offices.install_staged, card["stage_id"], approval_id=card["approval_id"])
    assert refused.code == "rehearsal_required"


def test_a_read_only_package_installs_without_a_rehearsal(world):
    start, tmp_path = world
    services = start()
    flow = {**WRITE_FLOW, "steps": [{**WRITE_FLOW["steps"][0], "tools": [
        {"capability": "file.read", "arguments": {"path": "notes.md"}}]}]}
    card = services.offices.stage_folder(write_package(tmp_path / "demo", flow=flow))
    assert card["import_checks"]["writes_files"] is False and card["import_checks"]["rehearsal_required"] is False
    _decide(services, card["approval_id"])

    assert services.offices.install_staged(card["stage_id"], approval_id=card["approval_id"])["office"]["id"] == "demo"


# --- the route ---------------------------------------------------------------------------------------------------


def test_rehearse_route_shape_errors_and_actor(world):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))
    client, headers = _client(services)
    with client:
        path = f"/gui/api/offices/stage/{card['stage_id']}/rehearse"
        assert client.post(path, json={}).status_code == 401
        response = client.post(path, headers=headers, json={})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == "finished" and body["actor"] == "user:gui"
        assert set(body) >= {"stage_id", "fingerprint", "state", "at", "flows", "files", "notice", "folder_deleted"}
        assert client.post(path, headers=headers, json={"extra": 1}).status_code == 422
        assert client.post(path, headers=headers, json={"area": ""}).status_code == 422
        unknown = client.post(path, headers=headers, json={"area": "absent"})
        assert unknown.status_code == 422 and unknown.json()["detail"]["details"]["reason"] == \
            "office_rehearsal_unknown_flow"
        missing = client.post(f"/gui/api/offices/stage/{'0' * 32}/rehearse", headers=headers, json={})
        assert missing.status_code == 409
        refused = client.post(f"/gui/api/offices/stage/{card['stage_id']}/install", headers=headers,
                              json={"approval_id": card["approval_id"]})
        assert refused.status_code == 409 and refused.json()["detail"]["details"]["reason"] == \
            "office_install_not_approved"

# --- round 2: environment, teams, the approval double ----------------------------------------------------------------


def _role_flow(flow_id: str) -> dict:
    flow = copy.deepcopy(WRITE_FLOW)
    flow["id"] = flow_id
    flow["steps"][0]["role"] = "analyst"
    return flow


def test_two_flows_with_a_role_in_one_area_share_the_approved_team(world):
    start, tmp_path = world
    services = start()
    source = write_package(tmp_path / "demo", flow=_role_flow("first_note"))
    second = _role_flow("second_note")
    (source / "areas" / "reception" / "workflows" / "second_note.yaml").write_text(
        yaml.safe_dump(second, sort_keys=False), encoding="utf-8")
    card = services.offices.stage_folder(source)

    result = services.office_rehearsal.run(card["stage_id"])

    assert [(flow["workflow"], flow["status"]) for flow in result["flows"]] == [
        ("first_note", "completed"), ("second_note", "completed")], result["flows"]
    assert result["state"] == "finished"


def test_the_process_environment_is_never_read(world, monkeypatch):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("keep me", encoding="utf-8")
    for name, value in (("DB_PATH", sentinel / "other.db"), ("DATA_DIR", sentinel / "data"),
                        ("RUNS_DIR", sentinel / "runs"), ("ASSETS_DIR", sentinel / "assets"),
                        ("FILE_TOOLS_ROOT", sentinel), ("LOCAL_DIR", sentinel / "local"),
                        ("GUI_TOKEN_FILE", sentinel / "token"), ("EXECUTOR_WORKDIR_ROOT", sentinel / "exec")):
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("EXECUTION_BACKEND", "native")
    seen: list = []
    original = ApplicationServices.create

    def spy(settings, *args, **kwargs):
        seen.append(settings)
        return original(settings, *args, **kwargs)

    monkeypatch.setattr(ApplicationServices, "create", staticmethod(spy))
    before = sorted(path.relative_to(sentinel).as_posix() for path in sentinel.rglob("*"))

    result = services.office_rehearsal.run(card["stage_id"])

    assert result["state"] == "finished" and result["flows"][0]["status"] == "completed", result
    assert sorted(path.relative_to(sentinel).as_posix() for path in sentinel.rglob("*")) == before == ["keep.txt"]
    assert (sentinel / "keep.txt").read_text(encoding="utf-8") == "keep me"
    assert len(seen) == 1
    used = seen[0]
    base = (services.office_rehearsal.base).resolve()
    assert used.mock_mode is True
    for name in ("local_dir", "data_dir", "db_path", "runs_dir", "assets_dir", "file_tools_root", "gui_token_file",
                 "executor_workdir_root", "project_commands_file", "integration_marker"):
        assert getattr(used, name).is_relative_to(base), (name, getattr(used, name))


def _drive_with(monkeypatch, services, change):
    """Rehearse while ``change`` edits the pending tool approval the double looks at."""
    from multiagent.services import runs as runs_module

    original = runs_module.pending_tool_approval

    def edited(run):
        pending = original(run)
        return None if pending is None else change(pending)

    monkeypatch.setattr(runs_module, "pending_tool_approval", edited)


@pytest.mark.parametrize("change", [
    lambda pending: {**pending, "actions": []},
    lambda pending: {**pending, "actions_total": len(pending["actions"]) + 3}], ids=["no-actions", "truncated"])
def test_an_approval_with_no_actions_or_a_truncated_list_is_refused(world, monkeypatch, change):
    start, tmp_path = world
    services = start()
    card = services.offices.stage_folder(write_package(tmp_path / "demo"))
    _drive_with(monkeypatch, services, change)

    result = services.office_rehearsal.run(card["stage_id"])

    flow = result["flows"][0]
    assert result["state"] == "failed" and flow["status"] == "failed"
    assert flow["reason"] == "write_outside_rehearsal_folder"
    assert result["files"] == [] and _real_files(tmp_path) == {"sentinel.txt": "keep me"}


def _make_link(link: Path, target: Path) -> bool:
    """A symbolic link, or a junction on Windows when links are not allowed; False when neither can be created."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return True
        except (OSError, ImportError, AttributeError):
            return False
    return False


def test_a_link_inside_the_folder_that_leaves_it_is_refused(tmp_path):
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    if not _make_link(work / "door", outside):
        pytest.skip("neither a symbolic link nor a junction can be created here")
    assert _inside(work, "door/escaped.md") is False
    (work / "plain").mkdir()
    assert _inside(work, "plain/fine.md") is True
