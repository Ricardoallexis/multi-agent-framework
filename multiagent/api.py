from __future__ import annotations

from contextlib import asynccontextmanager
import inspect
from pathlib import Path
from typing import Any, Callable, Mapping, Union
from urllib.parse import urlsplit

import anyio
from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .bootstrap import build_system
from .contracts import FeedbackRequest, HumanReviewRequest, HumanStepSubmission, SocialPostRequest
from .run_request import RunRequest
from .services import ApplicationServices, ServiceError
from .services.availability import FOREGROUND_RUNNING
from .services.errors import INVALID_REQUEST
from .gui import mount_gui
from .version import __version__


class HumanCheckBody(BaseModel):
    raw_response: str = Field(min_length=1)


class ResearchSubmission(HumanStepSubmission):
    provider: str = Field(default="human_web", max_length=120)


class ExternalEvidenceBody(BaseModel):
    name: str
    content_base64: str


class ExternalPreviewBody(BaseModel):
    output: dict[str, Any]
    evidence: list[ExternalEvidenceBody] = Field(default_factory=list)
    actor: str = ""
    channel: str = "api"


class ExternalConfirmBody(BaseModel):
    preview_id: str
    actor: str
    channel: str = "api"


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


# Request bodies of the task routes. Field rules live in services.tasks; these only fix the shape.
class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstalledInstallRequest(_Body):
    # No ``trust_functions`` here: only the GUI sets it (PAQ-T03).
    name: str
    folder: str
    actor: str
    source_label: str = ""


class InstalledReinstallRequest(_Body):
    folder: str
    actor: str


class InstalledRemoveRequest(_Body):
    actor: str


class PlanStatusRequest(_Body):
    document: dict[str, Any]


class PlanAdvanceRequest(PlanStatusRequest):
    actor: str


class PlanProposeRequest(_Body):
    planner: str
    request: dict[str, Any]
    confirm_cloud: bool = False


class PlanProposalSave(_Body):
    proposal: dict[str, Any]
    actor: str
    channel: str = "api"
    note: str = ""


class PlanProposalEdit(_Body):
    tasks: list[dict[str, Any]]
    actor: str
    channel: str = "api"
    note: str = ""
    expected_revision: int


class PlanProposalReassign(_Body):
    task_id: str
    actor: str
    channel: str = "api"
    note: str = ""
    expected_revision: int
    preferred: str | None = None
    workflow: str | None = None
    bundle: str | None = None


class PlanProposalApprove(_Body):
    actor: str
    channel: str = "api"
    note: str = ""
    expected_revision: int


class PlanProposalReject(_Body):
    actor: str
    channel: str = "api"
    note: str = ""


class PlanProposalAdvance(_Body):
    actor: str
    channel: str = "api"


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


class TaskWaitSet(_Body):
    actor: str
    expected_revision: int = Field(strict=True, ge=1)
    who: str
    question: str
    target: str = ""
    reference: str = ""
    reset_at: str | None = None


class TaskWaitClear(_Body):
    actor: str
    expected_revision: int = Field(strict=True, ge=1)
    wait_id: str


class TaskWaitDecision(TaskWaitClear):
    decision: str


class ExecutorRunRequest(_Body):
    task_id: str
    executor_id: str | None = None
    model: str | None = None
    effort: str | None = None
    profile_id: str | None = None
    actor: str | None = None
    timeout_seconds: int | None = None
    step_kind: str | None = None
    wait: bool | None = None          # AVA-T02: true waits for the whole attempt; omitted follows the availability policy
    accept_quota_warning: StrictBool = False


class ExecutorResumeRequest(_Body):
    task_id: str
    model: str | None = None
    effort: str | None = None
    actor: str | None = None
    timeout_seconds: int | None = None


class ExecutorOpenRequest(_Body):
    task_id: str
    actor: str | None = None


class ExecutorResumeDueRequest(_Body):
    task_id: str | None = None
    limit: int | None = None
    dry_run: bool = False
    timeout_seconds: int | None = None


class ExecutorHandoffRequest(_Body):
    task_id: str
    executor_id: str | None = None        # without it, or the same one: the delegate a person chose on the card; no other
    decision_id: str | None = None
    expected_revision: int | None = None
    actor: str | None = None              # only a claim here (no token): an executor, system or policy actor is refused
    run: bool = True
    timeout_seconds: int | None = None


