# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARR3-T06: the result of a completed run as a Markdown document (service, API, GUI route and CLI)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.documents import document_filename
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.services.errors import ServiceError

GENERAL = Path(__file__).resolve().parents[1] / "examples" / "bundles" / "general"
BODY = {"workflow_id": "ask_once", "project_name": "Document", "inputs": {"task": "Explica la idea."}}


class _ClientContext:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, exc_type, exc, traceback):
        return False


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"general": GENERAL}, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, headers={"Authorization": f"Bearer {token}"},
                              tmp=tmp_path)
    services.runs.close()


def _create(env) -> str:
    response = env.client.post("/api/v1/runs", params={"bundle": "general"}, json=BODY)
    assert response.status_code == 202, response.text
    return response.json()["id"]


def _completed(env) -> str:
    run_id = _create(env)
    assert env.services.runs._bundle_runtime("general").worker.process_once()
    assert env.services.runs.get(run_id)["status"] == "completed"
    return run_id


def _cli(monkeypatch, capsys, env, args):
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(env.client))
    code = 0
    try:
        cli.main(args)
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_document_of_completed_run(env):
    run_id = _completed(env)
    document = env.services.runs.document(run_id)
    assert document["schema"] == "maf.run-document/1"
    assert document["run_id"] == run_id and document["step_id"] == "answer"
    assert document["title"] == "Responde una tarea sencilla en una sesión única con el modelo elegido."
    assert document["markdown"].startswith("# ")
    assert document["filename"].endswith(f"-{run_id[:8]}.md")


def test_document_reasons(env):
    run_id = _create(env)  # queued: the worker has not run
    with pytest.raises(ServiceError) as raised:
        env.services.runs.document(run_id)
    assert raised.value.status == 409 and raised.value.code == "invalid_state"
    assert raised.value.details["reason"] == "run_not_completed"
    response = env.client.get(f"/api/v1/runs/{run_id}/document.md")
    assert response.status_code == 409
    assert env.client.get("/api/v1/runs/unknown-run/document.md").status_code == 404
    with pytest.raises(ServiceError) as missing:
        env.services.runs.document("unknown-run")
    assert missing.value.status == 404


def test_document_without_a_result_step_output(env):
    run_id = _completed(env)
    runtime = env.services.runs._bundle_runtime("general")
    with runtime.store.db.connect() as conn:
        conn.execute("DELETE FROM run_steps WHERE run_id=?", (run_id,))
        conn.commit()
    with pytest.raises(ServiceError) as raised:
        env.services.runs.document(run_id)
    assert raised.value.status == 409 and raised.value.details["reason"] == "no_result"


def test_api_markdown_route(env):
    run_id = _completed(env)
    document = env.services.runs.document(run_id)
    response = env.client.get(f"/api/v1/runs/{run_id}/document.md")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["content-disposition"] == f'attachment; filename="{document["filename"]}"'
    assert response.text == document["markdown"]


def test_gui_route_same_payload(env):
    run_id = _completed(env)
    response = env.client.get(f"/gui/api/runs/{run_id}/document", headers=env.headers)
    assert response.status_code == 200
    assert response.json() == env.services.runs.document(run_id)
    assert env.client.get(f"/gui/api/runs/{run_id}/document").status_code == 401


def test_cli_prints_and_writes(env, monkeypatch, capsys):
    run_id = _completed(env)
    markdown = env.services.runs.document(run_id)["markdown"]
    code, out, _ = _cli(monkeypatch, capsys, env, ["run-document", run_id])
    assert code == 0 and out == markdown
    target = env.tmp / "out" / "resultado.md"
    target.parent.mkdir()
    code, out, _ = _cli(monkeypatch, capsys, env, ["run-document", run_id, "--output", str(target)])
    assert code == 0 and target.read_text(encoding="utf-8") == markdown
    target.write_text("mío", encoding="utf-8")
    code, _, err = _cli(monkeypatch, capsys, env, ["run-document", run_id, "--output", str(target)])
    assert code == 1 and "already exists" in err
    assert target.read_text(encoding="utf-8") == "mío"


def test_cli_reports_a_run_without_document(env, monkeypatch, capsys):
    run_id = _create(env)
    code, out, err = _cli(monkeypatch, capsys, env, ["run-document", run_id])
    assert code == 1 and out == "" and "run_not_completed" in err


def test_filename():
    assert document_filename("Resumen de documento", "3f2a9c1b-aaaa-bbbb") == "resumen-de-documento-3f2a9c1b.md"
    assert document_filename("¡Ñandú, rápido!  ", "ab12cd34ef") == "nandu-rapido-ab12cd34.md"
    long = document_filename("x" * 200, "3f2a9c1b-1")
    assert long == "x" * 60 + "-3f2a9c1b.md"
    assert document_filename("???", "3f2a9c1b") == "3f2a9c1b.md"


def test_document_in_bundle_runtime(env):
    # The run lives in the store of the registered bundle: its workflow and its contract are the ones used.
    run_id = _completed(env)
    assert env.services.runs._find(run_id).bundle == "general"
    document = env.services.runs.document(run_id)
    assert "Esta es una respuesta de ejemplo de Mock." in document["markdown"]
