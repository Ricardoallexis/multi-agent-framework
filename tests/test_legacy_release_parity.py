from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices


def make_services(root: Path) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings)


def post_ok(client: TestClient, path: str, body: dict[str, Any]):
    response = client.post(path, json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()


def add_scenario(client: TestClient, number: int, mode: str) -> tuple[str, str]:
    group_id = f"REL-G{number}"
    prerequisite_id = f"REL-P{number}"
    dependent_id = f"REL-D{number}"
    group = client.post("/api/v1/task-groups", json={
        "id": group_id, "title": "Synthetic release group", "actor": "agent:fixture", "authorized": True,
    })
    assert group.status_code == 201, group.text
    for task_id in (prerequisite_id, dependent_id):
        task = client.post("/api/v1/tasks", json={
            "group_id": group_id, "id": task_id, "title": "Synthetic release task", "actor": "agent:fixture",
        })
        assert task.status_code == 201, task.text
    mode_response = client.put(f"/api/v1/task-groups/{group_id}/release-mode", json={
        "mode": mode, "actor": "agent:fixture",
    })
    assert mode_response.status_code == 200, mode_response.text
    edge = post_ok(client, f"/api/v1/tasks/{dependent_id}/release-dependencies", {
        "depends_on_id": prerequisite_id, "source_ref": f"doc://fixtures/release-{number}.md",
        "actor": "agent:fixture",
    })
    assert edge["kind"] == "legacy_release"
    return prerequisite_id, dependent_id


def record_delivery(client: TestClient, task_id: str, *, has_patch: bool, patchset: str = "b" * 64):
    return post_ok(client, f"/api/v1/tasks/{task_id}/release-facts/delivery", {
        "delivery_ref": f"report://delivery-{task_id}", "delivery_sha256": "a" * 64,
        "has_patch": has_patch, "patchset_sha256": patchset if has_patch else "",
        "actor": "agent:fixture",
    })


def record_test(client: TestClient, task_id: str, *, verdict: str, patchset: str):
    return post_ok(client, f"/api/v1/tasks/{task_id}/release-facts/test", {
        "test_verdict": verdict, "test_patchset_sha256": patchset,
        "test_ref": f"report://test-{task_id}-{verdict}", "actor": "agent:fixture",
    })


def record_integration(client: TestClient, task_id: str):
    return post_ok(client, f"/api/v1/tasks/{task_id}/release-facts/integration", {
        "integration_commit": "c" * 40, "integration_ref": f"doc://fixtures/integration-{task_id}.md",
        "actor": "agent:fixture",
    })


def is_ready(client: TestClient, task_id: str) -> bool:
    response = client.get("/api/v1/tasks/ready")
    assert response.status_code == 200, response.text
    return task_id in {task["id"] for task in response.json()}


@pytest.mark.parametrize(
    "mode,delivery,verdict,verdict_patchset,integrated,expected",
    [
        (mode, "none", None, None, False, False)
        for mode in ("terminada", "probada", "integrada")
    ] + [
        (mode, "no_patch", None, None, False, True)
        for mode in ("terminada", "probada", "integrada")
    ] + [
        ("terminada", "patch", None, None, False, True),
        ("terminada", "patch", "failed", "current", False, True),
        ("probada", "patch", None, None, False, False),
        ("probada", "patch", "failed", "current", False, False),
        ("probada", "patch", "passed", "current", False, True),
        ("probada", "patch", "passed", "stale", False, False),
        ("integrada", "patch", "passed", "current", False, False),
    ] + [
        (mode, "patch", None, None, True, True)
        for mode in ("terminada", "probada", "integrada")
    ],
)
def test_legacy_release_readiness_matrix_uses_current_evidence(
        mode, delivery, verdict, verdict_patchset, integrated, expected, tmp_path):
    services = make_services(tmp_path)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        scenario = len(client.get("/api/v1/task-groups").json()) + 1
        prerequisite_id, dependent_id = add_scenario(client, scenario, mode)
        if delivery != "none":
            record_delivery(client, prerequisite_id, has_patch=(delivery == "patch"))
        if verdict is not None:
            patchset = "b" * 64 if verdict_patchset == "current" else "d" * 64
            if verdict_patchset == "stale":
                before_facts = client.get(f"/api/v1/tasks/{prerequisite_id}/release-facts").json()
                before_events = client.get("/api/v1/team-events").json()
                stale = client.post(f"/api/v1/tasks/{prerequisite_id}/release-facts/test", json={
                    "test_verdict": verdict, "test_patchset_sha256": patchset,
                    "test_ref": f"report://test-{prerequisite_id}-stale", "actor": "agent:fixture",
                })
                assert stale.status_code == 409, stale.text
                assert client.get(f"/api/v1/tasks/{prerequisite_id}/release-facts").json() == before_facts
                assert client.get("/api/v1/team-events").json() == before_events
            else:
                record_test(client, prerequisite_id, verdict=verdict, patchset=patchset)
        if integrated:
            if delivery == "none":
                record_delivery(client, prerequisite_id, has_patch=True)
            record_integration(client, prerequisite_id)
        assert is_ready(client, dependent_id) is expected


def test_new_delivery_invalidates_prior_test_and_integration_facts(tmp_path):
    services = make_services(tmp_path)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        prerequisite_id, dependent_id = add_scenario(client, 1, "probada")
        record_delivery(client, prerequisite_id, has_patch=True, patchset="b" * 64)
        record_test(client, prerequisite_id, verdict="passed", patchset="b" * 64)
        record_integration(client, prerequisite_id)
        assert is_ready(client, dependent_id)

        record_delivery(client, prerequisite_id, has_patch=True, patchset="d" * 64)
        facts = client.get(f"/api/v1/tasks/{prerequisite_id}/release-facts").json()
        assert facts["test_verdict"] is None
        assert facts["test_ref"] == ""
        assert facts["integration_commit"] == ""
        assert facts["integration_ref"] == ""
        assert not is_ready(client, dependent_id)


def test_accepted_and_gate_pass_do_not_satisfy_a_legacy_dependency(tmp_path):
    services = make_services(tmp_path)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        prerequisite_id, dependent_id = add_scenario(client, 1, "probada")
        gate = client.put(f"/api/v1/tasks/{prerequisite_id}/gate", json={
            "checks": ["review"], "actor": "agent:fixture",
        })
        assert gate.status_code == 200, gate.text
        for action in ("claim", "start", "submit"):
            response = client.post(f"/api/v1/tasks/{prerequisite_id}/transitions", json={
                "action": action, "actor": "agent:fixture",
            })
            assert response.status_code == 200, response.text
        review = client.post(f"/api/v1/tasks/{prerequisite_id}/reviews", json={
            "actor": "agent:reviewer", "approved": True,
        })
        assert review.status_code == 200, review.text
        gate_run = client.post(f"/api/v1/tasks/{prerequisite_id}/gate/run", json={"actor": "agent:fixture"})
        assert gate_run.status_code == 200, gate_run.text
        assert gate_run.json()["outcome"] == "passed"
        assert gate_run.json()["task"]["status"] == "accepted"
        assert not is_ready(client, dependent_id)


def test_task_create_rejects_invalid_legacy_edge_without_partial_native_edge_or_events(tmp_path):
    services = make_services(tmp_path)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        group = client.post("/api/v1/task-groups", json={
            "id": "ATOMIC-G", "title": "Atomic create", "actor": "agent:fixture", "authorized": True,
        })
        assert group.status_code == 201, group.text
        prerequisite = client.post("/api/v1/tasks", json={
            "group_id": "ATOMIC-G", "id": "ATOMIC-P", "title": "Prerequisite", "actor": "agent:fixture",
        })
        assert prerequisite.status_code == 201, prerequisite.text
        before_events = client.get("/api/v1/team-events").json()
        response = client.post("/api/v1/tasks", json={
            "group_id": "ATOMIC-G", "id": "ATOMIC-D", "title": "Atomic dependent", "actor": "agent:fixture",
            "depends_on": ["ATOMIC-P"],
            "release_dependencies": [{"depends_on_id": "MISSING", "source_ref": "doc://fixtures/missing.md"}],
        })
        assert response.status_code == 404, response.text
        missing_task = client.get("/api/v1/tasks/ATOMIC-D")
        assert missing_task.status_code == 404
        assert client.get("/api/v1/team-events").json() == before_events