class ExecutorDecideHoldsRequest(_Body):
    task_id: str | None = None
    limit: int | None = None
    dry_run: bool = False
    run: bool = True
    timeout_seconds: int | None = None


class ExecutorLiftPauseRequest(_Body):
    executor_id: str
    actor: str | None = None
    reason: str = ""


class CursorAck(_Body):
    seq: int
    scope: str = ""


class ProjectPlanRequest(_Body):
    doc: Union[dict[str, Any], str]
    key: str
    title: str | None = None
    visibility: str | None = None


class ProjectCreateRequest(ProjectPlanRequest):
    plan_sha256: str
    actor: str


class ProjectUpdateRequest(_Body):
    actor: str
    expected_revision: int
    title: str | None = None
    visibility: str | None = None


class ProjectRevisionRequest(_Body):
    actor: str
    expected_revision: int


class ProjectStageAttach(ProjectRevisionRequest):
    group_id: str


class ProjectStageOrder(ProjectRevisionRequest):
    group_ids: list[str]


class TaskSessionLink(_Body):
    session_id: str
    actor: str


class ActivityStart(_Body):
    title: str
    actor: str
    executor: str | None = None
    idempotency_key: str | None = None
    objective: str = ""


class MemoryCreate(_Body):
    kind: str
    title: str
    body: str
    actor: str
    confidence: str | None = None
    source_ref: str = ""


class DecisionPropose(_Body):
    title: str
    body: str
    actor: str
    valid_until: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    attempt: int | None = None
    task_id: str | None = None
    team_id: str | None = None
    project: str | None = None


class DecisionResolution(_Body):
    actor: str
    outcome: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None


class DecisionSupersede(_Body):
    actor: str
    replacement_id: str | None = None
    title: str | None = None
    body: str | None = None
    outcome: str | None = None
    valid_until: str | None = None


class TeamPropose(_Body):
    actor: str
    draft: bool = False
    channel: str = "api"


class TeamSubmit(_Body):
    actor: str
    channel: str = "api"


class TeamDecision(_Body):
    actor: str
    note: str | None = ""
    channel: str = "api"


class ActivityClose(_Body):
    summary: str
    actor: str


