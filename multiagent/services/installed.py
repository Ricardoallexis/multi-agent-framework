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

import io
import json
import os
import re
import shutil
import stat
import struct
import threading
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Literal

from ..config import IN_CHECKOUT, PROJECT_ROOT
from ..db.database import utcnow
from ..teams import validate_team_workflows
from ..tools.files import _is_link_or_junction
from ..bundles import load_bundle
from ..functions import (FunctionConflictError, FunctionsImportError, load_trusted_functions, loaded_functions,
                         unregister_owner)
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
FUNCTIONS_FIX_TEXT = "This package includes functions; trust them from the package details"
SOURCE_COPY, SOURCE_SHIPPED = "copy", "shipped"
SOURCE_OFFICE = "office"                      # OFI-T03: an area of an installed office (the entry also has ``office``)
_FOLDER_SOURCES = (SOURCE_COPY, SOURCE_OFFICE)   # entries that own a copy under installed/<name>/v<n>/
_BATCH_OPS = ("install", "reinstall", "remove")

# PAQ-T02: the .zip a person picks in the GUI. The route checks the upload cap before it reads the body.
ZIP_UPLOAD_MAX_BYTES = 25 * 2**20
ZIP_EXTRACT_MAX_BYTES = 50 * 2**20
ZIP_MAX_FILES = 2000
SHIPPED_EXAMPLES = {"general": "examples/bundles/general"}   # name -> folder of the product, registered by reference
SHIPPED_LABEL = "Paquete de ejemplo del producto"

_REGISTRY_FILE = "registry.json"
_STAGING = ".staging"
_DRIVE = re.compile(r"^[A-Za-z]:")
_ZIP_CHUNK = 64 * 1024
_ZIP_LOCAL_HEADER = struct.Struct("<4s5H3L2H")   # signature, version, flags, method, time, date, crc, sizes, name and extra lengths
_ZIP_IGNORED_TOP = "__MACOSX"                     # the resource forks that macOS adds to a .zip


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


# --- .zip extraction (PAQ-T02) ------------------------------------------------

def _zip_error(reason: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"reason": reason, **details})


def _unsafe_entry(name: str) -> ServiceError:
    return _zip_error("zip_unsafe_entry", "The .zip has an entry that is not allowed", entry=name[:120])


def _too_large(message: str, **details: Any) -> ServiceError:
    return _zip_error("zip_too_large", message, **details)


def _checked_entries(infos: list[zipfile.ZipInfo], max_bytes: int, max_files: int
                     ) -> list[tuple[zipfile.ZipInfo, tuple[str, ...], bool]]:
    """Validate every name before anything is written: ``(info, path parts, is a folder)`` of each entry to extract."""
    checked, seen, files = [], set(), set()
    declared = 0
    for info in infos:
        name = info.orig_filename  # ZipInfo.filename turns "\" into "/" on Windows: the name as stored is the one to judge
        if not name or "\x00" in name or "\\" in name or name.startswith("/") or _DRIVE.match(name):
            raise _unsafe_entry(name)
        folder = name.endswith("/")
        parts = tuple(part for part in name.split("/") if part not in ("", "."))
        if "//" in name.rstrip("/") or any(part == ".." or ":" in part for part in parts):
            raise _unsafe_entry(name)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise _unsafe_entry(name)  # a symbolic link or a device: never extracted
        if not parts or parts[0] == _ZIP_IGNORED_TOP:
            continue
        key = "/".join(parts).casefold()
        if key in seen:
            raise _unsafe_entry(name)
        seen.add(key)
        if info.flag_bits & 0x1:
            raise _zip_error("zip_invalid", "The .zip is encrypted")
        if not folder:
            files.add(parts)
            declared += info.file_size
        checked.append((info, parts, folder))
    if len(files) > max_files:
        raise _too_large(f"The .zip has more than {max_files} files", limit=max_files, field="files")
    if declared > max_bytes:
        raise _too_large(f"The .zip is larger than {max_bytes} bytes when extracted", limit=max_bytes, field="bytes")
    for parts in files:  # a file that is also a folder of another entry
        if any(parts[:end] in files for end in range(1, len(parts))):
            raise _unsafe_entry("/".join(parts))
    if not files:
        raise _zip_error("zip_invalid", "The .zip has no files")
    return checked


