# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""«Try in Mock»: rehearse a staged office package in a system built only for the rehearsal (OFA-T06).

The rehearsal builds its own ``ApplicationServices`` inside a disposable folder: Mock mode, its own database created
with the existing migrations, and its own ``FILE_TOOLS_ROOT`` inside that folder. It never receives a path of the
person and never reads the configured ``FILE_TOOLS_ROOT`` or database. No code of the package runs: ``.py`` files are
not even copied, so a flow with a function step of a package whose ``functions.py`` was not accepted as trusted comes
out «not rehearsed». The folder is deleted when the rehearsal ends, also when it fails; a folder left by a stop is
deleted at the next start, by its prefix. One rehearsal runs at a time and each has a time limit.
"""
from __future__ import annotations

import json
import ntpath
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from ..config import Settings
from ..db.database import utcnow
from ..offices.package_checks import (cross_check_inputs, flow_inputs, in_file_tools_root, read_package_files,
                                      writes_files)
from ..offices.schema import area_bundle_name
from ..run_request import RunRequest
from ..tools.files import _is_link_or_junction
from .errors import INVALID_REQUEST, INVALID_STATE, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices

FOLDER_NAME = "maf-rehearsals"
PREFIX = "rehearsal-"
RECORD = "rehearsal.json"
TIME_LIMIT_SECONDS = 120.0
ORPHAN_SECONDS = 600.0
MAX_FLOWS = 8
MAX_TURNS = 60
FILES_SHOWN = 50
NOTICE = ("Ran in a temporary folder with made-up data and no real model. The folder was deleted when it finished. "
          "Nothing was installed.")
REHEARSAL_REQUIRED = "rehearsal_required"
_ACTOR = "rehearsal"


class _RehearsalSettings(Settings):
    """``Settings`` that reads only what the rehearsal passes in: no process environment, no ``.env`` file and no secret
    files. A field the rehearsal does not pass takes its default, and every default path hangs from ``local_dir``,
    which is inside the rehearsal's folder, so a variable such as ``DB_PATH`` or ``MOCK_MODE`` cannot reach it."""

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings,
                                   file_secret_settings):
        return (init_settings,)


def _inside(root: Path, target: Any) -> bool:
    """Whether a tool target is a relative path that stays inside ``root``: the only kind the rehearsal approves."""
    if not isinstance(target, str) or not target or "\x00" in target:
        return False
    if (PureWindowsPath(target).is_absolute() or PurePosixPath(target).is_absolute()
            or ntpath.splitdrive(target)[0] or target.startswith(("/", "\\"))):
        return False
    try:
        return (root / target).resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


