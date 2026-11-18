"""Isolated, durable runtime shared by backend conformance cases."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from multiagent.adapters.fake import FakeAdapter
from multiagent.artifacts import ArtifactWriter
from multiagent.backends import BackendContext, build_backend
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.model_router import ModelRouter
from multiagent.run_request import RunRequest
from multiagent.run_service import RunService
from multiagent.worker import LocalWorker


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "backend_conformance"


@pytest.fixture
def backend_system(tmp_path, request):
    """Build the named backend with fresh storage and only a FakeAdapter."""
    name = request.node.callspec.params["system"]
    settings = Settings(
        _env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
        gemini_api_key="", openai_api_key="", openai_enabled=False,
    )
    settings.ensure_directories()
    store = Store(Database(settings))
    store.db.migrate()
    bundle = load_bundle(FIXTURES, settings)
    adapter = FakeAdapter(output_schemas=bundle.output_schemas)
    router = ModelRouter(bundle.catalog, {"fake": adapter}, dry_run=True)
    sample = adapter.generate_structured(
        spec=None, prompt="", output_schema=bundle.output_schemas["ContentOutput"],
    ).parsed.model_dump(mode="json")
    context = BackendContext(
        store=store, settings=settings, workflows=bundle.workflows,
        prompts=bundle.prompts, catalog=bundle.catalog,
        artifacts=ArtifactWriter(settings), router=router,
        output_schemas=bundle.output_schemas,
        sample_outputs={"ContentOutput": sample},
    )
    backend = build_backend(name, context)
    return SimpleNamespace(
        name=name, settings=settings, store=store, backend=backend,
        service=RunService(settings, store, engine=backend),
        worker=LocalWorker(store, backend), adapter=adapter,
    )


def create_run(system, workflow_id="two_steps", **overrides):
    return system.service.create_run(RunRequest(
        workflow_id=workflow_id, project_name="Conformance fixture",
        inputs={"topic": "durable execution"}, **overrides,
    ))


def advance(system, run_id):
    assert system.worker.process_once(), "the queued run was not picked up"
    return system.store.get_run(run_id)


def events(run, kind):
    return [event for event in run["events"] if event["event_type"] == kind]


def steps(system, run_id):
    with system.store.db.connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM run_steps WHERE run_id=? ORDER BY rowid", (run_id,),
        )]


def usage(system, run_id):
    with system.store.db.connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM telemetry WHERE run_id=? ORDER BY id", (run_id,),
        )]
