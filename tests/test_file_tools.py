# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Tests for the built-in confined file.* tools and FILE_TOOLS_ROOT wiring."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import PipelineMode, RunStatus, SocialPostRequest
from multiagent.document_actions import ActionKind, Approval
from multiagent.document_trust import Provenance, SourceKind
from multiagent.services.runs import pending_tool_approval
from multiagent.tools import (EVENT_FAILED, ImpactClass, ToolCallContext, ToolErrorCode, ToolExecutionError,
                              ToolInvoker, ToolOrigin, ToolRegistry, ToolRequest, register_file_tools,
                              request_id_for)
from multiagent.tools.selection import select_tool
from multiagent.workflows import WorkflowCatalog


def _ctx() -> ToolCallContext:
    return ToolCallContext(scope="run_step", run_id="run-files", step_id="step-1", attempt=1)


def _req(tool_id: str, arguments: dict, ordinal: int = 0) -> ToolRequest:
    ctx = _ctx()
    return ToolRequest(
        request_id=request_id_for(ctx, ordinal),
        tool_id=tool_id,
        arguments=arguments,
        context=ctx,
        origin=ToolOrigin(kind="workflow_step", ref="step:step-1"),
    )


def _write_approval(*targets: str) -> list[Approval]:
    return [Approval(Provenance(SourceKind.USER, ref="user:1"), frozenset({ActionKind.WRITE}), frozenset(targets))]


def _make_invoker(root: Path, *, max_bytes: int | None = None):
    events: list[tuple[str, dict]] = []
    registry = ToolRegistry()
    kwargs = {"max_bytes": max_bytes} if max_bytes is not None else {}
    register_file_tools(registry, root, **kwargs)
    invoker = ToolInvoker(registry, event_sink=lambda kind, payload: events.append((kind, dict(payload))))
    return registry, invoker, events


def _settings(tmp_path: Path, *, file_tools_root: Path | str | None = None) -> Settings:
    return Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "multiagent.db",
        runs_dir=tmp_path / "data" / "runs",
        assets_dir=tmp_path / "data" / "assets",
        file_tools_root=file_tools_root,
        worker_enabled=False,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )


def _create_escape_link(link_path: Path, outside_target: Path, *, is_dir: bool) -> None:
    try:
        os.symlink(outside_target, link_path, target_is_directory=is_dir)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt" and is_dir:
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(outside_target)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and link_path.exists():
            return
    pytest.skip("Operating system did not permit creating a symlink or junction")


