from __future__ import annotations

from contextlib import asynccontextmanager
import inspect
from pathlib import Path
from typing import Any, Callable, Mapping, Union
from urllib.parse import urlsplit

import anyio
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
from .gui import mount_gui
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


class TaskGroupStateUpdate(_Body):
    state: str
    actor: str
    cause: str
    expected_revision: int


class TaskScopeUpdate(_Body):
    file_scope: list[str]
    actor: str
    expected_revision: int
    lock_policy: dict[str, list[str]] | None = None
    workspace_domain: str | None = None


class TaskGroupUpdate(_Body):
    actor: str
    title: str | None = None
    authorized: bool | None = None
    default_requires: str | None = None


class TaskDependencyRef(_Body):
    task_id: str
    requires: str | None = None


class ReleaseDependencyRef(_Body):
    depends_on_id: str
    source_ref: str


class TaskCreate(_Body):
    group_id: str
    id: str
    title: str
    actor: str
    objective: str = ""
    depends_on: list[Union[str, TaskDependencyRef]] = Field(default_factory=list)
    release_dependencies: list[ReleaseDependencyRef] = Field(default_factory=list)
    file_scope: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    preferred: str = ""
    context_refs: list[str] = Field(default_factory=list)
    reopen_group: bool = False
    expected_group_revision: int | None = None
    cause: str = ""


class TaskDependencyCreate(_Body):
    depends_on_id: str
    actor: str
    requires: str | None = None


class ReleaseModeUpdate(_Body):
    mode: str
    actor: str


class ReleaseDependencyCreate(_Body):
    depends_on_id: str
    source_ref: str
    actor: str


class DeliveryFactRequest(_Body):
    delivery_ref: str
    delivery_sha256: str
    has_patch: bool
    patchset_sha256: str
    actor: str


class TestVerdictRequest(_Body):
    test_verdict: str
    test_patchset_sha256: str
    test_ref: str
    actor: str


class IntegrationFactRequest(_Body):
    integration_commit: str
    integration_ref: str
    actor: str


class ReleaseFactsResetRequest(_Body):
    actor: str
    reason: str


class GateUpdate(_Body):
    checks: list[str]
    actor: str


class GateRunRequest(_Body):
    actor: str
    cause: str = ""


class GateAbortRequest(_Body):
    actor: str
    cause: str


class ReviewRequest(_Body):
    actor: str
    approved: bool
    comment: str = ""


class TaskTransitionRequest(_Body):
    action: str
    actor: str
    cause: str = ""
    expected_revision: int | None = None
    actor_domain: str | None = None


class TaskSnapshotImportRequest(_Body):
    snapshot: dict[str, Any]
    source_key: str
    source_ref: str
    snapshot_sha256: str
    actor: str
    what_if: bool = False


class TaskSnapshotRollbackRequest(_Body):
    actor: str


class TaskContextUpdate(_Body):
    actor: str
    base_commit: str | None = None
    context_refs: list[str] | None = None
    constraints: list[str] | None = None


class TaskResultSubmit(_Body):
    actor: str
    result: dict[str, Any]


class AssignmentPreviewRequest(_Body):
    profile_id: str | None = None
    requested_executor: str | None = None
    requested_model: str | None = None
    requested_effort: str | None = None
    required_context_isolation: str | None = None
    additional_requirements: list[str] = Field(default_factory=list)
    permission_scope: list[str] | None = None
    data_class: str | None = None
    step_kind: str | None = None      # orchestration | planning | review | work (default): picks the routing level (ECO-T08)


class AssignmentAcceptRequest(_Body):
    preview_token: str
    acknowledge: bool


class ExecutorRunRequest(_Body):
    task_id: str
    executor_id: str | None = None
    model: str | None = None
    effort: str | None = None
    profile_id: str | None = None
    actor: str | None = None
    timeout_seconds: int | None = None
    step_kind: str | None = None


class ExecutorResumeRequest(_Body):
    task_id: str
    model: str | None = None
    effort: str | None = None
    actor: str | None = None
    timeout_seconds: int | None = None


class ExecutorOpenRequest(_Body):
    task_id: str
    actor: str | None = None


class CursorAck(_Body):
    seq: int
    scope: str = ""


