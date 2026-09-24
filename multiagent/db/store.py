from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import uuid
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
)
from ..errors import IdempotencyConflict, InvalidStateTransition


def _canonical_request_fingerprint(req: SocialPostRequest) -> str:
    payload = req.model_dump(mode="json")
    payload.pop("idempotency_key", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, db: Database):
        self.db = db

    # -------- projects / runs --------
    def get_or_create_project(self, name: str) -> str:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
            if row:
                return row["id"]
            pid = uuid.uuid4().hex
            conn.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", (pid, name, utcnow()))
            conn.commit()
            return pid

    def create_run(self, req: SocialPostRequest, max_llm_calls: int, max_run_seconds: int) -> dict[str, Any]:
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

        run_id = uuid.uuid4().hex
        project_id = self.get_or_create_project(req.project_name)
        brand_version = self.active_brand_version() if req.use_brand_context else None
        brand_profile_id = (self.active_brand_id() or "") if req.use_brand_context else ""
        now = utcnow()
        workflow_id = "social_post_full" if req.pipeline_mode.value == "full" else "social_post"
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO runs(
                    id,project_id,workflow_id,status,request_json,brand_version,current_step,revision_feedback,
                    cancel_requested,idempotency_key,max_llm_calls,max_run_seconds,llm_calls,
                    created_at,updated_at,error,brand_profile_id,execution_mode,research_mode,pipeline_mode,step_modes_json,
                    request_fingerprint,waiting_reason,waiting_step,waiting_attempt,worker_id,started_at,
                    heartbeat_at,active_seconds,budget_exhausted,successful_llm_calls
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, project_id, workflow_id, RunStatus.QUEUED.value,
                    req.model_dump_json(), brand_version, 0, "", 0, req.idempotency_key or None,
                    max_llm_calls, max_run_seconds, 0, now, now, "", brand_profile_id,
                    req.execution_mode.value, req.research_mode.value, req.pipeline_mode.value,
                    json.dumps({k: v.value for k, v in req.step_modes.items()}, ensure_ascii=False),
                    fingerprint, "", "", None, "", "", "", 0.0, 0, 0,
                ),
            )
            conn.commit()
        self.db.log_event(run_id, "workflow_started", {"workflow_id": workflow_id})
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
            for erow in conn.execute("SELECT * FROM run_events WHERE run_id=? ORDER BY id", (run_id,)):
                item = dict(erow)
                item["payload"] = json.loads(item.pop("payload_json"))
                events.append(item)
            result["artifacts"] = artifacts
            result["events"] = events
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
            return [dict(row) for row in rows]

    def next_queued_run(self) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE status=? AND cancel_requested=0 ORDER BY created_at LIMIT 1",
                (RunStatus.QUEUED.value,),
            ).fetchone()
        return self.get_run(row["id"]) if row else None

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

    def mark_worker_start(self, run_id: str, worker_id: str) -> None:
        now = utcnow()
        self.update_run(run_id, worker_id=worker_id, started_at=now, heartbeat_at=now)

    def heartbeat(self, run_id: str) -> None:
        self.update_run(run_id, heartbeat_at=utcnow())

    def recover_running_runs(self, worker_id: str) -> list[str]:
        recovered: list[str] = []
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id,cancel_requested,worker_id FROM runs WHERE status=?",
                (RunStatus.RUNNING.value,),
            ).fetchall()
        for row in rows:
            run_id = row["id"]
            self.db.log_event(run_id, "run_interrupted", {"reason": "backend_restart", "previous_worker_id": row["worker_id"] or ""})
            if row["cancel_requested"]:
                self.update_run(
                    run_id,
                    status=RunStatus.CANCELLED.value,
                    waiting_reason="",
                    waiting_step="",
                    waiting_attempt=None,
                    worker_id="",
                )
                self.db.log_event(run_id, "workflow_cancelled", {"reason": "recovered_cancel_request"})
            else:
                self.update_run(
                    run_id,
                    status=RunStatus.QUEUED.value,
                    worker_id="",
                    started_at="",
                    heartbeat_at="",
                )
                self.db.log_event(run_id, "run_recovered", {"action": "requeued"})
                recovered.append(run_id)
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

    def approve_latest_artifact(self, run_id: str) -> None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM artifacts WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
            if row:
                conn.execute("UPDATE artifacts SET approved=1 WHERE id=?", (row["id"],))
                conn.commit()

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
                    req = SocialPostRequest.model_validate(json.loads(row["request_json"]))
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
            "run_id","step_id","provider","model","model_digest","prompt_id","prompt_version",
            "prompt_sha256","brand_version","tokens_in","tokens_out","num_ctx","latency_ms",
            "estimated_cost_usd","success","error_type","created_at"
        ]
        values = [data.get(k) for k in fields[:-1]] + [utcnow()]
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO telemetry({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                values,
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
