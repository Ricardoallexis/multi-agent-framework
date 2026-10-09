# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ORQ-3 end to end and parity: facade, HTTP and CLI give the same envelopes, results and errors (ORQ-T27)."""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError
from multiagent.tasks.envelopes import envelope_sha256

PATCH = b"diff --git a/src/a.py b/src/a.py\n+print('a')\n"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_envelopes.py"


def make_services(local: Path) -> ApplicationServices:
    local.mkdir(parents=True, exist_ok=True)
    settings = Settings(_env_file=None, local_dir=local, worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True)


class _ClientContext:
    """The shared TestClient as the CLI sees it (per-request context, no timeout argument)."""

    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self):
        client = self.client

        class _Client:
            def request(self, method, url, **kwargs):
                kwargs.pop("timeout", None)
                return client.request(method, url, **kwargs)

        return _Client()

    def __exit__(self, *exc_info) -> bool:
        return False


@pytest.fixture
def world(tmp_path: Path, monkeypatch):
    services = make_services(tmp_path / "local")
    services.tasks.create_group("G", "Group", actor="user", authorized=True)
    services.tasks.create_task("G", "A", "Task A", actor="user", objective="Build A", file_scope=["src/a.py"])
    services.tasks.create_task("G", "B", "Task B", actor="user", depends_on=["A"], file_scope=["src/b.py"])
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as http:
        monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(http))

        def run(*argv: str):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main(list(argv))
            text = out.getvalue()
            try:
                return json.loads(text)
            except ValueError:
                return text

        yield services, http, run, tmp_path


def cli_error(run, *argv: str) -> str:
    err = io.StringIO()
    with contextlib.redirect_stderr(err), pytest.raises(SystemExit):
        run(*argv)
    return err.getvalue()


def start(services, task_id: str = "A", agent: str = "agent:a") -> None:
    for action in ("claim", "start"):
        services.tasks.transition(task_id, action, actor=agent)


# -------- end to end over HTTP --------
def test_a_dependent_gets_the_result_by_reference_over_http(world):
    services, http, _, _ = world
    assert http.put("/api/v1/tasks/B/context", json={"actor": "user", "constraints": ["no push"],
                                                     "context_refs": ["doc://README.md"]}).status_code == 200
    start(services)
    blob = http.post("/api/v1/blobs", params={"actor": "agent:a", "media_type": "text/x-patch"}, content=PATCH).json()
    result = {"summary": "Built A.", "patch": {"blob": f"sha256:{blob['sha256']}", "files": ["src/a.py"]}}
    answer = http.post("/api/v1/tasks/A/results", json={"actor": "agent:a", "result": result})
    assert answer.status_code == 200 and answer.json()["task"]["state"] == "submitted"
    replay = http.post("/api/v1/tasks/A/results", json={"actor": "agent:a", "result": result}).json()
    assert replay["duplicate"] is True

    envelope = http.get("/api/v1/tasks/B/envelope").json()
    assert envelope["depends_on"][0]["result_ref"] == "result://A/1"
    assert envelope["artifact_refs"] == [blob["ref"]] and envelope["constraints"] == ["no push"]
    assert PATCH.decode() not in json.dumps(envelope)
    same = http.get("/api/v1/tasks/B/envelope", params={"known_hash": envelope_sha256(envelope)}).json()
    assert same["unchanged"] is True

    full = http.get("/api/v1/results", params={"ref": "result://A/1"}).json()
    assert full["summary"] == "Built A." and full["patch"]["blob"] == f"sha256:{blob['sha256']}"
    first = http.get("/api/v1/artifacts", params={"ref": blob["ref"], "length": 10}).json()
    rest = http.get("/api/v1/artifacts", params={"ref": blob["ref"], "offset": first["length"]}).json()
    assert (first["content"] + rest["content"]).encode() == PATCH and rest["eof"] is True


def test_a_consumer_resumes_from_its_cursor_over_http(world):
    services, http, _, _ = world
    head = http.get("/api/v1/cursors/agent:a").json()["head"]
    assert http.post("/api/v1/cursors/agent:a/ack", json={"seq": head}).json()["seq"] == head
    start(services)
    delta = http.get("/api/v1/cursors/agent:a/events", params={"mine": "true"}).json()
    assert [e["type"] for e in delta["events"]] == ["task.claimed", "task.started"]
    assert delta["cursor"] == head
    again = http.get("/api/v1/cursors/agent:a/events", params={"type": ["task.started"]}).json()
    assert [e["type"] for e in again["events"]] == ["task.started"]   # reading did not move the cursor
    http.post("/api/v1/cursors/agent:a/ack", json={"seq": delta["events"][-1]["seq"]})
    assert http.get("/api/v1/cursors/agent:a/events").json()["events"] == []


def test_blob_upload_over_the_limit_is_refused_with_413(world):
    services, http, _, _ = world
    services.envelopes.blob_max_bytes = 4
    response = http.post("/api/v1/blobs", params={"actor": "agent:a"}, content=b"12345")
    assert response.status_code == 413 and response.json()["detail"]["code"] == "invalid_request"


