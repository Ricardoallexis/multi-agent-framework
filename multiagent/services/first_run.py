# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The five steps of "Home" and their times (INI-T02, design §7.7 and L13). Server side only.

Every step is computed from the real state, never from a click:

* ``install``: ``installed.list()`` has an entry with ``state: "ok"`` (the ``shipped`` example too);
* ``try_mock``: a ``completed`` run created by this process, in Mock, whose document can be rendered;
* ``connect``: ``connections.list()`` has an available row that is not a stand-in (rows only; no token is read);
* ``activate``: ``executor_activations.active_ids()`` is not empty;
* ``real_run``: a ``completed`` run created by this process with real models, with its document.

A step that was done stays done: the first time it is seen done, its hour is written to
``<local_dir>/primer-recorrido.json`` (atomic write). The file never leaves the computer: it is not an
``/api/v1`` resource and no export carries it. The clock starts at ``primer-recorrido-inicio.txt`` (written by the
launcher, INI-T01) or, when that file is missing, at the first ``status()``.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .executor_activations import EXECUTOR_IDS
from .errors import ServiceError

if TYPE_CHECKING:
    from . import ApplicationServices

FIRST_RUN_FILE = "primer-recorrido.json"
FIRST_RUN_SCHEMA = "maf.first-run/1"
START_FILE = "primer-recorrido-inicio.txt"
STEPS = ("install", "try_mock", "connect", "activate", "real_run")
STEP_TITLES = {
    "install": "Install an office",
    "try_mock": "Try Mock and view the document",
    "connect": "Connect an agent",
    "activate": "Activate it",
    "real_run": "Run with your data and save the document",
}
RESTART_TEXT = "Close this window and reopen the Iniciar launcher"
_RUN_LIMIT = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    """An ISO hour as an aware UTC datetime, or ``None``. A naive hour is read as UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


class FirstRunServices:
    """``services.first_run``: the steps of "Home", their hours, and the switch between Mock and real models."""

    def __init__(self, services: ApplicationServices, *, clock: Callable[[], datetime] = _now,
                 started_at: str | None = None):
        self._services = services
        self._clock = clock
        # The start of this process, with its full precision: a run of the second before it must not count.
        self.started_at: str = started_at or clock().astimezone(timezone.utc).isoformat()
        self._lock = threading.Lock()

    # ---- the file ------------------------------------------------------------------------------------------

    @property
    def _dir(self) -> Path:
        return Path(self._services.settings.local_dir)

    def _read(self) -> dict[str, Any]:
        """``{"steps": {id: iso}, "install_started_at": iso | None}``; a missing or broken file reads as empty."""
        try:
            data = json.loads((self._dir / FIRST_RUN_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"steps": {}, "install_started_at": None}
        if not isinstance(data, dict):
            return {"steps": {}, "install_started_at": None}
        raw = data.get("steps") if isinstance(data.get("steps"), dict) else {}
        steps = {step: raw[step]["done_at"] for step in STEPS
                 if isinstance(raw.get(step), dict) and _parse(raw[step].get("done_at")) is not None}
        started = data.get("install_started_at")
        return {"steps": steps, "install_started_at": started if _parse(started) is not None else None}

    def _write(self, steps: dict[str, str], install_started_at: str | None) -> None:
        """Atomic: a temporary file in the same folder, then ``os.replace``. No ``.tmp`` is left behind."""
        folder = self._dir
        folder.mkdir(parents=True, exist_ok=True)
        content: dict[str, Any] = {"schema": FIRST_RUN_SCHEMA,
                                   "steps": {step: {"done_at": steps[step]} for step in STEPS if step in steps}}
        if install_started_at:
            content["install_started_at"] = install_started_at
        handle, name = tempfile.mkstemp(prefix=FIRST_RUN_FILE + ".", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(content, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(name, folder / FIRST_RUN_FILE)
        except BaseException:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise

    def _launcher_start(self) -> str | None:
        """The hour the launcher wrote (it may carry a BOM when a Windows shell wrote it), or ``None``."""
        try:
            text = (self._dir / START_FILE).read_text(encoding="utf-8-sig")
        except (OSError, ValueError):
            return None
        moment = _parse(text.splitlines()[0] if text.strip() else "")
        return _iso(moment) if moment else None

    # ---- the checks ----------------------------------------------------------------------------------------

    def _check_install(self) -> bool:
        return any(entry.get("state") == "ok" for entry in self._services.installed.list())

    def _runs_with_document(self) -> bool:
        """A ``completed`` run of this process whose document renders."""
        since = _parse(self.started_at)
        for run in self._services.runs.list(limit=_RUN_LIMIT, status="completed"):
            created = _parse(run.get("created_at"))
            if created is None or since is None or created < since:
                continue
            try:
                self._services.runs.document(run["id"])
            except ServiceError:
                continue
            return True
        return False

    def _check_connect(self) -> bool:
        """An available connection row that is not a stand-in. Only the rows are read, never a token.

        ``connections.list()`` has no row for Mock itself. The one stand-in it can show is a provider whose
        adapter is the product's ``FakeAdapter`` (the double of Mock): that row never counts.
        """
        from ..adapters.fake import FakeAdapter

        adapters = getattr(getattr(self._services, "system", None), "adapters", None) or {}
        for row in self._services.connections.list().get("connections", []):
            if row.get("available") is True and not isinstance(adapters.get(row.get("id")), FakeAdapter):
                return True
        return False

    def _check_activate(self) -> bool:
        return bool(self._services.executor_activations.active_ids())

    def _activate_applicable(self) -> bool:
        """Only a detected CLI executor makes activation a first-run action."""
        rows = self._services.connections.list().get("connections", [])
        return any(row.get("id") in EXECUTOR_IDS and row.get("installed") is True for row in rows)

    def _process_is_mock(self) -> bool:
        return bool(self._services.settings.mock_mode)

    def _checks(self) -> dict[str, Callable[[], bool]]:
        mock = self._process_is_mock()
        return {
            "install": self._check_install,
            "try_mock": lambda: mock and self._runs_with_document(),
            "connect": self._check_connect,
            "activate": self._check_activate,
            "real_run": lambda: not mock and self._runs_with_document(),
        }

    # ---- the public surface --------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            stored = self._read()
            steps = dict(stored["steps"])
            first_seen = _iso(self._clock())
            changed = False
            for step, check in self._checks().items():
                if step in steps:
                    continue
                try:
                    done = bool(check())
                except Exception:      # a failing check reads as not done: the screen must still open
                    done = False
                if done:
                    steps[step] = _iso(self._clock())
                    changed = True
            start = self._launcher_start() or stored["install_started_at"]
            persisted_start = stored["install_started_at"]
            if start is None:
                start = persisted_start = first_seen
                changed = True
            if changed:
                self._write(steps, persisted_start)
            return self._view(steps, start)

    def _view(self, steps: dict[str, str], start: str) -> dict[str, Any]:
        begun = _parse(start)
        try:
            activate_applicable = self._activate_applicable()
        except Exception:  # a failed check must not silently dismiss a step
            activate_applicable = True
        listing = [{"id": step, "title": STEP_TITLES[step], "done": step in steps, "done_at": steps.get(step),
                    "applicable": step != "activate" or step in steps or activate_applicable}
                   for step in STEPS]
        times = []
        for step in STEPS:
            if step not in steps:
                continue
            moment = _parse(steps[step])
            minutes = max(0.0, round((moment - begun).total_seconds() / 60, 1)) if moment and begun else None
            times.append({"id": step, "done_at": steps[step], "minutes": minutes})
        mock = self._process_is_mock()
        return {"schema": FIRST_RUN_SCHEMA, "steps": listing,
                "pending": [item["id"] for item in listing if not item["done"] and item["applicable"]],
                "all_done": all(item["done"] or not item["applicable"] for item in listing),
                "mock": mock, "models": "mock" if mock else "real",
                "offer_real": "try_mock" in steps and mock,
                "install_started_at": start, "times": times}

    def times_text(self, status: dict[str, Any] | None = None) -> str:
        """The hours as plain text to copy. The observer subtracts the downloads with their own stopwatch (L13).

        Pass the ``status()`` already in hand to avoid computing it twice."""
        status = status if status is not None else self.status()
        lines = [f"Start: {status['install_started_at']}"]
        done = {item["id"]: item for item in status["times"]}
        not_applicable = {item["id"] for item in status["steps"] if item.get("applicable") is False}
        for step in STEPS:
            if step in done:
                minutes = done[step]["minutes"]
                lines.append(f"{STEP_TITLES[step]}: {done[step]['done_at']} ({minutes:g} min from start)")
            elif step in not_applicable:
                lines.append(f"{STEP_TITLES[step]}: not applicable")
            else:
                lines.append(f"{STEP_TITLES[step]}: pending")
        return "\n".join(lines)

    def set_models(self, models: str, *, actor: str = "user:gui", channel: str = "gui") -> dict[str, Any]:
        """Write ``models`` (``mock`` or ``real``); the change takes effect when the person opens Iniciar again.

        Whether to offer "Switch to real models" is the screen's call (``offer_real``); the server does not refuse
        either value, because only the next start is affected. A value outside the list is a 422
        (``GuiSettingsServices.set_value`` validates it)."""
        written = self._services.gui_settings.set_value("models", models, actor=actor, channel=channel)
        return {"models": written["models"], "restart_required": True, "text": RESTART_TEXT}
