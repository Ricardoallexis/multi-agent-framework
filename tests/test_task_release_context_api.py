from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices
from multiagent.tasks.envelopes import canonical_json


def make_services(root: Path) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings)


def seed_release_graph(client: TestClient) -> None:
    created = client.post("/api/v1/task-groups", json={
        "id": "CTX-G", "title": "Context fixture", "actor": "agent:fixture", "authorized": True,
    })
    assert created.status_code == 201, created.text
    for task_id in ("CTX-PREREQ", "CTX-NATIVE", "CTX-LEGACY"):
        response = client.post("/api/v1/tasks", json={
            "group_id": "CTX-G", "id": task_id, "title": task_id,
            "actor": "agent:fixture", "file_scope": [],
        })
        assert response.status_code == 201, response.text
    native = client.post("/api/v1/tasks/CTX-NATIVE/dependencies", json={
        "depends_on_id": "CTX-PREREQ", "requires": "accepted", "actor": "agent:fixture",
    })
    assert native.status_code == 200, native.text
    legacy = client.post("/api/v1/tasks/CTX-LEGACY/release-dependencies", json={
        "depends_on_id": "CTX-PREREQ", "source_ref": "doc://fixtures/release-edge.md",
        "actor": "agent:fixture",
    })
    assert legacy.status_code == 200, legacy.text


def test_context_v2_preserves_v1_native_envelope_and_fails_closed_for_legacy(monkeypatch, capsys, tmp_path):
    services = make_services(tmp_path)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        seed_release_graph(client)
        native_v1 = client.get("/api/v1/tasks/CTX-NATIVE/envelope")
        assert native_v1.status_code == 200, native_v1.text
        native_v2 = client.get("/api/v2/tasks/CTX-NATIVE/context")
        assert native_v2.status_code == 200, native_v2.text
        assert native_v2.json()["task_envelope"] == native_v1.json()
        assert native_v2.json()["capabilities"] == ["release_dependencies/v1"]

        legacy_v1 = client.get("/api/v1/tasks/CTX-LEGACY/envelope")
        assert legacy_v1.status_code == 409
        detail = legacy_v1.json()["detail"]
        assert detail["code"] == "invalid_state"
        assert detail["details"]["required_capability"] == "release_dependencies/v1"

        response = client.get("/api/v2/tasks/CTX-LEGACY/context")
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"capabilities", "task_envelope", "release_context", "fingerprint"}
        assert body["capabilities"] == ["release_dependencies/v1"]
        edges = body["release_context"]["dependencies"]
        assert {edge["kind"] for edge in edges} == {"native", "legacy_release"}
        assert all(edge["task_id"] == "CTX-PREREQ" for edge in edges)
        fingerprint_data = {"task_envelope": dict(body["task_envelope"]),
                            "release_context": body["release_context"]}
        fingerprint_data["task_envelope"].pop("events_after")
        expected = hashlib.sha256(canonical_json(fingerprint_data)).hexdigest()
        assert body["fingerprint"] == expected

        cached = client.get("/api/v2/tasks/CTX-LEGACY/context", params={
            "known_fingerprint": body["fingerprint"], "after": 0,
        })
        assert cached.status_code == 200, cached.text
        assert cached.json() == {"unchanged": True, "fingerprint": body["fingerprint"]}

        head = client.get("/api/v1/team-events").json()[-1]["seq"]
        same_context = client.get("/api/v2/tasks/CTX-LEGACY/context", params={"after": head})
        assert same_context.status_code == 200, same_context.text
        assert same_context.json()["fingerprint"] == body["fingerprint"]
        before_events = client.get("/api/v1/team-events").json()
        future = client.get("/api/v2/tasks/CTX-LEGACY/context", params={"after": head + 1})
        assert future.status_code == 422
        assert client.get("/api/v1/team-events").json() == before_events

        # Evidence changes the context fingerprint while the unmet tested threshold stays blocked.
        delivery = client.post("/api/v1/tasks/CTX-PREREQ/release-facts/delivery", json={
            "delivery_ref": "report://delivery-ctx", "delivery_sha256": "a" * 64,
            "has_patch": True, "patchset_sha256": "b" * 64, "actor": "agent:fixture",
        })
        assert delivery.status_code == 200, delivery.text
        after_delivery = client.get("/api/v2/tasks/CTX-LEGACY/context").json()
        failed = client.post("/api/v1/tasks/CTX-PREREQ/release-facts/test", json={
            "test_verdict": "failed", "test_patchset_sha256": "b" * 64,
            "test_ref": "report://tests-ctx", "actor": "agent:fixture",
        })
        assert failed.status_code == 200, failed.text
        after_test = client.get("/api/v2/tasks/CTX-LEGACY/context").json()
        assert after_delivery["release_context"]["readiness"] == after_test["release_context"]["readiness"]
        assert after_delivery["fingerprint"] != after_test["fingerprint"]

        # The CLI is a thin HTTP consumer and exposes the v2 fingerprint cursor explicitly.
        class ClientContext:
            def __enter__(self):
                return client

            def __exit__(self, *_):
                return None

        monkeypatch.setattr(cli, "api_client", lambda: ClientContext())
        try:
            cli.main(["task", "context-v2", "CTX-LEGACY", "--known-fingerprint", after_test["fingerprint"],
                      "--after", str(head)])
        except SystemExit as exc:
            assert exc.code == 0
        assert json.loads(capsys.readouterr().out) == {
            "unchanged": True, "fingerprint": after_test["fingerprint"],
        }


def test_invalid_release_fact_is_atomic(tmp_path):
    services = make_services(tmp_path)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        seed_release_graph(client)
        before_facts = client.get("/api/v1/tasks/CTX-PREREQ/release-facts").json()
        before_events = client.get("/api/v1/team-events").json()
        invalid = client.post("/api/v1/tasks/CTX-PREREQ/release-facts/delivery", json={
            "delivery_ref": "report://bad-delivery", "delivery_sha256": "not-a-hash",
            "has_patch": True, "patchset_sha256": "b" * 64, "actor": "agent:fixture",
        })
        assert invalid.status_code == 422
        assert client.get("/api/v1/tasks/CTX-PREREQ/release-facts").json() == before_facts
        assert client.get("/api/v1/team-events").json() == before_events