# -------- the CLI --------
def test_the_cli_drives_the_same_flow(world):
    services, _, run, tmp_path = world
    start(services)
    patch_file = tmp_path / "a.patch"
    patch_file.write_bytes(PATCH)
    blob = run("blob", "put", str(patch_file), "--actor", "agent:a", "--media-type", "text/x-patch")
    result_file = tmp_path / "result.json"
    result_file.write_text(json.dumps({"summary": "Built A.",
                                       "patch": {"blob": f"sha256:{blob['sha256']}", "files": ["src/a.py"]}}))
    submitted = run("task", "result", "submit", "A", "--actor", "agent:a", "--file", str(result_file))
    assert submitted["task"]["state"] == "submitted"
    assert run("task", "result", "show", "A")["ref"] == "result://A/1"
    assert run("task", "result", "show", "result://A/1")["summary"] == "Built A."
    assert run("task", "result", "show", "A", "--attempt", "1")["ref"] == "result://A/1"
    assert [item["ref"] for item in run("task", "result", "list", "A")] == ["result://A/1"]

    run("task", "context", "B", "--actor", "user", "--context-ref", "doc://README.md", "--constraint", "no push")
    envelope = run("task", "envelope", "B")
    assert envelope == services.envelopes.get_envelope("B")
    assert run("task", "envelope", "B", "--known-hash", envelope_sha256(envelope))["unchanged"] is True
    run("task", "context", "B", "--actor", "user", "--clear-constraints")
    assert services.envelopes.get_envelope("B")["constraints"] == []

    output = tmp_path / "copy.patch"
    message = run("artifact", "get", blob["ref"], "--output", str(output))
    assert output.read_bytes() == PATCH and "written" in message

    cursor = run("cursor", "show", "agent:x")
    assert cursor["seq"] == 0
    events = run("cursor", "events", "agent:x", "--type", "result.submitted")
    assert [e["type"] for e in events["events"]] == ["result.submitted"]
    assert run("cursor", "ack", "agent:x", "--seq", str(events["events"][-1]["seq"]))["seq"] == events["events"][-1]["seq"]


def test_artifact_get_writes_binary_content(world):
    services, _, run, tmp_path = world
    data = bytes(range(256))
    ref = services.envelopes.put_blob(data, actor="agent:a")["ref"]
    piece = run("artifact", "get", ref, "--length", "16")
    assert piece["encoding"] == "base64" and base64.b64decode(piece["content"]) == data[:16]
    output = tmp_path / "copy.bin"
    run("artifact", "get", ref, "--output", str(output))
    assert output.read_bytes() == data


# -------- same errors everywhere --------
def test_errors_are_the_same_on_the_facade_http_and_cli(world):
    services, http, run, tmp_path = world
    env = services.envelopes
    start(services)
    services.tasks.create_task("G", "C", "Task C", actor="user")
    services.tasks.transition("C", "cancel", actor="user")   # closed: its context cannot change
    bad_result = tmp_path / "bad.json"
    bad_result.write_text(json.dumps({"summary": ""}))
    cases = [
        ("not_found", lambda: env.get_envelope("NOPE"), lambda: http.get("/api/v1/tasks/NOPE/envelope"),
         ("task", "envelope", "NOPE")),
        ("invalid_request", lambda: env.get_artifact("doc://README.md"),
         lambda: http.get("/api/v1/artifacts", params={"ref": "doc://README.md"}),
         ("artifact", "get", "doc://README.md")),
        ("not_found", lambda: env.get_artifact("blob:sha256:" + "0" * 64),
         lambda: http.get("/api/v1/artifacts", params={"ref": "blob:sha256:" + "0" * 64}),
         ("artifact", "get", "blob:sha256:" + "0" * 64)),
        ("invalid_request", lambda: env.submit_result("A", {"summary": ""}, actor="agent:a"),
         lambda: http.post("/api/v1/tasks/A/results", json={"actor": "agent:a", "result": {"summary": ""}}),
         ("task", "result", "submit", "A", "--actor", "agent:a", "--file", str(bad_result))),
        ("invalid_state", lambda: env.submit_result("B", {"summary": "x"}, actor="agent:a"),
         lambda: http.post("/api/v1/tasks/B/results", json={"actor": "agent:a", "result": {"summary": "x"}}),
         None),
        ("not_found", lambda: env.get_result("result://A/9"), lambda: http.get("/api/v1/results", params={"ref": "result://A/9"}),
         ("task", "result", "show", "result://A/9")),
        ("invalid_request", lambda: env.get_cursor("agent:x", scope="team"),
         lambda: http.get("/api/v1/cursors/agent:x", params={"scope": "team"}),
         ("cursor", "show", "agent:x", "--scope", "team")),
        ("invalid_request", lambda: env.ack_events("agent:x", 10_000),
         lambda: http.post("/api/v1/cursors/agent:x/ack", json={"seq": 10_000}),
         ("cursor", "ack", "agent:x", "--seq", "10000")),
        ("invalid_state", lambda: env.set_task_context("C", actor="user", constraints=[]),
         lambda: http.put("/api/v1/tasks/C/context", json={"actor": "user", "constraints": []}),
         ("task", "context", "C", "--actor", "user", "--clear-constraints")),
    ]
    for code, facade, over_http, argv in cases:
        with pytest.raises(ServiceError) as caught:
            facade()
        assert caught.value.code == code
        response = over_http()
        assert response.status_code == caught.value.status and response.json()["detail"]["code"] == code
        if argv is not None:
            assert code in cli_error(run, *argv)


# -------- benchmark (criterion U) --------
def load_benchmark():
    spec = importlib.util.spec_from_file_location("benchmark_envelopes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_context_benchmark_is_reproducible_and_reads_less(tmp_path):
    benchmark = load_benchmark()
    first = benchmark.run_benchmark(tmp_path / "one")
    second = benchmark.run_benchmark(tmp_path / "two")
    assert first == second
    for scenario in ("start_task_with_dependencies", "resume_from_cursor"):
        numbers = first[scenario]
        assert 0 < numbers["new_bytes"] < numbers["current_bytes"]
        assert numbers["new_reads"] <= numbers["current_reads"]
