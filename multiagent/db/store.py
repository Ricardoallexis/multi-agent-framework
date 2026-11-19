from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .database import Database, utcnow
from ..contracts import (
    AssetMetadata,
    BrandProfilePayload,
    MetricSnapshotInput,
    PublicationStatus,
    RunStatus,
    SocialPostRequest,
    WaitingReason,
)
from ..budget_cut import (
    EVENT_APPROVED as BUDGET_CUT_APPROVED, EVENT_REJECTED as BUDGET_CUT_REJECTED, approval_from_pause, is_budget_pause,
    rejection_message,
)
from ..egress_guard import (
    EVENT_APPROVED as EGRESS_APPROVED, EVENT_REJECTED as EGRESS_REJECTED,
    approval_from_pause as egress_approval_from_pause, is_egress_pause,
)
from ..document_review import (
    DECISION_APPROVED, DECISION_REJECTED, resolution_payload, resolution_source, review_key, unresolved_requests,
)
from ..document_trust import EVENT_REVIEW_REQUESTED, EVENT_REVIEW_RESOLVED
from ..errors import IdempotencyConflict, InvalidStateTransition
from ..run_request import RunRequest
from ..tools import approvals as _tool_approvals
from ..tools.approvals import (APPROVAL_TTL as TOOL_APPROVAL_TTL, EVENT_APPROVED as TOOL_APPROVED,
                               EVENT_REJECTED as TOOL_REJECTED, EVENT_RELEASED as TOOL_RELEASED,
                               EVENT_REQUESTED as TOOL_REQUESTED, ToolApprovalMismatch, _approval_from_decision)
from ..tools.contracts import PermissionRequest
from datetime import timedelta, timezone