def _entry_chunks(buffer: io.BytesIO, info: zipfile.ZipInfo) -> Iterator[bytes]:
    """The real content of one entry, in chunks. The size the .zip declares is not trusted: the caller counts the bytes."""
    buffer.seek(info.header_offset)
    header = buffer.read(_ZIP_LOCAL_HEADER.size)
    if len(header) < _ZIP_LOCAL_HEADER.size or header[:4] != b"PK\x03\x04":
        raise _zip_error("zip_invalid", "The .zip is damaged")
    name_length, extra_length = _ZIP_LOCAL_HEADER.unpack(header)[-2:]
    buffer.seek(info.header_offset + _ZIP_LOCAL_HEADER.size + name_length + extra_length)
    remaining = info.compress_size
    if info.compress_type == zipfile.ZIP_STORED:
        while remaining > 0:
            chunk = buffer.read(min(_ZIP_CHUNK, remaining))
            if not chunk:
                raise _zip_error("zip_invalid", "The .zip is damaged")
            remaining -= len(chunk)
            yield chunk
        return
    if info.compress_type != zipfile.ZIP_DEFLATED:
        raise _zip_error("zip_invalid", "The .zip uses a compression that is not supported")
    inflater, pending = zlib.decompressobj(-15), b""
    try:
        while True:
            if not pending and remaining > 0:
                pending = buffer.read(min(_ZIP_CHUNK, remaining))
                if not pending:
                    raise _zip_error("zip_invalid", "The .zip is damaged")
                remaining -= len(pending)
            out = inflater.decompress(pending, _ZIP_CHUNK)  # one chunk of output at a time: a bomb cannot swell here
            pending = inflater.unconsumed_tail
            if out:
                yield out
            if inflater.eof or (not out and not pending and remaining <= 0):
                break
    except zlib.error:
        raise _zip_error("zip_invalid", "The .zip is damaged") from None
    if not inflater.eof:
        raise _zip_error("zip_invalid", "The .zip is damaged")