def _snapshot(work: Path) -> dict[str, tuple[int, int]]:
    found: dict[str, tuple[int, int]] = {}
    for path in work.rglob("*"):
        if path.is_file() and not _is_link_or_junction(path):
            stat = path.stat()
            found[path.relative_to(work).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return found


class OfficeRehearsalServices:
    def __init__(self, services: ApplicationServices) -> None:
        self._services = services
        self._lock = threading.Lock()
        self.base: Path | None = None          # tests only: the parent of the disposable folders
        self.limit_seconds = TIME_LIMIT_SECONDS
        self.sweep()

    # --- disposable folders --------------------------------------------------------------------------------------

    def _base(self) -> Path:
        return Path(self.base) if self.base is not None else Path(tempfile.gettempdir()) / FOLDER_NAME

    def sweep(self, older_than: float = ORPHAN_SECONDS) -> int:
        """Delete the folders a stopped rehearsal left behind (by their prefix, and only the old ones)."""
        try:
            entries = list(os.scandir(self._base()))
        except OSError:
            return 0
        cutoff = time.time() - older_than
        removed = 0
        for entry in entries:
            if not entry.name.startswith(PREFIX):
                continue
            try:
                if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                    continue
                path = Path(entry.path)
                if _is_link_or_junction(path):
                    try:
                        path.unlink()
                    except OSError:
                        os.rmdir(path)
                else:
                    shutil.rmtree(path, ignore_errors=True)
                removed += 1
            except OSError:
                continue
        return removed

    @staticmethod
    def _delete(folder: Path) -> None:
        for _ in range(5):
            try:
                shutil.rmtree(folder)
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.2)
        shutil.rmtree(folder, ignore_errors=True)

    # --- the record kept with the stage ---------------------------------------------------------------------------

    def _stage_folder(self, stage_id: str) -> Path:
        return self._services.offices._stage_folder(stage_id)

    def _stage_fingerprint(self, stage_id: str) -> str | None:
        try:
            metadata = json.loads((self._stage_folder(stage_id) / "stage.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, ServiceError):
            return None
        return metadata.get("fingerprint") if isinstance(metadata, dict) else None

    def _save(self, stage_id: str, record: dict[str, Any]) -> None:
        stage = self._stage_folder(stage_id)
        temporary = stage / (RECORD + ".tmp")
        temporary.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(temporary, stage / RECORD)

    def last(self, stage_id: str) -> dict[str, Any] | None:
        """The last finished rehearsal of this stage, only if it was made for the stage's current fingerprint."""
        try:
            record = json.loads((self._stage_folder(stage_id) / RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError, ServiceError):
            return None
        if (not isinstance(record, dict) or record.get("stage_id") != stage_id
                or record.get("fingerprint") != self._stage_fingerprint(stage_id)):
            return None
        return record

    def summary(self, stage_id: str) -> dict[str, Any] | None:
        record = self.last(stage_id)
        if record is None:
            return None
        return {"state": record["state"], "at": record["at"],
                "flows": [{"area": flow["area"], "workflow": flow["workflow"], "status": flow["status"]}
                          for flow in record["flows"]],
                "files": len(record["files"])}

    def require_done(self, stage_id: str, root: Path, definition: Any, fingerprint: str) -> None:
        """The guard of ``install_staged``: a package whose tools write files is installed only after a rehearsal that
        ended for this same stage and fingerprint. A rehearsal that failed counts: its result is on the card."""
        files = read_package_files(root)
        if not writes_files(self._services.office_package_chat.tool_rows(files, definition)):
            return
        record = self.last(stage_id)
        if record is None or record.get("fingerprint") != fingerprint or record.get("state") not in {"finished", "failed"}:
            raise ServiceError(REHEARSAL_REQUIRED, "Try this office in Mock before installing it: its tools write files",
                               status=409, details={"reason": "office_rehearsal_required", "stage_id": stage_id})

    # --- the rehearsal --------------------------------------------------------------------------------------------

    @service_errors
    def run(self, stage_id: str, *, area: str | None = None, workflow: str | None = None,
            actor: str = "user:gui") -> dict[str, Any]:
        """Rehearse the flows of a staged package (all of them up to ``MAX_FLOWS``, or the one named). Installs
        nothing and leaves no run in the person's list."""
        from .projects import _user_actor

        actor = _user_actor(actor)
        for name, value in (("area", area), ("workflow", workflow)):
            if value is not None and not isinstance(value, str):
                raise ServiceError(INVALID_REQUEST, f"{name} must be text", status=422, details={"field": name})
        material = self._services.offices.stage_material(stage_id)
        flows = self._services.office_package_chat.flows(material["files"], material["definition"])
        if not flows:
            raise ServiceError(INVALID_REQUEST, "The package has no workflow to rehearse", status=422,
                               details={"reason": "office_rehearsal_no_workflows"})
        selected = [doc for doc in flows
                    if (area is None or doc.area == area) and (workflow is None or doc.workflow == workflow)]
        if not selected:
            raise ServiceError(INVALID_REQUEST, "That area or workflow is not in the package", status=422,
                               details={"reason": "office_rehearsal_unknown_flow"})
        if not self._lock.acquire(blocking=False):
            raise ServiceError(INVALID_STATE, "Another rehearsal is running", status=409,
                               details={"reason": "office_rehearsal_busy"})
        try:
            record = self._rehearse(stage_id, material, selected[:MAX_FLOWS], len(selected), actor)
            self._save(stage_id, record)
            return record
        finally:
            self._lock.release()

    def _rehearse(self, stage_id: str, material: dict[str, Any], selected: list[Any], total: int,
                  actor: str) -> dict[str, Any]:
        from . import ApplicationServices

        definition, files, root = material["definition"], material["files"], material["root"]
        started = time.monotonic()
        folder: Path | None = None
        services: ApplicationServices | None = None
        flows: list[dict[str, Any]] = []
        written: dict[str, int] = {}
        state, reason, error_type = "finished", "", ""
        try:
            base = self._base()
            if in_file_tools_root(base, self._services.settings.file_tools_root):
                raise ServiceError(INVALID_STATE, "The temporary folder is inside the work folder", status=409,
                                   details={"reason": "office_rehearsal_folder_unsafe"})
            base.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix=PREFIX, dir=base))
            work = folder / "work"
            work.mkdir()
            bundles: dict[str, Path] = {}
            for item in definition.areas:
                target = folder / "areas" / item.id
                # No Python of the package is copied: none of it can be imported by the rehearsal.
                shutil.copytree(root.joinpath(*item.path.split("/")), target,
                                ignore=shutil.ignore_patterns("*.py", "__pycache__"))
                bundles[item.id] = target
            names = {area_id: area_bundle_name(definition.id, area_id) for area_id in bundles}
            settings = _RehearsalSettings(
                local_dir=folder / "local", worker_enabled=False, mock_mode=True, bundle_scan="off",
                gemini_api_key="", openai_api_key="", openai_enabled=False, gui_token_file=folder / "gui-token",
                file_tools_root=work)
            services = ApplicationServices.create(
                settings, bundles={names[area_id]: path for area_id, path in bundles.items()}, dry_run=True)
            deadline = started + self.limit_seconds
            for doc in selected:
                before = _snapshot(work)
                flow = self._flow(services, doc, files, names[doc.area], work, folder, deadline)
                after = _snapshot(work)
                flow["files"] = [{"path": path, "size": size} for path, (size, _) in sorted(after.items())
                                 if before.get(path, (None, None))[1] != after[path][1]][:FILES_SHOWN]
                flows.append(flow)
                if flow["status"] in {"failed", "timed_out"}:
                    state = "failed"
            written = {path: size for path, (size, _) in _snapshot(work).items()}
        except ServiceError:
            raise
        except Exception as exc:
            state, reason, error_type = "failed", "rehearsal_error", type(exc).__name__
        finally:
            if services is not None:
                services.bundle_scans.close()
                services.runs.close()
            if folder is not None:
                self._delete(folder)
        return {"stage_id": stage_id, "fingerprint": material["metadata"]["fingerprint"], "state": state,
                "reason": reason, "error_type": error_type, "at": utcnow(), "actor": actor,
                "duration_ms": int((time.monotonic() - started) * 1000), "flows": flows,
                "flows_total": total, "flows_omitted": max(0, total - len(selected)),
                "files": [{"path": path, "size": size} for path, size in sorted(written.items())][:FILES_SHOWN],
                "folder_deleted": folder is None or not folder.exists(), "notice": NOTICE}

    @staticmethod
    def _clean(text: Any, folder: Path) -> str:
        shown = str(text)
        for variant in {str(folder), str(folder.resolve()), folder.as_posix(), folder.resolve().as_posix()}:
            shown = shown.replace(variant, "<rehearsal>")
        return shown[:300]

    @staticmethod
    def _not_rehearsed(flow: dict[str, Any], doc: Any, reason: str) -> dict[str, Any]:
        flow["status"], flow["reason"] = "not_rehearsed", reason
        flow["steps"] = [{"id": step.get("id"), "state": "not_rehearsed",
                          "reason": reason if step.get("function") is not None else "flow_not_run"}
                         for step in doc.steps]
        return flow

    def _flow(self, services: Any, doc: Any, files: dict[str, bytes], bundle: str, work: Path, folder: Path,
              deadline: float) -> dict[str, Any]:
        used = flow_inputs(files, doc)
        cross = cross_check_inputs(files, [doc])[0]
        names = sorted(name for name in {*doc.declared, *used["placeholders"]}
                       if name.isidentifier() and name[0].isalpha() and not name.endswith("_output"))
        flow: dict[str, Any] = {"area": doc.area, "workflow": doc.workflow, "status": "", "reason": "", "steps": [],
                                "inputs": {"synthetic": names, "missing": cross["missing"]}, "tools": [],
                                "files": [], "error": None}
        if doc.invalid:
            return self._not_rehearsed(flow, doc, "workflow_unreadable")
        untrusted = (f"{doc.base}/contracts.py" in files
                     or f"{doc.base}/functions.py" in files and any(step.get("function") is not None
                                                                    for step in doc.steps))
        if untrusted:
            return self._not_rehearsed(flow, doc, "untrusted_code")
        try:
            if (any(step.get("role") for step in doc.steps) and f"{doc.base}/team.yaml" in files
                    and services.teams.approved(bundle) is None):      # a second flow of the area reuses its team
                version = services.teams.propose(bundle, actor=f"agent:{_ACTOR}", channel=_ACTOR)
                services.teams.approve(version["id"], actor=f"user:{_ACTOR}", channel=_ACTOR)
            run = services.runs.create(
                RunRequest(project_name="Mock rehearsal", workflow_id=doc.workflow,
                           inputs={name: f"Sample {name}" for name in names}), bundle=bundle)
            state = self._drive(services, run["id"], bundle, work, deadline)
        except ServiceError as exc:
            flow.update(status="failed", reason="run_refused",
                        error={"code": exc.code, "reason": exc.details.get("reason"),
                               "message": self._clean(exc.message, folder)})
            flow["steps"] = [{"id": step.get("id"), "state": "not_run"} for step in doc.steps]
            return flow
        events = state["events"]
        done = {event["payload"].get("step_id") for event in events if event["event_type"] == "step_completed"}
        skipped = {event["payload"].get("step_id") for event in events if event["event_type"] == "step_skipped"}
        calls = [{"tool": event["payload"].get("tool_id"), "status": event["event_type"].removeprefix("tool_call_")}
                 for event in events if event["event_type"] in {"tool_call_completed", "tool_call_failed",
                                                                 "tool_call_denied"}]
        # a call held for approval is "denied" until it is approved: keep it only if nothing followed for that tool
        flow["tools"] = [call for index, call in enumerate(calls) if call["status"] != "denied"
                         or not any(later["tool"] == call["tool"] for later in calls[index + 1:])]
        status = state["status"]
        flow["status"] = ("completed" if status == "completed" else "timed_out" if state.get("timed_out")
                          else "not_rehearsed" if state.get("needs_person") else "failed")
        failed_seen = False
        for step in doc.steps:
            step_id = step.get("id")
            if step_id in done:
                mark = "ran"
            elif step_id in skipped:
                mark = "skipped"
            elif flow["status"] == "completed":
                mark = "not_run"
            elif not failed_seen:
                failed_seen = True
                mark = "not_rehearsed" if flow["status"] == "not_rehearsed" else "failed"
            else:
                mark = "not_run"
            flow["steps"].append({"id": step_id, "state": mark})
        if flow["status"] == "not_rehearsed":
            flow["reason"] = "needs_a_person"
        elif flow["status"] == "timed_out":
            flow["reason"] = "time_limit"
        elif flow["status"] == "failed":
            failure = next((event["payload"] for event in reversed(events)
                            if event["event_type"] in {"workflow_failed", "tool_call_failed"}), {})
            flow["reason"] = state.get("rejected") or "run_failed"
            flow["error"] = {"code": str(failure.get("type") or failure.get("code") or ""),
                             "message": self._clean(failure.get("error") or failure.get("message") or "", folder)}
        return flow

    def _drive(self, services: Any, run_id: str, bundle: str, work: Path, deadline: float) -> dict[str, Any]:
        """Process the run until it ends. A review is approved (it is the rehearsal's own throwaway run), a write is
        approved only inside the rehearsal's work folder, and anything that needs a person stops the flow."""
        runtime = services.runs._bundle_runtime(bundle)
        state: dict[str, Any] = services.runs.get(run_id)
        for _ in range(MAX_TURNS):
            if time.monotonic() > deadline:
                services.runs.cancel(run_id, force=True)
                return {**services.runs.get(run_id), "timed_out": True}
            runtime.worker.process_once()
            state = services.runs.get(run_id)
            if state["status"] in {"completed", "failed", "cancelled", "rejected", "interrupted"}:
                return state
            if state["status"] != "waiting_human":
                continue
            reason = state.get("waiting_reason") or ""
            if reason == "tool_approval":
                pending = state.get("tool_approval") or {}
                actions = pending.get("actions") or []
                # An empty list or a truncated one (``actions_total`` larger than what is listed) is never approved.
                if (pending and actions and pending.get("actions_total", len(actions)) == len(actions)
                        and all(_inside(work, action.get("target")) for action in actions)):
                    services.runs.approve(run_id, by=_ACTOR, channel=_ACTOR, person=True,
                                          permission_request_id=pending["permission_request_id"])
                else:
                    services.runs.reject(run_id, "A write outside the rehearsal folder was refused",
                                         by=_ACTOR, channel=_ACTOR)
                    return {**services.runs.get(run_id), "rejected": "write_outside_rehearsal_folder"}
            elif reason in {"review", "approval_pending", ""}:
                services.runs.approve(run_id, by=_ACTOR, channel=_ACTOR)
            else:
                return {**state, "needs_person": True}
        return {**state, "timed_out": True}
