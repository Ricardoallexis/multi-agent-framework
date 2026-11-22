# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Failure detail formatting, secret redaction and propagation for function steps."""
from __future__ import annotations

from multiagent.adapters.fake import FakeAdapter
from multiagent.artifacts import ArtifactWriter
from multiagent.backends import BackendContext, build_backend
from multiagent.bootstrap import System
from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.functions import FAILURE_DETAIL_LIMIT, FunctionStepError, failure_detail, get_function, register_function
from multiagent.gui.threads_api import build_thread
from multiagent.model_router import ModelRouter
from multiagent.prompts import PromptManager
from multiagent.run_request import RunRequest
from multiagent.run_service import RunService
from multiagent.services import ApplicationServices
from multiagent.worker import LocalWorker
from multiagent.workflow_engine import WorkflowEngine
from multiagent.workflows import WorkflowCatalog


def _detail_raises(context, arguments):
    raise ValueError("missing date column")


if get_function("test.detail_raises") is None:
    register_function("test.detail_raises", _detail_raises, description="Raise a ValueError for detail checks")


def _mock_system(tmp_path, workflow_dir):
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        execution_backend="mock",
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )
    settings.ensure_directories()
    db = Database(settings)
    db.migrate()
    store = Store(db)
    catalog = Catalog(settings)
    prompts = PromptManager(settings)
    workflows = WorkflowCatalog(settings)
    workflows.workflows_dir = workflow_dir
    artifacts = ArtifactWriter(settings)
    adapter = FakeAdapter()
    router = ModelRouter(catalog, {"fake": adapter}, dry_run=True)
    context = BackendContext(
        store=store, catalog=catalog, prompts=prompts, workflows=workflows,
        router=router, artifacts=artifacts, settings=settings,
    )
    backend = build_backend("mock", context)
    engine = WorkflowEngine(
        store=store, catalog=catalog, prompts=prompts, workflows=workflows,
        router=router, artifacts=artifacts, settings=settings,
    )
    run_service = RunService(settings, store, engine=backend)
    worker = LocalWorker(store, backend)
    return System(
        settings=settings, db=db, store=store, catalog=catalog,
        run_service=run_service, brand_service=None, asset_service=None,
        engine=engine, backend=backend, worker=worker, adapters={"fake": adapter},
    )


def test_detail_starts_with_type_and_has_no_newlines():
    assert failure_detail(ValueError("a\nb")) == "ValueError: a b"
    assert failure_detail(ValueError("a\r\nb\tc")) == "ValueError: a b c"


def test_detail_cut_to_300():
    detail = failure_detail(ValueError("x" * 1000))
    assert len(detail) == FAILURE_DETAIL_LIMIT == 300
    assert detail.endswith("…")


def test_secret_redacted_before_cut():
    token = "sk" + "-" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    detail = failure_detail(ValueError("x" * 270 + " " + token))
    assert len(detail) == FAILURE_DETAIL_LIMIT
    assert "[REDACTED:" in detail
    assert all(token[i:i + 5] not in detail for i in range(len(token) - 4))


def test_secret_redacted_short_message():
    token = "sk" + "-" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    detail = failure_detail(RuntimeError(f"bad token {token}"))
    assert detail == "RuntimeError: bad token [REDACTED:secret_key:1]"
    assert token not in detail


def test_detail_in_run_error_event_and_thread(tmp_path):
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "function_detail.yaml").write_text(
        "id: function_detail\n"
        "steps:\n"
        "  - id: fail_step\n"
        "    function: test.detail_raises\n"
        "    arguments: {}\n"
        "    contract: ContentOutput\n",
        encoding="utf-8",
    )
    system = _mock_system(tmp_path, workflow_dir)
    run = system.run_service.create_run(RunRequest(
        workflow_id="function_detail",
        project_name="Failure detail fixture",
        inputs={"topic": "detail"},
    ))
    assert system.worker.process_once()
    final = system.store.get_run(run["id"])
    assert final["status"] == "failed"

    expected_detail = "ValueError: missing date column"
    failed_event = [event for event in final["events"] if event["event_type"] == "workflow_failed"][0]
    assert failed_event["payload"]["detail"] == expected_detail
    assert expected_detail in final["error"]

    thread = build_thread(ApplicationServices(system), final)
    final_entry = [entry for entry in thread["entries"] if entry["kind"] == "final"][-1]
    assert final_entry["status"] == "failed"
    assert expected_detail in final_entry["error"]
    assert final_entry["error"] == final["error"]


def test_str_includes_detail():
    assert str(FunctionStepError("function_failed", "x", "ValueError: y")) == (
        "function_failed: function step 'x' — ValueError: y"
    )
