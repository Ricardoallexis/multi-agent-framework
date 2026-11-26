# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Activate an executor from the GUI, on the server (EJE-T01): the fingerprint, the local setting and the pause.

Only a person turns an executor on, one at a time, after reading a fixed note and with the one-use code of the
approvals (``executor_activate``). The setting is a local file, ``<local_dir>/executors-activados.json``; the catalog
keeps every executor off and ``apply_to_config`` turns on the active ones when the registry loads. Nothing here starts,
detects on its own or enables an application: ``fingerprint`` asks the same detector as "Connections" (a fixed
``--version`` and the status command of each CLI) and never reads a token.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import INVALID_STATE, NOT_FOUND, ServiceError

if TYPE_CHECKING:
    from . import ApplicationServices

ACTIVATIONS_FILE = "executors-activados.json"
ACTIVATIONS_SCHEMA = "maf.executor-activations/1"
KILL_MARKER = "maf.kill-switch/1"
NOTICE_IDS = {"antigravity_cli": "agy-third-party-notice/1"}
EXECUTOR_IDS = ("codex_cli", "claude_cli", "copilot_cli", "antigravity_cli")
STATES = ("not_installed", "no_session", "ready", "active", "paused")
APPROVAL_KIND = "executor_activate"
NOTE_ID = "executor-activation-note/1"
ACTIVATED_EVENT = "executor.activated"
DEACTIVATED_EVENT = "executor.deactivated"
USER_ACTOR = "user:gui"
SYSTEM_ACTOR = "system:executor-activations"
CHANNEL = "gui"
# the executor ids and the app names of the detector (``setup/detect.py``)
_APPS = {"codex_cli": "codex", "claude_cli": "claude", "copilot_cli": "copilot", "antigravity_cli": "agy"}
_LABELS = {"codex_cli": "Codex", "claude_cli": "Claude", "copilot_cli": "Copilot", "antigravity_cli": "Antigravity (agy)"}
_MAJOR = re.compile(r"v?(\d+)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conflict(reason: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_STATE, message, status=409, details={"reason": reason, **details})


def _unknown(executor_id: str) -> ServiceError:
    return ServiceError(NOT_FOUND, f"Executor not found: {executor_id}", status=404, details={"executor": executor_id})


def _major(version: Any) -> str | None:
    match = _MAJOR.match(version.strip()) if isinstance(version, str) else None
    return match.group(1) if match else None


def _same_path(path: str | None) -> str | None:
    return os.path.normcase(os.path.realpath(path)) if path else None


def _digest(path: str | None, major: str | None, account: str | None) -> str:
    return hashlib.sha256(json.dumps({"path": path, "major": major, "account": account}, sort_keys=True,
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def compare(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What changed from the activated fingerprint to the current one (rule 5), as ``[{"field", "from", "to"}]``.

    Only a different ``path``, a different ``major`` or a different ``account`` counts, the last two only when both
    sides have a value: a new minor version does not, nor an account the CLI stops reporting (Review focus 5).
    """
    changes: list[dict[str, Any]] = []
    if old.get("path") != new.get("path"):
        changes.append({"field": "path", "from": old.get("path"), "to": new.get("path")})
    for field in ("major", "account"):
        before, after = old.get(field), new.get(field)
        if before is not None and after is not None and before != after:
            changes.append({"field": field, "from": before, "to": after})
    return changes


def note_text(executor_id: str, fingerprint: Mapping[str, Any]) -> str:
    """The fixed note the person reads before "Activate" (rule 3). Only the application and the account vary."""
    label = _LABELS[executor_id]
    account = fingerprint.get("account")
    who = (f"the account {account}" if account else "the account you signed in with in that application")
    lines = [
        f"You are about to activate {label}: the framework will be able to launch "
        f"the application installed on {fingerprint.get('path') or 'your computer'} with {who}.",
        "Each launch uses your subscription and quota, not the framework's.",
        "The agent reads and writes in each task's working directory.",
        "The application's provider terms apply.",
        "Usage limits, permissions and privacy do not change.",
        "If the path, major version or account changes, activation is paused and you will be asked again. "
        "A provider's change to where it stores data cannot be detected.",
        "You can deactivate it at any time, or turn off all executors at once.",
    ]
    if executor_id == "antigravity_cli":
        lines.insert(4, "You accept Antigravity's third-party notice (" + NOTICE_IDS[executor_id] + "): it is an "
                        "application from another provider, and its use from other programs may be limited. "
                        "AI credits are never used.")
    return "\n".join(lines)


class ExecutorActivationServices:
    """``services.executor_activations``: the local setting of the executors a person turned on."""

    def __init__(self, services: ApplicationServices, *, detector: Callable[..., dict] | None = None):
        self._services = services
        self._detector = detector
        self._lock = threading.RLock()

    # -------- inputs --------
    @property
    def detector(self) -> Callable[..., dict]:
        """The detector of "Connections" (``setup/detect.py``); tests replace it with a fake one."""
        if self._detector is not None:
            return self._detector
        return self._services.connections.detector

    @detector.setter
    def detector(self, value: Callable[..., dict] | None) -> None:
        self._detector = value

    @property
    def _dir(self) -> Path:
        return Path(self._services.settings.local_dir)

    @property
    def _file(self) -> Path:
        return self._dir / ACTIVATIONS_FILE

    @property
    def _store(self):
        return self._services.tasks.store

    @staticmethod
    def _check(executor_id: str) -> str:
        if executor_id not in EXECUTOR_IDS:
            raise _unknown(executor_id)
        return executor_id

    # -------- the local file --------
    def _read(self) -> dict[str, dict[str, Any]]:
        """The valid entries of the file; a missing or broken file reads as nothing activated."""
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        entries = raw.get("executors") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for executor_id in EXECUTOR_IDS:
            entry = entries.get(executor_id)
            if isinstance(entry, dict) and isinstance(entry.get("fingerprint"), dict):
                paused = entry.get("paused")
                result[executor_id] = {**entry, "paused": paused if isinstance(paused, dict) else None}
        return result

    def _write(self, entries: Mapping[str, Mapping[str, Any]]) -> None:
        """Atomic: a temporary file in the same folder, then ``os.replace``. No ``.tmp`` is left behind."""
        folder = self._dir
        folder.mkdir(parents=True, exist_ok=True)
        content = {"schema": ACTIVATIONS_SCHEMA, "executors": {key: dict(value) for key, value in entries.items()}}
        handle, name = tempfile.mkstemp(prefix=ACTIVATIONS_FILE + ".", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(content, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(name, self._file)
        except BaseException:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise

    def _changed(self) -> None:
        """Every change reloads the registry on its next use, so the catalog setting takes effect at once."""
        self._services.assignment.reset_registry()

    def _event(self, event_type: str, executor_id: str, fingerprint: Mapping[str, Any], *, actor: str, reason: str) -> None:
        payload = {"executor_id": executor_id, "major": fingerprint.get("major"), "account": fingerprint.get("account"),
                   "channel": CHANNEL, "reason": reason}
        with self._store.transaction() as conn:
            self._store.append_event(conn, event_type, actor=actor, ref=executor_id, payload=payload)

    # -------- the fingerprint --------
    def _observe(self, executor_id: str, report: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """``{fingerprint, installed, session}`` from the detector. A detector that fails reads as not installed."""
        app = _APPS[executor_id]
        try:
            observed = report if report is not None else self.detector(
                cwd=Path.cwd(), context="packaged_process", include_antigravity=True)
            apps = observed["contexts"]["packaged_process"].get("apps", [])
            fact = next((item for item in apps if item.get("app") == app), {})
        except Exception:  # noqa: BLE001 - nothing about the failure leaves this method
            fact = {}
        installed = fact.get("detection") == "detected"
        path = account = major = None
        if installed:
            found = fact.get("path") if isinstance(fact.get("path"), str) else shutil.which(app)
            path = _same_path(found)
            major = _major(fact.get("version"))
            reported = fact.get("account")
            account = reported if isinstance(reported, str) and reported.strip() else None
        session = (fact.get("session") or {}).get("state", "unknown") if installed else "unknown"
        return {"fingerprint": {"path": path, "major": major, "account": account,
                                "sha256": _digest(path, major, account)},
                "installed": installed, "session": session}

    def fingerprint(self, executor_id: str) -> dict[str, Any]:
        """``{path, major, account, sha256}`` of the application today. Nothing reads a token."""
        return self._observe(self._check(executor_id))["fingerprint"]

    # -------- reading --------
    def _kill_path(self) -> Path | None:
        path = getattr(self._services.settings, "antigravity_kill_switch", None)
        return Path(path) if path else None

    def _kill_state(self) -> str:
        """``none``, ``marked`` (the framework wrote it) or ``foreign`` (a person did, or it is not a plain file)."""
        path = self._kill_path()
        if path is None or not os.path.lexists(path):
            return "none"
        try:
            with open(path, encoding="utf-8") as stream:
                first = stream.readline().strip()
        except (OSError, UnicodeDecodeError):
            return "foreign"
        return "marked" if first == KILL_MARKER else "foreign"

    def status(self) -> list[dict[str, Any]]:
        entries = self._read()
        # A status refresh must expose drift even when no new launch was attempted.
        for executor_id, entry in entries.items():
            if entry["paused"] is None:
                try:
                    self.check_launch(executor_id)
                except ServiceError as exc:
                    if exc.details.get("reason") not in {"executor_paused_changed", "executor_stopped_by_person"}:
                        raise
        entries = self._read()
        report: Mapping[str, Any] | None = None
        if any(executor_id not in entries for executor_id in EXECUTOR_IDS):
            try:
                report = self.detector(cwd=Path.cwd(), context="packaged_process", include_antigravity=True)
            except Exception:  # noqa: BLE001
                report = {}
        foreign = self._kill_state() == "foreign"
        rows = []
        for executor_id in EXECUTOR_IDS:
            entry = entries.get(executor_id)
            session = "unknown"
            if entry is not None:
                state = "paused" if entry["paused"] else "active"
            else:
                seen = self._observe(executor_id, report)
                session = seen["session"]
                state = ("not_installed" if not seen["installed"] else "no_session" if session == "signed_out"
                         else "ready")
            paused = entry["paused"] if entry else None
            rows.append({"id": executor_id, "state": state, "session": session,
                         "activated_at": entry.get("at") if entry else None,
                         "activated_by": entry.get("by") if entry else None,
                         "paused": paused, "changes": list(paused.get("changes", [])) if paused else [],
                         "kill_switch_foreign": foreign and executor_id == "antigravity_cli"})
        return rows

    def active_ids(self) -> list[str]:
        """The executors turned on and not paused, in the fixed order."""
        entries = self._read()
        return [executor_id for executor_id in EXECUTOR_IDS if executor_id in entries and not entries[executor_id]["paused"]]

    def notice_accepted(self, executor_id: str) -> str | None:
        """The notice the person accepted in "Activate" (agy), only while the executor is active."""
        if executor_id in self.active_ids():
            return self._read()[executor_id].get("notice") or None
        return None

    def apply_to_config(self, config: Any) -> Any:
        """A copy of the executor routing config with ``enabled: true`` for the active executors; the catalog is not touched."""
        if not isinstance(config, Mapping):
            return config
        result = copy.deepcopy(dict(config))
        declared = result.get("executors")
        if isinstance(declared, dict):
            for executor_id in self.active_ids():
                if isinstance(declared.get(executor_id), dict):
                    declared[executor_id]["enabled"] = True
        return result

    # -------- Activate --------
    def request(self, executor_id: str) -> dict[str, Any]:
        """Create, or reuse when one is pending for the same fingerprint, the ``executor_activate`` approval."""
        self._check(executor_id)
        seen = self._observe(executor_id)
        if not seen["installed"]:
            raise _conflict("not_installed", f"{_LABELS[executor_id]} is not installed", executor_id=executor_id)
        if seen["session"] == "signed_out":
            raise _conflict("no_session", f"{_LABELS[executor_id]} has no open session", executor_id=executor_id)
        fingerprint = seen["fingerprint"]
        destination = {"executor": executor_id}
        approvals = self._services.user_approvals
        with self._lock:
            found = approvals.pending_like(APPROVAL_KIND, sha256=fingerprint["sha256"], destination=destination)
            if found is None:
                found = approvals.request(kind=APPROVAL_KIND, destination=destination,
                                          location={"source": "executor_activation", "executor": executor_id},
                                          sha256=fingerprint["sha256"])
        return {"approval_id": found["id"], "sha256": fingerprint["sha256"], "note_id": NOTE_ID,
                "note": note_text(executor_id, fingerprint),
                "fingerprint": {key: fingerprint[key] for key in ("path", "major", "account")}}

    def activate(self, executor_id: str, approval_id: str) -> dict[str, Any]:
        """Turn the executor on. Without an approved, unspent approval of this note it is ``person_required``."""
        self._check(executor_id)
        approvals = self._services.user_approvals
        destination = {"executor": executor_id}
        item = approvals.peek(approval_id) if isinstance(approval_id, str) and approval_id else None
        if (item is None or item.get("kind") != APPROVAL_KIND or item.get("state") != "approved"
                or not approvals.consume(approval_id, sha256=item["sha256"], destination=destination)):
            raise _conflict("person_required", 'Activating an executor needs a person: use "Activate" in the GUI',
                            executor_id=executor_id)
        fingerprint = self.fingerprint(executor_id)
        if fingerprint["sha256"] != item["sha256"]:      # the application changed between the note and the click
            raise _conflict("executor_paused_changed", f"{_LABELS[executor_id]} changed after the note was shown",
                            executor_id=executor_id)
        with self._lock:
            entries = self._read()
            entries[executor_id] = {"fingerprint": fingerprint, "by": USER_ACTOR, "at": _now(), "channel": CHANNEL,
                                    "notice": NOTICE_IDS.get(executor_id), "paused": None}
            self._write(entries)
            if executor_id == "antigravity_cli" and self._kill_state() == "marked":
                try:      # only a switch the framework wrote; one a person made by hand stays
                    os.unlink(self._kill_path())
                except OSError:
                    pass
        self._changed()
        self._event(ACTIVATED_EVENT, executor_id, fingerprint, actor=USER_ACTOR, reason="by_person")
        return self._row(executor_id)

    def deactivate(self, executor_id: str) -> dict[str, Any]:
        """Remove the activation at once. Turning off is the safe direction: the GUI token is enough."""
        self._check(executor_id)
        with self._lock:
            entries = self._read()
            entry = entries.pop(executor_id, None)
            if entry is None:
                raise _conflict("not_active", f"{_LABELS[executor_id]} is not activated", executor_id=executor_id)
            self._write(entries)
        self._changed()
        self._event(DEACTIVATED_EVENT, executor_id, entry["fingerprint"], actor=USER_ACTOR, reason="by_person")
        return self._row(executor_id)

    def stop_all(self) -> dict[str, Any]:
        """"Turn all off": stop what runs, pause every activation and write the agy kill switch."""
        paused: list[str] = []
        with self._lock:
            entries = self._read()
            for executor_id, entry in entries.items():
                if entry["paused"] is None:
                    entry["paused"] = {"reason": "stopped_by_person", "changes": [], "at": _now()}
                    paused.append(executor_id)
            if paused:
                self._write(entries)
        self._changed()
        for executor_id in paused:
            self._event(DEACTIVATED_EVENT, executor_id, entries[executor_id]["fingerprint"], actor=USER_ACTOR,
                        reason="stopped_by_person")
        background = self._services.executors.background
        stopped = [item["task_id"] for item in background.active() if background.cancel(item["task_id"])]
        kill = self._write_kill_switch()
        return {"stopped_tasks": stopped, "paused": paused, "kill_switch": kill}

    def _write_kill_switch(self) -> str:
        """``written``, ``foreign`` (a person's own switch stays as it is) or ``failed``."""
        path = self._kill_path()
        if path is None:
            return "failed"
        state = self._kill_state()
        if state == "foreign":
            return "foreign"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(KILL_MARKER + "\n", encoding="utf-8")
        except OSError:
            return "failed"
        return "written"

    def _row(self, executor_id: str) -> dict[str, Any]:
        entries = self._read()
        entry = entries.get(executor_id)
        paused = entry["paused"] if entry else None
        return {"id": executor_id, "state": ("paused" if paused else "active") if entry else "ready",
                "activated_at": entry.get("at") if entry else None, "activated_by": entry.get("by") if entry else None,
                "paused": paused, "changes": list(paused.get("changes", [])) if paused else [],
                "kill_switch_foreign": self._kill_state() == "foreign" and executor_id == "antigravity_cli"}

    # -------- every launch (rule 5) --------
    def check_launch(self, executor_id: str) -> None:
        """Called before a launch. An executor the person did not activate here is not this service's business."""
        with self._lock:
            entry = self._read().get(executor_id)
        if entry is None:
            return
        paused = entry["paused"]
        if paused is not None:
            reason = "executor_paused_changed" if paused.get("reason") == "changed" else "executor_stopped_by_person"
            raise _conflict(reason, f"{_LABELS[executor_id]} is paused: activate it again in the GUI",
                            executor_id=executor_id, changes=paused.get("changes", []))
        current = self.fingerprint(executor_id)
        changes = compare(entry["fingerprint"], current)
        if not changes:
            return
        with self._lock:
            entries = self._read()
            if executor_id in entries and entries[executor_id]["paused"] is None:
                entries[executor_id]["paused"] = {"reason": "changed", "changes": changes, "at": _now()}
                self._write(entries)
        self._changed()
        self._event(DEACTIVATED_EVENT, executor_id, current, actor=SYSTEM_ACTOR, reason="changed")
        raise _conflict("executor_paused_changed", f"{_LABELS[executor_id]} changed: activate it again in the GUI",
                        executor_id=executor_id, changes=changes)