class InboxMessageRequest(_Body):
    """A message of the person to a run or a task (AVA-T03); ``message_id`` makes sending it again harmless."""
    message_id: str
    kind: str
    text: str = ""
    actor: str


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
        # AVA-T02: no program started in the background outlives the API that launched it. Stopping closes the
        # background (nothing new is launched); starting opens it again, for services that outlive one app.
        background = getattr(getattr(services, "executors", None), "background", None)
        if background is not None:
            background.reopen()
        services.runs.reopen()   # CER-T01: services that outlive one app build package runtimes again
        if system_obj.settings.worker_enabled:
            system_obj.worker.start()
        yield
        if background is not None:
            await anyio.to_thread.run_sync(background.shutdown)
        bundle_scans = getattr(services, "bundle_scans", None)
        if bundle_scans is not None:
            await anyio.to_thread.run_sync(bundle_scans.close)
        services.runs.close()
        system_obj.worker.stop()

    app = FastAPI(title="Multi-Agent Framework API", version=__version__, lifespan=lifespan)
    app.add_middleware(LocalAPISecurityMiddleware, allowed_hosts=system_obj.settings.api_allowed_hosts)
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

    # SES-T01: cached, non-generating connection facts and one-tool recheck.
    @app.get("/api/v1/connections")
    def list_connections():
        return services.connections.list()

    @app.post("/api/v1/connections/{connection_id}/check")
    def check_connection(connection_id: str):
        return services.connections.check(connection_id)

    # --- definitions ----------------------------------------------------------

    @app.get("/api/v1/workflows")
    def list_workflows(bundle: str | None = None):
        return services.definitions.list_workflows(bundle=bundle)

    @app.get("/api/v1/workflows/{workflow_id}")
    def get_workflow(workflow_id: str, bundle: str | None = None):
        return services.definitions.get_workflow(workflow_id, bundle=bundle)

    @app.get("/api/v1/workflows/{workflow_id}/inputs")
    def workflow_inputs(workflow_id: str, bundle: str | None = None):
        return services.definitions.workflow_inputs(workflow_id, bundle=bundle)

    @app.get("/api/v1/agents")
    def list_agents(bundle: str | None = None):
        return services.definitions.list_agents(bundle=bundle)

    @app.get("/api/v1/bundles")
    def list_bundles():
        return services.definitions.list_bundles()

    @app.post("/api/v1/bundles/{name}/validate")
    def validate_bundle(name: str):
        return services.definitions.validate_bundle(name)

    # SKL-T02: reading scan status never starts the worker or decides an approval.
    @app.get("/api/v1/bundles/{name}/scan")
    def bundle_scan_status(name: str):
        return services.bundle_scans.status(name)

    # --- installed packages (PAQ-T01) ------------------------------------------

    @app.get("/api/v1/installed")
    def list_installed():
        return services.installed.list()

    @app.get("/api/v1/installed/{name}")
    def get_installed(name: str):
        return services.installed.get(name)

    @app.post("/api/v1/installed", status_code=201)
    def install_package(body: InstalledInstallRequest):
        return services.installed.install_folder(body.name, Path(body.folder), actor=body.actor, channel="api",
                                                 source_label=body.source_label)

    @app.post("/api/v1/installed/{name}/reinstall")
    def reinstall_package(name: str, body: InstalledReinstallRequest):
        return services.installed.reinstall(name, Path(body.folder), actor=body.actor, channel="api")

    @app.post("/api/v1/installed/{name}/remove")
    def remove_package(name: str, body: InstalledRemoveRequest):
        return services.installed.remove(name, actor=body.actor, channel="api")

    # --- evaluation sets (EVA-T01) ------------------------------------------

    @app.post("/api/v1/evaluations/run")
    def run_evaluation(body: dict[str, Any]):
        payload = dict(body)
        live = payload.pop("live", False)
        actor = payload.pop("actor", None)
        if not isinstance(live, bool):
            raise ServiceError(INVALID_REQUEST, "Invalid evaluation set", status=422,
                               details={"errors": [{"loc": ["live"], "msg": "Expected a boolean"}]})
        return services.evaluation.run(payload, live=live, actor=actor)

    # --- evaluation runs, baselines and comparison (EVA-T02) ----------------

    @app.get("/api/v1/evaluations/runs")
    def list_evaluation_runs(set_name: str | None = None, limit: int = 100):
        return services.evaluation.list_runs(set_name=set_name, limit=limit)

    @app.get("/api/v1/evaluations/runs/{run_id}")
    def get_evaluation_run(run_id: str):
        return services.evaluation.get_run(run_id)

    @app.put("/api/v1/evaluations/baselines/{set_name}")
    def set_evaluation_baseline(set_name: str, body: dict[str, Any]):
        return services.evaluation.set_baseline(set_name, body.get("run_id"), body.get("actor"))

    @app.get("/api/v1/evaluations/baselines/{set_name}")
    def get_evaluation_baseline(set_name: str):
        return services.evaluation.get_baseline(set_name)

    @app.post("/api/v1/evaluations/compare")
    def compare_evaluations(body: dict[str, Any]):
        return services.evaluation.compare(body.get("candidate_id"), body.get("base_id"), body.get("threshold"))

    # --- runs -------------------------------------------------------------------

    @app.post("/api/v1/runs", status_code=202)
    def create_run(request: Union[RunRequest, SocialPostRequest], bundle: str | None = None):
        """A generic ``RunRequest`` (optionally for a bundle) or the social-post request."""
        if bundle is not None and not isinstance(request, RunRequest):
            raise ServiceError(INVALID_REQUEST, "Bundles run generic requests with a workflow_id", status=422)
        if isinstance(request, RunRequest):
            from .services.limits import require_cloud_confirmation
            catalog = services.system.catalog if bundle is None else services.definitions._bundle(bundle).catalog
            require_cloud_confirmation(request, catalog)
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

    @app.get("/api/v1/runs/{run_id}/report")
    def run_report(run_id: str, format: str = "json", include_raw: bool = False):
        """Run report with the same read access as the run endpoint."""
        from fastapi.responses import PlainTextResponse

        result = services.runs.report(run_id, format=format, include_raw=include_raw)
        return PlainTextResponse(result, media_type="text/markdown") if format == "markdown" else result

    @app.get("/api/v1/runs/{run_id}/document.md")
    def run_document(run_id: str):
        """The result of a completed run as Markdown, to save as a file (ARR3-T06). 409 ``invalid_state`` until then."""
        from fastapi.responses import PlainTextResponse

        document = services.runs.document(run_id)
        return PlainTextResponse(document["markdown"], media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{document["filename"]}"'})

    @app.get("/api/v1/runs/{run_id}/steps/{step_id}/attempts")
    def step_attempts(run_id: str, step_id: str):
        """Completed attempts of one step with producer, input, output, review and tokens."""
        return services.runs.step_attempts(run_id, step_id)

    @app.get("/api/v1/runs/{run_id}/steps/{step_id}/attempts/compare")
    def compare_step_attempts(run_id: str, step_id: str, a: int = Query(..., ge=1), b: int = Query(..., ge=1)):
        """Compare two completed attempts of one step field by field."""
        return services.runs.compare_attempts(run_id, step_id, a, b)

    @app.get("/api/v1/usage/costs")
    def usage_costs(since: str | None = None, until: str | None = None, limit: int = 100):
        """Estimated cost of the framework's provider calls per run, model, day and month, each with its price evidence.

        Window ``[since, until)`` in ISO-8601 (default: the last 31 days); an unknown cost is counted apart, never as 0.
        The plan usage and the apps' own tokens are private and only served under ``/gui/api/usage`` with the token.
        """
        return services.usage.costs(since=since, until=until, limit=limit)

    @app.post("/api/v1/runs/{run_id}/approve")
    def approve(run_id: str):
        # No token here: this route never approves a budget cut (it spends money) nor a tool call (``person_required``).
        # The GUI route does; it takes the permission_request_id it showed.
        return services.runs.approve(run_id, recorded_only=True, channel="api")

    @app.post("/api/v1/runs/{run_id}/changes")
    def changes(run_id: str, review: HumanReviewRequest):
        return services.runs.request_changes(run_id, review.feedback, comments=review.comments, channel="api")

    @app.post("/api/v1/runs/{run_id}/reject")
    def reject(run_id: str, review: FeedbackRequest):
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

    @app.post("/api/v1/runs/{run_id}/human-check")
    def human_check(run_id: str, body: HumanCheckBody):
        return services.runs.human_check(run_id, body.raw_response)

    @app.post("/api/v1/runs/{run_id}/research-submit")
    def research_submit(run_id: str, submission: ResearchSubmission):
        return services.runs.human_submit(run_id, submission)

    # --- external step output (HIL2-T01) -------------------------------------

    @app.post("/api/v1/runs/{run_id}/external-output/preview")
    def external_output_preview(run_id: str, body: ExternalPreviewBody):
        return services.runs.external_output_preview(
            run_id, body.output, evidence=[item.model_dump() for item in body.evidence],
            actor=body.actor, channel=body.channel)

    @app.post("/api/v1/runs/{run_id}/external-output/confirm")
    def external_output_confirm(run_id: str, body: ExternalConfirmBody):
        return services.runs.external_output_confirm(
            run_id, body.preview_id, actor=body.actor, channel=body.channel)

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
    def list_tasks(group: str | None = None, status: str | None = None, assignee: str | None = None,
                   waiting_for: str | None = Query(None, pattern="^(user|agent|task|quota|window|all)$")):
        tasks = services.tasks.list_tasks(group_id=group, status=status, assignee=assignee)
        return tasks if waiting_for is None else [task for task in tasks if task.get("waiting") and
                                                  (waiting_for == "all" or task["waiting"]["who"] == waiting_for)]

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

    @app.put("/api/v1/tasks/{task_id}/waiting")
    def set_task_wait(task_id: str, body: TaskWaitSet):
        return services.tasks.set_wait(task_id, **body.model_dump())

    @app.post("/api/v1/tasks/{task_id}/waiting/clear")
    def clear_task_wait(task_id: str, body: TaskWaitClear):
        return services.tasks.clear_wait(task_id, **body.model_dump())

    @app.post("/api/v1/tasks/{task_id}/waiting/decision")
    def resolve_task_wait(task_id: str, body: TaskWaitDecision):
        return services.tasks.resolve_wait(task_id, **body.model_dump())

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
                    project: str | None = None, limit: int = Query(500, ge=1, le=1000)):
        """Team events with ``seq`` greater than ``after``, oldest first; ``project`` keeps those of that project."""
        return services.tasks.events(after=after, group_id=group, task_id=task, limit=limit, project_id=project)

    # --- task plans ---------------------------------------------------------------
    @app.post("/api/v1/plans/status")
    def plan_status(body: PlanStatusRequest):
        return services.plans.status(body.document)

    @app.post("/api/v1/plans/advance")
    def plan_advance(body: PlanAdvanceRequest):
        return services.plans.advance(body.document, actor=body.actor)

    # --- plan proposals ------------------------------------------------------------
    @app.get("/api/v1/planners")
    def plan_planners():
        return services.planning.planners()

    @app.post("/api/v1/plans/propose")
    def plan_propose(body: PlanProposeRequest):
        return services.planning.propose(body.planner, body.request, confirm_cloud=body.confirm_cloud)

    # --- saved plan proposals (S8.D2) --------------------------------------------
    @app.post("/api/v1/plan-proposals", status_code=201)
    def save_plan_proposal(body: PlanProposalSave):
        return services.plan_proposals.save(body.proposal, actor=body.actor, channel=body.channel, note=body.note)

    @app.get("/api/v1/plan-proposals")
    def list_plan_proposals(group_id: str | None = None, status: str | None = None):
        return services.plan_proposals.list(group_id=group_id, status=status)

    @app.get("/api/v1/plan-proposals/{proposal_id}")
    def get_plan_proposal(proposal_id: str):
        return services.plan_proposals.get(proposal_id)

    @app.post("/api/v1/plan-proposals/{proposal_id}/edit")
    def edit_plan_proposal(proposal_id: str, body: PlanProposalEdit):
        return services.plan_proposals.edit(proposal_id, body.tasks, actor=body.actor,
                                            channel=body.channel, note=body.note,
                                            expected_revision=body.expected_revision)

    @app.post("/api/v1/plan-proposals/{proposal_id}/reassign")
    def reassign_plan_proposal(proposal_id: str, body: PlanProposalReassign):
        return services.plan_proposals.reassign(proposal_id, body.task_id, actor=body.actor,
                                                channel=body.channel, note=body.note,
                                                expected_revision=body.expected_revision,
                                                preferred=body.preferred, workflow=body.workflow,
                                                bundle=body.bundle)

    @app.post("/api/v1/plan-proposals/{proposal_id}/approve")
    def approve_plan_proposal(proposal_id: str, body: PlanProposalApprove):
        return services.plan_proposals.approve(proposal_id, actor=body.actor, channel=body.channel,
                                               note=body.note, expected_revision=body.expected_revision)

    @app.post("/api/v1/plan-proposals/{proposal_id}/reject")
    def reject_plan_proposal(proposal_id: str, body: PlanProposalReject):
        return services.plan_proposals.reject(proposal_id, actor=body.actor, channel=body.channel,
                                              note=body.note)

    @app.post("/api/v1/plan-proposals/{proposal_id}/advance")
    def advance_plan_proposal(proposal_id: str, body: PlanProposalAdvance):
        return services.plan_proposals.advance(proposal_id, actor=body.actor, channel=body.channel)

    # --- projects (PRY) --------------------------------------------------------------

    @app.get("/api/v1/task-labels")
    def task_labels():
        """The shared map of task and stage labels (text, tone and explanation)."""
        return services.projects.labels()

    @app.get("/api/v1/projects")
    def list_projects(archived: bool = False):
        return services.projects.list(archived=archived)

    @app.post("/api/v1/projects/plan")
    def plan_project(body: ProjectPlanRequest):
        """Validate a plan or template without writing; the answer holds the ``plan_sha256`` that creating needs."""
        return services.projects.plan(body.doc, key=body.key, title=body.title, visibility=body.visibility)

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: ProjectCreateRequest):
        return services.projects.create(body.doc, key=body.key, title=body.title, visibility=body.visibility,
                                        plan_sha256=body.plan_sha256, actor=body.actor)

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: str):
        return services.projects.get(project_id)

    @app.patch("/api/v1/projects/{project_id}")
    def update_project(project_id: str, body: ProjectUpdateRequest):
        """Title and visibility. ``actor`` is only a claim here (no token): a project may be made ``private`` but a
        private one is not made ``normal`` (``person_required``): that is ``/gui/api/projects/{id}/visibility``."""
        return services.projects.update(project_id, actor=body.actor, expected_revision=body.expected_revision,
                                        title=body.title, visibility=body.visibility, may_lift_private=False)

    @app.post("/api/v1/projects/{project_id}/archive")
    def archive_project(project_id: str, body: ProjectRevisionRequest):
        return services.projects.archive(project_id, actor=body.actor, expected_revision=body.expected_revision)

    @app.post("/api/v1/projects/{project_id}/unarchive")
    def unarchive_project(project_id: str, body: ProjectRevisionRequest):
        return services.projects.unarchive(project_id, actor=body.actor, expected_revision=body.expected_revision)

    @app.post("/api/v1/projects/{project_id}/stages")
    def attach_project_stage(project_id: str, body: ProjectStageAttach):
        return services.projects.attach_stage(project_id, body.group_id, actor=body.actor,
                                              expected_revision=body.expected_revision)

    @app.put("/api/v1/projects/{project_id}/stages")
    def reorder_project_stages(project_id: str, body: ProjectStageOrder):
        return services.projects.reorder(project_id, body.group_ids, actor=body.actor,
                                         expected_revision=body.expected_revision)

    @app.post("/api/v1/projects/{project_id}/stages/{group_id}/close")
    def close_project_stage(project_id: str, group_id: str, body: ProjectRevisionRequest):
        """``expected_revision`` is the stage's revision. Records the delivery of accepted tasks from their results."""
        return services.projects.close_stage(project_id, group_id, actor=body.actor,
                                             expected_revision=body.expected_revision)

    # --- activities (PRY-T09, amendment 1 of the PRY contract) -----------------------

    @app.post("/api/v1/projects/{project_id}/activities", status_code=201)
    def start_activity(project_id: str, body: ActivityStart, response: Response):
        """Start an activity of the project. 201 for a new one, 200 when ``idempotency_key`` found an earlier one."""
        started = services.activities.start(project_id, body.title, actor=body.actor, executor=body.executor,
                                            idempotency_key=body.idempotency_key, objective=body.objective)
        response.status_code = 201 if started["created"] else 200
        return started

    @app.get("/api/v1/projects/{project_id}/tasks")
    def list_project_tasks(project_id: str, owner: str = "", state: str = "", due_from: str = "", due_to: str = ""):
        return services.projects.task_list(project_id, owner=owner, state=state, due_from=due_from, due_to=due_to)

    @app.get("/api/v1/projects/{project_id}/activities")
    def list_activities(project_id: str, status: str | None = None, since: str | None = None):
        return services.activities.list(project_id, status=status, since=since)

    @app.post("/api/v1/activities/{task_id}/close")
    def close_activity(task_id: str, body: ActivityClose):
        return services.activities.close(task_id, body.summary, actor=body.actor)

    # --- project memory (S12.A1) ---------------------------------------------------

    @app.post("/api/v1/projects/{project_id}/memory", status_code=201)
    def create_memory(project_id: str, body: MemoryCreate):
        """Write a memory entry (a fact, decision or convention) with its author as provenance.

        A ``user:`` author is stored ``untrusted`` and the answer carries ``pending_approval`` and
        ``details.reason = "user_confirmation_pending"``: a person confirms it in the GUI (MEM-T01)."""
        return services.memory.create(project_id, kind=body.kind, title=body.title, body=body.body, actor=body.actor,
                                      confidence=body.confidence, source_ref=body.source_ref)

    @app.get("/api/v1/projects/{project_id}/memory")
    def list_memory(project_id: str, kind: str | None = None):
        return services.memory.list(project_id, kind=kind)

    @app.get("/api/v1/memory/{entry_id}")
    def get_memory(entry_id: str):
        return services.memory.get(entry_id)

    @app.delete("/api/v1/memory/{entry_id}")
    def delete_memory(entry_id: str, actor: str = Query(...)):
        """Only a ``user:`` actor may ask to delete an entry; it goes when a person approves ``pending_approval`` in the GUI."""
        return services.memory.delete(entry_id, actor=actor)

    # --- decision register (S13.B2) -----------------------------------------------

    @app.post("/api/v1/decisions", status_code=201)
    def propose_decision(body: DecisionPropose):
        return services.decisions.propose(**body.model_dump())

    @app.get("/api/v1/decisions")
    def list_decisions(active: bool = False, project: str | None = None, run_id: str | None = None):
        return services.decisions.list(active=active, project=project, run_id=run_id)

    @app.get("/api/v1/decisions/{decision_id}")
    def get_decision(decision_id: str):
        return services.decisions.get(decision_id)

    @app.post("/api/v1/decisions/{decision_id}/accept")
    def accept_decision(decision_id: str, body: DecisionResolution):
        return services.decisions.accept(decision_id, **body.model_dump())

    @app.post("/api/v1/decisions/{decision_id}/reject")
    def reject_decision(decision_id: str, body: DecisionResolution):
        return services.decisions.reject(decision_id, actor=body.actor, outcome=body.outcome)

    @app.post("/api/v1/decisions/{decision_id}/supersede")
    def supersede_decision(decision_id: str, body: DecisionSupersede):
        return services.decisions.supersede(decision_id, **body.model_dump())

    # --- teams (EQP-T03) ------------------------------------------------------

    @app.post("/api/v1/teams/{bundle}/versions", status_code=201)
    def propose_team_version(bundle: str, body: TeamPropose):
        return services.teams.propose(bundle, actor=body.actor, channel=body.channel, draft=body.draft)

    @app.get("/api/v1/teams/{bundle}/versions")
    def list_team_versions(bundle: str, status: str | None = None):
        return services.teams.list(bundle, status=status)

    @app.get("/api/v1/teams/versions/{version_id}")
    def get_team_version(version_id: str):
        return services.teams.get(version_id)

    @app.post("/api/v1/teams/versions/{version_id}/submit")
    def submit_team_version(version_id: str, body: TeamSubmit):
        return services.teams.submit(version_id, body.actor, channel=body.channel)

    @app.post("/api/v1/teams/versions/{version_id}/approve")
    def approve_team_version(version_id: str, body: TeamDecision):
        return services.teams.approve(version_id, actor=body.actor, channel=body.channel, note=body.note or "")

    @app.post("/api/v1/teams/versions/{version_id}/reject")
    def reject_team_version(version_id: str, body: TeamDecision):
        return services.teams.reject(version_id, actor=body.actor, channel=body.channel, note=body.note or "")

    @app.get("/api/v1/teams/{bundle}/approved")
    def get_approved_team(bundle: str):
        return services.teams.approved(bundle)

    # --- team org chart (EQP-T04) ---------------------------------------------

    @app.get("/api/v1/teams/{bundle}/org-chart")
    def get_team_org_chart(bundle: str, version_id: str | None = None):
        return services.teams.org_chart(bundle, version_id=version_id)

    @app.post("/api/v1/tasks/{task_id}/sessions")
    def link_task_session(task_id: str, body: TaskSessionLink):
        return services.projects.link_session(task_id, body.session_id, actor=body.actor)

    @app.delete("/api/v1/tasks/{task_id}/sessions")
    def unlink_task_session(task_id: str, session_id: str = Query(...), actor: str = Query(...)):
        return services.projects.unlink_session(task_id, session_id, actor=actor)

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
    async def run_executor_task(body: ExecutorRunRequest, request: Request, response: Response):
        """Launch a task in an executor application.

        Without the availability policy it waits for the attempt, so it can take minutes. With the policy on (AVA-T02)
        the attempt runs in the background and this waits at most ``max_wait_seconds``: an attempt that ended answers 200
        as always, one that is still going answers 202 with ``outcome`` ``running`` (its heartbeat and end are team events).
        """
        operator_context = operator_context_provider(request) if operator_context_provider else None
        if inspect.isawaitable(operator_context):
            operator_context = await operator_context
        result = await anyio.to_thread.run_sync(lambda: services.executors.run_task(
            body.task_id, body.executor_id, body.model, body.effort, body.actor, profile_id=body.profile_id,
            timeout_seconds=body.timeout_seconds, operator_context=operator_context, step_kind=body.step_kind,
            wait=body.wait, accept_quota_warning=body.accept_quota_warning))
        if isinstance(result, dict) and result.get("outcome") == FOREGROUND_RUNNING:
            response.status_code = 202
        return result

    @app.post("/api/v1/executors/resume")
    async def resume_executor_task(body: ExecutorResumeRequest):
        """Continue the session the framework created for the task (never one opened in an app in the last 12 hours)."""
        return await anyio.to_thread.run_sync(lambda: services.executors.resume_task(
            body.task_id, body.model, body.effort, body.actor, timeout_seconds=body.timeout_seconds))

    @app.post("/api/v1/executors/resume-due")
    async def resume_due_executor_tasks(body: ExecutorResumeDueRequest):
        """Resume the tasks held by a usage limit whose time has come (CNT-T04); waits for every attempt it makes."""
        return await anyio.to_thread.run_sync(lambda: services.executors.resume_due(
            task_id=body.task_id, limit=body.limit, dry_run=body.dry_run, timeout_seconds=body.timeout_seconds))

    @app.post("/api/v1/executors/handoff")
    async def hand_off_executor_task(body: ExecutorHandoffRequest):
        """Carry out the hand-off a person recorded on the decision card (CNT-T05); waits for the delegate.

        This route has no token and ``actor`` is only a claim, so it executes nothing but a decision a person already
        recorded (the executor must be the one the hold records, else ``person_required``). Choosing the executor, any of
        them, is the card's job (``/gui/api``, with the token).
        """
        return await anyio.to_thread.run_sync(lambda: services.executors.handoff_task(
            body.task_id, body.executor_id, decision_id=body.decision_id, expected_revision=body.expected_revision,
            actor=body.actor or "user", run=body.run, timeout_seconds=body.timeout_seconds, recorded_only=True))

    @app.post("/api/v1/executors/decide-holds")
    async def decide_executor_holds(body: ExecutorDecideHoldsRequest):
        """Apply the continuity policy to the tasks held by a usage limit: wait, hand over or ask (CNT-T05)."""
        return await anyio.to_thread.run_sync(lambda: services.executors.decide_holds(
            task_id=body.task_id, limit=body.limit, dry_run=body.dry_run, run=body.run,
            timeout_seconds=body.timeout_seconds))

    @app.post("/api/v1/executors/lift-pause")
    def lift_executor_pause(body: ExecutorLiftPauseRequest):
        """Lift the pause of an executor (the only end of a credits_exhausted pause); its held tasks become due."""
        return services.executors.lift_pause(body.executor_id, actor=body.actor or "user", reason=body.reason)

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

    # --- the person's inbox (AVA-T03) -------------------------------------------------------
    # Posting only appends a message (and needs the availability policy on). The work reads it at its next step boundary,
    # in its own thread; nothing here approves, cancels or pauses anything by itself.

    def _post_message(scope: str, scope_id: str, body: InboxMessageRequest, response: Response):
        posted = services.inbox.post(scope, scope_id, message_id=body.message_id, kind=body.kind, text=body.text,
                                     actor=body.actor)
        response.status_code = 201 if posted["created"] else 200
        return posted

    @app.post("/api/v1/runs/{run_id}/messages", status_code=201)
    def post_run_message(run_id: str, body: InboxMessageRequest, response: Response):
        """Write to a run. 201 for a new message, 200 when ``message_id`` found the same message already written."""
        return _post_message("run", run_id, body, response)

    @app.get("/api/v1/runs/{run_id}/messages")
    def run_messages(run_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                     state: str | None = None):
        """The messages of a run with ``seq`` greater than ``after`` (the cursor), oldest first."""
        return services.inbox.messages("run", run_id, after=after, limit=limit, state=state)

    @app.post("/api/v1/tasks/{task_id}/messages", status_code=201)
    def post_task_message(task_id: str, body: InboxMessageRequest, response: Response):
        """Write to a task. 201 for a new message, 200 when ``message_id`` found the same message already written."""
        return _post_message("task", task_id, body, response)

    @app.get("/api/v1/tasks/{task_id}/messages")
    def task_messages(task_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                      state: str | None = None):
        """The messages of a task with ``seq`` greater than ``after`` (the cursor), oldest first."""
        return services.inbox.messages("task", task_id, after=after, limit=limit, state=state)

    @app.get("/", include_in_schema=False)
    def root():
        """The server root opens the operator GUI, the one supported local interface."""
        return RedirectResponse("/gui/")

    mount_gui(app)  # operator GUI at /gui (token-protected /gui/api, docs/GUI.md)
    return app
