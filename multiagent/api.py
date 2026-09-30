from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Mapping, Union
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import MutableHeaders

from .bootstrap import build_system
from .contracts import HumanReviewRequest, HumanStepSubmission, SocialPostRequest
from .run_request import RunRequest
from .services import ApplicationServices, ServiceError
from .services.errors import INVALID_REQUEST
from .ui import mount_ui
from .version import __version__


def _origin_tuple(value: str) -> tuple[str, str, int] | None:
    """Accept a serialized HTTP origin, never credentials, paths or opaque origins."""
    if not value or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None
                or url.path or url.query or url.fragment
                or "?" in value or "#" in value or "\\" in value
                or url.netloc.endswith(":")):
            return None
        port = url.port
        if port is not None and port < 1:
            return None
        return url.scheme, url.hostname.lower(), port or (443 if url.scheme == "https" else 80)
    except ValueError:
        return None


class LocalAPISecurityMiddleware:
    """Guard local HTTP requests; this is not authentication for network exposure."""

    def __init__(self, app, *, allowed_hosts: list[str]):
        self.app = app
        self.allowed_hosts = frozenset(allowed_hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        hosts = [value.decode("latin-1") for key, value in headers if key.lower() == b"host"]
        origin = _origin_tuple(f"{scope['scheme']}://{hosts[0]}") if len(hosts) == 1 else None
        if origin is None or origin[1] not in self.allowed_hosts:
            response = JSONResponse(status_code=400, content={"detail": {
                "code": "invalid_host", "message": "Host is not allowed", "details": {},
            }})
            await response(scope, receive, send)
            return
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origins = [value.decode("latin-1") for key, value in headers if key.lower() == b"origin"]
            sites = [value.decode("latin-1") for key, value in headers if key.lower() == b"sec-fetch-site"]
            # Origin-less httpx clients remain supported. Browser cross-site requests do not.
            invalid = (len(origins) > 1 or (bool(origins) and _origin_tuple(origins[0]) != origin)
                       or len(sites) > 1 or (bool(sites) and sites[0] not in {"same-origin", "none"}))
            if invalid:
                response = JSONResponse(status_code=403, content={"detail": {
                    "code": "forbidden_origin", "message": "Request origin is not allowed", "details": {},
                }})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class UISecurityHeadersMiddleware:
    """Apply the operator UI policy to documents, assets, redirects, and errors."""

    policy = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; font-src 'self'; "
        "object-src 'none'; frame-src 'none'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/ui" or path.startswith("/ui/")):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = self.policy
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        await self.app(scope, receive, send_with_headers)


# Request bodies of the task routes. Field rules live in services.tasks; these only fix the shape.
class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskGroupCreate(_Body):
    id: str
    title: str
    actor: str
    authorized: bool = False
    default_requires: str = "accepted"


class TaskGroupUpdate(_Body):
    actor: str
    title: str | None = None
    authorized: bool | None = None
    default_requires: str | None = None


class TaskDependencyRef(_Body):
    task_id: str
    requires: str | None = None


class TaskCreate(_Body):
    group_id: str
    id: str
    title: str
    actor: str
    objective: str = ""
    depends_on: list[Union[str, TaskDependencyRef]] = Field(default_factory=list)
    file_scope: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    preferred: str = ""


class TaskDependencyCreate(_Body):
    depends_on_id: str
    actor: str
    requires: str | None = None


class TaskTransitionRequest(_Body):
    action: str
    actor: str
    cause: str = ""
    expected_revision: int | None = None


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
    app.add_middleware(LocalAPISecurityMiddleware, allowed_hosts=system_obj.settings.api_allowed_hosts)
    app.add_middleware(UISecurityHeadersMiddleware)
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

    @app.get("/api/v1/runs/{run_id}/events")
    def run_events(run_id: str, after: int = Query(0, ge=0)):
        """Events with ``seq`` greater than ``after`` (0 returns all)."""
        return services.runs.events(run_id, after=after)

    @app.get("/api/v1/runs/{run_id}/usage")
    def run_usage(run_id: str):
        """Token usage records and totals per run, agent and step (cost is an estimate)."""
        return services.runs.usage(run_id)

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

    # --- tasks (ORQ-1) --------------------------------------------------------

    @app.get("/api/v1/task-groups")
    def list_task_groups():
        return services.tasks.list_groups()

    @app.post("/api/v1/task-groups", status_code=201)
    def create_task_group(body: TaskGroupCreate):
        return services.tasks.create_group(body.id, body.title, actor=body.actor, authorized=body.authorized,
                                           default_requires=body.default_requires)

    @app.get("/api/v1/task-groups/{group_id}")
    def get_task_group(group_id: str):
        return services.tasks.get_group(group_id)

    @app.patch("/api/v1/task-groups/{group_id}")
    def update_task_group(group_id: str, body: TaskGroupUpdate):
        return services.tasks.update_group(group_id, actor=body.actor, title=body.title,
                                           authorized=body.authorized, default_requires=body.default_requires)

    @app.get("/api/v1/tasks")
    def list_tasks(group: str | None = None, status: str | None = None, assignee: str | None = None):
        return services.tasks.list_tasks(group_id=group, status=status, assignee=assignee)

    @app.post("/api/v1/tasks", status_code=201)
    def create_task(body: TaskCreate):
        depends_on = [item if isinstance(item, str) else item.model_dump(exclude_none=True) for item in body.depends_on]
        return services.tasks.create_task(body.group_id, body.id, body.title, actor=body.actor, objective=body.objective,
                                          depends_on=depends_on, file_scope=body.file_scope,
                                          requirements=body.requirements, preferred=body.preferred)

    @app.get("/api/v1/tasks/ready")
    def ready_tasks(group: str | None = None):
        """Ready tasks of authorized groups (declared before ``/tasks/{task_id}``)."""
        return services.tasks.ready_tasks(group_id=group)

    @app.get("/api/v1/tasks/{task_id}")
    def get_task(task_id: str):
        return services.tasks.get_task(task_id)

    @app.post("/api/v1/tasks/{task_id}/dependencies")
    def add_task_dependency(task_id: str, body: TaskDependencyCreate):
        return services.tasks.add_dependency(task_id, body.depends_on_id, actor=body.actor, requires=body.requires)

    @app.delete("/api/v1/tasks/{task_id}/dependencies/{depends_on_id}")
    def remove_task_dependency(task_id: str, depends_on_id: str, actor: str = Query(...)):
        return services.tasks.remove_dependency(task_id, depends_on_id, actor=actor)

    @app.post("/api/v1/tasks/{task_id}/transitions")
    def transition_task(task_id: str, body: TaskTransitionRequest):
        """The single state-change route: ``{action, actor, cause?, expected_revision?}``."""
        return services.tasks.transition(task_id, body.action, actor=body.actor, cause=body.cause,
                                         expected_revision=body.expected_revision)

    @app.get("/api/v1/team-events")
    def team_events(after: int = Query(0, ge=0), group: str | None = None, task: str | None = None,
                    limit: int = Query(500, ge=1, le=1000)):
        """Team events with ``seq`` greater than ``after``, oldest first."""
        return services.tasks.events(after=after, group_id=group, task_id=task, limit=limit)

    mount_ui(app)  # Stage 0 UI at /ui: static files only, a client of the routes above
    return app