def _canonical_request_fingerprint(req: SocialPostRequest | RunRequest) -> str:
    payload = req.model_dump(mode="json")
    payload.pop("idempotency_key", None)
    if payload.get("task_id") is None:
        payload.pop("task_id", None)   # a run without a task keeps the fingerprint it had before the field existed
    if not payload.get("routing_preferences"):
        payload.pop("routing_preferences", None)  # keep existing idempotency fingerprints
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, db: Database):
        self.db = db

    def hold_tool_approval(self, permission: PermissionRequest) -> None:
        """Host bridge for an invoker approval_required result; no decision is made here."""
        if permission.context.scope != "run_step" or not permission.context.run_id or not permission.context.step_id:
            raise InvalidStateTransition("Tool approval requires a run step")
        run_id = permission.context.run_id
        with self.db.tx() as conn:
            fresh = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if (fresh is None or fresh["status"] != RunStatus.RUNNING.value or fresh["cancel_requested"]):
                raise InvalidStateTransition("The run is not executing this tool step")
            now = utcnow()
            conn.execute("""UPDATE runs SET status=?,waiting_reason=?,waiting_step=?,waiting_attempt=?,updated_at=?
                            WHERE id=?""", (RunStatus.WAITING_HUMAN.value, WaitingReason.TOOL_APPROVAL.value,
                                           permission.context.step_id, permission.context.attempt, now, run_id))
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run_id, now, TOOL_REQUESTED, permission.model_dump_json()))

    def finish_tool_approval(self, run: dict[str, Any], *, approve: bool, feedback: str = "",
                             by: str | None = None, channel: str | None = None,
                             permission_request_id: str | None = None) -> None:
        """Atomically settle exactly the pending tool request."""
        run_id = run["id"]
        with self.db.tx() as conn:
            fresh = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            fields = ("status", "current_step", "waiting_reason", "waiting_step", "waiting_attempt", "cancel_requested")
            if (fresh is None or fresh["status"] != RunStatus.WAITING_HUMAN.value
                    or fresh["waiting_reason"] != WaitingReason.TOOL_APPROVAL.value or fresh["cancel_requested"]
                    or any(fresh[key] != run[key] for key in fields)):
                raise InvalidStateTransition("The persisted tool approval changed")
            pending = None
            for row in conn.execute("SELECT event_type,payload_json FROM run_events WHERE run_id=? "
                                    "AND event_type IN (?,?,?) ORDER BY id", (run_id, TOOL_REQUESTED,
                                    TOOL_APPROVED, TOOL_REJECTED)):
                if row["event_type"] == TOOL_REQUESTED:
                    try:
                        candidate = PermissionRequest.model_validate_json(row["payload_json"])
                    except Exception:
                        pending = None
                        continue
                    pending = candidate if (candidate.context.step_id == fresh["waiting_step"] and
                                            candidate.context.attempt == fresh["waiting_attempt"] and
                                            candidate.context.run_id == run_id) else None
                else:
                    pending = None
            if approve:
                if pending is None:
                    raise ToolApprovalMismatch("No pending tool approval", code="no_pending_tool_approval")
                if permission_request_id is None or permission_request_id != pending.permission_request_id:
                    raise ToolApprovalMismatch("The approval does not name the pending tool request",
                                               code="permission_request_mismatch")
            # Rejecting never releases anything, so it works even when the request event is missing or unreadable.
            now = utcnow()
            declared, door = resolution_source(by, channel)
            payload = {"permission_request": pending.model_dump(mode="json") if pending is not None else None,
                       "by": declared, "channel": door, "feedback": feedback}
            if approve:
                # The clock is the approvals module's ``_now`` (injectable); the grant lives ten minutes from here.
                approved_at = _tool_approvals._now()
                payload["approved_at"] = approved_at.isoformat()
                payload["expires_at"] = (approved_at + TOOL_APPROVAL_TTL).isoformat()
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run_id, now, TOOL_APPROVED if approve else TOOL_REJECTED,
                          json.dumps(payload, ensure_ascii=False)))
            if approve:
                conn.execute("UPDATE runs SET status=?,waiting_reason='',waiting_step='',waiting_attempt=NULL,updated_at=? "
                             "WHERE id=?", (RunStatus.QUEUED.value, now, run_id))
            else:
                conn.execute("UPDATE runs SET status=?,error=?,revision_feedback=?,waiting_reason='',waiting_step='',"
                             "waiting_attempt=NULL,updated_at=? WHERE id=?",
                             (RunStatus.FAILED.value, "Tool approval rejected", feedback, now, run_id))
                conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                             (run_id, now, "workflow_failed",
                              json.dumps({"error": "Tool approval rejected", "type": "ToolApprovalRejected",
                                          "code": "denied"}, ensure_ascii=False)))

    @staticmethod
    def _same_request(stored: Any, wanted: dict[str, Any]) -> bool:
        if not isinstance(stored, dict):
            return False
        return {k: v for k, v in stored.items() if k not in ("reason", "summary")} == wanted

    def take_tool_approval(self, permission: PermissionRequest):
        """Release a human grant once for the exact permission request; never infer one from request text."""
        run_id = permission.context.run_id
        if not run_id:
            return None
        with self.db.tx() as conn:
            fresh = conn.execute("SELECT status,cancel_requested FROM runs WHERE id=?", (run_id,)).fetchone()
            if fresh is None or fresh["cancel_requested"] or fresh["status"] != RunStatus.RUNNING.value:
                return None
            # The binding ignores the wording (reason, summary): the same call asked again with other evidence is the same call.
            wanted = permission.model_dump(mode="json", exclude={"reason", "summary"})
            rows = conn.execute("SELECT event_type,payload_json FROM run_events WHERE run_id=? AND event_type IN (?,?) "
                                "ORDER BY id", (run_id, TOOL_APPROVED, TOOL_RELEASED)).fetchall()
            approved = None
            for row in rows:
                payload = json.loads(row["payload_json"])
                if row["event_type"] == TOOL_RELEASED and payload.get("permission_request_id") == permission.permission_request_id:
                    approved = None
                elif row["event_type"] == TOOL_APPROVED and self._same_request(payload.get("permission_request"), wanted):
                    approved = payload
            if approved is None:
                return None
            try:
                expiry = datetime.fromisoformat(approved["expires_at"])
                approved_at = datetime.fromisoformat(approved["approved_at"])
            except (KeyError, TypeError, ValueError):
                return None
            now_dt = _tool_approvals._now()
            if (expiry.tzinfo is None or approved_at.tzinfo is None or now_dt >= expiry or now_dt < approved_at
                    or expiry - now_dt > TOOL_APPROVAL_TTL):
                return None
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run_id, utcnow(), TOOL_RELEASED,
                          json.dumps({"permission_request_id": permission.permission_request_id})))
            return _approval_from_decision(PermissionRequest.model_validate(approved["permission_request"]), expiry, approved_at)

    # -------- projects / runs --------
    def get_or_create_project(self, name: str) -> str:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
            if row:
                return row["id"]
            pid = uuid.uuid4().hex
            conn.execute("INSERT INTO projects(id,name,created_at,title) VALUES(?,?,?,?)", (pid, name, utcnow(), name))
            conn.commit()
            return pid

    @staticmethod
    def workflow_id_for(req: SocialPostRequest | RunRequest) -> str:
        if isinstance(req, RunRequest):
            return req.workflow_id
        return "social_post_full" if req.pipeline_mode.value == "full" else "social_post"

    def find_idempotent_run(self, req: SocialPostRequest | RunRequest) -> dict[str, Any] | None:
        """Resolve an existing request before preflight, without creating a run."""
        fingerprint = _canonical_request_fingerprint(req)
        if req.idempotency_key:
            with self.db.connect() as conn:
                row = conn.execute(
                    "SELECT id,request_fingerprint FROM runs WHERE idempotency_key=?",
                    (req.idempotency_key,),
                ).fetchone()
                if row:
                    if (row["request_fingerprint"] or "") == fingerprint:
                        self.db.log_event(row["id"], "idempotency_hit", {"request_fingerprint": fingerprint})
                        return self.get_run(row["id"])
                    self.db.log_event(row["id"], "idempotency_conflict", {"request_fingerprint": fingerprint})
                    raise IdempotencyConflict(
                        "The idempotency_key already exists for a different request"
                    )

        return None

    def create_run(self, req: SocialPostRequest | RunRequest, max_llm_calls: int, max_run_seconds: int, *,
                   events: tuple[tuple[str, dict[str, Any]], ...] = ()) -> dict[str, Any]:
        """Persist a queued run. ``workflow_started`` and then ``events`` (``(event_type, payload)``, such as the
        ``team_version_bound`` of a bundle run) are stored in the same transaction as the run, so a worker never
        claims the run without them. An idempotent replay returns the existing run and stores nothing."""
        existing = self.find_idempotent_run(req)
        if existing is not None:
            return existing
        fingerprint = _canonical_request_fingerprint(req)
        run_id = uuid.uuid4().hex
        project_id = self.get_or_create_project(req.project_name)
        social = isinstance(req, SocialPostRequest)
        use_brand = social and req.use_brand_context
        brand_version = self.active_brand_version() if use_brand else None
        brand_profile_id = (self.active_brand_id() or "") if use_brand else ""
        now = utcnow()
        workflow_id = self.workflow_id_for(req)
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO runs(
                    id,project_id,workflow_id,status,request_json,brand_version,current_step,revision_feedback,
                    cancel_requested,idempotency_key,max_llm_calls,max_run_seconds,llm_calls,
                    created_at,updated_at,error,brand_profile_id,execution_mode,research_mode,pipeline_mode,step_modes_json,
                    request_fingerprint,waiting_reason,waiting_step,waiting_attempt,worker_id,started_at,
                    heartbeat_at,active_seconds,budget_exhausted,successful_llm_calls,task_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, project_id, workflow_id, RunStatus.QUEUED.value,
                    req.model_dump_json(), brand_version, 0, "", 0, req.idempotency_key or None,
                    max_llm_calls, max_run_seconds, 0, now, now, "", brand_profile_id,
                    req.execution_mode.value, req.research_mode.value if social else "none",
                    req.pipeline_mode.value if social else "custom",
                    json.dumps({k: v.value for k, v in req.step_modes.items()}, ensure_ascii=False),
                    fingerprint, "", "", None, "", "", "", 0.0, 0, 0,
                    getattr(req, "task_id", None),
                ),
            )
            notices = []
            for event_type, payload in (("workflow_started", {"workflow_id": workflow_id}), *events):
                cursor = conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                                      (run_id, utcnow(), event_type, json.dumps(payload, ensure_ascii=False)))
                notices.append((run_id, event_type, payload, cursor.lastrowid))
            conn.commit()
        for notice in notices:
            for listener in tuple(self.db.event_listeners):
                try:
                    listener(*notice)
                except Exception:
                    pass
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(f"Run not found: {run_id}")
            result = dict(row)
            result["request"] = json.loads(result.pop("request_json"))
            result["step_modes"] = json.loads(result.pop("step_modes_json", "{}") or "{}")
            artifacts: list[dict[str, Any]] = []
            for arow in conn.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at", (run_id,)):
                item = dict(arow)
                item["data"] = json.loads(item.pop("data_json"))
                artifacts.append(item)
            events: list[dict[str, Any]] = []
            # Events are append-only, so their position is a stable per-run sequence: 1, 2, 3...
            rows = conn.execute("SELECT * FROM run_events WHERE run_id=? ORDER BY id", (run_id,))
            for seq, erow in enumerate(rows, start=1):
                item = dict(erow)
                item["payload"] = json.loads(item.pop("payload_json"))
                item["seq"] = seq
                events.append(item)
            result["artifacts"] = artifacts
            result["events"] = events
            result["attention"] = self._approval_attention(result, events)
            pending = conn.execute(
                "SELECT * FROM human_step_requests WHERE run_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            result["human_step_request"] = dict(pending) if pending else None
            return result

    def list_runs(self, *, limit: int = 20, status: str = "", project: str = "") -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("r.status=?")
            params.append(status)
        if project:
            clauses.append("p.name=?")
            params.append(project)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""SELECT r.id,r.workflow_id,r.status,r.current_step,r.brand_version,r.llm_calls,
                           r.max_llm_calls,r.waiting_reason,r.waiting_step,r.created_at,r.updated_at,
                           r.error,p.id AS project_id,p.name AS project_name
                    FROM runs r JOIN projects p ON p.id=r.project_id
                    {where}
                    ORDER BY r.created_at DESC LIMIT ?""",
                params,
            ).fetchall()
            results = [dict(row) for row in rows]
            for item in results:
                # Read the durable wait, not an in-memory timer. Include the attempt for stale-wait protection.
                saved = conn.execute("SELECT waiting_attempt FROM runs WHERE id=?", (item["id"],)).fetchone()
                item["waiting_attempt"] = saved["waiting_attempt"]
                events = [{"id": row["id"], "event_type": row["event_type"],
                           "payload": json.loads(row["payload_json"])} for row in conn.execute(
                    "SELECT id,event_type,payload_json FROM run_events WHERE run_id=? "
                    "AND event_type IN ('run.approval_pending','run.approval_overdue') ORDER BY id", (item["id"],))]
                item["attention"] = self._approval_attention(item, events)
            return results

    @staticmethod
    def _approval_attention(run: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
        if (run["status"] != RunStatus.WAITING_HUMAN.value
                or run.get("waiting_reason") != WaitingReason.APPROVAL_PENDING.value):
            return None
        pending = next((event for event in reversed(events)
                        if event["event_type"] == "run.approval_pending"
                        and event["payload"].get("step_id") == run.get("waiting_step")
                        and event["payload"].get("attempt") == run.get("waiting_attempt")), None)
        if pending is None:
            return None
        overdue = any(event["event_type"] == "run.approval_overdue"
                      and event["payload"].get("wait_id") == pending["id"] for event in events)
        return {**pending["payload"], "wait_id": pending["id"], "kind": "approval_pending",
                "who": "user", "overdue": overdue,
                "question": "Approve, request changes or reject the current artifact"}

    def begin_approval_wait(self, run: dict[str, Any], artifact: dict[str, Any], *, since: str, deadline: str) -> None:
        """CAS the review and its deadline together; no schema change or new lifecycle state."""
        with self.db.tx() as conn:
            fresh = conn.execute("SELECT * FROM runs WHERE id=?", (run["id"],)).fetchone()
            fields = ("status", "waiting_reason", "waiting_step", "waiting_attempt", "current_step")
            if (fresh is None or fresh["cancel_requested"] or fresh["status"] != RunStatus.WAITING_HUMAN.value
                    or any(fresh[key] != run[key] for key in fields)):
                raise InvalidStateTransition("The persisted approval wait changed; read the run again")
            if fresh["waiting_reason"] == WaitingReason.APPROVAL_PENDING.value:
                return
            if fresh["waiting_reason"] != WaitingReason.REVIEW.value:
                raise InvalidStateTransition("Only a review can wait for approval")
            conn.execute("UPDATE runs SET waiting_reason=?,waiting_step=?,waiting_attempt=?,updated_at=? WHERE id=?",
                         (WaitingReason.APPROVAL_PENDING.value, artifact["step_id"], artifact["attempt"], since, run["id"]))
            payload = {"step_id": artifact["step_id"], "attempt": artifact["attempt"],
                       "artifact_id": artifact["id"], "since": since, "deadline": deadline}
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run["id"], since, "run.approval_pending", json.dumps(payload)))

    def _due_approval_waits(self, conn, *, now: str, run_id: str | None = None):
        sql = "SELECT * FROM runs WHERE status=? AND waiting_reason=? AND cancel_requested=0"
        params = [RunStatus.WAITING_HUMAN.value, WaitingReason.APPROVAL_PENDING.value]
        if run_id is not None:
            sql += " AND id=?"
            params.append(run_id)
        due = []
        for row in conn.execute(sql, params).fetchall():
            events = [{"id": event["id"], "event_type": event["event_type"],
                       "payload": json.loads(event["payload_json"])} for event in conn.execute(
                "SELECT id,event_type,payload_json FROM run_events WHERE run_id=? "
                "AND event_type IN ('run.approval_pending','run.approval_overdue') ORDER BY id", (row["id"],))]
            attention = self._approval_attention(dict(row), events)
            if (attention is not None and not attention["overdue"]
                    and datetime.fromisoformat(now) >= datetime.fromisoformat(attention["deadline"])):
                due.append((row["id"], attention))
        return due

    def check_approval_deadlines(self, *, now: str, run_id: str | None = None) -> int:
        # GET/list and idle workers need no write lock unless a notice is actually due.
        with self.db.connect() as conn:
            if not self._due_approval_waits(conn, now=now, run_id=run_id):
                return 0
        count = 0
        with self.db.tx() as conn:
            # Re-read under the lock: a decision or another observer may have won.
            for identifier, attention in self._due_approval_waits(conn, now=now, run_id=run_id):
                payload = {key: attention[key] for key in ("wait_id", "step_id", "attempt", "deadline")}
                conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                             (identifier, now, "run.approval_overdue", json.dumps(payload)))
                count += 1
        return count

    def next_queued_run(self) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE status=? AND cancel_requested=0 ORDER BY created_at LIMIT 1",
                (RunStatus.QUEUED.value,),
            ).fetchone()
        return self.get_run(row["id"]) if row else None

    def claim_queued_run(self, worker_id: str, *, lease_seconds: float,
                         max_active_total: int | None = None,
                         max_active_per_project: int | None = None,
                         now: str | None = None) -> dict[str, Any] | None:
        """Claim a queued run and check shared limits under one SQLite write lock."""
        now = now or utcnow()
        cutoff = (datetime.fromisoformat(now) - timedelta(seconds=lease_seconds)).isoformat()
        with self.db.tx() as conn:
            active = "status=? OR (status=? AND worker_id IS NOT NULL AND worker_id!='')"
            params = (RunStatus.RUNNING.value, RunStatus.QUEUED.value)
            if max_active_total is not None:
                count = conn.execute(f"SELECT COUNT(*) FROM runs WHERE ({active}) AND heartbeat_at>=?",
                                     (*params, cutoff)).fetchone()[0]
                if count >= max_active_total:
                    return None
            rows = conn.execute(
                "SELECT id,project_id FROM runs WHERE status=? AND cancel_requested=0 "
                "AND (worker_id IS NULL OR worker_id='') ORDER BY created_at,id",
                (RunStatus.QUEUED.value,),
            ).fetchall()
            for row in rows:
                if max_active_per_project is not None:
                    count = conn.execute(
                        f"SELECT COUNT(*) FROM runs WHERE ({active}) AND heartbeat_at>=? AND project_id=?",
                        (*params, cutoff, row["project_id"]),
                    ).fetchone()[0]
                    if count >= max_active_per_project:
                        continue
                changed = conn.execute(
                    "UPDATE runs SET worker_id=?,started_at=?,heartbeat_at=?,updated_at=? "
                    "WHERE id=? AND status=? AND cancel_requested=0 AND (worker_id IS NULL OR worker_id='')",
                    (worker_id, now, now, now, row["id"], RunStatus.QUEUED.value),
                ).rowcount
                if changed:
                    return dict(conn.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone())
        return None

    def renew_lease(self, run_id: str, worker_id: str, *, now: str | None = None) -> bool:
        with self.db.connect() as conn:
            changed = conn.execute(
                "UPDATE runs SET heartbeat_at=? WHERE id=? AND worker_id=? AND status IN (?,?)",
                (now or utcnow(), run_id, worker_id, RunStatus.QUEUED.value, RunStatus.RUNNING.value),
            ).rowcount
            conn.commit()
        return bool(changed)

    def release_claim(self, run_id: str, worker_id: str) -> None:
        """Clear ownership once the engine has left the running state."""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE runs SET worker_id='',heartbeat_at='' WHERE id=? AND worker_id=? AND status!=?",
                (run_id, worker_id, RunStatus.RUNNING.value),
            )
            conn.commit()

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status", "current_step", "revision_feedback", "cancel_requested", "llm_calls", "error",
            "waiting_reason", "waiting_step", "waiting_attempt", "worker_id", "started_at", "heartbeat_at",
            "active_seconds", "budget_exhausted", "successful_llm_calls",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Fields cannot be updated: {sorted(unknown)}")
        fields["updated_at"] = utcnow()
        sets = ",".join(f"{key}=?" for key in fields)
        values = list(fields.values()) + [run_id]
        with self.db.connect() as conn:
            conn.execute(f"UPDATE runs SET {sets} WHERE id=?", values)
            conn.commit()

    def fail_active_run(self, run_id: str, error: Exception, *,
                        issues: list[dict[str, Any]] | None = None,
                        message: str | None = None) -> bool:
        """Atomically record failure without overwriting a settled run state."""
        message = str(error) if message is None else message
        payload: dict[str, Any] = {"error": message, "type": type(error).__name__}
        if type(error).__name__ == "EgressBlocked":
            payload["code"] = "egress_blocked"
        if issues is not None:
            payload["issues"] = issues
        with self.db.tx() as conn:
            changed = conn.execute(
                """UPDATE runs SET status=?,error=?,waiting_reason='',waiting_step='',
                   waiting_attempt=NULL,updated_at=? WHERE id=? AND status IN (?,?)""",
                (RunStatus.FAILED.value, message, utcnow(), run_id,
                 RunStatus.QUEUED.value, RunStatus.RUNNING.value),
            ).rowcount
            if changed:
                conn.execute(
                    "INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                    (run_id, utcnow(), "workflow_failed", json.dumps(payload, ensure_ascii=False)),
                )
        return bool(changed)

    def mark_worker_start(self, run_id: str, worker_id: str) -> None:
        now = utcnow()
        self.update_run(run_id, worker_id=worker_id, started_at=now, heartbeat_at=now)

    def heartbeat(self, run_id: str) -> None:
        self.update_run(run_id, heartbeat_at=utcnow())

    def recover_running_runs(self, worker_id: str, *, lease_seconds: float = 30,
                             now: str | None = None) -> list[str]:
        recovered: list[str] = []
        notices: list[tuple[str, str, dict[str, Any], int | None]] = []
        now = now or utcnow()
        cutoff = (datetime.fromisoformat(now) - timedelta(seconds=lease_seconds)).isoformat()
        with self.db.tx() as conn:
            # A process may die after claiming and before entering the engine.
            conn.execute(
                "UPDATE runs SET worker_id='',started_at='',heartbeat_at='',updated_at=? "
                "WHERE status=? AND worker_id IS NOT NULL AND worker_id!='' "
                "AND (heartbeat_at IS NULL OR heartbeat_at='' OR heartbeat_at<?)",
                (now, RunStatus.QUEUED.value, cutoff),
            )
            rows = conn.execute(
                "SELECT id,cancel_requested,worker_id FROM runs WHERE status=? "
                "AND (heartbeat_at IS NULL OR heartbeat_at='' OR heartbeat_at<?)",
                (RunStatus.RUNNING.value, cutoff),
            ).fetchall()
            for row in rows:
                run_id = row["id"]
                interrupted = {"reason": "backend_restart", "previous_worker_id": row["worker_id"] or ""}
                cursor = conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                                      (run_id, now, "run_interrupted", json.dumps(interrupted)))
                notices.append((run_id, "run_interrupted", interrupted, cursor.lastrowid))
                if row["cancel_requested"]:
                    conn.execute(
                        "UPDATE runs SET status=?,waiting_reason='',waiting_step='',waiting_attempt=NULL,"
                        "worker_id='',heartbeat_at='',updated_at=? WHERE id=?",
                        (RunStatus.CANCELLED.value, now, run_id),
                    )
                    event_type, payload = "workflow_cancelled", {"reason": "recovered_cancel_request"}
                else:
                    conn.execute(
                        "UPDATE runs SET status=?,worker_id='',started_at='',heartbeat_at='',updated_at=? WHERE id=?",
                        (RunStatus.QUEUED.value, now, run_id),
                    )
                    event_type, payload = "run_recovered", {"action": "requeued"}
                    recovered.append(run_id)
                cursor = conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                                      (run_id, now, event_type, json.dumps(payload)))
                notices.append((run_id, event_type, payload, cursor.lastrowid))
        for notice in notices:
            for listener in tuple(self.db.event_listeners):
                try:
                    listener(*notice)
                except Exception:
                    pass
        return recovered

    def latest_step_attempt(self, run_id: str, step_id: str) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT MAX(attempt) a FROM run_steps WHERE run_id=? AND step_id=?",
                (run_id, step_id),
            ).fetchone()
            completed = int(row["a"] or 0)
            pending = conn.execute(
                "SELECT MAX(attempt) a FROM human_step_requests WHERE run_id=? AND step_id=?",
                (run_id, step_id),
            ).fetchone()
            return max(completed, int(pending["a"] or 0))

    def record_step(
        self,
        *,
        run_id: str,
        step_id: str,
        attempt: int,
        status: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any] | None,
        model_id: str,
        prompt_id: str,
        prompt_version: int,
        error: str = "",
    ) -> None:
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO run_steps(
                    run_id,step_id,attempt,status,input_json,output_json,model_id,prompt_id,prompt_version,error,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, step_id, attempt, status,
                    json.dumps(input_data, ensure_ascii=False),
                    json.dumps(output_data or {}, ensure_ascii=False), model_id, prompt_id,
                    prompt_version, error, now, now,
                ),
            )
            conn.commit()

    def latest_step_output(self, run_id: str, step_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT output_json FROM run_steps WHERE run_id=? AND step_id=? AND status='completed' ORDER BY attempt DESC LIMIT 1",
                (run_id, step_id),
            ).fetchone()
            return json.loads(row["output_json"]) if row else None

    def latest_completed_step(self, run_id: str, step_id: str) -> tuple[int, dict[str, Any]] | None:
        """``(attempt, output)`` of the latest completed attempt of a step, or ``None`` when it has none.

        Same row as ``latest_step_output`` plus the attempt it came from (context compaction names the
        originals it replaces by step and attempt).
        """
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT attempt, output_json FROM run_steps WHERE run_id=? AND step_id=? AND status='completed' "
                "ORDER BY attempt DESC LIMIT 1",
                (run_id, step_id),
            ).fetchone()
            return (int(row["attempt"]), json.loads(row["output_json"])) if row else None

    def list_step_attempts(self, run_id: str, step_id: str) -> list[dict[str, Any]]:
        """All persisted ``run_steps`` rows for one step, oldest attempt first."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_steps WHERE run_id=? AND step_id=? ORDER BY attempt",
                (run_id, step_id),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["input"] = json.loads(item.pop("input_json") or "{}")
            item["output"] = json.loads(item.pop("output_json") or "{}")
            items.append(item)
        return items

    def add_artifact(self, *, run_id: str, step_id: str, attempt: int, kind: str, path: str, data: dict[str, Any]) -> str:
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM artifacts WHERE run_id=? AND step_id=? AND attempt=?",
                (run_id, step_id, attempt),
            ).fetchone()
            if existing:
                return existing["id"]
            aid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO artifacts(id,run_id,step_id,attempt,kind,path,data_json,approved,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (aid, run_id, step_id, attempt, kind, path, json.dumps(data, ensure_ascii=False), 0, utcnow()),
            )
            conn.commit()
            return aid

    def review_artifact(self, run_id: str, *, step_id: str = "",
                        attempt: int | None = None) -> dict[str, Any]:
        """Read a completed artifact and its prompt identity without changing it.

        An omitted step is reserved for a budget stop; insertion order identifies
        the last saved result even when timestamps have equal precision.
        """
        clauses = ["a.run_id=?", "s.status='completed'"]
        params: list[Any] = [run_id]
        if step_id:
            clauses.append("a.step_id=?")
            params.append(step_id)
        if attempt is not None:
            clauses.append("a.attempt=?")
            params.append(attempt)
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT a.*,s.prompt_id,s.prompt_version FROM artifacts a
                   JOIN run_steps s ON s.run_id=a.run_id AND s.step_id=a.step_id
                     AND s.attempt=a.attempt WHERE """ + " AND ".join(clauses) +
                " ORDER BY a.rowid DESC", params,
            ).fetchall()
        if not rows or (attempt is not None and len(rows) != 1):
            raise InvalidStateTransition("The review has no unique completed artifact")
        return dict(rows[0])

    def finish_review(self, run: dict[str, Any], artifact: dict[str, Any], *,
                      approve: bool, current_step: int, completed: bool = False,
                       feedback: str = "", regenerate: bool = False,
                       revision_feedback: str | None = None, comments: list[dict[str, Any]] | None = None,
                       by: str | None = None, channel: str | None = None) -> None:
        """Commit the artifact decision, cursor and events together.

        Compare the persisted wait again inside the transaction. This rejects an
        immediate duplicate or a cancellation after the caller read the run.
        It is not a token for a late retry arriving at a different checkpoint.
        """
        run_id = run["id"]
        with self.db.tx() as conn:
            fresh = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            fields = ("status", "current_step", "waiting_reason", "waiting_step",
                      "waiting_attempt", "budget_exhausted", "cancel_requested")
            if (fresh is None or fresh["status"] != RunStatus.WAITING_HUMAN.value
                    or fresh["cancel_requested"]
                    or any(fresh[key] != run[key] for key in fields)):
                raise InvalidStateTransition("The persisted review changed; read the run again")
            saved = conn.execute(
                """SELECT a.*,s.prompt_id,s.prompt_version FROM artifacts a
                   JOIN run_steps s ON s.run_id=a.run_id AND s.step_id=a.step_id
                     AND s.attempt=a.attempt
                   WHERE a.id=? AND a.run_id=? AND s.status='completed'""",
                (artifact["id"], run_id),
            ).fetchone()
            if saved is None or dict(saved) != artifact:
                raise InvalidStateTransition("The reviewed artifact changed; read the run again")
            if not fresh["budget_exhausted"] and fresh["waiting_reason"] != "budget_exhausted":
                latest = conn.execute(
                    """SELECT MAX(attempt) FROM (
                         SELECT attempt FROM run_steps WHERE run_id=? AND step_id=?
                         UNION ALL
                         SELECT attempt FROM human_step_requests WHERE run_id=? AND step_id=?)""",
                    (run_id, artifact["step_id"], run_id, artifact["step_id"]),
                ).fetchone()[0]
                if saved["approved"] or latest != artifact["attempt"]:
                    raise InvalidStateTransition("The review no longer identifies the current unapproved attempt")
            if approve:
                conn.execute("UPDATE artifacts SET approved=1 WHERE id=?", (artifact["id"],))
            status = RunStatus.COMPLETED.value if completed else RunStatus.QUEUED.value
            conn.execute(
                """UPDATE runs SET status=?,current_step=?,revision_feedback=?,
                   waiting_reason='',waiting_step='',waiting_attempt=NULL,updated_at=?
                   WHERE id=?""",
                (status, current_step, feedback if revision_feedback is None else revision_feedback, utcnow(), run_id),
            )
            payload = {"step_id": artifact["step_id"], "attempt": artifact["attempt"],
                       "artifact_id": artifact["id"]}
            if not approve:
                payload.update(mode="regenerate" if regenerate else "revise", feedback=feedback)
                if comments is not None:
                    payload["comments"] = comments
                payload.update(by=by, channel=channel)
            else:
                # ARR-T04: an approval says who gave it and through which door, as a tool approval does.
                declared, door = resolution_source(by, channel)
                payload.update(by=declared, channel=door)
            conn.execute(
                "INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                (run_id, utcnow(), "human_approved" if approve else "human_revision",
                 json.dumps(payload, ensure_ascii=False)),
            )
            if completed:
                conn.execute(
                    "INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                    (run_id, utcnow(), "workflow_completed", "{}"),
                )

    def finish_escalation(self, run: dict[str, Any], *, approve: bool, feedback: str = "") -> None:
        """Commit a human's decision on a paused escalation (ECO-T06): approve queues the run, reject fails it.

        Like ``finish_review`` it compares the persisted wait again inside the transaction, so a duplicate
        or a decision after a cancellation is refused. The decision is tied to the latest
        ``escalation_requested`` event of the waiting step and attempt, which has no decision yet.
        Approval records ``escalation_approved`` (the engine resumes the step with the escalated model);
        rejection records ``escalation_rejected`` and fails the run with the reason.
        """
        run_id = run["id"]
        with self.db.tx() as conn:
            fresh = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            fields = ("status", "current_step", "waiting_reason", "waiting_step", "waiting_attempt", "cancel_requested")
            if (fresh is None or fresh["status"] != RunStatus.WAITING_HUMAN.value
                    or fresh["waiting_reason"] != "escalation_approval" or fresh["cancel_requested"]
                    or any(fresh[key] != run[key] for key in fields)):
                raise InvalidStateTransition("The persisted escalation changed; read the run again")
            requested = None
            for row in conn.execute(
                    """SELECT event_type,payload_json FROM run_events WHERE run_id=? AND event_type IN
                       ('escalation_requested','escalation_approved','escalation_rejected') ORDER BY id""",
                    (run_id,)):
                payload = json.loads(row["payload_json"])
                if payload.get("step_id") != fresh["waiting_step"] or payload.get("attempt") != fresh["waiting_attempt"]:
                    continue
                requested = payload if row["event_type"] == "escalation_requested" else None
            if requested is None:
                raise InvalidStateTransition("The run has no escalation waiting for a decision")
            now = utcnow()
            if approve:
                conn.execute(
                    """UPDATE runs SET status=?,waiting_reason='',waiting_step='',waiting_attempt=NULL,updated_at=?
                       WHERE id=?""", (RunStatus.QUEUED.value, now, run_id))
                conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                             (run_id, now, "escalation_approved", json.dumps(requested, ensure_ascii=False)))
                return
            error = (f"Escalation of step {requested['step_id']!r} from {requested['from_level']} to "
                     f"{requested['to_level']} ({requested['to_model']}) was rejected by a human"
                     + (f": {feedback}" if feedback.strip() else "") + "; the step failed")
            conn.execute(
                """UPDATE runs SET status=?,error=?,revision_feedback=?,waiting_reason='',waiting_step='',
                   waiting_attempt=NULL,updated_at=? WHERE id=?""",
                (RunStatus.FAILED.value, error, feedback, now, run_id))
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run_id, now, "escalation_rejected",
                          json.dumps({**requested, "feedback": feedback}, ensure_ascii=False)))
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run_id, now, "workflow_failed",
                          json.dumps({"error": error, "type": "EscalationRejected"}, ensure_ascii=False)))

    def finish_budget_cut(self, run: dict[str, Any], *, approve: bool, feedback: str = "",
                          by: str | None = None, channel: str | None = None) -> None:
        """Commit a person's decision on a call held by a budget or egress guard.

        Like ``finish_escalation`` it compares the persisted wait again inside the transaction, so a duplicate or a decision
        after a cancellation is refused. The decision is tied to the latest ``call_paused`` event of the budget guard for the
        waiting step and attempt that has no decision yet; everything it records comes from that event, nothing from the
        caller but ``by`` and ``channel`` (as it declared them; nothing authenticates them, see ``document_review``).
        Approval records ``budget_cut_approved`` with a fresh ``approval_id`` and queues the run: the resumed step lets
        exactly that call through with it (same step attempt, model and position, the budgets and estimate the person saw;
        ``budget_cut_released`` spends it). Rejection records ``budget_cut_rejected`` and fails the run with the reason.
        Only the approval path of runs calls this, and the unauthenticated HTTP route is not allowed to (``RunService.approve``):
        no guard or model approves a cut.
        """
        run_id = run["id"]
        egress = run.get("waiting_reason") == WaitingReason.EGRESS_REVIEW.value
        reason = WaitingReason.EGRESS_REVIEW.value if egress else WaitingReason.BUDGET_CUT.value
        approved_event = EGRESS_APPROVED if egress else BUDGET_CUT_APPROVED
        rejected_event = EGRESS_REJECTED if egress else BUDGET_CUT_REJECTED
        from_pause = egress_approval_from_pause if egress else approval_from_pause
        is_pause = is_egress_pause if egress else is_budget_pause
        with self.db.tx() as conn:
            fresh = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            fields = ("status", "current_step", "waiting_reason", "waiting_step", "waiting_attempt", "cancel_requested")
            if (fresh is None or fresh["status"] != RunStatus.WAITING_HUMAN.value
                    or fresh["waiting_reason"] != reason or fresh["cancel_requested"]
                    or any(fresh[key] != run[key] for key in fields)):
                raise InvalidStateTransition("The persisted call review changed; read the run again")
            paused = None
            for row in conn.execute(
                    "SELECT event_type,payload_json FROM run_events WHERE run_id=? AND event_type IN (?,?,?) ORDER BY id",
                    (run_id, "call_paused", approved_event, rejected_event)):
                payload = json.loads(row["payload_json"])
                if payload.get("step_id") != fresh["waiting_step"] or payload.get("attempt") != fresh["waiting_attempt"]:
                    continue
                paused = payload if row["event_type"] == "call_paused" and is_pause(payload) else None
            if paused is None:
                raise InvalidStateTransition("The run has no call review waiting for a decision")
            declared, door = resolution_source(by, channel)
            now = utcnow()
            if approve:
                approval = from_pause(paused, approval_id=uuid.uuid4().hex, by=declared, channel=door)
                conn.execute(
                    """UPDATE runs SET status=?,waiting_reason='',waiting_step='',waiting_attempt=NULL,updated_at=?
                       WHERE id=?""", (RunStatus.QUEUED.value, now, run_id))
                conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                             (run_id, now, approved_event, json.dumps(approval, ensure_ascii=False)))
                return
            error = ("Egress review rejected" + (f": {feedback}" if feedback else "")) if egress else rejection_message(paused, feedback)
            conn.execute(
                """UPDATE runs SET status=?,error=?,revision_feedback=?,waiting_reason='',waiting_step='',
                   waiting_attempt=NULL,updated_at=? WHERE id=?""",
                (RunStatus.FAILED.value, error, feedback, now, run_id))
            refusal = from_pause(paused, approval_id="", by=declared, channel=door)
            del refusal["approval_id"]            # a refusal lets nothing through
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run_id, now, rejected_event,
                          json.dumps({**refusal, "feedback": feedback}, ensure_ascii=False)))
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run_id, now, "workflow_failed",
                          json.dumps({"error": error, "type": "EgressBlocked" if egress else "BudgetCutRejected",
                                      "code": "egress_blocked" if egress else "budget_cut_rejected"}, ensure_ascii=False)))

    def finish_content_review(self, run: dict[str, Any], *, approve: bool, feedback: str = "",
                              by: str | None = None, channel: str | None = None) -> int:
        """Commit a decision on a run held by the content guard (BDI-T04); returns how many reviews it settled.

        Like ``finish_escalation`` it compares the persisted wait again inside the transaction, so a duplicate or a
        decision after a cancellation is refused. It settles every ``document_review_requested`` still open for the
        waiting step attempt (the guard pauses on the first; one decision covers them all) with one
        ``document_review_resolved`` each, carrying the same four keys as the request (``document_review``), so the
        resumed run does not ask again, plus ``by`` and ``channel`` as the caller declared them (``api`` when it did
        not say; nothing authenticates them, see ``document_review``). Approval queues the run; rejection rejects it
        (``human_rejected``), and a ``rejected`` resolution never lifts the guard. A request that names the step but
        has no usable key cannot be settled: approving is refused while one is open (reject or cancel the run).
        This is called by the run approval path; nothing else in the framework settles a review.
        """
        run_id = run["id"]
        with self.db.tx() as conn:
            fresh = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            fields = ("status", "current_step", "waiting_reason", "waiting_step", "waiting_attempt", "cancel_requested")
            if (fresh is None or fresh["status"] != RunStatus.WAITING_HUMAN.value
                    or fresh["waiting_reason"] != WaitingReason.CONTENT_REVIEW.value or fresh["cancel_requested"]
                    or any(fresh[key] != run[key] for key in fields)):
                raise InvalidStateTransition("The persisted content review changed; read the run again")
            logged = [{"event_type": row["event_type"], "payload": json.loads(row["payload_json"])}
                      for row in conn.execute(
                          "SELECT event_type,payload_json FROM run_events WHERE run_id=? AND event_type IN (?,?) "
                          "ORDER BY id", (run_id, EVENT_REVIEW_REQUESTED, EVENT_REVIEW_RESOLVED))]
            blocking = unresolved_requests(logged, step_id=fresh["waiting_step"], attempt=fresh["waiting_attempt"],
                                           include_unkeyed=True)
            if not blocking:
                raise InvalidStateTransition("The run has no content review waiting for a decision")
            pending = [request for request in blocking if review_key(request) is not None]
            if approve and len(pending) != len(blocking):
                raise InvalidStateTransition("A content review of this step cannot be identified, so it cannot be "
                                             "approved; reject or cancel the run")
            now, decision = utcnow(), DECISION_APPROVED if approve else DECISION_REJECTED
            for request in pending:
                conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                             (run_id, now, EVENT_REVIEW_RESOLVED,
                              json.dumps(resolution_payload(request, decision, by=by, channel=channel),
                                         ensure_ascii=False)))
            if approve:
                conn.execute(
                    """UPDATE runs SET status=?,waiting_reason='',waiting_step='',waiting_attempt=NULL,updated_at=?
                       WHERE id=?""", (RunStatus.QUEUED.value, now, run_id))
            else:
                conn.execute(
                    """UPDATE runs SET status=?,revision_feedback=?,waiting_reason='',waiting_step='',
                       waiting_attempt=NULL,updated_at=? WHERE id=?""",
                    (RunStatus.REJECTED.value, feedback, now, run_id))
                conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                             (run_id, now, "human_rejected", json.dumps({"feedback": feedback}, ensure_ascii=False)))
            return len(pending)

    def finish_user_message(self, run: dict[str, Any], *, approve: bool, feedback: str = "") -> None:
        """Commit a person's decision on a run that a message of theirs paused (AVA-T03, ``waiting_reason`` ``user_message``).

        Like ``finish_escalation`` it compares the persisted wait again inside the transaction, so a duplicate decision or
        one after a cancellation is refused. Approving queues the run again (the message was consumed when it paused the run,
        so the guard does not read it twice); rejecting rejects the run. Only the approval path of runs calls this: the
        inbox that recorded the message never does.
        """
        run_id = run["id"]
        with self.db.tx() as conn:
            fresh = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            fields = ("status", "current_step", "waiting_reason", "waiting_step", "waiting_attempt", "cancel_requested")
            if (fresh is None or fresh["status"] != RunStatus.WAITING_HUMAN.value
                    or fresh["waiting_reason"] != WaitingReason.USER_MESSAGE.value or fresh["cancel_requested"]
                    or any(fresh[key] != run[key] for key in fields)):
                raise InvalidStateTransition("The persisted wait for the message changed; read the run again")
            now = utcnow()
            if approve:
                conn.execute(
                    """UPDATE runs SET status=?,waiting_reason='',waiting_step='',waiting_attempt=NULL,updated_at=?
                       WHERE id=?""", (RunStatus.QUEUED.value, now, run_id))
                conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                             (run_id, now, "user_message_resolved", json.dumps({"decision": "approved"})))
                return
            conn.execute(
                """UPDATE runs SET status=?,revision_feedback=?,waiting_reason='',waiting_step='',
                   waiting_attempt=NULL,updated_at=? WHERE id=?""", (RunStatus.REJECTED.value, feedback, now, run_id))
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run_id, now, "user_message_resolved", json.dumps({"decision": "rejected"})))
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         (run_id, now, "human_rejected", json.dumps({"feedback": feedback}, ensure_ascii=False)))

    def increment_llm_calls(self, run_id: str) -> int:
        with self.db.connect() as conn:
            conn.execute("UPDATE runs SET llm_calls=llm_calls+1, updated_at=? WHERE id=?", (utcnow(), run_id))
            conn.commit()
            row = conn.execute("SELECT llm_calls FROM runs WHERE id=?", (run_id,)).fetchone()
            return int(row["llm_calls"])

    def increment_successful_llm_calls(self, run_id: str) -> int:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE runs SET successful_llm_calls=successful_llm_calls+1, updated_at=? WHERE id=?",
                (utcnow(), run_id),
            )
            conn.commit()
            row = conn.execute("SELECT successful_llm_calls FROM runs WHERE id=?", (run_id,)).fetchone()
            return int(row["successful_llm_calls"])

    def artifact_count(self, run_id: str) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) c FROM artifacts WHERE run_id=?", (run_id,)).fetchone()
            return int(row["c"])

    # -------- human execution bridge --------
    def create_human_step_request(
        self,
        *,
        run_id: str,
        step_id: str,
        attempt: int,
        prompt_id: str,
        prompt_version: int,
        prompt_sha256: str,
        prompt_base: str,
        expected_contract: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM human_step_requests WHERE run_id=? AND step_id=? AND attempt=?",
                (run_id, step_id, attempt),
            ).fetchone()
            if existing:
                return dict(existing)
            conn.execute(
                """INSERT INTO human_step_requests(
                    id,run_id,step_id,attempt,prompt_id,prompt_version,prompt_sha256,prompt_base,
                    expected_contract,context_json,status,created_at,completed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_id, run_id, step_id, attempt, prompt_id, prompt_version, prompt_sha256,
                    prompt_base, expected_contract, json.dumps(context, ensure_ascii=False), "pending", utcnow(), "",
                ),
            )
            conn.commit()
        return self.pending_human_step(run_id) or {}

    def pending_human_step(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM human_step_requests WHERE run_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["context"] = json.loads(item.pop("context_json"))
            return item

    def save_human_submission(
        self,
        *,
        request_id: str,
        run_id: str,
        step_id: str,
        attempt: int,
        provider: str,
        model: str,
        prompt_used: str,
        raw_response: str,
        normalized: dict[str, Any],
        validation: dict[str, Any],
        notes: str,
    ) -> str:
        sid = uuid.uuid4().hex
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO human_step_submissions(
                    id,request_id,run_id,step_id,attempt,provider,model,provenance,prompt_used,raw_response,
                    normalized_json,validation_json,notes,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sid, request_id, run_id, step_id, attempt, provider, model, "human_reported",
                    prompt_used, raw_response, json.dumps(normalized, ensure_ascii=False),
                    json.dumps(validation, ensure_ascii=False), notes, utcnow(),
                ),
            )
            conn.execute(
                "UPDATE human_step_requests SET status='completed',completed_at=? WHERE id=?",
                (utcnow(), request_id),
            )
            conn.commit()
        return sid

    def backfill_orchestration_metadata(self) -> None:
        """Backfill derivable metadata when opening an older database without deleting history."""
        with self.db.connect() as conn:
            rows = conn.execute("SELECT id,request_json,brand_version,request_fingerprint,brand_profile_id FROM runs").fetchall()
        for row in rows:
            updates: dict[str, Any] = {}
            if not row["request_fingerprint"]:
                try:
                    payload = json.loads(row["request_json"])
                    request_type = RunRequest if "workflow_id" in payload else SocialPostRequest
                    req = request_type.model_validate(payload)
                    updates["request_fingerprint"] = _canonical_request_fingerprint(req)
                except Exception:
                    pass
            if row["brand_version"] is not None and not row["brand_profile_id"]:
                with self.db.connect() as conn:
                    brand = conn.execute("SELECT id FROM brand_profiles WHERE version=?", (row["brand_version"],)).fetchone()
                if brand:
                    updates["brand_profile_id"] = brand["id"]
            if updates:
                with self.db.connect() as conn:
                    sets = ",".join(f"{k}=?" for k in updates)
                    conn.execute(f"UPDATE runs SET {sets} WHERE id=?", [*updates.values(), row["id"]])
                    conn.commit()

    # -------- telemetry --------
    def record_telemetry(self, **data) -> None:
        fields = [
            "run_id","step_id","agent_id","attempt","provider","model","model_digest","prompt_id",
            "prompt_version","prompt_sha256","brand_version","tokens_in","tokens_out","num_ctx",
            "latency_ms","duration_ms","estimated_cost_usd","cost_basis","cache_read_tokens","cache_write_tokens","success","error_type","created_at"
        ]
        values = [data.get(k) for k in fields[:-1]] + [utcnow()]
        with self.db.connect() as conn:
            # task_id is copied from the run when the row is written (PRY): usage can then be added up by task.
            conn.execute(
                f"INSERT INTO telemetry({','.join(fields)},task_id) "
                f"VALUES({','.join('?' for _ in fields)},(SELECT task_id FROM runs WHERE id=?))",
                [*values, data.get("run_id")],
            )
            conn.commit()

    # -------- brand --------
    def save_brand_profile(self, payload: BrandProfilePayload, *, created_by: str = "human", activate: bool = False) -> dict[str, Any]:
        profile_id = uuid.uuid4().hex
        with self.db.connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM brand_profiles").fetchone()
            version = int(row["v"])
            if activate:
                conn.execute("UPDATE brand_profiles SET is_active=0")
            conn.execute(
                "INSERT INTO brand_profiles(id,version,is_active,payload_json,created_at,created_by) VALUES(?,?,?,?,?,?)",
                (profile_id, version, int(activate), payload.model_dump_json(), utcnow(), created_by),
            )
            conn.commit()
        return {"id": profile_id, "version": version, "is_active": activate}

    def list_brand_profiles(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id,version,is_active,created_at,created_by FROM brand_profiles ORDER BY version DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def activate_brand_profile(self, profile_id: str) -> None:
        with self.db.connect() as conn:
            if not conn.execute("SELECT 1 FROM brand_profiles WHERE id=?", (profile_id,)).fetchone():
                raise KeyError("BrandProfile not found")
            conn.execute("UPDATE brand_profiles SET is_active=0")
            conn.execute("UPDATE brand_profiles SET is_active=1 WHERE id=?", (profile_id,))
            conn.commit()

    def activate_brand_version(self, version: int) -> str:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM brand_profiles WHERE version=?", (version,)).fetchone()
        if not row:
            raise KeyError(f"BrandProfile version={version} was not found")
        self.activate_brand_profile(row["id"])
        return row["id"]

    def active_brand_version(self) -> int | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT version FROM brand_profiles WHERE is_active=1 ORDER BY version DESC LIMIT 1").fetchone()
            return int(row["version"]) if row else None

    def active_brand_id(self) -> str | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM brand_profiles WHERE is_active=1 ORDER BY version DESC LIMIT 1").fetchone()
            return str(row["id"]) if row else None

    def brand_profile_by_version(self, version: int | None = None) -> BrandProfilePayload | None:
        with self.db.connect() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT payload_json FROM brand_profiles WHERE is_active=1 ORDER BY version DESC LIMIT 1"
                ).fetchone()
            else:
                row = conn.execute("SELECT payload_json FROM brand_profiles WHERE version=?", (version,)).fetchone()
            return BrandProfilePayload.model_validate_json(row["payload_json"]) if row else None

    def active_brand_profile(self) -> BrandProfilePayload | None:
        return self.brand_profile_by_version(None)

    def brand_context(self, sections: list[str], *, version: int | None = None) -> str:
        profile = self.brand_profile_by_version(version)
        if not profile:
            return ""
        payload = {
            section: profile.usable_section(section)
            for section in sections
            if section in {"core", "voice", "visual", "strategy"}
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # -------- assets / publications / metrics --------
    def add_asset(self, source_path: Path, metadata: AssetMetadata, assets_root: Path) -> dict[str, Any]:
        source_path = source_path.resolve()
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(source_path)
        raw = source_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        mime = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        ext = source_path.suffix.lower()

        with self.db.connect() as conn:
            blob = conn.execute("SELECT * FROM file_blobs WHERE sha256=?", (digest,)).fetchone()
            if blob:
                blob_id = blob["id"]
                relative_destination = Path(blob["internal_path"])
                physical_duplicate = True
            else:
                blob_id = uuid.uuid4().hex
                blob_dir = assets_root / "blobs" / digest[:2]
                blob_dir.mkdir(parents=True, exist_ok=True)
                destination = blob_dir / f"{digest}{ext}"
                if not destination.exists():
                    shutil.copy2(source_path, destination)
                relative_destination = destination.relative_to(assets_root.parent)
                conn.execute(
                    "INSERT INTO file_blobs(id,sha256,internal_path,mime_type,bytes,original_filename,created_at) VALUES(?,?,?,?,?,?,?)",
                    (blob_id, digest, str(relative_destination), mime, len(raw), source_path.name, utcnow()),
                )
                conn.commit()
                physical_duplicate = False

        with self.db.connect() as conn:
            existing_ref = conn.execute(
                "SELECT * FROM asset_references WHERE project_id=? AND blob_id=?",
                (metadata.project_id, blob_id),
            ).fetchone()
            if existing_ref:
                return {
                    "id": existing_ref["id"], "blob_id": blob_id, "sha256": digest,
                    "path": str(relative_destination), "duplicate": True,
                    "physical_duplicate": physical_duplicate, "logical_duplicate": True,
                }
            asset_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO asset_references(
                    id,project_id,blob_id,source,status,title,notes,tags_json,rejection_reason,prompt_id,
                    prompt_version,model_id,run_id,step_id,attempt,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    asset_id, metadata.project_id, blob_id, metadata.source.value, metadata.status.value,
                    metadata.title, metadata.notes, json.dumps(metadata.tags, ensure_ascii=False),
                    metadata.rejection_reason, metadata.prompt_id, metadata.prompt_version, metadata.model_id,
                    metadata.run_id, metadata.step_id, metadata.attempt, utcnow(),
                ),
            )
            conn.commit()
        sidecar_dir = assets_root / "references" / metadata.project_id
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar = sidecar_dir / f"{asset_id}.metadata.json"
        sidecar.write_text(json.dumps({
            "asset_id": asset_id, "blob_id": blob_id, "sha256": digest,
            "original_filename": source_path.name, "internal_path": str(relative_destination),
            **metadata.model_dump(mode="json")
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "id": asset_id, "blob_id": blob_id, "sha256": digest, "path": str(relative_destination),
            "sidecar": str(sidecar.relative_to(assets_root.parent)),
            "duplicate": physical_duplicate, "physical_duplicate": physical_duplicate, "logical_duplicate": False,
        }

    def add_publication(
        self,
        *,
        project_id: str,
        platform: str,
        asset_id: str | None = None,
        artifact_id: str | None = None,
        external_url: str = "",
        objective: str = "",
        audience: str = "",
        brand_version: int | None = None,
        status: str = PublicationStatus.DRAFT.value,
    ) -> str:
        if status not in {item.value for item in PublicationStatus}:
            raise ValueError(f"Invalid publication status: {status}")
        with self.db.connect() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise KeyError("Project not found")
            if artifact_id:
                artifact = conn.execute(
                    """SELECT a.id,r.project_id FROM artifacts a JOIN runs r ON r.id=a.run_id WHERE a.id=?""",
                    (artifact_id,),
                ).fetchone()
                if not artifact:
                    raise KeyError("Artifact not found")
                if artifact["project_id"] != project_id:
                    raise InvalidStateTransition("The artifact does not belong to the publication project")
            if asset_id:
                asset = conn.execute("SELECT project_id FROM asset_references WHERE id=?", (asset_id,)).fetchone()
                if not asset:
                    raise KeyError("Asset not found")
                if asset["project_id"] != project_id:
                    raise InvalidStateTransition("The asset does not belong to the publication project")

            pid = uuid.uuid4().hex
            published_at = utcnow() if status == PublicationStatus.PUBLISHED.value else ""
            conn.execute(
                """INSERT INTO publications(
                    id,project_id,platform,asset_id,artifact_id,external_url,objective,audience,brand_version,
                    published_at,created_at,status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pid, project_id, platform, asset_id, artifact_id, external_url, objective, audience,
                    brand_version, published_at, utcnow(), status,
                ),
            )
            conn.commit()
        return pid

    def add_metric_snapshot(self, item: MetricSnapshotInput) -> str:
        mid = uuid.uuid4().hex
        with self.db.connect() as conn:
            if not conn.execute("SELECT 1 FROM publications WHERE id=?", (item.publication_id,)).fetchone():
                raise KeyError("Publication not found")
            conn.execute(
                "INSERT INTO metric_snapshots(id,publication_id,platform,captured_at,metrics_json,raw_json) VALUES(?,?,?,?,?,?)",
                (
                    mid, item.publication_id, item.platform, utcnow(),
                    json.dumps(item.metrics, ensure_ascii=False), json.dumps(item.raw_payload, ensure_ascii=False),
                ),
            )
            conn.commit()
        return mid
