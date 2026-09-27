from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Mapping, Union

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .bootstrap import build_system
from .contracts import HumanReviewRequest, HumanStepSubmission, SocialPostRequest
from .run_request import RunRequest
from .services import ApplicationServices, ServiceError
from .services.errors import INVALID_REQUEST
from .version import __version__


def create_app(system=None, *, services: ApplicationServices | None = None,
               bundles: Mapping[str, str | Path] | None = None) -> FastAPI:
    """HTTP API over the application services; every route goes through the facade.

    Pass ``services`` to reuse a facade, or ``system`` (and optionally trusted
    ``bundles`` by name) to build one; with neither, the default system is built.
    Errors answer ``{"detail": {"code", "message", "details"}}`` with the
    status of the ``ServiceError``.
    """
    if services is None:
        services = ApplicationServices(system or build_system(), bundles=bundles)
    system_obj = services.system

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if system_obj.settings.worker_enabled:
            system_obj.worker.start()
        yield
        services.runs.close()
        system_obj.worker.stop()

    app = FastAPI(title="Multi-Agent Framework API", version=__version__, lifespan=lifespan)
    app.state.services = services

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.to_payload()})

    @app.exception_handler(RequestValidationError)
    async def request_error(request: Request, exc: RequestValidationError):
        # Same shape as the facade's invalid_request; inputs and contexts are left out.
        errors = [{"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]
        error = ServiceError(INVALID_REQUEST, "Invalid request", status=422, details={"errors": errors})
        return JSONResponse(status_code=422, content={"detail": error.to_payload()})

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "version": __version__,
            "schema_version": system_obj.db.schema_version(),
            "dry_run": system_obj.engine.router.dry_run,
            "ollama": system_obj.adapters["ollama"].health(),
            "gemini": system_obj.adapters["gemini"].health(),
            "openai": system_obj.adapters["openai"].health(),
        }

    # --- definitions ----------------------------------------------------------

    @app.get("/api/v1/workflows")
    def list_workflows(bundle: str | None = None):
        return services.definitions.list_workflows(bundle=bundle)

    @app.get("/api/v1/workflows/{workflow_id}")
    def get_workflow(workflow_id: str, bundle: str | None = None):
        return services.definitions.get_workflow(workflow_id, bundle=bundle)

    @app.get("/api/v1/agents")
    def list_agents(bundle: str | None = None):
        return services.definitions.list_agents(bundle=bundle)

    @app.get("/api/v1/bundles")
    def list_bundles():
        return services.definitions.list_bundles()

    @app.post("/api/v1/bundles/{name}/validate")
    def validate_bundle(name: str):
        return services.definitions.validate_bundle(name)

    # --- runs -------------------------------------------------------------------

    @app.post("/api/v1/runs", status_code=202)
    def create_run(request: Union[RunRequest, SocialPostRequest], bundle: str | None = None):
        """A generic ``RunRequest`` (optionally for a bundle) or the social-post request."""
        if bundle is not None and not isinstance(request, RunRequest):
            raise ServiceError(INVALID_REQUEST, "Bundles run generic requests with a workflow_id", status=422)
        return services.runs.create(request, bundle=bundle)

    @app.get("/api/v1/runs")
    def list_runs(limit: int = Query(20, ge=1, le=500), status: str = "", project: str = ""):
        return services.runs.list(limit=limit, status=status, project=project)

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str):
        return services.runs.get(run_id)

    @app.get("/api/v1/runs/{run_id}/artifacts")
    def run_artifacts(run_id: str):
        return services.runs.artifacts(run_id)

    @app.post("/api/v1/runs/{run_id}/approve")
    def approve(run_id: str):
        return services.runs.approve(run_id)

    @app.post("/api/v1/runs/{run_id}/changes")
    def changes(run_id: str, review: HumanReviewRequest):
        return services.runs.request_changes(run_id, review.feedback)

    @app.post("/api/v1/runs/{run_id}/reject")
    def reject(run_id: str, review: HumanReviewRequest):
        return services.runs.reject(run_id, review.feedback)

    @app.post("/api/v1/runs/{run_id}/regenerate")
    def regenerate(run_id: str):
        return services.runs.regenerate(run_id)

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel(run_id: str, force: bool = False):
        return services.runs.cancel(run_id, force=force)

    @app.get("/api/v1/runs/{run_id}/human-next")
    def human_next(run_id: str):
        return services.runs.human_next(run_id)

    @app.post("/api/v1/runs/{run_id}/human-submit")
    def human_submit(run_id: str, submission: HumanStepSubmission):
        return services.runs.human_submit(run_id, submission)

    return app
