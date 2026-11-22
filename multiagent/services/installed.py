# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Installed packages (PAQ-T01): a bundle copied under ``<data_dir>/installed/`` and registered at every start.

Installing copies the folder to ``installed/.staging/<uuid>/`` and then to ``installed/<name>/v<n>/``. The origin and the
copy must have the same fingerprint (``BundleScanServices.fingerprint``), so what runs is what was reviewed. A package
made only of data files (``package_level`` is ``data``) is validated whole before it enters the registry; one that has
code takes today's path: its first load goes through ``BundleScanServices.require_load``, tied to that fingerprint.
Removing keeps the copy and the run data. Nothing here returns an absolute path.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..db.database import utcnow
from ..teams import validate_team_workflows
from ..tools.files import _is_link_or_junction
from ..bundles import load_bundle
from ..workflow_validation import WorkflowDefinitionError, require_safe_workflow_id
from .bundle_scans import BundleScanServices
from .errors import INVALID_DEFINITION, INVALID_REQUEST, INVALID_STATE, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices

REGISTRY_SCHEMA = "maf.installed/1"
CODE_FREE_SUFFIXES = (".yaml", ".yml", ".md", ".txt", ".json", ".csv")
BUSY_STATUSES = ("queued", "running", "waiting_human", "paused")

STATE_OK, STATE_CHANGED, STATE_MISSING = "ok", "changed", "missing"
REASON_CHANGED = "installed_bundle_changed"
SOURCE_COPY, SOURCE_SHIPPED = "copy", "shipped"

_REGISTRY_FILE = "registry.json"
_STAGING = ".staging"


def _skipped(relative: Path) -> bool:
    # __pycache__ is not package content: the fingerprint leaves it out too.
    return "__pycache__" in relative.parts


def package_level(folder: Path) -> Literal["data", "code"]:
    """``data`` when every file has a suffix of ``CODE_FREE_SUFFIXES``; ``code`` otherwise (``.py`` or no suffix)."""
    folder = Path(folder)
    for path in folder.rglob("*"):
        if path.is_file() and not _skipped(path.relative_to(folder)) and path.suffix.lower() not in CODE_FREE_SUFFIXES:
            return "code"
    return "data"


def _conflict(message: str, reason: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_STATE, message, status=409, details={"reason": reason, **details})


def _invalid(message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details=details)