def extract_zip_safely(data: bytes, dest: Path, *, max_bytes: int = ZIP_EXTRACT_MAX_BYTES,
                       max_files: int = ZIP_MAX_FILES) -> Path:
    """Extract a ``.zip`` into the new folder ``dest`` and return the root folder of the package.

    Every name is checked before anything is written: an absolute path, a drive letter, ``..``, a backslash, a symbolic
    link and a repeated name give ``zip_unsafe_entry`` (422); a damaged or encrypted file gives ``zip_invalid``; more than
    ``max_files`` files or ``max_bytes`` bytes give ``zip_too_large``. The bytes are counted as they are written, so a
    ``.zip`` that declares less than it holds stops at the limit. A single top folder (what Windows makes with
    ("Send to > Compressed folder") is unwrapped: the returned folder is that one. If anything fails, ``dest`` is removed.
    """
    dest = Path(dest)
    if len(data) > ZIP_UPLOAD_MAX_BYTES:
        raise _too_large(f"The .zip is larger than {ZIP_UPLOAD_MAX_BYTES} bytes", limit=ZIP_UPLOAD_MAX_BYTES, field="zip")
    buffer = io.BytesIO(data)
    try:
        with zipfile.ZipFile(buffer) as archive:
            entries = _checked_entries(archive.infolist(), max_bytes, max_files)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, zlib.error):
        raise _zip_error("zip_invalid", "The file is not a valid .zip") from None
    tops = {parts[0] for _, parts, _ in entries}
    wrapped = len(tops) == 1 and all(len(parts) > 1 for _, parts, folder in entries if not folder)
    dest.mkdir(parents=True, exist_ok=False)
    root, total, current = dest.resolve(), 0, ""
    try:
        for info, parts, folder in entries:
            current = "/".join(parts)
            target = root.joinpath(*parts)
            if not target.resolve().is_relative_to(root):
                raise _unsafe_entry(current)
            if folder:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            crc = size = 0
            with target.open("wb") as stream:
                for chunk in _entry_chunks(buffer, info):
                    total += len(chunk)
                    size += len(chunk)
                    if total > max_bytes:
                        raise _too_large(f"The .zip is larger than {max_bytes} bytes when extracted", limit=max_bytes,
                                         field="bytes")
                    crc = zlib.crc32(chunk, crc)
                    stream.write(chunk)
            if crc != info.CRC or size != info.file_size:  # what it holds must be what it says it holds
                raise _zip_error("zip_invalid", "The .zip is damaged")
    except OSError:  # a name the file system refuses (a reserved name, a file that is also a folder)
        shutil.rmtree(dest, ignore_errors=True)
        raise _unsafe_entry(current) from None
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest / next(iter(tops)) if wrapped else dest


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
                "functions": self.functions_status(entry["name"]),
                "source_label": entry.get("source_label", ""), "version": entry.get("version"),
                "installed_at": entry.get("installed_at"), "installed_by": entry.get("installed_by"),
                "channel": entry.get("channel"), "source": entry.get("source", SOURCE_COPY),
                "office": entry.get("office"), "state": state, "reason": REASON_CHANGED if state != STATE_OK else None}

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

    # --- trusted functions (PAQ-T03) ----------------------------------------

    def _is_trusted(self, name: str) -> bool:
        """Trusted by ``--trust-functions`` (this start only) or by the registry entry (tied to its fingerprint)."""
        if name in self._services._trust_functions:
            return True
        try:
            entry = self._read().get(name)
        except ValueError:
            return False
        return bool(entry and entry.get("source", SOURCE_COPY) in (SOURCE_COPY, SOURCE_OFFICE)
                    and entry.get("trust_functions") is True)

    def functions_status(self, name: str) -> dict[str, Any]:
        """``{"present", "trusted", "loaded"}``: does the package bring ``functions.py``, may it load, what is loaded."""
        folder = self._services._bundles.get(name)
        present = bool(folder is not None and (folder / "functions.py").is_file())
        return {"present": present, "trusted": self._is_trusted(name), "loaded": loaded_functions(name)}

    def check_trusted_names(self) -> None:
        """``ValueError`` for a ``--trust-functions`` name that is neither a ``--bundle`` nor an installed package."""
        if not self._services._trust_functions:
            return
        known = set(self._services._bundles) | set(self._read())
        for name in sorted(self._services._trust_functions):
            if name not in known:
                raise ValueError(f"unknown bundle for --trust-functions: {name}")

    def has_untrusted_functions(self, name: str) -> bool:
        status = self.functions_status(name)
        return status["present"] and not status["trusted"]

    def load_functions(self, name: str) -> None:
        """Import the ``functions.py`` of a trusted package. Call it right after ``require_load`` and before loading.

        Without trust nothing is imported. Errors: 409 ``invalid_state`` with ``reason`` ``function_conflict``
        (``function_id``, ``owner``) and 422 ``invalid_definition`` with ``reason`` ``functions_import_failed``
        (``error_type`` only: no message and no trace).
        """
        folder = self._services._bundles.get(name)
        if folder is None or not (folder / "functions.py").is_file() or not self._is_trusted(name):
            return
        fingerprint = BundleScanServices.fingerprint(folder)
        try:
            load_trusted_functions(name, folder, fingerprint)
        except FunctionConflictError as exc:
            raise _conflict(f"A function of {name} is already registered: {exc.function_id}", "function_conflict",
                            bundle=name, function_id=exc.function_id, owner=exc.owner) from None
        except FunctionsImportError as exc:
            raise ServiceError(INVALID_DEFINITION, f"functions.py of {name} could not be imported: {exc.error_type}",
                               status=422, details={"reason": "functions_import_failed", "bundle": name,
                                                    "error_type": exc.error_type}) from None

    @staticmethod
    def _not_installed(name: str) -> ServiceError:
        return _conflict(f"Package not installed: {name}", "bundle_not_installed", bundle=name)

    @staticmethod
    def _shipped_folder(name: str) -> Path | None:
        """The folder of a package of the product (``SHIPPED_EXAMPLES``), or ``None`` outside a checkout (no examples)."""
        relative = SHIPPED_EXAMPLES.get(name)
        if relative is None or not IN_CHECKOUT:
            return None
        folder = (PROJECT_ROOT / relative).resolve()
        return folder if folder.is_dir() else None

    def example_available(self, name: str = "general") -> bool:
        """Whether "Install example package" is offered: a checkout, and the name not taken."""
        if self._shipped_folder(name) is None:
            return False
        with self._lock:
            try:
                taken = name in self._read()
            except ValueError:
                return False
            return not taken and name not in self._services._bundles

    def _folder_of(self, entry: dict[str, Any]) -> Path:
        return (self._root / entry["path"]).resolve()

    def _state_of(self, entry: dict[str, Any]) -> str:
        if entry.get("source") == SOURCE_SHIPPED:
            return STATE_OK if self._shipped_folder(entry["name"]) is not None else STATE_MISSING
        if entry.get("source", SOURCE_COPY) not in _FOLDER_SOURCES:
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
                if entry.get("source", SOURCE_COPY) not in (SOURCE_COPY, SOURCE_SHIPPED, SOURCE_OFFICE):
                    continue
                # PAQ-T02: a shipped entry points at the product's own folder, found by its name in SHIPPED_EXAMPLES
                # (the ``path`` of a hand-edited registry is never followed), and only inside a checkout.
                shipped = entry.get("source") == SOURCE_SHIPPED
                folder = self._shipped_folder(name) if shipped else self._folder_of(entry)
                state = self._state_of(entry)
                self._states[name] = state
                if folder is None:
                    continue  # the product's folder is not here (not a checkout): the row shows "missing"
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
            if entry.get("source", SOURCE_COPY) in (SOURCE_COPY, SOURCE_SHIPPED, SOURCE_OFFICE):
                folder = self._shipped_folder(name) if entry.get("source") == SOURCE_SHIPPED else self._folder_of(entry)
                if state == STATE_OK and folder is not None:
                    self._services.register_bundle(name, folder)
                else:
                    unregister_owner(name)
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

    def _validate(self, folder: Path) -> dict[str, Any]:
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
            team_issues = list(validate_team_workflows(
                loaded.team, definitions, output_schemas=loaded.output_schemas,
                skills_dir=loaded.prompts.skills_dir, tool_registry=getattr(system, "tool_registry", None),
                tool_selection=getattr(getattr(system, "engine", None), "tool_selection", None)))
        team_errors = [issue.to_dict() for issue in team_issues if issue.severity == "error"]
        if problems or team_errors:
            count = sum(len(p["issues"]) for p in problems) + len(team_errors)
            raise ServiceError(INVALID_DEFINITION, f"Invalid package: {count} problem{'' if count == 1 else 's'}",
                               status=422, details={"workflows": problems, "team": team_errors})
        return {"warnings": [issue.to_dict() for issue in team_issues if issue.severity == "warning"]}

    def _next_version(self, name: str, entries: dict[str, dict[str, Any]]) -> int:
        numbers = [int(entry.get("version") or 0) for key, entry in entries.items() if key == name]
        base = self._root / name
        if base.is_dir():
            numbers += [int(p.name[1:]) for p in base.iterdir() if p.name[:1] == "v" and p.name[1:].isdigit()]
        return max(numbers, default=0) + 1

    def _stage_and_place(self, name: str, folder: Path, entries: dict[str, dict[str, Any]], *,
                         extracted: bool = False, code_gate: Callable[[str], None] | None = None
                         ) -> tuple[int, str, str, Path]:
        """Copy ``folder``, prove the copy equal to it, validate a data package and move it to ``v<n>``.

        ``extracted``: the folder is what ``extract_zip_safely`` left under ``.staging/`` (the only place inside
        ``installed/`` that may be a source). ``code_gate(level)`` runs once the level is known, before anything is placed.
        """
        source = Path(folder).expanduser()
        if not source.is_dir():
            raise _invalid("The package folder does not exist", field="folder")
        source = source.resolve()
        if not extracted and source.is_relative_to(self._root.resolve()):
            raise _invalid("The package folder is already inside the installed packages", field="folder")
        fingerprint = BundleScanServices.fingerprint(source)
        staging = self._copy_to_staging(source)
        try:
            if BundleScanServices.fingerprint(staging) != fingerprint:
                raise _conflict("The copy does not match the folder; nothing was installed", "copy_fingerprint_mismatch")
            level = package_level(staging)
            if code_gate is not None:
                code_gate(level)
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

    @staticmethod
    def _refuse_office_area(entry: dict[str, Any] | None) -> None:
        """An area belongs to its office: it changes by reimporting or removing the office, never alone (OFI-T03)."""
        if entry is not None and entry.get("source") == SOURCE_OFFICE:
            raise _conflict(f"The package {entry['name']} belongs to the office {entry.get('office')}; "
                            "remove or reimport the office instead", "bundle_in_office",
                            bundle=entry["name"], office=entry.get("office"))

    def _assert_idle(self, name: str) -> None:
        if self._services.runs.has_runs_in(name, BUSY_STATUSES):
            raise _conflict(f"Package {name} has runs in progress", "bundle_busy", bundle=name)

    @service_errors
    def install_folder(self, name: str, folder: Path, *, actor: str, channel: str = "python",
                       trust_functions: bool = False, source_label: str = "") -> dict[str, Any]:
        return self._install(name, folder, actor=actor, channel=channel, trust_functions=trust_functions,
                             source_label=source_label)

    def _install(self, name: str, folder: Path, *, actor: str, channel: str, trust_functions: bool, source_label: str,
                 extracted: bool = False, code_gate: Callable[[str], None] | None = None) -> dict[str, Any]:
        from .projects import _user_actor

        name = self._check_name(name)
        actor = _user_actor(actor)
        with self._lock:
            entries = self._read()
            if name in entries or name in self._services._bundles:
                raise _conflict(f"Bundle name in use: {name}", "bundle_name_in_use", bundle=name)
            version, fingerprint, level, destination = self._stage_and_place(
                name, folder, entries, extracted=extracted, code_gate=code_gate)
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
        return self._reinstall(name, folder, actor=actor, channel=channel)

    def _reinstall(self, name: str, folder: Path, *, actor: str, channel: str, extracted: bool = False,
                   code_gate: Callable[[str], None] | None = None) -> dict[str, Any]:
        from .projects import _user_actor

        name = self._check_name(name)
        actor = _user_actor(actor)
        with self._lock:
            entries = self._read()
            old = entries.get(name)
            self._refuse_office_area(old)
            if old is None or old.get("source", SOURCE_COPY) != SOURCE_COPY:
                raise self._not_installed(name)
            self._assert_idle(name)
            # The new version is built and validated first: if it fails, the registry still points at the old one.
            version, fingerprint, level, destination = self._stage_and_place(
                name, folder, entries, extracted=extracted, code_gate=code_gate)
            entry = {**old, "fingerprint": fingerprint, "level": level, "trust_functions": False,
                     "version": version, "installed_at": utcnow(), "installed_by": actor, "channel": channel,
                     "path": f"{name}/v{version}"}
            entries[name] = entry
            self._write(entries)
            self._states[name] = STATE_OK
            unregister_owner(name)  # the new fingerprint is not trusted yet: the old functions go
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
            self._refuse_office_area(entry)
            if entry is None or entry.get("source", SOURCE_COPY) not in (SOURCE_COPY, SOURCE_SHIPPED):
                raise self._not_installed(name)
            self._assert_idle(name)
            del entries[name]
            self._write(entries)
            self._states.pop(name, None)
            unregister_owner(name)
            self._services.unregister_bundle(name)
            # The copy under installed/<name>/ and the run data under bundles/<name>/ stay: nothing is deleted.
            # A shipped entry has no copy: the product's folder is never touched.
            return {"name": name, "removed": True, "kept_data": True, "removed_by": actor, "channel": channel}

    # --- several packages at once (OFI-T03) ---------------------------------

    @service_errors
    def apply_batch(self, changes: list[dict[str, Any]], *, actor: str, channel: str = "python",
                    source: str = SOURCE_COPY, office: str | None = None, allow_code: bool = False,
                    trusted_functions: set[str] | frozenset[str] = frozenset(),
                    expected_fingerprints: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Install, reinstall and remove several packages as one change: all of them, or none.

        Each change is ``{"op": "install" | "reinstall" | "remove", "name", "folder"?}``; ``folder`` is the new
        content of an install or a reinstall. Everything is checked before the registry is written (names, runs in
        progress, the copy and the validation of every data package), and the registry is written once. If a
        change fails, no copy stays under ``installed/`` and the registry is as it was. ``source="office"`` with the
        office id registers areas of an office. Code is rejected unless ``allow_code`` is explicitly set by the
        one-card office installer after approval; names in ``trusted_functions`` are trusted for this copy only.
        ``expected_fingerprints`` binds office package copies to the fingerprints reviewed on that card. The answer
        has one item per change, in order: the entry of an install or reinstall, and the removal summary of a remove.
        Copies and run data of a removed package stay, as in ``remove``.
        """
        from .projects import _user_actor

        actor = _user_actor(actor)
        if source not in (SOURCE_COPY, SOURCE_OFFICE):
            raise _invalid("Unknown package source", field="source")
        if (source == SOURCE_OFFICE) != (office is not None):
            raise _invalid("source 'office' goes with an office id, and only with it", field="office")
        if type(allow_code) is not bool or (allow_code and source != SOURCE_OFFICE):
            raise _invalid("Code installs are available only for an approved office install", field="allow_code")
        if (not isinstance(trusted_functions, (set, frozenset))
                or any(not isinstance(name, str) for name in trusted_functions)
                or trusted_functions and (not allow_code or source != SOURCE_OFFICE)):
            raise _invalid("Trusted functions must belong to an approved office install", field="trusted_functions")
        plan: list[tuple[str, str, Path | None]] = []
        names: set[str] = set()
        for change in changes if isinstance(changes, list) else [None]:
            if not isinstance(change, dict) or change.get("op") not in _BATCH_OPS:
                raise _invalid("Each change is {op: install|reinstall|remove, name, folder?}", field="changes")
            op, name = change["op"], self._check_name(change.get("name"))
            if name in names:
                raise _invalid("A package appears twice in the changes", field="changes", name=name)
            names.add(name)
            folder = change.get("folder")
            if op != "remove" and not isinstance(folder, (str, Path)):
                raise _invalid("An install or a reinstall needs its folder", field="folder", name=name)
            plan.append((op, name, Path(folder) if op != "remove" else None))
        if not trusted_functions <= names:
            raise _invalid("Trusted functions must name an area in this batch", field="trusted_functions")
        if expected_fingerprints is not None:
            if (source != SOURCE_OFFICE or not isinstance(expected_fingerprints, dict)
                    or any(name not in names or not isinstance(fingerprint, str)
                           or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
                           for name, fingerprint in expected_fingerprints.items())):
                raise _invalid("Expected fingerprints must name office packages in this batch",
                               field="expected_fingerprints")
        if not plan:
            return []
        with self._lock:
            entries = self._read()
            for op, name, _ in plan:
                old = entries.get(name)
                if op == "install":
                    if old is not None or name in self._services._bundles:
                        raise _conflict(f"Bundle name in use: {name}", "bundle_name_in_use", bundle=name)
                    continue
                if source == SOURCE_OFFICE:
                    owned = old is not None and old.get("source") == SOURCE_OFFICE and old.get("office") == office
                else:
                    self._refuse_office_area(old)
                    owned = old is not None and old.get("source", SOURCE_COPY) == SOURCE_COPY
                if not owned:
                    raise self._not_installed(name)
                self._assert_idle(name)
            staging_root = (self._root / _STAGING).resolve()
            placed: list[Path] = []
            summaries: list[dict[str, Any]] = []
            try:
                for op, name, folder in plan:
                    if op == "remove":
                        summaries.append({"name": name, "removed": True, "kept_data": True, "removed_by": actor,
                                          "channel": channel})
                        entries.pop(name, None)
                        continue
                    folder = Path(folder).expanduser()
                    inside = folder.exists() and folder.resolve().is_relative_to(staging_root)
                    version, fingerprint, level, destination = self._stage_and_place(
                        name, folder, entries, extracted=inside,
                        code_gate=self._office_gate(name) if source == SOURCE_OFFICE and not allow_code else None)
                    placed.append(destination)
                    expected = expected_fingerprints.get(name) if expected_fingerprints is not None else None
                    if expected is not None and fingerprint != expected:
                        raise _conflict("The office changed after its card was prepared",
                                        "office_changed_since_card", bundle=name)
                    if op == "install":
                        entry = {"name": name, "fingerprint": fingerprint, "level": level,
                                 "trust_functions": name in trusted_functions,
                                 "source_label": "", "version": version, "installed_at": utcnow(),
                                 "installed_by": actor, "channel": channel, "source": source,
                                 "path": f"{name}/v{version}"}
                    else:
                        entry = {**entries[name], "fingerprint": fingerprint, "level": level,
                                 "trust_functions": name in trusted_functions,
                                 "version": version, "installed_at": utcnow(), "installed_by": actor,
                                 "channel": channel, "path": f"{name}/v{version}"}
                    if office is not None:
                        entry["office"] = office
                    entries[name] = entry
                    summaries.append(entry)
                self._write(entries)
            except BaseException:
                for destination in placed:
                    shutil.rmtree(destination, ignore_errors=True)
                    try:
                        destination.parent.rmdir()   # the folder of a package that had no copy before: only if empty
                    except OSError:
                        pass
                raise
            result: list[dict[str, Any]] = []
            for (op, name, _), item in zip(plan, summaries):
                if op == "remove":
                    self._states.pop(name, None)
                    unregister_owner(name)
                    self._services.unregister_bundle(name)
                    result.append(item)
                    continue
                self._states[name] = STATE_OK
                if op == "reinstall":
                    unregister_owner(name)
                self._services.register_bundle(name, self._root / item["path"])
                result.append(self._public(item))
            return result

    @staticmethod
    def _office_gate(name: str) -> Callable[[str], None]:
        def gate(level: str) -> None:
            if level == "code":
                raise _conflict(f"The area {name} has code; an office of level 1 has data only", "office_has_code",
                                bundle=name)
        return gate

    # --- .zip and the example (PAQ-T02) -------------------------------------

    def _code_gate(self, require_advanced: bool) -> Callable[[str], None] | None:
        """A package that has code is an "install_code" of the advanced mode (L3); a data package is not."""
        if not require_advanced:
            return None

        def gate(level: str) -> None:
            if level == "code":
                self._services.gui_settings.require_advanced_mode("install_code")
        return gate

    def _extract_to_staging(self, data: bytes) -> tuple[Path, Path]:
        """``(staging folder, package root)``: the ``.zip`` extracted under ``installed/.staging/<uuid>/``."""
        staging = self._root / _STAGING / uuid.uuid4().hex
        staging.parent.mkdir(parents=True, exist_ok=True)
        return staging, extract_zip_safely(data, staging)  # removes the folder itself if it fails

    @service_errors
    def install_zip(self, name: str, data: bytes, *, actor: str, channel: str = "python",
                    trust_functions: bool = False, filename: str = "", require_advanced_for_code: bool = False
                    ) -> dict[str, Any]:
        """Install the package inside a ``.zip``. ``require_advanced_for_code``: the GUI asks the advanced mode for code."""
        name = self._check_name(name)
        with self._lock:
            if name in self._read() or name in self._services._bundles:
                raise _conflict(f"Bundle name in use: {name}", "bundle_name_in_use", bundle=name)
        staging, root = self._extract_to_staging(data)
        try:
            return self._install(name, root, actor=actor, channel=channel, trust_functions=trust_functions,
                                 source_label=filename, extracted=True,
                                 code_gate=self._code_gate(require_advanced_for_code))
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @service_errors
    def reinstall_zip(self, name: str, data: bytes, *, actor: str, channel: str = "python",
                      require_advanced_for_code: bool = False) -> dict[str, Any]:
        name = self._check_name(name)
        with self._lock:
            old = self._read().get(name)
            self._refuse_office_area(old)
            if old is None or old.get("source", SOURCE_COPY) != SOURCE_COPY:
                raise self._not_installed(name)
        staging, root = self._extract_to_staging(data)
        try:
            return self._reinstall(name, root, actor=actor, channel=channel, extracted=True,
                                   code_gate=self._code_gate(require_advanced_for_code))
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @service_errors
    def install_shipped(self, name: str = "general", *, actor: str, channel: str = "python") -> dict[str, Any]:
        """Register a package of the product by reference: no copy, and exempt from the scan because it is product code."""
        from .projects import _user_actor

        name = self._check_name(name)
        actor = _user_actor(actor)
        if name not in SHIPPED_EXAMPLES:
            raise _invalid("There is no example package with that name", field="name")
        folder = self._shipped_folder(name)
        if folder is None:
            raise _conflict("The example package is only available in a checkout of the product", "example_not_available",
                            bundle=name)
        with self._lock:
            entries = self._read()
            if name in entries or name in self._services._bundles:
                raise _conflict(f"Bundle name in use: {name}", "bundle_name_in_use", bundle=name)
            entry = {"name": name, "fingerprint": None, "level": package_level(folder), "trust_functions": False,
                     "source_label": SHIPPED_LABEL, "version": None, "installed_at": utcnow(), "installed_by": actor,
                     "channel": channel, "source": SOURCE_SHIPPED, "path": SHIPPED_EXAMPLES[name]}
            entries[name] = entry
            self._write(entries)
            self._states[name] = STATE_OK
            self._services.register_bundle(name, folder)
            return self._public(entry)
