# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only, hash-verified previews for pending file.write approvals."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.tools.contracts import ToolExecutionError
from multiagent.tools.files import preview_file_write
from multiagent.workflows import WorkflowCatalog

POST_INPUTS = {"topic": "Gatos que programan", "platform": "Instagram", "audience": "Equipos pequenos",
               "instructions": "Tono cercano"}


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        max_llm_calls=4, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        file_tools_root=root, gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, dry_run=True)

    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    workflow = yaml.safe_load((Path(settings.workflows_dir) / "social_post.yaml").read_text(encoding="utf-8"))
    create_step = next(step for step in workflow["steps"] if step["id"] == "create")
    create_step["tools"] = [{"capability": "file.write",
                             "arguments": {"path": "draft.md", "content": "${topic}"}}]
    (workflow_dir / "social_post.yaml").write_text(
        yaml.safe_dump(workflow, sort_keys=False, allow_unicode=True), encoding="utf-8")
    services.system.engine.workflows = WorkflowCatalog(settings, workflows_dir=workflow_dir)

    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, root=root, token=token)
    services.runs.close()


def _headers(env):
    return {"Authorization": f"Bearer {env.token}"}


def _waiting_run(env):
    response = env.client.post("/gui/api/runs", headers=_headers(env), json={
        "workflow_id": "social_post", "project_name": "Preview test", "inputs": dict(POST_INPUTS),
        "execution_mode": "auto",
    })
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert env.services.system.worker.process_once()
    run = env.services.system.store.get_run(run_id)
    assert run["status"] == "waiting_human" and run["waiting_reason"] == "tool_approval"
    return run_id


def test_file_write_preview_does_not_write_new_file(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()

    preview = preview_file_write(root, {"path": "new.md", "content": "proposed text"})

    assert preview["path"] == "new.md" and preview["content"] == "proposed text"
    assert preview["size"] == len(b"proposed text") and preview["exists"] is False
    assert "+proposed text" in preview["diff"] and preview["removed_lines"] == 0
    assert not (root / "new.md").exists()


def test_file_write_preview_diff_marks_removed_lines(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "existing.md"
    target.write_text("old line\nkeep line\n", encoding="utf-8")

    preview = preview_file_write(root, {"path": "existing.md", "content": "new line\nkeep line"})

    assert preview["exists"] is True
    assert preview["removed_lines"] == 1
    assert "-old line" in preview["diff"] and "+new line" in preview["diff"]
    assert target.read_text(encoding="utf-8") == "old line\nkeep line\n"


@pytest.mark.parametrize(("path", "content", "max_bytes", "code"), [
    ("../escape.md", "x", 10, "outside_root"),
    ("unsupported.py", "x", 10, "unsupported_format"),
    ("large.txt", "four", 3, "too_large"),
])
def test_file_write_preview_reuses_path_extension_and_size_errors(tmp_path: Path, path, content, max_bytes, code):
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(ToolExecutionError) as error:
        preview_file_write(root, {"path": path, "content": content}, max_bytes=max_bytes)

    assert error.value.code == code


def test_gui_preview_is_token_protected_read_only_and_resolves_run_inputs(env):
    run_id = _waiting_run(env)
    permission_request_id = env.services.runs.get(run_id)["tool_approval"]["permission_request_id"]
    path = f"/gui/api/runs/{run_id}/tool-approval/preview?permission_request_id={permission_request_id}"
    before = env.services.system.store.get_run(run_id)

    assert env.client.get(path).status_code == 401
    response = env.client.get(path, headers=_headers(env))

    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["available"] is True
    assert preview["path"] == "draft.md" and preview["content"] == POST_INPUTS["topic"]
    assert preview["exists"] is False and not (env.root / "draft.md").exists()
    assert env.services.system.store.get_run(run_id) == before


def test_gui_preview_refuses_a_mismatched_fingerprint(env, monkeypatch):
    from multiagent.services import runs as run_services

    run_id = _waiting_run(env)
    original = run_services.pending_tool_approval

    def mismatched(run):
        pending = original(run)
        return {**pending, "arguments_sha256": "f" * 64} if pending else None

    monkeypatch.setattr(run_services, "pending_tool_approval", mismatched)
    permission_request_id = env.services.runs.get(run_id)["tool_approval"]["permission_request_id"]
    response = env.client.get(
        f"/gui/api/runs/{run_id}/tool-approval/preview?permission_request_id={permission_request_id}",
        headers=_headers(env))

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["reason"] == "arguments_mismatch"
    assert "fingerprint" in response.json()["message"]


def test_gui_preview_refuses_a_stale_permission_request_id(env):
    run_id = _waiting_run(env)
    response = env.client.get(
        f"/gui/api/runs/{run_id}/tool-approval/preview?permission_request_id=stale-request",
        headers=_headers(env))

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["reason"] == "permission_request_mismatch"


def test_thread_and_execution_cards_mount_the_preview():
    js = Path(__file__).resolve().parents[1] / "multiagent" / "gui" / "static" / "js"
    cards = (js / "thread-cards.js").read_text(encoding="utf-8")
    actions = (js / "human-actions.js").read_text(encoding="utf-8")
    css = (js.parent / "new-session.css").read_text(encoding="utf-8")

    assert 'runPath(runId, "tool-approval/preview") + "?permission_request_id="' in cards
    assert "encodeURIComponent(permissionRequestId || \"\")" in cards
    assert '<${ToolApprovalPreview}' in cards and '<${ToolApprovalPreview}' in actions
    assert "permission_request_id: entry.decision.permission_request_id" in cards
    assert "permission_request_id: call.permission_request_id" in actions
    assert "preview.removed_lines > 0" in cards and 'role="alert"' in cards
    assert '"human.tool_preview":' in (js / "help-text.js").read_text(encoding="utf-8")
    assert "/* CHT-T04:" in css and ".tool-approval-preview" in css
    assert ".thread-readable-text { max-height: 20rem; overflow: auto;" in css
