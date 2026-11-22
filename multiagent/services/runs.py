# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Run services: create, inspect and steer runs.

Contract fixed by G4a-T01; implemented by G4a-T03 by delegating to
``RunService`` without duplicating its rules. Every method returns JSON-ready
data (the run dict of ``RunService`` plus ``"bundle"``, unless stated) and
raises only ``ServiceError`` for known failures: ``not_found`` for an unknown
run, ``invalid_state``/``invalid_operation``/``budget_exceeded`` when the
run's state does not allow the operation.

Runs of built-in workflows live in the system's store. A bundle engine only
knows its own workflows (``WorkflowEngine.from_bundle``), so each registered
bundle gets its own runtime, created on first use: a store under
``<data_dir>/bundles/<name>/``, an engine, a ``RunService`` and a worker that
starts when ``settings.worker_enabled`` is true. Run ids are unique, so the
per-id methods find the run in whichever store holds it.
"""
from __future__ import annotations

import threading
import json
import base64
import binascii
import os
import time
import uuid
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..artifacts import ArtifactWriter
from ..bundles import load_bundle
from ..contracts import HumanStepSubmission, SocialPostRequest
from ..db.database import Database
from ..db.store import Store
from ..errors import HumanSubmissionError, InvalidStateTransition, ValidationFailed
from ..external_output import EvidenceRejected, canonical_json, check_evidence, external_step, sha256
from ..observability.usage import read_usage, summarize_usage
from ..run_request import FunctionStepRequestError, RunRequest
from ..run_request import operating_mode_from_events
from ..run_service import PersonRequired, RunService, UserClickRequired
from ..tasks.envelopes import envelope_sha256
from ..tools.approvals import ToolApprovalMismatch
from ..tools.contracts import PermissionRequest, ToolExecutionError
from ..tools.files import preview_file_write
from ..tools.registry import ToolLookupError
from ..worker import LocalWorker
from .plan_autoadvance import notify_run_finished
from ..workflow_engine import WorkflowEngine
from .errors import HUMAN_STEP_UNAVAILABLE, HUMAN_SUBMISSION_INVALID, INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices


@dataclass
class _Runtime:
    bundle: str | None
    store: Store
    service: RunService
    worker: LocalWorker | None = None


_SHOWN_ACTIONS = 20


def _visible(text: Any) -> str:
    """Text for a person who decides on it: C0/C1 controls, bidi marks and other format, separator and private-use
    characters (categories Cc, Cf, Zl, Zp, Co) are removed, so a destination cannot disguise itself (CAP-T14 B2)."""
    return "".join(ch for ch in str(text) if unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp", "Co"))


def pending_tool_approval(run: dict[str, Any]) -> dict[str, Any] | None:
    """The permission request a run waits on (``waiting_reason`` ``tool_approval``), from its persisted events.

    Same reading as ``Store.finish_tool_approval``: the last ``tool_approval_requested`` of the waiting step and attempt
    that no later approval or rejection settled. Only summary, actions, destinations and ids come out: never raw
    arguments (the request holds none) nor model text."""
    if run.get("status") != "waiting_human" or run.get("waiting_reason") != "tool_approval":
        return None
    pending = None
    for event in run.get("events") or []:
        kind = event.get("event_type")
        if kind == "tool_approval_requested":
            try:
                candidate = PermissionRequest.model_validate(event.get("payload"))
            except Exception:
                pending = None
                continue
            pending = candidate if (candidate.context.run_id == run["id"] and candidate.context.step_id == run.get("waiting_step")
                                    and candidate.context.attempt == run.get("waiting_attempt")) else None
        elif kind in ("tool_approval_approved", "tool_approval_rejected"):
            pending = None
    if pending is None:
        return None
    omitted = max(0, len(pending.actions) - _SHOWN_ACTIONS)
    return {"permission_request_id": pending.permission_request_id, "tool_id": pending.tool_id,
            "tool_version": pending.tool_version, "step_id": pending.context.step_id,
            "attempt": pending.context.attempt, "summary": _visible(pending.summary)[:300],
            "impacts": sorted(impact.value for impact in pending.impacts),
            "actions": [{"kind": ref.kind.value, "target": _visible(ref.target) if ref.target is not None else None}
                        for ref in pending.actions[:_SHOWN_ACTIONS]],
            "actions_total": len(pending.actions),
            "actions_omitted": f"+{omitted} más (total {len(pending.actions)})" if omitted else "",
            "arguments_sha256": pending.arguments_sha256}


class RunServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._lock = threading.Lock()
        self._bundle_runtimes: dict[str, _Runtime] = {}
        self._external_now = time.time

    @property
    def _system_runtime(self) -> _Runtime:
        system = self._services.system
        return _Runtime(None, system.store, system.run_service)

    # --- runtimes -----------------------------------------------------------

    def _bundle_settings(self, name: str):
        data_dir = self._services.settings.data_dir / "bundles" / name
        return self._services.settings.model_copy(update={
            "data_dir": data_dir,
            "db_path": data_dir / "multiagent.db",
            "runs_dir": data_dir / "runs",
            "assets_dir": data_dir / "assets",
        })

    def _bundle_runtime(self, name: str, *, create: bool = True) -> _Runtime | None:
        """Runtime of a registered bundle; ``None`` if ``create`` is false and it has no store yet."""
        path = self._services.bundle_path(name)  # not_found for unknown bundles
        if not create and not self._bundle_settings(name).db_path.exists():
            return None
        self._services.bundle_scans.require_load(name)
        self._services.installed.load_functions(name)  # PAQ-T03: the functions of a trusted package, after the scan
        with self._lock:
            runtime = self._bundle_runtimes.get(name)
            if runtime is not None:
                return runtime
            settings = self._bundle_settings(name)
            if not create and not settings.db_path.exists():
                return None
            system = self._services.system
            bundle = load_bundle(path, settings)
            settings.ensure_directories()
            database = Database(settings)
            database.migrate()
            store = Store(database)
            # The document policy is the system engine's (BDI-T04): one source of truth for the screening of the
            # context, the content guard of these runs and the gates of effects (``ExecutorServices`` reads the same
            # value). It is read when the runtime is built, so it is the policy in force from then on.
            engine = WorkflowEngine.from_bundle(
                bundle, store=store, artifacts=ArtifactWriter(settings), settings=settings,
                adapters=system.adapters, dry_run=system.engine.router.dry_run,
                document_policy=system.engine.document_policy,
                tool_registry=getattr(system, "tool_registry", None),
            )
            inbox = getattr(self._services, "inbox", None)
            if inbox is not None:
                engine.availability_source = inbox.policy          # one place decides whether the inbox is in use (AVA-T03)
            usage = getattr(self._services, "usage", None)
            if usage is not None:
                usage.attach(engine)                               # the same budget guard and alerts as the system engine (USO-T04)
            engine.memory_source = self._services.memory.entries_for_project_name
            runtime = _Runtime(name, store, RunService(settings, store, engine=engine),
                               LocalWorker(
                                   store, engine, settings.worker_poll_seconds, concurrency=settings.worker_concurrency,
                                   max_active_total=settings.worker_max_active_total,
                                   max_active_per_project=settings.worker_max_active_per_project,
                                   lease_seconds=settings.worker_lease_seconds,
                                   shutdown_wait_seconds=settings.worker_shutdown_wait_seconds,
                                   on_finished=lambda finished_id: notify_run_finished(self._services, finished_id),
                               ))
            if settings.worker_enabled:
                runtime.worker.start()
            self._bundle_runtimes[name] = runtime
            return runtime

    def release_bundle(self, name: str) -> None:
        """Drop the cached runtime of a bundle (PAQ-T01) and stop its worker, as ``close`` does.

        The next use builds a new runtime from the folder the bundle has by then; that one gets the same
        ``on_finished`` hook, because ``_bundle_runtime`` builds every runtime. Stored runs are not touched.
        """
        with self._lock:
            runtime = self._bundle_runtimes.pop(name, None)
        if runtime is not None and runtime.worker is not None:
            runtime.worker.stop()

    def has_runs_in(self, name: str, statuses: tuple[str, ...]) -> bool:
        """Whether the bundle has a stored run in one of ``statuses``. Read only: it opens no runtime and gates nothing."""
        settings = self._bundle_settings(name)
        if not settings.db_path.exists():
            return False
        with self._lock:
            runtime = self._bundle_runtimes.get(name)
        store = runtime.store if runtime is not None else Store(Database(settings))
        marks = ",".join("?" for _ in statuses)
        with store.db.connect() as conn:
            return conn.execute(f"SELECT 1 FROM runs WHERE status IN ({marks}) LIMIT 1", tuple(statuses)).fetchone() is not None

    def _runtimes(self):
        """System runtime, then every bundle that has runs (opening each store on demand)."""
        yield self._system_runtime
        for name in self._services.bundle_names():
            runtime = self._bundle_runtime(name, create=False)
            if runtime is not None:
                yield runtime

    def _find(self, run_id: str) -> _Runtime:
        for runtime in self._runtimes():
            try:
                runtime.store.get_run(run_id)
            except KeyError:
                continue
            return runtime
        raise ServiceError(NOT_FOUND, f"Run not found: {run_id}", status=404)

    @staticmethod
    def _tag(runtime: _Runtime, run: dict[str, Any]) -> dict[str, Any]:
        return {**run, "bundle": runtime.bundle}

    def close(self) -> None:
        """Stop the workers of bundle runtimes (the system worker belongs to its owner)."""
        with self._lock:
            for runtime in self._bundle_runtimes.values():
                if runtime.worker is not None:
                    runtime.worker.stop()

    def _record_decision(self, runtime: _Runtime, before: dict[str, Any], action: str,
                         *, by: str | None = None) -> None:
        """Best effort audit after a successful run review; never undo the review."""
        try:
            from .projects import actor_kind
            from .memory import MAX_BODY_CHARS

            step = before.get("waiting_step") or "run"
            attempt = before.get("waiting_attempt")
            reason = before.get("waiting_reason") or "review"
            artifact = next((item for item in reversed(before.get("artifacts", []))
                             if item.get("step_id") == step and (attempt is None or item.get("attempt") == attempt)), None)
            proposed = json.dumps(artifact.get("data"), ensure_ascii=False, default=str) if artifact else (
                f"Run {before['id']} waits for {reason} at step {step}.")
            title = f"Review {step} attempt {attempt or 1}"[:120]
            actor = by if actor_kind(by) == "user" else "user"
            outcome = ("changes_requested" if action == "request_changes" else
                       f"{reason}:{action}" if reason not in {"review", "approval_pending", ""} else action)
            self._services.decisions.record_review(
                title=title, body=proposed[:MAX_BODY_CHARS], proposed_by="agent:workflow", decided_by=actor,
                accepted=action == "approve", outcome=outcome, run_id=before["id"],
                step_id=step if step != "run" else None, attempt=attempt or None,
                project=before.get("project_name"), task_id=before.get("request", {}).get("task_id"))
        except Exception as exc:
            try:
                runtime.store.db.log_event(before["id"], "decision_record_failed",
                                           {"action": action, "error": type(exc).__name__})
            except Exception:
                pass

    # --- operations ---------------------------------------------------------

    @service_errors
    def create(self, request: RunRequest | SocialPostRequest, *, bundle: str | None = None) -> dict[str, Any]:
        """Validate the workflow and queue a run; returns the new (or idempotent) run.

        ``bundle`` selects a registered bundle for ``request.workflow_id``.
        A generic request may carry ``task_id``: the task must exist in the system database (a bundle's runs live in
        another one) and the link never changes; usage and telemetry of the run are then counted for that task.
        Errors: ``not_found`` (workflow, bundle or task), ``invalid_definition``, ``idempotency_conflict``.
        """
        task_id = getattr(request, "task_id", None)
        if task_id is not None and self._services.tasks.store.get_task(task_id) is None:
            raise ServiceError(NOT_FOUND, f"Task not found: {task_id}", status=404, details={"task": task_id})
        runtime = self._system_runtime if bundle is None else self._bundle_runtime(bundle)
        # The approved team travels with the run (its runs live in the bundle's database, the versions in the
        # system's): the binding is stored in the same transaction as the run, before any worker can take it.
        team_binding = None
        approved_team = self._services.teams.approved(bundle) if bundle is not None else None
        if approved_team is not None:
            team_binding = {
                "id": approved_team["id"],
                "version_id": approved_team["id"],
                "bundle": bundle,
                "team_id": approved_team["team_id"],
                "version": approved_team["version"],
                "sha256": approved_team["sha256"],
                "team": approved_team["team"],
            }
        try:
            run = runtime.service.create_run(request, team_binding=team_binding)
        except ValueError as exc:
            if isinstance(exc, FunctionStepRequestError):
                raise
            if getattr(request, "routing_preferences", None):
                raise ServiceError(INVALID_REQUEST, str(exc), status=422) from exc
            raise
        return self._tag(runtime, run)

    @service_errors
    def get(self, run_id: str) -> dict[str, Any]:
        """Run with its events and artifacts.

        A run waiting for a tool approval also carries ``tool_approval``: the pending permission request as the persisted
        event states it, with bounded fields only (never the call's arguments, only their hash). Absent otherwise."""
        runtime = self._find(run_id)
        run = self._tag(runtime, runtime.service.get(run_id))
        run["operating_mode"] = operating_mode_from_events(run.get("events"))
        pending = pending_tool_approval(run)
        if pending is not None:
            run["tool_approval"] = pending
        return run

    @service_errors
    def list(self, *, limit: int = 20, status: str = "", project: str = "") -> list[dict[str, Any]]:
        """Most recent runs of every store, optionally filtered by status and project."""
        runs = [
            self._tag(runtime, run)
            for runtime in self._runtimes()
            for run in runtime.service.list(limit=limit, status=status, project=project)
        ]
        runs.sort(key=lambda run: run.get("created_at") or "", reverse=True)
        return runs[:max(1, min(int(limit), 500))]

    @service_errors
    def approve(self, run_id: str, *, by: str | None = None, channel: str | None = None,
                recorded_only: bool = False, permission_request_id: str | None = None,
                person: bool = False) -> dict[str, Any]:
        """Approve the artifact under review and continue or complete the run.

        A run paused on a supervised escalation (``waiting_reason`` ``escalation_approval``) has no artifact
        under review: approving it queues the run so the step resumes with the escalated model. A run held by the
        content guard (``waiting_reason`` ``content_review``, policy ``pause``) has none either: approving it settles
        the open reviews (``document_review_resolved``) and queues the run, which goes on with the quarantined
        prompt it had. A run held by the budget guard (``waiting_reason`` ``budget_cut_approval``, USO-T04) has no artifact
        either: approving it records ``budget_cut_approved`` and queues the run, and the resumed step makes that one call
        (the approval is for one call and is spent by it). ``by`` and ``channel`` (``api`` by default, ``gui`` from the GUI
        router) are recorded with that resolution as the caller declared them. Known limit (BDI-T07): this method does not
        authenticate its caller, so any local process that reaches the run API can settle a content review.

        ``recorded_only`` is for a caller nobody authenticated (``/api/v1``, which has no token): it cannot approve a
        budget cut, because that spends money. The error is ``invalid_state`` with ``details.reason`` ``person_required``
        and the run stays waiting; the token route of the GUI and the command line approve it. Rejecting needs no such
        flag: a refusal spends nothing.

        A run waiting for a tool approval (``tool_approval``, CAP-T14) is approved only by naming the
        ``permission_request_id`` the person was shown (``get`` returns it as ``tool_approval``); a missing, old or
        other id is ``invalid_state`` with ``code`` ``permission_request_mismatch`` and nothing is approved. A caller
        nobody authenticated (``recorded_only``), or one that does not pass ``person=True``, never approves it
        (``PersonRequired``, CAP-T15 B1); only the token route of the GUI passes it.

        Every approval records ``by`` and ``channel`` on its event (``human_approved``, ``tool_approval_approved``, the
        content and budget resolutions), as ``request_changes`` already did: ``by`` is what the caller declared, or the
        channel when it declared nothing, and ``channel`` is ``gui`` or ``api`` (ARR-T04). Nothing authenticates ``by``.
        """
        runtime = self._find(run_id)
        before = runtime.store.get_run(run_id)
        try:
            result = self._tag(runtime, runtime.service.approve(run_id, by=by, channel=channel, recorded_only=recorded_only,
                                                                 permission_request_id=permission_request_id,
                                                                 person=person))
            self._record_decision(runtime, before, "approve", by=by)
            notify_run_finished(self._services, run_id)   # ARR3-T03: the last approved step ends the run outside the worker
            return result
        except ToolApprovalMismatch as exc:
            raise ServiceError(INVALID_STATE, str(exc), status=409,
                               details={"reason": exc.code, "run_id": run_id}) from exc
        except PersonRequired as exc:
            raise ServiceError(INVALID_STATE, str(exc), status=409,
                               details={"reason": "person_required", "run_id": run_id}) from exc
        except UserClickRequired as exc:
            raise ServiceError(INVALID_STATE, str(exc), status=409,
                               details={"reason": "user_click_required", "run_id": run_id,
                                        "where": "Aprueba en la GUI → Aprobaciones"}) from exc

    # CHT-T04: reconstruct only the declared file.write call; never modify the run or its target file.
    @service_errors
    def tool_approval_preview(self, run_id: str, *, permission_request_id: str | None = None) -> dict[str, Any]:
        """Return the preview only when rehydrated arguments match the pending request's canonical hash.

        If tool arguments later reference earlier step outputs (C2), extend this reconstruction before enabling the
        preview for those calls; this method intentionally resolves only the current literal and run-input contract.
        """
        runtime = self._find(run_id)
        run = runtime.store.get_run(run_id)
        pending = pending_tool_approval(run)
        if pending is None:
            return {"available": False, "reason": "no_pending_approval",
                    "message": "There is no pending tool approval for this run."}
        if permission_request_id is not None and pending.get("permission_request_id") != permission_request_id:
            return {"available": False, "reason": "permission_request_mismatch",
                    "message": "The pending approval changed; refresh the run before reviewing its preview."}
        if pending.get("tool_id") != "file.write":
            return {"available": False, "reason": "unsupported_tool",
                    "message": "A preview is available only for file.write approvals."}

        root = self._services.settings.file_tools_root
        if root is None:
            return {"available": False, "reason": "file_tools_disabled",
                    "message": "File tools are not configured for this application."}
        registry = getattr(self._services.system, "tool_registry", None)
        if registry is None:
            return {"available": False, "reason": "file_write_unavailable",
                    "message": "The file.write tool is not registered."}
        try:
            registered = registry.lookup("file.write", pending.get("tool_version"))
        except ToolLookupError:
            return {"available": False, "reason": "file_write_unavailable",
                    "message": "The approved file.write tool version is no longer registered."}

        request = run.get("request")
        if not isinstance(request, Mapping):
            return {"available": False, "reason": "run_inputs_unavailable",
                    "message": "The run inputs needed to rebuild this tool call are unavailable."}
        definition = self._services.definitions.read_workflow(run["workflow_id"], bundle=runtime.bundle)
        step = next((item for item in definition.get("steps", [])
                     if item.get("id") == pending.get("step_id")), None)
        if step is None:
            return {"available": False, "reason": "declaration_unavailable",
                    "message": "The workflow no longer contains the step that requested this approval."}

        matched_arguments = None
        has_declaration = False
        has_reconstructed_arguments = False
        for entry in step.get("tools") or []:
            if not isinstance(entry, Mapping):
                continue
            declared_tool = entry.get("capability") if "capability" in entry else entry.get("tool")
            if declared_tool != pending.get("tool_id"):
                continue
            has_declaration = True
            declared_arguments = entry.get("arguments") or {}
            if not isinstance(declared_arguments, Mapping):
                continue
            try:
                arguments = WorkflowEngine._tool_arguments(declared_arguments, request)
            except ValidationFailed:
                continue
            has_reconstructed_arguments = True
            if envelope_sha256(arguments) == pending.get("arguments_sha256"):
                matched_arguments = arguments
                break

        if matched_arguments is None:
            if not has_declaration:
                return {"available": False, "reason": "declaration_unavailable",
                        "message": "The file.write call is no longer declared by this workflow step."}
            if not has_reconstructed_arguments:
                return {"available": False, "reason": "arguments_unavailable",
                        "message": "The declared file.write arguments cannot be rebuilt from this run's inputs."}
            return {"available": False, "reason": "arguments_mismatch",
                    "message": "The rebuilt file.write arguments do not match the pending approval's fingerprint."}

        try:
            preview = preview_file_write(root, matched_arguments, max_bytes=registered.spec.max_output_bytes)
        except ToolExecutionError as exc:
            return {"available": False, "reason": exc.code, "message": str(exc)}
        return {"available": True, **preview}

    @service_errors
    def request_changes(self, run_id: str, feedback: str, *, comments=None,
                        by: str | None = None, channel: str | None = None) -> dict[str, Any]:
        """Send the reviewed step back with feedback."""
        runtime = self._find(run_id)
        before = runtime.store.get_run(run_id)
        try:
            result = self._tag(runtime, runtime.service.changes(run_id, feedback, comments=comments or (),
                                                                by=by, channel=channel))
            self._record_decision(runtime, before, "request_changes", by=by)
            return result
        except ValueError as exc:
            from ..revision_comments import RevisionFeedbackTooLong
            if isinstance(exc, RevisionFeedbackTooLong):
                raise ServiceError(INVALID_REQUEST, str(exc), status=422) from exc
            raise

    @service_errors
    def regenerate(self, run_id: str) -> dict[str, Any]:
        """Repeat the reviewed step without feedback."""
        runtime = self._find(run_id)
        return self._tag(runtime, runtime.service.changes(run_id, "", regenerate=True))

    @service_errors
    def reject(self, run_id: str, feedback: str = "", *, by: str | None = None,
               channel: str | None = None) -> dict[str, Any]:
        """Reject the run while it waits for a human.

        Rejecting a paused escalation fails the step and the run (status ``failed``) with the reason. Rejecting a
        run held for a content review rejects it (status ``rejected``) and records the resolution (``by``, ``channel``
        as in ``approve``). Rejecting a run held by the budget guard fails it (status ``failed``): the call is never made.
        """
        runtime = self._find(run_id)
        before = runtime.store.get_run(run_id)
        result = self._tag(runtime, runtime.service.reject(run_id, feedback, by=by, channel=channel))
        self._record_decision(runtime, before, "reject", by=by)
        notify_run_finished(self._services, run_id)   # ARR3-T03
        return result

    @service_errors
    def cancel(self, run_id: str, *, force: bool = False) -> dict[str, Any]:
        """Cancel the run (or request cancellation if it is running and not forced)."""
        runtime = self._find(run_id)
        result = self._tag(runtime, runtime.service.cancel(run_id, force=force))
        notify_run_finished(self._services, run_id)   # ARR3-T03
        return result

    @service_errors
    def human_next(self, run_id: str) -> dict[str, Any]:
        """Pending human-guided step (prompt and expected contract).

        Error ``human_step_unavailable`` when the run has none.
        """
        runtime = self._find(run_id)
        try:
            pending = runtime.service.human_next(run_id)
        except HumanSubmissionError as exc:
            raise ServiceError(HUMAN_STEP_UNAVAILABLE, str(exc), status=409) from exc
        return {**pending, "bundle": runtime.bundle}

    @service_errors
    def human_submit(self, run_id: str, submission: HumanStepSubmission) -> dict[str, Any]:
        """Submit the response of a human or external model for the pending step.

        Error ``human_submission_invalid`` when the response is rejected.
        """
        runtime = self._find(run_id)
        self._require_human_wait(runtime, run_id)
        return self._tag(runtime, runtime.service.human_submit(run_id, submission))

    @service_errors
    def external_output_preview(self, run_id: str, output: dict[str, Any], *,
                                evidence: list[dict[str, str]] | None = None,
                                actor: str = "", channel: str = "api") -> dict[str, Any]:
        runtime = self._find(run_id)
        engine = runtime.service.engine
        if "external_output" not in getattr(engine, "capabilities", {"external_output"}):
            raise ServiceError(INVALID_STATE, "The backend does not support external output", status=409)
        try:
            run, _, _, step, _ = external_step(engine, run_id)
        except (HumanSubmissionError, InvalidStateTransition) as exc:
            raise ServiceError(INVALID_STATE, str(exc), status=409) from exc
        try:
            normalized = engine.external_output(run_id, output)
        except HumanSubmissionError as exc:
            raise ServiceError(HUMAN_SUBMISSION_INVALID, str(exc), status=422,
                               details={"problems": [{"path": "$", "message": str(exc)}]}) from exc
        settings = runtime.service.settings
        files = []
        total = 0
        decoded_files = []
        for item in evidence or []:
            name = item.get("name", "")
            encoded = item.get("content_base64", "")
            if len(encoded) > 4 * ((settings.external_evidence_max_file_bytes + 2) // 3):
                raise ServiceError("evidence_rejected", "Evidence file exceeds the size limit", status=422,
                                   details={"reason": "file_size"})
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ServiceError("evidence_rejected", "Evidence base64 is invalid", status=422,
                                   details={"reason": "invalid_base64"}) from exc
            try:
                kind = check_evidence(name, content, file_limit=settings.external_evidence_max_file_bytes)
            except EvidenceRejected as exc:
                raise ServiceError("evidence_rejected", str(exc), status=422,
                                   details={"reason": str(exc)}) from exc
            total += len(content)
            if total > settings.external_evidence_max_total_bytes:
                raise ServiceError("evidence_rejected", "Evidence total exceeds the size limit", status=422,
                                   details={"reason": "total_size"})
            filename = f"{uuid.uuid4().hex}{os.path.splitext(name)[1].lower()}"
            files.append({"name": name, "file": filename, "kind": kind,
                          "size": len(content), "sha256": sha256(content)})
            decoded_files.append((filename, content))
        preview_id = uuid.uuid4().hex
        expires_at = self._external_now() + settings.external_preview_ttl_seconds
        waiting = [run["status"], run["waiting_reason"], run["waiting_step"], run["waiting_attempt"]]
        stage = settings.data_dir / "staging" / "external" / preview_id
        stage.mkdir(parents=True, exist_ok=False)
        for filename, content in decoded_files:
            (stage / filename).write_bytes(content)
        preview = {"id": preview_id, "run_id": run_id, "step_id": step.id,
                   "attempt": run["waiting_attempt"], "waiting": waiting,
                   "output": normalized, "files": files, "actor": actor, "channel": channel,
                   "expires_at": expires_at}
        (stage / "preview.json").write_bytes(canonical_json(preview))
        return preview

    @service_errors
    def external_output_confirm(self, run_id: str, preview_id: str, *,
                                actor: str, channel: str = "api") -> dict[str, Any]:
        if not actor.startswith("user:") or len(actor) <= 5:
            raise ServiceError(INVALID_REQUEST, "Confirmation actor must start with user:", status=422)
        runtime = self._find(run_id)
        settings = runtime.service.settings
        if len(preview_id) != 32 or any(c not in "0123456789abcdef" for c in preview_id):
            raise ServiceError(NOT_FOUND, "External output preview not found", status=404)
        stage = settings.data_dir / "staging" / "external" / preview_id
        try:
            preview = json.loads((stage / "preview.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ServiceError(NOT_FOUND, "External output preview not found", status=404) from exc
        run = runtime.store.get_run(run_id)
        waiting = [run["status"], run["waiting_reason"], run["waiting_step"], run["waiting_attempt"]]
        if (preview["run_id"] != run_id or preview["waiting"] != waiting
                or self._external_now() >= preview["expires_at"] or (stage / "confirmed").exists()):
            raise ServiceError(INVALID_STATE, "External output preview is stale", status=409)
        try:
            external_step(runtime.service.engine, run_id)
        except (HumanSubmissionError, InvalidStateTransition) as exc:
            raise ServiceError(INVALID_STATE, str(exc), status=409) from exc
        contents = []
        for item in preview["files"]:
            content = (stage / item["file"]).read_bytes()
            if sha256(content) != item["sha256"]:
                raise ServiceError(INVALID_STATE, "Staged evidence changed", status=409)
            contents.append(content)
        try:
            fd = os.open(stage / "confirmed", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError as exc:
            raise ServiceError(INVALID_STATE, "External output preview was already used", status=409) from exc
        engine = runtime.service.engine
        engine.external_output(run_id, preview["output"], commit=True)
        attempt = run["waiting_attempt"] + (1 if run["waiting_reason"] == "review" else 0)
        writer = engine.artifacts
        run_dir = writer.run_dir(run_id)
        artifact_files = []
        for item, content in zip(preview["files"], contents):
            path = run_dir / f"external_{preview_id}_{item['file']}"
            path.write_bytes(content)
            relative = str(path.relative_to(settings.data_dir))
            data = {**item, "path": relative}
            aid = runtime.store.add_artifact(run_id=run_id, step_id=f"{preview['step_id']}:external:{item['file']}",
                                             attempt=attempt, kind="external_evidence", path=relative, data=data)
            artifact_files.append({**data, "artifact_id": aid})
        output_sha = sha256(canonical_json(preview["output"]))
        manifest = {"preview_id": preview_id, "step_id": preview["step_id"], "attempt": attempt,
                    "output_sha256": output_sha, "files": artifact_files}
        manifest_bytes = canonical_json(manifest)
        manifest_sha = sha256(manifest_bytes)
        manifest_path = run_dir / f"external_{preview_id}_manifest.json"
        manifest_path.write_bytes(manifest_bytes)
        manifest_id = runtime.store.add_artifact(
            run_id=run_id, step_id=f"{preview['step_id']}:external:manifest:{preview_id}",
            attempt=attempt, kind="external_manifest",
            path=str(manifest_path.relative_to(settings.data_dir)), data=manifest)
        receipt = {"id": uuid.uuid4().hex, "preview_id": preview_id, "manifest_id": manifest_id,
                   "manifest_sha256": manifest_sha, "file_sha256": {f["file"]: f["sha256"] for f in artifact_files},
                   "actor": actor, "channel": channel, "confirmed_at": self._external_now()}
        receipt_path = run_dir / f"external_{preview_id}_receipt.json"
        receipt_path.write_bytes(canonical_json(receipt))
        receipt_id = runtime.store.add_artifact(
            run_id=run_id, step_id=f"{preview['step_id']}:external:receipt:{preview_id}",
            attempt=attempt, kind="external_receipt",
            path=str(receipt_path.relative_to(settings.data_dir)), data=receipt)
        runtime.store.db.log_event(run_id, "external_output_confirmed", {
            "preview_id": preview_id, "step_id": preview["step_id"], "attempt": attempt,
            "manifest_id": manifest_id, "receipt_id": receipt_id,
            "manifest_sha256": manifest_sha, "output_sha256": output_sha,
            "file_sha256": receipt["file_sha256"]})
        return self._tag(runtime, runtime.store.get_run(run_id))

    @staticmethod
    def _require_human_wait(runtime: _Runtime, run_id: str) -> None:
        run = runtime.store.get_run(run_id)
        pending = runtime.store.pending_human_step(run_id)
        if (run["status"] != "waiting_human" or run.get("waiting_reason") not in {"execute_step", "research"}
                or run.get("cancel_requested") or not pending
                or run.get("waiting_step") != pending["step_id"]
                or run.get("waiting_attempt") != pending["attempt"]
                or runtime.store.latest_step_attempt(run_id, pending["step_id"]) != pending["attempt"]):
            raise ServiceError(INVALID_STATE, "The run is not waiting for this human step", status=409,
                               details={"reason": "not_waiting"})

    @service_errors
    def human_check(self, run_id: str, raw_response: str) -> dict[str, Any]:
        runtime = self._find(run_id)
        self._require_human_wait(runtime, run_id)
        if not callable(getattr(runtime.service.engine, "check_human_step", None)):
            raise ServiceError(INVALID_STATE, "The backend does not support human checks", status=409,
                               details={"reason": "unsupported"})
        return runtime.service.human_check(run_id, raw_response)

    @service_errors
    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        """Artifacts of the run, oldest first."""
        return self.get(run_id)["artifacts"]

    @service_errors
    def events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        """Events of the run whose ``seq`` is greater than ``after``, oldest first.

        ``seq`` numbers a run's events 1, 2, 3... without gaps, so a client that
        sees a gap, a repeat, or a lower number than it already has knows its
        copy is stale and reloads the run.
        Errors: ``not_found``, ``invalid_request`` (``after`` is not an integer >= 0).
        """
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ServiceError(INVALID_REQUEST, "after must be an integer >= 0", status=422,
                               details={"after": repr(after)})
        return [event for event in self.get(run_id)["events"] if event["seq"] > after]

    @service_errors
    def usage(self, run_id: str) -> dict[str, Any]:
        """Token usage of the run: every record plus totals per run, agent, step and model.

        Tokens a provider did not report stay ``None`` and are counted in
        ``records_without_usage`` instead of adding 0. Costs are estimates
        (``cost_is_estimate``), never billed amounts: tokens x the catalog price
        evidence in force at the call, or ``None`` when either is missing. Totals
        keep unknown costs apart (``estimated_cost_usd`` is the known subtotal,
        ``cost_unknown_records`` how many calls are missing from it,
        ``cost_complete`` whether it is the whole cost). Error: ``not_found``.
        """
        runtime = self._find(run_id)
        records = read_usage(runtime.store, run_id)
        return {
            "run_id": run_id,
            "bundle": runtime.bundle,
            "cost_is_estimate": True,
            "records": records,
            "totals": summarize_usage(records),
        }

    @service_errors
    def step_attempts(self, run_id: str, step_id: str) -> dict[str, Any]:
        """Completed attempts of one step with producer, input, output, review and tokens."""
        from .attempts import build_step_attempts

        runtime = self._find(run_id)
        return build_step_attempts(runtime, self._services.definitions, run_id, step_id)

    @service_errors
    def compare_attempts(self, run_id: str, step_id: str, a: int, b: int) -> dict[str, Any]:
        """Compare two completed attempts ``a`` and ``b`` of one step."""
        from .attempts import build_attempt_comparison

        runtime = self._find(run_id)
        return build_attempt_comparison(runtime, self._services.definitions, run_id, step_id, a, b)

    @service_errors
    def report(self, run_id: str, *, format: str = "json", include_raw: bool = False) -> dict[str, Any] | str:
        """Export a run as JSON-ready data or Markdown. Raw payloads require explicit opt-in."""
        from .reports import build_run_report, render_run_report_markdown

        if format not in ("json", "markdown"):
            raise ServiceError(INVALID_REQUEST, "format must be json or markdown", status=422)
        report = build_run_report(self.get(run_id), self.usage(run_id), include_raw=include_raw)
        return report if format == "json" else render_run_report_markdown(report)
