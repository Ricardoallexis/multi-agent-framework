from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui.token import ensure_token
from multiagent.services.work_map import build_work_map
from multiagent.services.errors import ServiceError
from test_projects_service import make_services


class ProjectService:
    def __init__(self):
        self.store = SimpleNamespace(session_links=lambda task_id: (
            [{"session_id": "claude:root"}] if task_id == "demo-T01" else []))

    def get(self, project_id):
        return {
            "id": project_id, "name": "demo", "title": "Demo", "archived": False,
            "description": "PROJECT PROMPT SECRET",
            "stages": [{
                "group_id": "demo-STG", "title": "Stage", "tasks": [{
                    "id": "demo-T01", "title": "Safe title", "objective": "TASK PROMPT SECRET"
                }]
            }],
            "attention": {
                "reviews": [{"id": "demo-T01", "title": "Review", "objective": "ATTENTION PROMPT"}],
                "verifications": [],
                "mine": [],
                "blocked": [{
                    "id": "demo-T02", "title": "Blocked", "objective": "BLOCK PROMPT",
                    "blockers": [{
                        "task_id": "demo-T01", "state": "running", "requires": "accepted",
                        "secret": "BLOCKER SECRET"
                    }]
                }]
            }
        }

    def list(self, archived=False):
        return []


class SessionService:
    def list_sessions(self, limit=500, after=None):
        return {"sessions": [
            {"id": "claude:root", "source": "claude", "status": "active", "models": ["model-a"],
             "tokens": None, "created_at": "start", "updated_at": "recent", "parent_id": None},
            {"id": "claude:kid", "source": "claude", "status": "idle", "models": ["model-b"],
             "tokens": None, "created_at": "kid-start", "updated_at": "kid-recent", "parent_id": "claude:root"},
            {"id": "codex:untasked", "source": "codex", "status": "active", "models": [],
             "tokens": None, "created_at": "other-start", "updated_at": "other-recent", "parent_id": None},
        ], "next_cursor": None, "warnings": ["summary_warning"]}

    def list_children(self, session_id, status=None):
        if session_id == "claude:root":
            return {"children": [
                {"id": "child-no-session", "source": "claude", "kind": "subagent",
                 "name": "Explore", "model": "model-c", "status": "running", "tokens": None,
                 "started_at": "child-start", "last_activity_at": "child-now",
                 "label": "PROMPT SECRET", "private_extra": "MUST NOT LEAK"},
            ], "warnings": ["reader_warning"]}
        return {"children": [], "warnings": []}


def fake_services():
    return SimpleNamespace(projects=ProjectService(), sessions=SessionService(),
                           runs=SimpleNamespace(_runtimes=lambda: []))


def test_map_includes_unlinked_sessions_parent_edges_and_safe_allowlists():
    result = build_work_map(fake_services(), project_id="demo")
    assert result["projects"][0]["id"] == "demo"
    assert result["links"] == [{"task_id": "demo-T01", "session_id": "claude:root"}]
    assert result["sessions"]["claude:root"]["children"] == [
        {"id": "child-no-session", "source": "claude", "kind": "subagent",
         "name": "Explore", "model": "model-c", "status": "running", "tokens": None,
         "started_at": "child-start", "last_activity_at": "child-now"}
    ]
    assert result["sessions"]["claude:kid"]["parent_id"] == "claude:root"
    assert "codex:untasked" in result["sessions"]  # unlinked session: the “Sesiones sin tarea” zone
    assert result["sessions"]["claude:root"]["model"] == ["model-a"]
    assert result["sessions"]["claude:root"]["tokens"] is None
    assert result["warnings"] == ["reader_warning", "summary_warning"]
    for private_text in ("PROMPT SECRET", "MUST NOT LEAK", "TASK PROMPT SECRET", "PROJECT PROMPT SECRET"):
        assert private_text not in str(result)
    assert result["version"] == build_work_map(fake_services(), project_id="demo")["version"]
    changed = fake_services()
    changed.sessions.list_sessions = lambda **kwargs: {
        "sessions": [{"id": "claude:root", "source": "claude", "status": "active",
                      "models": ["different"], "tokens": None, "created_at": "start",
                      "updated_at": "recent", "parent_id": None}],
        "next_cursor": None, "warnings": ["summary_warning"]}
    assert result["version"] != build_work_map(changed, project_id="demo")["version"]


def test_map_only_accepts_supported_filter():
    with pytest.raises(ServiceError):
        build_work_map(fake_services(), only="bogus")


@pytest.fixture
def gui(tmp_path):
    services = make_services(tmp_path / "local")
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield client, {"Authorization": f"Bearer {token}"}
    services.runs.close()


def test_map_route_is_token_protected_and_returns_stable_shape(gui):
    client, auth = gui
    path = "/gui/api/map"
    assert client.get(path).status_code == 401
    answer = client.get(path, headers=auth).json()
    assert answer["projects"] == [] and answer["sessions"] == {}
    assert answer["links"] == [] and answer["warnings"]
    assert isinstance(answer["version"], str) and len(answer["version"]) == 16
    assert client.get(path, params={"only": "invalid"}, headers=auth).status_code == 422