def create_app(system=None, *, services: ApplicationServices | None = None,
               bundles: Mapping[str, str | Path] | None = None,
               operator_context_provider: Callable[[Request], Any] | None = None) -> FastAPI:
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

    @app.post("/api/v1/task-groups/{group_id}/state")
    def set_task_group_state(group_id: str, body: TaskGroupStateUpdate):
        return services.tasks.set_group_state(group_id, body.state, actor=body.actor, cause=body.cause,
                                              expected_revision=body.expected_revision)

    @app.put("/api/v1/task-groups/{group_id}/release-mode")
    def set_group_release_mode(group_id: str, body: ReleaseModeUpdate):
        return services.tasks.set_release_mode(group_id, body.mode, actor=body.actor)

    @app.post("/api/v1/task-snapshots/import")
    def import_task_snapshot(body: TaskSnapshotImportRequest):
        operation = services.tasks.plan_snapshot if body.what_if else services.tasks.import_snapshot
        return operation(body.snapshot, source_key=body.source_key, source_ref=body.source_ref,
                         snapshot_sha256=body.snapshot_sha256, actor=body.actor)

    @app.get("/api/v1/task-snapshots/{source_key}")
    def get_task_snapshot(source_key: str):
        return services.tasks.get_snapshot(source_key)

    @app.post("/api/v1/task-snapshots/{source_key}/rollback")
    def rollback_task_snapshot(source_key: str, body: TaskSnapshotRollbackRequest):
        return services.tasks.rollback_snapshot(source_key, actor=body.actor)

    @app.get("/api/v1/tasks")
    def list_tasks(group: str | None = None, status: str | None = None, assignee: str | None = None):
        return services.tasks.list_tasks(group_id=group, status=status, assignee=assignee)

    @app.post("/api/v1/tasks", status_code=201)
    def create_task(body: TaskCreate):
        depends_on = [item if isinstance(item, str) else item.model_dump(exclude_none=True) for item in body.depends_on]
        release_dependencies = [item.model_dump() for item in body.release_dependencies]
        return services.tasks.create_task(body.group_id, body.id, body.title, actor=body.actor, objective=body.objective,
                                          depends_on=depends_on, file_scope=body.file_scope,
                                          requirements=body.requirements, preferred=body.preferred,
                                          release_dependencies=release_dependencies, reopen_group=body.reopen_group,
                                          expected_group_revision=body.expected_group_revision, cause=body.cause,
                                          context_refs=body.context_refs)

    @app.get("/api/v1/tasks/ready")
    def ready_tasks(group: str | None = None):
        """Ready tasks of authorized groups (declared before ``/tasks/{task_id}``)."""
        return services.tasks.ready_tasks(group_id=group)

    @app.get("/api/v1/tasks/{task_id}")
    def get_task(task_id: str):
        return services.tasks.get_task(task_id)

    @app.post("/api/v1/tasks/{task_id}/scope")
    def update_task_scope(task_id: str, body: TaskScopeUpdate):
        return services.tasks.update_scope(task_id, file_scope=body.file_scope, actor=body.actor,
                                           expected_revision=body.expected_revision,
                                           lock_policy=body.lock_policy, workspace_domain=body.workspace_domain)

    @app.post("/api/v1/tasks/{task_id}/dependencies")
    def add_task_dependency(task_id: str, body: TaskDependencyCreate):
        return services.tasks.add_dependency(task_id, body.depends_on_id, actor=body.actor, requires=body.requires)

    @app.delete("/api/v1/tasks/{task_id}/dependencies/{depends_on_id}")
    def remove_task_dependency(task_id: str, depends_on_id: str, actor: str = Query(...)):
        return services.tasks.remove_dependency(task_id, depends_on_id, actor=actor)

    @app.post("/api/v1/tasks/{task_id}/release-dependencies")
    def add_release_dependency(task_id: str, body: ReleaseDependencyCreate):
        return services.tasks.add_release_dependency(task_id, body.depends_on_id, actor=body.actor,
                                                     source_ref=body.source_ref)

    @app.delete("/api/v1/tasks/{task_id}/release-dependencies/{depends_on_id}")
    def remove_release_dependency(task_id: str, depends_on_id: str, actor: str = Query(...)):
        return services.tasks.remove_release_dependency(task_id, depends_on_id, actor=actor)

    @app.get("/api/v1/tasks/{task_id}/release-facts")
    def get_release_facts(task_id: str):
        return services.tasks.get_release_facts(task_id)

    @app.post("/api/v1/tasks/{task_id}/release-facts/delivery")
    def record_delivery_fact(task_id: str, body: DeliveryFactRequest):
        return services.tasks.record_delivery_fact(
            task_id, delivery_ref=body.delivery_ref, delivery_sha256=body.delivery_sha256,
            has_patch=body.has_patch, patchset_sha256=body.patchset_sha256, actor=body.actor,
        )

    @app.post("/api/v1/tasks/{task_id}/release-facts/test")
    def record_test_verdict(task_id: str, body: TestVerdictRequest):
        return services.tasks.record_test_verdict(
            task_id, test_verdict=body.test_verdict, test_patchset_sha256=body.test_patchset_sha256,
            test_ref=body.test_ref, actor=body.actor,
        )

    @app.post("/api/v1/tasks/{task_id}/release-facts/integration")
    def record_integration_fact(task_id: str, body: IntegrationFactRequest):
        return services.tasks.record_integration_fact(
            task_id, integration_commit=body.integration_commit, integration_ref=body.integration_ref,
            actor=body.actor,
        )

    @app.post("/api/v1/tasks/{task_id}/release-facts/reset")
    def reset_release_facts(task_id: str, body: ReleaseFactsResetRequest):
        return services.tasks.reset_release_facts(task_id, actor=body.actor, reason=body.reason)

    @app.post("/api/v1/tasks/{task_id}/transitions")
    def transition_task(task_id: str, body: TaskTransitionRequest):
        """The single state-change route: ``{action, actor, cause?, expected_revision?}``."""
        return services.tasks.transition(task_id, body.action, actor=body.actor, cause=body.cause,
                                         expected_revision=body.expected_revision, actor_domain=body.actor_domain)

    # --- gates (ORQ-2) ----------------------------------------------------------

    @app.get("/api/v1/tasks/{task_id}/gate")
    def get_task_gate(task_id: str):
        return services.gates.get_gate(task_id)

    @app.put("/api/v1/tasks/{task_id}/gate")
    def set_task_gate(task_id: str, body: GateUpdate):
        return services.gates.set_task_gate(task_id, body.checks, actor=body.actor)

    @app.put("/api/v1/task-groups/{group_id}/gate")
    def set_group_gate(group_id: str, body: GateUpdate):
        return services.gates.set_group_gate(group_id, body.checks, actor=body.actor)

    @app.post("/api/v1/tasks/{task_id}/gate/run")
    def run_task_gate(task_id: str, body: GateRunRequest):
        """Run the task's gate now; it only runs project commands from the server's local file."""
        return services.gates.run_gate(task_id, actor=body.actor, cause=body.cause)

    @app.post("/api/v1/tasks/{task_id}/gate/abort")
    def abort_task_gate(task_id: str, body: GateAbortRequest):
        return services.gates.abort_gate(task_id, actor=body.actor, cause=body.cause)

    @app.get("/api/v1/tasks/{task_id}/gate/results")
    def task_gate_results(task_id: str, attempt: int | None = Query(None, ge=0)):
        return services.gates.results(task_id, attempt=attempt)

    @app.post("/api/v1/tasks/{task_id}/reviews")
    def review_task(task_id: str, body: ReviewRequest):
        return services.gates.review(task_id, actor=body.actor, approved=body.approved, comment=body.comment)

    @app.get("/api/v1/project-commands")
    def project_commands():
        """Configured command names, timeouts and working directories; never the argv."""
        return services.gates.project_commands()

    @app.get("/api/v1/team-events")
    def team_events(after: int = Query(0, ge=0), group: str | None = None, task: str | None = None,
                    limit: int = Query(500, ge=1, le=1000)):
        """Team events with ``seq`` greater than ``after``, oldest first."""
        return services.tasks.events(after=after, group_id=group, task_id=task, limit=limit)

    # --- envelopes, results, blobs and cursors (ORQ-3) ---------------------------

    @app.put("/api/v1/tasks/{task_id}/context")
    def set_task_context(task_id: str, body: TaskContextUpdate):
        return services.envelopes.set_task_context(task_id, actor=body.actor, base_commit=body.base_commit,
                                                   context_refs=body.context_refs, constraints=body.constraints)

    @app.get("/api/v1/tasks/{task_id}/envelope")
    def get_task_envelope(task_id: str, known_hash: str | None = None):
        """The task envelope v1, or ``{unchanged, hash, revision}`` when ``known_hash`` still matches."""
        return services.envelopes.get_envelope(task_id, known_hash=known_hash)

    @app.get("/api/v2/tasks/{task_id}/context")
    def get_task_context_v2(task_id: str, known_fingerprint: str | None = None,
                            after: int = Query(0, ge=0)):
        return services.envelopes.get_context_v2(task_id, known_fingerprint=known_fingerprint, after=after)

    @app.post("/api/v1/blobs")
    async def put_blob(request: Request, actor: str = Query(...),
                       media_type: str = "application/octet-stream", sha256: str | None = None):
        """Store the raw request body by its sha256 (same size limit as the service: 413 above it)."""
        limit = services.envelopes.blob_max_bytes
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise ServiceError(INVALID_REQUEST, f"The blob is larger than {limit} bytes", status=413,
                               details={"field": "content", "limit": limit})
        content = bytearray()
        async for chunk in request.stream():
            content += chunk
            if len(content) > limit:
                raise ServiceError(INVALID_REQUEST, f"The blob is larger than {limit} bytes", status=413,
                                   details={"field": "content", "limit": limit})
        return services.envelopes.put_blob(bytes(content), actor=actor, media_type=media_type,
                                           expected_sha256=sha256)

    @app.get("/api/v1/artifacts")
    def get_artifact(ref: str, offset: int = 0, length: int = 64 * 1024):
        """A range of a blob by ``blob:sha256:<hex>``; text as UTF-8, the rest as base64."""
        return services.envelopes.get_artifact(ref, offset=offset, length=length)

    @app.post("/api/v1/tasks/{task_id}/results")
    def submit_task_result(task_id: str, body: TaskResultSubmit):
        """Store the result of the current attempt and submit the task in one step."""
        return services.envelopes.submit_result(task_id, body.result, actor=body.actor)

    @app.get("/api/v1/tasks/{task_id}/results")
    def task_results(task_id: str, attempt: int | None = None):
        """Summaries of every attempt's result, or the full result of one ``attempt``."""
        if attempt is None:
            return services.envelopes.list_results(task_id)
        return services.envelopes.get_result(task_id, attempt=attempt)

    @app.post("/api/v1/tasks/{task_id}/assignment/preview")
    async def preview_task_assignment(task_id: str, body: AssignmentPreviewRequest, request: Request):
        operator_context = operator_context_provider(request) if operator_context_provider else None
        if inspect.isawaitable(operator_context):
            operator_context = await operator_context
        return services.assignment.preview(task_id, body.model_dump(exclude_none=True),
                                           operator_context=operator_context)

    @app.post("/api/v1/tasks/{task_id}/assignment/accept")
    async def accept_task_assignment(task_id: str, body: AssignmentAcceptRequest, request: Request):
        operator_context = operator_context_provider(request) if operator_context_provider else None
        if inspect.isawaitable(operator_context):
            operator_context = await operator_context
        return services.assignment.accept(task_id, body.preview_token, body.acknowledge,
                                          operator_context=operator_context)

    # --- executor applications (ORQ-T51) ---------------------------------------------

    @app.get("/api/v1/executors/status")
    def executors_status(task: str | None = None):
        """Executors with an adapter (enabled, paused until) and, with ``task``, its sessions and attempts."""
        return services.executors.status(task)

    @app.post("/api/v1/executors/run")
    async def run_executor_task(body: ExecutorRunRequest, request: Request):
        """Launch a task in an executor application; waits for the attempt, so it can take minutes."""
        operator_context = operator_context_provider(request) if operator_context_provider else None
        if inspect.isawaitable(operator_context):
            operator_context = await operator_context
        return await anyio.to_thread.run_sync(lambda: services.executors.run_task(
            body.task_id, body.executor_id, body.model, body.effort, body.actor, profile_id=body.profile_id,
            timeout_seconds=body.timeout_seconds, operator_context=operator_context, step_kind=body.step_kind))

    @app.post("/api/v1/executors/resume")
    async def resume_executor_task(body: ExecutorResumeRequest):
        """Continue the session the framework created for the task (never one opened in an app in the last 12 hours)."""
        return await anyio.to_thread.run_sync(lambda: services.executors.resume_task(
            body.task_id, body.model, body.effort, body.actor, timeout_seconds=body.timeout_seconds))

    @app.post("/api/v1/executors/open")
    def open_executor_session(body: ExecutorOpenRequest):
        """The target that opens the task's session in its app; it also records the open (12 hour resume guard)."""
        return services.executors.open_target(body.task_id, body.actor)

    @app.get("/api/v1/results")
    def get_result(ref: str):
        """A full result by ``result://<task_id>/<attempt>``."""
        return services.envelopes.get_result(ref)

    @app.get("/api/v1/cursors/{consumer}")
    def get_cursor(consumer: str, scope: str = ""):
        return services.envelopes.get_cursor(consumer, scope=scope)

    @app.get("/api/v1/cursors/{consumer}/events")
    def read_cursor_events(consumer: str, scope: str = "", type: list[str] | None = Query(None),
                           mine: bool = False, limit: int = 100):
        """Events after the consumer's cursor; reading does not move it (``POST .../ack`` does)."""
        return services.envelopes.read_events(consumer, scope=scope, types=type, assigned_to_me=mine, limit=limit)

    @app.post("/api/v1/cursors/{consumer}/ack")
    def ack_cursor(consumer: str, body: CursorAck):
        return services.envelopes.ack_events(consumer, body.seq, scope=body.scope)

    mount_gui(app)  # operator GUI at /gui (token-protected /gui/api, docs/GUI.md)
    mount_ui(app)  # Stage 0 UI at /ui: static files only, a client of the routes above
    return app