class InstalledServices:
    """The registry of installed packages of one facade. Entries live in ``<data_dir>/installed/registry.json``."""

    def __init__(self, services: ApplicationServices):
        self._services = services
        self._lock = threading.RLock()
        self._states: dict[str, str] = {}

    # --- registry file ------------------------------------------------------

    @property
    def _root(self) -> Path:
        return self._services.settings.data_dir / "installed"

    @property
    def _registry_path(self) -> Path:
        return self._root / _REGISTRY_FILE

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise ValueError(f"The installed packages registry ({_REGISTRY_FILE}) cannot be read") from None
        entries = data.get("bundles") if isinstance(data, dict) and data.get("schema") == REGISTRY_SCHEMA else None
        if not isinstance(entries, dict):
            raise ValueError(f"The installed packages registry ({_REGISTRY_FILE}) has an unknown format")
        return {name: entry for name, entry in entries.items() if isinstance(entry, dict)}

    def _write(self, entries: dict[str, dict[str, Any]]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        temporary = self._registry_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema": REGISTRY_SCHEMA, "bundles": entries},
                                        ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(self._registry_path)

    # --- reading ------------------------------------------------------------

    def _public(self, entry: dict[str, Any]) -> dict[str, Any]:
        state = self._states.get(entry["name"], STATE_OK)
        return {"name": entry["name"], "fingerprint": entry.get("fingerprint"), "level": entry.get("level"),
                "trust_functions": bool(entry.get("trust_functions", False)),
                "source_label": entry.get("source_label", ""), "version": entry.get("version"),
                "installed_at": entry.get("installed_at"), "installed_by": entry.get("installed_by"),
                "channel": entry.get("channel"), "source": entry.get("source", SOURCE_COPY),
                "state": state, "reason": REASON_CHANGED if state != STATE_OK else None}

    @service_errors
    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public(entry) for _, entry in sorted(self._read().items())]

    @service_errors
    def get(self, name: str) -> dict[str, Any]:
        with self._lock:
            entry = self._read().get(name)
            if entry is None:
                raise self._not_installed(name)
            return self._public(entry)

    @staticmethod
    def _not_installed(name: str) -> ServiceError:
        return _conflict(f"Package not installed: {name}", "bundle_not_installed", bundle=name)

    def _folder_of(self, entry: dict[str, Any]) -> Path:
        return (self._root / entry["path"]).resolve()

    def _state_of(self, entry: dict[str, Any]) -> str:
        if entry.get("source", SOURCE_COPY) != SOURCE_COPY:
            return STATE_OK
        folder = self._folder_of(entry)
        if not folder.is_dir() or not folder.is_relative_to(self._root.resolve()):
            return STATE_MISSING
        return STATE_OK if BundleScanServices.fingerprint(folder) == entry.get("fingerprint") else STATE_CHANGED

    # --- start --------------------------------------------------------------

    def load_registry(self) -> None:
        """Recompute the fingerprint of every copy and register the ones that did not change.

        Called by the constructor of ``ApplicationServices`` after the ``--bundle`` options. A changed or missing copy
        stays out of the process (``state`` ``changed`` or ``missing``); the others load. A name that is already a
        ``--bundle`` stops the start (``ValueError``), unless it is the same folder.
        """
        if getattr(self._services.system, "settings", None) is None:
            return  # a facade over a stand-in system (some unit tests) has no data_dir to read
        with self._lock:
            entries = self._read()
            staging = self._root / _STAGING
            if staging.is_dir():
                shutil.rmtree(staging, ignore_errors=True)  # a start that was cut in the middle of an install
            services = self._services
            from . import _BUNDLE_NAME

            for name, entry in entries.items():  # a hand-edited registry never reaches the process unchecked
                if (not _BUNDLE_NAME.fullmatch(str(name)) or entry.get("name") != name
                        or not isinstance(entry.get("path"), str)):
                    raise ValueError(f"The installed packages registry ({_REGISTRY_FILE}) has an invalid entry: {name!r}")
            for name, entry in sorted(entries.items()):
                if entry.get("source", SOURCE_COPY) != SOURCE_COPY:
                    continue  # PAQ-T02: the shipped entries point at the product's own folder
                folder = self._folder_of(entry)
                state = self._state_of(entry)
                self._states[name] = state
                if name in services._bundles:
                    if services._bundles[name] == folder:
                        continue  # the same folder under the same name: nothing to replace
                    raise ValueError(f"bundle name in use: {name}")
                if state == STATE_OK:
                    services._bundles[name] = folder

    def raise_if_unloadable(self, name: str) -> None:
        """409 ``installed_bundle_changed`` for a package whose copy changed or is missing; nothing for other names."""
        if self._states.get(name, STATE_OK) != STATE_OK:
            raise _conflict(f"The installed package {name} changed or is missing; reinstall it",
                            REASON_CHANGED, bundle=name, state=self._states[name])

    @service_errors
    def verify(self, name: str) -> dict[str, Any]:
        """Recompute the fingerprint of one copy and align the process with it (a changed copy is unloaded)."""
        with self._lock:
            entry = self._read().get(name)
            if entry is None:
                raise self._not_installed(name)
            state = self._state_of(entry)
            self._states[name] = state
            if entry.get("source", SOURCE_COPY) == SOURCE_COPY:
                if state == STATE_OK:
                    self._services.register_bundle(name, self._folder_of(entry))
                else:
                    self._services.unregister_bundle(name)
            return self._public(entry)

    # --- install, reinstall, remove ----------------------------------------

    @staticmethod
    def _check_name(name: Any) -> str:
        from . import _BUNDLE_NAME

        if not isinstance(name, str) or not _BUNDLE_NAME.fullmatch(name):
            raise _invalid("Invalid bundle name", field="name")
        return name

    def _copy_to_staging(self, source: Path) -> Path:
        staging = self._root / _STAGING / uuid.uuid4().hex
        staging.mkdir(parents=True)
        try:
            for current, directories, files in os.walk(source, followlinks=False):
                here = Path(current)
                relative = here.relative_to(source)
                directories[:] = [d for d in directories if "__pycache__" != d]
                for item in [*directories, *files]:
                    if _is_link_or_junction(here / item):
                        raise _invalid("A package cannot contain symbolic links or junctions",
                                       reason="links_not_allowed", path=(relative / item).as_posix())
                for directory in directories:
                    (staging / relative / directory).mkdir(parents=True, exist_ok=True)
                for file in files:
                    shutil.copy2(here / file, staging / relative / file)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return staging

    def _validate(self, folder: Path) -> None:
        """Load the copy and every workflow of it (and its team): the ``bundle-validate`` check, on a data package."""
        settings = self._services.settings
        loaded = load_bundle(folder, settings)
        problems, definitions = [], []
        directory = folder / "workflows"
        for path in sorted(directory.glob("*.yaml")):
            try:
                require_safe_workflow_id(path.stem)
                definitions.append(loaded.load_workflow(path.stem))
            except WorkflowDefinitionError as exc:
                problems.append(exc.to_payload())
        team_issues = []
        if loaded.team is not None:
            system = self._services.system
            team_issues = [issue.to_dict() for issue in validate_team_workflows(
                loaded.team, definitions, output_schemas=loaded.output_schemas,
                skills_dir=loaded.prompts.skills_dir, tool_registry=getattr(system, "tool_registry", None),
                tool_selection=getattr(getattr(system, "engine", None), "tool_selection", None))
                if issue.severity == "error"]
        if problems or team_issues:
            count = sum(len(p["issues"]) for p in problems) + len(team_issues)
            raise ServiceError(INVALID_DEFINITION, f"Invalid package: {count} problem{'' if count == 1 else 's'}",
                               status=422, details={"workflows": problems, "team": team_issues})

    def _next_version(self, name: str, entries: dict[str, dict[str, Any]]) -> int:
        numbers = [int(entry.get("version") or 0) for key, entry in entries.items() if key == name]
        base = self._root / name
        if base.is_dir():
            numbers += [int(p.name[1:]) for p in base.iterdir() if p.name[:1] == "v" and p.name[1:].isdigit()]
        return max(numbers, default=0) + 1

    def _stage_and_place(self, name: str, folder: Path, entries: dict[str, dict[str, Any]]
                         ) -> tuple[int, str, str, Path]:
        """Copy ``folder``, prove the copy equal to it, validate a data package and move it to ``v<n>``."""
        source = Path(folder).expanduser()
        if not source.is_dir():
            raise _invalid("The package folder does not exist", field="folder")
        source = source.resolve()
        if source.is_relative_to(self._root.resolve()):
            raise _invalid("The package folder is already inside the installed packages", field="folder")
        fingerprint = BundleScanServices.fingerprint(source)
        staging = self._copy_to_staging(source)
        try:
            if BundleScanServices.fingerprint(staging) != fingerprint:
                raise _conflict("The copy does not match the folder; nothing was installed", "copy_fingerprint_mismatch")
            level = package_level(staging)
            if level == "data":
                self._validate(staging)
            version = self._next_version(name, entries)
            destination = self._root / name / f"v{version}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging.replace(destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return version, fingerprint, level, destination

    def _assert_idle(self, name: str) -> None:
        if self._services.runs.has_runs_in(name, BUSY_STATUSES):
            raise _conflict(f"Package {name} has runs in progress", "bundle_busy", bundle=name)

    @service_errors
    def install_folder(self, name: str, folder: Path, *, actor: str, channel: str = "python",
                       trust_functions: bool = False, source_label: str = "") -> dict[str, Any]:
        from .projects import _user_actor

        name = self._check_name(name)
        actor = _user_actor(actor)
        with self._lock:
            entries = self._read()
            if name in entries or name in self._services._bundles:
                raise _conflict(f"Bundle name in use: {name}", "bundle_name_in_use", bundle=name)
            version, fingerprint, level, destination = self._stage_and_place(name, folder, entries)
            entry = {"name": name, "fingerprint": fingerprint, "level": level,
                     "trust_functions": bool(trust_functions), "source_label": str(source_label or "")[:120],
                     "version": version, "installed_at": utcnow(), "installed_by": actor, "channel": channel,
                     "source": SOURCE_COPY, "path": f"{name}/v{version}"}
            entries[name] = entry
            self._write(entries)
            self._states[name] = STATE_OK
            self._services.register_bundle(name, destination)
            return self._public(entry)

    @service_errors
    def reinstall(self, name: str, folder: Path, *, actor: str, channel: str = "python") -> dict[str, Any]:
        from .projects import _user_actor

        name = self._check_name(name)
        actor = _user_actor(actor)
        with self._lock:
            entries = self._read()
            old = entries.get(name)
            if old is None or old.get("source", SOURCE_COPY) != SOURCE_COPY:
                raise self._not_installed(name)
            self._assert_idle(name)
            # The new version is built and validated first: if it fails, the registry still points at the old one.
            version, fingerprint, level, destination = self._stage_and_place(name, folder, entries)
            entry = {**old, "fingerprint": fingerprint, "level": level, "trust_functions": False,
                     "version": version, "installed_at": utcnow(), "installed_by": actor, "channel": channel,
                     "path": f"{name}/v{version}"}
            entries[name] = entry
            self._write(entries)
            self._states[name] = STATE_OK
            self._services.register_bundle(name, destination)
            return self._public(entry)

    @service_errors
    def remove(self, name: str, *, actor: str, channel: str = "python") -> dict[str, Any]:
        from .projects import _user_actor

        name = self._check_name(name)
        actor = _user_actor(actor)
        with self._lock:
            entries = self._read()
            entry = entries.get(name)
            if entry is None or entry.get("source", SOURCE_COPY) != SOURCE_COPY:
                raise self._not_installed(name)
            self._assert_idle(name)
            del entries[name]
            self._write(entries)
            self._states.pop(name, None)
            self._services.unregister_bundle(name)
            # The copy under installed/<name>/ and the run data under bundles/<name>/ stay: nothing is deleted.
            return {"name": name, "removed": True, "kept_data": True, "removed_by": actor, "channel": channel}