def test_file_tools_specs_capabilities_and_impacts(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    registry, _, _ = _make_invoker(root)
    specs = {spec.tool_id: spec for spec in registry.list(namespace="file")}
    assert set(specs) == {"file.list", "file.read", "file.write", "file.move"}
    for tool_id, spec in specs.items():
        assert spec.declared_by == "builtin"
        assert spec.capabilities == frozenset({tool_id})
        assert select_tool(registry, tool_id).spec.tool_id == tool_id

    assert specs["file.list"].impacts == frozenset({ImpactClass.READ})
    assert specs["file.list"].effects == ()
    assert specs["file.read"].impacts == frozenset({ImpactClass.READ})
    assert specs["file.read"].effects == ()

    assert specs["file.write"].impacts == frozenset({ImpactClass.WRITE})
    assert [(e.kind, e.target_from) for e in specs["file.write"].effects] == [(ActionKind.WRITE, "/path")]

    assert specs["file.move"].impacts == frozenset({ImpactClass.WRITE})
    assert [(e.kind, e.target_from) for e in specs["file.move"].effects] == [
        (ActionKind.WRITE, "/source"),
        (ActionKind.WRITE, "/destination"),
    ]


def test_file_list_root_subfolder_and_bounded_recursion(tmp_path: Path):
    root = tmp_path / "workspace"
    (root / "sub" / "nested").mkdir(parents=True)
    (root / "top.txt").write_text("hello", encoding="utf-8")
    (root / "sub" / "a.md").write_text("# A", encoding="utf-8")
    (root / "sub" / "nested" / "deep.json").write_text("{}", encoding="utf-8")
    _, invoker, _ = _make_invoker(root)

    root_res = invoker.invoke(_req("file.list", {}), grants={"tool:file.list"})
    assert root_res.ok
    assert root_res.output == {
        "path": "",
        "entries": [
            {"name": "sub", "type": "dir", "size": 0},
            {"name": "top.txt", "type": "file", "size": 5},
        ],
        "truncated": False,
    }

    sub_res = invoker.invoke(_req("file.list", {"path": "sub"}), grants={"tool:file.list"})
    assert sub_res.ok
    assert sub_res.output["path"] == "sub"
    assert [e["name"] for e in sub_res.output["entries"]] == ["a.md", "nested"]

    rec_res = invoker.invoke(
        _req("file.list", {"recursive": True, "max_depth": 2}),
        grants={"tool:file.list"},
    )
    assert rec_res.ok
    assert [e["name"] for e in rec_res.output["entries"]] == ["sub", "sub/a.md", "sub/nested", "top.txt"]
    assert rec_res.output["truncated"] is True

    capped_res = invoker.invoke(
        _req("file.list", {"recursive": True, "max_depth": 4, "max_entries": 3}),
        grants={"tool:file.list"},
    )
    assert capped_res.ok
    assert len(capped_res.output["entries"]) == 3
    assert capped_res.output["truncated"] is True

    missing_res = invoker.invoke(_req("file.list", {"path": "missing"}), grants={"tool:file.list"})
    assert not missing_res.ok
    assert missing_res.error.code == ToolErrorCode.EXECUTION_FAILED
    assert missing_res.error.details == {"code": "not_found"}


def test_file_read_formats_and_utf8(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.md").write_text("# Título\nCafé", encoding="utf-8", newline="\n")
    (root / "table.csv").write_text("a,b\n1,2\n", encoding="utf-8", newline="\n")
    (root / "data.json").write_text('{"ok": true}', encoding="utf-8")
    (root / "plain.txt").write_text("plain text", encoding="utf-8")
    (root / "bad_utf8.txt").write_bytes(b"\xff\xfe\x00\x00")

    _, invoker, events = _make_invoker(root)
    for name, expected in (
        ("note.md", "# Título\nCafé"),
        ("table.csv", "a,b\n1,2\n"),
        ("data.json", '{"ok": true}'),
        ("plain.txt", "plain text"),
    ):
        res = invoker.invoke(_req("file.read", {"path": name}), grants={"tool:file.read"})
        assert res.ok, name
        assert res.output["path"] == name
        assert res.output["content"] == expected

    bad_utf8 = invoker.invoke(_req("file.read", {"path": "bad_utf8.txt"}), grants={"tool:file.read"})
    assert not bad_utf8.ok
    assert bad_utf8.error.details == {"code": "unsupported_format"}

    missing = invoker.invoke(_req("file.read", {"path": "nope.txt"}), grants={"tool:file.read"})
    assert not missing.ok
    assert missing.error.details == {"code": "not_found"}
    failed_event = [payload for kind, payload in events if kind == EVENT_FAILED][-1]
    assert failed_event["details"] == {"code": "not_found"}


def test_file_write_creates_replaces_and_validates_json_before_disk(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    _, invoker, _ = _make_invoker(root)

    needs_approval = invoker.invoke(
        _req("file.write", {"path": "notes/hello.md", "content": "hi"}),
        grants={"tool:file.write"},
    )
    assert not needs_approval.ok
    assert needs_approval.error.code == ToolErrorCode.APPROVAL_REQUIRED
    assert not (root / "notes" / "hello.md").exists()

    created = invoker.invoke(
        _req("file.write", {"path": "notes/hello.md", "content": "hola"}),
        grants={"tool:file.write"},
        evidence=_write_approval("notes/hello.md"),
    )
    assert created.ok
    assert (root / "notes" / "hello.md").read_text(encoding="utf-8") == "hola"

    replaced = invoker.invoke(
        _req("file.write", {"path": "notes/hello.md", "content": "actualizado"}),
        grants={"tool:file.write"},
        evidence=_write_approval("notes/hello.md"),
    )
    assert replaced.ok
    assert (root / "notes" / "hello.md").read_text(encoding="utf-8") == "actualizado"

    bad_json = invoker.invoke(
        _req("file.write", {"path": "cfg/bad.json", "content": "{not json"}),
        grants={"tool:file.write"},
        evidence=_write_approval("cfg/bad.json"),
    )
    assert not bad_json.ok
    assert bad_json.error.code == ToolErrorCode.EXECUTION_FAILED
    assert bad_json.error.details == {"code": "invalid_json"}
    assert not (root / "cfg").exists()


def test_file_move_renames_and_never_overwrites_existing_destination(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a.txt").write_text("source", encoding="utf-8")
    (root / "existing.txt").write_text("keep", encoding="utf-8")
    _, invoker, _ = _make_invoker(root)

    no_overwrite = invoker.invoke(
        _req("file.move", {"source": "a.txt", "destination": "existing.txt"}),
        grants={"tool:file.move"},
        evidence=_write_approval("a.txt", "existing.txt"),
    )
    assert not no_overwrite.ok
    assert no_overwrite.error.details == {"code": "already_exists"}
    assert (root / "a.txt").read_text(encoding="utf-8") == "source"
    assert (root / "existing.txt").read_text(encoding="utf-8") == "keep"

    moved = invoker.invoke(
        _req("file.move", {"source": "a.txt", "destination": "archive/b.txt"}),
        grants={"tool:file.move"},
        evidence=_write_approval("a.txt", "archive/b.txt"),
    )
    assert moved.ok
    assert moved.output == {"source": "a.txt", "destination": "archive/b.txt"}
    assert not (root / "a.txt").exists()
    assert (root / "archive" / "b.txt").read_text(encoding="utf-8") == "source"


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd.txt",
        "\\Windows\\win.txt",
        "../secret.txt",
        "sub/../../secret.txt",
        "sub/../sub/ok.txt",
        "C:\\temp\\secret.txt",
        "C:secret.txt",
        "\\\\server\\share\\secret.txt",
        "//server/share/secret.txt",
    ],
)
def test_confinement_rejects_absolute_traversal_drive_and_unc_paths(tmp_path: Path, bad_path: str):
    root = tmp_path / "workspace"
    root.mkdir()
    _, invoker, _ = _make_invoker(root)

    read_res = invoker.invoke(_req("file.read", {"path": bad_path}), grants={"tool:file.read"})
    assert not read_res.ok and read_res.error.details == {"code": "outside_root"}

    list_res = invoker.invoke(_req("file.list", {"path": bad_path}), grants={"tool:file.list"})
    assert not list_res.ok and list_res.error.details == {"code": "outside_root"}

    write_res = invoker.invoke(
        _req("file.write", {"path": bad_path, "content": "x"}),
        grants={"tool:file.write"},
        evidence=_write_approval(bad_path),
    )
    assert not write_res.ok and write_res.error.details == {"code": "outside_root"}

    move_res = invoker.invoke(
        _req("file.move", {"source": bad_path, "destination": "safe.txt"}),
        grants={"tool:file.move"},
        evidence=_write_approval(bad_path, "safe.txt"),
    )
    assert not move_res.ok and move_res.error.details == {"code": "outside_root"}


def test_confinement_rejects_symlink_or_junction_pointing_outside_root(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP SECRET", encoding="utf-8")

    root = tmp_path / "workspace"
    root.mkdir()
    escape_dir = root / "escape_dir"
    _create_escape_link(escape_dir, outside, is_dir=True)

    _, invoker, _ = _make_invoker(root)

    assert invoker.invoke(_req("file.list", {"path": "escape_dir"}), grants={"tool:file.list"}).error.details == {
        "code": "outside_root"
    }
    assert invoker.invoke(_req("file.read", {"path": "escape_dir/secret.txt"}), grants={"tool:file.read"}).error.details == {
        "code": "outside_root"
    }
    write_escape = invoker.invoke(
        _req("file.write", {"path": "escape_dir/new.txt", "content": "x"}),
        grants={"tool:file.write"},
        evidence=_write_approval("escape_dir/new.txt"),
    )
    assert not write_escape.ok and write_escape.error.details == {"code": "outside_root"}
    assert not (outside / "new.txt").exists()

    # Recursive listing on root must not descend into an escaping link/junction.
    listed = invoker.invoke(_req("file.list", {"recursive": True}), grants={"tool:file.list"})
    assert listed.ok
    assert all("secret.txt" not in entry["name"] for entry in listed.output["entries"])


@pytest.mark.parametrize("bad_name", ["script.py", "sheet.xlsx", "doc.pdf", "noext"])
def test_unsupported_formats_fail_before_touching_disk(tmp_path: Path, bad_name: str):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "ok.txt").write_text("ok", encoding="utf-8")
    _, invoker, _ = _make_invoker(root)

    assert invoker.invoke(_req("file.read", {"path": bad_name}), grants={"tool:file.read"}).error.details == {
        "code": "unsupported_format"
    }
    write_res = invoker.invoke(
        _req("file.write", {"path": f"sub/{bad_name}", "content": "x"}),
        grants={"tool:file.write"},
        evidence=_write_approval(f"sub/{bad_name}"),
    )
    assert not write_res.ok and write_res.error.details == {"code": "unsupported_format"}
    assert not (root / "sub").exists()

    move_res = invoker.invoke(
        _req("file.move", {"source": "ok.txt", "destination": bad_name}),
        grants={"tool:file.move"},
        evidence=_write_approval("ok.txt", bad_name),
    )
    assert not move_res.ok and move_res.error.details == {"code": "unsupported_format"}
    assert (root / "ok.txt").exists()


def test_size_limit_matches_max_output_bytes_for_read_and_write(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    _, invoker, _ = _make_invoker(root, max_bytes=120)

    over_write = invoker.invoke(
        _req("file.write", {"path": "big.txt", "content": "a" * 121}),
        grants={"tool:file.write"},
        evidence=_write_approval("big.txt"),
    )
    assert not over_write.ok and over_write.error.details == {"code": "too_large"}
    assert not (root / "big.txt").exists()

    ok_write = invoker.invoke(
        _req("file.write", {"path": "fit.txt", "content": "a" * 60}),
        grants={"tool:file.write"},
        evidence=_write_approval("fit.txt"),
    )
    assert ok_write.ok

    (root / "too_big.txt").write_text("b" * 121, encoding="utf-8")
    over_read = invoker.invoke(_req("file.read", {"path": "too_big.txt"}), grants={"tool:file.read"})
    assert not over_read.ok and over_read.error.details == {"code": "too_large"}

    ok_read = invoker.invoke(_req("file.read", {"path": "fit.txt"}), grants={"tool:file.read"})
    assert ok_read.ok and ok_read.output["content"] == "a" * 60


def test_unset_file_tools_root_registers_no_file_tools_and_step_fails_unknown_tool(tmp_path: Path):
    empty_env = Settings(_env_file=None, local_dir=tmp_path / "local2", file_tools_root="", worker_enabled=False)
    assert empty_env.file_tools_root is None

    settings = _settings(tmp_path, file_tools_root=None)
    system = build_system(settings, dry_run=True)
    assert system.tool_registry.list(namespace="file") == ()

    folder = tmp_path / "workflows"
    folder.mkdir()
    doc = yaml.safe_load((Path(settings.workflows_dir) / "social_post_full.yaml").read_text(encoding="utf-8"))
    step = next(step for step in doc["steps"] if step["id"] == "strategy")
    step["tools"] = [{"tool": "file.read", "arguments": {"path": "brief.md"}}]
    (folder / "social_post_full.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    system.engine.workflows = WorkflowCatalog(settings, workflows_dir=folder)

    run = system.run_service.create_social_post(
        SocialPostRequest(
            project_name="no-file-root",
            objective="Create an educational post",
            topic="KNX",
            pipeline_mode=PipelineMode.FULL,
        )
    )
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.FAILED.value
    assert "unknown_tool" in state["error"]


def test_workflow_run_mock_waits_for_approval_on_file_write_and_writes_on_approve(tmp_path: Path):
    workspace = tmp_path / "declared_folder"
    workspace.mkdir()
    (workspace / "brief.md").write_text("Brief content", encoding="utf-8")
    unnormalized = workspace / "." / ".." / "declared_folder"

    settings = _settings(tmp_path, file_tools_root=unnormalized)
    assert settings.file_tools_root == workspace.resolve()
    system = build_system(settings, dry_run=True)

    folder = tmp_path / "workflows"
    folder.mkdir()
    doc = yaml.safe_load((Path(settings.workflows_dir) / "social_post_full.yaml").read_text(encoding="utf-8"))
    for step in doc["steps"]:
        if step["id"] == "strategy":
            step["tools"] = [{"tool": "file.read", "arguments": {"path": "brief.md"}}]
        elif step["id"] == "create":
            step["tools"] = [{"capability": "file.write", "arguments": {"path": "out/post.md", "content": "${topic}"}}]
    (folder / "social_post_full.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    system.engine.workflows = WorkflowCatalog(settings, workflows_dir=folder)

    run = system.run_service.create_social_post(
        SocialPostRequest(
            project_name="file-tools-e2e",
            objective="Create an educational post",
            topic="KNX",
            pipeline_mode=PipelineMode.FULL,
        )
    )
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert state["waiting_reason"] == "tool_approval"
    pending = pending_tool_approval(state)
    assert pending["tool_id"] == "file.write"
    assert not (workspace / "out" / "post.md").exists()

    approved = system.run_service.approve(
        state["id"],
        permission_request_id=pending["permission_request_id"],
        person=True,
    )
    assert approved["status"] == RunStatus.QUEUED.value
    system.worker.process_once()

    state = system.run_service.get(state["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert state["waiting_reason"] == "review"
    assert (workspace / "out" / "post.md").read_text(encoding="utf-8") == "KNX"
    assert system.run_service.approve(state["id"])["status"] == RunStatus.COMPLETED.value


def test_invoker_records_tool_execution_error_code_in_failed_event_details():
    registry = ToolRegistry()
    register_file_tools(registry, Path(".").resolve())
    # Custom tool raising ToolExecutionError directly
    spec = registry.lookup("file.read").spec.model_copy(update={"tool_id": "file.custom_err", "capabilities": frozenset({"file.custom_err"})})
    registry.register(spec, lambda *a, **k: (_ for _ in ()).throw(ToolExecutionError("too_large", "secret path")))
    events: list[tuple[str, dict]] = []
    invoker = ToolInvoker(registry, event_sink=lambda kind, payload: events.append((kind, dict(payload))))
    result = invoker.invoke(_req("file.custom_err", {"path": "x.txt"}), grants={"tool:file.custom_err"})
    assert not result.ok
    assert result.error.code == ToolErrorCode.EXECUTION_FAILED
    assert result.error.details == {"code": "too_large"}
    assert "secret path" not in result.error.message
    failed = [payload for kind, payload in events if kind == EVENT_FAILED]
    assert len(failed) == 1 and failed[0]["details"] == {"code": "too_large"}
